"""All the money on the association's Stripe account, for a period, and a check of it.

Everything the association sells is paid into one Stripe account: the
membership, the teams' fees, and -- outside the portal -- the webshop and
events. The portal keeps each team payment with its amount (services/
team_money.py), but of the membership only which invoice paid for which
period, and of the rest nothing. So the totals come from Stripe's own books
(its balance transactions), each booking sorted by what it was for:

- **membership** and **team**: a payment that paid an invoice, sorted as
  services/stripe_scope.py sorts every webhook; a team's one-time payment also
  by the payment the portal recorded, or the ``purpose`` it carries. A refund,
  a bounced SEPA debit or a chargeback goes where the payment it takes back
  went.
- **other**: every other payment -- the webshop and events, which Stripe bills
  without invoices.
- Bookings that are no payment at all (Stripe billing its own fees, say)
  count only in the total.

For each: received, refunded, bounced, taken back, Stripe's fees and the net.
Of the net, the teams' share is what their members paid (Stripe's fees are the
association's), and the rest is the association's own. Since all of Stripe's
books are read from the very first booking, the balance on the last day of
the period is their sum -- and up to today, it must be what Stripe says it
holds now.

And the check, invoice by invoice: every paid team invoice is a team payment
in the portal with the same amount, and the other way round; every paid
membership invoice has a membership period, and every period the portal counts
as paid has an invoice Stripe shows as paid.

Read-only, and on request: it pages through Stripe and takes a few seconds.
"""

from datetime import datetime, time, timedelta, timezone

import stripe

from ..config import MEMBERSHIP_TIMEZONE
from ..db_models import MembershipPeriod, Payment, TeamPayout, db
from . import payments
from .clock import get_membership_today
from .stripe_scope import _from_metadata, _id_of, scope_of_invoice
from .team_money import _earned

MEMBERSHIP, TEAM, OTHER = "membership", "team", "other"
PRODUCTS = (MEMBERSHIP, TEAM, OTHER)

RECEIVED = ("charge", "payment")
REFUNDED = ("refund", "payment_refund")
# A SEPA debit announced and then not honoured: received, and handed back.
RETURNED = ("payment_failure_refund",)
TAKEN_BACK = ("adjustment", "payment_reversal")  # chargebacks and their reversals
PAID_OUT = ("payout", "payout_cancel", "payout_failure")


def _unix(day):
    return int(datetime.combine(day, time(0, 0), MEMBERSHIP_TIMEZONE).timestamp())


def _local_day(moment):
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(MEMBERSHIP_TIMEZONE).date()


def _in_period(moment, since, until):
    day = _local_day(moment)
    return day is not None and (since is None or day >= since) and day <= until


def _product_of_scope(scope):
    if scope.is_ours:
        return MEMBERSHIP
    return TEAM if scope.purpose == payments.PURPOSE_TEAM else OTHER


def _invoices(end):
    """Every invoice up to ``end``: {id: (invoice, product)}."""
    found = {}
    for invoice in stripe.Invoice.list(created={"lt": end}, limit=100).auto_paging_iter():
        found[invoice["id"]] = (invoice, _product_of_scope(scope_of_invoice(invoice)))
    return found


def _products_of_payments(invoices):
    """What each payment was for, by payment intent and charge id."""
    product_of = {}
    for invoice_payment in stripe.InvoicePayment.list(limit=100).auto_paging_iter():
        known = invoices.get(_id_of(invoice_payment.get("invoice")))
        if known is None:
            continue
        paid_by = invoice_payment.get("payment") or {}
        for key in ("payment_intent", "charge"):
            if _id_of(paid_by.get(key)):
                product_of[_id_of(paid_by.get(key))] = known[1]
    # A team's one-time payment, recorded by its payment before the receipt existed.
    for payment_intent_id in db.session.execute(
        db.select(Payment.stripe_payment_intent_id).where(Payment.purpose == payments.PURPOSE_TEAM,
                                                          Payment.stripe_payment_intent_id.is_not(None))
    ).scalars():
        product_of.setdefault(payment_intent_id, TEAM)
    return product_of


def _product_of_booking(source, product_of):
    """Where a payment, or what takes one back (refund, dispute), belongs."""
    if isinstance(source, str):
        return product_of.get(source, OTHER)
    for key in ("payment_intent", "charge", "id"):
        found = product_of.get(_id_of((source or {}).get(key)))
        if found:
            return found
    scope = _from_metadata((source or {}).get("metadata"))
    return _product_of_scope(scope) if scope else OTHER


def _empty():
    return {"received": 0, "refunded": 0, "returned": 0, "taken_back": 0, "fees": 0, "net": 0}


def _stripe_books(start, end, product_of):
    """Stripe's books up to ``end``: the period from ``start`` by product, and the balance at ``end``."""
    bookings = [
        txn for txn in stripe.BalanceTransaction.list(
            created={"lt": end}, limit=100, expand=["data.source"]).auto_paging_iter()
        if (txn.get("currency") or "eur") == "eur"
    ]
    # Payments first, so a refund finds the payment it took back by its charge.
    for txn in bookings:
        source = txn.get("source")
        if txn.get("type") in RECEIVED and not isinstance(source, str) and source:
            product_of.setdefault(source.get("id"), _product_of_booking(source, product_of))

    by_product = {product: _empty() for product in PRODUCTS}
    account = {"stripe_fees": 0, "other": 0, "net": 0}
    books = {"by_product": by_product, "account": account, "paid_out": 0, "balance_at_end": 0}
    for txn in bookings:
        kind = txn.get("type")
        amount, fee, net = int(txn.get("amount") or 0), int(txn.get("fee") or 0), int(txn.get("net") or 0)
        books["balance_at_end"] += net
        if start is not None and int(txn.get("created") or 0) < start:
            continue
        if kind in PAID_OUT:
            books["paid_out"] -= net
            continue
        if kind == "stripe_fee":
            account["stripe_fees"] -= amount  # billed on its own, as a negative amount
            account["net"] += net
            continue
        if kind not in RECEIVED + REFUNDED + RETURNED + TAKEN_BACK:
            account["other"] += amount
            account["net"] += net
            continue
        totals = by_product[_product_of_booking(txn.get("source"), product_of)]
        totals["fees"] += fee
        totals["net"] += net
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


def _in_stripe_now():
    balance = stripe.Balance.retrieve()

    def eur(entries):
        return sum(int(entry.get("amount") or 0) for entry in entries or [] if entry.get("currency") == "eur")

    return {"available": eur(balance.get("available")), "pending": eur(balance.get("pending"))}


def _paid_in_period(invoices, start, end, product):
    """The period's paid invoices with money on them, for ``product``."""
    return {
        invoice_id: invoice for invoice_id, (invoice, found) in invoices.items()
        if found == product and invoice.get("status") == "paid" and int(invoice.get("amount_paid") or 0) > 0
        and (start is None or int(invoice.get("created") or 0) >= start) and int(invoice.get("created") or 0) < end
    }  # a EUR 0 invoice opens an October free period, or a trial


def _paid_in_stripe(invoice_id):
    """Whether Stripe shows this invoice as paid -- for one outside the period."""
    try:
        return stripe.Invoice.retrieve(invoice_id).get("status") == "paid"
    except stripe.InvalidRequestError:
        return False


def _who(invoice):
    return invoice.get("customer_email") or invoice.get("customer_name") or invoice.get("customer") or ""


def _check_teams(stripe_team, since, until):
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
        if payment.id in matched or not _in_period(payment.paid_at, since, until):
            continue
        if payment.stripe_invoice_id and _paid_in_stripe(payment.stripe_invoice_id):
            continue  # the invoice is from before the period; the money came in it
        problems.append({"what": "In the portal, not paid in Stripe", "invoice": payment.stripe_invoice_id
                         or payment.stripe_payment_intent_id, "amount": payment.amount_cents,
                         "who": payment.user.email if payment.user is not None else ""})
    return problems


def _check_membership(stripe_membership, since, until):
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
        if not _in_period(period.created_at, since, until) or period.stripe_invoice_id in stripe_membership:
            continue
        if period.revoked_at is not None or _paid_in_stripe(period.stripe_invoice_id):
            continue
        member = period.member
        problems.append({"what": "Membership period counted as paid, invoice not paid in Stripe",
                         "invoice": period.stripe_invoice_id, "amount": None,
                         "who": member.email_private if member is not None else ""})
    return problems


def owed_to_teams_on(until):
    """What the teams' members had paid by ``until``, less what was passed on to the teams by then."""
    earned = sum(
        _earned(payment) for payment in db.session.execute(
            db.select(Payment).filter_by(purpose=payments.PURPOSE_TEAM)).scalars()
        if _in_period(payment.paid_at, None, until)
    )
    passed_on = sum(
        payout.amount_cents for payout in db.session.execute(
            db.select(TeamPayout).where(TeamPayout.paid_on <= until)).scalars()
    )
    return earned - passed_on


def overview(since, until):
    """The money from ``since`` (None: from the first booking) to ``until``, both days
    included, in cents, with the check. Raises stripe.StripeError when Stripe cannot be asked."""
    payments.apply_runtime_stripe_config()
    start = _unix(since) if since else None
    end = _unix(until + timedelta(days=1))
    invoices = _invoices(end)
    books = _stripe_books(start, end, _products_of_payments(invoices))

    team = books["by_product"][TEAM]
    # The teams get what their members paid, less what was given back; the fees are the association's.
    teams_share = team["received"] - team["returned"] - team["refunded"] - team["taken_back"]
    in_stripe_now = _in_stripe_now() if until >= get_membership_today() else None
    return {
        "since": since,
        "until": until,
        "books": books,
        "in_stripe_now": in_stripe_now,
        "teams_share": teams_share,
        "association_own": books["total"]["net"] - teams_share,
        "owed_to_teams": owed_to_teams_on(until),
        "checks": {
            "teams": _check_teams(_paid_in_period(invoices, start, end, TEAM), since, until),
            "membership": _check_membership(_paid_in_period(invoices, start, end, MEMBERSHIP), since, until),
        },
        "checked_at": datetime.now(timezone.utc),
    }
