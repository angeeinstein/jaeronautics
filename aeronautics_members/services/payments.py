"""The one place the portal talks to Stripe about money, whatever it is for.

Everything sold through the association's Stripe account goes through here:
the association membership, the teams' fees, and whatever comes later (a
balance for the coffee machine, say). What they share lives in this module --
the connection, the person's Stripe customer, opening a Checkout without
opening two, cancelling, reading subscriptions whichever API version answers,
and handing each webhook event to whoever it belongs to. What differs -- the
membership's calendar year and free October, a team's semesters, a top-up
adding to a balance -- stays with each of them, in a handler of its own.

**Purposes.** Everything the portal sells carries ``purpose`` in the metadata
of its Checkout session and subscription (``membership``, ``team``, ...).
services/stripe_scope.py reads it back off every event, and
:func:`handler_for` names the handler that event goes to. The membership is
the webhook's own long-standing code and is not registered here; anything
else registers with :func:`register_purpose`.
"""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

import stripe
from babel.numbers import format_currency
from flask import current_app
from flask_babel import get_locale

from ..config import STRIPE_SECRET_KEY
from . import ExternalServiceError
from .settings import get_stripe_settings_map

PURPOSE_MEMBERSHIP = "membership"
PURPOSE_TEAM = "team"

# Card and SEPA direct debit, for everything; the same as the membership.
PAYMENT_METHOD_TYPES = ("card", "sepa_debit")

# Stripe refuses a Checkout trial ending sooner than this.
MIN_TRIAL_SECONDS = 48 * 3600

_handlers = {}


# --- Routing -----------------------------------------------------------------


def register_purpose(purpose, handler):
    """Have ``handler(event)`` receive the webhook events marked ``purpose``.

    It returns ``(body, status)`` like the webhook: a status of 400 or more
    leaves the event for Stripe to deliver again.
    """
    _handlers[purpose] = handler


def handler_for(scope):
    """The registered handler for a sorted event, or None (the membership's own code, or nobody's)."""
    purpose = getattr(scope, "purpose", None)
    if not purpose or purpose == PURPOSE_MEMBERSHIP:
        return None
    return _handlers.get(purpose)


# --- The connection ------------------------------------------------------------


def apply_runtime_stripe_config():
    stripe_settings = get_stripe_settings_map()
    stripe.api_key = stripe_settings.get("stripe_secret_key") or STRIPE_SECRET_KEY
    return stripe_settings


# --- Reading what Stripe sends ---------------------------------------------------


def subscription_has_scheduled_cancellation(subscription):
    if not subscription:
        return False

    if bool(subscription.get("cancel_at_period_end")):
        return True

    cancel_at = subscription.get("cancel_at")
    if cancel_at is None:
        return False

    try:
        return int(cancel_at) > int(datetime.now(timezone.utc).timestamp())
    except (TypeError, ValueError):
        return False


def subscription_period_bounds(subscription):
    """Return the (start, end) unix timestamps of a subscription's current period.

    Stripe's Basil API version (2025-03-31) removed the top-level
    ``current_period_start`` / ``current_period_end`` fields and moved them onto
    each subscription item. Read the item-level values first and fall back to the
    legacy top-level fields, so this keeps working whichever API version the
    installed library and the account negotiate.
    """
    if not subscription:
        return None, None

    items = ((subscription.get("items") or {}).get("data")) or []
    starts = [item.get("current_period_start") for item in items if item.get("current_period_start")]
    ends = [item.get("current_period_end") for item in items if item.get("current_period_end")]

    period_start = min(starts) if starts else subscription.get("current_period_start")
    period_end = max(ends) if ends else subscription.get("current_period_end")
    return period_start, period_end


def invoice_period_end(invoice):
    """The latest end of any line's period on an invoice, as a unix timestamp.

    For a subscription's invoice that is how far it pays: the end of the
    trial on the first one, the end of the new period on a renewal. A one-off
    line's period is the moment it was billed, so it never wins.
    """
    ends = []
    for line in ((invoice or {}).get("lines") or {}).get("data") or []:
        end = (line.get("period") or {}).get("end")
        if end:
            ends.append(int(end))
    return max(ends) if ends else None


def format_amount(amount_cents, currency):
    locale = str(get_locale()) if get_locale() else None
    amount = Decimal(amount_cents) / Decimal("100")
    try:
        return format_currency(amount, currency.upper(), locale=locale)
    except Exception:
        return f"{amount:.2f} {currency.upper()}"


# --- The person's Stripe customer ----------------------------------------------


def reusable_stripe_customer_id(member):
    """The member's existing Stripe customer, brought up to date, or None.

    Somebody coming back stays the same customer in Stripe, with their old
    invoices next to the new ones, rather than becoming a second customer that
    has to be matched to the first by hand. Their address and name are sent
    again first: receipts go to the address Stripe holds, and the member may
    have changed theirs since. One customer per person, for the membership and
    every team alike.
    """
    if member is None or not member.stripe_customer_id:
        return None
    try:
        stripe.Customer.modify(
            member.stripe_customer_id,
            email=member.email_private,
            name=f"{member.first_name} {member.last_name}",
        )
    except stripe.StripeError as exc:
        if getattr(exc, "code", None) != "resource_missing":
            raise
        current_app.logger.warning(
            "Stripe customer %s for member_id=%s no longer exists; a new one will be made.",
            member.stripe_customer_id, member.id,
        )
        member.stripe_customer_id = None
        return None
    return member.stripe_customer_id


def customer_params(member):
    """``customer`` for somebody Stripe knows, else ``customer_email``: Stripe refuses both."""
    customer_id = reusable_stripe_customer_id(member)
    return {"customer": customer_id} if customer_id else {"customer_email": member.email_private}


def remember_customer(member, customer_id):
    """Keep a customer Stripe made during a Checkout, for the person's next one."""
    if (
        member is not None
        and getattr(member, "deleted_at", None) is None
        and not member.stripe_customer_id
        and isinstance(customer_id, str)
        and customer_id.startswith("cus_")
    ):
        member.stripe_customer_id = customer_id


# --- Checkout ----------------------------------------------------------------------


def request_fingerprint(params):
    """A short hash of a request, for idempotency keys that change when it does.

    Stripe refuses a key reused with different parameters, so the request's
    content is part of the key: the same request twice is one session, a
    changed one is a new one.
    """
    return hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def open_checkout_session(session_id, *, what="Checkout session"):
    """The Checkout session if it can still be paid, else None.

    None too when Stripe cannot be asked: the caller then starts a new one,
    so a failed lookup must not be an error.
    """
    if not session_id:
        return None
    apply_runtime_stripe_config()
    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except stripe.StripeError as exc:
        current_app.logger.warning("Could not load %s %s: %s", what, session_id, exc)
        return None
    if session.get("status") == "open" and session.get("url"):
        return session
    return None


def expire_checkout_session(session_id):
    """Close a Checkout session nobody should pay any more. Never fails."""
    if not session_id:
        return False
    apply_runtime_stripe_config()
    try:
        stripe.checkout.Session.expire(session_id)
    except stripe.StripeError as exc:
        current_app.logger.info("Checkout session %s not expired: %s", session_id, exc)
        return False
    return True


def create_checkout_session(params, *, idempotency_key):
    apply_runtime_stripe_config()
    return stripe.checkout.Session.create(idempotency_key=idempotency_key, **params)


def retrieve_price(price_id):
    apply_runtime_stripe_config()
    return stripe.Price.retrieve(price_id)


# --- Subscriptions ------------------------------------------------------------------


def cancel_subscription(subscription_id, *, reason=None):
    """End a subscription in Stripe now, without refund. Returns whether there was one.

    Stripe would otherwise bill a final prorated invoice for the unused time.
    Already-cancelled and already-deleted subscriptions count as success: the
    caller asked for it to be gone, and it is.
    """
    if not subscription_id:
        return False
    apply_runtime_stripe_config()
    try:
        stripe.Subscription.cancel(subscription_id, prorate=False)
    except stripe.StripeError as exc:
        if getattr(exc, "code", None) != "resource_missing":
            raise ExternalServiceError(
                "Stripe could not cancel the subscription.",
                code="subscription_cancel_failed",
                details={"stripe_code": getattr(exc, "code", None)},
            ) from exc
        current_app.logger.warning(
            "Stripe subscription %s was already gone when cancelling (%s).",
            subscription_id, reason or "no reason given",
        )
    current_app.logger.info("Cancelled Stripe subscription %s (%s).", subscription_id, reason or "no reason given")
    return True


def set_cancel_at_period_end(subscription_id, cancel):
    """Have a subscription stop at the end of what is paid, or carry on after all."""
    apply_runtime_stripe_config()
    try:
        return stripe.Subscription.modify(subscription_id, cancel_at_period_end=bool(cancel))
    except stripe.StripeError as exc:
        raise ExternalServiceError(
            "Stripe could not change the subscription.",
            code="subscription_change_failed",
            details={"stripe_code": getattr(exc, "code", None)},
        ) from exc
