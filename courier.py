"""Admin-only shipment preparation; no live calls happen at import or page load."""
import json
import secrets
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Response, abort, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError
from models import db, Order, CourierShipment
from econt import EcontClient, EcontError, safe_pdf_url, fetch_pdf


def required(form, key, title, limit=150):
    value = form.get(key, '').strip()
    if not value or len(value) > limit:
        raise ValueError(f'Попълнете правилно: {title}.')
    return value


def build_label(order, form):
    try:
        weight = Decimal(form.get('weight', ''))
        packs = int(form.get('pack_count', ''))
        if not weight.is_finite() or not Decimal('0') < weight <= 50 or not 1 <= packs <= 100:
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise ValueError('Въведете тегло над 0 до 50 kg и брой пакети от 1 до 100.') from None
    label = {
        'senderClient': {'name': required(form, 'sender_name', 'име на подателя'),
                         'phones': [required(form, 'sender_phone', 'телефон на подателя', 50)]},
        'senderOfficeCode': required(form, 'sender_office', 'офис на изпращане', 20),
        'receiverClient': {'name': required(form, 'receiver_name', 'име на получателя'),
                           'phones': [required(form, 'receiver_phone', 'телефон на получателя', 50)]},
        'packCount': packs, 'weight': float(weight), 'shipmentType': 'pack',
        'orderNumber': str(order.id),
        'shipmentDescription': f'Поръчка #{order.id}: ' + '; '.join(
            f'{item.product_name} x{item.qty}' for item in order.items)[:180],
        'paymentSenderMethod': 'cash',
    }
    delivery = form.get('delivery_type')
    if delivery == 'office':
        label['receiverOfficeCode'] = required(form, 'receiver_office', 'офис на получаване', 20)
    elif delivery == 'address':
        label['receiverAddress'] = {
            'city': {'country': {'code3': 'BGR'},
                     'name': required(form, 'city', 'населено място', 100),
                     'postCode': required(form, 'post_code', 'пощенски код', 10)},
            'fullAddress': required(form, 'address', 'адрес', 300),
        }
    else:
        raise ValueError('Изберете доставка до офис или адрес.')
    payer = form.get('payer')
    if payer not in ('sender', 'receiver'):
        raise ValueError('Изберете кой плаща доставката.')
    if payer == 'receiver':
        label.update(paymentReceiverMethod='cash', paymentReceiverAmount=100,
                     paymentReceiverAmountIsPercent=True)
    if form.get('cod') == '1':
        amount = Decimal(str(order.total)).quantize(Decimal('.01'))
        if not amount.is_finite() or amount <= 0:
            raise ValueError('Невалидна сума за наложен платеж.')
        label['services'] = {'cdAmount': float(amount), 'cdType': 'get', 'cdCurrency': 'EUR'}
    return label


def save_status(shipment, status):
    shipment.shipment_number = str(status['shipmentNumber'])
    shipment.pdf_url = safe_pdf_url(status.get('pdfURL'), shipment.environment)
    shipment.delivery_status = str(status.get('shortDeliveryStatus') or 'Подготвена в Еконт')[:200]
    shipment.state = 'created'
    shipment.error_message = ''
    db.session.commit()


def register_courier(app, admin_required):
    @app.context_processor
    def courier_context():
        return {'courier_enabled': app.config.get('COURIER_ENABLED', False)}

    def protected_post(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            expected = session.get('courier_csrf', '')
            actual = request.form.get('csrf_token', '')
            if not expected or not secrets.compare_digest(expected, actual):
                abort(400, 'Невалидна или изтекла форма. Презаредете страницата.')
            return view(*args, **kwargs)
        return wrapped

    def render_order(order, form=None, quote=None, error=None, code=200):
        environment = app.config['COURIER_ENVIRONMENT']
        shipment = CourierShipment.query.filter_by(order_id=order.id, environment=environment).first()
        session.setdefault('courier_csrf', secrets.token_urlsafe(32))
        if form is None:
            form = {'sender_name': app.config.get('ECONT_SENDER_NAME', ''),
                    'sender_phone': app.config.get('ECONT_SENDER_PHONE', ''),
                    'sender_office': app.config.get('ECONT_SENDER_OFFICE_CODE', ''),
                    'receiver_name': order.customer_name, 'receiver_phone': order.phone,
                    'address': order.address, 'delivery_type': 'office',
                    'pack_count': '1', 'payer': 'receiver', 'cod': '1'}
        return render_template('admin/courier.html', order=order, shipment=shipment,
                               form=form, quote=quote, error=error, environment=environment), code

    @app.route('/admin/orders/<int:order_id>/econt', methods=['GET'])
    @admin_required
    def courier_order(order_id):
        return render_order(Order.query.get_or_404(order_id))

    @app.route('/admin/econt/offices')
    @admin_required
    def courier_offices():
        try:
            result = EcontClient(app.config).call('Nomenclatures/NomenclaturesService.getOffices',
                                                   {'countryCode': 'BGR'})
            offices = [{'code': str(o['code']), 'name': o.get('name', ''),
                        'city': (o.get('address') or {}).get('city', {}).get('name', '')}
                       for o in result.get('offices', []) if o.get('code') and not o.get('isAPS')]
            return jsonify(offices=offices)
        except EcontError as exc:
            return jsonify(error=str(exc)), 502

    @app.route('/admin/orders/<int:order_id>/econt', methods=['POST'])
    @admin_required
    @protected_post
    def courier_submit(order_id):
        order = Order.query.get_or_404(order_id)
        form = request.form.to_dict()
        action = form.get('action')
        if action not in ('calculate', 'create'):
            abort(400)
        try:
            client = EcontClient(app.config)
            if order.status in ('отказана', 'завършена'):
                raise ValueError('Не се създават товарителници за отказани или завършени поръчки.')
            label = build_label(order, form)
            if action == 'calculate':
                quote = client.label(label, 'calculate')
                return render_order(order, form, quote=quote)
        except (ValueError, EcontError) as exc:
            return render_order(order, form, error=str(exc), code=400)

        # Commit a reservation BEFORE contacting Econt, across all WSGI workers.
        environment = client.environment
        shipment = CourierShipment.query.filter_by(order_id=order.id, environment=environment).first()
        if shipment:
            claimed = CourierShipment.query.filter_by(id=shipment.id, state='failed').update(
                {'state': 'pending', 'error_message': '',
                 'request_json': json.dumps(label, ensure_ascii=False)})
            db.session.commit()
            if not claimed:
                return render_order(order, form, error='Вече има товарителница или непотвърдена заявка. Проверете състоянието.', code=409)
            db.session.refresh(shipment)
        else:
            shipment = CourierShipment(order_id=order.id, environment=environment, state='pending',
                                       request_json=json.dumps(label, ensure_ascii=False))
            db.session.add(shipment)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                return render_order(order, form, error='Друга заявка вече създава товарителницата.', code=409)
        try:
            status = client.label(label, 'create')
            if not status.get('shipmentNumber'):
                raise EcontError('Липсва номер от Еконт. Проверете в e-Econt преди нов опит.', uncertain=True)
            save_status(shipment, status)
        except EcontError as exc:
            shipment.state = 'uncertain' if exc.uncertain else 'failed'
            shipment.error_message = str(exc)
            db.session.commit()
            return render_order(order, form, error=str(exc), code=502)
        flash('Товарителницата е създадена.' + (' Това е тестова пратка.' if environment == 'test' else ''), 'success')
        return redirect(url_for('courier_order', order_id=order.id))

    @app.route('/admin/orders/<int:order_id>/econt/refresh', methods=['POST'])
    @admin_required
    @protected_post
    def courier_refresh(order_id):
        order = Order.query.get_or_404(order_id)
        try:
            client = EcontClient(app.config)
            shipment = CourierShipment.query.filter_by(order_id=order.id, environment=client.environment).first_or_404()
            number = shipment.shipment_number
            if not number:
                if shipment.state not in ('pending', 'uncertain'):
                    raise ValueError('Няма товарителница за възстановяване.')
                number = required(request.form, 'shipment_number', 'номер от e-Econt', 64)
            status = client.status(number)
            if not shipment.shipment_number:
                # Reconcile a timed-out create only if this is the original order/recipient.
                original = json.loads(shipment.request_json)
                receiver = status.get('receiverClient') or {}
                if (status.get('shipmentDescription') != original['shipmentDescription']
                        or receiver.get('name') != original['receiverClient']['name']
                        or receiver.get('phones') != original['receiverClient']['phones']):
                    raise ValueError('Товарителницата не съвпада с тази поръчка и получател.')
            save_status(shipment, status)
            flash('Статусът и PDF са обновени.', 'success')
        except (ValueError, EcontError) as exc:
            flash(str(exc), 'error')
        return redirect(url_for('courier_order', order_id=order.id))

    @app.route('/admin/orders/<int:order_id>/econt/pdf')
    @admin_required
    def courier_pdf(order_id):
        shipment = CourierShipment.query.filter_by(order_id=order_id,
            environment=app.config['COURIER_ENVIRONMENT'], state='created').first_or_404()
        url = safe_pdf_url(shipment.pdf_url, shipment.environment)
        if not url:
            flash('Еконт още не е предоставил PDF. Натиснете „Обнови статус / PDF“.', 'error')
            return redirect(url_for('courier_order', order_id=order_id))
        response = redirect(url)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response


    @app.route('/admin/orders/<int:order_id>/econt/print')
    @admin_required
    def courier_print(order_id):
        shipment = CourierShipment.query.filter_by(order_id=order_id,
            environment=app.config['COURIER_ENVIRONMENT'], state='created').first_or_404()
        response = Response(render_template('admin/courier_print.html', shipment=shipment))
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.route('/admin/orders/<int:order_id>/econt/print.pdf')
    @admin_required
    def courier_print_pdf(order_id):
        shipment = CourierShipment.query.filter_by(order_id=order_id,
            environment=app.config['COURIER_ENVIRONMENT'], state='created').first_or_404()
        try:
            content = fetch_pdf(shipment.pdf_url, shipment.environment)
        except EcontError as exc:
            return Response(str(exc), status=502, content_type='text/plain; charset=utf-8',
                            headers={'Cache-Control': 'no-store'})
        return Response(content, content_type='application/pdf', headers={
            'Content-Disposition': f'inline; filename="econt-order-{order_id}.pdf"',
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'SAMEORIGIN'})
