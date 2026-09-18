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
