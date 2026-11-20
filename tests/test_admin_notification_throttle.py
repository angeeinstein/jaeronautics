"""Admin emails: throttled per kind of event, not per channel.

Before, every error shared one throttle, so an error that kept coming back held
up a different, new one, and there were at most four error emails a day. Now:
the first and second of a kind go out at once -- the second is what shows it
recurs -- and after that they are summarised, further apart each time. Review
items wait a minute first, so photos uploaded together make one email.
"""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import db, make_member
from aeronautics_members import notification_service as ns
from aeronautics_members.db_models import ForumAvatarSubmission

@pytest.fixture
def clock(monkeypatch):
    # Started now, not at import: events are stamped with the real time.
    current = {"now": datetime.now(timezone.utc), "start": datetime.now(timezone.utc)}

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return current["now"]

    monkeypatch.setattr(ns, "datetime", FakeDateTime)
    return current


@pytest.fixture
def service(app, clock, monkeypatch):
    service = ns.NotificationService(app)
    sent = []
    monkeypatch.setattr(service, "get_admin_recipient_emails", lambda *_: ["admin@example.org"])

    def send(recipients, subject, template_vars):
        sent.append(sorted({e["summary"] for e in template_vars["events"]}))
        return True, None

    monkeypatch.setattr(service, "_send_admin_digest_mail", send)
    service.sent = sent
    return service


def _error(service, kind, text):
    service.queue_admin_error(kind, text)
    db.session.commit()
    service.deliver_pending_notifications(channels=[ns.ADMIN_ERROR_CHANNEL])


def _deliver(service, channel=ns.ADMIN_ERROR_CHANNEL):
    service.deliver_pending_notifications(channels=[channel])


def test_the_first_and_second_of_a_kind_go_out_at_once_then_they_are_collected(service, clock):
    _error(service, "forum_sync_failed", "one")
    clock["now"] += timedelta(minutes=5)
    _error(service, "forum_sync_failed", "two")
    clock["now"] += timedelta(minutes=5)
    _error(service, "forum_sync_failed", "three")
    _error(service, "forum_sync_failed", "four")

    assert service.sent == [["one"], ["two"]]

    clock["now"] = clock["start"] + timedelta(minutes=5 + 61)
    _deliver(service)

    assert service.sent[-1] == ["four", "three"]


def test_the_summaries_grow_further_apart(service, clock):
    for text in ("a", "b"):
        _error(service, "stripe_webhook_failed", text)
    sent_second = clock["now"]

    _error(service, "stripe_webhook_failed", "c")
    clock["now"] = sent_second + timedelta(minutes=61)
    _deliver(service)
    assert len(service.sent) == 3

    _error(service, "stripe_webhook_failed", "d")
    clock["now"] += timedelta(minutes=61)
    _deliver(service)
    assert len(service.sent) == 3, "now four hours"
    clock["now"] += timedelta(hours=3)
    _deliver(service)
    assert len(service.sent) == 4


def test_a_different_kind_is_not_held_up_by_one_that_recurs(service, clock):
    for text in ("a", "b", "c"):
        _error(service, "forum_sync_failed", text)

    _error(service, "stripe_webhook_failed", "new problem")

    assert service.sent[-1] == ["new problem"]


def test_a_kind_quiet_for_a_day_starts_afresh(service, clock):
    for text in ("a", "b"):
        _error(service, "forum_sync_failed", text)

    clock["now"] += timedelta(hours=25)
    _error(service, "forum_sync_failed", "back again")

    assert service.sent[-1] == ["back again"]


def test_photos_uploaded_together_make_one_email_after_a_minute(service, clock):
    for n in range(10):
        service.queue_admin_general("forum_avatar_uploaded", f"photo {n}")
    db.session.commit()
    _deliver(service, ns.ADMIN_GENERAL_CHANNEL)

    assert service.sent == [], "not while they are still coming in"

    clock["now"] += timedelta(seconds=61)
    _deliver(service, ns.ADMIN_GENERAL_CHANNEL)

    assert len(service.sent) == 1
    assert len(service.sent[0]) == 10


def test_errors_do_not_wait_the_minute(service, clock):
    _error(service, "forum_sync_failed", "right now")

    assert service.sent == [["right now"]]


def test_no_more_than_ten_admin_emails_an_hour(service, clock):
    for n in range(12):
        _error(service, f"kind_{n}", f"error {n}")

    assert len(service.sent) == 10

    clock["now"] += timedelta(minutes=61)
    _deliver(service)

    assert len(service.sent) == 11
    assert service.sent[-1] == ["error 10", "error 11"], "held over, not lost"


def test_a_photo_waiting_over_a_day_brings_a_daily_reminder(service, clock):
    START = clock["start"]
    member = make_member(email="waiting@example.com")
    db.session.add(ForumAvatarSubmission(
        user=member.user, member=member, status="pending",
        uploaded_at=START - timedelta(hours=25),
        storage_path="/tmp/none.png", content_type="image/png",
    ))
    db.session.commit()

    _deliver(service, ns.ADMIN_GENERAL_CHANNEL)
    clock["now"] += timedelta(seconds=61)
    _deliver(service, ns.ADMIN_GENERAL_CHANNEL)
    assert service.sent == [["1 item(s) waiting for more than a day."]]

    clock["now"] += timedelta(hours=2)
    _deliver(service, ns.ADMIN_GENERAL_CHANNEL)
    assert len(service.sent) == 1, "once a day, not every run"


class TestEachKindToWhoeverHandlesIt:
    """An admin email goes to whoever holds the permission of the page that shows the same
    (2026-11-20): no separate permission for being emailed."""

    def test_the_kinds_and_their_permissions(self):
        from aeronautics_members.permissions import Permission

        assert ns.permission_for_event("identity_change_request_created") == Permission.APPROVALS_REVIEW
        assert ns.permission_for_event("credit_payment_unmatched") == Permission.CREDIT_MANAGE
        assert ns.permission_for_event("team_payment_unmatched") == Permission.TEAMS_MONEY
        assert ns.permission_for_event("team_member_left") == Permission.TEAMS_MANAGE
        assert ns.permission_for_event("stripe_webhook_processing_failed") == Permission.ACCOUNTS_BILLING
        assert ns.permission_for_event("forum_sync_failed") == Permission.FORUM_MODERATE
        assert ns.permission_for_event("welcome_email_failed") == Permission.NOTIFICATIONS_MANAGE
        # A server error, or a kind nobody mapped: the super admins, who hold everything.
        assert ns.permission_for_event("internal_server_error") == Permission.SYSTEM_UPDATE
        assert ns.permission_for_event("something_new") == Permission.SYSTEM_UPDATE

    def test_each_person_gets_only_their_kinds(self, app, clock, monkeypatch):
        from aeronautics_members.permissions import Permission

        service = ns.NotificationService(app)
        holders = {Permission.FORUM_MODERATE: ["boss@example.org", "mod@example.org"],
                   Permission.SYSTEM_UPDATE: ["boss@example.org"]}
        monkeypatch.setattr(service, "get_admin_recipient_emails", lambda permission: holders.get(permission, []))
        sent = []
        monkeypatch.setattr(service, "_send_admin_digest_mail", lambda recipients, subject, template_vars: (
            sent.append((recipients, sorted(event["summary"] for event in template_vars["events"]))) or (True, None)))

        service.queue_admin_error("forum_sync_failed", "the forum")
        service.queue_admin_error("internal_server_error", "the server")
        db.session.commit()
        _deliver(service)

        assert sorted(sent) == [(["boss@example.org"], ["the forum", "the server"]),
                                (["mod@example.org"], ["the forum"])]
