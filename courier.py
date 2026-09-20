"""Admin-only shipment preparation; no live calls happen at import or page load."""
import json
import secrets
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for, send_file
from sqlalchemy.exc import IntegrityError
from models import db, Order, CourierShipment, CourierAction
from econt import EcontClient, EcontError, safe_pdf_url
from security import safe_equal


# Actions in these states may be submitted again (definite failure, or pickup rejected by Econt).
RETRYABLE_ACTION_STATES = ('failed', 'rejected')
# Shipment states that wait for the result of an external cancel/pickup call.
UNRESOLVED_STATES = ('cancel_pending', 'cancel_unknown', 'pickup_pending', 'pickup_unknown')
# A *_pending call is in flight for at most the 25 s HTTP timeout; only later is it "stuck".
PENDING_GRACE = timedelta(minutes=2)


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
        'receiverClient': {'name': required(form, 'receiver_name', 'име на получателя'),
                           'phones': [required(form, 'receiver_phone', 'телефон на получателя', 50)]},
        'packCount': packs, 'weight': float(weight), 'shipmentType': 'pack',
        'orderNumber': str(order.id),
        'shipmentDescription': f'Поръчка #{order.id}: ' + '; '.join(
            f'{item.product_name} x{item.qty}' for item in order.items)[:180],
        'paymentSenderMethod': 'cash',
    }
    sender_type = form.get('sender_type', 'office')
    if sender_type == 'office':
        label['senderOfficeCode'] = required(form, 'sender_office', 'офис на изпращане', 20)
    elif sender_type == 'address':
        label['senderAddress'] = {
            'city': {'country': {'code3': 'BGR'},
                     'name': required(form, 'sender_city', 'населено място на подателя', 100),
                     'postCode': required(form, 'sender_post_code', 'пощенски код на подателя', 10)},
            'fullAddress': required(form, 'sender_address', 'адрес на подателя', 300),
        }
    else:
        raise ValueError('Изберете изпращане от офис или адрес.')
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
        return {'courier_enabled': app.config.get('COURIER_ENABLED', False),
                'shop_sender_address': {'city': app.config.get('ECONT_SENDER_CITY', ''),
                                        'post_code': app.config.get('ECONT_SENDER_POST_CODE', ''),
                                        'address': app.config.get('ECONT_SENDER_ADDRESS', '')}}

    def protected_post(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            expected = session.get('courier_csrf', '')
            actual = request.form.get('csrf_token', '')
            if not expected or not safe_equal(expected, actual):
                abort(400, 'Невалидна или изтекла форма. Презаредете страницата.')
            return view(*args, **kwargs)
        return wrapped

    def render_order(order, form=None, quote=None, error=None, code=200):
        environment = app.config['COURIER_ENVIRONMENT']
        shipment = CourierShipment.query.filter_by(order_id=order.id, environment=environment).first()
        session.setdefault('courier_csrf', secrets.token_urlsafe(32))
        if form is None:
            # Адресът на подател по подразбиране е адресът на магазина (от .env). Името и телефонът
            # идват от профила в Еконт (те трябва да съвпадат с акаунта), а офисът е по избор.
            form = {'sender_type': 'address', 'sender_name': '',
                    'sender_phone': '',
                    'sender_city': app.config.get('ECONT_SENDER_CITY', ''),
                    'sender_post_code': app.config.get('ECONT_SENDER_POST_CODE', ''),
                    'sender_address': app.config.get('ECONT_SENDER_ADDRESS', ''),
                    'sender_office': app.config.get('ECONT_SENDER_OFFICE_CODE', ''),
                    'receiver_name': order.customer_name, 'receiver_phone': order.phone,
                    'address': order.address, 'delivery_type': 'office',
                    'pack_count': '1', 'payer': 'receiver', 'cod': '1'}
        return render_template('admin/courier.html', order=order, shipment=shipment,
                               form=form, quote=quote, error=error, environment=environment,
                               actions=CourierAction.query.filter_by(shipment_id=shipment.id).all() if shipment else []), code

    @app.route('/admin/orders/<int:order_id>/econt', methods=['GET'])
    @admin_required
    def courier_order(order_id):
        return render_order(Order.query.get_or_404(order_id))

    @app.route('/admin/econt/profiles')
    @admin_required
    def courier_profiles():
        try:
            response = jsonify(profiles=EcontClient(app.config).profiles())
        except EcontError as exc:
            response = jsonify(error=str(exc))
            response.status_code = 502
        except Exception:
            current_app.logger.exception('Unexpected Econt profiles error')
            response = jsonify(error='Грешка при зареждане на профилите от Еконт.')
            response.status_code = 502
        response.headers['Cache-Control'] = 'no-store'
        return response

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
            current_app.logger.exception('Econt offices request failed')
            return jsonify(error=str(exc)), 502
        except Exception:
            current_app.logger.exception('Unexpected Econt offices error')
            return jsonify(error='Грешка при връзка с Еконт.'), 502

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
            current_app.logger.exception('Econt shipment creation failed')
            shipment.state = 'uncertain' if exc.uncertain else 'failed'
            shipment.error_message = str(exc)
            db.session.commit()
            return render_order(order, form, error=str(exc), code=502)
        except Exception as exc:
            current_app.logger.exception('Unexpected Econt shipment creation error')
            shipment.state = 'failed'
            shipment.error_message = str(exc)[:500]
            db.session.commit()
            return render_order(
                order, form,
                error='Грешка при връзка с Еконт. Проверете логовете.',
                code=502,
            )
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
            if shipment.state not in ('created', 'pending', 'uncertain'):
                raise ValueError('Първо изяснете резултата от действието към Еконт.')
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
            current_app.logger.exception('Econt shipment status request failed')
            flash(str(exc), 'error')
        except Exception:
            current_app.logger.exception('Unexpected Econt shipment status error')
            flash('Грешка при връзка с Еконт.', 'error')
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


    @app.route('/admin/orders/<int:order_id>/econt/export/<fmt>')
    @admin_required
    def courier_export(order_id, fmt):
        if fmt not in ('docx', 'xlsx', 'csv'):
            abort(404)
        shipment = CourierShipment.query.filter_by(
            order_id=order_id, environment=app.config['COURIER_ENVIRONMENT'],
            state='created').first_or_404()
        from courier_exports import export_shipment
        output, mime = export_shipment(shipment, fmt)
        response = send_file(output, mimetype=mime, as_attachment=True,
                             download_name=f'econt-order-{order_id}.{fmt}', max_age=0)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.route('/admin/orders/<int:order_id>/econt/action/<kind>', methods=['GET', 'POST'])
    @admin_required
    def courier_action(order_id, kind):
        if kind not in ('cancel', 'pickup'):
            abort(404)
        order = Order.query.get_or_404(order_id)
        environment = app.config['COURIER_ENVIRONMENT']
        shipment = CourierShipment.query.filter_by(order_id=order_id, environment=environment).first_or_404()
        session.setdefault('courier_csrf', secrets.token_urlsafe(32))
        original = json.loads(shipment.request_json)
        action = CourierAction.query.filter_by(shipment_id=shipment.id, kind=kind).first()
        form = request.form.to_dict() if request.method == 'POST' else {}
        if request.method == 'GET' and kind == 'pickup':
            sender_address = original.get('senderAddress') or {}
            sender_city = sender_address.get('city') or {}
            form = {'city': sender_city.get('name') or '',
                    'post_code': sender_city.get('postCode') or '',
                    'address': sender_address.get('fullAddress') or ''}
        def page(error=None, code=200):
            return render_template('admin/courier_action.html', order=order, shipment=shipment,
                                   kind=kind, action=action, form=form, original=original,
                                   environment=environment, error=error), code
        if request.method == 'GET':
            return page()
        if not safe_equal(session['courier_csrf'], form.get('csrf_token', '')):
            abort(400, 'Невалидна или изтекла форма. Презаредете страницата.')
        if form.get('confirm') != str(shipment.id):
            return page('Потвърдете действието за тази товарителница.', 400)
        if shipment.state != 'created' or (action and action.state not in RETRYABLE_ACTION_STATES):
            return page('Действието вече е изпълнено или очаква проверка. Не е изпратена нова заявка.', 409)
        try:
            client = EcontClient(app.config)
            payload = {'shipmentNumbers': [shipment.shipment_number]}
            if kind == 'pickup':
                from zoneinfo import ZoneInfo
                if order.status in ('отказана', 'завършена', 'изпратена'):
                    raise ValueError('Не може да заявите вземане за изпратена, завършена или отказана поръчка.')
                tz = ZoneInfo('Europe/Sofia')
                start = datetime.fromisoformat(required(form, 'time_from', 'начало на интервала', 25)).replace(tzinfo=tz)
                end = datetime.fromisoformat(required(form, 'time_to', 'край на интервала', 25)).replace(tzinfo=tz)
                if start <= datetime.now(tz) or end <= start or start.date() != end.date():
                    raise ValueError('Изберете бъдещ интервал в рамките на един ден по българско време.')
                payload = {
                    'requestTimeFrom': int(start.timestamp() * 1000),
                    'requestTimeTo': int(end.timestamp() * 1000),
                    'shipmentType': original['shipmentType'],
                    'shipmentPackCount': original['packCount'],
                    'shipmentWeight': original['weight'],
                    'senderClient': original['senderClient'],
                    'senderAddress': {'city': {'country': {'code3': 'BGR'},
                                              'name': required(form, 'city', 'населено място', 100),
                                              'postCode': required(form, 'post_code', 'пощенски код', 10)},
                                      'fullAddress': required(form, 'address', 'адрес за вземане', 300)},
                    'attachShipments': [shipment.shipment_number],
                }
        except (ValueError, EcontError) as exc:
            return page(str(exc), 400)
        # This shared reservation serializes cancellation and pickup across workers.
        claimed = CourierShipment.query.filter_by(id=shipment.id, state='created').update(
            {'state': kind + '_pending'}, synchronize_session=False)
        if not claimed:
            db.session.rollback()
            return page('Друга заявка вече обработва тази пратка.', 409)
        if action is None:
            action = CourierAction(shipment_id=shipment.id, kind=kind)
            db.session.add(action)
        action.state = 'pending'
        action.request_id = ''
        action.request_json = json.dumps(payload, ensure_ascii=False)
        action.message = ''
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return page('Действието вече е заявено.', 409)
        try:
            if kind == 'cancel':
                client.cancel_label(shipment.shipment_number)
                shipment.state = 'cancelled'
                shipment.delivery_status = 'Анулирана в Еконт'
                shipment.pdf_url = ''
                action.message = 'Еконт потвърди анулирането на товарителницата.'
            else:
                action.request_id, warning = client.request_courier(payload)
                shipment.state = 'created'
                action.message = 'Заявката е приета от Еконт.' + (' ' + warning if warning else '')
            action.state = 'succeeded'
            shipment.error_message = ''
        except EcontError as exc:
            action.state = 'uncertain' if exc.uncertain else 'failed'
            shipment.state = kind + '_unknown' if exc.uncertain else 'created'
            action.message = str(exc)
            shipment.error_message = str(exc)
        except Exception:
            current_app.logger.exception('Unexpected courier action result')
            action.state = 'uncertain'
            shipment.state = kind + '_unknown'
            action.message = 'Резултатът не е потвърден. Проверете в Еконт преди ново действие.'
            shipment.error_message = action.message
        db.session.commit()
        flash(action.message, 'success' if action.state == 'succeeded' else 'error')
        return redirect(url_for('courier_order', order_id=order_id))

    @app.route('/admin/orders/<int:order_id>/econt/pickup-status', methods=['POST'])
    @admin_required
    @protected_post
    def courier_pickup_status(order_id):
        shipment = CourierShipment.query.filter_by(order_id=order_id, environment=app.config['COURIER_ENVIRONMENT']).first_or_404()
        action = CourierAction.query.filter_by(shipment_id=shipment.id, kind='pickup', state='succeeded').first_or_404()
        names = {'unprocess': 'Очаква обработка', 'process': 'Назначен е куриер',
                 'taken': 'Пратката е взета', 'reject': 'Заявката е отменена', 'reject_client': 'Отменена от клиента'}
        try:
            status = EcontClient(app.config).courier_request_status(action.request_id)
            action.message = names.get(status.get('status'), 'Непознат статус от Еконт')
            if status.get('status') in ('reject', 'reject_client'):
                # Econt will not send a courier; allow a new pickup request.
                action.state = 'rejected'
            for key in ('note', 'reject_reason'):
                if status.get(key):
                    action.message += ' · ' + str(status[key])[:500]
            db.session.commit()
            flash(action.message, 'success')
        except EcontError as exc:
            flash(str(exc), 'error')
        return redirect(url_for('courier_order', order_id=order_id))

    @app.route('/admin/orders/<int:order_id>/econt/resolve', methods=['POST'])
    @admin_required
    @protected_post
    def courier_resolve(order_id):
        """Let the admin record the real outcome of a cancel/pickup call whose result is unknown.

        Nothing is sent to Econt here: the admin checked e-Econt and tells the shop what happened.
        Without this a shipment stuck in *_pending / *_unknown could never be unlocked or deleted.
        """
        shipment = CourierShipment.query.filter_by(order_id=order_id,
                                                   environment=app.config['COURIER_ENVIRONMENT']).first_or_404()
        back = redirect(url_for('courier_order', order_id=order_id))
        if shipment.state not in UNRESOLVED_STATES:
            flash('Няма неизяснено действие към Еконт за тази пратка.', 'error')
            return back
        if request.form.get('confirm') != str(shipment.id):
            flash('Потвърдете, че сте проверили резултата в e-Econt.', 'error')
            return back
        outcome = request.form.get('outcome')
        if outcome not in ('done', 'not_done'):
            flash('Изберете какъв е резултатът в Еконт.', 'error')
            return back
        if shipment.state.endswith('_pending') and shipment.updated_at \
                and datetime.utcnow() - shipment.updated_at < PENDING_GRACE:
            flash('Заявката към Еконт може още да се обработва. Опитайте отново след няколко минути.', 'error')
            return back
        kind = shipment.state.split('_')[0]
        action = CourierAction.query.filter_by(shipment_id=shipment.id, kind=kind).first()
        if action is None:
            action = CourierAction(shipment_id=shipment.id, kind=kind, request_json='{}')
            db.session.add(action)
        if outcome == 'not_done':
            shipment.state = 'created'
            action.state = 'failed'
            action.message = 'Администратор потвърди, че действието не е изпълнено в Еконт. Може да се опита отново.'
        elif kind == 'cancel':
            shipment.state = 'cancelled'
            shipment.delivery_status = 'Анулирана в Еконт'
            shipment.pdf_url = ''
            action.state = 'succeeded'
            action.message = 'Администратор потвърди, че товарителницата е анулирана в Еконт.'
        else:
            try:
                request_id = required(request.form, 'request_id', 'номер на заявката за куриер', 64)
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), 'error')
                return back
            shipment.state = 'created'
            action.state = 'succeeded'
            action.request_id = request_id
            action.message = 'Администратор потвърди, че заявката за куриер е приета в Еконт.'
        shipment.error_message = ''
        db.session.commit()
        flash('Резултатът е записан.', 'success')
        return back
