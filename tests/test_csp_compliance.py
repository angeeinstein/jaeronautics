"""Templates must not rely on inline scripts or styles the deployed CSP blocks.

The portal's policy (aeronautics_members/content_security.py) allows
``script-src 'self'`` with no ``'unsafe-inline'``, and inline styles only in
``<style>`` tags carrying the answer's nonce -- which the new front end's
components use and no template does. An inline <script> block, a style=""
attribute or an onclick="" is silently dropped by the browser in production
while still "working" in a Flask render test. This guard keeps the two apart.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "aeronautics_members" / "templates"
EMAIL_TEMPLATES = TEMPLATES / "emails"
NGINX_CONF = REPO / "deploy" / "nginx" / "aeronautics.conf"

# <script> with no src= attribute, i.e. one carrying an inline body.
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.I)
# A style="" attribute, which style-src blocks just as it blocks inline scripts.
INLINE_STYLE = re.compile(r"<[^>]+\sstyle\s*=\s*[\"']", re.I)
# An onclick="" / oninput="" handler -- in HTML or passed to a form field in
# Jinja -- which script-src blocks too.
INLINE_HANDLER = re.compile(r"(?<![\w-])on[a-z]+\s*=\s*[\"']", re.I)


def _csp():
    """The pages' policy, as the portal sends it (with a stand-in nonce)."""
    from aeronautics_members.content_security import policy

    return policy("TESTNONCE")


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


def test_no_inline_event_handlers_in_templates():
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        if path.is_relative_to(EMAIL_TEMPLATES):
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if INLINE_HANDLER.search(line):
                offenders.append(f"{path.relative_to(TEMPLATES)}:{lineno}")

    assert not offenders, (
        "Inline event handlers are blocked by the production CSP and never run; "
        "attach the listener from a script in aeronautics_members/static/:\n"
        + "\n".join(offenders)
    )


def test_csp_does_not_permit_inline_scripts():
    # If this ever gains 'unsafe-inline', the guard above stops being meaningful,
    # so the two must be changed together deliberately.
    csp = _csp()
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]


def test_no_inline_style_attributes_in_templates():
    """style="" is blocked by style-src for the same reason inline scripts are.

    It fails quietly: the element simply renders unstyled in production while
    looking correct in any local render test.
    """
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        # Emails are exempt: no browser applies the site's CSP to them, and
        # inline styles are what mail clients -- Outlook, Gmail's apps -- do
        # honour. They are only ever rendered by send_mail, never served.
        if path.parent == EMAIL_TEMPLATES:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if INLINE_STYLE.search(line):
                offenders.append(f"{path.relative_to(TEMPLATES)}:{lineno}")

    assert not offenders, (
        "Inline style attributes are blocked by the production CSP; use a class "
        "in the page's stylesheet instead:\n" + "\n".join(offenders)
    )


def test_csp_does_not_permit_inline_styles():
    csp = _csp()
    style_src = csp.split("style-src")[1].split(";")[0]
    assert "'unsafe-inline'" not in style_src
    # Only <style> tags with the answer's nonce: style="" attributes stay blocked.
    assert style_src.split() == ["'self'", "'nonce-TESTNONCE'"]


def test_hsts_is_sent():
    """Without HSTS a first visit can be downgraded before the redirect."""
    conf = NGINX_CONF.read_text()
    assert "Strict-Transport-Security" in conf, "the deployment should send HSTS"
    header = next(line for line in conf.splitlines() if "Strict-Transport-Security" in line)
    assert "max-age=" in header
    # preload is effectively irreversible; it should be a deliberate decision,
    # not something that arrives with a default config.
    assert "preload" not in header


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


# Third-party origins the policy allows on purpose, each with the reason. Any
# origin not listed here fails the test below, so a CDN cannot creep back in
# without someone deciding to add it.
DELIBERATE_EXTERNAL_ORIGINS = {
    "https://static.cloudflareinsights.com": "Cloudflare Web Analytics beacon, injected by the tunnel",
    "https://cloudflareinsights.com": "where that beacon reports back",
}


def test_csp_allows_no_unrecorded_external_origin():
    csp = _csp()
    unexpected = []
    for directive in ("script-src", "style-src", "connect-src"):
        if directive not in csp:
            continue
        value = csp.split(directive)[1].split(";")[0]
        for token in value.split():
            if token.startswith("http") and token not in DELIBERATE_EXTERNAL_ORIGINS:
                unexpected.append(f"{directive}: {token}")

    assert not unexpected, (
        "CSP allows an external origin that is not recorded as deliberate:\n  "
        + "\n  ".join(unexpected)
        + "\nAdd it to DELIBERATE_EXTERNAL_ORIGINS with a reason, or serve it from this origin."
    )


def test_stylesheets_stay_same_origin():
    # Nothing needs a third-party stylesheet: the front end's are built into static/app.
    csp = _csp()
    assert "http" not in csp.split("style-src")[1].split(";")[0]
    # Defences that cost nothing once everything is same-origin.
    for directive in ("object-src 'none'", "base-uri 'self'", "frame-ancestors 'self'"):
        assert directive in csp, f"CSP is missing {directive}"


def test_email_templates_are_never_served_as_pages():
    """What makes their exemption above safe."""
    python = (REPO / "aeronautics_members").rglob("*.py")
    for path in python:
        if path.name == "mail_utils.py":
            continue
        assert 'render_template(f"emails/' not in path.read_text(), path
        assert "render_template('emails/" not in path.read_text(), path
        assert 'render_template("emails/' not in path.read_text(), path
