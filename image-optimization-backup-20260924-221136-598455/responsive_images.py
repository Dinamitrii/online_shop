"""Local image variants; originals and database URLs remain unchanged."""
import hashlib
import json
import logging
from pathlib import Path
from urllib.parse import unquote, urlsplit
from PIL import Image, ImageOps
from markupsafe import Markup, escape

SIZES = (320, 640, 1200)

def generate(path):
    path = Path(path)
    try:
        with Image.open(path) as raw:
            if getattr(raw, 'is_animated', False):
                return False
            image = ImageOps.exif_transpose(raw)
            image = image.convert('RGBA' if 'A' in image.getbands() or 'transparency' in image.info else 'RGB')
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            folder = path.parent / '_responsive'
            folder.mkdir(exist_ok=True)
            rows = []
            for size in SIZES:
                copy = image.copy()
                copy.thumbnail((size, size), Image.Resampling.LANCZOS)
                name = f'{path.name}.{digest}.{size}.webp'
                target = folder / name
                if not target.exists():
                    copy.save(target, 'WEBP', quality=82, method=6)
                rows.append({'size': size, 'width': copy.width, 'name': name})
            manifest = folder / (path.name + '.json')
            temp = manifest.with_suffix('.json.tmp')
            temp.write_text(json.dumps(rows), encoding='utf-8')
            temp.replace(manifest)
            return True
    except (OSError, ValueError, Image.DecompressionBombError):
        logging.getLogger(__name__).warning('Image variants unavailable for %s; keeping original', path.name)
        return False

def install(app):
    def responsive_image_attrs(url, size=640, sizes='(max-width: 600px) 100vw, 300px'):
        attrs = {'src': url or ''}
        parts = urlsplit(url or '')
        prefix = '/static/img/products/'
        if not parts.scheme and not parts.netloc and parts.path.startswith(prefix):
            name = unquote(parts.path[len(prefix):])
            if name and '/' not in name and '\\' not in name and name not in ('.', '..'):
                folder = Path(app.static_folder) / 'img/products/_responsive'
                try:
                    rows = json.loads((folder / (name + '.json')).read_text())
                    if all((folder / row['name']).is_file() for row in rows):
                        base = '/static/img/products/_responsive/'
                        from urllib.parse import quote
                        chosen = min(rows, key=lambda row: abs(row['size'] - size))
                        attrs['src'] = base + quote(chosen['name'])
                        unique = {row['width']: row for row in rows}
                        attrs['srcset'] = ', '.join(base + quote(row['name']) + f' {width}w' for width, row in sorted(unique.items()))
                        attrs['sizes'] = sizes
                except (OSError, ValueError, KeyError, TypeError):
                    pass
        return Markup(' ').join(Markup('{}="{}"').format(key, escape(value)) for key, value in attrs.items())
    app.jinja_env.globals['responsive_image_attrs'] = responsive_image_attrs
