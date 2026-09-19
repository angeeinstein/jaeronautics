"""The membership coverage ledger.

``Member.payment_status`` and the coverage dates answer *what* a member's state
is. This module answers *why*, and treats that as the authority: a member is
covered because some period was granted on stated grounds, not because a field
says so.

The distinction is not academic. Coverage used to be written by several code
paths that each held part of the picture, so a subscription merely reported as
``active`` by Stripe could set a member to paid with nothing behind it. A period
row cannot be created without saying what it is based on, which makes an
unsupported grant something you have to write on purpose.

Revocation rather than deletion: a lost dispute or a refund means the grant
should no longer count, but the fact that it was once believed is part of the
history an administrator may need to explain a member's access.
"""

from ..db_models import MembershipPeriod, db
from . import ValidationError
from .clock import (
    datetime_to_membership_date,
    first_day_of_year,
    get_membership_today,
    last_day_of_year,
)
from .membership import ACTIVE_MEMBER_STATUSES


def grant_period(
    member,
    starts_on,
    ends_on,
    reason,
    *,
    stripe_invoice_id=None,
    stripe_subscription_id=None,
    granted_by_user_id=None,
    note=None,
):
    """Record that ``member`` is covered from ``starts_on`` to ``ends_on``.

    Idempotent on ``stripe_invoice_id``: a redelivered ``invoice.paid`` webhook
    returns the period already granted for that invoice instead of a second one.
    """
    if member is None:
        raise ValidationError("A member is required to grant a membership period.")
    if starts_on is None or ends_on is None:
        raise ValidationError("A membership period needs both a start and an end date.")
    if ends_on < starts_on:
        raise ValidationError("A membership period cannot end before it starts.")
    if reason not in {
        MembershipPeriod.REASON_PAID,
        MembershipPeriod.REASON_FREE_PERIOD,
        MembershipPeriod.REASON_ADMIN_GRANT,
    }:
        raise ValidationError(f"Unknown membership period reason: {reason!r}")

    # Coverage cannot begin before the member existed. A first year is prorated
    # from the join date, but the invoice that pays for it names only a calendar
    # year, so granting from it alone would claim the months before they joined
    # -- months they were not members and did not pay for.
    joined_on = datetime_to_membership_date(member.created_at)
    if joined_on and starts_on < joined_on <= ends_on:
        starts_on = joined_on

    if stripe_invoice_id:
        existing = db.session.execute(
            db.select(MembershipPeriod).filter_by(stripe_invoice_id=stripe_invoice_id)
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    # One payment can be reported twice: a Checkout subscription produces
    # checkout.session.completed *and* invoice.paid, and the invoice id is not
    # yet known at checkout time, so the id above cannot match them up. Keep one
    # period per member, reason and coverage year, and prefer the window already
    # recorded -- for a prorated first year that is the accurate one, where a
    # whole calendar year would claim coverage that was never bought.
    same_year = [
        period
        for period in active_periods(member, on_date=None, include_future=True)
        if period.reason == reason and period.ends_on.year == ends_on.year
    ]
    if same_year:
        existing = same_year[0]
        if stripe_invoice_id and not existing.stripe_invoice_id:
            # Attach the evidence that arrived with the later event.
            existing.stripe_invoice_id = stripe_invoice_id
        if stripe_subscription_id and not existing.stripe_subscription_id:
            existing.stripe_subscription_id = stripe_subscription_id
        # Keep the narrower window. Stripe does not order its events, so which
        # of checkout.session.completed and invoice.paid arrives first is
        # arbitrary -- and the two describe the same payment differently: one
        # knows the prorated join date, the other only a calendar year. Taking
        # the later start makes the record say the same thing either way, and
        # never claims more coverage than was actually bought.
        if starts_on > existing.starts_on:
            existing.starts_on = starts_on
        return existing

    period = MembershipPeriod(
        member=member,
        starts_on=starts_on,
        ends_on=ends_on,
        reason=reason,
        stripe_invoice_id=stripe_invoice_id,
        stripe_subscription_id=stripe_subscription_id,
        granted_by_user_id=granted_by_user_id,
        note=note,
    )
    db.session.add(period)
    return period


def grant_calendar_year(member, year, reason, **evidence):
    """Grant a full Jan 1 - Dec 31 membership year.

    Renewals always cover a whole calendar year, because the association runs one
    shared cycle rather than per-member anniversaries.
    """
    return grant_period(member, first_day_of_year(year), last_day_of_year(year), reason, **evidence)


def revoke_period(period, reason):
    """Stop a period counting towards access, keeping it on the record."""
    if period is None or period.is_revoked:
        return False
    from .clock import get_now_utc

    period.revoked_at = get_now_utc()
    period.revoked_reason = (reason or "")[:255] or None
    return True


def revoke_periods_for_subscription(member, subscription_id, reason):
    """Revoke coverage granted on the strength of one subscription.

    Used when a charge is disputed and lost: the money came back, so the coverage
    it bought is no longer supported.
    """
    revoked = 0
    for period in active_periods(member, on_date=None, include_future=True):
        if subscription_id and period.stripe_subscription_id != subscription_id:
            continue
        if period.reason != MembershipPeriod.REASON_PAID:
            continue
        if revoke_period(period, reason):
            revoked += 1
    return revoked


def active_periods(member, on_date=None, include_future=False):
    """Non-revoked periods, optionally restricted to ones covering ``on_date``."""
    if member is None:
        return []
    periods = [p for p in (member.membership_periods or []) if not p.is_revoked]
    if include_future:
        return periods
    day = on_date or get_membership_today()
    return [p for p in periods if p.covers(day)]


def has_coverage(member, on_date=None):
    """Whether the ledger supports membership access on ``on_date``."""
    return bool(active_periods(member, on_date=on_date))


def coverage_end(member, on_date=None):
    """The furthest date the ledger currently covers, or None."""
    periods = active_periods(member, on_date=None, include_future=True)
    if not periods:
        return None
    return max(p.ends_on for p in periods)


def coverage_start(member):
    periods = active_periods(member, on_date=None, include_future=True)
    if not periods:
        return None
    return min(p.starts_on for p in periods)


def project_coverage_onto_member(member, on_date=None):
    """Recompute the member's cached coverage fields from the ledger.

    The cached fields exist so ordinary reads do not need a join, but they are a
    projection: this is the one place that derives them, which is what keeps them
    from drifting away from the evidence. Returns True if anything changed.
    """
    if member is None:
        return False

    periods = active_periods(member, on_date=None, include_future=True)
    if not periods:
        return False

    day = on_date or get_membership_today()
    ends_on = max(p.ends_on for p in periods)
    starts_on = min(p.starts_on for p in periods)
    covering = [p for p in periods if p.covers(day)]

    changed = False
    # Extend-only: a ledger that has not caught up with a renewal must not pull
    # a member's coverage backwards.
    if member.membership_ends_on is None or ends_on > member.membership_ends_on:
        member.membership_ends_on = ends_on
        member.renewal_due_on = first_day_of_year(ends_on.year + 1)
        changed = True
    if member.membership_starts_on is None or starts_on < member.membership_starts_on:
        member.membership_starts_on = starts_on
        changed = True

    if covering:
        # Paid evidence outranks a free grant when both cover today.
        reasons = {p.reason for p in covering}
        if MembershipPeriod.REASON_PAID in reasons:
            desired_status = "paid"
        elif MembershipPeriod.REASON_FREE_PERIOD in reasons:
            desired_status = "free_period"
        else:
            desired_status = "paid"  # an admin grant confers full access
        # Never overwrite a status that carries more specific meaning than
        # "covered" -- a scheduled cancellation still has valid coverage.
        if member.payment_status not in ACTIVE_MEMBER_STATUSES and member.payment_status != desired_status:
            member.payment_status = desired_status
            changed = True
        if not member.is_active:
            member.is_active = True
            changed = True

    return changed


def describe_coverage(member, on_date=None):
    """A JSON-serializable view of why a member has access.

    Returned as plain data so an HTML page, an admin screen and a future JSON
    endpoint all describe coverage the same way.
    """
    day = on_date or get_membership_today()
    periods = sorted(
        member.membership_periods or [], key=lambda p: (p.ends_on, p.id or 0), reverse=True
    )
    return {
        "has_coverage": has_coverage(member, on_date=day),
        "coverage_start": coverage_start(member),
        "coverage_end": coverage_end(member),
        "periods": [
            {
                "starts_on": p.starts_on,
                "ends_on": p.ends_on,
                "reason": p.reason,
                "stripe_invoice_id": p.stripe_invoice_id,
                "note": p.note,
                "revoked": p.is_revoked,
                "revoked_reason": p.revoked_reason,
                "covers_today": p.covers(day),
            }
            for p in periods
        ],
    }
