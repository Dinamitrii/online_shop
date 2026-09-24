#!/usr/bin/env python3
"""Сменя <img> на логото в шаблоните с адаптивен вариант (srcset + WebP).

Преди: <img src="/static/img/brand/logo.png" alt="..." class="brand-logo">
След:  <img src="...logo-140.png" srcset="...70.webp 70w, ...140.webp 140w, ...210.webp 210w"
            sizes="69px" width="69" height="52" alt="..." class="brand-logo">

Първо пуснете make_logo_variants.py (създава logo-70/140/210.webp и logo-140.png).

Употреба (от папката на проекта):
    python apply_logo_template.py --dry-run   # само показва промените
    python apply_logo_template.py             # прилага ги
Отмяна: git checkout -- templates/

Обработва всички *.html в templates/ (включително подпапки), намира <img> тагове,
които сочат към img/brand/logo.png - и с url_for(...), и с директен /static/... адрес.
Запазва останалите атрибути (alt, class, id, style ...) и CRLF окончанията.
Ако логото в някой шаблон има собствен размер (width/height или style), размерът
не се пипа и не се добавя sizes - така логото не може да се деформира.
Безопасно е да се пуска повторно.
"""
import difflib
import re
import sys
from pathlib import Path

SHOWN_W, SHOWN_H = 69, 52          # размер, на който логото се показва
VARIANTS = ('logo-70.webp', 'logo-140.webp', 'logo-210.webp', 'logo-140.png')
BRAND_DIR = Path('static/img/brand')

IMG_RE = re.compile(r'<img\b[^>]*?brand/logo\.png[^>]*>', re.S | re.I)
ATTR_RE = re.compile(r'''([\w:-]+)\s*=\s*("[^"]*"|'[^']*')''', re.S)
DROP = {'src', 'srcset', 'sizes'}


def static_url(name):
    return "{{ url_for('static', filename='img/brand/%s') }}" % name


def rebuild(tag):
    """Нов <img> таг: адаптивни src/srcset, запазени останалите атрибути.

    width/height/sizes се добавят само ако тагът няма собствен размер (width/height
    атрибут или style с width/height) - т.е. ако е стандартното лого в хедъра,
    което се показва на 69x52. Иначе размерът не се пипа, за да не се деформира логото.
    """
    attrs = [(m.group(1), m.group(2)) for m in ATTR_RE.finditer(tag)]
    names = {n.lower() for n, _ in attrs}
    style = next((v for n, v in attrs if n.lower() == 'style'), '')
    own_size = bool(names & {'width', 'height'}) or bool(re.search(r'width|height', style, re.I))

    kept = [f'{n}={v}' for n, v in attrs if n.lower() not in DROP]
    srcset = ',\n                '.join(f'{static_url(f"logo-{w}.webp")} {w}w' for w in (70, 140, 210))
    parts = [f'src="{static_url("logo-140.png")}"', f'srcset="{srcset}"']
    if not own_size:
        parts += [f'sizes="{SHOWN_W}px"', f'width="{SHOWN_W}"', f'height="{SHOWN_H}"']
    return '<img ' + '\n     '.join(parts + kept) + '>'


def main():
    dry_run = '--dry-run' in sys.argv
    tpl_dir = Path('templates')
    if not tpl_dir.is_dir():
        sys.exit('Не намирам папка templates/. Пуснете скрипта от папката на проекта.')

    missing = [v for v in VARIANTS if not (BRAND_DIR / v).is_file()]
    if missing:
        sys.exit('Липсват файлове в static/img/brand/: ' + ', '.join(missing) +
                 '\nПърво пуснете: python make_logo_variants.py')

    changed = 0
    others = []
    for path in sorted(tpl_dir.rglob('*.html')):
        raw = path.read_bytes().decode('utf-8')
        crlf = '\r\n' in raw
        text = raw.replace('\r\n', '\n')

        def repl(m):
            tag = m.group(0)
            return tag if 'logo-70.webp' in tag else rebuild(tag)

        new = IMG_RE.sub(repl, text)

        # други препратки към стария файл (CSS, meta og:image ...) - само се показват
        rest = IMG_RE.sub('', new)
        if 'brand/logo.png' in rest:
            others.append(path)

        if new == text:
            continue
        changed += 1
        print('\n'.join(difflib.unified_diff(
            text.splitlines(), new.splitlines(), f'{path} (преди)', f'{path} (след)',
            lineterm='', n=1)))
        if not dry_run:
            path.write_bytes((new.replace('\n', '\r\n') if crlf else new).encode('utf-8'))

    if not changed:
        print('Не намерих <img> с logo.png за смяна (или вече е приложено).')
    elif dry_run:
        print(f'\n--dry-run: {changed} файл(а) биха се променили, нищо не е записано.')
    else:
        print(f'\nГотово: променени са {changed} файл(а). Отмяна: git checkout -- templates/')

    for p in others:
        print(f'ВНИМАНИЕ: {p} още сочи към brand/logo.png извън <img> '
              f'(например CSS или og:image) - не е променено.')


if __name__ == '__main__':
    main()
