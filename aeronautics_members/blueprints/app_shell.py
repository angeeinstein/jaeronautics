"""Handing the new front end its pages.

The front end (``frontend/``) is built into ``static/app/``: one
``index.html`` and its hashed scripts and styles, which nginx serves under
``/static/``. Every address the app draws (``frontend/src/app/paths.json``)
is answered here with that ``index.html``; the browser loads the app, and the
app draws the page. The Flask routes for those addresses stay where they are,
with their checks (signed in, permissions) and their endpoint names, so
``url_for`` and every link already sent out keep working -- only what they
answer changes, to ``app_shell()``.

The page's nonce for the security policy (content_security.py) goes into
``<meta name="csp-nonce">``, where the app's style tags read it.
"""

from pathlib import Path

from flask import current_app, make_response

from ..content_security import nonce

_PLACEHOLDER = "__CSP_NONCE__"
_cache = {}


def frontend_dir():
    configured = current_app.config.get("FRONTEND_DIST_DIR")
    return Path(configured) if configured else Path(current_app.static_folder) / "app"


def _index_html():
    """The built index.html, read once per build (re-read when the file changes)."""
    path = frontend_dir() / "index.html"
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _cache:
        _cache.clear()
        _cache[key] = path.read_text(encoding="utf-8")
    return _cache[key]


def app_shell():
    """The app's page for the address asked for."""
    try:
        html = _index_html()
    except FileNotFoundError:
        current_app.logger.error("The front end is not built: %s is missing.", frontend_dir() / "index.html")
        response = make_response(
            "<!doctype html><title>Not built</title><p>The portal's front end has not been built yet. "
            "Run the update again, or <code>npm run build</code> in <code>frontend/</code>.</p>",
            503,
        )
        response.headers["Retry-After"] = "60"
        return response
    response = make_response(html.replace(_PLACEHOLDER, nonce()))
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    return response
