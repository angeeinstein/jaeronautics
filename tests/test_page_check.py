"""System health looks at what the start page arrives with (services/page_check.py).

On the live site an old nginx file added a second, fixed security policy; the
browser applied both and the pages looked broken, with nothing in the portal
saying why.
"""
import urllib.error

import pytest

from aeronautics_members.services import diagnostics, page_check

PAGE = '<html><head><meta name="csp-nonce" content="abc123" /></head></html>'
OURS = "default-src 'self'; style-src 'self' 'nonce-abc123'; object-src 'none';"


@pytest.fixture
def answer(app, monkeypatch):
    """What the public address answers: ``answer.policies``, or ``answer.error``."""
    class Answer:
        policies = [OURS]
        error = None
        asked = 0

    def fetch(url):
        Answer.asked += 1
        if Answer.error:
            raise Answer.error
        return Answer.policies, PAGE

    monkeypatch.setattr(page_check, "_fetch", fetch)
    app.config["HEALTH_PAGE_CHECK"] = True
    page_check.forget()
    yield Answer
    page_check.forget()


def test_the_portals_own_policy_and_only_that(app, answer):
    health = diagnostics.collect_system_health()

    assert health["pages"]["policies"] == 1 and health["pages"]["warning"] is None
    assert "only that" in health["pages"]["note"]
    assert health["warnings"] == []


def test_two_policies_are_a_warning_that_says_where_to_look(app, answer):
    answer.policies = [OURS, "default-src 'self'; style-src 'self';"]

    warnings = diagnostics.collect_system_health()["warnings"]

    assert len(warnings) == 1
    assert "2 security policies" in warnings[0] and "/etc/nginx/conf.d/" in warnings[0]


def test_a_policy_without_the_pages_nonce_is_one_too(app, answer):
    answer.policies = ["default-src 'self'; style-src 'self';"]

    assert "not the portal's own" in diagnostics.collect_system_health()["warnings"][0]


def test_none_at_all(app, answer):
    answer.policies = []

    assert "without a security policy" in diagnostics.collect_system_health()["warnings"][0]


def test_out_of_reach_is_said_but_not_a_warning(app, answer):
    answer.error = urllib.error.URLError("timed out")

    health = diagnostics.collect_system_health()

    assert health["warnings"] == [] and health["pages"]["checked"] is False
    assert "could not be reached" in health["pages"]["note"]


def test_asked_once_every_few_minutes(app, answer):
    diagnostics.collect_system_health()
    diagnostics.collect_system_health()

    assert answer.asked == 1


def test_the_dashboard_does_not_ask(app, answer):
    diagnostics.collect_system_health(check_pages=False)

    assert answer.asked == 0


def test_the_settings_page_shows_it(app, answer, client):
    from api_helpers import signed_in
    from test_admin_reviews import _staff

    body = signed_in(client, _staff("boss@example.org", "superadmin")).get("/api/v1/admin/settings/health").get_json()

    assert body["pages"]["policies"] == 1 and body["pages"]["checked"] is True
