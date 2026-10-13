"""Stripe's books in the portal: every booking on the association's account.

Everything the association sells is paid into one Stripe account -- the
membership, the teams' fees, credit, and outside the portal the webshop and
events. Stripe keeps a book of it: one balance transaction per payment,
refund, returned debit, chargeback, fee it bills and transfer to the bank.
This module keeps a copy of that book (:class:`StripeBooking`), so the money
pages read the database instead of asking Stripe each time, and the check
against the portal's records (services/money_checks.py) runs after every
fetch. Plan and decisions: docs/money-ledger-plan.md.

**Only ever added to.** Stripe never changes a booking's amounts: a refund or
a chargeback is a booking of its own naming the payment it takes back. So a
fetch asks only for what is new since the last one, with a few days' overlap
for bookings Stripe dates back; the first fetch reads the whole book.

**When.** Right after a Stripe webhook (several in a row make one fetch: it
is one item on the outbox queue), and every hour as a safety net
(:func:`request_sync_if_due`). "Fetch from Stripe now" on Admin › Money does
the same at once.

**What each booking was for.** The payment it is, or takes back, decides:
- the portal's own record of it -- a team fee or a credit top-up is a
  :class:`Payment` with its payment and invoice;
- the invoice it paid: a membership period's, or a team subscription's;
- otherwise what Stripe says it is (services/stripe_scope.py): marked by the
  portal, or for the membership's product. Anything else -- the webshop,
  events -- is *other*.
A booking that could not be told yet (Stripe out of reach, or the portal's
record not written yet) is tried again on the next fetch.

**Debits not cleared yet.** Stripe books a SEPA debit when it is announced,
days before it clears; the portal records it only once it has cleared. Such
a booking is marked *pending*, and looked at again on each fetch -- the
webhook of the debit clearing starts one -- so the check waits for it.

**The balance.** The sum of every booking is what Stripe holds; after each
fetch the copy's sum is set beside Stripe's own figure, one request.
"""

from datetime import datetime, timedelta, timezone

import stripe
from flask import current_app

from ..db_models import ExternalWorkItem, MembershipPeriod, Payment, StripeBooking, StripeBooksState, db
from . import payments
from .clock import get_now_utc

MEMBERSHIP, TEAM, CREDIT, OTHER = "membership", "team", "credit", "other"
PRODUCTS = (MEMBERSHIP, TEAM, CREDIT, OTHER)
CURRENCY = "eur"

RECEIVED = ("charge", "payment")
REFUNDED = ("refund", "payment_refund")
# A SEPA debit announced and then not honoured: received, and handed back.
RETURNED = ("payment_failure_refund",)
TAKEN_BACK = ("adjustment", "payment_reversal")  # chargebacks and their reversals
PAID_OUT = ("payout", "payout_cancel", "payout_failure")
#: Bookings that belong to a payment: the payment itself, or what takes it back.
OF_A_PAYMENT = RECEIVED + REFUNDED + RETURNED + TAKEN_BACK

#: Fetched again on each fetch: Stripe may date a booking a little back.
OVERLAP = timedelta(days=3)
#: The safety net: fetched at least this often even without a webhook.
EVERY = timedelta(hours=1)
#: Bookings not told apart yet are tried again for this long after they were fetched,
#: then left as other.
SORT_RETRY = timedelta(days=14)

QUEUE_KEY = "stripe_books"
#: A long fetch is saved every so many bookings.
SAVE_EVERY = 200


def _aware(moment):
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _id_of(value):
    if not value:
        return None
    return value if isinstance(value, str) else value.get("id")


def state():
    """The one row saying how far the book was fetched."""
    found = db.session.get(StripeBooksState, 1)
    if found is None:
        found = StripeBooksState(id=1)
        db.session.add(found)
        db.session.flush()
    return found


# --- Asking for a fetch ------------------------------------------------------------------


def request_sync(reason=None):
    """Queue one fetch; folded into one already waiting. Started at once after the commit
    (services/outbox.py), so the money pages are current seconds after Stripe told us."""
    from .outbox import enqueue

    return enqueue(ExternalWorkItem.KIND_STRIPE_BOOKS, dedupe_key=QUEUE_KEY, reason=reason or "Fetch Stripe's books")


def request_sync_if_due(now=None):
    """The hourly safety net: queue a fetch when the last one is older than :data:`EVERY`."""
    if not payments_configured():
        return None
    now = now or get_now_utc()
    last = _aware(state().fetched_at)
    if last is not None and now - last < EVERY:
        return None
    return request_sync("Hourly fetch of Stripe's books")


def fetching():
    """Whether a fetch is waiting or under way."""
    return db.session.execute(
        db.select(ExternalWorkItem.id).where(
            ExternalWorkItem.kind == ExternalWorkItem.KIND_STRIPE_BOOKS,
            ExternalWorkItem.status.in_((ExternalWorkItem.STATUS_PENDING, ExternalWorkItem.STATUS_PROCESSING)),
        )
    ).first() is not None


def payments_configured():
    from .settings import get_stripe_settings_map
    from ..config import STRIPE_SECRET_KEY

    return bool(get_stripe_settings_map().get("stripe_secret_key") or STRIPE_SECRET_KEY)


#: Webhook events after which the book has something new.
WEBHOOK_EVENTS = (
    "charge.succeeded", "charge.refunded", "charge.refund.updated", "charge.failed",
    "charge.dispute.created", "charge.dispute.closed", "charge.dispute.funds_withdrawn",
    "charge.dispute.funds_reinstated", "invoice.paid", "payment_intent.succeeded",
    "checkout.session.completed", "checkout.session.async_payment_succeeded",
    "payout.paid", "payout.failed", "refund.created", "refund.updated",
)


def after_webhook(event_type):
    """Called for every webhook event handled: a fetch when the book has changed."""
    if event_type in WEBHOOK_EVENTS:
        request_sync(f"Stripe event {event_type}")


# --- Fetching ----------------------------------------------------------------------------


def sync(full=False, now=None):
    """Fetch what is new in Stripe's book, sort it, and compare the balance.

    Returns ``{"added": int, "sorted": int}``. Raises stripe.StripeError when
    Stripe cannot be asked -- the caller (the queue) tries again later.
    """
    payments.apply_runtime_stripe_config()
    now = now or get_now_utc()
    current = state()
    through = _aware(current.fetched_through)
    params = {"limit": 100, "expand": ["data.source"]}
    if through is not None and not full:
        params["created"] = {"gte": int((through - OVERLAP).timestamp())}
    known = set(db.session.execute(db.select(StripeBooking.stripe_id)).scalars())
    sorter = _Sorter()
    added, newest = _fetch(params, known, sorter, now, through)
    # A long fetch (the first reads the whole book) misses what Stripe booked
    # meanwhile: one more short pass for that, right before the balance is
    # compared, so the two describe the same moment.
    if newest is not None:
        late, newest = _fetch({"limit": 100, "expand": ["data.source"],
                               "created": {"gte": int((newest - timedelta(minutes=1)).timestamp())}},
                              known, sorter, now, newest)
        added += late
    db.session.flush()
    _check_pending()
    resorted = _sort_again(sorter, now)
    _fill_in_fees()
    current.fetched_through = newest or now
    current.last_error = None
    _compare_balance(current)
    # When it finished: what the page says it was fetched at.
    current.fetched_at = get_now_utc()
    return {"added": added, "sorted": resorted}


def _fetch(params, known, sorter, now, newest):
    """Add every booking Stripe lists for ``params`` not in the copy yet. Returns (added, newest)."""
    added = 0
    for txn in stripe.BalanceTransaction.list(**params).auto_paging_iter():
        booked = datetime.fromtimestamp(int(txn.get("created") or 0), timezone.utc)
        newest = booked if newest is None or booked > newest else newest
        if txn.get("id") in known or (txn.get("currency") or CURRENCY) != CURRENCY:
            continue
        booking = _booking(txn, booked, now)
        sorter.sort(booking, txn.get("source"))
        db.session.add(booking)
        known.add(booking.stripe_id)
        added += 1
        if added % SAVE_EVERY == 0:
            # The first fetch reads the whole book: what is fetched stays fetched if it stops halfway.
            db.session.commit()
    return added, newest


def _booking(txn, booked, now):
    source = txn.get("source")
    charge_id, payment_intent_id, pending = None, None, False
    if txn.get("type") in OF_A_PAYMENT and source and not isinstance(source, str):
        kind = source.get("object")
        charge_id = source.get("id") if kind == "charge" else _id_of(source.get("charge"))
        payment_intent_id = _id_of(source.get("payment_intent"))
        pending = txn.get("type") in RECEIVED and kind == "charge" and source.get("status") == "pending"
    return StripeBooking(
        stripe_id=txn.get("id"), type=txn.get("type") or "", amount_cents=int(txn.get("amount") or 0),
        fee_cents=int(txn.get("fee") or 0), net_cents=int(txn.get("net") or 0),
        currency=(txn.get("currency") or CURRENCY)[:3], booked_at=booked,
        charge_id=charge_id, payment_intent_id=payment_intent_id, pending=pending, fetched_at=now,
    )


def _check_pending():
    """Debits booked before they cleared: has each cleared, or failed, since? One request per payment,
    and only while some are pending -- a SEPA debit clears within days. Stripe out of reach: next time."""
    for booking in db.session.execute(
        db.select(StripeBooking).where(StripeBooking.pending.is_(True))
    ).scalars().all():
        try:
            charge = stripe.Charge.retrieve(booking.charge_id)
        except stripe.StripeError as exc:
            current_app.logger.info("Stripe's book: charge %s not looked at again: %s", booking.charge_id, exc)
            continue
        booking.pending = charge.get("status") == "pending"


def _compare_balance(current):
    balance = stripe.Balance.retrieve()

    def eur(entries):
        return sum(int(entry.get("amount") or 0) for entry in entries or [] if entry.get("currency") == CURRENCY)

    # Available and incoming, and what was moved to the Issuing balance (cards
    # Stripe issues): moving it there is a booking of the book, so it counts.
    issuing = balance.get("issuing") or {}
    current.stripe_balance_cents = (eur(balance.get("available")) + eur(balance.get("pending"))
                                    + eur(issuing.get("available")))
    current.books_balance_cents = int(db.session.execute(
        db.select(db.func.coalesce(db.func.sum(StripeBooking.net_cents), 0))
    ).scalar() or 0)


def _sort_again(sorter, now):
    """Bookings of a payment still not told apart, or not matched to the portal's record:
    tried again, as the record may have been written since."""
    waiting = db.session.execute(
        db.select(StripeBooking).where(
            StripeBooking.type.in_(OF_A_PAYMENT),
            db.or_(StripeBooking.product.is_(None),
                   db.and_(StripeBooking.product.in_((TEAM, CREDIT)), StripeBooking.payment_id.is_(None))),
            StripeBooking.fetched_at >= now - SORT_RETRY,
        )
    ).scalars().all()
    for booking in waiting:
        sorter.sort(booking, None)
    # Told to be other, but the portal has written its record of the payment since: matched
    # here, without asking Stripe again.
    for booking in db.session.execute(
        db.select(StripeBooking).where(StripeBooking.type.in_(OF_A_PAYMENT), StripeBooking.product == OTHER,
                                       StripeBooking.charge_id.is_not(None),
                                       StripeBooking.fetched_at >= now - SORT_RETRY)
    ).scalars().all():
        sorter.match_locally(booking)
    # Anything older that still could not be told is left as other, for the totals.
    for booking in db.session.execute(
        db.select(StripeBooking).where(StripeBooking.type.in_(OF_A_PAYMENT), StripeBooking.product.is_(None),
                                       StripeBooking.fetched_at < now - SORT_RETRY)
    ).scalars():
        booking.product = OTHER
    return len(waiting)


def _fill_in_fees():
    """Each payment's fee from its booking: what Stripe kept when the money arrived."""
    for payment, fee in db.session.execute(
        db.select(Payment, StripeBooking.fee_cents).join(StripeBooking, StripeBooking.payment_id == Payment.id)
        .where(StripeBooking.type.in_(RECEIVED), Payment.fee_cents.is_(None))
    ).all():
        payment.fee_cents = int(fee or 0)


# --- What a booking was for -----------------------------------------------------------


class _Sorter:
    """Sorts bookings by the payment they belong to, asking Stripe at most once per payment."""

    def __init__(self):
        self._by_charge = {}

    def sort(self, booking, source):
        if booking.type not in OF_A_PAYMENT:
            return  # Stripe's own fees, transfers to the bank: the account's, not a product's
        if not booking.charge_id:
            booking.product = booking.product or OTHER
            return
        known = self._known(booking.charge_id)
        if known is None:
            known = self._find(booking, source)
            if known is None:
                return  # Stripe out of reach: next time
            self._by_charge[booking.charge_id] = known
        product, invoice_id, payment = known
        booking.product = product
        booking.invoice_id = booking.invoice_id or invoice_id
        if payment is not None:
            booking.payment_id = payment.id

    def match_locally(self, booking):
        """The portal's record of the payment, by its payment or its invoice, when there is one now."""
        payment = _payment_by_intent(booking.payment_intent_id)
        if payment is None and booking.invoice_id:
            payment = db.session.execute(
                db.select(Payment).filter_by(stripe_invoice_id=booking.invoice_id)
            ).scalars().first()
        if payment is None:
            payment = self._payment_of_earlier(booking.charge_id)
        if payment is not None:
            booking.product = payment.purpose if payment.purpose in PRODUCTS else OTHER
            booking.payment_id = payment.id
            self._by_charge[booking.charge_id] = (booking.product, booking.invoice_id, payment)

    def _payment_of_earlier(self, charge_id):
        """A refund's payment found through the payment booking it takes back."""
        found = db.session.execute(
            db.select(StripeBooking).where(StripeBooking.charge_id == charge_id, StripeBooking.type.in_(RECEIVED))
        ).scalars().first()
        return _payment_by_intent(found.payment_intent_id) if found is not None else None

    def _known(self, charge_id):
        if charge_id in self._by_charge:
            return self._by_charge[charge_id]
        # Sorted already with an earlier booking of the same payment, and matched.
        earlier = db.session.execute(
            db.select(StripeBooking).where(StripeBooking.charge_id == charge_id, StripeBooking.product.is_not(None))
            .order_by(StripeBooking.id)
        ).scalars().first()
        if earlier is not None and (earlier.payment_id or earlier.product in (MEMBERSHIP, OTHER)):
            found = (earlier.product, earlier.invoice_id, earlier.payment)
            self._by_charge[charge_id] = found
            return found
        return None

    def _find(self, booking, source):
        """(product, invoice id, the portal's Payment or None); None when Stripe could not be asked."""
        payment = _payment_by_intent(booking.payment_intent_id)
        if payment is not None:
            return payment.purpose if payment.purpose in PRODUCTS else OTHER, payment.stripe_invoice_id, payment
        try:
            invoice_id = _invoice_of(booking)
        except stripe.StripeError as exc:
            current_app.logger.info("Stripe's book: the invoice of %s not found yet: %s", booking.charge_id, exc)
            return None
        if invoice_id:
            payment = db.session.execute(
                db.select(Payment).filter_by(stripe_invoice_id=invoice_id)
            ).scalars().first()
            if payment is not None:
                return payment.purpose if payment.purpose in PRODUCTS else OTHER, invoice_id, payment
            if db.session.execute(
                db.select(MembershipPeriod.id).filter_by(stripe_invoice_id=invoice_id)
            ).first() is not None:
                return MEMBERSHIP, invoice_id, None
        product = _product_by_scope(source, booking.charge_id)
        if product is None:
            return None
        return product, invoice_id, None


def _payment_by_intent(payment_intent_id):
    if not payment_intent_id:
        return None
    return db.session.execute(
        db.select(Payment).filter_by(stripe_payment_intent_id=payment_intent_id)
    ).scalars().first()


def _invoice_of(booking):
    from .stripe_scope import invoice_id_of_payment_intent

    return invoice_id_of_payment_intent(booking.payment_intent_id) if booking.payment_intent_id else None


def _product_by_scope(source, charge_id):
    """What Stripe says the payment is: the portal's mark, or the membership's product."""
    from .stripe_scope import scope_of_charge

    charge = source if source is not None and not isinstance(source, str) and source.get("object") == "charge" \
        else None
    try:
        if charge is None:
            charge = stripe.Charge.retrieve(charge_id)
    except stripe.StripeError as exc:
        current_app.logger.info("Stripe's book: charge %s not found yet: %s", charge_id, exc)
        return None
    scope = scope_of_charge(charge)
    if scope.is_unreachable:
        return None
    if scope.is_ours:
        return MEMBERSHIP
    if scope.purpose in (TEAM, CREDIT):
        return scope.purpose
    return OTHER


# --- Reading the copy ---------------------------------------------------------------------


def bookings_of_payment(payment):
    """Every booking of one of the portal's payments: the payment, and what took it back."""
    if payment is None or payment.id is None:
        return []
    return db.session.execute(
        db.select(StripeBooking).where(StripeBooking.payment_id == payment.id).order_by(StripeBooking.booked_at,
                                                                                         StripeBooking.id)
    ).scalars().all()


def fees_of_payments(payment_ids):
    """{payment id: {"fee": cents kept on the payment, "later_fees": on what took it back}}."""
    found = {}
    if not payment_ids:
        return found
    for booking in db.session.execute(
        db.select(StripeBooking).where(StripeBooking.payment_id.in_(list(payment_ids)))
    ).scalars():
        row = found.setdefault(booking.payment_id, {"fee": None, "later_fees": 0, "bookings": []})
        row["bookings"].append(booking)
        if booking.type in RECEIVED:
            row["fee"] = (row["fee"] or 0) + booking.fee_cents
        else:
            row["later_fees"] += booking.fee_cents
    return found


# --- The queue's handler -----------------------------------------------------------------


def _handle_fetch(_item):
    """One fetch, then the check, then the email about differences that have lasted."""
    from . import money_checks

    try:
        sync()
    except stripe.StripeError as exc:
        db.session.rollback()
        state().last_error = str(exc)[:500]
        db.session.commit()
        raise
    money_checks.run()
    money_checks.email_new()


def fetch_now():
    """"Fetch from Stripe now" on Admin › Money: the same, at once, in the request."""
    _handle_fetch(None)


from .outbox import register_handler  # noqa: E402 -- the queue knows its handlers by registration

register_handler(ExternalWorkItem.KIND_STRIPE_BOOKS, _handle_fetch)
