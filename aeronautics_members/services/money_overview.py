"""All the money on the association's Stripe account, for a period -- read from the copy.

Everything the association sells is paid into one Stripe account: the
membership, the teams' fees, credit, and -- outside the portal -- the webshop
and events. The portal keeps a copy of Stripe's book (services/stripe_books.py),
each booking sorted by what it was for, so this reads the database and answers
at once, for any period:

- **membership**, **team**, **credit**: a payment, and what took it back -- a
  refund, a bounced SEPA debit, a chargeback -- goes where the payment went.
- **other**: every other payment -- the webshop and events.
- Bookings that are no payment (Stripe billing its own fees, say) count only
  in the total; transfers to the bank are listed on their own.

For each: received, refunded, bounced, taken back, Stripe's fees and the net.
Since the copy holds every booking from the very first, the balance on the
last day of the period is their sum -- and up to now, it must be what Stripe
says it holds, which each fetch compares. Of that balance, what is still owed
to the teams and what members still hold as credit topped up through Stripe
is not the association's own.

The check of the payments against the portal's records runs after every
fetch (services/money_checks.py); its findings are listed with the overview.
"""

from datetime import datetime, time, timedelta, timezone

from ..config import MEMBERSHIP_TIMEZONE
from ..db_models import StripeBooking, db
from .money_checks import records_since  # noqa: F401 -- the API asks here
from .stripe_books import (
    CREDIT, MEMBERSHIP, OTHER, PAID_OUT, RECEIVED, REFUNDED, RETURNED, TAKEN_BACK, TEAM, state,
)

PRODUCTS = (MEMBERSHIP, TEAM, CREDIT, OTHER)


def _start_of(day):
    return datetime.combine(day, time(0, 0), MEMBERSHIP_TIMEZONE).astimezone(timezone.utc)


def _aware(moment):
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _empty():
    return {"received": 0, "refunded": 0, "returned": 0, "taken_back": 0, "fees": 0, "net": 0}


def _books(start, end):
    """The period's bookings by product, Stripe's own fees and other bookings, what went to the
    bank, and the balance at ``end``."""
    by_product = {product: _empty() for product in PRODUCTS}
    account = {"stripe_fees": 0, "other": 0, "net": 0}
    books = {"by_product": by_product, "account": account, "paid_out": 0, "balance_at_end": 0}
    for booking in db.session.execute(
        db.select(StripeBooking).where(StripeBooking.booked_at < end)
    ).scalars():
        books["balance_at_end"] += booking.net_cents
        if start is not None and _aware(booking.booked_at) < start:
            continue
        kind, amount = booking.type, booking.amount_cents
        if kind in PAID_OUT:
            books["paid_out"] -= booking.net_cents
            continue
        if kind == "stripe_fee":
            account["stripe_fees"] -= amount  # billed on its own, as a negative amount
            account["net"] += booking.net_cents
            continue
        if kind not in RECEIVED + REFUNDED + RETURNED + TAKEN_BACK:
            account["other"] += amount
            account["net"] += booking.net_cents
            continue
        totals = by_product[booking.product or OTHER]
        totals["fees"] += booking.fee_cents
        totals["net"] += booking.net_cents
        if kind in RECEIVED:
            totals["received"] += amount
        elif kind in REFUNDED:
            totals["refunded"] -= amount
        elif kind in RETURNED:
            totals["returned"] -= amount
        else:
            totals["taken_back"] -= amount
    total = _empty()
    for totals in by_product.values():
        for key in total:
            total[key] += totals[key]
    total["fees"] += account["stripe_fees"]
    total["net"] += account["net"]
    books["total"] = total
    return books


def owed_to_teams_on(until):
    """What the teams were owed at the end of ``until``: every line of their money up to then,
    transfers included (services/team_money.py)."""
    from .team_money import statement
    from .teams import all_teams

    owed = 0
    for team in all_teams():
        found = statement(team)
        owed += sum(line.amount for line in found["lines"]
                    if not line.waiting and (line.day is None or line.day <= until))
    return owed


def period_asked(since, until, today):
    """The period asked for: from ``since`` (None: the start) to ``until`` (None: today).
    Never past today, and never before the portal's records begin; a start
    after the end, or before 2000, counts as none (the start)."""
    until = min(until or today, today)
    if since is not None and (since > until or since.year < 2000):
        since = None
    start = records_since()
    if start is not None:
        since = max(since or start, start)
        until = max(until, since)
    return since, until


def overview(since, until):
    """The money from ``since`` (None: from the first booking) to ``until``, both days included,
    in cents, from the copy of Stripe's book."""
    from .credit_fees import held_on

    start = _start_of(since) if since else None
    end = _start_of(until + timedelta(days=1))
    books = _books(start, end)
    owed = owed_to_teams_on(until)
    held = held_on(end)
    current = state()
    return {
        "since": since,
        "until": until,
        "books": books,
        "owed_to_teams": owed,
        "credit_held": held,
        # What is left once the teams and the members have what is theirs.
        "association_own": books["balance_at_end"] - owed - held["top_ups"],
        "fetched_at": _aware(current.fetched_at),
        "stripe_balance": current.stripe_balance_cents,
        "books_balance": current.books_balance_cents,
        "last_error": current.last_error,
        "checked_at": _aware(current.checked_at),
    }
