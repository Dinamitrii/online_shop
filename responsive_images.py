"""Versioned WebP variants for uploaded shop images; originals stay intact."""
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from PIL import Image, ImageOps, features
from markupsafe import Markup, escape

SIZES = (80, 160, 240, 320, 480, 640, 960, 1200)
# Changing the profile creates new URLs instead of overwriting cached images.
PROFILE = b'shop-webp-v3:q55-62-70:alpha80:80-160-240-320-480-640-960-1200'


def _atomic_bytes(path, data):
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.tmp', delete=False) as temp:
            temp_path = Path(temp.name)
            temp.write(data)
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def generate(path):
    """Return True after publishing a complete manifest; failures keep the old one."""
    path = Path(path)
    try:
        if not features.check('webp'):
            return False
        digest = hashlib.sha256(path.read_bytes() + PROFILE).hexdigest()[:16]
        folder = path.parent / '_responsive'
        manifest = folder / (path.name + '.json')
        with Image.open(path) as raw:
            if getattr(raw, 'is_animated', False):
                return False  # Preserve animation by using the original URL.
            image = ImageOps.exif_transpose(raw)
            transparent = 'A' in image.getbands() or 'transparency' in image.info
            image = image.convert('RGBA' if transparent else 'RGB')
            if image.mode == 'RGBA' and image.getchannel('A').getextrema() == (255, 255):
                image = image.convert('RGB')
            folder.mkdir(exist_ok=True)
            # Encode each actual size once. No upscaling and no duplicate widths.
            dimensions = set()
            rows = []
            for size in SIZES:
                preview = image.copy()
                preview.thumbnail((size, size), Image.Resampling.LANCZOS)
                if preview.size in dimensions:
                    continue
                dimensions.add(preview.size)
                name = f'{path.name}.{digest}.{size}.webp'
                target = folder / name
                valid = False
                if target.is_file():
                    try:
                        with Image.open(target) as existing:
                            valid = existing.size == preview.size and existing.format == 'WEBP'
                            existing.load()
                    except (OSError, ValueError):
                        valid = False
                if not valid:
                    quality = 55 if size <= 320 else 62 if size <= 640 else 70
                    temp_path = None
                    try:
                        with tempfile.NamedTemporaryFile(dir=folder, suffix='.tmp', delete=False) as temp:
                            temp_path = Path(temp.name)
                        preview.save(temp_path, 'WEBP', quality=quality, method=6, alpha_quality=80)
                        with Image.open(temp_path) as check:
                            check.load()
                            if check.size != preview.size:
                                raise ValueError('Unexpected encoded dimensions')
                        os.chmod(temp_path, 0o644)
                        os.replace(temp_path, target)
                    finally:
                        if temp_path is not None:
                            temp_path.unlink(missing_ok=True)
                rows.append(dict(size=size, width=preview.width, height=preview.height, name=name))
            payload = json.dumps(rows, ensure_ascii=False).encode('utf-8')
            if not manifest.exists() or manifest.read_bytes() != payload:
                _atomic_bytes(manifest, payload)
            return True
    except (OSError, ValueError, Image.DecompressionBombError):
        logging.getLogger(__name__).warning('Image optimization failed for %s; original retained', path.name)
        return False


def install(app):
    def responsive_image_attrs(url, size=640, sizes='(max-width: 600px) 100vw, 300px'):
        attrs = {'src': url or ''}
        try:
            parts = urlsplit(url or '')
            local = not parts.scheme and not parts.netloc
            if parts.netloc and parts.scheme in ('', 'http', 'https'):
                from flask import has_request_context, request
                local = has_request_context() and parts.netloc.lower() == request.host.lower()
            prefix = '/static/img/products/'
            if parts.path == '/static/img/brand/logo.png':
                prefix = '/static/img/brand/'
            if local and parts.path.startswith(prefix):
                name = unquote(parts.path[len(prefix):])
                if name and '/' not in name and '\\' not in name and name not in ('.', '..'):
                    folder = Path(app.static_folder) / prefix.removeprefix('/static/') / '_responsive'
                    rows = json.loads((folder / (name + '.json')).read_text(encoding='utf-8'))
                    valid = isinstance(rows, list) and bool(rows)
                    for row in rows:
                        valid = valid and isinstance(row, dict) and all(
                            isinstance(row.get(key), int) and row[key] > 0 for key in ('width', 'size'))
                        entry = row.get('name', '') if isinstance(row, dict) else ''
                        valid = valid and isinstance(entry, str) and bool(entry) and '/' not in entry and '\\' not in entry and (folder / entry).is_file()
                    if valid:
                        # Older manifests may contain duplicate widths: prefer the smaller file.
                        unique = {}
                        for row in rows:
                            previous = unique.get(row['width'])
                            if previous is None or (folder / row['name']).stat().st_size < (folder / previous['name']).stat().st_size:
                                unique[row['width']] = row
                        chosen = min(rows, key=lambda row: abs(row['size'] - size))
                        base = prefix + '_responsive/'
                        attrs['src'] = base + quote(chosen['name'])
                        attrs['srcset'] = ', '.join(base + quote(row['name']) + f' {width}w' for width, row in sorted(unique.items()))
                        attrs['sizes'] = sizes
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        return Markup(' ').join(Markup('{}="{}"').format(key, escape(value)) for key, value in attrs.items())
    app.jinja_env.globals['responsive_image_attrs'] = responsive_image_attrs
