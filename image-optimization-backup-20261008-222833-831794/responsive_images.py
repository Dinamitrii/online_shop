"""Versioned WebP variants for uploaded shop images; originals stay intact."""
import hashlib
import io
import re
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
PROFILE = b'shop-webp-v4:content-detection:animation:adaptive-q55-62-70-floor45-52-60:alpha80-60:budget020:80-160-240-320-480-640-960-1200'
FORMATS = {'JPEG', 'PNG', 'WEBP', 'GIF', 'AVIF', 'BMP', 'TIFF'}
IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif', '.bmp', '.tif', '.tiff', '.heic', '.heif'}
MAX_ANIMATION_PIXELS = 40_000_000
MAX_ANIMATION_FRAMES = 150


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


def _encode_static(image, quality):
    # Retain the normal profile for light images. Try at most two moderate
    # reductions for expensive images, from the original resized pixels each time.
    budget = max(4096, int(image.width * image.height * 0.20))
    best = None
    for q, alpha in ((quality, 80), (quality - 5, 60), (quality - 10, 60)):
        buffer = io.BytesIO()
        image.save(buffer, 'WEBP', quality=q, alpha_quality=alpha, method=6)
        candidate = buffer.getvalue()
        if best is None or len(candidate) < len(best):
            best = candidate
        if len(best) <= budget:
            break
    return best


def inventory(root):
    """Inspect real file content, including nested folders and uppercase extensions."""
    root = Path(root)
    paths, skipped, failed = [], [], []
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d != '_responsive' and not (Path(parent) / d).is_symlink())
        for name in sorted(files):
            path = Path(parent) / name
            if path.is_symlink():
                continue
            try:
                with Image.open(path) as image:
                    if image.format in FORMATS:
                        paths.append(path)
                    else:
                        skipped.append((path, 'unsupported format: ' + str(image.format)))
            except Image.UnidentifiedImageError:
                if path.suffix.lower() in IMAGE_SUFFIXES:
                    failed.append((path, 'unreadable image or missing decoder'))
            except (OSError, ValueError, Image.DecompressionBombError) as error:
                failed.append((path, type(error).__name__))
    return paths, skipped, failed


def generate_result(path):
    """Publish a complete manifest; report skipped formats and failures explicitly."""
    path = Path(path)
    try:
        if not features.check('webp'):
            return {'status': 'failed', 'reason': 'WebP encoder unavailable'}
        source_bytes = path.read_bytes()
        digest = hashlib.sha256(source_bytes + PROFILE).hexdigest()[:16]
        folder = path.parent / '_responsive'
        manifest = folder / (path.name + '.json')
        with Image.open(io.BytesIO(source_bytes)) as raw:
            if raw.format not in FORMATS:
                return {'status': 'skipped', 'reason': 'unsupported format: ' + str(raw.format)}
            animated = getattr(raw, 'is_animated', False)
            frame_count = getattr(raw, 'n_frames', 1)
            if frame_count > 1 and not animated:
                return {'status': 'skipped', 'reason': 'multipage document preserved'}
            if animated and (frame_count > MAX_ANIMATION_FRAMES or raw.width * raw.height * frame_count > MAX_ANIMATION_PIXELS):
                return {'status': 'skipped', 'reason': 'large animation preserved (resource limit)'}
            orientation = raw.getexif().get(274, 1)
            loop = raw.info.get('loop', 0)
            # GIF without a loop extension plays once; WebP loop=1 matches that.
            if raw.format == 'GIF':
                loop = 0 if raw.info.get('loop') == 0 else raw.info.get('loop', 0) + 1
            frames, durations = [], []
            first_frame = 1 if animated and raw.format == 'PNG' and raw.info.get('default_image') else 0
            for index in range(first_frame, frame_count if animated else 1):
                raw.seek(index)
                frame = ImageOps.exif_transpose(raw)
                transparent = 'A' in frame.getbands() or 'transparency' in frame.info
                frame = frame.convert('RGBA' if transparent else 'RGB')
                if frame.mode == 'RGBA' and frame.getchannel('A').getextrema() == (255, 255):
                    frame = frame.convert('RGB')
                frames.append(frame)
                durations.append(int(raw.info.get('duration', 100)))
            image = frames[0]
            folder.mkdir(exist_ok=True)
            dimensions, rows = set(), []
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
                            valid = valid and bool(getattr(existing, 'is_animated', False)) == bool(animated)
                            for index in range(getattr(existing, 'n_frames', 1)):
                                existing.seek(index)
                                existing.load()
                    except (OSError, ValueError):
                        valid = False
                if not valid:
                    quality = 55 if size <= 320 else 62 if size <= 640 else 70
                    stream = io.BytesIO()
                    options = dict(quality=quality, method=6, alpha_quality=80)
                    if animated:
                        resized = [frame.resize(preview.size, Image.Resampling.LANCZOS) for frame in frames]
                        resized[0].save(stream, 'WEBP', save_all=True, append_images=resized[1:],
                                        duration=durations, loop=loop, **options)
                        encoded = stream.getvalue()
                    else:
                        encoded = _encode_static(preview, quality)
                    # Do not replace a small, already efficient WebP with more bytes
                    # at identical dimensions, or recompress it unnecessarily.
                    if raw.format == 'WEBP' and orientation == 1 and preview.size == image.size and len(source_bytes) < len(encoded):
                        encoded = source_bytes
                    with Image.open(io.BytesIO(encoded)) as check:
                        check.load()
                        if check.size != preview.size:
                            raise ValueError('Unexpected encoded dimensions')
                    _atomic_bytes(target, encoded)
                rows.append(dict(size=size, width=preview.width, height=preview.height, name=name))
            payload = json.dumps(rows, ensure_ascii=False).encode('utf-8')
            if not manifest.exists() or manifest.read_bytes() != payload:
                _atomic_bytes(manifest, payload)
            return {'status': 'optimized', 'format': raw.format, 'animated': animated, 'variants': len(rows)}
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        logging.getLogger(__name__).warning('Image optimization failed for %s: %s; original retained', path.name, type(error).__name__)
        return {'status': 'failed', 'reason': type(error).__name__}


def generate(path):
    """Compatible with the existing product/category upload callback."""
    return generate_result(path)['status'] == 'optimized'


def _source_for_url(app, url):
    parts = urlsplit(url or '')
    local = not parts.scheme and not parts.netloc
    if parts.netloc and parts.scheme in ('', 'http', 'https'):
        from flask import has_request_context, request
        local = has_request_context() and parts.netloc.lower() == request.host.lower()
    prefix = '/static/img/products/'
    if parts.path == '/static/img/brand/logo.png':
        prefix = '/static/img/brand/'
    if not local or not parts.path.startswith(prefix):
        return None
    relative = unquote(parts.path[len(prefix):])
    pieces = relative.split('/')
    if any(p in ('', '.', '..') for p in pieces) or any('\\' in p or '\x00' in p for p in pieces):
        return None
    # A saved URL to an older generated WebP can resolve to its original.
    if '_responsive' in pieces:
        if len(pieces) < 2 or pieces[-2] != '_responsive' or pieces.count('_responsive') != 1:
            return None
        match = re.fullmatch(r'(.+)\.[0-9a-f]{16}\.\d+\.webp', pieces[-1])
        if not match:
            return None
        pieces = pieces[:-2] + [match.group(1)]
    root = (Path(app.static_folder) / prefix.removeprefix('/static/')).resolve()
    source = root.joinpath(*pieces)
    if not source.resolve().is_relative_to(root):
        return None
    return source, prefix + '/'.join(quote(p) for p in pieces[:-1]) + ('/' if len(pieces) > 1 else '')

def install(app):
    def responsive_image_attrs(url, size=640, sizes='(max-width: 600px) 100vw, 300px'):
        attrs = {'src': url or ''}
        try:
            resolved = _source_for_url(app, url)
            if resolved is not None:
                source, prefix = resolved
                folder = source.parent / '_responsive'
                rows = json.loads((folder / (source.name + '.json')).read_text(encoding='utf-8'))
                valid = isinstance(rows, list) and bool(rows)
                for row in rows:
                    valid = valid and isinstance(row, dict) and all(
                        isinstance(row.get(key), int) and row[key] > 0 for key in ('width', 'size'))
                    entry = row.get('name', '') if isinstance(row, dict) else ''
                    valid = valid and isinstance(entry, str) and bool(entry) and '/' not in entry and '\\' not in entry and (folder / entry).is_file()
                if valid:
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
