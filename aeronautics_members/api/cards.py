"""Cards linked to people (services/cards.py, docs/cards-plan.md): one's own
on My Account › Cards, and anybody's for admins (``cards.manage``).

A card's ID never leaves the portal again once linked: only its last
characters (``…E5F6``) are shown.
"""

from typing import Literal

from flask_login import current_user
from pydantic import Field

from ..app import limiter, rate_limit_person
from ..config import RATELIMIT_CARD_LINK
from ..db_models import Device, User, db
from ..permissions import Permission
from ..services import NotFoundError
from ..services import cards, devices
from ..services.pictures import picture_url
from ._core import Model, UtcDateTime, endpoint

TAG = "Cards"
MANAGE = [Permission.CARDS_MANAGE]

CardState = Literal["active", "disabled", "blocked", "removed"]


class CardOut(Model):
    id: int
    name: str
    #: Its last characters, to tell cards apart: "…E5F6".
    tail: str
    state: CardState
    linked_at: UtcDateTime
    #: "Scanned by its owner", "Tapped on a reader, with an admin", ...
    linked_how: str
    last_used_at: UtcDateTime | None
    #: The reader it was last used on; none once that reader was removed.
    last_used_on: str | None


def _card(card):
    return CardOut(
        id=card.id, name=card.name, tail=cards.tail(card), state=cards.state(card), linked_at=card.linked_at,
        linked_how=cards.LINKED_HOW.get(card.linked_how, card.linked_how), last_used_at=card.last_used_at,
        last_used_on=devices.label(card.last_used_device) if card.last_used_device is not None else None,
    )


# --- One's own ------------------------------------------------------------------------


class MyCardsOut(Model):
    items: list[CardOut]
    most: int


def _mine():
    return MyCardsOut(items=[_card(card) for card in cards.cards_of(current_user)], most=cards.MOST_CARDS)


@endpoint("GET", "/account/cards", response=MyCardsOut, tag=TAG)
def my_cards():
    """One's cards: their names, states and last use."""
    return _mine()


class CardLinkIn(Model):
    #: As read off the card: "04:a1:b2:…" from a phone, "04A1B2…" from a reader.
    card_id: str = Field(max_length=40)
    name: str | None = Field(default=None, max_length=80)


@endpoint("POST", "/account/cards", response=MyCardsOut, body=CardLinkIn, status=201, tag=TAG)
@limiter.limit(RATELIMIT_CARD_LINK, key_func=rate_limit_person)
def my_cards_link(body):
    """Link a card one has just scanned with one's phone (Web NFC)."""
    cards.link(current_user, body.card_id, name=body.name, by=current_user, how=cards.BY_PHONE)
    db.session.commit()
    return _mine()


class CardChangeIn(Model):
    name: str | None = Field(default=None, max_length=80)
    #: Off: refused at every reader until switched on again.
    enabled: bool | None = None


@endpoint("PATCH", "/account/cards/<int:card_id>", response=MyCardsOut, body=CardChangeIn, tag=TAG)
def my_cards_change(card_id, body):
    """Rename one's card, or switch it off or on."""
    card = cards.card_of(card_id, current_user)
    if body.name is not None:
        cards.rename(current_user, card, body.name)
    if body.enabled is not None:
        (cards.enable if body.enabled else cards.disable)(current_user, card)
    db.session.commit()
    return _mine()


@endpoint("DELETE", "/account/cards/<int:card_id>", response=MyCardsOut, tag=TAG)
def my_cards_remove(card_id):
    """Remove one's card; it can be linked again later, by anybody."""
    cards.remove(current_user, cards.card_of(card_id, current_user))
    db.session.commit()
    return _mine()


# --- Anybody's (admins) ------------------------------------------------------------------


def _owner_name(user):
    if user.deleted_at is not None:
        return "Erased account"
    member = user.member
    if member is not None and (member.first_name or member.last_name):
        return f"{member.first_name} {member.last_name}".strip()
    return user.email


class CardOwnerOut(Model):
    user_id: int
    name: str
    email: str
    picture_url: str | None


class AdminCardOut(CardOut):
    owner: CardOwnerOut


class AdminCardsOut(Model):
    items: list[AdminCardOut]


def _admin_card(card):
    owner = card.user
    return AdminCardOut(**_card(card).model_dump(), owner=CardOwnerOut(
        user_id=owner.id, name=_owner_name(owner), email=owner.email, picture_url=picture_url(owner)))


@endpoint("GET", "/admin/cards", response=AdminCardsOut, permissions=MANAGE, tag=TAG)
def admin_cards():
    """Every card linked now, the newest first, with whom it belongs to."""
    return AdminCardsOut(items=[_admin_card(card) for card in cards.all_cards()])


class CardReaderOut(Model):
    id: int
    name: str
    place: str | None


class CardWaitOut(Model):
    reader: CardReaderOut
    expires_at: UtcDateTime


class PersonCardsOut(Model):
    user_id: int
    items: list[CardOut]
    #: The readers a card can be tapped on, to link it there.
    readers: list[CardReaderOut]
    #: A reader waiting for this person's card, if one is.
    waiting: CardWaitOut | None
    most: int


def _reader(device):
    return CardReaderOut(id=device.id, name=devices.label(device), place=device.place)


def _person(user_id):
    user = db.session.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise NotFoundError("No such account.")
    return user


def _persons_cards(user):
    waits = cards.waiting_for(user)
    return PersonCardsOut(
        user_id=user.id,
        items=[_card(card) for card in cards.cards_of(user)],
        readers=[_reader(device) for device in cards.readers()],
        waiting=CardWaitOut(reader=_reader(waits[0].device), expires_at=waits[0].expires_at) if waits else None,
        most=cards.MOST_CARDS,
    )


@endpoint("GET", "/admin/accounts/<int:user_id>/cards", response=PersonCardsOut, permissions=MANAGE, tag=TAG)
def admin_person_cards(user_id):
    """A person's cards, and the readers one could be linked on."""
    return _persons_cards(_person(user_id))


@endpoint("POST", "/admin/accounts/<int:user_id>/cards", response=PersonCardsOut, body=CardLinkIn,
          permissions=MANAGE, status=201, tag=TAG)
@limiter.limit(RATELIMIT_CARD_LINK, key_func=rate_limit_person)
def admin_person_cards_link(user_id, body):
    """Link a card to this person: scanned with the admin's phone, or its ID typed in."""
    user = _person(user_id)
    cards.link(user, body.card_id, name=body.name, by=current_user, how=cards.BY_ADMIN_PHONE)
    db.session.commit()
    return _persons_cards(user)


class CardWaitIn(Model):
    device_id: int


@endpoint("POST", "/admin/accounts/<int:user_id>/cards/wait", response=PersonCardsOut, body=CardWaitIn,
          permissions=MANAGE, tag=TAG)
def admin_person_cards_wait(user_id, body):
    """Link the next unknown card tapped on this reader, within a minute, to this person."""
    user = _person(user_id)
    device = db.session.get(Device, body.device_id)
    if device is None:
        raise NotFoundError("No such reader.")
    cards.wait_at_reader(current_user, user, device)
    db.session.commit()
    return _persons_cards(user)


@endpoint("DELETE", "/admin/accounts/<int:user_id>/cards/wait", response=PersonCardsOut, permissions=MANAGE,
          tag=TAG)
def admin_person_cards_stop_waiting(user_id):
    """Stop waiting for this person's card."""
    user = _person(user_id)
    cards.stop_waiting(user)
    db.session.commit()
    return _persons_cards(user)


class AdminCardChangeIn(CardChangeIn):
    #: Blocked: refused everywhere; only an admin lifts it.
    blocked: bool | None = None


@endpoint("PATCH", "/admin/cards/<int:card_id>", response=AdminCardOut, body=AdminCardChangeIn,
          permissions=MANAGE, tag=TAG)
def admin_cards_change(card_id, body):
    """Rename anybody's card, switch it off or on, block it or lift the block."""
    card = cards.card_of(card_id)
    if body.name is not None:
        cards.rename(current_user, card, body.name)
    if body.enabled is not None:
        (cards.enable if body.enabled else cards.disable)(current_user, card)
    if body.blocked is not None:
        (cards.block if body.blocked else cards.unblock)(current_user, card)
    db.session.commit()
    return _admin_card(card)


@endpoint("DELETE", "/admin/cards/<int:card_id>", response=AdminCardOut, permissions=MANAGE, tag=TAG)
def admin_cards_remove(card_id):
    """Remove anybody's card."""
    card = cards.card_of(card_id)
    cards.remove(current_user, card)
    db.session.commit()
    return _admin_card(card)
