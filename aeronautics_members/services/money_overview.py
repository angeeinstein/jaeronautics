"""All the money on the association's Stripe account, for a year, and a check of it.

Everything the association sells is paid into one Stripe account: the
membership, the teams' fees, and -- outside the portal -- the webshop and
events. The portal keeps each team payment with its amount (services/
team_money.py), but of the membership only which invoice paid for which
period, and of the rest nothing. So the totals come from Stripe:

- **Stripe's own books** (balance transactions): what was received, refunded
  or taken back, Stripe's fees, the net, and what went to the bank account;
  and what is in the account now.
- **What it was for** (paid invoices, sorted as services/stripe_scope.py sorts
  every webhook): membership, team, or neither -- the rest of what was
  received is the webshop and events, which Stripe bills without invoices.
- **Whose it is:** of the net, the teams' share is what their members paid
  (less refunds and lost chargebacks) -- Stripe's fees are the association's
  -- and the rest is the association's own.

And the check, invoice by invoice: every paid team invoice is a team payment
in the portal with the same amount, and the other way round; every paid
membership invoice has a membership period, and every period the portal counts
as paid has an invoice Stripe shows as paid.

Read-only, and on request: it pages through Stripe and takes a few seconds.
"""

from datetime import date, datetime, time, timezone

import stripe

from ..config import MEMBERSHIP_TIMEZONE
from ..db_models import MembershipPeriod, Payment, db
from . import payments
from .stripe_scope import scope_of_invoice
from .team_money import _earned

RECEIVED = ("charge", "payment")
REFUNDED = ("refund", "payment_refund")
# A SEPA debit announced and then not honoured: received, and handed back.
RETURNED = ("payment_failure_refund",)
TAKEN_BACK = ("adjustment", "payment_reversal")  # chargebacks and their reversals
PAID_OUT = ("payout", "payout_cancel", "payout_failure")


def _unix(day):
    return int(datetime.combine(day, time(0, 0), MEMBERSHIP_TIMEZONE).timestamp())


def _year_bounds(year):
    return _unix(date(year, 1, 1)), _unix(date(year + 1, 1, 1))


def _in_year(moment, year):
    if moment is None:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(MEMBERSHIP_TIMEZONE).year == year


def _stripe_books(start, end):
    totals = {"received": 0, "refunded": 0, "returned": 0, "taken_back": 0, "fees": 0, "other": 0,
              "net": 0, "paid_out": 0}
    listing = stripe.BalanceTransaction.list(created={"gte": start, "lt": end}, limit=100)
    for txn in listing.auto_paging_iter():
        if (txn.get("currency") or "eur") != "eur":
            continue
        kind, amount, fee, net = txn.get("type"), int(txn.get("amount") or 0), int(txn.get("fee") or 0), int(txn.get("net") or 0)
        if kind in PAID_OUT:
            totals["paid_out"] -= net
            continue
        totals["fees"] += fee
        totals["net"] += net
        if kind in RECEIVED:
            totals["received"] += amount
        elif kind in REFUNDED:
            totals["refunded"] -= amount
        elif kind in RETURNED:
            totals["returned"] -= amount
        elif kind in TAKEN_BACK:
            totals["taken_back"] -= amount
        elif kind == "stripe_fee":
            totals["fees"] -= amount  # billed on its own, as a negative amount
        else:
            totals["other"] += amount
    return totals


def _in_stripe_now():
    balance = stripe.Balance.retrieve()

    def eur(entries):
        return sum(int(entry.get("amount") or 0) for entry in entries or [] if entry.get("currency") == "eur")

    return {"available": eur(balance.get("available")), "pending": eur(balance.get("pending"))}


def _paid_invoices(start, end):
    """Paid invoices of the year with money on them, sorted: (membership, team, neither)."""
    membership, team, neither = {}, {}, {}
    listing = stripe.Invoice.list(status="paid", created={"gte": start, "lt": end}, limit=100)
    for invoice in listing.auto_paging_iter():
        if int(invoice.get("amount_paid") or 0) <= 0:
            continue  # the EUR 0 invoice that opens an October free period, a trial
        scope = scope_of_invoice(invoice)
        if scope.is_ours:
            membership[invoice["id"]] = invoice
        elif scope.purpose == payments.PURPOSE_TEAM:
            team[invoice["id"]] = invoice
        else:
            neither[invoice["id"]] = invoice
    return membership, team, neither


def _paid_in_stripe(invoice_id):
    """Whether Stripe shows this invoice as paid -- for one outside the year's listing."""
    try:
        return stripe.Invoice.retrieve(invoice_id).get("status") == "paid"
    except stripe.InvalidRequestError:
        return False


def _who(invoice):
    return invoice.get("customer_email") or invoice.get("customer_name") or invoice.get("customer") or ""


def _check_teams(stripe_team, year):
    """Every paid team invoice is a payment in the portal, with its amount, and back.

    A payment once per period is matched by its invoice, or -- recorded before
    Stripe made the receipt -- by the team membership and period the invoice
    names.
    """
    problems = []
    portal = db.session.execute(db.select(Payment).filter_by(purpose=payments.PURPOSE_TEAM)).scalars().all()
    by_invoice = {payment.stripe_invoice_id: payment for payment in portal if payment.stripe_invoice_id}
    by_period = {(str(payment.team_membership_id), payment.covers_until.isoformat() if payment.covers_until else None):
                 payment for payment in portal if not payment.stripe_invoice_id}
    matched = set()
    for invoice_id, invoice in stripe_team.items():
        metadata = invoice.get("metadata") or {}
        payment = by_invoice.get(invoice_id) or by_period.get(
            (str(metadata.get("team_membership_id")), metadata.get("covers_until")))
        if payment is None:
            problems.append({"what": "Paid in Stripe, missing in the portal", "invoice": invoice_id,
                             "amount": int(invoice.get("amount_paid") or 0), "who": _who(invoice)})
            continue
        matched.add(payment.id)
        if payment.amount_cents != int(invoice.get("amount_paid") or 0):
            problems.append({"what": "Amount differs (portal / Stripe)", "invoice": invoice_id,
                             "amount": payment.amount_cents, "stripe_amount": int(invoice.get("amount_paid") or 0),
                             "who": _who(invoice)})
    for payment in portal:
        if payment.id in matched or not _in_year(payment.paid_at, year):
            continue
        if payment.stripe_invoice_id and _paid_in_stripe(payment.stripe_invoice_id):
            continue  # the invoice is from the year before; the money came in this one
        problems.append({"what": "In the portal, not paid in Stripe", "invoice": payment.stripe_invoice_id
                         or payment.stripe_payment_intent_id, "amount": payment.amount_cents,
                         "who": payment.user.email if payment.user is not None else ""})
    return problems


def _check_membership(stripe_membership, year):
    """Every paid membership invoice has a period, and every paid period an invoice."""
    problems = []
    periods = db.session.execute(
        db.select(MembershipPeriod).where(MembershipPeriod.reason == MembershipPeriod.REASON_PAID,
                                          MembershipPeriod.stripe_invoice_id.is_not(None))
    ).scalars().all()
    recorded = {period.stripe_invoice_id for period in periods}
    for invoice_id, invoice in stripe_membership.items():
        if invoice_id not in recorded:
            problems.append({"what": "Paid in Stripe, no membership period in the portal", "invoice": invoice_id,
                             "amount": int(invoice.get("amount_paid") or 0), "who": _who(invoice)})
    for period in periods:
        if not _in_year(period.created_at, year) or period.stripe_invoice_id in stripe_membership:
            continue
        if period.revoked_at is not None or _paid_in_stripe(period.stripe_invoice_id):
            continue
        member = period.member
        problems.append({"what": "Membership period counted as paid, invoice not paid in Stripe",
                         "invoice": period.stripe_invoice_id, "amount": None,
                         "who": member.email_private if member is not None else ""})
    return problems


def overview(year):
    """The year's money, in cents, with the check. Raises stripe.StripeError when Stripe cannot be asked."""
    payments.apply_runtime_stripe_config()
    start, end = _year_bounds(year)
    books = _stripe_books(start, end)
    membership, team, neither = _paid_invoices(start, end)

    team_paid = sum(int(invoice.get("amount_paid") or 0) for invoice in team.values())
    membership_paid = sum(int(invoice.get("amount_paid") or 0) for invoice in membership.values())
    # What actually came in: received, less the SEPA debits handed back again.
    came_in = books["received"] - books["returned"]
    teams_share = sum(
        _earned(payment) for payment in db.session.execute(
            db.select(Payment).filter_by(purpose=payments.PURPOSE_TEAM)
        ).scalars() if _in_year(payment.paid_at, year)
    )
    return {
        "year": year,
        "books": books,
        "in_stripe_now": _in_stripe_now(),
        "split": {
            "membership": membership_paid,
            "teams": team_paid,
            "other": came_in - membership_paid - team_paid,
        },
        "teams_share": teams_share,
        "association_own": books["net"] - teams_share,
        "checks": {"teams": _check_teams(team, year), "membership": _check_membership(membership, year)},
        "other_invoices": len(neither),
        "checked_at": datetime.now(timezone.utc),
    }
