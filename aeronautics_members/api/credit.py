"""Credit: one's own (My Account › Credit) and everybody's (Admin › Credit).

A member reads their balance and history and opens Stripe Checkout to top
up. Whoever holds ``credit.manage`` -- the admins and the association's
treasurer -- sees every balance and books what happens by hand: cash handed
over, credit paid out, corrections, refunds. Amounts are in cents. The rules
are in services/credit.py; the settings in api/admin_settings.py. Drawn by
frontend/src/pages/account/Credit.tsx and frontend/src/pages/admin/credit/.
"""

from typing import Literal

from flask import url_for
from flask_login import current_user
from pydantic import Field

from ..db_models import User, db
from ..permissions import Permission
from ..services import ConflictError, ExternalServiceError, NotFoundError
from ..services import credit
from ..services.pictures import picture_url, picture_urls
from ..services.teams import is_active_association_member
from ._core import Model, UtcDateTime, endpoint

TAG = "Credit"
MANAGE = [Permission.CREDIT_MANAGE]

Kind = Literal["top_up", "cash_in", "cash_out", "purchase", "refund", "reversal", "correction"]

KIND_LABELS = credit.KIND_LABELS


class CreditEntryOut(Model):
    id: int
    kind: Kind
    kind_label: str
    description: str
    amount_cents: int
    balance_after_cents: int
    at: UtcDateTime
    #: Stripe's receipt, for a top-up paid there.
    receipt_url: str | None
    #: The team something bought was for.
    team_name: str | None


def _entry(entry, receipt_endpoint="account.credit_receipt"):
    receipt = None
    if entry.kind == credit.TOP_UP and entry.payment is not None and entry.payment.stripe_invoice_id:
        receipt = url_for(receipt_endpoint, entry_id=entry.id)
    return CreditEntryOut(
        id=entry.id, kind=entry.kind, kind_label=KIND_LABELS.get(entry.kind, entry.kind),
        description=entry.description, amount_cents=entry.amount_cents,
        balance_after_cents=entry.balance_after_cents, at=entry.created_at, receipt_url=receipt,
        team_name=entry.team.name if entry.team is not None else None,
    )


# --- One's own ------------------------------------------------------------------------


class TopUpOut(Model):
    #: Why topping up is not possible now; None when it is.
    refused: str | None
    #: The suggested amounts that fit, smallest first.
    choices: list[int]
    least_cents: int
    #: The most this top-up may be: the most anybody holds, less the balance.
    most_cents: int
    max_balance_cents: int


class MyCreditOut(Model):
    balance_cents: int
    top_up: TopUpOut
    entries: list[CreditEntryOut]
    #: The whole history as a spreadsheet.
    export_url: str


def _visible():
    """Credit on, and this person a member or somebody who has credit."""
    if not credit.enabled() or not (is_active_association_member(current_user) or credit.has_credit(current_user)):
        raise NotFoundError("Credit is not available.", code="credit_not_available")


def _my_credit():
    current = credit.settings()
    refused = credit.may_top_up(current_user)
    return MyCreditOut(
        balance_cents=credit.balance_of(current_user),
        top_up=TopUpOut(
            refused=refused,
            choices=[] if refused else credit.top_up_choices(current_user),
            least_cents=current.min_top_up_cents,
            most_cents=credit.top_up_room(current_user),
            max_balance_cents=current.max_balance_cents,
        ),
        entries=[_entry(entry) for entry in credit.history(current_user)],
        export_url=url_for("account.credit_history_csv"),
    )


@endpoint("GET", "/account/credit", response=MyCreditOut, tag=TAG)
def my_credit():
    """The balance, whether and how much one may top up, and every entry, newest first."""
    _visible()
    return _my_credit()


class TopUpIn(Model):
    amount_cents: int = Field(ge=1)


class CheckoutOut(Model):
    #: Stripe's payment page.
    url: str


@endpoint("POST", "/account/credit/top-up", response=CheckoutOut, body=TopUpIn, tag=TAG)
def my_credit_top_up(body):
    """Open Stripe Checkout for this amount. The credit grows once Stripe says the money is there."""
    _visible()
    url = credit.start_top_up(current_user, body.amount_cents)
    db.session.commit()
    return CheckoutOut(url=url)


# --- Everybody's -------------------------------------------------------------------------


def _name(user):
    if user.deleted_at is not None:
        return "Erased account"
    member = user.member
    if member is not None and (member.first_name or member.last_name):
        return f"{member.first_name} {member.last_name}".strip()
    return user.email


class CreditPersonOut(Model):
    user_id: int
    name: str
    picture_url: str | None
    balance_cents: int
    erased: bool


class CreditTotalsOut(Model):
    #: What the association holds for its members: every balance together.
    held_cents: int
    #: Topped up and handed over in cash, the last 30 days.
    came_in_cents: int
    #: Spent, the last 30 days.
    spent_cents: int
    #: People with credit (or owing some).
    people: int


class AdminCreditEntryOut(CreditEntryOut):
    user_id: int
    name: str
    booked_by: str | None


class CreditOverviewOut(Model):
    enabled: bool
    totals: CreditTotalsOut
    people: list[CreditPersonOut]
    recent: list[AdminCreditEntryOut]
    #: Balances that are not the sum of their entries: should never be any.
    mismatched: list[int]


def _admin_entry(entry):
    base = _entry(entry, "admin.credit_receipt")
    return AdminCreditEntryOut(**base.model_dump(), user_id=entry.user_id, name=_name(entry.user),
                               booked_by=_name(entry.booked_by) if entry.booked_by is not None else None)


@endpoint("GET", "/admin/credit", response=CreditOverviewOut, permissions=MANAGE, tag=TAG)
def admin_credit():
    """Every balance, what came in, and the latest entries."""
    accounts = credit.accounts()
    pictures = picture_urls([account.user for account in accounts])
    return CreditOverviewOut(
        enabled=credit.enabled(),
        totals=CreditTotalsOut(**credit.totals()),
        people=[CreditPersonOut(user_id=account.user_id, name=_name(account.user),
                                picture_url=pictures.get(account.user_id), balance_cents=account.balance_cents,
                                erased=account.user.deleted_at is not None)
                for account in accounts],
        recent=[_admin_entry(entry) for entry in credit.recent_entries(limit=50)],
        mismatched=credit.mismatched_balances(),
    )


class CreditHolderOut(Model):
    user_id: int
    name: str
    email: str | None
    picture_url: str | None
    erased: bool
    #: An active member of the association: may top up.
    member: bool
    balance_cents: int
    #: What a refund would give back through Stripe now (cash is paid out by hand).
    refundable_cents: int
    max_balance_cents: int
    entries: list[AdminCreditEntryOut]
    #: Their account in Admin › Accounts, for whoever may see it.
    account_url: str | None


def _holder(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        raise NotFoundError("No such account.", code="account_not_found")
    return user


def _holder_out(user):
    erased = user.deleted_at is not None
    return CreditHolderOut(
        user_id=user.id, name=_name(user), email=None if erased else user.email,
        picture_url=None if erased else picture_url(user), erased=erased,
        member=is_active_association_member(user), balance_cents=credit.balance_of(user),
        refundable_cents=credit.refundable_cents(user), max_balance_cents=credit.settings().max_balance_cents,
        entries=[_admin_entry(entry) for entry in credit.history(user)],
        account_url=(f"/admin/accounts/{user.id}"
                     if current_user.can(Permission.ACCOUNTS_VIEW) and not erased else None),
    )


@endpoint("GET", "/admin/credit/<int:user_id>", response=CreditHolderOut, permissions=MANAGE, tag=TAG)
def admin_credit_holder(user_id):
    """One person's credit, every entry, and what a refund would give back."""
    return _holder_out(_holder(user_id))


class CashIn(Model):
    amount_cents: int = Field(ge=1)
    #: Credit paid out (cash or a transfer), rather than cash handed over.
    paid_out: bool = False
    note: str | None = Field(None, max_length=140)


@endpoint("POST", "/admin/credit/<int:user_id>/cash", response=CreditHolderOut, body=CashIn, permissions=MANAGE,
          tag=TAG)
def admin_credit_cash(user_id, body):
    """Cash handed over, added to the credit -- or credit paid out by hand."""
    user = _holder(user_id)
    if user.deleted_at is not None and not body.paid_out:
        raise ConflictError("This account was erased.", code="credit_account_erased")
    credit.book_cash(current_user, user, body.amount_cents, paid_out=body.paid_out, note=body.note)
    db.session.commit()
    return _holder_out(user)


class CorrectionIn(Model):
    #: Up (above zero) or down (below).
    amount_cents: int
    note: str = Field(max_length=140)


@endpoint("POST", "/admin/credit/<int:user_id>/correction", response=CreditHolderOut, body=CorrectionIn,
          permissions=MANAGE, tag=TAG)
def admin_credit_correction(user_id, body):
    """Put a mistake right, with the reason. Never below zero."""
    user = _holder(user_id)
    credit.correct(current_user, user, body.amount_cents, note=body.note)
    db.session.commit()
    return _holder_out(user)


class RefundOut(CreditHolderOut):
    refunded_cents: int
    #: Still on the account: cash, or payments Stripe would not refund any more.
    left_cents: int


@endpoint("POST", "/admin/credit/<int:user_id>/refund", response=RefundOut, permissions=MANAGE, tag=TAG)
def admin_credit_refund(user_id):
    """Give the credit back to the payments it came from, newest first."""
    user = _holder(user_id)
    try:
        refunded, left = credit.refund_balance(user, by=current_user)
    except ExternalServiceError:
        db.session.commit()  # what was refunded before Stripe went away stays booked
        raise
    db.session.commit()
    return RefundOut(**_holder_out(user).model_dump(), refunded_cents=refunded, left_cents=left)
