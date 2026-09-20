import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch
import test_courier as fixture
from app import app
from models import db, Order, CourierShipment, CourierAction
from econt import EcontClient, EcontError


class ActionTests(unittest.TestCase):
    setUp = fixture.CourierTests.setUp
    tearDown = fixture.CourierTests.tearDown

    def created(self):
        with patch('courier.EcontClient.label', return_value=self.status):
            self.c.post(self.url, data=self.form)
        self.shipment=CourierShipment.query.one()
        start=datetime.now(ZoneInfo('Europe/Sofia'))+timedelta(days=2)
        self.action_form=dict(csrf_token='csrf',confirm=str(self.shipment.id),city='София',
                              post_code='1000',address='Тестова улица 1',
                              time_from=start.replace(hour=10,minute=0).strftime('%Y-%m-%dT%H:%M'),
                              time_to=start.replace(hour=12,minute=0).strftime('%Y-%m-%dT%H:%M'))

    def test_cancel_success_and_duplicate(self):
        self.created()
        url=self.url+'/action/cancel'
        with patch('courier.EcontClient.cancel_label') as api:
            self.assertEqual(self.c.get(url).status_code,200);api.assert_not_called()
            self.assertEqual(self.c.post(url,data=self.action_form).status_code,302)
            self.assertEqual(self.c.post(url,data=self.action_form).status_code,409)
            api.assert_called_once_with('DEMO123')
        self.assertEqual(self.shipment.state,'cancelled')
        self.assertEqual(Order.query.count(),1)
        self.assertIn('е анулирана',self.c.get(self.url).text)
        with patch('courier.EcontClient.status') as api:
            self.c.post(self.url+'/refresh',data={'csrf_token':'csrf'})
            api.assert_not_called()
        self.assertEqual(self.shipment.state,'cancelled')

    def test_pickup_payload_status_and_duplicate(self):
        self.created();url=self.url+'/action/pickup'
        with patch('courier.EcontClient.request_courier',return_value=('123','Часът подлежи на потвърждение')) as api:
            self.assertEqual(self.c.get(url).status_code,200)
            self.assertEqual(self.c.post(url,data=self.action_form).status_code,302)
            self.assertEqual(self.c.post(url,data=self.action_form).status_code,409)
            api.assert_called_once()
            payload=api.call_args.args[0]
            self.assertEqual(payload['attachShipments'],['DEMO123'])
            self.assertEqual(payload['senderAddress']['city']['name'],'София')
            self.assertGreater(payload['requestTimeFrom'],10**12)
        self.assertEqual(CourierAction.query.one().request_id,'123')
        self.assertEqual(Order.query.one().status,'нова')
        with patch('courier.EcontClient.courier_request_status',return_value={'id':123,'status':'process'}):
            self.c.post(self.url+'/pickup-status',data={'csrf_token':'csrf'})
        self.assertIn('Назначен е куриер',self.c.get(self.url).text)

    def test_unknown_result_blocks_other_actions_refresh_and_delete(self):
        self.created()
        with patch('courier.EcontClient.request_courier',side_effect=EcontError('timeout',uncertain=True)):
            self.c.post(self.url+'/action/pickup',data=self.action_form)
        self.assertEqual(self.shipment.state,'pickup_unknown')
        with patch('courier.EcontClient.cancel_label') as api:
            self.assertEqual(self.c.post(self.url+'/action/cancel',data=self.action_form).status_code,409)
            api.assert_not_called()
        with patch('courier.EcontClient.status') as api:
            self.c.post(self.url+'/refresh',data={'csrf_token':'csrf'});api.assert_not_called()
        delete=f'/admin/orders/{self.order_id}/delete'
        self.c.get(delete)
        with self.c.session_transaction() as session:token=session['order_delete_csrf']
        self.c.post(delete,data={'csrf_token':token,'confirm_order_id':str(self.order_id)})
        self.assertEqual(Order.query.count(),1)

    def test_definite_failure_retries(self):
        self.created();url=self.url+'/action/cancel'
        with patch('courier.EcontClient.cancel_label',side_effect=EcontError('отказ')):
            self.c.post(url,data=self.action_form)
        self.assertEqual(self.shipment.state,'created')
        self.assertEqual(CourierAction.query.one().state,'failed')
        with patch('courier.EcontClient.cancel_label') as api:
            self.c.post(url,data=self.action_form);api.assert_called_once()

    def test_security_time_and_environment(self):
        self.created();url=self.url+'/action/pickup'
        with patch('courier.EcontClient.request_courier') as api:
            self.assertEqual(app.test_client().post(url,data=self.action_form).status_code,302)
            self.assertEqual(self.c.post(url,data={**self.action_form,'csrf_token':'bad'}).status_code,400)
            self.assertEqual(self.c.post(url,data={**self.action_form,'confirm':'wrong'}).status_code,400)
            self.assertEqual(self.c.post(url,data={**self.action_form,'time_to':self.action_form['time_from']}).status_code,400)
            self.shipment.environment='live';db.session.commit()
            self.assertEqual(self.c.post(url,data=self.action_form).status_code,404)
            api.assert_not_called()

    def test_transport_requires_matching_confirmation(self):
        client=EcontClient(app.config)
        for result in ({}, {'results':[]}, {'results':[{'shipmentNum':'OTHER','error':None}]}):
            with patch.object(client,'call',return_value=result):
                with self.assertRaises(EcontError) as error:client.cancel_label('DEMO123')
                self.assertTrue(error.exception.uncertain)
        with patch.object(client,'call',return_value={'results':[{'shipmentNum':'DEMO123','error':{'message':' ','innerErrors':[{'message':'Приета пратка'}]}}]}):
            with self.assertRaises(EcontError) as error:client.cancel_label('DEMO123')
            self.assertIn('Приета пратка',str(error.exception));self.assertFalse(error.exception.uncertain)
        with patch.object(client,'call',return_value={}):
            with self.assertRaises(EcontError) as error:client.request_courier({})
            self.assertTrue(error.exception.uncertain)
