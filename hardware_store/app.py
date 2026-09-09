import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
from models import db, Category, Product, Order, OrderItem

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config['SECRET_KEY'] = 'zamenete-tozi-kliuch-s-nesto-sekretno'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'store.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)


# ---------- Помощни функции за количката (пазена в session) ----------

def get_cart():
    """Връща количката като dict {product_id(str): quantity}"""
    return session.setdefault('cart', {})


def cart_items_with_products():
    cart = get_cart()
    items = []
    total = 0
    if cart:
        ids = [int(pid) for pid in cart.keys()]
        products = Product.query.filter(Product.id.in_(ids)).all()
        products_by_id = {p.id: p for p in products}
        for pid, qty in cart.items():
            product = products_by_id.get(int(pid))
            if product:
                subtotal = product.price * qty
                total += subtotal
                items.append({'product': product, 'qty': qty, 'subtotal': subtotal})
    return items, total


# ---------------------------- Публични страници ----------------------------

@app.route('/')
def index():
    categories = Category.query.all()
    featured = Product.query.order_by(Product.id.desc()).limit(8).all()
    return render_template('index.html', categories=categories, featured=featured)


@app.route('/category/<int:category_id>')
def category_view(category_id):
    category = Category.query.get_or_404(category_id)
    categories = Category.query.all()
    q = request.args.get('q', '').strip()
    products_query = Product.query.filter_by(category_id=category.id)
    if q:
        products_query = products_query.filter(Product.name.ilike(f'%{q}%'))
    products = products_query.all()
    return render_template('category.html', category=category, products=products,
                            categories=categories, q=q)


@app.route('/search')
def search():
    q = request.args.get('q', '').strip()
    categories = Category.query.all()
    products = []
    if q:
        products = Product.query.filter(Product.name.ilike(f'%{q}%')).all()
    return render_template('search.html', products=products, q=q, categories=categories)


@app.route('/product/<int:product_id>')
def product_view(product_id):
    product = Product.query.get_or_404(product_id)
    categories = Category.query.all()
    related = Product.query.filter(
        Product.category_id == product.category_id,
        Product.id != product.id
    ).limit(4).all()
    return render_template('product.html', product=product, related=related, categories=categories)


# ---------------------------- Количка ----------------------------

@app.route('/cart/add/<int:product_id>', methods=['POST'])
def cart_add(product_id):
    product = Product.query.get_or_404(product_id)
    qty = max(1, int(request.form.get('qty', 1)))
    cart = get_cart()
    key = str(product_id)
    cart[key] = cart.get(key, 0) + qty
    session['cart'] = cart
    session.modified = True
    flash(f'"{product.name}" е добавен в количката.', 'success')
    return redirect(request.referrer or url_for('index'))


@app.route('/cart/update/<int:product_id>', methods=['POST'])
def cart_update(product_id):
    cart = get_cart()
    key = str(product_id)
    qty = int(request.form.get('qty', 1))
    if qty <= 0:
        cart.pop(key, None)
    else:
        cart[key] = qty
    session['cart'] = cart
    session.modified = True
    return redirect(url_for('cart_view'))


@app.route('/cart/remove/<int:product_id>')
def cart_remove(product_id):
    cart = get_cart()
    cart.pop(str(product_id), None)
    session['cart'] = cart
    session.modified = True
    return redirect(url_for('cart_view'))


@app.route('/cart')
def cart_view():
    categories = Category.query.all()
    items, total = cart_items_with_products()
    return render_template('cart.html', items=items, total=total, categories=categories)


# ---------------------------- Поръчка ----------------------------

@app.route('/checkout', methods=['GET', 'POST'])
def checkout():
    categories = Category.query.all()
    items, total = cart_items_with_products()

    if not items:
        flash('Количката е празна.', 'error')
        return redirect(url_for('index'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        address = request.form.get('address', '').strip()
        email = request.form.get('email', '').strip()

        if not name or not phone or not address:
            flash('Моля, попълнете име, телефон и адрес.', 'error')
            return render_template('checkout.html', items=items, total=total,
                                    categories=categories)

        order = Order(customer_name=name, phone=phone, address=address,
                       email=email, total=total)
        db.session.add(order)
        db.session.flush()  # за да получим order.id

        for item in items:
            order_item = OrderItem(
                order_id=order.id,
                product_id=item['product'].id,
                product_name=item['product'].name,
                price=item['product'].price,
                qty=item['qty']
            )
            db.session.add(order_item)

        db.session.commit()

        session['cart'] = {}
        session.modified = True

        return redirect(url_for('order_confirmation', order_id=order.id))

    return render_template('checkout.html', items=items, total=total, categories=categories)


@app.route('/order/<int:order_id>/confirmation')
def order_confirmation(order_id):
    order = Order.query.get_or_404(order_id)
    categories = Category.query.all()
    return render_template('confirmation.html', order=order, categories=categories)


# ---------------------------- Инициализация на БД + примерни данни ----------------------------

def seed_data():
    if Category.query.first():
        return  # вече има данни

    categories_data = {
        'Ръчни инструменти': [
            ('Чук дърводелски 500г', 14.90, 'Стоманена глава, дървена дръжка, за общи ремонтни дейности.', 40),
            ('Комплект отвертки 6бр', 19.50, 'Комбиниран комплект права и кръстата глава, ергономична дръжка.', 60),
            ('Клещи комбинирани 180мм', 11.20, 'Хромирани, за рязане и захващане на тел и кабел.', 35),
            ('Ножовка за метал', 8.40, 'С резервно острие, за прецизно рязане.', 25),
            ('Метър рулетка 5м', 6.90, 'Стоманена лента с автоматично прибиране и спирачка.', 80),
        ],
        'Електроинструменти': [
            ('Акумулаторен винтоверт 18V', 89.00, 'С 2 батерии и куфар, до 45Nm въртящ момент.', 20),
            ('Ъглошлайф 125мм 900W', 64.50, 'За рязане и шлайфане на метал и камък.', 18),
            ('Перфоратор SDS+ 700W', 112.00, 'За пробиване на бетон, тухла и камък.', 12),
            ('Бормашина ударна 650W', 47.80, 'С регулиране на оборотите и реверс.', 22),
        ],
        'Крепежни елементи': [
            ('Дюбели 8х40мм (100бр)', 4.30, 'Найлонови дюбели за бетон и тухла.', 200),
            ('Винтове за дърво 4х40 (100бр)', 5.10, 'Поцинковани, кръстат шлиц.', 150),
            ('Болтове М8х60 (20бр)', 6.75, 'Поцинковани болтове с гайки и шайби.', 90),
            ('Пирони строителни 3.5х80 (1кг)', 3.90, 'Поцинковани, за дървени конструкции.', 100),
        ],
        'Боя и лакове': [
            ('Латекс бяла боя 10л', 34.90, 'Миеща се латексова боя за стени и тавани.', 30),
            ('Грунд универсален 5л', 22.40, 'Подходящ за бетон, мазилка и гипскартон.', 28),
            ('Спрей боя черна 400мл', 5.60, 'Гланцова, за метал и дърво.', 55),
            ('Лак за дърво безцветен 2.5л', 18.90, 'Устойчив на влага и надраскване.', 24),
        ],
        'Ключалки и брави': [
            ('Секретна брава цилиндрична', 24.50, 'Комплект с 3 ключа, за входни врати.', 15),
            ('Катинар стоманен 50мм', 9.80, 'С 3 ключа, устойчив на прерязване.', 40),
            ('Дръжка за врата с шпионка', 16.20, 'Хромирано покритие, комплект за монтаж.', 20),
        ],
        'Градина': [
            ('Градински маркуч 25м', 29.90, 'Гъвкав, устойчив на UV лъчи, с конектори.', 18),
            ('Лопата права с дръжка', 13.40, 'Стоманено острие, дървена дръжка.', 26),
            ('Градински ножици за клони', 17.60, 'С тефлоново покритие на острието.', 22),
        ],
    }

    for cat_name, products in categories_data.items():
        category = Category(name=cat_name)
        db.session.add(category)
        db.session.flush()
        for name, price, desc, stock in products:
            db.session.add(Product(
                name=name, price=price, description=desc,
                stock=stock, category_id=category.id
            ))
    db.session.commit()


@app.cli.command('init-db')
def init_db_command():
    """Инициализира базата и зарежда примерни продукти: flask init-db"""
    db.create_all()
    seed_data()
    print('Базата данни е готова с примерни продукти.')


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        seed_data()
    app.run(debug=True)
