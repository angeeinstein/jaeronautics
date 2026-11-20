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

    def test_an_email_that_could_not_be_sent_goes_to_who_manages_email(self):
        # Codex on PR #87: these fell through to the super admins.
        from aeronautics_members.permissions import Permission

        for kind in ("verification_email_failed", "work_verification_email_failed",
                     "password_reset_email_failed", "account_deletion_email_failed",
                     "account_action_email_failed"):
            assert ns.permission_for_event(kind) == Permission.NOTIFICATIONS_MANAGE, kind
        assert ns.permission_for_event("team_dispute_unmatched") == Permission.TEAMS_MONEY

    def test_every_kind_the_code_sends_has_somebody_besides_the_super_admins(self):
        # Every literal kind handed to an admin email in the code; only a server error is meant
        # for the super admins alone.
        import pathlib
        import re

        from aeronautics_members.permissions import Permission

        patterns = (
            r"queue_curated_admin_notification\(\s*[A-Z_]+,\s*\"([a-z_]+)\"",
            r"queue_admin_(?:general|error)\(\s*\"([a-z_]+)\"",
            r"failure_event_type=\"([a-z_]+)\"",
            r"_tell_admins\(\s*\"([a-z_]+)\"",
            r"_tell_leads\(\s*[a-z_.]+,\s*\"([a-z_]+)\"",
            r"event_type = \"([a-z_]+)\"",
        )
        root = pathlib.Path(ns.__file__).parent
        kinds = {kind for path in root.rglob("*.py") for pattern in patterns
                 for kind in re.findall(pattern, path.read_text(encoding="utf-8"))}
        assert {"verification_email_failed", "credit_payment_unmatched", "forum_sync_failed"} <= kinds
        left = sorted(kind for kind in kinds if ns.permission_for_event(kind) == Permission.SYSTEM_UPDATE)
        assert left == ["internal_server_error"]

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

    def test_a_digest_that_failed_is_sent_again_to_its_own_only(self, app, clock, monkeypatch):
        # Codex on PR #87: marked sent after the boss's digest went, the forum event never reached
        # the moderator, whose digest had failed.
        from aeronautics_members.db_models import NotificationEvent
        from aeronautics_members.permissions import Permission

        service = ns.NotificationService(app)
        holders = {Permission.FORUM_MODERATE: ["boss@example.org", "mod@example.org"],
                   Permission.SYSTEM_UPDATE: ["boss@example.org"]}
        monkeypatch.setattr(service, "get_admin_recipient_emails", lambda permission: holders.get(permission, []))
        sent, down = [], {"mod@example.org"}

        def send(recipients, subject, template_vars):
            if set(recipients) & down:
                return False, "the mail server refused"
            sent.append((recipients, sorted(event["summary"] for event in template_vars["events"])))
            return True, None

        monkeypatch.setattr(service, "_send_admin_digest_mail", send)
        service.queue_admin_error("forum_sync_failed", "the forum")
        service.queue_admin_error("internal_server_error", "the server")
        db.session.commit()

        _deliver(service)
        forum = db.session.query(NotificationEvent).filter_by(event_type="forum_sync_failed").one()
        assert sent == [(["boss@example.org"], ["the forum", "the server"])] and forum.status == "pending"

        down.clear()
        clock["now"] += timedelta(hours=6)
        _deliver(service)

        assert sent[1:] == [(["mod@example.org"], ["the forum"])]
        assert forum.status == "sent" and forum.delivered_to == ["boss@example.org", "mod@example.org"]

    def test_a_kind_delivered_counts_for_its_throttle_when_another_digest_failed(self, app, clock, monkeypatch):
        # Codex on PR #87: the forum digest went, the server one failed, and the forum kind's
        # throttle never moved -- its events were done and never came back to count.
        from aeronautics_members.db_models import NotificationChannelState
        from aeronautics_members.permissions import Permission

        service = ns.NotificationService(app)
        holders = {Permission.FORUM_MODERATE: ["mod@example.org"], Permission.SYSTEM_UPDATE: ["boss@example.org"]}
        monkeypatch.setattr(service, "get_admin_recipient_emails", lambda permission: holders.get(permission, []))
        monkeypatch.setattr(service, "_send_admin_digest_mail", lambda recipients, subject, template_vars: (
            (False, "the mail server refused") if recipients == ["boss@example.org"] else (True, None)))
        service.queue_admin_error("forum_sync_failed", "the forum")
        service.queue_admin_error("internal_server_error", "the server")
        db.session.commit()

        _deliver(service)

        def stage(kind):
            state = db.session.get(NotificationChannelState, service._kind_key(ns.ADMIN_ERROR_CHANNEL, kind))
            return state.cooldown_stage if state is not None else 0

        assert stage("forum_sync_failed") == 1
        assert stage("internal_server_error") == 0  # not delivered: it does not count yet

    def _routed(self, app, monkeypatch, holders):
        service = ns.NotificationService(app)
        monkeypatch.setattr(service, "get_admin_recipient_emails", lambda permission: holders.get(permission, []))
        sent = []
        monkeypatch.setattr(service, "_send_admin_digest_mail", lambda recipients, subject, template_vars: (
            sent.append((recipients, sorted(event["summary"] for event in template_vars["events"]))) or (True, None)))
        return service, sent

    def test_a_kind_nobody_can_receive_yet_waits_for_somebody(self, app, clock, monkeypatch):
        # Codex on PR #87: marked sent with nobody to receive it, it never reached whoever came to hold it.
        from aeronautics_members.db_models import NotificationEvent
        from aeronautics_members.permissions import Permission

        holders = {Permission.SYSTEM_UPDATE: ["boss@example.org"]}
        service, sent = self._routed(app, monkeypatch, holders)
        service.queue_admin_error("forum_sync_failed", "the forum")
        service.queue_admin_error("internal_server_error", "the server")
        db.session.commit()

        _deliver(service)
        forum = db.session.query(NotificationEvent).filter_by(event_type="forum_sync_failed").one()
        assert sent == [(["boss@example.org"], ["the server"])] and forum.status == "pending"

        holders[Permission.FORUM_MODERATE] = ["mod@example.org"]
        clock["now"] += timedelta(hours=6)
        _deliver(service)

        assert sent[1:] == [(["mod@example.org"], ["the forum"])] and forum.status == "sent"

    def test_the_hourly_limit_holds_across_the_digests_of_one_run(self, app, clock, monkeypatch):
        # Codex on PR #87: one run sends a digest per group of recipients; the limit was asked once.
        from aeronautics_members.db_models import NotificationBatch
        from aeronautics_members.permissions import Permission

        service, sent = self._routed(app, monkeypatch, {
            Permission.FORUM_MODERATE: ["mod@example.org"], Permission.SYSTEM_UPDATE: ["boss@example.org"]})
        for _ in range(ns.ADMIN_EMAILS_PER_HOUR - 1):
            db.session.add(NotificationBatch(channel=ns.ADMIN_ERROR_CHANNEL, status="sent", recipient_scope="x",
                                             recipient_count=1, event_count=1, sent_at=clock["now"]))
        service.queue_admin_error("forum_sync_failed", "the forum")
        service.queue_admin_error("internal_server_error", "the server")
        db.session.commit()

        _deliver(service)

        assert len(sent) == 1
        clock["now"] += timedelta(hours=2)
        _deliver(service)
        assert len(sent) == 2
