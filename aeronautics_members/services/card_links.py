"""Linking a card at a reader with a QR code (docs/cards-plan.md, "Linking a
card at a reader with a QR code"; the device's side: docs/device-protocol.md,
section 7.4).

1. A card nobody has linked is tapped on a reader. The portal keeps the card
   as cards are kept -- its keyed hash and end, never its ID -- in a
   :class:`CardLink` with a random token (:func:`offer`), and the reader
   shows the token's address as a QR code. The same card tapped again starts
   a new link, and its earlier ones end, opened or not: whoever lost theirs
   taps again.
2. The reader asks about it every few seconds while it shows the code
   (:func:`state_for_device`), and moves on as soon as a phone has opened it.
3. The first phone to open the address keeps the link (:func:`open_link`,
   which hands it a ``claim``); another phone is told it is in use. Signed
   in, it links the card to that account (:func:`use`) -- from what the link
   kept: the reader takes no part any more. A link works once, for
   ``LASTS``.

Any signed-in account may link a card this way, as with Web NFC: paying needs
credit, and topping up needs an active membership.
"""

import secrets
from datetime import timedelta, timezone

from ..db_models import Card, CardLink, db
from . import ConflictError, NotFoundError
from . import cards
from .clock import get_now_utc
from .locking import locked

#: How long a link works, from the tap: time enough on the phone (signing in,
#: a forgotten password), since the reader does not wait for it.
LASTS = timedelta(minutes=10)
#: How long the reader shows the QR code at most, and how often it asks
#: meanwhile (seconds): sent with the link, so they change without a firmware update.
SHOW_FOR = 30
CHECK_EVERY = 2
#: Links are deleted after this (the nightly clean-up).
KEPT = timedelta(days=1)

# What the reader is told about a link.
WAITING = "waiting"
OPENED = "opened"
LINKED = "linked"
EXPIRED = "expired"

#: The page the QR code leads to (frontend/src/pages/public/LinkCard.tsx).
PAGE = "/link/{token}"


def _aware(moment):
    return moment if moment is None or moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _token():
    return secrets.token_urlsafe(16)


def offer(device, card_id, now=None):
    """A link for the unknown card ``card_id`` just tapped on ``device``. Ends
    the card's earlier links not used yet, opened or not."""
    now = now or get_now_utc()
    card_id = cards.normalize(card_id)
    hashed = cards.id_hash(card_id)
    db.session.execute(
        db.update(CardLink)
        .where(CardLink.card_hash == hashed, CardLink.linked_at.is_(None), CardLink.ended_at.is_(None))
        .values(ended_at=now)
    )
    link = CardLink(token=_token(), device_id=device.id, card_hash=hashed, card_tail=card_id[-cards.TAIL:],
                    created_at=now, expires_at=now + LASTS)
    db.session.add(link)
    db.session.flush()
    return link


def for_device(link):
    """What the reader is sent with the refusal: the address for the QR code,
    how long to show it, and how often to ask about it."""
    from ..security_utils import get_public_base_url

    return {"id": link.id, "url": get_public_base_url() + PAGE.format(token=link.token),
            "show_for": SHOW_FOR, "check_every": CHECK_EVERY}


def _over(link, now):
    return link.ended_at is not None or _aware(link.expires_at) <= now


def _first_name(card):
    member = getattr(card.user, "member", None) if card is not None else None
    return member.first_name if member is not None and member.first_name else None


def state_for_device(device, link_id, now=None):
    """Where the link stands, for the reader that showed it."""
    now = now or get_now_utc()
    link = db.session.get(CardLink, link_id)
    if link is None or link.device_id != device.id:
        raise NotFoundError("This device has no such card link.", code="card_link_unknown")
    if link.linked_at is not None:
        return {"state": LINKED, "first_name": _first_name(link.card)}
    if _over(link, now):
        return {"state": EXPIRED}
    if link.opened_at is not None:
        return {"state": OPENED}
    return {"state": WAITING}


# --- On the phone ---------------------------------------------------------------------------


def _usable(token, now):
    """The link with this token, locked, if it can still be used."""
    link = db.session.execute(locked(db.select(CardLink).filter_by(token=token))).scalar_one_or_none()
    if link is not None and link.linked_at is not None:
        raise ConflictError("This link was used already: the card is linked.", code="card_link_used")
    if link is None or _over(link, now):
        raise NotFoundError("This link no longer works. Tap your card on the reader again for a new one.",
                            code="card_link_expired")
    return link


def _taken():
    return ConflictError("This link is open on another phone. Tap your card on the reader again for a new one.",
                         code="card_link_taken")


def _not_linked_yet(link):
    if db.session.execute(db.select(Card.id).filter_by(linked_hash=link.card_hash)).first() is not None:
        raise ConflictError("This card is already linked to an account.", code="card_link_card_linked")


def open_link(token, claim=None, now=None):
    """The phone opening the link: the first one keeps it, with the ``claim``
    returned (to be sent again); another phone is refused. Returns the link
    and the claim."""
    now = now or get_now_utc()
    link = _usable(token, now)
    if link.opened_at is None:
        link.opened_at, link.claim = now, _token()
    elif not claim or not secrets.compare_digest(claim, link.claim or ""):
        raise _taken()
    _not_linked_yet(link)
    return link, link.claim


def use(user, token, claim, *, name=None, now=None):
    """Link the link's card to ``user``, from the phone that opened it. Returns the card."""
    now = now or get_now_utc()
    link = _usable(token, now)
    if link.opened_at is None or not claim or not secrets.compare_digest(claim, link.claim or ""):
        raise _taken()
    card = cards.link_hashed(user, link.card_hash, link.card_tail, name=name, by=user, how=cards.BY_QR)
    link.linked_at, link.card_id = now, card.id
    return card


def forget_old(now=None):
    """Links of the day before and older deleted (the nightly clean-up). Returns how many."""
    cutoff = (now or get_now_utc()) - KEPT
    return db.session.query(CardLink).filter(CardLink.created_at < cutoff).delete(synchronize_session=False)
