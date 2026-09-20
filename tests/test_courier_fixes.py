import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch
import test_courier as fixture
from app import app
from models import db, Order, CourierShipment, CourierAction
from econt import EcontError
from security import safe_equal, safe_next_url


class FixTests(unittest.TestCase):
    setUp = fixture.CourierTests.setUp
    tearDown = fixture.CourierTests.tearDown

    def created(self):
        with patch('courier.EcontClient.label', return_value=self.status):
            self.c.post(self.url, data=self.form)
        self.shipment = CourierShipment.query.one()
        start = datetime.now(ZoneInfo('Europe/Sofia')) + timedelta(days=2)
        self.action_form = dict(csrf_token='csrf', confirm=str(self.shipment.id), city='София',
                                post_code='1000', address='Тестова улица 1',
                                time_from=start.replace(hour=10, minute=0).strftime('%Y-%m-%dT%H:%M'),
                                time_to=start.replace(hour=12, minute=0).strftime('%Y-%m-%dT%H:%M'))
        self.resolve_url = self.url + '/resolve'

    def resolve(self, **data):
        form = dict(csrf_token='csrf', confirm=str(self.shipment.id))
        form.update(data)
        return self.c.post(self.resolve_url, data=form)

    def make_stuck(self, kind, state):
        """Simulate a cancel/pickup whose result is unknown."""
        with patch('courier.EcontClient.cancel_label', side_effect=EcontError('timeout', uncertain=True)), \
                patch('courier.EcontClient.request_courier', side_effect=EcontError('timeout', uncertain=True)):
            self.c.post(f'{self.url}/action/{kind}', data=self.action_form)
        self.assertEqual(self.shipment.state, state)

    def age(self, minutes=10):
        self.shipment.updated_at = datetime.utcnow() - timedelta(minutes=minutes)
        db.session.commit()

    # ---- export buttons ----
    def test_export_buttons_are_shown(self):
        self.created()
        page = self.c.get(self.url).text
        for fmt in ('docx', 'xlsx', 'csv'):
            self.assertIn(f'/econt/export/{fmt}', page)
        self.assertEqual(self.c.get(self.url + '/export/csv').status_code, 200)

    # ---- unlocking stuck shipments ----
    def test_stuck_cancel_form_is_shown_and_not_done_unlocks(self):
        self.created(); self.make_stuck('cancel', 'cancel_unknown')
        self.assertIn('Записване на резултата', self.c.get(self.url).text)
        with patch('courier.EcontClient.cancel_label') as api:
            self.assertEqual(self.resolve(outcome='not_done').status_code, 302)
            api.assert_not_called()  # resolving never talks to Econt
        self.assertEqual(self.shipment.state, 'created')
        self.assertEqual(CourierAction.query.one().state, 'failed')
        with patch('courier.EcontClient.cancel_label') as api:
            self.assertEqual(self.c.post(self.url + '/action/cancel', data=self.action_form).status_code, 302)
            api.assert_called_once_with('DEMO123')
        self.assertEqual(self.shipment.state, 'cancelled')

    def test_stuck_cancel_done_marks_cancelled_and_allows_delete(self):
        self.created(); self.make_stuck('cancel', 'cancel_unknown')
        delete = f'/admin/orders/{self.order_id}/delete'
        self.c.get(delete)
        with self.c.session_transaction() as s: token = s['order_delete_csrf']
        self.c.post(delete, data={'csrf_token': token, 'confirm_order_id': str(self.order_id)})
        self.assertEqual(Order.query.count(), 1)  # still blocked
        self.resolve(outcome='done')
        self.assertEqual(self.shipment.state, 'cancelled')
        self.assertEqual(CourierAction.query.one().state, 'succeeded')
        self.c.post(delete, data={'csrf_token': token, 'confirm_order_id': str(self.order_id)})
        self.assertEqual(Order.query.count(), 0)

    def test_stuck_pickup_done_needs_request_number(self):
        self.created(); self.make_stuck('pickup', 'pickup_unknown')
        self.resolve(outcome='done')
        self.assertEqual(self.shipment.state, 'pickup_unknown')  # no request number -> unchanged
        self.resolve(outcome='done', request_id='777')
        self.assertEqual(self.shipment.state, 'created')
        action = CourierAction.query.one()
        self.assertEqual((action.state, action.request_id), ('succeeded', '777'))
        with patch('courier.EcontClient.courier_request_status', return_value={'id': 777, 'status': 'process'}):
            self.c.post(self.url + '/pickup-status', data={'csrf_token': 'csrf'})
        self.assertIn('Назначен е куриер', self.c.get(self.url).text)

    def test_stuck_pickup_not_done_allows_retry(self):
        self.created(); self.make_stuck('pickup', 'pickup_unknown')
        self.resolve(outcome='not_done')
        self.assertEqual(self.shipment.state, 'created')
        with patch('courier.EcontClient.request_courier', return_value=('5', '')) as api:
            self.assertEqual(self.c.post(self.url + '/action/pickup', data=self.action_form).status_code, 302)
            api.assert_called_once()
        self.assertEqual(CourierAction.query.one().request_id, '5')

    def test_pending_is_only_resolvable_after_grace_period(self):
        self.created()
        self.shipment.state = 'cancel_pending'; db.session.commit()
        self.resolve(outcome='not_done')
        self.assertEqual(self.shipment.state, 'cancel_pending')  # may still be in flight
        self.age(10)
        self.resolve(outcome='not_done')
        self.assertEqual(self.shipment.state, 'created')

    def test_resolve_is_protected(self):
        self.created(); self.make_stuck('cancel', 'cancel_unknown')
        self.assertEqual(app.test_client().post(self.resolve_url, data={'outcome': 'not_done'}).status_code, 302)
        self.assertEqual(self.c.post(self.resolve_url, data={'csrf_token': 'bad', 'outcome': 'not_done',
                                                             'confirm': str(self.shipment.id)}).status_code, 400)
        self.resolve(outcome='not_done', confirm='wrong')
        self.resolve(outcome='whatever')
        self.assertEqual(self.shipment.state, 'cancel_unknown')
        self.shipment.state = 'created'; db.session.commit()
        self.resolve(outcome='done')  # nothing to resolve
        self.assertEqual(self.shipment.state, 'created')

    # ---- pickup rejected by Econt can be requested again ----
    def test_rejected_pickup_can_be_requested_again(self):
        self.created(); url = self.url + '/action/pickup'
        with patch('courier.EcontClient.request_courier', return_value=('123', '')):
            self.c.post(url, data=self.action_form)
        with patch('courier.EcontClient.courier_request_status', return_value={'id': 123, 'status': 'reject'}):
            self.c.post(self.url + '/pickup-status', data={'csrf_token': 'csrf'})
        self.assertEqual(CourierAction.query.one().state, 'rejected')
        self.assertIn('можете да заявите куриер отново', self.c.get(self.url).text)
        self.assertIn('name="time_from"', self.c.get(url).text)
        with patch('courier.EcontClient.request_courier', return_value=('456', '')) as api:
            self.assertEqual(self.c.post(url, data=self.action_form).status_code, 302)
            self.assertEqual(self.c.post(url, data=self.action_form).status_code, 409)
            api.assert_called_once()
        action = CourierAction.query.one()
        self.assertEqual((action.state, action.request_id), ('succeeded', '456'))

    # ---- non-ASCII tokens must not cause a 500 ----
    def test_non_ascii_csrf_tokens_are_rejected_with_400(self):
        self.created()
        bad = {'csrf_token': 'грешен'}
        self.assertEqual(self.c.post(self.url, data={**self.form, **bad}).status_code, 400)
        self.assertEqual(self.c.post(self.url + '/action/cancel', data={**self.action_form, **bad}).status_code, 400)
        self.assertEqual(self.c.post(self.url + '/refresh', data=bad).status_code, 400)
        delete = f'/admin/orders/{self.order_id}/delete'
        self.c.get(delete)
        self.assertEqual(self.c.post(delete, data={**bad, 'confirm_order_id': str(self.order_id)}).status_code, 400)

    # ---- login ----
    def test_login_redirect_and_unicode_password(self):
        c = app.test_client()
        password = app.config['ADMIN_PASSWORD']
        response = c.post('/admin/login', data={'password': 'парола', 'next': '/admin/orders'})
        self.assertEqual(response.status_code, 200)
        for evil in ('//evil.example', 'https://evil.example/x', '\\\\evil.example', '/ok\r\nX: y'):
            response = c.post('/admin/login', data={'password': password, 'next': evil})
            self.assertEqual(response.status_code, 302)
            self.assertNotIn('evil', response.headers['Location'])
        response = c.post('/admin/login', data={'password': password, 'next': '/admin/orders'})
        self.assertTrue(response.headers['Location'].endswith('/admin/orders'))

    def test_helpers_and_cookie_policy(self):
        self.assertTrue(safe_equal('токен', 'токен'))
        self.assertFalse(safe_equal('токен', 'токeн'))
        self.assertFalse(safe_equal(None, 'x'))
        self.assertEqual(safe_next_url('/admin', '/d'), '/admin')
        self.assertEqual(safe_next_url('//x.y', '/d'), '/d')
        self.assertEqual(safe_next_url(None, '/d'), '/d')
        self.assertEqual(app.config['SESSION_COOKIE_SAMESITE'], 'Lax')

    # ---- profiles endpoint ----
    def test_profiles_endpoint_always_returns_json(self):
        with patch('courier.EcontClient.profiles', side_effect=RuntimeError('boom')):
            response = self.c.get('/admin/econt/profiles')
        self.assertEqual(response.status_code, 502)
        self.assertTrue(response.is_json)
        self.assertIn('error', response.get_json())

    # ---- default sender = the shop address from .env ----
    def test_sender_address_defaults_to_shop_address_from_env(self):
        with patch.dict(app.config, ECONT_SENDER_NAME='Old environment sender', ECONT_SENDER_CITY='София',
                        ECONT_SENDER_POST_CODE='1000', ECONT_SENDER_ADDRESS='бул. „Драган Цанков“ 59-63'):
            page = self.c.get(self.url).text
        self.assertIn('<option value="address" selected>', page)
        self.assertIn('id="sender_city" name="sender_city" value="София"', page)
        self.assertIn('id="sender_post_code" name="sender_post_code" value="1000"', page)
        self.assertIn('Драган Цанков', page)
        # name/phone still come from the Econt profile, never from .env
        self.assertNotIn('Old environment sender', page)
        # the JS falls back to the same address after choosing a profile
        self.assertIn('data-shop-city="София"', page)
        self.assertIn('data-shop-post-code="1000"', page)

    # ---- neutral wording in the live environment ----
    def test_error_text_does_not_claim_test_system(self):
        with patch('courier.EcontClient.label', side_effect=RuntimeError('x')):
            page = self.c.post(self.url, data=self.form).text
        self.assertNotIn('тестовата система', page)
        self.assertIn('Грешка при връзка с Еконт', page)


if __name__ == '__main__':
    unittest.main()
