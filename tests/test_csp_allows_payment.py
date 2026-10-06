"""The Content-Security-Policy: where it comes from, and that it lets people reach Stripe.

Paying happens by submitting a form to this site, which answers 303 to
Stripe's hosted checkout. ``form-action`` governs where a form submission may
end up *including every redirect it follows*, so ``form-action 'self'`` alone
lets the server answer perfectly and has the browser refuse the last hop.

That is what happened on the real server: the app returned 303 every time, the
log showed nothing wrong, and the button simply did nothing. It would have
blocked every signup in October while looking like a front-end glitch.

The policy is set by the portal on every answer (content_security.py), with a
fresh nonce for the new front end's style tags. nginx sets none: a second,
fixed policy would apply as well and block what the first allows.
"""
import re
from pathlib import Path

import pytest

from aeronautics_members import content_security

REPO_ROOT = Path(__file__).resolve().parent.parent

# Where somebody is sent by submitting a form on this site.
STRIPE_FORM_TARGETS = ("https://checkout.stripe.com", "https://billing.stripe.com")


def _directive(policy, name):
    for part in policy.split(";"):
        part = part.strip()
        if part.startswith(f"{name} "):
            return part
    return ""


def _policy(response):
    return response.headers.get("Content-Security-Policy")


def test_a_form_may_reach_stripe():
    form_action = _directive(content_security.policy("n"), "form-action")

    for target in STRIPE_FORM_TARGETS:
        assert target in form_action, f"the policy would block the redirect to {target}: {form_action}"


def test_the_policy_is_still_restrictive():
    """Allowing Stripe must not turn into allowing everything."""
    policy = content_security.policy("n")
    form_action = _directive(policy, "form-action")

    assert "'self'" in form_action, "same-site posts must still be allowed"
    assert "*" not in form_action, "a wildcard would defeat the directive"
    for directive in ("default-src 'self'", "object-src 'none'", "frame-ancestors 'self'", "base-uri 'self'"):
        assert directive in policy
    assert "'unsafe-inline'" not in policy and "'unsafe-eval'" not in policy


@pytest.mark.parametrize("path", ["/", "/login", "/api/v1/session", "/nothing-here"])
def test_every_answer_carries_it(client, path):
    assert _directive(_policy(client.get(path)), "form-action")


def test_a_fresh_nonce_for_every_answer(client):
    first, second = (_directive(_policy(client.get("/login")), "style-src") for _ in range(2))

    nonces = [re.search(r"'nonce-([^']+)'", value).group(1) for value in (first, second)]
    assert nonces[0] != nonces[1] and len(nonces[0]) >= 16


def test_pdfs_go_without(app):
    """Chrome's PDF viewer is a plugin object-src 'none' forbids."""
    with app.test_request_context():
        response = app.response_class(b"%PDF-1.7", mimetype="application/pdf")
        assert _policy(content_security.apply(response)) is None


@pytest.mark.parametrize("filename", ["deploy/nginx/aeronautics.conf", "install.sh"])
def test_nginx_sets_none_of_its_own(filename):
    text = (REPO_ROOT / filename).read_text(encoding="utf-8")

    assert not re.search(r"^\s*add_header Content-Security-Policy", text, re.M)
    assert "default-src" not in text
