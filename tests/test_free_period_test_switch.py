"""The test switch for the free period.

From 1 October every new signup is free, so nothing is charged at signup and
payment problems cannot be tried out. TEST_FREE_PERIOD_START moves the start
for testing; unset -- as on the live portal -- it is 1 October.
"""
from datetime import date

from aeronautics_members.services import membership


def test_without_the_switch_october_is_free(monkeypatch):
    monkeypatch.delenv("TEST_FREE_PERIOD_START", raising=False)

    assert membership.build_membership_cycle(date(2026, 10, 1), 3000)["free_period"] is True
    assert membership.build_membership_cycle(date(2026, 9, 30), 3000)["free_period"] is False


def test_the_switch_makes_october_signups_pay_again(monkeypatch):
    monkeypatch.setenv("TEST_FREE_PERIOD_START", "11-01")

    cycle = membership.build_membership_cycle(date(2026, 10, 1), 3000)

    assert cycle["free_period"] is False
    assert cycle["prorated_amount_cents"] > 0
    assert membership.build_membership_cycle(date(2026, 11, 1), 3000)["free_period"] is True


def test_nonsense_in_the_switch_is_ignored(monkeypatch):
    monkeypatch.setenv("TEST_FREE_PERIOD_START", "banana")

    assert membership.build_membership_cycle(date(2026, 10, 1), 3000)["free_period"] is True


def test_the_admin_pages_warn_while_it_is_set(app, client, monkeypatch):
    from test_admin_reviews import _login, _staff

    monkeypatch.setenv("TEST_FREE_PERIOD_START", "11-01")
    _login(client, _staff("boss@example.org", "admin").id)

    [notice] = client.get("/api/v1/me").get_json()["admin_notices"]

    assert notice["tone"] == "danger"
    assert "Test setting active" in notice["message"] and "01.11." in notice["message"]
