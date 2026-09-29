"""Whether something Stripe reports is the association membership at all.

The portal receives every event of the association's Stripe account. For as
long as the membership was the only thing sold there, every paid invoice and
every subscription was the membership's, and the handlers assumed so: an
invoice was matched to a member by customer or email and extended their
membership. The day the teams sell their own fee through the same account --
or somebody makes a payment link for a T-shirt -- that assumption credits a
student with a year of association membership for paying their team fee.

So each object is sorted before anything acts on it:

``ours``     it says so (the portal's own metadata), the portal already knows
             its subscription, or it is for the membership's price or product.
``foreign``  it says it is something else, or everything on it is for another
             product, or it is a payment with no invoice (the membership is
             always billed through a subscription, so always has one).
``unclear``  none of the above could be told -- Stripe could not be asked, or
             the object carries nothing to go on.

Only ``foreign`` is ignored. ``unclear`` is handled as the membership, as
everything was before, and an administrator is told: a membership payment
that is wrongly ignored leaves a paying student without access, which is the
worse of the two mistakes.

What the teams, or anything else sold later, must do to be recognised: set
``purpose`` in the metadata of their Checkout session, subscription and
payment intent to something other than ``membership`` -- or at least use a
product of their own, not the membership's.
"""

from dataclasses import dataclass

import stripe
from flask import current_app

from ..config import STRIPE_PRICE_ID
from ..db_models import Member, MembershipPeriod, db
from .settings import get_stripe_settings_map

MEMBERSHIP_PURPOSE = "membership"

OURS = "ours"
FOREIGN = "foreign"
UNCLEAR = "unclear"

# The reason given for a payment that paid no invoice. Named, because one
# event treats it more gently than the rest; see the webhook.
NO_INVOICE = "a payment with no invoice"

# Written by the portal's membership checkout before ``purpose`` existed, so
# every subscription from before the marker still carries them.
LEGACY_MEMBERSHIP_KEYS = ("membership_starts_on", "membership_ends_on", "activation_mode")

# The membership price's product, by price id. Products do not change under a
# price, so this is safe to keep for the life of the process; a failed lookup
# is not kept, so the next event asks again.
_product_of_price = {}


@dataclass(frozen=True)
class Scope:
    verdict: str
    reason: str

    @property
    def is_ours(self):
        return self.verdict == OURS

    @property
    def is_foreign(self):
        return self.verdict == FOREIGN

    @property
    def is_unclear(self):
        return self.verdict == UNCLEAR


def _get(obj, key, default=None):
    if obj is None:
        return default
    try:
        value = obj.get(key, default)
    except AttributeError:
        return default
    return default if value is None else value


def _id_of(value):
    """A Stripe reference is an id, or the object itself when expanded."""
    if isinstance(value, str):
        return value
    return _get(value, "id")


def _from_metadata(metadata):
    metadata = metadata or {}
    purpose = str(_get(metadata, "purpose", "") or "").strip().lower()
    if purpose:
        return Scope(OURS, "marked as the membership") if purpose == MEMBERSHIP_PURPOSE \
            else Scope(FOREIGN, f"marked as {purpose!r}")
    if any(_get(metadata, key) for key in LEGACY_MEMBERSHIP_KEYS):
        return Scope(OURS, "carries the membership checkout's metadata")
    return None


def _known_subscription(subscription_id):
    if not subscription_id:
        return False
    if db.session.query(Member.id).filter(Member.stripe_subscription_id == subscription_id).first():
        return True
    return db.session.query(MembershipPeriod.id).filter(
        MembershipPeriod.stripe_subscription_id == subscription_id
    ).first() is not None


def _membership_price_id():
    return get_stripe_settings_map().get("stripe_price_id") or STRIPE_PRICE_ID or None


def _membership_product_id(price_id):
    if not price_id:
        return None
    if price_id not in _product_of_price:
        try:
            settings = get_stripe_settings_map()
            stripe.api_key = settings.get("stripe_secret_key") or stripe.api_key
            price = stripe.Price.retrieve(price_id)
        except Exception as exc:  # noqa: BLE001 -- "could not tell", never "foreign"
            current_app.logger.warning("Could not look up the membership price %s: %s", price_id, exc)
            return None
        _product_of_price[price_id] = _id_of(_get(price, "product"))
    return _product_of_price[price_id]


def _from_prices(pairs):
    """``pairs`` of (price id, product id) from items or invoice lines."""
    pairs = [(price, product) for price, product in pairs if price or product]
    if not pairs:
        return None
    membership_price = _membership_price_id()
    if membership_price and any(price == membership_price for price, _ in pairs):
        return Scope(OURS, "for the membership price")
    products = {product for _, product in pairs if product}
    if not products:
        return None
    membership_product = _membership_product_id(membership_price)
    if membership_product is None:
        return None
    if membership_product in products:
        return Scope(OURS, "for the membership product")
    return Scope(FOREIGN, "for another product")


def _price_pair(price):
    return _id_of(price) if not isinstance(price, str) else price, _id_of(_get(price, "product"))


def _subscription_prices(subscription):
    items = _get(_get(subscription, "items", {}), "data", []) or []
    return [_price_pair(_get(item, "price")) for item in items]


def _invoice_prices(invoice):
    pairs = []
    for line in _get(_get(invoice, "lines", {}), "data", []) or []:
        price = _get(line, "price")
        if price:
            pairs.append(_price_pair(price))
            continue
        # The Basil and later API versions moved the price under ``pricing``.
        details = _get(_get(line, "pricing", {}), "price_details", {})
        if details:
            pairs.append((_id_of(_get(details, "price")), _id_of(_get(details, "product"))))
    return pairs


def _invoice_subscription_details(invoice):
    return _get(invoice, "subscription_details") or _get(_get(invoice, "parent", {}), "subscription_details") or {}


def invoice_subscription_id(invoice):
    """The subscription an invoice bills, wherever this API version puts it."""
    return _id_of(_get(invoice, "subscription")) or _id_of(_get(_invoice_subscription_details(invoice), "subscription"))


def scope_of_subscription(subscription):
    return (
        _from_metadata(_get(subscription, "metadata"))
        or (Scope(OURS, "a subscription the portal knows") if _known_subscription(_get(subscription, "id")) else None)
        or _from_prices(_subscription_prices(subscription))
        or Scope(UNCLEAR, "a subscription with nothing to tell it by")
    )


def scope_of_invoice(invoice):
    found = (
        _from_metadata(_get(_invoice_subscription_details(invoice), "metadata"))
        or _from_metadata(_get(invoice, "metadata"))
    )
    if found:
        return found
    if _known_subscription(invoice_subscription_id(invoice)):
        return Scope(OURS, "bills a subscription the portal knows")
    return _from_prices(_invoice_prices(invoice)) or Scope(UNCLEAR, "an invoice with nothing to tell it by")


def scope_of_checkout_session(session):
    found = _from_metadata(_get(session, "metadata"))
    if found:
        return found
    if _known_subscription(_id_of(_get(session, "subscription"))):
        return Scope(OURS, "for a subscription the portal knows")
    # The portal's own sessions always carry its metadata, so this is one it
    # did not make -- a payment link, say. What was bought decides.
    try:
        items = stripe.checkout.Session.list_line_items(_get(session, "id"), limit=100)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("Could not read the items of Checkout session %s: %s", _get(session, "id"), exc)
        return Scope(UNCLEAR, "a Checkout session whose items could not be read")
    pairs = [_price_pair(_get(item, "price")) for item in _get(items, "data", []) or []]
    return _from_prices(pairs) or Scope(UNCLEAR, "a Checkout session with nothing to tell it by")


def _invoice_of_payment_intent(payment_intent):
    """The invoice a payment paid, or None when it paid none. Raises when unsure."""
    invoice = _get(payment_intent, "invoice")
    if invoice:
        return invoice
    listing = stripe.InvoicePayment.list(
        payment={"type": "payment_intent", "payment_intent": _get(payment_intent, "id")}, limit=1,
    )
    payments = _get(listing, "data", []) or []
    return _get(payments[0], "invoice") if payments else None


def scope_of_payment_intent(payment_intent):
    found = _from_metadata(_get(payment_intent, "metadata"))
    if found:
        return found
    try:
        invoice = _invoice_of_payment_intent(payment_intent)
        if isinstance(invoice, str):
            invoice = stripe.Invoice.retrieve(invoice)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning(
            "Could not find the invoice of payment %s: %s", _get(payment_intent, "id"), exc,
        )
        return Scope(UNCLEAR, "a payment whose invoice could not be looked up")
    if not invoice:
        return Scope(FOREIGN, NO_INVOICE)
    return scope_of_invoice(invoice)


def scope_of_event(event):
    """Sort a verified webhook event. Events that concern no subscription or
    payment -- none, today -- count as the membership's."""
    event_type = event["type"]
    obj = event["data"]["object"]
    if event_type == "checkout.session.completed":
        return scope_of_checkout_session(obj)
    if event_type.startswith("invoice."):
        return scope_of_invoice(obj)
    if event_type.startswith("customer.subscription."):
        return scope_of_subscription(obj)
    if event_type.startswith("payment_intent."):
        return scope_of_payment_intent(obj)
    if event_type == "charge.dispute.closed":
        payment_intent = _get(obj, "payment_intent")
        if payment_intent:
            return scope_of_payment_intent(
                payment_intent if not isinstance(payment_intent, str) else {"id": payment_intent}
            )
        return Scope(UNCLEAR, "a dispute with no payment to trace")
    return Scope(OURS, "not a payment or subscription event")
