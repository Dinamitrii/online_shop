import unittest
from unittest.mock import patch
import test_courier as fixture
from app import app
from courier import build_label
from models import Order
from econt import EcontClient, EcontError

class ProfileTests(unittest.TestCase):
    setUp=fixture.CourierTests.setUp
    tearDown=fixture.CourierTests.tearDown

    def test_profile_endpoint_auth_and_no_cache(self):
        with patch('courier.EcontClient.profiles',return_value=[{'name':'Фирма','phones':['0888888888'],'addresses':[]}]) as api:
            self.assertEqual(app.test_client().get('/admin/econt/profiles').status_code,302)
            api.assert_not_called()
            response=self.c.get('/admin/econt/profiles')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.headers['Cache-Control'],'no-store')
            self.assertEqual(response.json['profiles'][0]['name'],'Фирма')
        with patch('courier.EcontClient.profiles',side_effect=EcontError('Отказан достъп')):
            response=self.c.get('/admin/econt/profiles')
            self.assertEqual(response.status_code,502)
            self.assertIn('Отказан',response.json['error'])

    def test_whitelist_and_structured_addresses(self):
        client=EcontClient(app.config)
        raw={'profiles':[{'client':{'name':'Фирма','phones':['0888888888'],'personalIDNumber':'SECRET','molEGN':'SECRET'},'addresses':[{'city':{'name':'София','postCode':'1000','country':{'code2':'BGR'}},'fullAddress':None,'street':'Тестова','num':'1','other':'ет 2'}],'cdPayOptions':[{'IBAN':'SECRET'}]}]}
        with patch.object(client,'call',return_value=raw) as api:
            result=client.profiles();api.assert_called_once_with('Profile/ProfileService.getClientProfiles',{})
        self.assertNotIn('SECRET',str(result))
        self.assertEqual(result[0]['addresses'][0]['address'],'Тестова 1 ет 2')
        with patch.object(client,'call',return_value={'profiles':[]}):self.assertEqual(client.profiles(),[])
        with patch.object(client,'call',return_value={}):
            with self.assertRaises(EcontError):client.profiles()

    def test_label_address_and_office_modes(self):
        order=Order.query.one()
        form={**self.form,'sender_type':'address','sender_city':'София','sender_post_code':'1000','sender_address':'Тестова 1','sender_office':''}
        label=build_label(order,form)
        self.assertNotIn('senderOfficeCode',label)
        self.assertEqual(label['senderAddress']['city']['name'],'София')
        self.assertEqual(build_label(order,self.form)['senderOfficeCode'],'1000')
        with self.assertRaises(ValueError):build_label(order,{**form,'sender_address':''})
        with self.assertRaises(ValueError):build_label(order,{**form,'sender_type':'bad'})

    def test_form_has_profile_picker_without_env_identity(self):
        with patch.dict(app.config,ECONT_SENDER_NAME='Old environment sender',ECONT_SENDER_PHONE='999'):
            html=self.c.get(self.url).text
        self.assertIn('Зареди подателя от Еконт',html)
        self.assertIn('sender_type',html)
        self.assertNotIn('Old environment sender',html)
