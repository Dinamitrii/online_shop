"""Self-hosted fonts and inline shop CSS without freezing future CSS edits."""
import json
import re
from pathlib import Path
from flask import abort, send_from_directory, url_for
from markupsafe import Markup, escape
from functools import lru_cache

@lru_cache(maxsize=8)
def _read_css(path, mtime, size):
    return Path(path).read_text(encoding='utf-8')


def install(app):
    if 'shop_performance_head' in app.jinja_env.globals:
        return
    assets = Path(app.root_path) / 'performance-fonts'
    config = json.loads((assets / 'fonts.json').read_text(encoding='utf-8'))
    names = {row['name'] for row in config['fonts'].values()}

    def serve_font(filename):
        if filename not in names:
            abort(404)
        response = send_from_directory(assets, filename, mimetype='font/woff2', conditional=True, max_age=31536000)
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        return response

    app.add_url_rule('/_shop-fonts/<filename>', endpoint='shop_performance_font', view_func=serve_font)

    def head():
        fonts_css = config['css']
        for original, row in config['fonts'].items():
            fonts_css = fonts_css.replace(original, url_for('shop_performance_font', filename=row['name']))
        output = []
        # Preload only the Cyrillic heading font that renders the Bulgarian LCP.
        match = re.search(r'/\* cyrillic \*/\s*(@font-face\s*\{[^}]+\})', config['css'])
        if match:
            font_url = re.search(r'url\(([^)]+)\)', match.group(1)).group(1)
            row = config['fonts'][font_url]
            output.append(Markup('<link rel="preload" href="{}" as="font" type="font/woff2" crossorigin>').format(url_for('shop_performance_font', filename=row['name'])))
        output.append(Markup('<style id="shop-local-fonts">') + Markup(fonts_css) + Markup('</style>'))
        path = Path(app.static_folder) / 'css/style.css'
        try:
            stat = path.stat()
            css = _read_css(str(path), stat.st_mtime_ns, stat.st_size)
            # Future styles with relative assets/imports keep normal CSS URL semantics.
            unsafe = re.search(r'</style|@import|@charset|url\s*\(', css, re.I)
            if not unsafe:
                output.append(Markup('<style id="shop-main-css">') + Markup(css) + Markup('</style>'))
            else:
                output.append(Markup('<link rel="stylesheet" href="{}">').format(url_for('static', filename='css/style.css', v=str(stat.st_mtime_ns))))
        except OSError:
            output.append(Markup('<link rel="stylesheet" href="{}">').format(url_for('static', filename='css/style.css')))
        return Markup('\n').join(output)
    app.jinja_env.globals['shop_performance_head'] = head
