"""Membership coverage and access rules.

This module owns one question -- *does this person have membership access, and
for which period?* -- so that the answer cannot differ between the account page,
the forum handoff, a webhook and a nightly job. Keeping it in one place is what
stops a fix in one caller from contradicting another.

The association runs a single shared calendar-year cycle rather than per-member
anniversaries: everyone renews on Jan 1, which gives one invoice date for the
whole association. Someone joining before Oct 1 pays a prorated share of the
remaining year; from Oct 1 the rest of the year is free, because the prorated
remainder would be too small to charge (a few cents cannot be processed
sensibly) and new students arrive at the start of the academic year in October.

``ACTIVE_MEMBER_STATUSES`` deliberately includes ``canceled`` and
``cancel_scheduled``: someone who cancels has already paid through Dec 31 and
keeps access until the coverage they bought runs out.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from babel.dates import format_date
from flask_babel import get_locale

from .clock import (
    first_day_of_year,
    get_membership_today,
    last_day_of_year,
    start_of_day_unix,
    to_membership_date,
)

# Statuses that grant access while coverage is still current.
ACTIVE_MEMBER_STATUSES = {"paid", "free_period", "canceled", "cancel_scheduled"}
RESUMABLE_MEMBER_STATUSES = {"pending_checkout", "processing", "failed", "unpaid"}

# Statuses established by real evidence -- a paid invoice, a completed Checkout,
# or an explicitly granted free period -- rather than inferred from a Stripe
# subscription's lifecycle. Reconciliation may preserve these, but must never
# promote a member into one without such evidence.
PAYMENT_EVIDENCE_STATUSES = {"paid", "free_period"}

# Joining on or after this day gives the rest of the year free.
FREE_PERIOD_START_MONTH = 10
FREE_PERIOD_START_DAY = 1


def format_membership_date_display(value):
    locale = str(get_locale()) if get_locale() else None
    try:
        return format_date(value, format="long", locale=locale)
    except Exception:
        return value.isoformat()


def build_membership_cycle(join_date, annual_amount_cents):
    """Describe the coverage window and price for someone joining on ``join_date``."""
    current_year = join_date.year
    next_year_start = first_day_of_year(current_year + 1)
    current_year_end = last_day_of_year(current_year)
    total_days = (first_day_of_year(current_year + 1) - first_day_of_year(current_year)).days
    remaining_days = (current_year_end - join_date).days + 1
    free_period = join_date >= date(current_year, FREE_PERIOD_START_MONTH, FREE_PERIOD_START_DAY)
    prorated_amount_cents = 0
    if not free_period:
        prorated_amount_cents = int(
            (Decimal(annual_amount_cents) * Decimal(remaining_days) / Decimal(total_days)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )

    return {
        "join_date": join_date,
        "coverage_start": join_date,
        "coverage_end": current_year_end,
        "renewal_due_on": next_year_start,
        "trial_end_unix": start_of_day_unix(next_year_start),
        "trial_end_iso": next_year_start.isoformat(),
        "free_period": free_period,
        "prorated_amount_cents": prorated_amount_cents,
        "remaining_days": remaining_days,
        "total_days": total_days,
        "current_year": current_year,
        "thank_you_phase": "free_period" if free_period else "prorated",
    }


def member_has_active_access(member, on_date=None):
    """Whether the member may use member-only features right now.

    The coverage ledger decides when it has anything to say. A member with
    periods recorded has access exactly while one of them covers today, so a
    revoked period -- a lost chargeback, say -- really does remove access rather
    than being contradicted by a cached flag that nobody updated.

    Members with no periods at all fall back to the cached fields. That covers
    accounts predating the ledger, and it is why the health report counts
    members who have access with no record behind it: that count going above
    zero means some path grants coverage without saying why.

    Reads ``member.membership_periods`` directly rather than importing the
    periods service, which would make these two modules import each other.
    """
    if member is None:
        return False

    today = on_date or get_membership_today()

    recorded = member.membership_periods or []
    if recorded:
        # The ledger has authority as soon as it holds *any* record for this
        # member, including one that was revoked. Deciding on the unrevoked ones
        # alone would mean a member whose only coverage was revoked fell back to
        # the cached fields -- which still say "paid", and would hand back the
        # access the revocation was meant to take away.
        return any(
            p.revoked_at is None and p.starts_on <= today <= p.ends_on
            for p in recorded
        )

    if not member.membership_ends_on or member.membership_ends_on < today:
        return False
    return member.payment_status in ACTIVE_MEMBER_STATUSES or member.is_active


def sync_member_active_state(member, on_date=None):
    """Reconcile the cached ``is_active`` flag with the coverage dates."""
    if member is None:
        return False

    today = on_date or get_membership_today()
    changed = False

    if member.membership_ends_on and member.membership_ends_on < today and member.is_active:
        member.is_active = False
        changed = True
        if member.payment_status in ACTIVE_MEMBER_STATUSES:
            member.payment_status = "expired"
    elif (
        member.membership_ends_on
        and member.membership_ends_on >= today
        and member.payment_status in ACTIVE_MEMBER_STATUSES
        and not member.is_active
    ):
        member.is_active = True
        changed = True

    return changed


def set_member_membership_window(member, starts_on, ends_on, renewal_due_on, payment_status, is_active, cancel_at_period_end=False):
    member.membership_starts_on = starts_on
    member.membership_ends_on = ends_on
    member.renewal_due_on = renewal_due_on
    member.payment_status = payment_status
    member.is_active = is_active
    member.cancel_at_period_end = cancel_at_period_end


def invoice_coverage_year(invoice):
    """Membership-timezone year of a Stripe invoice's billing period start, or None.

    Renewal coverage follows the invoice's billing period rather than the payment
    timestamp, which can map to the wrong calendar day near midnight or the year
    boundary and so fail to advance coverage.
    """
    if not invoice:
        return None
    period_starts = [
        (line.get("period") or {}).get("start")
        for line in ((invoice.get("lines") or {}).get("data") or [])
        if (line.get("period") or {}).get("start")
    ]
    period_start = max(period_starts) if period_starts else invoice.get("period_start")
    if not period_start:
        return None
    return to_membership_date(period_start).year


def update_member_paid_coverage(member, paid_on, coverage_year=None):
    """Record that a payment covers a membership year, never shortening coverage."""
    if coverage_year is None:
        coverage_year = paid_on.year
        if member.membership_ends_on and member.membership_ends_on >= paid_on:
            coverage_year = member.membership_ends_on.year
        starts_on = member.membership_starts_on
        if starts_on is None or starts_on.year != coverage_year:
            starts_on = first_day_of_year(coverage_year) if paid_on == first_day_of_year(coverage_year) else paid_on
    else:
        # A full paid year always runs Jan 1 - Dec 31.
        starts_on = first_day_of_year(coverage_year)

    # Never regress coverage: an explicit (or derived) year must not move the
    # membership end date earlier than what the member already has.
    if member.membership_ends_on and coverage_year < member.membership_ends_on.year:
        coverage_year = member.membership_ends_on.year
        starts_on = member.membership_starts_on or first_day_of_year(coverage_year)

    set_member_membership_window(
        member,
        starts_on=starts_on,
        ends_on=last_day_of_year(coverage_year),
        renewal_due_on=first_day_of_year(coverage_year + 1),
        payment_status="paid",
        is_active=True,
        cancel_at_period_end=member.cancel_at_period_end,
    )
