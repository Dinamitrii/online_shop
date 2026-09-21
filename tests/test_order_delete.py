import unittest
from unittest.mock import patch
import test_courier as fixture
from app import app
from models import db, Order, OrderItem, CourierShipment


class DeleteOrderTests(unittest.TestCase):
    setUp = fixture.CourierTests.setUp
    tearDown = fixture.CourierTests.tearDown

    def prepare(self):
        self.delete_url = f'/admin/orders/{self.order_id}/delete'
        self.assertEqual(self.c.get(self.delete_url).status_code, 200)
        with self.c.session_transaction() as s:
            return dict(csrf_token=s['order_delete_csrf'], confirm_order_id=str(self.order_id))

    def test_confirmation_security_and_get_preserves_order(self):
        form = self.prepare()
        self.assertEqual(Order.query.count(), 1)
        self.assertEqual(app.test_client().post(self.delete_url,data=form).status_code,302)
        self.assertEqual(self.c.post(self.delete_url,data={**form,'csrf_token':'wrong'}).status_code,400)
        self.assertEqual(self.c.post(self.delete_url,data={**form,'confirm_order_id':'99999'}).status_code,400)
        self.assertEqual(Order.query.count(),1)

    def test_deletes_related_records_and_preserves_other_orders(self):
        form=self.prepare()
        other=Order(customer_name='Друг клиент',phone='000',address='Тест',total=10)
        db.session.add(other); db.session.flush()
        other_id=other.id
        db.session.add(OrderItem(order_id=other_id,product_name='Друг артикул',price=10,qty=1))
        for environment in ('test','live'):
            db.session.add(CourierShipment(order_id=self.order_id,environment=environment,state='created',request_json='{}',shipment_number='TEST'))
        db.session.commit()
        result=self.c.post(self.delete_url,data=form)
        self.assertEqual(result.status_code,302)
        self.assertEqual(result.location,'/admin/orders')
        self.assertIsNone(db.session.get(Order,self.order_id))
        self.assertEqual(CourierShipment.query.count(),0)
        self.assertEqual(OrderItem.query.count(),1)
        self.assertIsNotNone(db.session.get(Order,other_id))
        self.assertEqual(self.c.post(self.delete_url,data=form).status_code,404)

    def test_pending_and_uncertain_block_deletion(self):
        form=self.prepare()
        shipment=CourierShipment(order_id=self.order_id,environment='test',state='pending',request_json='{}')
        db.session.add(shipment); db.session.commit()
        for state in ('pending','uncertain'):
            shipment.state=state;db.session.commit()
            result=self.c.post(self.delete_url,data=form)
            self.assertIn('/delete',result.location)
            self.assertEqual(Order.query.count(),1)
            self.assertEqual(OrderItem.query.count(),1)

    def test_failure_rolls_back_children(self):
        form=self.prepare()
        with patch.object(db.session,'commit',side_effect=RuntimeError('test')):
            self.c.post(self.delete_url,data=form)
        self.assertEqual(Order.query.count(),1)
        self.assertEqual(OrderItem.query.count(),1)


class DeleteOrderExtraTests(unittest.TestCase):
    setUp = fixture.CourierTests.setUp
    tearDown = fixture.CourierTests.tearDown

    def test_non_ascii_token_gives_400_not_500(self):
        url = f'/admin/orders/{self.order_id}/delete'
        self.c.get(url)
        response = self.c.post(url, data={'csrf_token': 'грешен', 'confirm_order_id': str(self.order_id)})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.query.count(), 1)

    def test_delete_buttons_are_shown_and_needs_admin(self):
        self.assertIn(f'/admin/orders/{self.order_id}/delete', self.c.get('/admin/orders').text)
        self.assertIn(f'/admin/orders/{self.order_id}/delete', self.c.get(f'/admin/orders/{self.order_id}').text)
        self.assertEqual(app.test_client().get(f'/admin/orders/{self.order_id}/delete').status_code, 302)
