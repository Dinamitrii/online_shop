#!/usr/bin/env python3
"""Замества sitemap_xml() в app.py с версия, която генерира пълна структура:
<loc>, <lastmod>, <changefreq>, <priority> за всеки адрес (като файла от краулъра).

Употреба (от папката на проекта):
    python apply_sitemap_full.py            # прилага промяната (прави app.py.bak-sitemap)
    python apply_sitemap_full.py --dry-run  # само показва какво ще се промени
    python apply_sitemap_full.py път/до/app.py

Безопасно е да се пуска повторно и работи независимо дали sitemap_lastmod.patch
вече е приложен. Запазва Windows (CRLF) окончания на редовете.
"""
import difflib
import re
import shutil
import sys
from pathlib import Path

BEGIN = '# --- sitemap:begin ---'
ROUTE = "@app.route('/sitemap.xml')"
END_RE = re.compile(r'^@app\.route\(["\']/favicon\.ico["\']\)', re.M)

NEW_BLOCK = '''# --- sitemap:begin ---
# Дата за записи без реална дата на промяна (създадени преди да се появи
# updated_at). Фиксирана е нарочно: "днешна дата" при всяка заявка би
# подвеждала търсачките. Щом продуктът/категорията се редактира, се ползва
# истинската дата.
SITEMAP_FALLBACK_LASTMOD = (2026, 9, 23)


@app.route('/sitemap.xml')
def sitemap_xml():
    from datetime import datetime
    from xml.etree import ElementTree as _ET

    site_url = 'https://e-jelezaria.bg'
    fallback = datetime(*SITEMAP_FALLBACK_LASTMOD)

    root = Element(
        'urlset',
        xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'
    )

    def add_url(endpoint, lastmod=None, changefreq='weekly', priority='0.7', **values):
        entry = SubElement(root, 'url')

        path = url_for(endpoint, _external=False, **values)
        SubElement(entry, 'loc').text = site_url + path

        # Датите в базата се пазят в UTC.
        lastmod = lastmod or fallback
        if lastmod.tzinfo is None:
            lastmod = lastmod.replace(tzinfo=timezone.utc)

        SubElement(entry, 'lastmod').text = (
            lastmod.astimezone(timezone.utc)
            .isoformat(timespec='seconds')
            .replace('+00:00', 'Z')
        )
        SubElement(entry, 'changefreq').text = changefreq
        SubElement(entry, 'priority').text = priority

    def latest(*dates):
        """Най-новата от подадените дати (пропуска празните)."""
        dates = [d for d in dates if d is not None]
        return max(dates) if dates else None

    categories = Category.query.order_by(Category.id).all()
    products = Product.query.order_by(Product.id).all()

    # Категорията е променена, когато е променена тя самата или някой от
    # продуктите ѝ. Началната страница - при промяна на каквото и да е в каталога.
    category_lastmod = {c.id: getattr(c, 'updated_at', None) for c in categories}
    for product in products:
        category_lastmod[product.category_id] = latest(
            category_lastmod.get(product.category_id),
            getattr(product, 'updated_at', None)
        )

    add_url('index', lastmod=latest(*category_lastmod.values()), priority='1.0')
    add_url('contacts')

    for category in categories:
        add_url(
            'category_view',
            category_id=category.id,
            lastmod=category_lastmod.get(category.id)
        )

    for product in products:
        add_url(
            'product_view',
            product_id=product.id,
            lastmod=getattr(product, 'updated_at', None)
        )

    if hasattr(_ET, 'indent'):  # Python 3.9+; по-четим изход
        _ET.indent(root, space='  ')

    return Response(
        tostring(root, encoding='utf-8', xml_declaration=True),
        content_type='application/xml; charset=utf-8',
        headers={'Cache-Control': 'no-store'}
    )
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

    start = text.find(BEGIN)
    if start == -1:
        start = text.find(ROUTE)
    end = END_RE.search(text, start) if start != -1 else None
    if start == -1 or end is None:
        sys.exit("Не намирам sitemap_xml() (очаквам @app.route('/sitemap.xml') "
                 "следван от @app.route('/favicon.ico')). Нищо не е променено.")

    new_text = text[:start] + NEW_BLOCK.rstrip('\n') + '\n\n\n' + text[end.start():]

    if new_text == text:
        print('Вече е приложено - няма какво да се променя.')
        return

    try:
        compile(new_text, str(path), 'exec')
    except SyntaxError as e:
        sys.exit(f'Резултатът не е валиден Python ({e}). Нищо не е променено.')

    diff = list(difflib.unified_diff(
        text.splitlines(), new_text.splitlines(), 'app.py (преди)', 'app.py (след)', lineterm='', n=1))
    print('\n'.join(diff))

    if dry_run:
        print('\n--dry-run: файлът не е променен.')
        return

    backup = path.with_name(path.name + '.bak-sitemap')
    shutil.copyfile(path, backup)
    out = new_text.replace('\n', '\r\n') if crlf else new_text
    path.write_bytes(out.encode('utf-8'))
    print(f'\nГотово. Копие на стария файл: {backup}')


if __name__ == '__main__':
    main()
