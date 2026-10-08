"""Credit: a balance members top up and spend with the association.

Prepared for coffee and drinks; nothing spends it yet (:func:`spend` is there
for whatever will). The plan and what was decided: docs/credit-plan.md.

**The switch.** Off, nobody sees credit and nobody can top up; balances and
history stay, and a top-up already paid is still added -- the money arrived.

**Topping up** is for active members of the association, through Stripe
Checkout on the one product "Credit", for the amount picked in the portal:
at least the smallest top-up, and never past the most a person may hold.
One top-up is open in Checkout at a time, so that limit holds. Card (Apple
and Google Pay with it) and, if switched on, EPS -- no SEPA direct debit,
which arrives days later and can be taken back for weeks. The money counts
only once Stripe says it is there.

**The ledger.** Every change is one :class:`CreditEntry`, never changed or
deleted; a mistake is put right by another. The balance is kept on the
person's :class:`CreditAccount` too, changed under its row lock in the same
transaction as the entry, so two changes at once wait for each other.

**Giving it back.** A refund goes to the payment a top-up came from -- Stripe
refuses more than is left of it and sends it nowhere else -- newest top-up
first. Cash is not refunded through Stripe: the treasurer pays it out by
hand and books that. Refunds made in Stripe's dashboard and lost chargebacks
come off the balance through the webhook, once each.

The webhook events arrive marked ``purpose: credit`` (services/payments.py).
"""

from dataclasses import dataclass
from datetime import timedelta, timezone

from flask import current_app
from sqlalchemy.exc import IntegrityError

from ..db_models import CreditAccount, CreditEntry, Payment, User, db
from ..security_utils import build_public_url
from . import ConflictError, ExternalServiceError, NotFoundError, ValidationError
from . import payments
from .audit import log_audit_event
from .clock import get_now_utc
from .locking import locked
from .settings import get_settings_map

PURPOSE = payments.PURPOSE_CREDIT
CURRENCY = "eur"

TOP_UP = "top_up"
CASH_IN = "cash_in"
CASH_OUT = "cash_out"
PURCHASE = "purchase"
REFUND = "refund"
REVERSAL = "reversal"
CORRECTION = "correction"
KINDS = (TOP_UP, CASH_IN, CASH_OUT, PURCHASE, REFUND, REVERSAL, CORRECTION)

SETTING_ENABLED = "credit_enabled"
SETTING_PRODUCT = "credit_stripe_product_id"
SETTING_MIN_TOP_UP = "credit_min_top_up_cents"
SETTING_MAX_BALANCE = "credit_max_balance_cents"
SETTING_SUGGESTED = "credit_suggested_cents"
SETTING_EPS = "credit_eps_enabled"
SETTING_KEYS = (SETTING_ENABLED, SETTING_PRODUCT, SETTING_MIN_TOP_UP, SETTING_MAX_BALANCE, SETTING_SUGGESTED,
                SETTING_EPS)

DEFAULT_MIN_TOP_UP = 1000
DEFAULT_MAX_BALANCE = 2000
DEFAULT_SUGGESTED = (1000, 1500, 2000)
# Below a euro Stripe's fee is most of it; above 500 € this is not coffee any more.
LEAST_TOP_UP = 100
MOST_BALANCE = 50000
MOST_SUGGESTED = 4
# What one booking by hand may move: a cash top-up, a payout, a correction.
MOST_BOOKING = MOST_BALANCE


# --- Settings ------------------------------------------------------------------------


@dataclass(frozen=True)
class CreditSettings:
    enabled: bool
    product_id: str | None
    min_top_up_cents: int
    max_balance_cents: int
    suggested_cents: tuple
    eps: bool


def _cents(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _suggested(value):
    if not value:
        return DEFAULT_SUGGESTED
    found = []
    for part in str(value).split(","):
        cents = _cents(part.strip(), None)
        if cents is not None and cents not in found:
            found.append(cents)
    return tuple(sorted(found)) or DEFAULT_SUGGESTED


def settings():
    stored = get_settings_map(SETTING_KEYS)
    return CreditSettings(
        enabled=stored.get(SETTING_ENABLED) == "True",
        product_id=stored.get(SETTING_PRODUCT) or None,
        min_top_up_cents=_cents(stored.get(SETTING_MIN_TOP_UP), DEFAULT_MIN_TOP_UP),
        max_balance_cents=_cents(stored.get(SETTING_MAX_BALANCE), DEFAULT_MAX_BALANCE),
        suggested_cents=_suggested(stored.get(SETTING_SUGGESTED)),
        eps=stored.get(SETTING_EPS) == "True",
    )


def enabled():
    return settings().enabled


def _refuse(field, message, code="credit_settings_invalid"):
    raise ValidationError(message, code=code, details={"fields": {field: message}})


def save_settings(actor, *, enabled, product_id, min_top_up_cents, max_balance_cents, suggested_cents, eps):
    """Switch credit on or off and set its limits. Returns the keys that changed.

    A new product is checked with Stripe. Switching on needs one.
    """
    from .settings_sections import _flag, _write

    product_id = (product_id or "").strip() or None
    if product_id is not None and not product_id.startswith("prod_"):
        _refuse("product_id", "A Stripe product ID starts with prod_.")
    if enabled and product_id is None:
        _refuse("product_id", "Credit needs its Stripe product before it can be switched on.")
    if not LEAST_TOP_UP <= min_top_up_cents <= MOST_BALANCE:
        _refuse("min_top_up_cents", "The smallest top-up is between 1 € and 500 €.")
    if not min_top_up_cents <= max_balance_cents <= MOST_BALANCE:
        _refuse("max_balance_cents", "The most a person may hold is at least the smallest top-up, and at most 500 €.")
    suggested = sorted(set(int(cents) for cents in suggested_cents or ()))
    if not suggested:
        _refuse("suggested_cents", "Suggest at least one amount.")
    if len(suggested) > MOST_SUGGESTED:
        _refuse("suggested_cents", f"At most {MOST_SUGGESTED} suggested amounts.")
    if any(not min_top_up_cents <= cents <= max_balance_cents for cents in suggested):
        _refuse("suggested_cents", "Every suggested amount lies between the smallest top-up and the most a person "
                                   "may hold.")
    if product_id is not None and product_id != settings().product_id:
        _check_product(product_id)
    return _write(actor, "credit", {
        SETTING_ENABLED: _flag(enabled),
        SETTING_PRODUCT: product_id,
        SETTING_MIN_TOP_UP: str(min_top_up_cents) if min_top_up_cents != DEFAULT_MIN_TOP_UP else None,
        SETTING_MAX_BALANCE: str(max_balance_cents) if max_balance_cents != DEFAULT_MAX_BALANCE else None,
        SETTING_SUGGESTED: ",".join(str(cents) for cents in suggested)
        if tuple(suggested) != DEFAULT_SUGGESTED else None,
        SETTING_EPS: _flag(eps),
    })


def _check_product(product_id):
    import stripe

    try:
        product = payments.retrieve_product(product_id)
    except stripe.InvalidRequestError:
        _refuse("product_id", "Stripe knows no product with this ID.")
    except stripe.StripeError as exc:
        raise ExternalServiceError("Stripe could not be asked about the product. Try again in a moment.",
                                   code="credit_product_unchecked", details={"reason": str(exc)}) from exc
    if not product.get("active"):
        _refuse("product_id", "This product is archived in Stripe.")


# --- Reading ---------------------------------------------------------------------------


def _account(user_id, *, lock=True, create=True):
    """The person's credit account, locked until commit; made the first time."""
    select = db.select(CreditAccount).filter_by(user_id=user_id)
    account = db.session.execute(locked(select) if lock else select).scalars().first()
    if account is not None or not create:
        return account
    try:
        with db.session.begin_nested():
            db.session.add(CreditAccount(user_id=user_id, balance_cents=0))
    except IntegrityError:
        pass  # made at the same moment by another request
    return db.session.execute(locked(select) if lock else select).scalars().first()


def balance_of(user):
    account = _account(user.id, lock=False, create=False) if user is not None else None
    return account.balance_cents if account is not None else 0


def has_credit(user):
    """Whether this person ever had credit: they keep seeing it, member or not."""
    return user is not None and _account(user.id, lock=False, create=False) is not None


def may_top_up(user):
    """Why this person cannot top up now, or None when they can."""
    from .teams import is_active_association_member

    current = settings()
    if not current.enabled:
        return "Credit is switched off."
    if not current.product_id:
        return "Topping up is not set up yet."
    if not is_active_association_member(user):
        return "Topping up is for members of the association."
    if top_up_room(user) < current.min_top_up_cents:
        return (f"You can top up again once your credit is below "
                f"{_euros(current.max_balance_cents - current.min_top_up_cents)}.")
    return None


def top_up_room(user):
    """How much more this person may hold."""
    return max(0, settings().max_balance_cents - balance_of(user))


def top_up_choices(user):
    """The suggested amounts that fit, smallest first."""
    current = settings()
    room = top_up_room(user)
    return [cents for cents in current.suggested_cents if current.min_top_up_cents <= cents <= room]


def history(user, limit=None):
    query = db.select(CreditEntry).filter_by(user_id=user.id).order_by(CreditEntry.id.desc())
    if limit:
        query = query.limit(limit)
    return db.session.execute(query).scalars().all()


def _euros(cents):
    return payments.format_amount(cents, CURRENCY)


# --- Booking ---------------------------------------------------------------------------


def _book(account, kind, amount_cents, description, *, payment=None, team=None, by=None):
    account.balance_cents += amount_cents
    account.updated_at = get_now_utc()
    entry = CreditEntry(
        user_id=account.user_id, kind=kind, amount_cents=amount_cents,
        balance_after_cents=account.balance_cents, description=description[:140],
        payment=payment, team=team, booked_by=by, created_at=get_now_utc(),
    )
    db.session.add(entry)
    log_audit_event("credit", f"credit_{kind}", actor_user=by, target_user=account.user, metadata={
        "amount_cents": amount_cents, "balance_cents": account.balance_cents,
        "payment_id": payment.id if payment is not None and payment.id else None,
        "team_id": team.id if team is not None else None,
    })
    return entry


def _amount(amount_cents, field="amount_cents"):
    try:
        amount = int(amount_cents)
    except (TypeError, ValueError):
        _refuse(field, "Enter an amount.", code="credit_amount_invalid")
    if amount <= 0:
        _refuse(field, "Enter an amount above zero.", code="credit_amount_invalid")
    if amount > MOST_BOOKING:
        _refuse(field, f"At most {_euros(MOST_BOOKING)} at once.", code="credit_amount_invalid")
    return amount


def _note(note, default, *, required=False):
    note = " ".join((note or "").split())[:140]
    if required and not note:
        _refuse("note", "Say why.", code="credit_note_missing")
    return note or default


def book_cash(actor, user, amount_cents, *, paid_out=False, note=None):
    """Cash handed to the treasurer, added to the credit -- or credit paid out in cash or by transfer."""
    amount = _amount(amount_cents)
    account = _account(user.id)
    if paid_out:
        if amount > account.balance_cents:
            _refuse("amount_cents", f"The credit is only {_euros(max(account.balance_cents, 0))}.",
                    code="credit_not_enough")
        return _book(account, CASH_OUT, -amount, _note(note, "Paid out"), by=actor)
    if account.balance_cents + amount > settings().max_balance_cents:
        _refuse("amount_cents", f"That would be more than the most a person may hold "
                                f"({_euros(settings().max_balance_cents)}).", code="credit_above_most")
    return _book(account, CASH_IN, amount, _note(note, "Cash top-up"), by=actor)


def correct(actor, user, amount_cents, *, note):
    """Put a mistake right, up or down, with the reason. Never below zero."""
    try:
        amount = int(amount_cents)
    except (TypeError, ValueError):
        amount = 0
    if amount == 0:
        _refuse("amount_cents", "Enter an amount other than zero.", code="credit_amount_invalid")
    if abs(amount) > MOST_BOOKING:
        _refuse("amount_cents", f"At most {_euros(MOST_BOOKING)} at once.", code="credit_amount_invalid")
    reason = _note(note, "", required=True)
    account = _account(user.id)
    if account.balance_cents + amount < 0:
        _refuse("amount_cents", "A correction cannot take the credit below zero.", code="credit_not_enough")
    return _book(account, CORRECTION, amount, reason, by=actor)


def spend(user, amount_cents, description, *, team=None, by=None):
    """Take something bought off the credit; refused when there is not enough.

    Nothing calls this yet: it is what a coffee machine or a shop will.
    """
    if not enabled():
        raise ConflictError("Credit is switched off.", code="credit_off")
    amount = _amount(amount_cents)
    account = _account(user.id)
    if account.balance_cents < amount:
        raise ConflictError(f"Not enough credit: {_euros(max(account.balance_cents, 0))} left.",
                            code="credit_not_enough")
    return _book(account, PURCHASE, -amount, description, team=team, by=by)


# --- Topping up ------------------------------------------------------------------------


def start_top_up(user, amount_cents):
    """Open Stripe Checkout for this amount; returns its address.

    One top-up is open at a time: the same amount again is the same page, a
    different one closes the old one first.
    """
    reason = may_top_up(user)
    if reason:
        raise ConflictError(reason, code="credit_top_up_refused")
    current = settings()
    amount = _amount(amount_cents)
    account = _account(user.id)
    room = max(0, current.max_balance_cents - account.balance_cents)
    if amount < current.min_top_up_cents:
        _refuse("amount_cents", f"Top up at least {_euros(current.min_top_up_cents)}.", code="credit_below_least")
    if amount > room:
        _refuse("amount_cents", f"You can top up at most {_euros(room)}: the most anyone holds is "
                                f"{_euros(current.max_balance_cents)}.", code="credit_above_most")
    if account.stripe_checkout_session_id:
        open_session = payments.open_checkout_session(account.stripe_checkout_session_id, what="credit Checkout")
        if open_session is not None and account.checkout_amount_cents == amount:
            return open_session["url"]
        if open_session is not None:
            payments.expire_checkout_session(account.stripe_checkout_session_id)
        account.stripe_checkout_session_id = None
        account.checkout_amount_cents = None

    member = user.member
    metadata = {"purpose": PURPOSE, "user_id": str(user.id), "amount_cents": str(amount)}
    description = f"Credit top-up, {_euros(amount)}"
    customer = payments.customer_params(member)
    if "customer_email" in customer:
        customer["customer_creation"] = "always"
    params = dict(
        mode="payment",
        metadata=metadata,
        payment_method_types=["card", "eps"] if current.eps else ["card"],
        line_items=[{"quantity": 1, "price_data": {
            "currency": CURRENCY, "product": current.product_id, "unit_amount": amount,
        }}],
        payment_intent_data={"metadata": metadata, "description": description},
        # A receipt the member can keep, from Stripe.
        invoice_creation={"enabled": True, "invoice_data": {"metadata": metadata, "description": description}},
        custom_text={"submit": {"message": f"{_euros(amount)} is added to your credit as soon as the payment is "
                                           "through."}},
        success_url=build_public_url("account.credit_page", topped_up=1),
        cancel_url=build_public_url("account.credit_page"),
        **customer,
    )
    key = f"checkout:credit:{user.id}:{account.balance_cents}:{payments.request_fingerprint(params)}"
    session = payments.create_checkout_session(params, idempotency_key=key)
    account.stripe_checkout_session_id = session.get("id")
    account.checkout_amount_cents = amount
    log_audit_event("credit", "credit_top_up_started", actor_user=user, target_user=user,
                    metadata={"amount_cents": amount})
    return session["url"]


def _id(value):
    return value if isinstance(value, str) else (value or {}).get("id")


def _user_of(metadata):
    user_id = _cents((metadata or {}).get("user_id"), None)
    return db.session.get(User, user_id) if user_id is not None else None


def _tell_admins(event_type, text, **payload):
    from ..notification_service import ADMIN_ERROR_CHANNEL
    from .notifications import queue_curated_admin_notification

    queue_curated_admin_notification(ADMIN_ERROR_CHANNEL, event_type, text, payload=payload, severity="warning")


def _session_done(session):
    """The Checkout is over, paid or not: the next top-up opens a new one."""
    user = _user_of(session.get("metadata"))
    if user is None:
        return None
    account = _account(user.id)
    if account.stripe_checkout_session_id == session.get("id"):
        account.stripe_checkout_session_id = None
        account.checkout_amount_cents = None
    return account


def _checkout_completed(session):
    account = _session_done(session)
    if account is None:
        _tell_admins("credit_payment_unmatched", "A completed credit top-up matches no account. Refund it in Stripe.",
                     session_id=session.get("id"))
        return
    payments.remember_customer(getattr(account.user, "member", None), session.get("customer"))
    if session.get("payment_status") == "paid":
        _paid(session, account)
    # Otherwise the money is on its way; the event below says when it arrived.


def _async_succeeded(session):
    account = _session_done(session)
    if account is not None:
        _paid(session, account)


def _async_failed(session):
    account = _session_done(session)
    if account is not None:
        log_audit_event("credit", "credit_top_up_failed", target_user=account.user,
                        metadata={"session_id": session.get("id")})


def _expired(session):
    _session_done(session)


def _paid(session, account):
    """The money is in: recorded once, and added to the credit."""
    payment_intent = _id(session.get("payment_intent"))
    if payment_intent and db.session.execute(
        db.select(Payment.id).filter_by(stripe_payment_intent_id=payment_intent)
    ).first() is not None:
        return  # reported twice
    amount = int(session.get("amount_total") or 0)
    currency = (session.get("currency") or CURRENCY)[:3].lower()
    if amount <= 0 or currency != CURRENCY:
        _tell_admins("credit_payment_odd", "A credit top-up was paid in an unexpected amount or currency and was not "
                     "added. Check it in Stripe.", session_id=session.get("id"), amount=amount, currency=currency)
        return
    payment = Payment(
        purpose=PURPOSE, user_id=account.user_id, stripe_payment_intent_id=payment_intent,
        stripe_invoice_id=_id(session.get("invoice")), amount_cents=amount, currency=currency,
        paid_at=get_now_utc(),
    )
    db.session.add(payment)
    db.session.flush()
    _book(account, TOP_UP, amount, "Top-up", payment=payment)


def _payment_of_charge(charge, payment_intent=None):
    from .team_payments import _payment_of_charge as by_charge

    payment = by_charge(charge, payment_intent)
    return payment if payment is not None and payment.purpose == PURPOSE else None


def _locked_payment(payment_id):
    return db.session.execute(locked(db.select(Payment).filter_by(id=payment_id))).scalars().first()


def _charge_refunded(charge):
    """A refund made in Stripe -- here, or in its dashboard -- comes off the credit, once."""
    found = _payment_of_charge(charge)
    if found is None:
        return
    account = _account(found.user_id)  # first, as every change of the credit does
    payment = _locked_payment(found.id)
    refunded = min(int(charge.get("amount_refunded") or 0), payment.amount_cents)
    given_back = refunded - payment.refunded_cents
    if given_back <= 0:
        return  # booked when it was made here, or reported twice
    payment.refunded_cents = refunded
    _book(account, REFUND, -given_back, "Refunded", payment=payment)


def _dispute_closed(dispute):
    """A chargeback lost: the money went back to the payer, and off the credit."""
    if dispute.get("status") != "lost":
        return
    import stripe

    payments.apply_runtime_stripe_config()
    charge = stripe.Charge.retrieve(dispute.get("charge"))
    found = _payment_of_charge(charge, dispute.get("payment_intent"))
    if found is None:
        _tell_admins("credit_dispute_unmatched", "A lost chargeback on a credit top-up matches no recorded payment.",
                     charge_id=dispute.get("charge"))
        return
    account = _account(found.user_id)
    payment = _locked_payment(found.id)
    if payment.status == "disputed":
        return
    payment.status = "disputed"
    lost = min(int(dispute.get("amount") or payment.amount_cents), payment.amount_cents - payment.refunded_cents)
    if lost > 0:
        _book(account, REVERSAL, -lost, "Payment taken back by the bank", payment=payment)


_HANDLERS = {
    "checkout.session.completed": _checkout_completed,
    "checkout.session.async_payment_succeeded": _async_succeeded,
    "checkout.session.async_payment_failed": _async_failed,
    "checkout.session.expired": _expired,
    "charge.refunded": _charge_refunded,
    "charge.dispute.closed": _dispute_closed,
}


def handle_event(event):
    """A webhook event marked ``purpose: credit``; see services/payments.py."""
    handler = _HANDLERS.get(event["type"])
    if handler is None:
        return "Not needed for credit", 200
    try:
        handler(event["data"]["object"])
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Credit event %s (%s) failed.", event.get("id"), event["type"])
        return "Credit event failed; deliver it again", 500
    return "Success", 200


# --- Giving it back --------------------------------------------------------------------


def _refundable(user_id):
    """The person's top-ups that can still be given back, newest first, with how much of each."""
    found = db.session.execute(
        db.select(Payment).filter_by(user_id=user_id, purpose=PURPOSE, status="paid")
        .where(Payment.stripe_payment_intent_id.is_not(None)).order_by(Payment.paid_at.desc(), Payment.id.desc())
    ).scalars().all()
    return [(payment, payment.amount_cents - payment.refunded_cents) for payment in found
            if payment.amount_cents > payment.refunded_cents]


def refundable_cents(user):
    """How much of the credit can go back through Stripe now: no more than the balance."""
    balance = balance_of(user)
    if balance <= 0:
        return 0
    return min(balance, sum(left for _payment, left in _refundable(user.id)))


def refund_balance(user, *, by=None):
    """Give the credit back to the payments it came from, newest first.

    Returns ``(refunded, left)`` in cents: what went back, and what is still
    on the account -- cash, or a payment Stripe would not refund any more --
    for the treasurer to pay out by hand. Each refund is booked as it is
    made, so the webhook reporting it later books nothing more.

    Stripe out of reach midway raises, after booking what was refunded so
    far; the caller commits that (``exc.details["refunded_cents"]``).
    """
    import stripe

    account = _account(user.id)
    left = max(account.balance_cents, 0)
    refunded = 0
    for found, _open in _refundable(user.id):
        if left <= 0:
            break
        payment = _locked_payment(found.id)
        amount = min(left, payment.amount_cents - payment.refunded_cents)
        if amount <= 0:
            continue
        key = f"credit-refund:{payment.id}:{payment.refunded_cents}:{amount}"
        try:
            payments.refund(payment.stripe_payment_intent_id, amount, idempotency_key=key,
                            metadata={"purpose": PURPOSE, "user_id": str(user.id)})
        except stripe.InvalidRequestError as exc:
            # Too old, or refused for this payment: the next one may still go.
            current_app.logger.warning("Credit refund on payment %s refused: %s", payment.id, exc)
            continue
        except stripe.StripeError as exc:
            raise ExternalServiceError(
                f"Stripe could not be reached: {_euros(refunded)} was refunded, the rest was not. Try again in a "
                "moment.", code="credit_refund_failed", details={"reason": str(exc), "refunded_cents": refunded},
            ) from exc
        payment.refunded_cents += amount
        _book(account, REFUND, -amount, "Refunded", payment=payment, by=by)
        left -= amount
        refunded += amount
    return refunded, max(account.balance_cents, 0)


def before_erasure(user, actor=None):
    """An account being erased gets its credit back; what cannot go back through Stripe the admins hear of."""
    if not has_credit(user):
        return 0, 0
    refunded, left = refund_balance(user, by=actor)
    if left > 0:
        _tell_admins("credit_left_at_erasure",
                     f"An erased account still had {_euros(left)} of credit that could not be refunded through Stripe "
                     "(cash, or too old). Pay it out by hand and book it under Admin › Credit.",
                     user_id=user.id, left_cents=left)
    return refunded, left


# --- The association's view ------------------------------------------------------------


def receipt_url(entry):
    """Stripe's receipt for a top-up, or None."""
    import stripe

    if entry.kind != TOP_UP or entry.payment is None or not entry.payment.stripe_invoice_id:
        return None
    try:
        invoice = payments.retrieve_invoice(entry.payment.stripe_invoice_id)
    except stripe.StripeError as exc:
        current_app.logger.warning("Receipt of credit entry %s not found: %s", entry.id, exc)
        return None
    return invoice.get("hosted_invoice_url")


def entry_of(user, entry_id):
    entry = db.session.get(CreditEntry, entry_id)
    if entry is None or (user is not None and entry.user_id != user.id):
        raise NotFoundError("No such entry.", code="credit_entry_not_found")
    return entry


def totals():
    """What the association holds for its members, and what came in in the last 30 days."""
    held = db.session.execute(db.select(db.func.coalesce(db.func.sum(CreditAccount.balance_cents), 0))).scalar()
    since = get_now_utc() - timedelta(days=30)
    came_in = db.session.execute(
        db.select(db.func.coalesce(db.func.sum(CreditEntry.amount_cents), 0))
        .where(CreditEntry.kind.in_((TOP_UP, CASH_IN)), CreditEntry.created_at >= since)
    ).scalar()
    spent = db.session.execute(
        db.select(db.func.coalesce(db.func.sum(CreditEntry.amount_cents), 0))
        .where(CreditEntry.kind == PURCHASE, CreditEntry.created_at >= since)
    ).scalar()
    people = db.session.execute(
        db.select(db.func.count()).select_from(CreditAccount).where(CreditAccount.balance_cents != 0)
    ).scalar()
    return {"held_cents": int(held or 0), "came_in_cents": int(came_in or 0), "spent_cents": -int(spent or 0),
            "people": int(people or 0)}


def accounts():
    """Everybody who has or had credit, the largest balance first."""
    return db.session.execute(
        db.select(CreditAccount).order_by(CreditAccount.balance_cents.desc(), CreditAccount.user_id)
    ).scalars().all()


def recent_entries(limit=200, kind=None):
    query = db.select(CreditEntry).order_by(CreditEntry.id.desc()).limit(limit)
    if kind:
        query = query.filter_by(kind=kind)
    return db.session.execute(query).scalars().all()


def mismatched_balances():
    """Accounts whose balance is not the sum of their entries: should never be any."""
    sums = dict(db.session.execute(
        db.select(CreditEntry.user_id, db.func.sum(CreditEntry.amount_cents)).group_by(CreditEntry.user_id)
    ).all())
    return [account.user_id for account in accounts() if int(sums.get(account.user_id) or 0) != account.balance_cents]


# --- As a spreadsheet ------------------------------------------------------------------

EXPORT_COLUMNS = ("Date", "Kind", "Description", "Amount (EUR)", "Balance after (EUR)")
EXPORT_COLUMNS_ALL = ("Date", "Account", "Kind", "Description", "Amount (EUR)", "Balance after (EUR)", "Booked by")

KIND_LABELS = {
    TOP_UP: "Top-up",
    CASH_IN: "Cash",
    CASH_OUT: "Paid out",
    PURCHASE: "Purchase",
    REFUND: "Refund",
    REVERSAL: "Chargeback",
    CORRECTION: "Correction",
}


def _plain_euros(cents):
    return f"{cents / 100:.2f}"


def _when(entry):
    from ..config import MEMBERSHIP_TIMEZONE

    created = entry.created_at if entry.created_at.tzinfo else entry.created_at.replace(tzinfo=timezone.utc)
    return created.astimezone(MEMBERSHIP_TIMEZONE).strftime("%d.%m.%Y %H:%M")


def export_rows(user):
    return [(_when(entry), KIND_LABELS.get(entry.kind, entry.kind), entry.description,
             _plain_euros(entry.amount_cents), _plain_euros(entry.balance_after_cents))
            for entry in reversed(history(user))]


def export_rows_all():
    entries = db.session.execute(db.select(CreditEntry).order_by(CreditEntry.id)).scalars().all()
    return [(_when(entry), f"#{entry.user_id}", KIND_LABELS.get(entry.kind, entry.kind), entry.description,
             _plain_euros(entry.amount_cents), _plain_euros(entry.balance_after_cents),
             f"#{entry.booked_by_user_id}" if entry.booked_by_user_id else "")
            for entry in entries]
