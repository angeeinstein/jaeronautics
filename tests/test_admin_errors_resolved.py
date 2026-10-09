"""Admin errors that resolved themselves are not emailed.

Seen live (2026-10-09): the forum in read-only mode for a backup made forum
syncs fail; the first two errors went out at once, the rest an hour later as
a summary -- after the forum was back and the accounts synced. Now a held-back
error is checked first, per kind, and left out (marked resolved) when its
problem has gone away.
"""
from datetime import timedelta

from conftest import db, make_member
from aeronautics_members import notification_service as ns
from aeronautics_members.db_models import EmailDeliveryJob, ForumAccount, NotificationEvent
from test_admin_notification_throttle import clock, service  # noqa: F401 -- fixtures


def _account(error="Discourse API request failed (503): read only"):
    member = make_member(email="sync@example.org")
    account = ForumAccount(user_id=member.user_id, member_id=member.id, external_id="x1", last_error=error)
    db.session.add(account)
    db.session.commit()
    return member, account


def _sync_failed(service, account, text):
    service.queue_admin_error("forum_sync_failed", text, object_type="forum_account", object_id=account.id)
    db.session.commit()
    service.deliver_pending_notifications(channels=[ns.ADMIN_ERROR_CHANNEL])


def test_a_forum_sync_that_went_through_since_is_left_out(service, clock):
    _member, account = _account()
    for text in ("one", "two", "three", "four"):
        _sync_failed(service, account, text)
    assert service.sent == [["one"], ["two"]]

    account.last_error = None  # synced again
    db.session.commit()
    clock["now"] += timedelta(minutes=61)
    service.deliver_pending_notifications(channels=[ns.ADMIN_ERROR_CHANNEL])

    assert service.sent == [["one"], ["two"]], "no summary for what has gone away"
    statuses = sorted(e.status for e in db.session.query(NotificationEvent).all())
    assert statuses == ["resolved", "resolved", "sent", "sent"]


def test_a_problem_still_there_is_sent_and_says_how_many_went(service, clock, monkeypatch):
    _member, account = _account()
    for text in ("one", "two", "three"):
        _sync_failed(service, account, text)
    service.queue_admin_error("forum_sync_failed", "stuck", object_type="forum_account", object_id=None)
    db.session.commit()
    seen = {}
    original = service._send_admin_digest_mail

    def send(recipients, subject, template_vars):
        seen.update(template_vars)
        return original(recipients, subject, template_vars)

    monkeypatch.setattr(service, "_send_admin_digest_mail", send)
    account.last_error = None
    db.session.commit()
    clock["now"] += timedelta(minutes=61)
    service.deliver_pending_notifications(channels=[ns.ADMIN_ERROR_CHANNEL])

    assert service.sent[-1] == ["stuck"]
    assert seen["resolved_count"] == 1


def test_a_welcome_email_sent_after_all_is_left_out(service, clock):
    member = make_member(email="welcome@example.org")
    for text in ("one", "two", "three"):
        service.queue_admin_error("welcome_email_failed", text, target_member=member)
        db.session.commit()
        service.deliver_pending_notifications(channels=[ns.ADMIN_ERROR_CHANNEL])
    db.session.add(EmailDeliveryJob(email_type="welcome_email", target_member_id=member.id, status="sent",
                                    sent_at=clock["now"] + timedelta(minutes=1)))
    db.session.commit()
    clock["now"] += timedelta(minutes=61)
    service.deliver_pending_notifications(channels=[ns.ADMIN_ERROR_CHANNEL])

    assert service.sent == [["one"], ["two"]]


def test_a_kind_without_a_check_is_sent_as_before(service, clock):
    for text in ("a", "b", "c"):
        service.queue_admin_error("billing_reconcile_failed", text)
        db.session.commit()
        service.deliver_pending_notifications(channels=[ns.ADMIN_ERROR_CHANNEL])
    clock["now"] += timedelta(minutes=61)
    service.deliver_pending_notifications(channels=[ns.ADMIN_ERROR_CHANNEL])

    assert service.sent[-1] == ["c"]


def test_a_check_that_fails_does_not_stop_the_email(service, clock, monkeypatch):
    def broken(event):
        raise RuntimeError("boom")

    monkeypatch.setitem(ns.RESOLVED_WHEN, "forum_sync_failed", broken)
    _member, account = _account()
    _sync_failed(service, account, "one")
    assert service.sent == [["one"]]
