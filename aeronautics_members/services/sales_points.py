"""The "Sales point" device type: a card reader that sells one fixed item
(docs/cards-plan.md). A member taps their card; the item's price comes off
their credit, through the same sale as one booked by hand
(services/credit_sales.py), and counts for the item's seller.

"For a starter, I would only begin with a fixed item per reader": the item
is the reader's one setting, chosen from any seller's price list; the seller
is the item's. Readers with buttons or a display come later, with the same
tap.

The tap (``POST /api/v1/device/events``, kind ``tap``, data ``{"card":
"04A1B2C3D4E5F6"}``) is answered with one of:

- ``{"result": "sold", "item", "price_cents", "balance_cents"}``;
- ``{"result": "linked", "card"}``: the card was unknown, and an admin was
  waiting at this reader to link it to somebody (services/cards.py);
- ``{"result": "refused", "reason", "message"}``, ``reason`` one of
  ``card_unknown``, ``card_disabled``, ``card_blocked``, ``account_off``,
  ``not_enough`` (with ``balance_cents``), ``item_off``, ``credit_off``,
  ``not_set_up``, ``too_soon``.

The firmware only shows the answer; the portal decides. A double tap is one
sale: a card is sold to at most once per reader in ``DOUBLE_TAP``, and the
same report sent again (its event id) gets the first answer. Taps on one
reader are taken one at a time, so two copies of a report arriving at the
same moment are one sale too.
"""

from datetime import timedelta, timezone

from ..db_models import CreditEntry, CreditItem, Device, Team, db
from . import ConflictError, ValidationError
from . import cards, credit, credit_sales, device_types
from .clock import get_now_utc
from .locking import locked

SLUG = "sales_point"
#: A card is sold to at most once in this time on one reader: a double tap is one sale.
DOUBLE_TAP = timedelta(seconds=5)

NOT_ENOUGH = "not_enough"
ITEM_OFF = "item_off"
CREDIT_OFF = "credit_off"
NOT_SET_UP = "not_set_up"
TOO_SOON = "too_soon"

MESSAGES = {
    **cards.REFUSALS,
    NOT_ENOUGH: "Not enough credit.",
    ITEM_OFF: "This item is not sold any more.",
    CREDIT_OFF: "Paying with credit is switched off.",
    NOT_SET_UP: "This reader has no item to sell yet.",
    TOO_SOON: "Sold to this card a moment ago.",
}

#: What the sale refused with, as the reader is told.
_SALE_REFUSALS = {
    "credit_not_enough": NOT_ENOUGH,
    "credit_item_inactive": ITEM_OFF,
    "credit_seller_archived": ITEM_OFF,
    "credit_off": CREDIT_OFF,
}


def _aware(moment):
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _item_choices():
    """Every item on sale, under its seller: the association's, then each active team's."""
    choices = [device_types.Choice(item.id, f"{item.name}, {credit._euros(item.price_cents)}",
                                   credit_sales.seller_name(None))
               for item in credit_sales.items(None)]
    teams = db.session.execute(
        db.select(Team).filter_by(status="active").order_by(db.func.lower(Team.name))
    ).scalars()
    for team in teams:
        choices += [device_types.Choice(item.id, f"{item.name}, {credit._euros(item.price_cents)}", team.name)
                    for item in credit_sales.items(team)]
    return choices


def item_of(device):
    item_id = (device.settings or {}).get("item")
    return db.session.get(CreditItem, item_id) if item_id else None


def _refused(reason, **extra):
    return {"result": "refused", "reason": reason, "message": MESSAGES[reason], **extra}


def _item(device):
    """What the reader sells, for a display or a label."""
    item = item_of(device)
    if item is None:
        return {"item": None}
    return {"item": item.name, "price_cents": item.price_cents, "seller": credit_sales.seller_name(item.team),
            "on_sale": bool(item.active)}


def _keep(data):
    """A tap is kept on the device's page without the card's ID: its end only."""
    card_id = data.get("card")
    tail = card_id[-cards.TAIL:].upper() if isinstance(card_id, str) and card_id else None
    return {"card": f"…{tail}" if tail else None}


def _summary(kept, answer):
    """A tap in words, for the device's page."""
    card = kept.get("card") or "A card"
    result = answer.get("result")
    if result == "sold":
        return (f"{card} bought {answer.get('item')}, {credit._euros(answer.get('price_cents') or 0)}; "
                f"{credit._euros(answer.get('balance_cents') or 0)} left")
    if result == "linked":
        return f"{card} linked"
    return f"{card} refused: {answer.get('message') or answer.get('reason')}"


def _tap(device, data, event_id):
    card_id = data.get("card")
    if not isinstance(card_id, str) or not card_id.strip():
        raise ValidationError("Send the card's ID as \"card\".", code="sales_point_card_missing")
    # One tap at a time on this reader; then, read afresh, whether this very
    # report was answered already by a copy that arrived at the same moment.
    db.session.execute(locked(db.select(Device).filter_by(id=device.id)))
    if event_id:
        from . import devices

        earlier = devices.earlier_answer(device, event_id)
        if earlier is not None:
            return earlier

    tap = cards.tapped(device, card_id)
    if tap.linked is not None:
        return {"result": "linked", "card": tap.linked.name}
    if tap.refused is not None:
        return _refused(tap.refused)
    card = tap.card

    item = item_of(device)
    if item is None:
        return _refused(NOT_SET_UP)
    now = get_now_utc()
    last = db.session.execute(locked(
        db.select(CreditEntry).filter_by(card_id=card.id, device_id=device.id, kind=credit.PURCHASE)
        .order_by(CreditEntry.id.desc()).limit(1)
    )).scalar_one_or_none()
    if last is not None and now - _aware(last.created_at) < DOUBLE_TAP:
        return _refused(TOO_SOON)
    try:
        entry = credit_sales.sell(card.user, item, channel=credit_sales.READER, card=card, device=device)
    except ConflictError as exc:
        reason = _SALE_REFUSALS.get(exc.code)
        if reason is None:
            raise
        if reason == NOT_ENOUGH:
            return _refused(reason, balance_cents=max(credit.balance_of(card.user), 0))
        return _refused(reason)
    return {"result": "sold", "item": item.name, "price_cents": item.price_cents,
            "balance_cents": entry.balance_after_cents}


device_types.register(device_types.DeviceType(
    slug=SLUG,
    label="Sales point",
    description="A card reader beside the fridge or the coffee machine: a tap sells one item for credit.",
    settings=(device_types.Setting(
        "item", "Item", kind="choice", choices=_item_choices,
        help="What a tap sells, from a price list. Its seller is who the sale counts for."),),
    resources={"item": device_types.Resource(_item, "What a tap sells, at what price: for a label or a display.")},
    reports={"tap": device_types.Report(_tap, description="A card tapped: sells the item, or says why not.",
                                        keep=_keep, summary=_summary)},
    reads_cards=True,
))
