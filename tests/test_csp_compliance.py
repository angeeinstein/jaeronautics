"""Templates must not rely on inline scripts the deployed CSP blocks.

The nginx config serves ``script-src 'self' https://js.stripe.com
https://cdn.jsdelivr.net/npm/`` with no ``'unsafe-inline'`` and no nonce, so an
inline <script> block is silently dropped by the browser in production while
still "working" in a Flask render test. This guard keeps the two apart.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "aeronautics_members" / "templates"
NGINX_CONF = REPO / "deploy" / "nginx" / "aeronautics.conf"

# <script> with no src= attribute, i.e. one carrying an inline body.
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.I)
# A style="" attribute, which style-src blocks just as it blocks inline scripts.
INLINE_STYLE = re.compile(r"<[^>]+\sstyle\s*=\s*[\"']", re.I)


def test_no_inline_scripts_in_templates():
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if INLINE_SCRIPT.search(line):
                offenders.append(f"{path.relative_to(TEMPLATES)}:{lineno}")

    assert not offenders, (
        "Inline <script> blocks are blocked by the production CSP; move the code "
        "into aeronautics_members/static/ and reference it with url_for('static', ...):\n"
        + "\n".join(offenders)
    )


def test_csp_does_not_permit_inline_scripts():
    # If this ever gains 'unsafe-inline', the guard above stops being meaningful,
    # so the two must be changed together deliberately.
    csp = next(
        line for line in NGINX_CONF.read_text().splitlines()
        if "Content-Security-Policy" in line
    )
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]


def test_no_inline_style_attributes_in_templates():
    """style="" is blocked by style-src for the same reason inline scripts are.

    It fails quietly: the element simply renders unstyled in production while
    looking correct in any local render test.
    """
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if INLINE_STYLE.search(line):
                offenders.append(f"{path.relative_to(TEMPLATES)}:{lineno}")

    assert not offenders, (
        "Inline style attributes are blocked by the production CSP; use a class "
        "in static/style.css instead:\n" + "\n".join(offenders)
    )


def test_csp_does_not_permit_inline_styles():
    csp = next(
        line for line in NGINX_CONF.read_text().splitlines()
        if "Content-Security-Policy" in line
    )
    assert "'unsafe-inline'" not in csp.split("style-src")[1].split(";")[0]


def test_hsts_is_sent():
    """Without HSTS a first visit can be downgraded before the redirect."""
    conf = NGINX_CONF.read_text()
    assert "Strict-Transport-Security" in conf, "the deployment should send HSTS"
    header = next(line for line in conf.splitlines() if "Strict-Transport-Security" in line)
    assert "max-age=" in header
    # preload is effectively irreversible; it should be a deliberate decision,
    # not something that arrives with a default config.
    assert "preload" not in header
