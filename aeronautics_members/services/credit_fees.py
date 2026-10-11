"""Which top-up paid for what: Stripe's fee on credit, followed to each sale.

A top-up costs a Stripe fee once (€0.55 on €20 by card, say); the credit it
bought is then spent bit by bit, with the association and with teams. When
the teams carry Stripe's fees (Admin › Money, "Stripe's fees"), each team's
sale carries the part of a top-up's fee that matches what it took of it, so
the team is not charged for the association's coffee, nor the other way
round. Decided with the maintainer: oldest top-up first, and every share
shown with the top-up it came from (docs/money-ledger-plan.md).

**The rule.** Each person's credit is a pile of what came in, oldest first:
top-ups (with their fee), cash handed over and corrections upwards (no fee).
What goes out takes from the oldest first; a refund or a chargeback takes
first from the top-up it went back to. Each part taken carries that share of
its top-up's fee -- the last part the rest, so the shares add up to the fee
exactly. A sale taken back puts back exactly what it took.

**Worked out, not stored.** The credit's entries never change, nor does a
fee once Stripe has said it, so the same entries always give the same
shares: they are worked out when asked. A fee Stripe has not said yet makes
the shares of that top-up unknown until it has (seconds, for a card).
"""

from dataclasses import dataclass

from ..db_models import CreditEntry, Payment, db
from . import credit

#: What adds to the pile; a correction does when it is upwards.
_IN = (credit.TOP_UP, credit.CASH_IN)
#: What takes from it; a correction does when it is downwards.
_OUT = (credit.PURCHASE, credit.CASH_OUT, credit.REFUND, credit.REVERSAL)


@dataclass
class Part:
    """A part of one outgoing entry, taken from one incoming one."""

    #: The incoming entry it came from (a top-up, cash, a correction); None
    #: when more went out than had come in (a chargeback after spending).
    source_id: int | None
    #: The top-up's payment, when it was one.
    payment_id: int | None
    amount: int
    #: Its share of the top-up's fee; 0 for cash; None while Stripe has not said.
    fee: int | None


@dataclass
class _Layer:
    entry: CreditEntry
    payment_id: int | None
    left: int
    fee_left: int | None


def _share(layer, amount):
    if layer.fee_left is None:
        return None
    if amount >= layer.left:
        return layer.fee_left
    # Rounded half up; the last part takes whatever is left, so the shares sum to the fee.
    return (layer.fee_left * amount * 2 + layer.left) // (layer.left * 2)


def _take(layer, amount):
    fee = _share(layer, amount)
    layer.left -= amount
    if layer.fee_left is not None:
        layer.fee_left -= fee
    return Part(source_id=layer.entry.id, payment_id=layer.payment_id, amount=amount, fee=fee)


def _fees_of(payment_ids):
    if not payment_ids:
        return {}
    return dict(db.session.execute(
        db.select(Payment.id, Payment.fee_cents).where(Payment.id.in_(list(payment_ids)))
    ).all())


def parts_for_people(user_ids):
    """{entry id: [Part]} for every entry of these people that took from or put back the pile."""
    user_ids = sorted({user_id for user_id in user_ids if user_id is not None})
    if not user_ids:
        return {}
    entries = db.session.execute(
        db.select(CreditEntry).where(CreditEntry.user_id.in_(user_ids)).order_by(CreditEntry.user_id, CreditEntry.id)
    ).scalars().all()
    return _walk(entries)[0]


def held_on(end=None):
    """What members' credit still holds before ``end`` (a UTC moment; None: now), by where it
    came from: ``{"top_ups": cents, "cash": cents}`` -- top-ups are money in Stripe, cash and
    corrections are not."""
    query = db.select(CreditEntry).order_by(CreditEntry.user_id, CreditEntry.id)
    if end is not None:
        query = query.where(CreditEntry.created_at < end)
    piles = _walk(db.session.execute(query).scalars().all())[1]
    held = {"top_ups": 0, "cash": 0}
    for pile in piles.values():
        for layer in pile:
            held["top_ups" if layer.payment_id else "cash"] += max(layer.left, 0)
    return held


def _walk(entries):
    """Work through entries (by person, oldest first): the parts each outgoing one took, and
    what is left in each person's pile."""
    fees = _fees_of({entry.payment_id for entry in entries if entry.kind == credit.TOP_UP and entry.payment_id})
    parts = {}
    piles = {}
    for entry in entries:
        pile = piles.setdefault(entry.user_id, [])
        if entry.kind in _IN or (entry.kind == credit.CORRECTION and entry.amount_cents > 0):
            fee = fees.get(entry.payment_id) if entry.kind == credit.TOP_UP else 0
            pile.append(_Layer(entry=entry, payment_id=entry.payment_id if entry.kind == credit.TOP_UP else None,
                               left=entry.amount_cents, fee_left=fee))
        elif entry.kind == credit.TAKEN_BACK and entry.reverses_id not in parts:
            pile.append(_Layer(entry=entry, payment_id=None, left=entry.amount_cents, fee_left=0))
        elif entry.kind == credit.TAKEN_BACK:
            # Put back exactly what the sale took, where it took it from.
            by_id = {layer.entry.id: layer for layer in pile}
            undone = []
            for part in parts[entry.reverses_id]:
                layer = by_id.get(part.source_id)
                if layer is not None:
                    layer.left += part.amount
                    if layer.fee_left is not None and part.fee is not None:
                        layer.fee_left += part.fee
                undone.append(Part(source_id=part.source_id, payment_id=part.payment_id, amount=-part.amount,
                                   fee=-part.fee if part.fee is not None else None))
            parts[entry.id] = undone
        elif entry.kind in _OUT or (entry.kind == credit.CORRECTION and entry.amount_cents < 0):
            parts[entry.id] = _take_from(pile, -entry.amount_cents,
                                         first=entry.payment_id if entry.kind in (credit.REFUND, credit.REVERSAL)
                                         else None)
    return parts, piles


def _take_from(pile, amount, first=None):
    taken = []
    order = [layer for layer in pile if first is not None and layer.payment_id == first]
    order += [layer for layer in pile if layer not in order]
    for layer in order:
        if amount <= 0:
            break
        if layer.left <= 0:
            continue
        part = _take(layer, min(amount, layer.left))
        taken.append(part)
        amount -= part.amount
    if amount > 0:
        taken.append(Part(source_id=None, payment_id=None, amount=amount, fee=0))
    return taken


def fee_of(parts):
    """The parts' fee shares together; None when any is not known yet."""
    if any(part.fee is None for part in parts):
        return None
    return sum(part.fee for part in parts)
