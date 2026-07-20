"""Regression tests for audit M5: forum active-state must be date-based.

The forum previously used the raw is_active flag, which can go stale, while the
site uses date-based coverage. They must agree.
"""
from datetime import date, timedelta

from conftest import db, make_member
from aeronautics_members.forum_service import member_has_active_membership

TODAY = date.today()


def test_expired_coverage_is_inactive_even_if_flag_true(app):
    # is_active is stale-True but coverage lapsed yesterday.
    member = make_member(
        email="lapsed@example.com",
        payment_status="paid",
        is_active=True,
        membership_ends_on=TODAY - timedelta(days=1),
    )
    db.session.commit()
    assert member_has_active_membership(member) is False


def test_valid_coverage_is_active_even_if_flag_false(app):
    # is_active is stale-False but coverage is still valid and paid.
    member = make_member(
        email="valid@example.com",
        payment_status="paid",
        is_active=False,
        membership_ends_on=date(TODAY.year, 12, 31),
    )
    db.session.commit()
    assert member_has_active_membership(member) is True


def test_none_member_is_inactive(app):
    assert member_has_active_membership(None) is False
