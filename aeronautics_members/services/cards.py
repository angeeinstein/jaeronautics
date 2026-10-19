"""Cards linked to people: student cards, key fobs (docs/cards-plan.md).

A card is a record of its own, linked to the person it belongs to; a person
may have several. Readers find a card by its ID (UID), which a reader or a
phone reads off it. What a card may do at a reader -- buy, later open a
cabinet -- is the device type's business, not the card's.

**Its ID is never kept.** The portal keeps an HMAC of it under a key derived
from the server's ``SECRET_KEY`` (a card's ID is short and starts the same
on every card of a maker, so a plain hash could be reversed by trying them
all), and its last characters, to tell cards apart. A changed ``SECRET_KEY``
would make every card unknown: a check value kept at the first link lets
System health say so (:func:`key_changed`).

**Switching a card off** is two things: its owner *disables* it and can
switch it on again; an admin *blocks* it, and only an admin lifts that.
**Removing** it keeps the row -- what was paid with it still names it -- and
frees its ID to be linked again.

**Linking** a card: its owner taps it on a reader with a screen and opens
the QR code shown there with their phone (services/card_links.py). There
is no linking anybody can start from the portal alone: anybody, anywhere,
could start it, and a stranger's first tap would end up in their account.
The QR code only exists on the reader, just after the card's tap. (Web NFC
and an admin linking a card were the first ways, removed 2026-10-14:
docs/cards-plan.md.)
"""

import hashlib
import hmac
import re
from datetime import timezone

from flask import current_app
from sqlalchemy.exc import IntegrityError

from ..db_models import Card, CardLink, Device, db
from . import ConflictError, NotFoundError, ValidationError
from .audit import log_audit_event
from .clock import get_now_utc

#: The ID read off a card, written as hex: 4, 7 or 10 bytes.
ID_PATTERN = re.compile(r"^(?:[0-9A-F]{8}|[0-9A-F]{14}|[0-9A-F]{20})$")
TAIL = 4
NAME_MAX = 40
DEFAULT_NAME = "Card"
#: Cards one person may have linked at once.
MOST_CARDS = 10
#: The setting that holds the check value of the key cards are hashed with.
KEY_CHECK_SETTING = "cards_key_check"

# How a card was linked: "qr" now; the others were the first ways, kept for
# the cards linked so.
BY_PHONE = "phone"
BY_ADMIN_PHONE = "admin_phone"
BY_READER = "reader"
BY_QR = "qr"
LINKED_HOW = {
    BY_PHONE: "Scanned by its owner",
    BY_ADMIN_PHONE: "Scanned by an admin",
    BY_READER: "Tapped on a reader, with an admin",
    BY_QR: "Tapped on a reader, linked by its owner",
}

# A card's state, as shown.
ACTIVE = "active"
DISABLED = "disabled"
BLOCKED = "blocked"
REMOVED = "removed"


def _aware(moment):
    return moment if moment is None or moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _refuse(field, message, code):
    raise ValidationError(message, code=code, details={"fields": {field: message}})


# --- The ID and its hash -----------------------------------------------------------


def normalize(card_id):
    """The ID as kept everywhere: hex, upper case, no separators -- the same
    whether a phone read it (``04:a1:…``) or a reader (``04A1…``)."""
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", card_id or "").upper()
    if not ID_PATTERN.match(cleaned):
        _refuse("card_id", "Not a card ID this portal can read.", "card_id_invalid")
    return cleaned


def _key():
    secret = current_app.config["SECRET_KEY"]
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    return hmac.new(secret, b"jaeronautics card ids", hashlib.sha256).digest()


def id_hash(card_id):
    """The HMAC kept for a card's (normalized) ID."""
    return hmac.new(_key(), card_id.encode("ascii"), hashlib.sha256).hexdigest()


def _key_check():
    return id_hash("0" * 8)[:16]


def _remember_key():
    from .settings import get_settings_map, set_setting_value

    if KEY_CHECK_SETTING not in get_settings_map([KEY_CHECK_SETTING]):
        set_setting_value(KEY_CHECK_SETTING, _key_check())


def key_changed():
    """Whether the cards were linked under another ``SECRET_KEY`` -- then none
    of them is recognised any more. For System health."""
    from .settings import get_settings_map

    kept = get_settings_map([KEY_CHECK_SETTING]).get(KEY_CHECK_SETTING)
    return kept is not None and kept != _key_check() and db.session.execute(
        db.select(Card.id).where(Card.linked_hash.is_not(None)).limit(1)
    ).first() is not None


# --- Reading them -----------------------------------------------------------------


def state(card):
    if card.removed_at is not None:
        return REMOVED
    if card.blocked_at is not None:
        return BLOCKED
    if card.disabled_at is not None:
        return DISABLED
    return ACTIVE


def tail(card):
    return f"…{card.id_tail}"


def kept_blocked(card):
    """Removed by its owner while blocked: the block stays with the card, and
    nobody links it again until an admin lifts it (:func:`remove`)."""
    return card.removed_at is not None and card.blocked_at is not None and card.id_hash is not None


_KEPT_BLOCKED = db.and_(Card.removed_at.is_not(None), Card.blocked_at.is_not(None), Card.id_hash.is_not(None))


def _shown(query, include_removed, with_kept_blocks):
    if include_removed:
        return query
    if with_kept_blocks:
        return query.where(db.or_(Card.removed_at.is_(None), _KEPT_BLOCKED))
    return query.where(Card.removed_at.is_(None))


def cards_of(user, *, include_removed=False, with_kept_blocks=False):
    """The person's cards; for admins (``with_kept_blocks``) also those
    removed while blocked, whose block an admin may lift."""
    query = _shown(db.select(Card).filter_by(user_id=user.id), include_removed, with_kept_blocks)
    return db.session.execute(query.order_by(Card.linked_at, Card.id)).scalars().all()


def card_of(card_id, user=None, *, with_kept_blocks=False):
    """One card (of ``user``, when given), not removed -- or, for an admin,
    removed while blocked; else NotFoundError."""
    card = db.session.get(Card, card_id)
    removed = card is not None and card.removed_at is not None and not (with_kept_blocks and kept_blocked(card))
    if card is None or removed or (user is not None and card.user_id != user.id):
        raise NotFoundError("No such card.", code="card_not_found")
    return card


def all_cards(*, include_removed=False, with_kept_blocks=False):
    query = _shown(db.select(Card), include_removed, with_kept_blocks)
    return db.session.execute(query.order_by(Card.linked_at.desc(), Card.id.desc())).scalars().all()


def find(card_id):
    """The linked card with this ID (not normalized yet), or None."""
    return db.session.execute(
        db.select(Card).filter_by(linked_hash=id_hash(normalize(card_id)))
    ).scalar_one_or_none()


# --- Linking ----------------------------------------------------------------------


def _clean_name(name):
    name = " ".join((name or "").split())
    if len(name) > NAME_MAX:
        _refuse("name", f"At most {NAME_MAX} characters.", "card_name_invalid")
    return name or DEFAULT_NAME


def link(user, card_id, *, name=None, by, how):
    """Link the card with this ID to ``user``. Refused when it is linked to
    somebody already, or ``user`` has as many cards as anyone may."""
    card_id = normalize(card_id)
    return link_hashed(user, id_hash(card_id), card_id[-TAIL:], name=name, by=by, how=how)


def link_hashed(user, hashed, id_tail, *, name=None, by, how):
    """:func:`link`, for a card known by its hash and end only (a QR code's link)."""
    existing = db.session.execute(db.select(Card).filter_by(linked_hash=hashed)).scalar_one_or_none()
    if existing is not None:
        if existing.user_id == user.id:
            raise ConflictError(f"This card is linked to this account already, as {existing.name}.",
                                code="card_linked_already")
        raise ConflictError("This card belongs to another account. Ask an admin.", code="card_linked_elsewhere")
    if db.session.execute(db.select(Card.id).where(Card.id_hash == hashed, _KEPT_BLOCKED)).first() is not None:
        raise ConflictError("This card is blocked. Ask an admin.", code="card_blocked")
    if len(cards_of(user)) >= MOST_CARDS:
        raise ConflictError(f"At most {MOST_CARDS} cards per person.", code="card_too_many")
    now = get_now_utc()
    card = Card(user_id=user.id, name=_clean_name(name), id_hash=hashed, linked_hash=hashed,
                id_tail=id_tail, linked_how=how, linked_at=now,
                linked_by_user_id=by.id if by is not None else None)
    try:
        with db.session.begin_nested():
            db.session.add(card)
    except IntegrityError:
        # The same card linked by somebody else at the same moment.
        raise ConflictError("This card belongs to another account. Ask an admin.",
                            code="card_linked_elsewhere") from None
    _remember_key()
    _audit("card_linked", by, card, how=how)
    return card


def rename(actor, card, name):
    card.name = _clean_name(name)
    _audit("card_renamed", actor, card)
    return card


def disable(actor, card):
    """Switched off by its owner (or an admin for them); its owner can switch it on again."""
    if card.disabled_at is None:
        card.disabled_at = get_now_utc()
        _audit("card_disabled", actor, card)
    return card


def enable(actor, card):
    if card.disabled_at is not None:
        card.disabled_at = None
        _audit("card_enabled", actor, card)
    return card


def block(actor, card):
    """Blocked by an admin: refused at every reader until an admin lifts it."""
    if card.blocked_at is None:
        card.blocked_at = get_now_utc()
        card.blocked_by_user_id = actor.id if actor is not None else None
        _audit("card_blocked", actor, card)
    return card


def unblock(actor, card):
    """The block lifted. A card removed while blocked stays removed, and may
    be linked again."""
    if card.blocked_at is not None:
        card.blocked_at = None
        card.blocked_by_user_id = None
        _audit("card_unblocked", actor, card)
    return card


def remove(actor, card):
    """Gone from the person's cards; its ID may be linked again. The row
    stays, nameless of its ID, so what was paid with it still names it.

    A blocked card keeps its block: removing and linking it again must not
    lift what only an admin lifts (a card passed around stays stopped,
    whoever links it next). Its ``id_hash`` stays for that, and admins still
    see it, blocked."""
    if card.removed_at is None:
        card.removed_at = get_now_utc()
        card.removed_by_user_id = actor.id if actor is not None else None
        card.linked_hash = None
        _audit("card_removed", actor, card)
    return card


def forget_for_erasure(user, actor=None):
    """At erasure: every card of the person removed, and its hash gone too.
    Returns how many."""
    count = 0
    owned = cards_of(user, include_removed=True)
    # The QR codes' links that linked them keep the card's hash: gone too.
    card_ids = [card.id for card in owned]
    for card in owned:
        if card.removed_at is None:
            card.removed_at = get_now_utc()
            card.removed_by_user_id = actor.id if actor is not None else None
        card.linked_hash = None
        card.id_hash = None
        card.name = DEFAULT_NAME
        count += 1
    if card_ids:
        db.session.execute(db.delete(CardLink).where(CardLink.card_id.in_(card_ids)))
    return count


def _audit(event, actor, card, **extra):
    log_audit_event("cards", event, actor_user=actor, target_user=card.user,
                    metadata={"card_id": card.id, "name": card.name, "tail": card.id_tail, **extra})


# --- Linking at a reader, with an admin ---------------------------------------------


def reads_cards(device):
    from . import device_types

    device_type = device_types.get(device.type)
    return device_type is not None and device_type.reads_cards


def readers():
    """The devices a card can be tapped on, switched on."""
    from . import devices

    return [device for device in devices.devices() if device.enabled and reads_cards(device)]


def in_use():
    """Whether any device reads cards: then members see My Account › Cards."""
    from . import device_types

    slugs = [device_type.slug for device_type in device_types.all_types() if device_type.reads_cards]
    return bool(slugs) and db.session.execute(
        db.select(Device.id).where(Device.type.in_(slugs)).limit(1)
    ).first() is not None


# --- At a reader ----------------------------------------------------------------------

#: Why a card is refused at a reader, as the device is told.
CARD_UNKNOWN = "card_unknown"
CARD_DISABLED = "card_disabled"
CARD_BLOCKED = "card_blocked"
ACCOUNT_OFF = "account_off"

REFUSALS = {
    CARD_UNKNOWN: "This card is not linked to an account.",
    CARD_DISABLED: "This card is switched off.",
    CARD_BLOCKED: "This card is blocked. Ask an admin.",
    ACCOUNT_OFF: "This card's account is switched off.",
}


class Tap:
    """What a tap on a reader came to, as far as the card goes: ``card`` the
    card to go on with, or ``refused`` why not."""

    def __init__(self, card=None, refused=None):
        self.card, self.refused = card, refused


def tapped(device, card_id, now=None):
    """A card tapped on ``device``: the card when it may go on, else why it is
    refused. Marks the card as used here."""
    now = now or get_now_utc()
    card_id = normalize(card_id)
    card = db.session.execute(db.select(Card).filter_by(linked_hash=id_hash(card_id))).scalar_one_or_none()
    if card is None:
        return Tap(refused=CARD_UNKNOWN)
    card.last_used_at, card.last_used_device_id = now, device.id
    if card.blocked_at is not None:
        return Tap(refused=CARD_BLOCKED)
    if card.disabled_at is not None:
        return Tap(refused=CARD_DISABLED)
    if card.user is None or card.user.disabled_at is not None or card.user.deleted_at is not None:
        return Tap(refused=ACCOUNT_OFF)
    return Tap(card=card)
