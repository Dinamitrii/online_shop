#!/usr/bin/env python3
"""Защита на страницата /order/<id>/confirmation в app.py.

Проблем: всеки може да отвори /order/1/confirmation, /order/2/confirmation ...
и да види чужди поръчки (име, телефон, адрес, имейл).

Поправка (само в app.py):
  1. checkout() записва ID-то на новата поръчка в сесията на клиента;
  2. order_confirmation() връща 404, освен ако поръчката е в сесията на
     посетителя или той е влязъл като администратор.

Употреба (от папката на проекта):
    python apply_order_privacy.py            # прилага промяната (прави app.py.bak-order)
    python apply_order_privacy.py --dry-run  # само показва какво ще се промени
    python apply_order_privacy.py път/до/app.py

Безопасно е да се пуска повторно. Запазва Windows (CRLF) окончания на редовете.
"""
import difflib
import re
import shutil
import sys
from pathlib import Path

# 1) checkout(): реда, който препраща към потвърждението
REDIRECT_RE = re.compile(
    r"^(?P<indent>[ \t]+)return redirect\(url_for\('order_confirmation', order_id=order\.id\)\)[ \t]*$",
    re.M)
# 2) order_confirmation(): началото на функцията
DEF_RE = re.compile(r"^def order_confirmation\(order_id\):[ \t]*\n", re.M)

REMEMBER = '''{i}# Запомняме поръчката в сесията на клиента - само той може да види потвърждението ѝ.
{i}my_order_ids = session.get('my_order_ids', [])
{i}session['my_order_ids'] = (my_order_ids + [order.id])[-20:]
'''

GUARD = '''{i}# Потвърждението съдържа лични данни: достъпно е само за клиента, направил
{i}# поръчката (в неговата сесия), и за администратора. Другите виждат 404.
{i}if not session.get('is_admin') and order_id not in session.get('my_order_ids', []):
{i}    abort(404)
'''


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    dry_run = '--dry-run' in sys.argv
    path = Path(args[0] if args else 'app.py')
    if not path.is_file():
        sys.exit(f'Не намирам {path}. Пуснете скрипта от папката на проекта.')

    raw = path.read_bytes().decode('utf-8')
    crlf = '\r\n' in raw
    text = raw.replace('\r\n', '\n')
    new = text

    m_flask = re.search(r'^from flask import (?:[^\n]*\\\n)*[^\n]*', text, re.M)
    if not m_flask or not all(re.search(rf'\b{n}\b', m_flask.group(0)) for n in ('abort', 'session')):
        sys.exit("В 'from flask import ...' липсва abort или session. Нищо не е променено.")

    # --- стъпка 1: запомняне на поръчката при checkout ---
    if "session['my_order_ids']" not in new:
        matches = list(REDIRECT_RE.finditer(new))
        if len(matches) != 1:
            sys.exit("Не намирам еднозначно реда 'return redirect(url_for('order_confirmation', "
                     "order_id=order.id))' в checkout(). Нищо не е променено.")
        m = matches[0]
        new = new[:m.start()] + REMEMBER.format(i=m.group('indent')) + new[m.start():]

    # --- стъпка 2: проверка при показване ---
    if "session.get('my_order_ids', [])" not in new.split('def order_confirmation', 1)[-1][:1200]:
        matches = list(DEF_RE.finditer(new))
        if len(matches) != 1:
            sys.exit("Не намирам еднозначно 'def order_confirmation(order_id):'. Нищо не е променено.")
        m = matches[0]
        nxt = re.match(r'([ \t]+)\S', new[m.end():])
        indent = nxt.group(1) if nxt else '    '
        new = new[:m.end()] + GUARD.format(i=indent) + new[m.end():]

    if new == text:
        print('Вече е приложено - няма какво да се променя.')
        return

    try:
        compile(new, str(path), 'exec')
    except SyntaxError as e:
        sys.exit(f'Резултатът не е валиден Python ({e}). Нищо не е променено.')

    print('\n'.join(difflib.unified_diff(
        text.splitlines(), new.splitlines(), 'app.py (преди)', 'app.py (след)', lineterm='', n=2)))

    if dry_run:
        print('\n--dry-run: файлът не е променен.')
        return

    backup = path.with_name(path.name + '.bak-order')
    shutil.copyfile(path, backup)
    path.write_bytes((new.replace('\n', '\r\n') if crlf else new).encode('utf-8'))
    print(f'\nГотово. Копие на стария файл: {backup}')


if __name__ == '__main__':
    main()
