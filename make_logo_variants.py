#!/usr/bin/env python3
"""Прави адаптивни варианти на логото (за srcset), без да пипа оригинала.

Логото се показва на 69x52 px, а файлът е 510x382 (~375 KiB). Скриптът създава:
    logo-70.webp    (1x)
    logo-140.webp   (2x, екрани с висока плътност)
    logo-210.webp   (3x)
    logo-140.png    (резервен вариант за src, ако браузърът не знае srcset)

Употреба (от папката на проекта, нужен е Pillow: pip install Pillow):
    python make_logo_variants.py
    python make_logo_variants.py static/img/brand/logo.png
"""
import sys
from pathlib import Path

from PIL import Image

WIDTHS = (70, 140, 210)
WEBP_QUALITY = 82


def kib(path):
    return f'{path.stat().st_size / 1024:.1f} KiB'


def main():
    src = Path(sys.argv[1] if len(sys.argv) > 1 else 'static/img/brand/logo.png')
    if not src.is_file():
        sys.exit(f'Не намирам {src}. Пуснете скрипта от папката на проекта или подайте път.')

    img = Image.open(src)
    has_alpha = img.mode in ('RGBA', 'LA') or 'transparency' in img.info
    img = img.convert('RGBA' if has_alpha else 'RGB')
    print(f'Оригинал: {src.name} {img.width}x{img.height}, {kib(src)}, '
          f'{"с" if has_alpha else "без"} прозрачност')

    out_dir = src.parent
    for w in WIDTHS:
        h = round(img.height * w / img.width)
        small = img.resize((w, h), Image.LANCZOS)
        out = out_dir / f'logo-{w}.webp'
        small.save(out, 'WEBP', quality=WEBP_QUALITY, method=6)
        print(f'  {out.name:14} {w}x{h:<4} {kib(out)}')
        if w == 140:
            fb = out_dir / 'logo-140.png'
            small.save(fb, 'PNG', optimize=True)
            print(f'  {fb.name:14} {w}x{h:<4} {kib(fb)}  (резервен)')

    print('\nОригиналът е запазен. Сега сменете <img> в шаблона (виж инструкциите).')


if __name__ == '__main__':
    main()
