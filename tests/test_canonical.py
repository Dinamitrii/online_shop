import unittest
from html.parser import HTMLParser
import test_availability as fixture
from app import app


class CanonicalParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.urls = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'link' and attrs.get('rel') == 'canonical':
            self.urls.append(attrs.get('href'))


class CanonicalTests(unittest.TestCase):
    setUp = fixture.AvailabilityTests.setUp
    tearDown = fixture.AvailabilityTests.tearDown
    make = fixture.AvailabilityTests.make

    def test_public_pages_use_https_non_www_and_remove_duplicate_parameters(self):
        product = self.make()
        paths = ['/', '/contacts', '/returns', f'/product/{product.id}', f'/category/{self.category.id}']
        for path in paths:
            with self.subTest(path=path):
                response = self.c.get(path + '?sort=price_asc&utm_source=test', base_url='http://localhost')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(CanonicalParser(response.text).urls, ['https://e-jelezaria.bg' + path])

    def test_non_public_pages_have_no_canonical(self):
        for path in ['/search?q=test', '/cart', '/admin', '/missing']:
            with self.subTest(path=path):
                self.assertEqual(CanonicalParser(self.c.get(path).text).urls, [])

    def test_www_redirect_preserves_path_and_query(self):
        response = self.c.get('/contacts?utm_source=test', base_url='http://www.e-jelezaria.bg')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.location, 'https://e-jelezaria.bg/contacts?utm_source=test')

    def test_canonical_domain_and_localhost_do_not_redirect(self):
        for base in ['https://e-jelezaria.bg', 'http://localhost']:
            self.assertEqual(self.c.get('/contacts', base_url=base).status_code, 200)

    def test_post_is_not_redirected_to_another_domain(self):
        response = self.c.post('/checkout', base_url='https://www.e-jelezaria.bg')
        self.assertNotEqual(response.status_code, 301)
