"""A slow Stripe or forum must slow a page down, not take the site with it.

Three web workers that each handled one request at a time, a forum call that
could wait 20 seconds and Stripe's own default of 80 seconds with two retries:
three people opening the forum while it was slow tied up every worker, and the
rest of the site stopped answering.
"""
from pathlib import Path
from urllib.error import URLError

import pytest
import stripe

from aeronautics_members import forum_service
from aeronautics_members.forum_service import DiscourseConnectProvider, ForumProviderError

REPO = Path(__file__).resolve().parents[1]


def test_the_web_server_runs_threads():
    install = (REPO / "install.sh").read_text()

    assert "--threads 4" in install
    assert "--timeout 60" in install


def test_stripe_calls_give_up_well_inside_the_web_server_timeout(app):
    client = stripe.default_http_client
    connect, read = client._timeout

    assert stripe.max_network_retries == 1
    # Worst case: two attempts, each connecting and waiting for an answer.
    assert (connect + read) * (1 + stripe.max_network_retries) < 60


def test_a_forum_call_from_a_page_waits_less_than_one_from_the_worker(app):
    with app.test_request_context("/forum"):
        on_a_page = forum_service._discourse_timeout()
    in_the_background = forum_service._discourse_timeout()

    assert on_a_page < in_the_background
    assert on_a_page <= 10


def test_a_forum_that_does_not_answer_is_an_ordinary_forum_error(app, monkeypatch):
    """So every caller's existing "the forum could not be reached" handling applies."""
    seen = {}

    def slow(request, timeout):
        seen["timeout"] = timeout
        raise URLError("timed out")

    monkeypatch.setattr(forum_service, "urlopen", slow)
    provider = DiscourseConnectProvider(forum_service.normalize_forum_settings({
        "forum_base_url": "https://forum.example", "discourse_api_key": "k",
        "discourse_api_username": "system",
    }))

    with app.test_request_context("/forum"):
        with pytest.raises(ForumProviderError, match="Could not reach Discourse"):
            provider._request("GET", "/about.json")

    assert seen["timeout"] == forum_service.DISCOURSE_TIMEOUT_PAGE
