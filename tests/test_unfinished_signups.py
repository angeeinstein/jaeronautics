"""Signups never paid for: told 7 days before, removed after 90.

Removed outright, not erased into an anonymous row, so years of abandoned
signups do not pile up as nameless leftovers. Only a bare signup: anything
more is left for an admin.
"""
from datetime import datetime, timedelta, timezone

import pytest

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
