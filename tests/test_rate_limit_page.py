"""What a rate-limited member actually sees.

The handler used to return ``redirect(...), 429``. Browsers do not follow a
Location header on a 429, so the flash message never appeared and the member
was left on an unstyled "Redirecting..." page -- observed on the live server
while signing up repeatedly during testing.
"""
import re
from pathlib import Path

from flask import render_template

from conftest import app_module

APP_SOURCE = (Path(__file__).resolve().parent.parent
              / "aeronautics_members" / "app.py").read_text()


def test_the_handler_renders_a_page_rather_than_redirecting():
    handler = re.search(
        r"def handle_rate_limit_error\(e\):(.*?)(?=\n    @app\.|\n    def )",
        APP_SOURCE, re.S,
    )
    assert handler, "rate limit handler not found"
    body = handler.group(1)
    assert "render_template" in body, "a 429 must carry a body; a redirect is not followed"
    assert "redirect(" not in body, "browsers ignore Location on 429"
    assert "429" in body


def test_the_page_explains_what_happened_and_what_to_do(app):
    with app.test_request_context("/"):
        html = render_template("429.html")

    text = re.sub(r"<[^>]+>", " ", html)
    assert "429" in text
    # It must say why, not just that something went wrong.
    assert "network" in text.lower() or "requests" in text.lower()
    # ...and what to do about it.
    assert "wait" in text.lower()
    # Shared-network members are the likely victims, so the page names that case.
    assert "wifi" in text.lower() or "network" in text.lower()


def test_signup_limits_allow_a_shared_campus_network():
    """The per-network limits are what a lecture hall behind one NAT shares.

    The tight limits count one email address from one network; see
    test_rate_limits.py for both working together.
    """
    from aeronautics_members.config import (
        RATELIMIT_LOGIN_PER_IP,
        RATELIMIT_MEMBERSHIP_PER_IP,
        RATELIMIT_REGISTER_PER_IP,
    )

    def count(value):
        match = re.match(r"\s*(\d+)\s*per", value)
        return int(match.group(1)) if match else None

    assert (count(RATELIMIT_MEMBERSHIP_PER_IP) or 0) >= 200, RATELIMIT_MEMBERSHIP_PER_IP
    assert (count(RATELIMIT_REGISTER_PER_IP) or 0) >= 200, RATELIMIT_REGISTER_PER_IP
    assert (count(RATELIMIT_LOGIN_PER_IP) or 0) >= 200, RATELIMIT_LOGIN_PER_IP


def test_the_app_still_registers_a_rate_limit_handler():
    assert "RateLimitExceeded" in APP_SOURCE
    assert app_module.limiter is not None
