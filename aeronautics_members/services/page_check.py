"""Whether the portal's pages reach the browser with the security policy the
portal gave them -- and only that one.

The portal sets the Content-Security-Policy on every answer, with a fresh
nonce for the front end's ``<style>`` tags (content_security.py). Something
between it and the browser can add a second policy: an old nginx file from an
earlier installation did, on the live site, and a Cloudflare rule could. The
browser then applies both, the stricter one blocks the styles, and the site
looks broken -- with nothing in the portal saying why. So System health
fetches the start page through the public address, as a browser would, and
looks at the headers that arrive.

Asked at most every few minutes: the answer changes only when somebody
changes the server.
"""

import re
import time
import urllib.error
import urllib.request

from flask import current_app

CACHE_SECONDS = 300
TIMEOUT_SECONDS = 6

_cache = {}

_NONCE_META = re.compile(r'<meta\s+name="csp-nonce"\s+content="([^"]+)"', re.IGNORECASE)

WHERE_TO_LOOK = (
    "Look for a Content-Security-Policy in the nginx files (/etc/nginx/conf.d/ and "
    "/etc/nginx/sites-enabled/; only the portal's own may serve it) and in Cloudflare's "
    "Transform Rules. The update moves an old nginx file of the portal's aside by itself."
)


def _fetch(url):
    """``(policies, page)``: every Content-Security-Policy header, and the page."""
    request = urllib.request.Request(url, headers={"User-Agent": "jaeronautics-health-check"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 (our own address)
        policies = response.headers.get_all("Content-Security-Policy") or []
        page = response.read(200_000).decode("utf-8", errors="replace")
    return policies, page


def _style_sources(policy):
    for directive in policy.split(";"):
        parts = directive.split()
        if parts and parts[0].lower() == "style-src":
            return parts[1:]
    return None


def _judge(address, policies, page):
    found = _NONCE_META.search(page)
    nonce = found.group(1) if found else None
    result = {"address": address, "checked": True, "policies": len(policies), "warning": None, "note": None}
    if len(policies) > 1:
        result["warning"] = (
            f"The pages arrive with {len(policies)} security policies (Content-Security-Policy). "
            "Something between the portal and the browser adds one; the browser applies both, "
            "and the pages look broken. " + WHERE_TO_LOOK
        )
    elif not policies:
        result["warning"] = (
            "The pages arrive without a security policy (Content-Security-Policy): something "
            "between the portal and the browser removes it. " + WHERE_TO_LOOK
        )
    elif nonce is None or f"'nonce-{nonce}'" not in (_style_sources(policies[0]) or []):
        result["warning"] = (
            "The pages arrive with a security policy that is not the portal's own: it does not "
            "allow the page's styles, so the pages look broken. Something between the portal "
            "and the browser replaces it. " + WHERE_TO_LOOK
        )
    else:
        result["note"] = "The pages arrive with the portal's own security policy, and only that."
    return result


def check_security_policy():
    """What the start page arrives with, through the public address."""
    base = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    if not base:
        return {"address": None, "checked": False, "policies": None, "warning": None,
                "note": "Not checked: the portal has no public address set (PUBLIC_BASE_URL)."}
    address = f"{base}/"
    cached = _cache.get(address)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    try:
        policies, page = _fetch(address)
        result = _judge(address, policies, page)
    except (urllib.error.URLError, OSError, ValueError) as error:
        reason = getattr(error, "reason", None) or error
        result = {"address": address, "checked": False, "policies": None, "warning": None,
                  "note": f"Not checked: {address} could not be reached from the server ({reason})."}
    _cache[address] = (time.monotonic(), result)
    return result


def forget():
    """Ask again next time (tests, and after an update)."""
    _cache.clear()
