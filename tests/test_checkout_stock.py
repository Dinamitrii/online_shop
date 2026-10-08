import unittest
from unittest.mock import patch
import test_availability as fixture
from app import app
from models import db, Product, Order, OrderItem


class CheckoutStockTests(unittest.TestCase):
    setUp = fixture.AvailabilityTests.setUp
    tearDown = fixture.AvailabilityTests.tearDown
    make = fixture.AvailabilityTests.make
    form = dict(name='Клиент', phone='0888888888', address='Тест адрес')

    def cart(self, client, products):
        with client.session_transaction() as session:
            session['cart'] = {str(product.id): qty for product, qty in products}

    def test_purchase_decrements_all_availability_types_and_clears_cart(self):
        products = [self.make(name=status, availability=status) for status in Product.AVAILABILITY_CHOICES]
        self.cart(self.c, [(product, 2) for product in products])
        response = self.c.post('/checkout', data=self.form)
        self.assertIn('/confirmation', response.location)
        db.session.expire_all()
        self.assertEqual([product.stock for product in products], [1, 1, 1])
        self.assertEqual(Order.query.one().total, 30)
        self.assertEqual(OrderItem.query.count(), 3)
        with self.c.session_transaction() as session:
            self.assertEqual(session['cart'], {})

    def test_second_customer_cannot_buy_units_already_purchased(self):
        product = self.make(stock=1)
        other = app.test_client()
        for client in (self.c, other):
            self.cart(client, [(product, 1)])
        self.c.post('/checkout', data=self.form)
        response = other.post('/checkout', data=self.form)
        self.assertEqual(response.location, '/cart')
        db.session.expire_all()
        self.assertEqual(product.stock, 0)
        self.assertEqual(Order.query.count(), 1)
        with other.session_transaction() as session:
            self.assertEqual(session['cart'], {str(product.id): 1})

    def test_insufficient_stock_rolls_back_entire_cart(self):
        first = self.make(stock=5)
        second = self.make(name='Друг продукт', stock=1)
        self.cart(self.c, [(first, 2), (second, 2)])
        self.assertEqual(self.c.post('/checkout', data=self.form).location, '/cart')
        db.session.expire_all()
        self.assertEqual((first.stock, second.stock), (5, 1))
        self.assertEqual(Order.query.count(), 0)
        self.assertEqual(OrderItem.query.count(), 0)

    def test_invalid_customer_details_do_not_reserve_stock(self):
        product = self.make()
        self.cart(self.c, [(product, 1)])
        self.assertEqual(self.c.post('/checkout', data={}).status_code, 200)
        db.session.expire_all()
        self.assertEqual(product.stock, 3)
        self.assertEqual(Order.query.count(), 0)

    def test_failed_commit_restores_stock_and_preserves_cart(self):
        product = self.make()
        self.cart(self.c, [(product, 2)])
        with patch.object(db.session, 'commit', side_effect=RuntimeError('test')):
            with self.assertRaises(RuntimeError):
                self.c.post('/checkout', data=self.form)
        db.session.expire_all()
        self.assertEqual(product.stock, 3)
        self.assertEqual(Order.query.count(), 0)
        with self.c.session_transaction() as session:
            self.assertEqual(session['cart'], {str(product.id): 2})

    def test_deleted_product_prevents_partial_order(self):
        product = self.make()
        self.cart(self.c, [(product, 1)])
        with self.c.session_transaction() as session:
            session['cart'] = {str(product.id): 1, '999999': 1}
        self.assertEqual(self.c.post('/checkout', data=self.form).location, '/cart')
        db.session.expire_all()
        self.assertEqual(product.stock, 3)
        self.assertEqual(Order.query.count(), 0)
