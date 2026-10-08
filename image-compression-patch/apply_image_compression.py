"""Run with the shop virtualenv Python, from the directory containing app.py."""
import ast
import json
import os
from pathlib import Path
import shutil
import tempfile
from datetime import datetime

FILES = (
    '18d0a3ca6ae0423693880ce0d267f849.png',
    'fee9cf7f48494991a296f0a8a535f48d.jpg',
    'a09d6a0c98594771a3e9cd82fb72df3d.jpg',
    '3454b0e9729a41dc87f73ae79a43235d.png',
    'e03b6d67d062483f85cbe9e66cebe3ac.png',
    'd3746ca07edc45e29417bd532c1f1d7c.png',
    '261f8d82c728473792d8ff86eecc1773.webp',
)
OLD_HASH = 'hashlib.sha256(path.read_bytes()).hexdigest()[:16]'
NEW_HASH = "hashlib.sha256(path.read_bytes() + b'webp-q65-small-q72-large-v1').hexdigest()[:16]"
OLD_SAVE = "copy.save(target, 'WEBP', quality=82, method=6)"
NEW_SAVE = "copy.save(target, 'WEBP', quality=(65 if size <= 320 else 72), method=6)"

def atomic_write(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
        f.write(data)
    try:
        os.chmod(tmp, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)

def main():
    root = Path.cwd()
    module = root / 'responsive_images.py'
    originals = root / 'static/img/products'
    if not (root / 'app.py').is_file() or not module.is_file():
        raise SystemExit('Run from the shop directory containing app.py and responsive_images.py.')
    before = module.read_bytes()
    source = before.decode('utf-8')
    if NEW_HASH in source and NEW_SAVE in source:
        updated = source
    elif source.count(OLD_HASH) == 1 and source.count(OLD_SAVE) == 1:
        updated = source.replace(OLD_HASH, NEW_HASH).replace(OLD_SAVE, NEW_SAVE)
    else:
        raise SystemExit('Unrecognized generator. Nothing changed; provide responsive_images.py for review.')
    ast.parse(updated)
    missing = [name for name in FILES if not (originals / name).is_file()]
    if missing:
        raise SystemExit('Missing original images; nothing changed: ' + ', '.join(missing))
    namespace = {'__name__': 'compression_patch_generator', '__file__': str(module)}
    exec(compile(updated, str(module), 'exec'), namespace)
    backup = root / ('image-compression-backup-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    backup.mkdir()
    shutil.copy2(module, backup / module.name)
    manifests = originals / '_responsive'
    snapshots = {name: (manifests / (name + '.json')).read_bytes() if (manifests / (name + '.json')).exists() else None for name in FILES}
    for name, data in snapshots.items():
        if data is not None:
            (backup / (name + '.json')).write_bytes(data)
    try:
        for name in FILES:
            if not namespace['generate'](originals / name):
                raise RuntimeError('Generation failed: ' + name)
            rows = json.loads((manifests / (name + '.json')).read_text())
            for row in rows:
                with namespace['Image'].open(manifests / row['name']) as image:
                    image.verify()
        atomic_write(module, updated.encode('utf-8'))
    except BaseException:
        atomic_write(module, before)
        for name, data in snapshots.items():
            path = manifests / (name + '.json')
            if data is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, data)
        raise
    print('OK: 7 images regenerated; original images preserved.')
    print('Backup:', backup)
    print('Restart the Python application, then run a new PageSpeed test.')

if __name__ == '__main__':
    main()
