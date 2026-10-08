"""Signups never paid for: told 7 days before, removed after 90.

Removed outright, not erased into an anonymous row, so years of abandoned
signups do not pile up as nameless leftovers. Only a bare signup: anything
more is left for an admin.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from conftest import app_module, db, make_member
from aeronautics_members.db_models import AuditLog, Member, MembershipPeriod, NotificationEvent, User
from aeronautics_members.services import unfinished_signups
from aeronautics_members.services.audit import log_audit_event

SIGNED_UP = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)


def _signup(email="anna@example.com", **fields):
    member = make_member(email, payment_status="pending_checkout", created_at=SIGNED_UP,
                         pending_checkout_started_at=SIGNED_UP, **fields)
    log_audit_event("members", "member_signup", target_user=member.user, target_member=member)
    db.session.commit()
    return member


def _day(days):
    return SIGNED_UP + timedelta(days=days)


def _notices():
    return db.session.query(NotificationEvent).filter_by(event_type="unfinished_signup_removal").all()


def test_left_alone_before_the_notice(app):
    _signup()

    assert unfinished_signups.clean_up(_day(82)) == {"noticed": 0, "removed": 0}


def test_told_seven_days_before_then_removed_with_everything_about_it(app):
    member = _signup()
    user_id, member_id = member.user.id, member.id

    assert unfinished_signups.clean_up(_day(83))["noticed"] == 1
    [notice] = _notices()
    assert notice.recipient_email == "anna@example.com" and notice.payload["removal_day"] == "10.04.2026"
    assert unfinished_signups.clean_up(_day(85)) == {"noticed": 0, "removed": 0}  # told once

    assert unfinished_signups.clean_up(_day(90))["removed"] == 1
    db.session.commit()

    assert db.session.get(Member, member_id) is None and db.session.get(User, user_id) is None
    assert not _notices()
    assert not db.session.query(AuditLog).filter_by(target_user_id=user_id).count()
    summary = db.session.query(AuditLog).filter_by(event_type="unfinished_signups_removed").one()
    assert summary.event_metadata == {"count": 1, "after_days": 90}


def test_never_removed_unwarned_when_the_job_missed_days(app):
    _signup()

    unfinished_signups.clean_up(_day(200))  # first run long after: told, not removed
    assert len(_notices()) == 1 and db.session.query(Member).count() == 1

    assert unfinished_signups.clean_up(_day(206))["removed"] == 0
    assert unfinished_signups.clean_up(_day(207))["removed"] == 1


def test_trying_to_pay_again_starts_the_count_afresh(app):
    member = _signup()
    unfinished_signups.clean_up(_day(83))
    member.pending_checkout_started_at = _day(88)
    db.session.commit()

    assert unfinished_signups.clean_up(_day(95)) == {"noticed": 0, "removed": 0}
    assert unfinished_signups.clean_up(_day(171))["noticed"] == 1  # a new notice for the new date


@pytest.mark.parametrize("change", ["paid", "processing", "subscription", "period", "role"])
def test_anything_more_than_a_bare_signup_is_left_alone(app, change):
    member = _signup()
    if change == "paid":
        member.payment_status, member.is_active = "paid", True
    elif change == "processing":
        member.payment_status = "processing"
    elif change == "subscription":
        member.stripe_subscription_id = "sub_1"
    elif change == "period":
        db.session.add(MembershipPeriod(member=member, starts_on=_day(0).date(), ends_on=_day(300).date(),
                                        reason=MembershipPeriod.REASON_ADMIN_GRANT))
    else:
        member.user.grant_role(app_module.get_role("admin"))
    db.session.commit()

    assert unfinished_signups.clean_up(_day(400)) == {"noticed": 0, "removed": 0}
    assert db.session.query(Member).count() == 1


def test_the_email(app):
    from aeronautics_members.services.notifications import get_notification_service

    _signup()
    unfinished_signups.clean_up(_day(83))
    [notice] = _notices()
    with app.test_request_context():
        subject, template_vars = get_notification_service()._build_user_status_message(notice)

    assert subject == "Your signup at Joanneum Aeronautics is not complete"
    assert any("10.04.2026" in (line or "") for line in template_vars["body_lines"])


def _rows_pointing_at(user_id, member_id):
    ids = {"users": user_id, "member": member_id}
    found = []
    for table in db.metadata.sorted_tables:
        for foreign_key in table.foreign_keys:
            target = foreign_key.column.table.name
            if target in ids and db.session.execute(
                db.select(foreign_key.parent).where(foreign_key.parent == ids[target]).limit(1)
            ).first():
                found.append(f"{table.name}.{foreign_key.parent.name}")
    return found


def test_a_real_signup_goes_without_a_trace_with_foreign_keys_enforced(app, client, monkeypatch):
    """As on the live database: a delete that leaves a row pointing at the
    account is refused there, though SQLite lets it pass unless asked."""
    from test_member_journey import TestTheSignupSentTwice

    from flask import g

    from aeronautics_members.services import signup as _signup
    from aeronautics_members.services.clock import get_now_utc

    def open_checkout(member):
        import types
        return types.SimpleNamespace(url=f"https://checkout.stripe.test/{member.id}"), {}

    monkeypatch.setattr(_signup, "create_checkout_session_for_member", open_checkout)
    monkeypatch.setattr(_signup, "send_email_verification_email", lambda *a, **k: True)
    monkeypatch.setattr(_signup, "send_work_email_verification_email", lambda *a, **k: True)
    client.post("/api/v1/signup", json=TestTheSignupSentTwice.FORM)
    client.delete("/api/v1/session")
    g.pop("_login_user", None)
    member = db.session.execute(db.select(Member).filter_by(email_private="dora@example.com")).scalar_one()
    user_id, member_id = member.user_id, member.id
    db.session.commit()
    own_logs = [log.id for log in db.session.query(AuditLog).filter(db.or_(
        AuditLog.target_user_id == user_id, AuditLog.actor_user_id == user_id,
        AuditLog.target_member_id == member_id))]
    assert own_logs  # the signup's own log entries, at least

    def enforce_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    engine = db.engine
    event.listen(engine, "connect", enforce_foreign_keys)
    db.session.remove()
    engine.dispose()
    try:
        assert db.session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        unfinished_signups.clean_up(get_now_utc() + timedelta(days=83))
        db.session.commit()
        assert unfinished_signups.clean_up(get_now_utc() + timedelta(days=91))["removed"] == 1
        db.session.commit()

        assert db.session.get(User, user_id) is None
        assert _rows_pointing_at(user_id, member_id) == []
        # Gone, not just unlinked: the database layer blanks a link rather than refusing.
        assert not db.session.query(AuditLog).filter(
            AuditLog.id.in_(own_logs), AuditLog.event_type != "unfinished_signups_removed").count()
    finally:
        db.session.remove()
        event.remove(engine, "connect", enforce_foreign_keys)
        engine.dispose()


def test_a_signup_something_else_points_at_is_left_alone(app):
    from aeronautics_members.db_models import TeamNote
    from test_teams_flow import _led

    member = _signup()
    team, lead = _led()
    db.session.add(TeamNote(team=team, user=member.user, author_user_id=lead.id, body="Met at the open day."))
    db.session.commit()

    assert unfinished_signups.clean_up(_day(400)) == {"noticed": 0, "removed": 0}


class TestTheOldForumsAccounts:
    """The archive of the old forum: never told, never removed, however old."""

    def test_an_imported_account_is_never_a_signup(self, app):
        from aeronautics_members.db_models import ImportedForumProfile
        from test_forum_account_claim import _archived

        profile = _archived()
        user_id = profile.user_id

        for days in (0, 400, 4000):
            assert unfinished_signups.clean_up(_day(days)) == {"noticed": 0, "removed": 0}
        assert db.session.get(User, user_id) is not None
        assert db.session.get(ImportedForumProfile, profile.id) is not None
        assert not _notices()

    def test_nor_one_reconnected_whose_membership_was_never_paid(self, app):
        from datetime import datetime as _datetime

        from aeronautics_members.services.forum_import import claim_archived_account
        from test_forum_account_claim import OLD_EMAIL, _archived

        _archived()
        member = _signup(email=OLD_EMAIL)
        member.user.email_verified_at = _datetime.utcnow()
        db.session.commit()
        claimed = claim_archived_account(member.user)
        db.session.commit()
        assert claimed is not None and claimed.user.member is not None  # the unpaid membership moved onto it

        for days in (100, 400):
            assert unfinished_signups.clean_up(_day(days)) == {"noticed": 0, "removed": 0}
        assert db.session.get(User, claimed.user_id).member is not None
