import os
import secrets
import uuid
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename

from models import db, Category, Product, Order, OrderItem

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))  # чете стойностите от .env файла (ако съществува)

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'img', 'products')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
MAX_IMAGE_SIZE_MB = 5

app = Flask(__name__,template_folder='templates', static_folder='static')

# SECRET_KEY — ако липсва в .env, генерира се случаен (но тогава сесиите/количките
# се нулират при всеки рестарт на сървъра, затова е препоръчително да се зададе фиксиран в .env).
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or secrets.token_hex(32)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'store.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = MAX_IMAGE_SIZE_MB * 1024 * 1024

# Парола за админ панела — вземи се от .env, с fallback за удобство при първо пускане.
app.config['ADMIN_PASSWORD'] = os.environ.get('ADMIN_PASSWORD', 'admin123')

# WhatsApp номер за бутона за жив чат (код на държава + номер, без + и интервали).
app.config['WHATSAPP_NUMBER'] = os.environ.get('WHATSAPP_NUMBER', '')

# Версия на статичните файлове (CSS) — сменя се при всяка визуална промяна,
# за да не показва браузърът стар кеширан style.css след ъпдейт.
app.config['ASSET_VERSION'] = '8'

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

db.init_app(app)


@app.context_processor
def inject_whatsapp_number():
    return {'whatsapp_number': app.config['WHATSAPP_NUMBER'], 'asset_version': app.config['ASSET_VERSION']}


# Emoji fallback за категории без качена снимка (виж admin/categories.html за upload)
CATEGORY_ICONS = {
    'Ръчни инструменти': '🔨',
    'Електроинструменти': '⚡',
    'Крепежни елементи': '🔩',
    'Боя и лакове': '🖌️',
    'Ключалки и брави': '🔒',
    'Градина': '🌱',
    'Мазилки и лепила': '🧱',
}


@app.context_processor
def inject_category_icons():
    return {'category_icons': CATEGORY_ICONS}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def save_uploaded_image(file_storage):
    """Записва качен файл в static/img/products и връща относителен URL за него.
    Връща None ако няма подаден файл."""
    if not file_storage or file_storage.filename == '':
        return None
    if not allowed_file(file_storage.filename):
        raise ValueError('Позволени са само изображения: PNG, JPG, JPEG, WEBP, GIF.')

    ext = file_storage.filename.rsplit('.', 1)[1].lower()
    unique_name = f'{uuid.uuid4().hex}.{ext}'
    safe_name = secure_filename(unique_name)
    filepath = os.path.join(UPLOAD_FOLDER, safe_name)
    file_storage.save(filepath)
    return url_for('static', filename=f'img/products/{safe_name}')


def delete_uploaded_image(image_url):
    """Изтрива локален качен файл, ако image_url сочи към static/img/products/."""
    if image_url and image_url.startswith('/static/img/products/'):
        filename = image_url.rsplit('/', 1)[-1]
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        if os.path.isfile(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass


def admin_required(view_func):
    """Декоратор, който пази /admin рутовете зад логин."""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get('is_admin'):
            flash('Моля, влезте в системата.', 'error')
            return redirect(url_for('admin_login', next=request.path))
        return view_func(*args, **kwargs)
    return wrapper


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


@app.route('/contacts')
def contacts():
    categories = Category.query.all()
    return render_template('contacts.html', categories=categories)


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


# ---------------------------- Админ: логин / изход ----------------------------

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == app.config['ADMIN_PASSWORD']:
            session['is_admin'] = True
            flash('Влязохте успешно.', 'success')
            next_url = request.form.get('next') or url_for('admin_dashboard')
            return redirect(next_url)
        flash('Грешна парола.', 'error')
    next_url = request.args.get('next', '')
    return render_template('admin/login.html', next_url=next_url)


@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    flash('Излязохте от админ панела.', 'success')
    return redirect(url_for('admin_login'))


# ---------------------------- Админ: табло ----------------------------

@app.route('/admin')
@admin_required
def admin_dashboard():
    products = Product.query.order_by(Product.id.desc()).all()
    low_stock_count = Product.query.filter(Product.stock <= 5).count()
    orders_count = Order.query.count()
    new_orders_count = Order.query.filter_by(status='нова').count()
    return render_template('admin/dashboard.html', products=products,
                            low_stock_count=low_stock_count, orders_count=orders_count,
                            new_orders_count=new_orders_count)


# ---------------------------- Админ: продукти (CRUD) ----------------------------

@app.route('/admin/products/new', methods=['GET', 'POST'])
@admin_required
def admin_product_new():
    categories = Category.query.all()

    if not categories:
        flash('Първо създайте поне една категория.', 'error')
        return redirect(url_for('admin_categories'))

    if request.method == 'POST':
        error = None
        name = request.form.get('name', '').strip()
        price_raw = request.form.get('price', '').strip()
        stock_raw = request.form.get('stock', '0').strip()
        category_id = request.form.get('category_id')
        description = request.form.get('description', '').strip()
        uploaded_file = request.files.get('image_file')

        if not name:
            error = 'Името на продукта е задължително.'
        try:
            price = float(price_raw)
            if price < 0:
                error = 'Цената не може да е отрицателна.'
        except ValueError:
            error = 'Невалидна цена.'
        try:
            stock = int(stock_raw)
        except ValueError:
            error = 'Невалидна наличност.'

        image_url = ''
        if not error:
            try:
                saved = save_uploaded_image(uploaded_file)
                if saved:
                    image_url = saved
            except ValueError as e:
                error = str(e)

        if error:
            flash(error, 'error')
            return render_template('admin/product_form.html', categories=categories,
                                    product=None, form=request.form)

        product = Product(name=name, price=price, stock=stock,
                           category_id=category_id, description=description,
                           image_url=image_url)
        db.session.add(product)
        db.session.commit()
        flash(f'Продукт "{name}" беше създаден.', 'success')
        return redirect(url_for('admin_dashboard'))

    return render_template('admin/product_form.html', categories=categories, product=None, form=None)


@app.route('/admin/products/<int:product_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_product_edit(product_id):
    product = Product.query.get_or_404(product_id)
    categories = Category.query.all()

    if request.method == 'POST':
        error = None
        name = request.form.get('name', '').strip()
        price_raw = request.form.get('price', '').strip()
        stock_raw = request.form.get('stock', '0').strip()
        uploaded_file = request.files.get('image_file')
        remove_image = request.form.get('remove_image') == '1'

        if not name:
            error = 'Името на продукта е задължително.'
        try:
            price = float(price_raw)
            if price < 0:
                error = 'Цената не може да е отрицателна.'
        except ValueError:
            error = 'Невалидна цена.'
        try:
            stock = int(stock_raw)
        except ValueError:
            error = 'Невалидна наличност.'

        new_image_url = None
        if not error:
            try:
                new_image_url = save_uploaded_image(uploaded_file)
            except ValueError as e:
                error = str(e)

        if error:
            flash(error, 'error')
            return render_template('admin/product_form.html', categories=categories,
                                    product=product, form=request.form)

        product.name = name
        product.price = price
        product.stock = stock
        product.category_id = request.form.get('category_id')
        product.description = request.form.get('description', '').strip()

        if new_image_url:
            delete_uploaded_image(product.image_url)
            product.image_url = new_image_url
        elif remove_image:
            delete_uploaded_image(product.image_url)
            product.image_url = ''

        db.session.commit()
        flash(f'Продукт "{name}" беше обновен.', 'success')
        return redirect(url_for('admin_dashboard'))

    return render_template('admin/product_form.html', categories=categories, product=product, form=None)


@app.route('/admin/products/<int:product_id>/delete', methods=['POST'])
@admin_required
def admin_product_delete(product_id):
    product = Product.query.get_or_404(product_id)
    name = product.name
    delete_uploaded_image(product.image_url)
    db.session.delete(product)
    db.session.commit()
    flash(f'Продукт "{name}" беше изтрит.', 'success')
    return redirect(url_for('admin_dashboard'))


# ---------------------------- Админ: категории ----------------------------

@app.route('/admin/categories', methods=['GET', 'POST'])
@admin_required
def admin_categories():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        uploaded_file = request.files.get('image_file')

        if not name:
            flash('Името на категорията е задължително.', 'error')
        elif Category.query.filter_by(name=name).first():
            flash('Вече съществува категория с това име.', 'error')
        else:
            image_url = ''
            try:
                saved = save_uploaded_image(uploaded_file)
                if saved:
                    image_url = saved
            except ValueError as e:
                flash(str(e), 'error')
                return redirect(url_for('admin_categories'))

            db.session.add(Category(name=name, image_url=image_url))
            db.session.commit()
            flash(f'Категория "{name}" беше създадена.', 'success')
        return redirect(url_for('admin_categories'))

    categories = Category.query.all()
    return render_template('admin/categories.html', categories=categories)


@app.route('/admin/categories/<int:category_id>/image', methods=['POST'])
@admin_required
def admin_category_image(category_id):
    category = Category.query.get_or_404(category_id)
    uploaded_file = request.files.get('image_file')
    remove_image = request.form.get('remove_image') == '1'

    if remove_image:
        delete_uploaded_image(category.image_url)
        category.image_url = ''
        db.session.commit()
        flash(f'Снимката на "{category.name}" беше премахната.', 'success')
        return redirect(url_for('admin_categories'))

    try:
        saved = save_uploaded_image(uploaded_file)
    except ValueError as e:
        flash(str(e), 'error')
        return redirect(url_for('admin_categories'))

    if saved:
        delete_uploaded_image(category.image_url)
        category.image_url = saved
        db.session.commit()
        flash(f'Снимката на "{category.name}" беше обновена.', 'success')
    else:
        flash('Не беше избран файл.', 'error')

    return redirect(url_for('admin_categories'))


@app.route('/admin/categories/<int:category_id>/delete', methods=['POST'])
@admin_required
def admin_category_delete(category_id):
    category = Category.query.get_or_404(category_id)
    if category.products:
        flash(f'Не може да изтриете "{category.name}" — все още съдържа продукти.', 'error')
    else:
        delete_uploaded_image(category.image_url)
        db.session.delete(category)
        db.session.commit()
        flash(f'Категория "{category.name}" беше изтрита.', 'success')
    return redirect(url_for('admin_categories'))


# ---------------------------- Админ: поръчки ----------------------------

@app.route('/admin/orders')
@admin_required
def admin_orders():
    status_filter = request.args.get('status', '').strip()
    query = Order.query
    if status_filter:
        query = query.filter_by(status=status_filter)
    orders = query.order_by(Order.created_at.desc()).all()
    return render_template('admin/orders.html', orders=orders,
                            statuses=Order.STATUSES, status_filter=status_filter)


@app.route('/admin/orders/<int:order_id>')
@admin_required
def admin_order_detail(order_id):
    order = Order.query.get_or_404(order_id)
    return render_template('admin/order_detail.html', order=order, statuses=Order.STATUSES)


@app.route('/admin/orders/<int:order_id>/status', methods=['POST'])
@admin_required
def admin_order_status(order_id):
    order = Order.query.get_or_404(order_id)
    new_status = request.form.get('status', '').strip()
    if new_status in Order.STATUSES:
        order.status = new_status
        db.session.commit()
        flash(f'Статусът на поръчка #{order.id} е обновен на "{new_status}".', 'success')
    else:
        flash('Невалиден статус.', 'error')
    return redirect(request.referrer or url_for('admin_orders'))


# ---------------------------- Инициализация на БД + примерни данни ----------------------------

def seed_data():
    # Цените са в евро (EUR), конвертирани по фиксирания официален курс
    # на присъединяване на България към еврозоната: 1 EUR = 1.95583 лв.
    categories_data = {
        'Ръчни инструменти': [
            ('Чук дърводелски 500г', 7.62, 'Стоманена глава, дървена дръжка, за общи ремонтни дейности.', 40),
            ('Комплект отвертки 6бр', 9.97, 'Комбиниран комплект права и кръстата глава, ергономична дръжка.', 60),
            ('Клещи комбинирани 180мм', 5.73, 'Хромирани, за рязане и захващане на тел и кабел.', 35),
            ('Ножовка за метал', 4.29, 'С резервно острие, за прецизно рязане.', 25),
            ('Метър рулетка 5м', 3.53, 'Стоманена лента с автоматично прибиране и спирачка.', 80),
        ],
        'Електроинструменти': [
            ('Акумулаторен винтоверт 18V', 45.50, 'С 2 батерии и куфар, до 45Nm въртящ момент.', 20),
            ('Ъглошлайф 125мм 900W', 32.98, 'За рязане и шлайфане на метал и камък.', 18),
            ('Перфоратор SDS+ 700W', 57.26, 'За пробиване на бетон, тухла и камък.', 12),
            ('Бормашина ударна 650W', 24.44, 'С регулиране на оборотите и реверс.', 22),
        ],
        'Крепежни елементи': [
            ('Дюбели 8х40мм (100бр)', 2.20, 'Найлонови дюбели за бетон и тухла.', 200),
            ('Винтове за дърво 4х40 (100бр)', 2.61, 'Поцинковани, кръстат шлиц.', 150),
            ('Болтове М8х60 (20бр)', 3.45, 'Поцинковани болтове с гайки и шайби.', 90),
            ('Пирони строителни 3.5х80 (1кг)', 1.99, 'Поцинковани, за дървени конструкции.', 100),
        ],
        'Боя и лакове': [
            ('Латекс бяла боя 10л', 17.84, 'Миеща се латексова боя за стени и тавани.', 30),
            ('Грунд универсален 5л', 11.45, 'Подходящ за бетон, мазилка и гипскартон.', 28),
            ('Спрей боя черна 400мл', 2.86, 'Гланцова, за метал и дърво.', 55),
            ('Лак за дърво безцветен 2.5л', 9.66, 'Устойчив на влага и надраскване.', 24),
        ],
        'Ключалки и брави': [
            ('Секретна брава цилиндрична', 12.53, 'Комплект с 3 ключа, за входни врати.', 15),
            ('Катинар стоманен 50мм', 5.01, 'С 3 ключа, устойчив на прерязване.', 40),
            ('Дръжка за врата с шпионка', 8.28, 'Хромирано покритие, комплект за монтаж.', 20),
        ],
        'Градина': [
            ('Градински маркуч 25м', 15.29, 'Гъвкав, устойчив на UV лъчи, с конектори.', 18),
            ('Лопата права с дръжка', 6.85, 'Стоманено острие, дървена дръжка.', 26),
            ('Градински ножици за клони', 9.00, 'С тефлоново покритие на острието.', 22),
        ],
        'Мазилки и лепила': [
            ('Циментова замазка 25кг', 7.50, 'За изравняване на подове преди полагане на настилка.', 45),
            ('Гипсова мазилка машинна 30кг', 8.90, 'За вътрешно измазване на стени и тавани.', 38),
            ('Декоративна мазилка "Камешек" 25кг', 14.50, 'Фасадна мазилка с фина зърнеста текстура.', 20),
            ('Лепило за фаянс и плочки 25кг', 9.20, 'Еластично, за вътрешно и външно приложение.', 50),
            ('Фугираща смес 5кг', 6.80, 'Водоустойчива, за фуги до 6мм.', 40),
            ('Шпакловка финна 20кг', 11.90, 'За заглаждане преди боядисване, готова за шлайфане.', 30),
            ('Лепило монтажно "Течни пирони" 310мл', 3.40, 'Силно фиксиращо, за дърво, метал, керамика и бетон.', 70),
            ('Грунд контактен за бетон 10л', 13.20, 'Подобрява сцеплението преди мазилка или замазка.', 22),
        ],
    }

    # Зареждаме примерните категории/продукти само при напълно празна база данни
    # (първо стартиране). Ако вече има поне една категория, приемаме че админът
    # управлява каталога ръчно — иначе изтрита категория щеше да "възкръсва"
    # при всеки рестарт на сървъра.
    if Category.query.first():
        return

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


def migrate_db():
    """Лека автоматична миграция — добавя колони, добавени след първото пускане
    на проекта, ако вече съществува по-стара база данни (store.db)."""
    from sqlalchemy import text
    with db.engine.connect() as conn:
        order_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(orders)"))]
        if 'status' not in order_cols:
            conn.execute(text("ALTER TABLE orders ADD COLUMN status VARCHAR(30) DEFAULT 'нова'"))
            conn.commit()

        category_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(categories)"))]
        if 'image_url' not in category_cols:
            conn.execute(text("ALTER TABLE categories ADD COLUMN image_url VARCHAR(300) DEFAULT ''"))
            conn.commit()


@app.cli.command('init-db')
def init_db_command():
    """Инициализира базата и зарежда примерни продукти: flask init-db"""
    db.create_all()
    migrate_db()
    seed_data()
    print('Базата данни е готова с примерни продукти.')


@app.route("/favicon.ico")
def favicon():
    return (url_for('static', filename='images/favicon/favicon.ico'),
            url_for('static', filename='images/favicon/favicon-16x16.png'),
            url_for('static', filename='images/favicon/favicon-32x32.png'),
            url_for('static', filename='images/favicon/android-chrome-192x192.png'),
            url_for('static', filename='images/favicon/android-chrome-256x256.png'),
            url_for('static', filename='images/favicon/apple-touch-icon.png'),
            url_for('static', filename='images/favicon/safari-pinned-tab.svg'),
            url_for('static', filename='images/favicon/mstile-150x150.png'),
            url_for('static', filename='images/favicon/browserconfig.xml'),
            url_for('static', filename='images/favicon/site.webmanifest'))


# The code below lets the Flask server respond to crawler request for robots.txt and sitemap files



@app.route('/robots.txt')
@app.route('/sitemap.xml')
def static_from_root():
    return send_from_directory(app.static_folder, request.path[1:])





    app.run(debug=True)
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        migrate_db()
        seed_data()
    app.run(host="0.0.0.0", port=8000, debug=True)
