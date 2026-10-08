import unittest
from xml.etree import ElementTree as ET
import test_availability as fixture
from app import app
from models import db


class DetachedMergeTests(unittest.TestCase):
    setUp = fixture.AvailabilityTests.setUp
    tearDown = fixture.AvailabilityTests.tearDown
    make = fixture.AvailabilityTests.make

    def test_sitemap_includes_products_categories_images_and_returns(self):
        product = self.make(image_url='/static/img/example.png')
        response = self.c.get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)
        root = ET.fromstring(response.data)
        ns = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9',
              'image': 'http://www.google.com/schemas/sitemap-image/1.1'}
        entries = {entry.find('s:loc', ns).text: entry for entry in root.findall('s:url', ns)}
        base = 'https://e-jelezaria.bg'
        for path in ('/', '/contacts', '/returns', f'/category/{self.category.id}', f'/product/{product.id}'):
            self.assertIn(base + path, entries)
            self.assertIsNotNone(entries[base + path].find('s:lastmod', ns))
        image = entries[base + f'/product/{product.id}'].find('image:image/image:loc', ns)
        self.assertEqual(image.text, base + product.image_url)

    def test_checkout_preserves_stock_fix_and_confirmation_privacy(self):
        product = self.make(stock=2)
        buyer = app.test_client()
        buyer.post(f'/cart/add/{product.id}', data={'qty': 1})
        response = buyer.post('/checkout', data=dict(name='Клиент', phone='123', address='Адрес'))
        self.assertEqual(buyer.get(response.location).status_code, 200)
        self.assertEqual(app.test_client().get(response.location).status_code, 404)
        self.assertEqual(self.c.get(response.location).status_code, 200)
        db.session.expire_all()
        self.assertEqual(product.stock, 1)

    def test_local_fonts_and_styles_render(self):
        page = self.c.get('/').text
        self.assertIn('shop-local-fonts', page)
        self.assertIn('shop-main-css', page)
        self.assertEqual(self.c.get('/_shop-fonts/305ea448603057c5.woff2').status_code, 200)
        self.assertEqual(self.c.get('/_shop-fonts/missing.woff2').status_code, 404)
