"""The "Sales point" device type: a card reader with a screen that sells
items for credit (docs/cards-plan.md, docs/devices-plan.md "The screens";
the messages: docs/device-protocol.md, "Sales point").

Its owner (a team, or the association) chooses up to four items from their
own price list, in order, so every sale counts for the owner. A card tapped
opens a menu on the device's screen -- the items with their names, prices
and icons, "Hi Alex" and the balance -- and one is bought:

1. ``card`` (data ``{"card": "04A1B2C3D4E5F6"}``): the card is checked, and
   answered ``{"result": "menu", "ticket", "expires_in", "first_name",
   "balance_cents", "items"}``, or ``refused`` -- an unknown card's with a
   ``link`` for the QR code its owner links it with (services/card_links.py).
2. ``buy`` (data ``{"ticket", "item"}``): the item is sold with the ticket,
   answered ``{"result": "sold", "item", "price_cents", "balance_cents",
   "first_name"}``, or ``refused``.

A ticket belongs to the card it was given for, to this device and to one
purchase, for ``TICKET_LASTS``: nothing more is bought without the card
being tapped again. The same report sent again (its event id) gets the first
answer, and a device's reports are taken one at a time, so two copies
arriving at the same moment are one sale.

``reason`` of a refusal is one of ``card_unknown``, ``card_disabled``,
``card_blocked``, ``account_off``, ``credit_off``, ``not_set_up`` (no items
chosen), ``ticket_expired``, ``ticket_used``, ``item_off``, ``not_enough``
(with ``balance_cents``); ``message`` says it in words.
"""

import secrets
from datetime import timedelta, timezone

from ..db_models import CreditItem, Device, SaleTicket, db
from . import ConflictError, ValidationError
from . import card_links, cards, credit, credit_sales, device_types
from .clock import get_now_utc
from .locking import locked

SLUG = "sales_point"
#: How many items a device offers: one screen of four. Kept in one place,
#: should the firmware page through more one day.
MOST_ITEMS = 4
#: How long a ticket lasts: longer than the device keeps its menu open.
TICKET_LASTS = timedelta(seconds=20)
#: Tickets are deleted after this (the nightly clean-up).
TICKETS_KEPT = timedelta(days=1)

NOT_ENOUGH = "not_enough"
ITEM_OFF = "item_off"
CREDIT_OFF = "credit_off"
NOT_SET_UP = "not_set_up"
TICKET_EXPIRED = "ticket_expired"
TICKET_USED = "ticket_used"

MESSAGES = {
    **cards.REFUSALS,
    NOT_ENOUGH: "Not enough credit.",
    ITEM_OFF: "This item is not sold here any more.",
    CREDIT_OFF: "Paying with credit is switched off.",
    NOT_SET_UP: "This device has nothing to sell yet.",
    TICKET_EXPIRED: "Too late: tap your card again.",
    TICKET_USED: "Already bought: tap your card again.",
}

#: What the sale refused with, as the device is told.
_SALE_REFUSALS = {
    "credit_not_enough": NOT_ENOUGH,
    "credit_item_inactive": ITEM_OFF,
    "credit_seller_archived": ITEM_OFF,
    "credit_off": CREDIT_OFF,
}


def _aware(moment):
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _item_choices(owner):
    """The owner's items on sale: a device sells only its owner's, so every
    sale counts for the owner."""
    return [device_types.Choice(item.id, f"{item.name}, {credit._euros(item.price_cents)}",
                                credit_sales.seller_name(owner))
            for item in credit_sales.items(owner)]


def items_of(device):
    """What it offers, in its owner's order: the items chosen that are its
    owner's (another's never sells here) and on sale."""
    found = []
    for item_id in (device.settings or {}).get("items") or []:
        item = db.session.get(CreditItem, item_id)
        if item is not None and item.team_id == device.team_id and item.active and item.deleted_at is None:
            found.append(item)
    return found[:MOST_ITEMS]


def _item_out(item):
    return {"id": item.id, "name": item.name, "price_cents": item.price_cents, "icon": item.icon}


def _refused(reason, **extra):
    return {"result": "refused", "reason": reason, "message": MESSAGES[reason], **extra}


def _first_name(user):
    member = getattr(user, "member", None)
    return member.first_name if member is not None else None


def _menu(device):
    """What it offers, for its idle screen: the items without a card's balance."""
    return {"items": [_item_out(item) for item in items_of(device)]}


def _keep_card(data):
    """A tap is kept on the device's page without the card's ID: its end only."""
    card_id = data.get("card")
    tail = card_id[-cards.TAIL:].upper() if isinstance(card_id, str) and card_id else None
    return {"card": f"…{tail}" if tail else None}


def _keep_buy(data):
    """A purchase is kept with the item chosen; the ticket is of no use afterwards."""
    return {"item": data.get("item")}


def _summary_card(kept, answer):
    card = kept.get("card") or "A card"
    result = answer.get("result")
    if result == "menu":
        return f"{card} opened the menu ({credit._euros(answer.get('balance_cents') or 0)})"
    return f"{card} refused: {answer.get('message') or answer.get('reason')}"


def _summary_buy(_kept, answer):
    if answer.get("result") == "sold":
        return (f"{answer.get('item')} sold, {credit._euros(answer.get('price_cents') or 0)}; "
                f"{credit._euros(answer.get('balance_cents') or 0)} left")
    return f"Not sold: {answer.get('message') or answer.get('reason')}"


def _one_at_a_time(device, event_id):
    """Lock the device, then the answer a copy of this report was given while
    waiting, if any (device_types.Report)."""
    db.session.execute(locked(db.select(Device).filter_by(id=device.id)))
    if event_id:
        from . import devices

        return devices.earlier_answer(device, event_id)
    return None


def _card(device, data, event_id):
    card_id = data.get("card")
    if not isinstance(card_id, str) or not card_id.strip():
        raise ValidationError("Send the card's ID as \"card\".", code="sales_point_card_missing")
    earlier = _one_at_a_time(device, event_id)
    if earlier is not None:
        return earlier

    tap = cards.tapped(device, card_id)
    if tap.refused == cards.CARD_UNKNOWN:
        # Its owner links it with the QR code the reader shows (services/card_links.py).
        return _refused(tap.refused, link=card_links.for_device(card_links.offer(device, card_id)))
    if tap.refused is not None:
        return _refused(tap.refused)
    if not credit.enabled():
        return _refused(CREDIT_OFF)
    offered = items_of(device)
    if not offered:
        return _refused(NOT_SET_UP)

    now = get_now_utc()
    ticket = SaleTicket(token=f"t_{secrets.token_urlsafe(18)}", device_id=device.id, card_id=tap.card.id,
                        created_at=now, expires_at=now + TICKET_LASTS)
    db.session.add(ticket)
    db.session.flush()
    user = tap.card.user
    balance = credit.balance_of(user)
    return {
        "result": "menu", "ticket": ticket.token, "expires_in": int(TICKET_LASTS.total_seconds()),
        "first_name": _first_name(user), "balance_cents": balance,
        "items": [{**_item_out(item), "affordable": balance >= item.price_cents} for item in offered],
    }


def _buy(device, data, event_id):
    token, item_id = data.get("ticket"), data.get("item")
    if not isinstance(token, str) or not token:
        raise ValidationError("Send the ticket the menu came with as \"ticket\".", code="sales_point_ticket_missing")
    if not isinstance(item_id, int) or isinstance(item_id, bool):
        raise ValidationError("Send the item's id as \"item\".", code="sales_point_item_missing")
    earlier = _one_at_a_time(device, event_id)
    if earlier is not None:
        return earlier

    ticket = db.session.execute(
        locked(db.select(SaleTicket).filter_by(token=token, device_id=device.id))
    ).scalar_one_or_none()
    now = get_now_utc()
    if ticket is None or _aware(ticket.expires_at) <= now:
        return _refused(TICKET_EXPIRED)
    if ticket.used_at is not None:
        return _refused(TICKET_USED)
    item = next((offered for offered in items_of(device) if offered.id == item_id), None)
    if item is None:
        return _refused(ITEM_OFF)
    # The card as it is now: blocked or switched off since the menu opened.
    card = ticket.card
    state = cards.state(card)
    if state == cards.REMOVED:
        return _refused(cards.CARD_UNKNOWN)
    if state == cards.BLOCKED:
        return _refused(cards.CARD_BLOCKED)
    if state != cards.ACTIVE:
        return _refused(cards.CARD_DISABLED)
    user = card.user
    if user is None or user.disabled_at is not None or user.deleted_at is not None:
        return _refused(cards.ACCOUNT_OFF)
    try:
        entry = credit_sales.sell(user, item, channel=credit_sales.READER, card=card, device=device)
    except ConflictError as exc:
        reason = _SALE_REFUSALS.get(exc.code)
        if reason is None:
            raise
        if reason == NOT_ENOUGH:
            return _refused(reason, balance_cents=max(credit.balance_of(user), 0))
        return _refused(reason)
    card.last_used_at, card.last_used_device_id = now, device.id
    ticket.used_at, ticket.entry_id = now, entry.id
    return {"result": "sold", "item": item.name, "price_cents": item.price_cents,
            "balance_cents": entry.balance_after_cents, "first_name": _first_name(user)}


def forget_old_tickets(now=None):
    """Tickets of the day before and older deleted (the nightly clean-up). Returns how many."""
    cutoff = (now or get_now_utc()) - TICKETS_KEPT
    return db.session.query(SaleTicket).filter(SaleTicket.created_at < cutoff).delete(synchronize_session=False)


device_types.register(device_types.DeviceType(
    slug=SLUG,
    label="Sales point",
    description="A card reader with a screen beside the fridge or the coffee machine: a card tapped opens a "
                "menu of up to four items, bought for credit.",
    settings=(device_types.Setting(
        "items", "Items", kind="choices", choices=_item_choices, owner_may_change=True, most=MOST_ITEMS, with_prices=True,
        default=[], help=f"What its menu offers, in this order: at most {MOST_ITEMS}, from the owner's price list."),),
    resources={"menu": device_types.Resource(_menu, "What its menu offers, for its screen while nobody uses it.")},
    reports={
        "card": device_types.Report(_card, description="A card tapped: the menu, the name and the balance, and a "
                                                       "ticket to buy one item with.",
                                    keep=_keep_card, summary=_summary_card),
        "buy": device_types.Report(_buy, description="An item chosen from the menu: sold with the ticket.",
                                   keep=_keep_buy, summary=_summary_buy),
    },
    reads_cards=True,
    firmware_notes="""\
**A card tapped:** report `card` with the card's ID in hex, `"data": {"card": "04A1B2C3D4E5F6"}`
(separators and case do not matter). Report every card: whether it is known, linked or may buy is the
portal's to decide. The `answer` is one of:

- `{"result": "menu", "ticket": "t_…", "expires_in": 20, "first_name": "Alex", "balance_cents": 1250,
  "items": [{"id": 4, "name": "Coffee", "price_cents": 80, "icon": "coffee", "affordable": true}, ...]}`
  -- show the menu: up to four items in this order, "Hi Alex" (`first_name` may be null) and the
  balance. Grey out items not `affordable`. Close the menu after your own timeout (about 8 seconds),
  shorter than `expires_in`.
- `{"result": "refused", "reason": "...", "message": "..."}` -- `reason` one of `card_unknown`,
  `card_disabled`, `card_blocked`, `account_off`, `credit_off`, `not_set_up` (no items chosen yet).
  `card_unknown` comes with `"link": {"id": 17, "url": "https://…/link/…", "show_for": 30,
  "check_every": 2}`: show `url` as a QR code for at most `show_for` seconds, and meanwhile ask
  `GET /api/v1/device/card-links/<id>` every `check_every` seconds. Its `state`: `waiting` (keep
  the code), `opened` (a phone has it: "Continue on your phone" for a few seconds, then the normal
  screen), `linked` (with `first_name`: "Linked, hi Alex"), `expired` (the normal screen).

**An item chosen:** report `buy` with `"data": {"ticket": "t_…", "item": 4}`. The answer:

- `{"result": "sold", "item": "Coffee", "price_cents": 80, "balance_cents": 1170, "first_name": "Alex"}`;
- `{"result": "refused", "reason": "...", "message": "..."}` -- `reason` one of `ticket_expired` (tap
  again), `ticket_used` (one ticket buys once), `item_off`, `not_enough` (with `balance_cents`),
  `credit_off`, `card_blocked`, `card_disabled`, `account_off`.

Show `message` for a reason you do not know.

**While nobody uses it:** `GET .../resources/menu` answers `{"items": [{"id", "name", "price_cents",
"icon"}, ...]}`. Its owner can change the items and prices at any time: fetch it again whenever the
sign of life answers `200`.""",
))
