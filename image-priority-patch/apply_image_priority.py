"""Run from shop root: python apply_image_priority.py"""
from pathlib import Path
from datetime import datetime
import os,re,tempfile
from jinja2 import Environment

GRID = '''fetchpriority="{{ 'high' if loop.index <= 2 else 'auto' }}" loading="{{ 'eager' if loop.index <= 6 else 'lazy' }}" decoding="async"'''
LAZY = 'fetchpriority="auto" loading="lazy" decoding="async"'
MAIN = 'fetchpriority="high" loading="eager"'
RULES = {
 'index.html': [('c.image_url', GRID), ('p.image_url', LAZY)],
 'category.html': [('category.image_url', 'fetchpriority="auto" loading="eager" decoding="async"'), ('p.image_url', GRID)],
 'search.html': [('p.image_url', GRID)],
 'product.html': [('product.image_url', MAIN), ('p.image_url', LAZY)],
}

def patch(source, rules):
    for variable, attrs in rules:
        matches = [m for m in re.finditer(r'<img\b[^>]*>', source, re.S|re.I)
                   if re.search(r'responsive_image_attrs\(\s*'+re.escape(variable)+r'\s*,', m.group())]
        if len(matches)!=1:
            raise ValueError('Expected one responsive image for '+variable+', found '+str(len(matches)))
        match=matches[0];tag=match.group()
        tag=re.sub(r'''\s+(?:fetchpriority|loading|decoding)\s*=\s*(?:"[^"]*"|'[^']*')''','',tag,flags=re.I)
        if tag.endswith('/>'):tag=tag[:-2].rstrip()+' '+attrs+' />'
        else:tag=tag[:-1].rstrip()+' '+attrs+'>'
        source=source[:match.start()]+tag+source[match.end():]
    Environment().parse(source)
    return source

def atomic(path,data):
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as temp:
        temp.write(data);name=Path(temp.name)
    try:
        os.chmod(name,path.stat().st_mode & 0o777)
        os.replace(name,path)
    finally:name.unlink(missing_ok=True)

def main():
    root=Path.cwd();templates=root/'templates'
    if not (root/'app.py').is_file():raise SystemExit('Run from the shop root containing app.py.')
    originals={};changes={}
    try:
        for name,rules in RULES.items():
            path=templates/name;originals[path]=path.read_bytes()
            changes[path]=patch(originals[path].decode('utf-8'),rules).encode('utf-8')
    except (OSError,ValueError) as error:
        raise SystemExit('Nothing changed: '+str(error))
    if all(changes[p]==originals[p] for p in changes):
        print('Already installed; nothing changed.');return
    backup=root/('image-priority-backup-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))/'templates'
    backup.mkdir(parents=True)
    for path,data in originals.items():(backup/path.name).write_bytes(data)
    try:
        for path,data in changes.items():atomic(path,data)
    except BaseException:
        for path,data in originals.items():atomic(path,data)
        raise
    print('OK: image priorities installed in four templates.')
    print('Backup:',backup.parent)
    print('Restart the application, then check the live pages.')

if __name__=='__main__':main()
