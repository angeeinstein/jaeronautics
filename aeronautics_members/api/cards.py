"""Cards linked to people (services/cards.py, docs/cards-plan.md): one's own
on My Account › Cards, and anybody's for admins (``cards.manage``).

A card's ID never leaves the portal again once linked: only its last
characters (``…E5F6``) are shown.
"""

from typing import Literal

from flask_login import current_user
from pydantic import Field

from ..app import limiter, rate_limit_person
from ..config import RATELIMIT_CARD_LINK, RATELIMIT_CARD_QR
from ..db_models import User, db
from ..permissions import Permission
from ..services import NotFoundError
from ..services import card_links, cards, devices
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


# --- Linked with a reader's QR code (services/card_links.py) ------------------------


class CardLinkOpenIn(Model):
    #: What this phone was given when it first opened the link; none the first time.
    claim: str | None = Field(default=None, max_length=40)


class CardLinkOut(Model):
    #: To send again: only the phone that opened the link first can use it.
    claim: str
    #: The card's last characters: "…E5F6".
    card: str
    #: The reader it was tapped on, and where that is.
    reader: str
    place: str | None
    tapped_at: UtcDateTime
    expires_at: UtcDateTime
    #: Whether somebody is signed in, to link it; else the page asks to sign in first.
    signed_in: bool


@endpoint("POST", "/card-links/<token>/open", response=CardLinkOut, body=CardLinkOpenIn, public=True, tag=TAG)
@limiter.limit(RATELIMIT_CARD_QR, key_func=rate_limit_person)
def card_link_open(token, body):
    """A reader's QR code opened on a phone: the card tapped there and the
    reader. The first phone keeps the link; signing in may come after."""
    link, claim = card_links.open_link(token, body.claim)
    db.session.commit()
    return CardLinkOut(claim=claim, card=f"…{link.card_tail}", reader=devices.label(link.device),
                       place=link.device.place, tapped_at=link.created_at, expires_at=link.expires_at,
                       signed_in=bool(current_user.is_authenticated))


class CardLinkUseIn(Model):
    claim: str = Field(max_length=40)
    name: str | None = Field(default=None, max_length=80)


class CardLinkedOut(Model):
    card: CardOut
    first_name: str | None


@endpoint("POST", "/card-links/<token>", response=CardLinkedOut, body=CardLinkUseIn, status=201, tag=TAG)
@limiter.limit(RATELIMIT_CARD_LINK, key_func=rate_limit_person)
def card_link_use(token, body):
    """Link the card of a reader's QR code to one's account, from the phone that opened it."""
    card = card_links.use(current_user, token, body.claim, name=body.name)
    db.session.commit()
    member = getattr(current_user, "member", None)
    return CardLinkedOut(card=_card(card), first_name=member.first_name if member is not None else None)


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


class PersonCardsOut(Model):
    user_id: int
    items: list[CardOut]


def _person(user_id):
    user = db.session.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise NotFoundError("No such account.")
    return user


def _persons_cards(user):
    return PersonCardsOut(user_id=user.id, items=[_card(card) for card in cards.cards_of(user)])


@endpoint("GET", "/admin/accounts/<int:user_id>/cards", response=PersonCardsOut, permissions=MANAGE, tag=TAG)
def admin_person_cards(user_id):
    """A person's cards; its owner links one at a reader, with the QR code shown there."""
    return _persons_cards(_person(user_id))


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
