"""Small security helpers shared by app.py and courier.py."""
import secrets
from urllib.parse import urlsplit


def safe_equal(left, right):
    """Constant-time comparison that also accepts non-ASCII text.

    secrets.compare_digest() raises TypeError for non-ASCII str arguments, which
    would turn a bad token or password into a 500 error.
    """
    return secrets.compare_digest(str(left or '').encode('utf-8'),
                                  str(right or '').encode('utf-8'))


def safe_next_url(url, default):
    """Allow only local absolute paths as a post-login redirect target."""
    if not isinstance(url, str) or not url.startswith('/') or url.startswith('//'):
        return default
    if '\\' in url or any(ord(char) < 32 for char in url):
        return default
    parts = urlsplit(url)
    if parts.scheme or parts.netloc:
        return default
    return url
