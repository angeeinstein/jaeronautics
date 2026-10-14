"""Selling for credit: the price lists, a sale, a sale taken back, and what each seller earned.

The association sells (coffee in the student area) and so does any team (a
beer from its fridge); each keeps a price list of its own. A sale takes the
item's price off the member's credit and names the item and the seller. What
a team sold counts towards what the team is owed, exactly like its fees
(services/team_money.py), and is passed on with the next transfer; what the
association sold stays with it. Stripe's fee on the top-up is the
association's -- unless the teams carry Stripe's fees: then each team sale
carries its share of the fee of the top-up that paid for it
(services/credit_fees.py).

A sale is made in two ways, through the same :func:`sell`: an admin or the
treasurer books one by hand, or a member taps their card on a sales point
beside the fridge (services/sales_points.py, docs/cards-plan.md). A sale
taken back gives the credit back and stops counting for the seller -- once.
"""

from ..db_models import CreditEntry, CreditItem, db
from . import ConflictError, NotFoundError, ValidationError
from . import credit
from .audit import log_audit_event
from .clock import get_now_utc

#: How a sale was made. ``booked``: by somebody in the portal, for the
#: member; ``reader``: with the member's card at a sales point.
BOOKED = "booked"
READER = "reader"
CHANNELS = (BOOKED, READER)

LEAST_PRICE = 1
#: An item's name: four of them share a small screen on a sales point.
NAME_LENGTH = 20
MOST_ITEMS = 50

#: The icons an item can have, shown beside it on a device's screen
#: (docs/device-protocol.md, "Icons"). The firmware carries a picture for each
#: name and shows ``other`` for one it does not know: a new icon is a new name
#: here and a firmware update. Chosen when the item is added.
ICONS = (
    ("coffee", "Coffee"),
    ("tea", "Tea"),
    ("hot_chocolate", "Hot chocolate"),
    ("beer", "Beer"),
    ("soft_drink", "Soft drink"),
    ("mate", "Mate"),
    ("energy_drink", "Energy drink"),
    ("juice", "Juice"),
    ("water", "Water"),
    ("snack", "Snack"),
    ("sweets", "Sweets"),
    ("ice_cream", "Ice cream"),
    ("other", "Other"),
)
ICON_NAMES = frozenset(name for name, _label in ICONS)


def _refuse(field, message, code="credit_item_invalid"):
    raise ValidationError(message, code=code, details={"fields": {field: message}})


# --- Price lists -------------------------------------------------------------------


def items(team=None, *, include_inactive=False):
    """A seller's price list in its order: the association's (``team`` None) or a team's."""
    query = db.select(CreditItem).where(
        CreditItem.team_id.is_(None) if team is None else CreditItem.team_id == team.id,
        CreditItem.deleted_at.is_(None),
    )
    if not include_inactive:
        query = query.where(CreditItem.active.is_(True))
    return db.session.execute(query.order_by(CreditItem.position, CreditItem.id)).scalars().all()


def item_of(item_id, team=None):
    """One item of this seller's list, or NotFoundError."""
    item = db.session.get(CreditItem, item_id)
    if item is None or item.deleted_at is not None or item.team_id != (team.id if team is not None else None):
        raise NotFoundError("No such item.", code="credit_item_not_found")
    return item


def _clean_name(name):
    name = " ".join((name or "").split())
    if not name:
        _refuse("name", "Name it.")
    if len(name) > NAME_LENGTH:
        _refuse("name", f"At most {NAME_LENGTH} characters.")
    return name


def _clean(name, price_cents):
    name = _clean_name(name)
    try:
        price = int(price_cents)
    except (TypeError, ValueError):
        _refuse("price_cents", "Enter a price.")
    if not LEAST_PRICE <= price <= credit.MOST_BOOKING:
        _refuse("price_cents", f"A price is between €0.01 and {credit._euros(credit.MOST_BOOKING)}.")
    return name, price


def _clean_icon(icon):
    if icon not in ICON_NAMES:
        _refuse("icon", "Choose an icon.")
    return icon


def add_item(actor, *, team=None, name, price_cents, icon="other"):
    name, price = _clean(name, price_cents)
    icon = _clean_icon(icon)
    if len(items(team, include_inactive=True)) >= MOST_ITEMS:
        raise ConflictError(f"A price list holds at most {MOST_ITEMS} items.", code="credit_items_too_many")
    last = max((item.position for item in items(team, include_inactive=True)), default=-1)
    item = CreditItem(team_id=team.id if team is not None else None, name=name, price_cents=price, icon=icon,
                      active=True, position=last + 1)
    db.session.add(item)
    db.session.flush()
    _audit("credit_item_added", actor, item)
    return item


def update_item(actor, item, *, name=None, price_cents=None, active=None, icon=None):
    """Rename it, change its price or icon, or switch it off and on. Sales made stay at their price."""
    # A name longer than the limit, from before it, stays until it is changed.
    new_name = item.name if name is None else _clean_name(name)
    _, new_price = _clean("-", item.price_cents if price_cents is None else price_cents)
    before = (item.name, item.price_cents, item.active, item.icon)
    item.name, item.price_cents = new_name, new_price
    if icon is not None:
        item.icon = _clean_icon(icon)
    if active is not None:
        item.active = bool(active)
    item.updated_at = get_now_utc()
    if before != (item.name, item.price_cents, item.active, item.icon):
        _audit("credit_item_changed", actor, item, before={"name": before[0], "price_cents": before[1],
                                                            "active": before[2], "icon": before[3]})
    return item


def move_item(actor, item, direction):
    """One place up (-1) or down (+1) in its list."""
    listed = items(item.team, include_inactive=True)
    index = next(position for position, found in enumerate(listed) if found.id == item.id)
    other = index + (1 if direction > 0 else -1)
    if not 0 <= other < len(listed):
        return item
    listed[index], listed[other] = listed[other], listed[index]
    for position, found in enumerate(listed):
        found.position = position
    return item


def delete_item(actor, item):
    """Off its price list for good: what was sold still names it. A sales
    point's menu loses it at once."""
    from ..db_models import Device

    item.deleted_at = get_now_utc()
    item.active = False
    devices = db.session.execute(
        db.select(Device).where(Device.team_id.is_(None) if item.team_id is None else Device.team_id == item.team_id)
    ).scalars().all()
    for device in devices:
        chosen = (device.settings or {}).get("items")
        if isinstance(chosen, list) and item.id in chosen:
            device.settings = {**device.settings, "items": [found for found in chosen if found != item.id]}
    _audit("credit_item_deleted", actor, item)
    return item


def _audit(event, actor, item, **extra):
    log_audit_event("credit", event, actor_user=actor, metadata={
        "item_id": item.id, "team_id": item.team_id, "name": item.name, "price_cents": item.price_cents,
        "icon": item.icon, "active": item.active, **extra,
    })


# --- Selling -----------------------------------------------------------------------


def seller_name(team):
    return team.name if team is not None else "Joanneum Aeronautics"


def sell(user, item, *, by=None, channel=BOOKED, card=None, device=None):
    """Sell ``item`` to ``user`` for credit, at its price now. Refused when the
    credit does not cover it, the item is switched off, or credit is. At a
    reader, ``card`` and ``device`` are what it was paid with, and where."""
    if channel not in CHANNELS:
        raise ValidationError("Unknown way of selling.", code="credit_channel_invalid")
    if not item.active or item.deleted_at is not None:
        raise ConflictError(f"{item.name} is not sold any more.", code="credit_item_inactive")
    if item.team is not None and item.team.status != "active":
        raise ConflictError(f"{item.team.name} is archived.", code="credit_seller_archived")
    return credit.spend(user, item.price_cents, item.name, team=item.team, by=by, item=item, channel=channel,
                        card=card, device=device)


def take_back(actor, entry, *, note=None):
    """Give a sale back: the credit returns, and the seller no longer counts it."""
    if entry.kind != credit.PURCHASE:
        raise ConflictError("Only a sale can be taken back.", code="credit_not_a_sale")
    if db.session.execute(db.select(CreditEntry.id).filter_by(reverses_id=entry.id)).first() is not None:
        raise ConflictError("This sale was taken back already.", code="credit_sale_taken_back")
    account = credit._account(entry.user_id)
    reason = " ".join((note or "").split())[:100]
    description = f"{entry.description} taken back" + (f": {reason}" if reason else "")
    undone = credit._book(account, credit.TAKEN_BACK, -entry.amount_cents, description, team=entry.team,
                          by=actor, item=entry.item, channel=entry.channel, reverses=entry, card=entry.card,
                          device=entry.device)
    undone.kept_cents = -entry.kept_cents if entry.kept_cents else None
    return undone


def taken_back_ids(entries):
    """Which of these sales were taken back."""
    ids = [entry.id for entry in entries if entry.kind == credit.PURCHASE]
    if not ids:
        return set()
    return set(db.session.execute(
        db.select(CreditEntry.reverses_id).where(CreditEntry.reverses_id.in_(ids))
    ).scalars())


# --- What each seller earned -------------------------------------------------------


def _sales_query(team, since=None):
    query = db.select(CreditEntry).where(
        CreditEntry.kind.in_((credit.PURCHASE, credit.TAKEN_BACK)),
        CreditEntry.team_id.is_(None) if team is None else CreditEntry.team_id == team.id,
    )
    if since is not None:
        query = query.where(CreditEntry.created_at >= since)
    return query


def _fee_shares(team, entries):
    """{sale id: its share of top-up fees} for a team's sales, when the teams carry Stripe's fees."""
    from .credit_fees import fee_of, parts_for_people
    from .money_settings import teams_bear_fees

    if team is None or not entries or not teams_bear_fees():
        return {}
    parts = parts_for_people({entry.user_id for entry in entries})
    return {entry.id: fee_of(parts.get(entry.id, [])) or 0 for entry in entries}


def sales_summary(team=None, since=None):
    """What a seller earned with credit, in cents, and by item: sales less what was taken back,
    and for a team less the association's share where it keeps one.

    ``{"earned": int, "count": int, "by_item": [{"key", "name", "icon", "count", "earned"}]}`` --
    ``count`` is the sales that stand (a sale taken back counts for neither).
    """
    entries = db.session.execute(_sales_query(team, since)).scalars().all()
    fee_shares = _fee_shares(team, entries)
    by_item = {}
    earned = 0
    count = 0
    for entry in entries:
        key = entry.item_id or f"name:{entry.description}"
        row = by_item.setdefault(key, {"key": key, "name": entry.item.name if entry.item else entry.description,
                                       "icon": entry.item.icon if entry.item else "other",
                                       "item_id": entry.item_id,
                                       "listed": entry.item is not None and entry.item.deleted_at is None,
                                       "count": 0, "earned": 0})
        # The seller's part: the price, less the association's share where it keeps one,
        # and the top-up fee's share where the teams carry Stripe's fees.
        cents = -entry.amount_cents - (entry.kept_cents or 0) - fee_shares.get(entry.id, 0)
        row["earned"] += cents
        earned += cents
        step = 1 if entry.kind == credit.PURCHASE else -1
        row["count"] += step
        count += step
    rows = sorted((row for row in by_item.values() if row["count"] or row["earned"]),
                  key=lambda row: (-row["earned"], row["name"]))
    return {"earned": earned, "count": count, "by_item": rows}
