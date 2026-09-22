import unittest
from unittest.mock import patch
import test_courier as fixture
from models import CourierShipment
from courier import build_label
from models import Order

class SenderAddressDefaultTests(unittest.TestCase):
    setUp=fixture.CourierTests.setUp
    tearDown=fixture.CourierTests.tearDown

    def test_address_default(self):
        html=self.c.get(self.url).text
        self.assertIn('<option value="address" selected>',html)
        self.assertNotIn('<option value="office" selected>Офис на Еконт',html)

    def test_pickup_prefills_saved_address_without_calling_econt(self):
        form={**self.form,'sender_type':'address','sender_city':'София','sender_post_code':'1000','sender_address':'Тестова улица 1','sender_office':''}
        with patch('courier.EcontClient.label',return_value=self.status):
            self.assertEqual(self.c.post(self.url,data=form).status_code,302)
        with patch('courier.EcontClient.request_courier') as api:
            html=self.c.get(self.url+'/action/pickup').text
            self.assertIn('value="София"',html)
            self.assertIn('value="1000"',html)
            self.assertIn('value="Тестова улица 1"',html)
            api.assert_not_called()
        self.assertEqual(CourierShipment.query.one().environment,'test')
        self.assertNotIn('senderOfficeCode',build_label(Order.query.one(),form))
        self.assertEqual(build_label(Order.query.one(),self.form)['senderOfficeCode'],'1000')
