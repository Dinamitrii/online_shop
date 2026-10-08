# Permanent shop image optimization. Run from the app directory.
GENERATOR_SOURCE = '"""Versioned WebP variants for uploaded shop images; originals stay intact."""\nimport hashlib\nimport json\nimport logging\nimport os\nimport tempfile\nfrom pathlib import Path\nfrom urllib.parse import quote, unquote, urlsplit\nfrom PIL import Image, ImageOps, features\nfrom markupsafe import Markup, escape\n\nSIZES = (80, 160, 240, 320, 480, 640, 960, 1200)\n# Changing the profile creates new URLs instead of overwriting cached images.\nPROFILE = b\'shop-webp-v3:q55-62-70:alpha80:80-160-240-320-480-640-960-1200\'\n\n\ndef _atomic_bytes(path, data):\n    temp_path = None\n    try:\n        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=\'.tmp\', delete=False) as temp:\n            temp_path = Path(temp.name)\n            temp.write(data)\n        os.chmod(temp_path, 0o644)\n        os.replace(temp_path, path)\n    finally:\n        if temp_path is not None:\n            temp_path.unlink(missing_ok=True)\n\n\ndef generate(path):\n    """Return True after publishing a complete manifest; failures keep the old one."""\n    path = Path(path)\n    try:\n        if not features.check(\'webp\'):\n            return False\n        digest = hashlib.sha256(path.read_bytes() + PROFILE).hexdigest()[:16]\n        folder = path.parent / \'_responsive\'\n        manifest = folder / (path.name + \'.json\')\n        with Image.open(path) as raw:\n            if getattr(raw, \'is_animated\', False):\n                return False  # Preserve animation by using the original URL.\n            image = ImageOps.exif_transpose(raw)\n            transparent = \'A\' in image.getbands() or \'transparency\' in image.info\n            image = image.convert(\'RGBA\' if transparent else \'RGB\')\n            if image.mode == \'RGBA\' and image.getchannel(\'A\').getextrema() == (255, 255):\n                image = image.convert(\'RGB\')\n            folder.mkdir(exist_ok=True)\n            # Encode each actual size once. No upscaling and no duplicate widths.\n            dimensions = set()\n            rows = []\n            for size in SIZES:\n                preview = image.copy()\n                preview.thumbnail((size, size), Image.Resampling.LANCZOS)\n                if preview.size in dimensions:\n                    continue\n                dimensions.add(preview.size)\n                name = f\'{path.name}.{digest}.{size}.webp\'\n                target = folder / name\n                valid = False\n                if target.is_file():\n                    try:\n                        with Image.open(target) as existing:\n                            valid = existing.size == preview.size and existing.format == \'WEBP\'\n                            existing.load()\n                    except (OSError, ValueError):\n                        valid = False\n                if not valid:\n                    quality = 55 if size <= 320 else 62 if size <= 640 else 70\n                    temp_path = None\n                    try:\n                        with tempfile.NamedTemporaryFile(dir=folder, suffix=\'.tmp\', delete=False) as temp:\n                            temp_path = Path(temp.name)\n                        preview.save(temp_path, \'WEBP\', quality=quality, method=6, alpha_quality=80)\n                        with Image.open(temp_path) as check:\n                            check.load()\n                            if check.size != preview.size:\n                                raise ValueError(\'Unexpected encoded dimensions\')\n                        os.chmod(temp_path, 0o644)\n                        os.replace(temp_path, target)\n                    finally:\n                        if temp_path is not None:\n                            temp_path.unlink(missing_ok=True)\n                rows.append(dict(size=size, width=preview.width, height=preview.height, name=name))\n            payload = json.dumps(rows, ensure_ascii=False).encode(\'utf-8\')\n            if not manifest.exists() or manifest.read_bytes() != payload:\n                _atomic_bytes(manifest, payload)\n            return True\n    except (OSError, ValueError, Image.DecompressionBombError):\n        logging.getLogger(__name__).warning(\'Image optimization failed for %s; original retained\', path.name)\n        return False\n\n\ndef install(app):\n    def responsive_image_attrs(url, size=640, sizes=\'(max-width: 600px) 100vw, 300px\'):\n        attrs = {\'src\': url or \'\'}\n        try:\n            parts = urlsplit(url or \'\')\n            local = not parts.scheme and not parts.netloc\n            if parts.netloc and parts.scheme in (\'\', \'http\', \'https\'):\n                from flask import has_request_context, request\n                local = has_request_context() and parts.netloc.lower() == request.host.lower()\n            prefix = \'/static/img/products/\'\n            if parts.path == \'/static/img/brand/logo.png\':\n                prefix = \'/static/img/brand/\'\n            if local and parts.path.startswith(prefix):\n                name = unquote(parts.path[len(prefix):])\n                if name and \'/\' not in name and \'\\\\\' not in name and name not in (\'.\', \'..\'):\n                    folder = Path(app.static_folder) / prefix.removeprefix(\'/static/\') / \'_responsive\'\n                    rows = json.loads((folder / (name + \'.json\')).read_text(encoding=\'utf-8\'))\n                    valid = isinstance(rows, list) and bool(rows)\n                    for row in rows:\n                        valid = valid and isinstance(row, dict) and all(\n                            isinstance(row.get(key), int) and row[key] > 0 for key in (\'width\', \'size\'))\n                        entry = row.get(\'name\', \'\') if isinstance(row, dict) else \'\'\n                        valid = valid and isinstance(entry, str) and bool(entry) and \'/\' not in entry and \'\\\\\' not in entry and (folder / entry).is_file()\n                    if valid:\n                        # Older manifests may contain duplicate widths: prefer the smaller file.\n                        unique = {}\n                        for row in rows:\n                            previous = unique.get(row[\'width\'])\n                            if previous is None or (folder / row[\'name\']).stat().st_size < (folder / previous[\'name\']).stat().st_size:\n                                unique[row[\'width\']] = row\n                        chosen = min(rows, key=lambda row: abs(row[\'size\'] - size))\n                        base = prefix + \'_responsive/\'\n                        attrs[\'src\'] = base + quote(chosen[\'name\'])\n                        attrs[\'srcset\'] = \', \'.join(base + quote(row[\'name\']) + f\' {width}w\' for width, row in sorted(unique.items()))\n                        attrs[\'sizes\'] = sizes\n        except (OSError, ValueError, KeyError, TypeError, AttributeError):\n            pass\n        return Markup(\' \').join(Markup(\'{}="{}"\').format(key, escape(value)) for key, value in attrs.items())\n    app.jinja_env.globals[\'responsive_image_attrs\'] = responsive_image_attrs\n'
import ast
from datetime import datetime
from pathlib import Path
import shutil
import sys


def main():
    root = Path.cwd()
    app_path = root / 'app.py'
    module = root / 'responsive_images.py'
    folder = root / 'static/img/products'
    if not app_path.is_file() or not module.is_file() or not folder.is_dir():
        raise SystemExit('Run from the shop directory containing app.py and responsive_images.py.')
    tree = ast.parse(app_path.read_text(encoding='utf-8'))
    imports = {alias.name: alias.asname or alias.name for node in ast.walk(tree)
               if isinstance(node, ast.ImportFrom) and node.module == 'responsive_images'
               for alias in node.names}
    funcs = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == 'save_uploaded_image']
    upload_hook = len(funcs) == 1 and any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == imports.get('generate') for n in ast.walk(funcs[0]))
    install_hook = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == imports.get('install') for n in ast.walk(tree))
    if not upload_hook or not install_hook:
        raise SystemExit('Automatic upload integration not recognized. Nothing changed; provide app.py for review.')
    templates = root / 'templates'
    for name in ('index.html', 'category.html', 'search.html', 'product.html'):
        path = templates / name
        if not path.exists() or 'responsive_image_attrs(' not in path.read_text(encoding='utf-8'):
            raise SystemExit('Responsive template integration not recognized: ' + name + '. Nothing changed.')
    namespace = {'__name__': 'shop_optimizer', '__file__': str(module)}
    exec(compile(GENERATOR_SOURCE, str(module), 'exec'), namespace)
    if not namespace['features'].check('webp'):
        raise SystemExit('Pillow lacks WebP support. Nothing changed.')
    # Dynamic inventory: local and hosting copies need not contain identical files.
    paths = sorted(p for p in folder.iterdir() if p.is_file() and not p.is_symlink()
                   and p.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.avif'})
    backup = root / ('image-optimization-backup-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    (backup / '_responsive').mkdir(parents=True)
    shutil.copy2(module, backup / module.name)
    old_manifests = folder / '_responsive'
    if old_manifests.is_dir():
        for p in old_manifests.glob('*.json'):
            shutil.copy2(p, backup / '_responsive' / p.name)
    namespace['_atomic_bytes'](module, GENERATOR_SOURCE.encode('utf-8'))
    print('Installed automatic image optimization.', flush=True)
    print('Backup:', backup, flush=True)
    optimized, animated, failed = 0, [], []
    for index, path in enumerate(paths, 1):
        try:
            with namespace['Image'].open(path) as image:
                if getattr(image, 'is_animated', False):
                    animated.append(path.name)
                    continue
        except (OSError, ValueError, namespace['Image'].DecompressionBombError):
            failed.append(path.name)
            continue
        if namespace['generate'](path):
            optimized += 1
        else:
            failed.append(path.name)
        print(f'[{index}/{len(paths)}] {path.name}', flush=True)
    print(f'RESULT: optimized={optimized}, animated_preserved={len(animated)}, failed={len(failed)}', flush=True)
    for name in failed:
        print('FAILED (original/previous variants retained):', name)
    print('Restart the Python application to enable the new optimizer for future uploads.')
    return 1 if failed else 0

if __name__ == '__main__':
    sys.exit(main())
