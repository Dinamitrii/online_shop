from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Category(db.Model):
    __tablename__ = 'categories'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    image_url = db.Column(db.String(300), default='')

    products = db.relationship('Product', backref='category', lazy=True)

    def __repr__(self):
        return f'<Category {self.name}>'


class Product(db.Model):
    __tablename__ = 'products'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    price = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Integer, default=0)
    # 'in_stock' (обикновена наличност), 'limited' (ограничена — предупреждение, но поръчваема),
    # 'on_order' (по поръчка — брои се същото поле 'stock', но означава наличен за поръчка брой).
    availability = db.Column(db.String(16), nullable=False, default='in_stock')
    image_url = db.Column(db.String(300), default='')
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False)

    AVAILABILITY_CHOICES = ('in_stock', 'limited', 'on_order')
    AVAILABILITY_LABELS = {'in_stock': 'В наличност', 'limited': 'Ограничена наличност',
                           'on_order': 'По поръчка'}
    # CSS клас (static/css/style.css) и икона за етикета на статуса — за витрината и админ панела.
    AVAILABILITY_BADGE_CLASS = {'in_stock': 'stock-ok', 'limited': 'stock-limited', 'on_order': 'stock-onorder'}
    AVAILABILITY_ICONS = {'in_stock': '✔', 'limited': '⚠', 'on_order': '⏳'}

    @property
    def in_stock_for_order(self):
        """Дали продуктът може да се поръча в момента — важи и за трите статуса,
        включително „По поръчка“, чиято наличност също е ограничена от полето stock."""
        return self.stock > 0

    def __repr__(self):
        return f'<Product {self.name}>'


class Order(db.Model):
    __tablename__ = 'orders'

    STATUSES = ['нова', 'в обработка', 'изпратена', 'завършена', 'отказана']

    id = db.Column(db.Integer, primary_key=True)
    customer_name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(50), nullable=False)
    address = db.Column(db.String(300), nullable=False)
    email = db.Column(db.String(150), default='')
    total = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(30), default='нова', nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    items = db.relationship('OrderItem', backref='order', lazy=True)


class OrderItem(db.Model):
    __tablename__ = 'order_items'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    product_name = db.Column(db.String(200), nullable=False)
    price = db.Column(db.Float, nullable=False)
    qty = db.Column(db.Integer, nullable=False)

    @property
    def subtotal(self):
        return self.price * self.qty


class CourierShipment(db.Model):
    """Durable reservation prevents duplicate labels across workers and retries."""
    __tablename__ = 'courier_shipments'
    __table_args__ = (db.UniqueConstraint('order_id', 'environment'),)

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    environment = db.Column(db.String(8), nullable=False)
    state = db.Column(db.String(16), nullable=False, default='pending')
    shipment_number = db.Column(db.String(64), nullable=True)
    pdf_url = db.Column(db.Text, default='')
    delivery_status = db.Column(db.String(200), default='')
    error_message = db.Column(db.String(600), default='')
    request_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CourierAction(db.Model):
    """Persist external operations before sending them; never retry an unknown result."""
    __tablename__ = 'courier_actions'
    __table_args__ = (db.UniqueConstraint('shipment_id', 'kind'),)
    id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey('courier_shipments.id'), nullable=False)
    kind = db.Column(db.String(16), nullable=False)
    state = db.Column(db.String(16), nullable=False, default='pending')
    request_id = db.Column(db.String(64), default='')
    request_json = db.Column(db.Text, nullable=False, default='{}')
    message = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
