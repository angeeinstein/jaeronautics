"""The Content-Security-Policy has to let people reach Stripe.

Paying happens by submitting a form to this site, which answers 303 to
Stripe's hosted checkout. ``form-action`` governs where a form submission may
end up *including every redirect it follows*, so ``form-action 'self'`` alone
lets the server answer perfectly and has the browser refuse the last hop.

That is what happened on the real server: the app returned 303 every time, the
log showed nothing wrong, and the button simply did nothing. It would have
blocked every signup in October while looking like a front-end glitch.

These read the deployed nginx configuration rather than the application,
because that is where the header is set -- and there is no other test in this
suite that would notice it changing.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Where somebody is sent by submitting a form on this site.
STRIPE_FORM_TARGETS = ("https://checkout.stripe.com", "https://billing.stripe.com")

CONFIG_FILES = ("deploy/nginx/aeronautics.conf", "install.sh")


def _policies(text):
    return re.findall(r'Content-Security-Policy\s+"([^"]+)"', text)


def _directive(policy, name):
    for part in policy.split(";"):
        part = part.strip()
        if part.startswith(f"{name} "):
            return part
    return ""


@pytest.mark.parametrize("filename", CONFIG_FILES)
def test_every_policy_lets_a_form_reach_stripe(filename):
    text = (REPO_ROOT / filename).read_text(encoding="utf-8")
    policies = _policies(text)
    assert policies, f"no Content-Security-Policy found in {filename}"

    for policy in policies:
        form_action = _directive(policy, "form-action")
        assert form_action, f"{filename} sets no form-action at all"
        for target in STRIPE_FORM_TARGETS:
            assert target in form_action, (
                f"{filename} would block the redirect to {target}: {form_action}"
            )


@pytest.mark.parametrize("filename", CONFIG_FILES)
def test_the_policy_is_still_restrictive(filename):
    """Allowing Stripe must not turn into allowing everything."""
    text = (REPO_ROOT / filename).read_text(encoding="utf-8")

    for policy in _policies(text):
        form_action = _directive(policy, "form-action")
        assert "'self'" in form_action, "same-site posts must still be allowed"
        assert "*" not in form_action, "a wildcard would defeat the directive"
        assert "default-src 'self'" in policy
        assert "object-src 'none'" in policy
        assert "frame-ancestors 'self'" in policy


def test_the_installer_and_the_deploy_file_agree():
    """Two copies of one policy drift, and only one of them is deployed."""
    seen = set()
    for filename in CONFIG_FILES:
        text = (REPO_ROOT / filename).read_text(encoding="utf-8")
        for policy in _policies(text):
            seen.add(_directive(policy, "form-action"))

    assert len(seen) == 1, f"form-action differs between copies: {seen}"
