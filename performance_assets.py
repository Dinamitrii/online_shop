"""Self-hosted fonts and inline shop CSS without freezing future CSS edits."""
import json
import re
from pathlib import Path
from flask import abort, send_from_directory, url_for, request
from markupsafe import Markup, escape
from functools import lru_cache

def _minify_css(css):
    """Remove non-semantic CSS whitespace while preserving strings and calc()."""
    output = []
    index = 0
    quote = None
    escaped = False
    pending_space = False
    while index < len(css):
        char = css[index]
        if quote:
            output.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in ('"', "'"):
            if pending_space and output and output[-1] not in '{:;,>':
                output.append(' ')
            pending_space = False
            quote = char
            output.append(char)
            index += 1
            continue
        if char == '/' and index + 1 < len(css) and css[index + 1] == '*':
            end = css.find('*/', index + 2)
            if end == -1:
                break
            index = end + 2
            continue
        if char.isspace():
            pending_space = True
            index += 1
            continue
        removable = char in '{};,>'
        if pending_space and output and not removable and output[-1] not in '{:;,>':
            output.append(' ')
        pending_space = False
        if char == '}' and output and output[-1] == ';':
            output.pop()
        if removable and output and output[-1] == ' ':
            output.pop()
        output.append(char)
        index += 1
    return ''.join(output)


@lru_cache(maxsize=8)
def _read_css(path, mtime, size):
    return _minify_css(Path(path).read_text(encoding='utf-8'))


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
        if request.endpoint == 'index':
            # The homepage headings are Cyrillic; keep Bitter Latin punctuation
            # out of this page so it falls back to the system serif font.
            fonts_css = re.sub(
                r"/\* latin \*/\s*@font-face\s*\{(?=[^}]*font-family: ['\"]Bitter['\"])[^}]+\}\s*",
                '', fonts_css,
            )
        for original, row in config['fonts'].items():
            fonts_css = fonts_css.replace(original, url_for('shop_performance_font', filename=row['name']))
        output = []
        # Preload only the Cyrillic fonts used above the fold: heading and body.
        for family, weight in (('Bitter', '600'), ('PT Sans', '400')):
            pattern = (
                r'/\* cyrillic \*/\s*(@font-face\s*\{'
                r'(?=[^}]*font-family:\s*[^;]*' + re.escape(family) + r'[^;]*;)'
                r'(?=[^}]*font-weight:\s*' + weight + r';)[^}]+\})'
            )
            match = re.search(pattern, config['css'])
            if match:
                font_url = re.search(r'url\(([^)]+)\)', match.group(1)).group(1)
                row = config['fonts'][font_url]
                output.append(Markup('<link rel="preload" href="{}" as="font" type="font/woff2" crossorigin>').format(
                    url_for('shop_performance_font', filename=row['name'])))
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
