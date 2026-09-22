import os
import tempfile
import unittest

_temp = tempfile.TemporaryDirectory()
os.environ.setdefault('SHOP_DATABASE_PATH', os.path.join(_temp.name, 'test.sqlite3'))
from app import app
from models import db, Category, Product


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY='unit-test')
        self.ctx = app.app_context(); self.ctx.push()
        db.create_all()
        self.category = Category(name='Тест категория')
        db.session.add(self.category); db.session.commit()
        self.c = app.test_client()
        with self.c.session_transaction() as s: s['is_admin'] = True

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def make(self, **kwargs):
        defaults = dict(name='Тестов продукт', price=5.0, stock=3, availability='in_stock',
                        category_id=self.category.id)
        defaults.update(kwargs)
        product = Product(**defaults)
        db.session.add(product); db.session.commit()
        return product

    def new_form(self, **overrides):
        data = dict(name='Нов продукт', price='9.90', stock='4', availability='limited',
                   category_id=str(self.category.id), description='')
        data.update(overrides)
        return self.c.post('/admin/products/new', data=data, follow_redirects=False)

    # ---- default and choices ----
    def test_default_availability_is_in_stock(self):
        product = self.make()
        self.assertEqual(product.availability, 'in_stock')

    def test_admin_form_shows_all_three_choices_and_marks_current(self):
        product = self.make(availability='limited')
        page = self.c.get(f'/admin/products/{product.id}/edit').text
        for value in Product.AVAILABILITY_CHOICES:
            self.assertIn(f'value="{value}"', page)
        self.assertIn('value="limited" selected', page)

    def test_new_product_rejects_invalid_availability(self):
        response = self.new_form(availability='not-a-real-status')
        self.assertEqual(response.status_code, 200)  # re-renders the form with an error
        self.assertEqual(Product.query.count(), 0)

    def test_new_product_saves_chosen_availability(self):
        response = self.new_form(availability='on_order', stock='7')
        self.assertEqual(response.status_code, 302)
        product = Product.query.one()
        self.assertEqual((product.availability, product.stock), ('on_order', 7))

    def test_edit_product_updates_availability(self):
        product = self.make(availability='in_stock')
        response = self.c.post(f'/admin/products/{product.id}/edit',
                               data=dict(name=product.name, price='5.0', stock='2',
                                        availability='on_order', description='',
                                        category_id=str(self.category.id)))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Product.query.get(product.id).availability, 'on_order')

    # ---- storefront badges ----
    def test_product_page_shows_matching_badge_for_each_status(self):
        expectations = {'in_stock': 'В наличност', 'limited': 'Ограничена наличност', 'on_order': 'По поръчка'}
        for status, label in expectations.items():
            product = self.make(name=f'Продукт {status}', availability=status)
            page = self.c.get(f'/product/{product.id}').text
            self.assertIn(label, page)

    def test_out_of_stock_shows_exhausted_regardless_of_status(self):
        exhausted = self.make(availability='on_order', stock=0)
        page = self.c.get(f'/product/{exhausted.id}').text
        self.assertIn('Изчерпан', page)
        self.assertNotIn('По поръчка', page)
        self.assertNotIn('name="qty"', page)  # no add-to-cart form when nothing can be ordered

    def test_listing_pages_show_badge_and_hide_button_when_out_of_stock(self):
        self.make(name='Наличен чук', availability='in_stock', stock=5)
        self.make(name='Изчерпана ножица', availability='limited', stock=0)
        page = self.c.get(f'/category/{self.category.id}').text
        self.assertIn('В наличност', page)
        self.assertIn('Изчерпан', page)
        # the exhausted product must not offer an add-to-cart button
        self.assertNotIn('action="/cart/add/', page.split('Изчерпана ножица')[1].split('</div>')[0])

    def test_on_order_button_says_zayavi(self):
        self.make(availability='on_order', stock=5)
        page = self.c.get('/').text
        self.assertIn('Заяви по поръчка', page)

    # ---- cart limits follow the stock field for every status ----
    def test_cart_add_blocked_when_stock_is_zero_for_any_status(self):
        for status in Product.AVAILABILITY_CHOICES:
            exhausted = self.make(name=f'{status} нула', availability=status, stock=0)
            response = self.c.post(f'/cart/add/{exhausted.id}', data={'qty': '1'}, follow_redirects=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('не е наличен в момента', response.text)
            self.assertIn('Количката е празна', self.c.get('/cart').text)

    def test_cart_add_caps_quantity_to_stock_for_limited_and_on_order(self):
        limited = self.make(name='Ограничен продукт', availability='limited', stock=3)
        self.c.post(f'/cart/add/{limited.id}', data={'qty': '10'})
        on_order = self.make(name='Продукт по поръчка', availability='on_order', stock=6)
        self.c.post(f'/cart/add/{on_order.id}', data={'qty': '10'})
        cart_page = self.c.get('/cart').text
        self.assertIn('3', cart_page)
        self.assertIn('6', cart_page)

    def test_cart_add_message_mentions_on_order_wording(self):
        product = self.make(availability='on_order', stock=2)
        response = self.c.post(f'/cart/add/{product.id}', data={'qty': '5'}, follow_redirects=True)
        self.assertIn('по поръчка може да се заявят', response.text)

    def test_cart_update_also_caps_to_stock(self):
        product = self.make(availability='limited', stock=4)
        self.c.post(f'/cart/add/{product.id}', data={'qty': '1'})
        self.c.post(f'/cart/update/{product.id}', data={'qty': '50'})
        self.assertIn('4', self.c.get('/cart').text)


if __name__ == '__main__':
    unittest.main()
