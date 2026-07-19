"""Regression tests for renewal coverage handling (audit finding C1).

Subscription metadata is frozen at signup, so for a renewed member it carries
last year's coverage dates. Re-syncing from Stripe (which happens on every
account-page load) must NOT revert a paid member's coverage to the stale
signup-year window and expire them.
"""
from datetime import date

from conftest import app_module, db, make_member
from aeronautics_members.db_models import Member

CUR = date.today().year
PREV = CUR - 1


def _stale_subscription(activation_mode="paid_now", status="active"):
    """A Stripe subscription whose metadata still holds the signup (prev) year."""
    return {
        "id": "sub_1",
        "customer": "cus_1",
        "status": status,
        "cancel_at_period_end": False,
        "cancel_at": None,
        "metadata": {
            "membership_starts_on": f"{PREV}-06-01",
            "membership_ends_on": f"{PREV}-12-31",
            "renewal_due_on": f"{CUR}-01-01",
            "activation_mode": activation_mode,
        },
    }


def test_renewed_member_keeps_coverage_on_resync(app):
    # A member who joined last year and has since been renewed for this year.
    member = make_member(
        email="renewed@example.com",
        payment_status="paid",
        is_active=True,
        stripe_customer_id="cus_1",
        stripe_subscription_id="sub_1",
        membership_starts_on=date(CUR, 1, 1),
        membership_ends_on=date(CUR, 12, 31),
        renewal_due_on=date(CUR + 1, 1, 1),
    )

    app_module.sync_member_subscription_state_from_subscription(member, _stale_subscription())
    db.session.commit()

    refreshed = db.session.get(Member, member.id)
    # Coverage must NOT be dragged back to last year's stale metadata.
    assert refreshed.membership_ends_on == date(CUR, 12, 31)
    assert refreshed.is_active is True
    assert refreshed.payment_status == "paid"


def test_backfill_still_fills_missing_dates(app):
    # A member matched by Stripe reference but missing local coverage dates:
    # the backfill should still populate them from metadata.
    member = make_member(
        email="missing@example.com",
        payment_status="paid",
        is_active=True,
        stripe_customer_id="cus_1",
        stripe_subscription_id="sub_1",
        membership_starts_on=None,
        membership_ends_on=None,
        renewal_due_on=None,
    )
    # Use metadata pointing at the current year so it represents real coverage.
    sub = _stale_subscription()
    sub["metadata"]["membership_ends_on"] = f"{CUR}-12-31"
    sub["metadata"]["membership_starts_on"] = f"{CUR}-06-01"

    changed = app_module.backfill_member_coverage_from_subscription(member, sub)
    db.session.commit()

    assert changed is True
    assert member.membership_ends_on == date(CUR, 12, 31)


def test_backfill_never_regresses_end_date(app):
    member = make_member(
        email="noregress@example.com",
        membership_starts_on=date(CUR, 1, 1),
        membership_ends_on=date(CUR, 12, 31),
        renewal_due_on=date(CUR + 1, 1, 1),
    )
    # Metadata carries older (signup-year) dates; none of them must be applied.
    changed = app_module.backfill_member_coverage_from_subscription(member, _stale_subscription())
    assert changed is False
    assert member.membership_ends_on == date(CUR, 12, 31)
    assert member.membership_starts_on == date(CUR, 1, 1)
    assert member.renewal_due_on == date(CUR + 1, 1, 1)
