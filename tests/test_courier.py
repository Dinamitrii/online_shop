import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError

_temp = tempfile.TemporaryDirectory()
os.environ['SHOP_DATABASE_PATH'] = os.path.join(_temp.name, 'test.sqlite3')
from app import app
from models import db, Order, OrderItem, CourierShipment
from econt import EcontClient, EcontError, safe_pdf_url


class CourierTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='unit-test', COURIER_ENABLED=True,
                          COURIER_PROVIDER='econt', COURIER_ENVIRONMENT='test',
                          ECONT_USERNAME='test-user', ECONT_PASSWORD='test-password')
        self.ctx = app.app_context(); self.ctx.push()
        db.create_all()
        order = Order(customer_name='Тест Получател', phone='0888888888', address='Тест адрес', total=12.35)
        db.session.add(order); db.session.flush()
        db.session.add(OrderItem(order_id=order.id, product_name='Болт', qty=2, price=6.175))
        db.session.commit(); self.order_id = order.id
        self.url = f'/admin/orders/{order.id}/econt'
        self.c = app.test_client()
        with self.c.session_transaction() as s:s.update(is_admin=True, courier_csrf='csrf')
        self.form = dict(csrf_token='csrf', action='create', sender_name='Тест Подател',
                         sender_phone='0888888888', sender_office='1000',
                         receiver_name='Тест Получател', receiver_phone='0888888888',
                         receiver_office='2000', delivery_type='office', weight='1.5',
                         pack_count='1', payer='receiver', cod='1')
        self.status = {'shipmentNumber': 'DEMO123', 'pdfURL': 'https://demo.econt.com/test.pdf',
                       'shortDeliveryStatus': 'Подготвена', 'totalPrice': 5.5, 'currency': 'EUR'}

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def test_admin_and_csrf_required(self):
        with patch('courier.EcontClient.label') as api:
            self.assertEqual(app.test_client().post(self.url, data=self.form).status_code, 302)
            self.assertEqual(self.c.post(self.url, data={**self.form, 'csrf_token':'bad'}).status_code, 400)
            api.assert_not_called()

    def test_form_and_order_link(self):
        self.assertIn('Създай тестова товарителница',self.c.get(self.url).text)
        self.assertIn('Товарителница и печат',self.c.get(f'/admin/orders/{self.order_id}').text)

    def test_create_persists_and_prevents_duplicate(self):
        with patch('courier.EcontClient.label', return_value=self.status) as api:
            self.assertEqual(self.c.post(self.url,data=self.form).status_code,302)
            self.assertEqual(self.c.post(self.url,data=self.form).status_code,409)
            self.assertEqual(api.call_count,1)
            payload,mode=api.call_args.args
            self.assertEqual(mode,'create')
            self.assertEqual(payload['services'],{'cdAmount':12.35,'cdType':'get','cdCurrency':'EUR'})
            self.assertEqual(payload['paymentReceiverAmount'],100)
        shipment=CourierShipment.query.one()
        self.assertEqual(shipment.shipment_number,'DEMO123')
        self.assertEqual(self.c.get(self.url+'/pdf').location,self.status['pdfURL'])
        self.assertEqual(Order.query.one().status,'нова')

    def test_calculate_does_not_reserve_or_create(self):
        with patch('courier.EcontClient.label', return_value=self.status) as api:
            result=self.c.post(self.url,data={**self.form,'action':'calculate'})
            self.assertEqual(result.status_code,200)
            self.assertIn('5.5 EUR',result.text)
            self.assertEqual(api.call_args.args[1],'calculate')
        self.assertEqual(CourierShipment.query.count(),0)

    def test_timeout_blocks_retry_and_other_worker(self):
        with patch('courier.EcontClient.label', side_effect=EcontError('timeout',uncertain=True)) as api:
            self.assertEqual(self.c.post(self.url,data=self.form).status_code,502)
            self.assertEqual(self.c.post(self.url,data=self.form).status_code,409)
            self.assertEqual(api.call_count,1)
        self.assertEqual(CourierShipment.query.one().state,'uncertain')
        shipment=CourierShipment.query.one();shipment.state='pending';db.session.commit()
        with patch('courier.EcontClient.label') as api:
            self.assertEqual(self.c.post(self.url,data=self.form).status_code,409);api.assert_not_called()

    def test_definite_rejection_can_be_corrected(self):
        with patch('courier.EcontClient.label', side_effect=EcontError('Invalid office')):
            self.c.post(self.url,data=self.form)
        self.assertEqual(CourierShipment.query.one().state,'failed')
        with patch('courier.EcontClient.label',return_value=self.status):
            self.assertEqual(self.c.post(self.url,data=self.form).status_code,302)

    def test_invalid_inputs_never_call_api(self):
        with patch('courier.EcontClient.label') as api:
            for change in [{'weight':'NaN'},{'weight':'Infinity'},{'weight':'0'},
                           {'pack_count':'-1'},{'delivery_type':'bad'},{'receiver_office':''},
                           {'payer':'bad'},{'action':'delete'}]:
                self.assertEqual(self.c.post(self.url,data={**self.form,**change}).status_code,400,change)
            api.assert_not_called()

    def test_address_and_no_cod(self):
        form={**self.form,'delivery_type':'address','city':'Русе','post_code':'7010',
              'address':'ул. Муткурова 84','cod':'0','payer':'sender'}
        with patch('courier.EcontClient.label',return_value=self.status) as api:
            self.assertEqual(self.c.post(self.url,data=form).status_code,302)
            payload=api.call_args.args[0]
            self.assertNotIn('receiverOfficeCode',payload)
            self.assertNotIn('services',payload)
            self.assertNotIn('paymentReceiverAmount',payload)
            self.assertEqual(payload['receiverAddress']['city']['name'],'Русе')

    def test_disabled_and_demo_live_guard(self):
        with patch('courier.EcontClient.label') as api:
            app.config['COURIER_ENABLED']=False
            self.assertEqual(self.c.post(self.url,data=self.form).status_code,400)
            app.config.update(COURIER_ENABLED=True,COURIER_ENVIRONMENT='live',ECONT_USERNAME='iasp-dev')
            self.assertEqual(self.c.post(self.url,data=self.form).status_code,400)
            api.assert_not_called()

    def test_environment_separation(self):
        with patch('courier.EcontClient.label',return_value=self.status):self.c.post(self.url,data=self.form)
        app.config['COURIER_ENVIRONMENT']='live'
        self.assertEqual(self.c.get(self.url+'/pdf').status_code,404)
        self.assertNotIn('Товарителница № DEMO123', self.c.get(self.url).text)

    def test_pdf_host_validation(self):
        for url in ['javascript:alert(1)','https://evil.test/a.pdf','https://demo.econt.com.evil.test/a',
                    'https://user@demo.econt.com/a','https://demo.econt.com:444/a',
                    'https://ee.econt.com/a']:
            self.assertEqual(safe_pdf_url(url,'test'),'')

    def test_legacy_pdf_link_is_upgraded_to_https(self):
        self.assertEqual(safe_pdf_url('http://demo.econt.com/ee/api_export.php?id=demo','test'),
                         'https://demo.econt.com/ee/api_export.php?id=demo')

    def test_status_refresh_and_reconcile(self):
        with patch('courier.EcontClient.label',side_effect=EcontError('timeout',uncertain=True)):
            self.c.post(self.url,data=self.form)
        shipment=CourierShipment.query.one(); original=json.loads(shipment.request_json)
        bad={**self.status,'receiverClient':{},'shipmentDescription':'other'}
        with patch('courier.EcontClient.status',return_value=bad):
            self.c.post(self.url+'/refresh',data={'csrf_token':'csrf','shipment_number':'DEMO123'})
        self.assertEqual(shipment.state,'uncertain')
        good={**self.status,'receiverClient':original['receiverClient'],
              'shipmentDescription':original['shipmentDescription']}
        with patch('courier.EcontClient.status',return_value=good):
            self.c.post(self.url+'/refresh',data={'csrf_token':'csrf','shipment_number':'DEMO123'})
        self.assertEqual(shipment.state,'created')

    def test_transport_post_auth_and_errors(self):
        client=EcontClient(app.config)
        response=MagicMock();response.__enter__.return_value.read.return_value=b'{"offices":[]}'
        with patch.object(client.opener,'open',return_value=response) as opened:
            self.assertEqual(client.call('Nomenclatures/NomenclaturesService.getOffices',{}),{'offices':[]})
            req=opened.call_args.args[0]
            self.assertEqual(req.method,'POST');self.assertTrue(req.headers['Authorization'].startswith('Basic '))
            self.assertTrue(req.full_url.startswith('https://demo.econt.com/'))
        validation = HTTPError('url',517,'Validation',{},io.BytesIO(
            json.dumps({'type':'ExInvalidParam','message':'Невалиден офис'}).encode()))
        with patch.object(client.opener,'open',side_effect=validation):
            with self.assertRaises(EcontError) as raised:client.call('test',{})
            self.assertFalse(raised.exception.uncertain)
            self.assertIn('Невалиден офис',str(raised.exception))
        for error,uncertain in [(URLError('timeout'),True),
                 (HTTPError('url',401,'Unauthorized',{},None),False),
                 (HTTPError('url',500,'Error',{},None),True)]:
            with patch.object(client.opener,'open',side_effect=error):
                with self.assertRaises(EcontError) as raised:client.call('test',{})
                self.assertEqual(raised.exception.uncertain,uncertain)


if __name__=='__main__':unittest.main()
