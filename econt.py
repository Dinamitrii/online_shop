"""Econt JSON transport. Fixed HTTPS origins, no redirects or automatic retries."""
import base64
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener


class EcontError(Exception):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class EcontClient:
    BASES = {'test': 'https://demo.econt.com/ee/services/',
             'live': 'https://ee.econt.com/services/'}

    def __init__(self, config):
        self.environment = config.get('COURIER_ENVIRONMENT', 'test')
        if (not config.get('COURIER_ENABLED') or config.get('COURIER_PROVIDER') != 'econt'
                or self.environment not in self.BASES):
            raise EcontError('Интеграцията с Еконт не е настроена или е изключена.')
        username, password = config.get('ECONT_USERNAME', ''), config.get('ECONT_PASSWORD', '')
        if not username or not password:
            raise EcontError('Липсват данни за достъп до Еконт в .env.')
        if self.environment == 'live' and username == 'iasp-dev':
            raise EcontError('Демо акаунтът може да се използва само в тестова среда.')
        self.authorization = 'Basic ' + base64.b64encode(f'{username}:{password}'.encode()).decode()
        self.opener = build_opener(NoRedirect())

    def call(self, method, data):
        request = Request(self.BASES[self.environment] + method + '.json',
                          data=json.dumps(data, ensure_ascii=False, allow_nan=False).encode(),
                          headers={'Authorization': self.authorization,
                                   'Content-Type': 'application/json', 'Accept': 'application/json'},
                          method='POST')
        try:
            with self.opener.open(request, timeout=25) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
        except HTTPError as exc:
            # Econt reports explicit input validation failures as HTTP 517.
            # These are safe to correct; all other server failures remain uncertain.
            try:
                fault = json.loads(exc.read(65536))
            except (ValueError, UnicodeError, OSError, AttributeError):
                fault = {}
            if isinstance(fault, dict) and fault.get('type') == 'ExInvalidParam':
                raise EcontError('Еконт: ' + str(fault.get('message', 'Невалидни данни.'))[:500]) from None
            if exc.code in (401, 403):
                raise EcontError('Еконт отказа достъпа. Проверете акаунта и средата.') from None
            # A server error may happen after the shipment has already been created.
            if exc.code >= 500 or 300 <= exc.code < 400:
                raise EcontError('Няма потвърден отговор от Еконт.', uncertain=True) from None
            raise EcontError('Еконт отхвърли заявката. Проверете данните за пратката.') from None
        except (URLError, TimeoutError, OSError):
            raise EcontError('Връзката с Еконт е прекъсната. Резултатът не е потвърден.', uncertain=True) from None
        try:
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError()
        except (ValueError, UnicodeError):
            raise EcontError('Получен е невалиден отговор от Еконт.', uncertain=True) from None
        error = result.get('error') or (result if result.get('type') and result.get('message') else None)
        if error:
            # Jinja escapes this message; never include request credentials or full response.
            message = error.get('message', 'Невалидни данни.') if isinstance(error, dict) else 'Невалидни данни.'
            raise EcontError('Еконт: ' + str(message)[:500])
        return result

    def label(self, label, mode):
        result = self.call('Shipments/LabelService.createLabel', {'label': label, 'mode': mode})
        if not isinstance(result.get('label'), dict):
            raise EcontError('Липсва товарителница в отговора на Еконт.', uncertain=True)
        return result['label']

    def status(self, number):
        result = self.call('Shipments/ShipmentService.getShipmentStatuses', {'shipmentNumbers': [number]})
        rows = result.get('shipmentStatuses') or []
        if not rows or rows[0].get('error') or not isinstance(rows[0].get('status'), dict):
            raise EcontError('Еконт не върна статус за тази товарителница.')
        status = rows[0]['status']
        if str(status.get('shipmentNumber')) != number:
            raise EcontError('Номерът на върнатата товарителница не съвпада.')
        return status


    def cancel_label(self, number):
        result = self.call('Shipments/LabelService.deleteLabels', {'shipmentNumbers': [number]})
        rows = result.get('results')
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or str(rows[0].get('shipmentNum')) != str(number):
            raise EcontError('Липсва потвърждение за анулирането от Еконт.', uncertain=True)
        if rows[0].get('error'):
            raise EcontError('Еконт: ' + error_message(rows[0]['error']))

    def request_courier(self, payload):
        result = self.call('Shipments/ShipmentService.requestCourier', payload)
        request_id = result.get('courierRequestID')
        if not isinstance(request_id, (str, int)) or isinstance(request_id, bool) or not str(request_id).strip() or str(request_id) == '0':
            raise EcontError('Липсва номер на заявката за куриер.', uncertain=True)
        return str(request_id), ' '.join(str(result.get(key) or '') for key in ('warnings', 'delayedRequestWarning')).strip()[:1000]

    def courier_request_status(self, request_id):
        result = self.call('Shipments/ShipmentService.getRequestCourierStatus', {'requestCourierIds': [request_id]})
        rows = result.get('requestCourierStatus')
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise EcontError('Липсва статус на заявката за куриер.')
        if rows[0].get('error'):
            raise EcontError('Еконт: ' + error_message(rows[0]['error']))
        status = rows[0].get('status')
        if not isinstance(status, dict) or str(status.get('id')) != str(request_id):
            raise EcontError('Номерът на заявката за куриер не съвпада.')
        return status


def safe_pdf_url(value, environment):
    """Only open Econt's own PDF host, in the shipment's original environment."""
    if not isinstance(value, str):
        return ''
    try:
        parsed = urlsplit(value)
        host = 'demo.econt.com' if environment == 'test' else 'ee.econt.com'
        if (parsed.scheme in ('http', 'https') and parsed.hostname == host and parsed.port in (None, 443)
                and not parsed.username and not parsed.password and '\\' not in value
                and not any(ord(c) < 32 for c in value)):
            # Econt still returns HTTP links in demo responses; open over TLS only.
            return parsed._replace(scheme='https').geturl()
    except ValueError:
        pass
    return ''
