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


VENDOR = REPO / "aeronautics_members" / "static" / "vendor"


def test_no_external_asset_origins_in_templates():
    """Every script and stylesheet must come from this origin.

    A third-party CDN is a dependency that can change under you, needs its own
    CSP allowance, and leaks every visitor's address to whoever runs it. It is
    also what produced the source-map console errors: the browser asked the CDN
    for files the CSP would not let it fetch.
    """
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        # Email bodies are rendered by mail clients, not by the browser under
        # this CSP, and they legitimately link out.
        if "emails" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            # Assets only: a <script src>, an <img src>, or a stylesheet <link>.
            # A plain <a href> is a link the visitor chooses to follow.
            is_asset = re.search(r'\bsrc\s*=\s*["\']https?://', line, re.I) or (
                "<link" in line.lower()
                and re.search(r'\bhref\s*=\s*["\']https?://', line, re.I)
            )
            if is_asset:
                offenders.append(f"{path.relative_to(TEMPLATES)}:{lineno}: {line.strip()[:90]}")

    assert not offenders, (
        "Assets must be served from this origin; vendor them into static/ instead:\n  "
        + "\n  ".join(offenders)
    )


def test_vendored_assets_are_present():
    # The templates reference these by path, so a missing file is an unstyled
    # site rather than a test failure anywhere else.
    for name in ("bootstrap.min.css", "bootstrap.bundle.min.js"):
        asset = VENDOR / name
        assert asset.exists(), f"{name} is missing from static/vendor"
        assert asset.stat().st_size > 10_000, f"{name} looks truncated"


def test_vendored_assets_request_no_source_maps():
    """A source map reference is a request the CSP will refuse."""
    for asset in VENDOR.iterdir():
        assert "sourceMappingURL" not in asset.read_text(errors="replace"), (
            f"{asset.name} points at a source map that is not vendored"
        )


def test_csp_allows_only_this_origin_for_code():
    csp = next(
        line for line in NGINX_CONF.read_text().splitlines()
        if "Content-Security-Policy" in line
    )
    for directive in ("script-src", "style-src"):
        value = csp.split(directive)[1].split(";")[0]
        assert "http" not in value, f"{directive} still allows an external origin: {value.strip()}"
    # Defences that cost nothing once everything is same-origin.
    for directive in ("object-src 'none'", "base-uri 'self'", "frame-ancestors 'self'"):
        assert directive in csp, f"CSP is missing {directive}"
