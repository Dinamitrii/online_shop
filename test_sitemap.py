import os
import shutil
import tempfile
import unittest
from xml.etree import ElementTree as ET

# Базата трябва да е подменена ПРЕДИ app да се импортира.
_TMP_DIR = tempfile.mkdtemp(prefix='sitemap-test-')
os.environ['SHOP_DATABASE_PATH'] = os.path.join(_TMP_DIR, 'test.db')

from app import app, db  # noqa: E402
from models import Category, Product  # noqa: E402

NS = {
    's': 'http://www.sitemaps.org/schemas/sitemap/0.9',
    'image': 'http://www.google.com/schemas/sitemap-image/1.1',
}
BASE = 'https://e-jelezaria.bg'


def tearDownModule():
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


class SitemapTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='unit-test')
        self.ctx = app.app_context()
        self.ctx.push()

        # Защита: никога не пипаме истинската база.
        self.assertNotIn('store.db', str(db.engine.url))
        db.create_all()

        # Рутът записва static/sitemap.xml - пренасочваме го към временна папка.
        self.static_dir = tempfile.mkdtemp(prefix='sitemap-static-')
        self.orig_static = app.static_folder
        app.static_folder = self.static_dir

        category = Category(name='Тест категория')
        db.session.add(category)
        db.session.commit()
        in_stock = Product(name='Наличен продукт', price=5, stock=3, category_id=category.id)
        exhausted = Product(name='Изчерпан продукт', price=7, stock=0, category_id=category.id)
        db.session.add_all([in_stock, exhausted])
        db.session.commit()

        self.category_id = category.id
        self.in_stock_id = in_stock.id
        self.exhausted_id = exhausted.id
        self.c = app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        app.static_folder = self.orig_static
        shutil.rmtree(self.static_dir, ignore_errors=True)
        self.ctx.pop()

    def _urls(self):
        response = self.c.get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)
        self.assertIn('xml', response.content_type)
        root = ET.fromstring(response.data)
        return {u.find('s:loc', NS).text: u for u in root.findall('s:url', NS)}

    def test_lists_home_categories_and_products(self):
        urls = self._urls()
        self.assertIn(f'{BASE}/', urls)
        self.assertIn(f'{BASE}/category/{self.category_id}', urls)
        self.assertIn(f'{BASE}/product/{self.in_stock_id}', urls)
        self.assertIn(f'{BASE}/product/{self.exhausted_id}', urls)

    def test_no_legacy_paths(self):
        xml = self.c.get('/sitemap.xml').text
        self.assertNotIn('/za-nas', xml)
        self.assertNotIn('/kontakti', xml)

    def test_dated_entries_have_valid_lastmod(self):
        urls = self._urls()
        for path in (f'/product/{self.in_stock_id}', f'/category/{self.category_id}', '/'):
            lastmod = urls[BASE + path].find('s:lastmod', NS)
            self.assertIsNotNone(lastmod, path)
            self.assertRegex(lastmod.text, r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')

    def test_every_page_url_has_changefreq_and_priority(self):
        # /robots.txt е в sitemap-а при теб, затова се пропуска.
        for loc, url in self._urls().items():
            if loc.endswith('/robots.txt'):
                continue
            self.assertIsNotNone(url.find('s:changefreq', NS), loc)
            self.assertIsNotNone(url.find('s:priority', NS), loc)

    def test_sitemap_is_written_to_static_folder(self):
        self.c.get('/sitemap.xml')
        path = os.path.join(self.static_dir, 'sitemap.xml')
        self.assertTrue(os.path.isfile(path))
        ET.parse(path)  # валиден XML

    def test_static_sitemap_route_matches(self):
        a = ET.fromstring(self.c.get('/sitemap.xml').data)
        b = ET.fromstring(self.c.get('/static/sitemap.xml').data)
        locs = lambda r: sorted(e.text for e in r.iter(f"{{{NS['s']}}}loc"))
        self.assertEqual(locs(a), locs(b))


if __name__ == '__main__':
    unittest.main()
