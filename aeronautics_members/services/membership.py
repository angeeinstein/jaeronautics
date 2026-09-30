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

from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from ..config import MEMBERSHIP_TIMEZONE
from .clock import (
    datetime_to_membership_date,
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

# How long a renewal Stripe is still collecting keeps somebody a member past
# the end of their year. A SEPA debit takes days to confirm -- usually about
# five business days, sometimes longer -- and it starts on Jan 1, a holiday.
RENEWAL_GRACE_DAYS = 21

# Statuses under which a renewal can be in flight: Stripe has not said it
# failed, and nobody cancelled.
RENEWAL_IN_FLIGHT_STATUSES = {"paid", "processing"}

# Joining on or after this day gives the rest of the year free.
FREE_PERIOD_START_MONTH = 10
FREE_PERIOD_START_DAY = 1


def format_membership_date_display(value):
    """A date as members see it everywhere: 31.12.2026.

    One format across the portal, the emails and Stripe's payment page. It
    used to follow the language setting, which in English gave the American
    "December 31, 2026" -- while the account page showed 2026-12-31.
    """
    return value.strftime("%d.%m.%Y")


def format_date_display(value):
    """The calendar day of a date, or of a stored moment in Vienna: 31.12.2026."""
    if isinstance(value, datetime):
        value = datetime_to_membership_date(value)
    return format_membership_date_display(value)


def format_datetime_display(value):
    """A moment as the clock in Vienna showed it: 31.12.2026 14:05.

    Everything is stored in UTC and shown in the membership timezone -- where
    the university is and nearly every member lives -- including the summer
    time shift, so a time on a page matches the clock on the wall in Graz.
    Takes a datetime (naive ones are UTC, as everything stored is) or the ISO
    text the update runner writes.
    """
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(MEMBERSHIP_TIMEZONE).strftime("%d.%m.%Y %H:%M")


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


def has_payment_evidence(member):
    """Whether anything real stands behind this member's membership.

    A recorded period that was not revoked -- written only by a paid invoice, a
    paid Checkout, a free October period or an administrator -- or a status
    only those events set. What a subscription merely *looks* like is not
    evidence: a SEPA debit still being collected leaves the subscription
    "trialing" exactly as a paid one does, and a subscription cancelled for
    never being paid is "canceled" exactly like one cancelled after a paid year.
    """
    if member is None:
        return False
    if any(period.revoked_at is None for period in (member.membership_periods or [])):
        return True
    return member.payment_status in PAYMENT_EVIDENCE_STATUSES


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

    # An erased member keeps their coverage rows -- they are the accounting
    # record -- so the ledger below would still report them as covered. The
    # membership ended with the erasure regardless of what was paid for.
    if getattr(member, "deleted_at", None) is not None:
        return False

    today = on_date or get_membership_today()

    recorded = member.membership_periods or []
    if recorded:
        # The ledger has authority as soon as it holds *any* record for this
        # member, including one that was revoked. Deciding on the unrevoked ones
        # alone would mean a member whose only coverage was revoked fell back to
        # the cached fields -- which still say "paid", and would hand back the
        # access the revocation was meant to take away.
        if any(
            p.revoked_at is None and p.starts_on <= today <= p.ends_on
            for p in recorded
        ):
            return True
        return renewal_in_flight(member, today)

    if not member.membership_ends_on or member.membership_ends_on < today:
        return False
    return member.payment_status in ACTIVE_MEMBER_STATUSES or member.is_active


def renewal_in_flight(member, on_date=None):
    """Whether the member's year has ended while Stripe collects the next one.

    The ledger only records a year once its payment is confirmed, and a SEPA
    debit taken on Jan 1 is confirmed days later. Without this, everybody
    paying by SEPA stopped being a member at midnight and started again a week
    or so on: shut out of the forum, moved to its inactive group and back, for
    a renewal they had done nothing wrong with.

    Narrow on purpose. Only straight after a recorded year has ended, and not
    after a revoked one -- a lost chargeback must still end access. Only for a
    subscription still running and not set to cancel, and only once Stripe has
    renewed it: that moves the cached end of the membership into the new year.
    And only while nothing has gone wrong: a failed debit makes the status
    "failed", which ends this at once, as a declined card always did.
    """
    if member is None or getattr(member, "deleted_at", None) is not None:
        return False
    today = on_date or get_membership_today()
    if not member.stripe_subscription_id or member.cancel_at_period_end:
        return False
    if member.payment_status not in RENEWAL_IN_FLIGHT_STATUSES:
        return False
    if not member.membership_ends_on or member.membership_ends_on < today:
        return False  # Stripe has not renewed it (yet)

    recorded = member.membership_periods or []
    if not recorded:
        return False
    latest = max(recorded, key=lambda p: (p.ends_on, p.id or 0))
    if latest.revoked_at is not None:
        return False
    return latest.ends_on < today <= latest.ends_on + timedelta(days=RENEWAL_GRACE_DAYS)


def sync_member_active_state(member, on_date=None):
    """Reconcile the cached ``is_active`` flag with the coverage dates."""
    if member is None:
        return False

    # An erased member is never active again, whatever the dates say. Erasure
    # cancels the subscription, Stripe reports that back as
    # customer.subscription.deleted, and the handler sets payment_status to
    # "canceled" -- which is in ACTIVE_MEMBER_STATUSES, because a member who
    # cancels keeps the coverage they paid for. Without this guard the rule
    # below then flips is_active back on and the erasure appears to undo itself.
    if getattr(member, "deleted_at", None) is not None:
        if member.is_active:
            member.is_active = False
            return True
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
        # A renewal covers the whole year, Jan 1 - Dec 31.
        starts_on = first_day_of_year(coverage_year)
        # ...but a member's *first* year is prorated from the day they joined,
        # and the invoice they just paid says so. Overwriting that with Jan 1
        # would claim coverage for months they were not a member and did not pay
        # for, contradicting both the coverage ledger and the invoice itself.
        # Only widen to Jan 1 when the existing window is from an earlier year.
        existing_start = member.membership_starts_on
        if existing_start and existing_start.year == coverage_year and existing_start > starts_on:
            starts_on = existing_start

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
