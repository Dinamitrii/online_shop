import unittest
from app import app, db


class ContactsMapTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='unit-test')
        self.ctx = app.app_context(); self.ctx.push()
        db.create_all()
        self.c = app.test_client()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def test_contacts_page_has_no_google_maps_iframe(self):
        page = self.c.get('/contacts').text
        self.assertNotIn('<iframe', page)
        self.assertNotIn('google.com/maps?q=', page)
        self.assertNotIn('google.com/maps/embed', page)

    def test_contacts_page_links_to_google_maps_search(self):
        page = self.c.get('/contacts').text
        self.assertIn('https://www.google.com/maps/search/?api=1&query=', page)
        self.assertIn('Отвори в Google Maps', page)
        self.assertIn('target="_blank"', page)
        self.assertIn('rel="noopener"', page)


if __name__ == '__main__':
    unittest.main()
