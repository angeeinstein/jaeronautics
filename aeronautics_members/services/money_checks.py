"""The check of Stripe's books against the portal's records, after every fetch.

Read from the copy of Stripe's books (services/stripe_books.py), so it asks
Stripe nothing and runs each time something new was fetched -- seconds after
a webhook, and at least hourly. Only what happened since the portal took over
the payments (:func:`records_since`) is checked.

What should hold:
- every payment of a team fee or a credit top-up in Stripe is the portal's
  record of it, with the same amount, and the other way round;
- every membership payment in Stripe paid an invoice the portal counts as a
  membership period, and every period counted as paid was paid in Stripe;
- what Stripe gave back -- a refund, a returned debit, a lost chargeback --
  the portal took off too.

A SEPA debit is in Stripe's books from the day it is announced, but the
portal records it only once it has cleared, days later: until then (the
booking is *pending*), and when it failed, a missing record is no difference.

Each difference is kept (:class:`MoneyDifference`) while it lasts, and
marked resolved when a check no longer finds it. Stripe and the portal hear
of a payment by different messages, seconds apart, so a difference is only
emailed once it has lasted :data:`SETTLE` -- once, to the admins and the
treasurer, unless switched off (Admin › Money, the settings).
"""

from datetime import date, datetime, time, timedelta, timezone

from flask import current_app

from ..config import MEMBERSHIP_TIMEZONE
from ..db_models import MembershipPeriod, MoneyDifference, Payment, Role, StripeBooking, User, db
from .clock import get_now_utc
from .stripe_books import CREDIT, MEMBERSHIP, RECEIVED, REFUNDED, RETURNED, TAKEN_BACK, TEAM, state

#: A difference is emailed once it has lasted this long: by then both of
#: Stripe's messages about a payment have long arrived.
SETTLE = timedelta(hours=1)

#: The day the portal took over the association's payments (it went live
#: then). Stripe's earlier bookings were the old site's and are no part of
#: the portal's records, so nothing before it is checked. The tests set
#: ``MONEY_RECORDS_SINCE`` to look further back.
PORTAL_STARTED_ON = date(2026, 10, 1)


MISSING_IN_PORTAL = "Paid in Stripe, missing in the portal"
AMOUNT_DIFFERS = "Amount differs (portal / Stripe)"
NOT_IN_STRIPE = "In the portal, not paid in Stripe"
NO_PERIOD = "Paid in Stripe, no membership period in the portal"
PERIOD_NOT_PAID = "Membership period counted as paid, no payment in Stripe"
REFUND_DIFFERS = "Refunded in Stripe, not in the portal"
TAKEN_BACK_IN_STRIPE = "Taken back in Stripe, still counted in the portal"


def records_since():
    return current_app.config.get("MONEY_RECORDS_SINCE", PORTAL_STARTED_ON)


def _start():
    since = records_since()
    return datetime.combine(since, time(0, 0), MEMBERSHIP_TIMEZONE).astimezone(timezone.utc) if since else None


def _aware(moment):
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def emails_on():
    from .money_settings import difference_emails

    return difference_emails()


# --- Finding them ------------------------------------------------------------------------


def _who_paid(payment):
    if payment is None or payment.user is None:
        return ""
    return payment.user.email or ""


def find(now=None):
    """Every difference now: {key: fields}."""
    start = _start()
    found = {}

    def add(key, product, what, reference, amount=None, stripe_amount=None, who=""):
        found[key] = {"product": product, "what": what, "reference": reference, "amount_cents": amount,
                      "stripe_amount_cents": stripe_amount, "who": (who or "")[:255]}

    bookings = db.session.execute(
        db.select(StripeBooking).where(StripeBooking.product.in_((MEMBERSHIP, TEAM, CREDIT)))
    ).scalars().all()
    by_charge = {}
    for booking in bookings:
        by_charge.setdefault(booking.charge_id, []).append(booking)

    periods = db.session.execute(
        db.select(MembershipPeriod).where(MembershipPeriod.reason == MembershipPeriod.REASON_PAID,
                                          MembershipPeriod.stripe_invoice_id.is_not(None))
    ).scalars().all()
    period_of = {period.stripe_invoice_id: period for period in periods}
    paid_invoices = set()
    group_of_invoice = {}
    for booking in bookings:
        if booking.invoice_id and booking.type in RECEIVED and booking.amount_cents > 0:
            group_of_invoice.setdefault(booking.invoice_id, []).append(booking)

    for charge_id, group in by_charge.items():
        paid = [booking for booking in group if booking.type in RECEIVED and booking.amount_cents > 0]
        if not paid:
            continue
        payment_booking = paid[0]
        if start is not None and _aware(payment_booking.booked_at) < start:
            continue
        product = payment_booking.product
        received = sum(booking.amount_cents for booking in paid)
        refunded = -sum(booking.amount_cents for booking in group if booking.type in REFUNDED)
        returned = -sum(booking.amount_cents for booking in group if booking.type in RETURNED)
        taken_back = -sum(booking.amount_cents for booking in group if booking.type in TAKEN_BACK)
        reference = payment_booking.invoice_id or payment_booking.payment_intent_id or charge_id
        # A debit not cleared yet (SEPA, a few days), or one that failed and was handed back:
        # the portal records it only once it has cleared, so a missing record is no difference.
        unsettled = any(booking.pending for booking in paid) or returned >= received
        if product == MEMBERSHIP:
            if payment_booking.invoice_id:
                paid_invoices.add(payment_booking.invoice_id)
            period = period_of.get(payment_booking.invoice_id)
            # By invoice: one paid by a second try after a bounced debit is one difference, not two.
            key = payment_booking.invoice_id or charge_id
            if period is None and not unsettled and f"membership:no_period:{key}" not in found:
                add(f"membership:no_period:{key}", MEMBERSHIP, NO_PERIOD, reference, stripe_amount=received)
            elif period is not None and (returned or taken_back) and period.revoked_at is None \
                    and not _paid_by_another(group_of_invoice.get(payment_booking.invoice_id, []), charge_id):
                add(f"membership:taken_back:{key}", MEMBERSHIP, TAKEN_BACK_IN_STRIPE, reference,
                    stripe_amount=returned + taken_back)
            continue
        payment = payment_booking.payment
        if payment is None and unsettled:
            continue
        if payment is None:
            add(f"{product}:missing:{charge_id}", product, MISSING_IN_PORTAL, reference, stripe_amount=received)
            continue
        who = _who_paid(payment)
        if payment.amount_cents != received:
            add(f"{product}:amount:{charge_id}", product, AMOUNT_DIFFERS, reference, amount=payment.amount_cents,
                stripe_amount=received, who=who)
        if refunded != (payment.refunded_cents or 0):
            add(f"{product}:refund:{charge_id}", product, REFUND_DIFFERS, reference,
                amount=payment.refunded_cents or 0, stripe_amount=refunded, who=who)
        if (returned or taken_back) and payment.status != "disputed" \
                and (payment.refunded_cents or 0) < payment.amount_cents \
                and not _paid_by_another(group_of_invoice.get(payment_booking.invoice_id, []), charge_id):
            add(f"{product}:taken_back:{charge_id}", product, TAKEN_BACK_IN_STRIPE, reference,
                stripe_amount=returned + taken_back, who=who)

    # The other way round: the portal's records with no payment in Stripe.
    linked = set(db.session.execute(
        db.select(StripeBooking.payment_id).where(StripeBooking.payment_id.is_not(None),
                                                  StripeBooking.type.in_(RECEIVED))
    ).scalars())
    for payment in db.session.execute(
        db.select(Payment).where(Payment.purpose.in_((TEAM, CREDIT)))
    ).scalars():
        if payment.id in linked or (start is not None and _aware(payment.paid_at) < start):
            continue
        add(f"{payment.purpose}:not_in_stripe:{payment.id}", payment.purpose, NOT_IN_STRIPE,
            payment.stripe_invoice_id or payment.stripe_payment_intent_id, amount=payment.amount_cents,
            who=_who_paid(payment))
    for period in periods:
        if period.revoked_at is not None or period.stripe_invoice_id in paid_invoices:
            continue
        if start is not None and _aware(period.created_at) < start:
            continue
        if _paid_before(period.stripe_invoice_id):
            continue
        member = period.member
        add(f"membership:not_paid:{period.stripe_invoice_id}", MEMBERSHIP, PERIOD_NOT_PAID,
            period.stripe_invoice_id, who=member.email_private if member is not None else "")
    return found


def _paid_by_another(paid, charge_id):
    """Another charge paid the same invoice: the one taken back was retried, and the period stands."""
    return any(booking.charge_id != charge_id for booking in paid)


def _paid_before(invoice_id):
    """A period recorded now for an invoice paid before the portal took over: its payment is in the book."""
    return db.session.execute(
        db.select(StripeBooking.id).where(StripeBooking.invoice_id == invoice_id, StripeBooking.type.in_(RECEIVED))
    ).first() is not None


# --- Keeping them ------------------------------------------------------------------------


def run(now=None):
    """Compare, keep what differs, mark what went away. Returns the open differences."""
    now = now or get_now_utc()
    if state().fetched_through is None:
        return []  # nothing fetched yet: everything would look missing
    found = find(now)
    open_rows = {row.key: row for row in db.session.execute(
        db.select(MoneyDifference).where(MoneyDifference.resolved_at.is_(None))
    ).scalars()}
    for key, fields in found.items():
        row = open_rows.pop(key, None)
        if row is None:
            row = db.session.execute(db.select(MoneyDifference).filter_by(key=key)).scalars().first()
            if row is None:
                row = MoneyDifference(key=key, first_seen_at=now)
                db.session.add(row)
            else:  # back again after it had gone away: a new one
                row.first_seen_at, row.resolved_at, row.emailed_at = now, None, None
        for name, value in fields.items():
            setattr(row, name, value)
        row.last_seen_at = now
    for row in open_rows.values():
        row.resolved_at = now
    state().checked_at = now
    db.session.flush()
    return open_differences()


def open_differences():
    return db.session.execute(
        db.select(MoneyDifference).where(MoneyDifference.resolved_at.is_(None))
        .order_by(MoneyDifference.first_seen_at, MoneyDifference.id)
    ).scalars().all()


#: Resolved differences are forgotten after this long.
KEPT = timedelta(days=365)


def forget_old(now=None):
    """Every night: resolved differences older than a year go. Returns how many."""
    now = now or get_now_utc()
    old = db.session.execute(
        db.select(MoneyDifference).where(MoneyDifference.resolved_at < now - KEPT)
    ).scalars().all()
    for row in old:
        db.session.delete(row)
    return len(old)


def forget_person(user):
    """An erased account: its address leaves the differences that named it."""
    emails = {address for address in (getattr(user, "email", None),
                                       getattr(getattr(user, "member", None), "email_private", None)) if address}
    if not emails:
        return 0
    rows = db.session.execute(db.select(MoneyDifference).where(MoneyDifference.who.in_(emails))).scalars().all()
    for row in rows:
        row.who = ""
    return len(rows)


def recently_resolved(days=30):
    since = get_now_utc() - timedelta(days=days)
    return db.session.execute(
        db.select(MoneyDifference).where(MoneyDifference.resolved_at >= since)
        .order_by(MoneyDifference.resolved_at.desc())
    ).scalars().all()


# --- Telling ------------------------------------------------------------------------------


def recipients():
    """The admins and the treasurers: whoever gets the admin emails or looks after the money."""
    from ..permissions import Permission, roles_with

    slugs = roles_with(Permission.NOTIFICATIONS_RECEIVE) | roles_with(Permission.TEAMS_MONEY)
    return db.session.execute(
        db.select(User.email).where(User.email_verified_at.is_not(None), User.deleted_at.is_(None),
                                    User.roles.any(Role.slug.in_(slugs)))
        .order_by(User.email)
    ).scalars().all()


def email_new(now=None):
    """Email the differences that have lasted, once each. Returns how many were in the email."""
    now = now or get_now_utc()
    if not emails_on():
        return 0
    due = [row for row in open_differences()
           if row.emailed_at is None and _aware(row.first_seen_at) <= now - SETTLE]
    if not due:
        return 0
    to = recipients()
    if not to:
        return 0
    from ..mail_utils import send_mail
    from ..security_utils import build_public_url
    from .notifications import get_notification_service
    from .team_money import euros

    lines = []
    for row in due:
        amounts = []
        if row.amount_cents is not None:
            amounts.append(f"portal {euros(row.amount_cents)}")
        if row.stripe_amount_cents is not None:
            amounts.append(f"Stripe {euros(row.stripe_amount_cents)}")
        lines.append(" · ".join(part for part in (
            row.product.capitalize(), row.what, row.reference or "", ", ".join(amounts), row.who or ""
        ) if part))
    count = len(due)
    sent, error = send_mail(
        from_account=get_notification_service().get_sender_account(),
        to_email=to[0], bcc_emails=to[1:] or None, return_error=True,
        subject=f"Stripe and the portal differ: {count} {'thing' if count == 1 else 'things'} to look at",
        template_name="money_differences.html",
        heading="Stripe and the portal differ",
        lines=lines,
        action_url=build_public_url("teams.admin_money"),
        action_label="Open Money",
    )
    if not sent:
        current_app.logger.warning("Could not email the money differences: %s", error)
        return 0
    for row in due:
        row.emailed_at = now
    return count
