"""Run from the shop root after extracting this package there."""
import ast
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


def write(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:
        f.write(data);temp=Path(f.name)
    try:
        os.chmod(temp,path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def main():
    root=Path.cwd();bundle=Path(__file__).resolve().parent/'css-fonts-payload'
    app_path=root/'app.py';base=root/'templates/base.html'
    if not app_path.is_file() or not base.is_file() or not (root/'static/css/style.css').is_file():
        raise SystemExit('Run from the shop root containing app.py and templates/base.html.')
    app_source=app_path.read_text();template=base.read_text()
    ast.parse(app_source)
    config=json.loads((bundle/'fonts.json').read_text())
    for row in config['fonts'].values():
        data=(bundle/'assets'/row['name']).read_bytes()
        if hashlib.sha256(data).hexdigest()!=row['sha256']:
            raise SystemExit('Font verification failed; nothing changed.')
    css=(root/'static/css/style.css').read_text()
    if re.search(r'</style|@import|@charset|url\s*\(',css,re.I):
        raise SystemExit('CSS uses imports or asset URLs; needs review before inlining. Nothing changed.')
    hook="\n# CSS and fonts performance\nfrom performance_assets import install as install_performance_assets\ninstall_performance_assets(app)\n"
    if 'install_performance_assets(app)' not in app_source:
        anchor='install_responsive_images(app)'
        if app_source.count(anchor)!=1:
            raise SystemExit('Unknown app initialization; nothing changed.')
        app_source=app_source.replace(anchor,anchor+hook)
    if '{{ shop_performance_head() }}' not in template:
        stylesheet_tags=[m.group() for m in re.finditer(r'<link\b[^>]*>',template,re.I|re.S) if 'css/style.css' in m.group()]
        if len(stylesheet_tags)!=1:
            raise SystemExit('Unknown stylesheet integration; nothing changed.')
        template=template.replace(stylesheet_tags[0],'{{ shop_performance_head() }}')
    template=re.sub(r'<link\b[^>]*>',lambda m:'' if 'fonts.googleapis.com' in m.group() or 'fonts.gstatic.com' in m.group() else m.group(),template,flags=re.I|re.S)
    template=re.sub(r'<noscript>\s*</noscript>','',template,flags=re.I)
    ast.parse(app_source);ast.parse((bundle/'performance_assets.py').read_text())
    from jinja2 import Environment
    Environment().parse(template)
    backup=root/('css-fonts-backup-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'));backup.mkdir()
    targets={app_path:app_source.encode(),base:template.encode(),root/'performance_assets.py':(bundle/'performance_assets.py').read_bytes()}
    previous={p:p.read_bytes() if p.exists() else None for p in targets}
    for path,data in previous.items():
        if data is not None:
            dest=backup/path.relative_to(root);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
    assets=root/'performance-fonts';assets.mkdir(exist_ok=True)
    old_config=assets/'fonts.json'
    if old_config.exists():shutil.copy2(old_config,backup/'fonts.json')
    for source in (bundle/'assets').iterdir():
        write(assets/source.name,source.read_bytes())
    write(assets/'fonts.json',(bundle/'fonts.json').read_bytes())
    try:
        for path,data in targets.items():write(path,data)
    except BaseException:
        for path,data in previous.items():
            if data is None:path.unlink(missing_ok=True)
            else:write(path,data)
        raise
    print('OK: CSS and local fonts installed.')
    print('Backup:',backup)
    print('Restart the Python application, then run a new PageSpeed test.')

if __name__=='__main__':main()
