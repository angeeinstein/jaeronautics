"""The Content-Security-Policy of every answer the portal gives.

It used to be a fixed header added by nginx. The new front end's components
write their CSS variables into ``<style>`` tags, and a fixed policy can only
allow those by allowing every inline style -- exactly what the policy is
there to forbid. So the portal sets it per answer, with a fresh nonce: a
``<style>`` tag carrying the nonce is allowed, nothing else inline is.

Otherwise it is as strict as before: scripts and styles from this site only
(and Cloudflare's analytics beacon, which the tunnel injects), no plugins,
nobody else may frame the portal, and forms go only to this site and Stripe
-- including the redirect a form submission follows, which is how paying
reaches Stripe's checkout (tests/test_csp_allows_payment.py).

PDFs (the legal texts) go without: Chrome shows them in a viewer plugin,
which ``object-src 'none'`` would refuse.
"""

import secrets

from flask import request

#: The policy, directive by directive. ``{nonce}`` is the answer's nonce.
DIRECTIVES = (
    ("default-src", "'self'"),
    ("script-src", "'self' https://static.cloudflareinsights.com"),
    ("style-src", "'self' {nonce}"),
    ("img-src", "'self' data:"),
    ("connect-src", "'self' https://cloudflareinsights.com"),
    ("object-src", "'none'"),
    ("base-uri", "'self'"),
    ("form-action", "'self' https://checkout.stripe.com https://billing.stripe.com"),
    ("frame-ancestors", "'self'"),
    ("frame-src", "'none'"),
)

EXEMPT_MIMETYPES = frozenset({"application/pdf"})


def nonce():
    """This answer's nonce: random, new for every request, the same within one.

    Kept on the request itself, not on ``g``, which can outlive a request
    (an application context held open around several, as the tests do).
    """
    return request.environ.setdefault("aeronautics.csp_nonce", secrets.token_urlsafe(18))


def policy(nonce_value):
    source = "'nonce-" + nonce_value + "'"
    return " ".join(f"{name} {value.format(nonce=source)};" for name, value in DIRECTIVES)


def apply(response):
    """Give ``response`` the policy, unless it is a PDF."""
    if response.mimetype not in EXEMPT_MIMETYPES:
        response.headers["Content-Security-Policy"] = policy(nonce())
    return response
