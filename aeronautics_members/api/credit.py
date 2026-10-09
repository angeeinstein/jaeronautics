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
from ..services import credit, credit_sales
from ..services.pictures import picture_url, picture_urls
from ..services.teams import is_active_association_member
from ._core import Model, UtcDateTime, endpoint

TAG = "Credit"
MANAGE = [Permission.CREDIT_MANAGE]

Kind = Literal["top_up", "cash_in", "cash_out", "purchase", "refund", "reversal", "correction", "taken_back"]

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
    #: Who sold it, for a sale or a sale taken back: a team, or the association.
    seller: str | None = None


def _entry(entry, receipt_endpoint="account.credit_receipt"):
    receipt = None
    if entry.kind == credit.TOP_UP and entry.payment is not None and entry.payment.stripe_invoice_id:
        receipt = url_for(receipt_endpoint, entry_id=entry.id)
    return CreditEntryOut(
        id=entry.id, kind=entry.kind, kind_label=KIND_LABELS.get(entry.kind, entry.kind),
        description=entry.description, amount_cents=entry.amount_cents,
        balance_after_cents=entry.balance_after_cents, at=entry.created_at, receipt_url=receipt,
        team_name=entry.team.name if entry.team is not None else None,
        seller=(credit_sales.seller_name(entry.team)
                if entry.kind in (credit.PURCHASE, credit.TAKEN_BACK) else None),
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
    #: A sale not taken back yet.
    may_take_back: bool = False


class SellerOut(Model):
    name: str
    #: None for the association itself.
    team_slug: str | None
    #: Sold, less what was taken back, the last 30 days and ever.
    earned_30_days: int
    earned: int
    items: int


def _sellers():
    from datetime import timedelta

    from ..services.clock import get_now_utc
    from ..services.teams import all_teams

    since = get_now_utc() - timedelta(days=30)
    rows = []
    for team in [None, *all_teams()]:
        listed = credit_sales.items(team)
        ever = credit_sales.sales_summary(team)["earned"]
        if team is not None and not listed and not ever:
            continue
        rows.append(SellerOut(name=credit_sales.seller_name(team), team_slug=team.slug if team else None,
                              earned_30_days=credit_sales.sales_summary(team, since)["earned"], earned=ever,
                              items=len(listed)))
    return rows


class CreditOverviewOut(Model):
    enabled: bool
    totals: CreditTotalsOut
    #: Who sells for credit -- the association first -- and what they sold.
    sellers: list[SellerOut]
    people: list[CreditPersonOut]
    recent: list[AdminCreditEntryOut]
    #: Balances that are not the sum of their entries: should never be any.
    mismatched: list[int]


def _admin_entry(entry, taken_back=frozenset()):
    base = _entry(entry, "admin.credit_receipt")
    return AdminCreditEntryOut(**base.model_dump(), user_id=entry.user_id, name=_name(entry.user),
                               booked_by=_name(entry.booked_by) if entry.booked_by is not None else None,
                               may_take_back=entry.kind == credit.PURCHASE and entry.id not in taken_back)


def _admin_entries(entries):
    taken_back = credit_sales.taken_back_ids(entries)
    return [_admin_entry(entry, taken_back) for entry in entries]


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
        recent=_admin_entries(credit.recent_entries(limit=50)),
        sellers=_sellers(),
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
    #: What can be sold to them, from every price list.
    items: list["SaleItemOut"]


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
        entries=_admin_entries(credit.history(user)),
        account_url=(f"/admin/accounts/{user.id}"
                     if current_user.can(Permission.ACCOUNTS_VIEW) and not erased else None),
        items=[] if erased else _sale_items(),
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


# --- Price lists and sales ------------------------------------------------------------


class SaleItemOut(Model):
    id: int
    name: str
    price_cents: int
    seller: str


def _sale_items():
    from ..services.teams import all_teams

    found = []
    for team in [None, *all_teams(include_archived=False)]:
        found += [SaleItemOut(id=item.id, name=item.name, price_cents=item.price_cents,
                              seller=credit_sales.seller_name(team)) for item in credit_sales.items(team)]
    return found


CreditHolderOut.model_rebuild()


class PriceItemOut(Model):
    id: int
    name: str
    price_cents: int
    #: Switched off: not sold, still named by the sales made.
    active: bool


class PriceListOut(Model):
    #: Credit is on: what is set here can be sold.
    enabled: bool
    items: list[PriceItemOut]


class PriceItemIn(Model):
    name: str = Field(max_length=200)
    price_cents: int


class PriceItemChangeIn(Model):
    name: str | None = Field(None, max_length=200)
    price_cents: int | None = None
    active: bool | None = None
    #: One place up (-1) or down (1).
    move: Literal[-1, 1] | None = None


def _price_list(team):
    return PriceListOut(enabled=credit.enabled(), items=[
        PriceItemOut(id=item.id, name=item.name, price_cents=item.price_cents, active=item.active)
        for item in credit_sales.items(team, include_inactive=True)
    ])


def _change(team, item_id, body):
    item = credit_sales.item_of(item_id, team)
    credit_sales.update_item(current_user, item, name=body.name, price_cents=body.price_cents, active=body.active)
    if body.move:
        credit_sales.move_item(current_user, item, body.move)
    db.session.commit()
    return _price_list(team)


@endpoint("GET", "/admin/credit/items", response=PriceListOut, permissions=MANAGE, tag=TAG)
def admin_credit_items():
    """The association's own price list: coffee in the student area, say."""
    return _price_list(None)


@endpoint("POST", "/admin/credit/items", response=PriceListOut, body=PriceItemIn, permissions=MANAGE, status=201,
          tag=TAG)
def admin_credit_item_add(body):
    """Add an item to the association's price list, at the end."""
    credit_sales.add_item(current_user, name=body.name, price_cents=body.price_cents)
    db.session.commit()
    return _price_list(None)


@endpoint("PUT", "/admin/credit/items/<int:item_id>", response=PriceListOut, body=PriceItemChangeIn,
          permissions=MANAGE, tag=TAG)
def admin_credit_item_change(item_id, body):
    """Rename, reprice, switch off or on, or move one place."""
    return _change(None, item_id, body)


def _priced_team(slug):
    from ..services import ServiceError
    from ..services import teams as teams_service
    from .team_manage import _may
    from .teams import _team

    if current_user.can(Permission.TEAMS_MONEY):
        try:
            team = teams_service.get_team(slug)
        except ServiceError as exc:
            raise NotFoundError("No such team.", code="team_not_found") from exc
    else:
        team = _team(slug)
    _may(team, teams_service.TeamPermission.EDIT_PRICES)
    return team


@endpoint("GET", "/teams/<slug>/manage/prices", response=PriceListOut, tag=TAG)
def team_prices(slug):
    """The team's price list, for what it sells for credit (a beer from its fridge)."""
    return _price_list(_priced_team(slug))


@endpoint("POST", "/teams/<slug>/manage/prices", response=PriceListOut, body=PriceItemIn, status=201, tag=TAG)
def team_price_add(slug, body):
    """Add an item to the team's price list, at the end."""
    team = _priced_team(slug)
    credit_sales.add_item(current_user, team=team, name=body.name, price_cents=body.price_cents)
    db.session.commit()
    return _price_list(team)


@endpoint("PUT", "/teams/<slug>/manage/prices/<int:item_id>", response=PriceListOut, body=PriceItemChangeIn,
          tag=TAG)
def team_price_change(slug, item_id, body):
    """Rename, reprice, switch off or on, or move one place."""
    return _change(_priced_team(slug), item_id, body)


class SaleIn(Model):
    item_id: int


@endpoint("POST", "/admin/credit/<int:user_id>/sale", response=CreditHolderOut, body=SaleIn, permissions=MANAGE,
          tag=TAG)
def admin_credit_sale(user_id, body):
    """Book a sale by hand: the item's price off their credit, for whoever sells it."""
    from ..db_models import CreditItem

    user = _holder(user_id)
    if user.deleted_at is not None:
        raise ConflictError("This account was erased.", code="credit_account_erased")
    item = db.session.get(CreditItem, body.item_id)
    if item is None:
        raise NotFoundError("No such item.", code="credit_item_not_found")
    credit_sales.sell(user, item, by=current_user)
    db.session.commit()
    return _holder_out(user)


class TakeBackIn(Model):
    note: str | None = Field(None, max_length=100)


@endpoint("POST", "/admin/credit/<int:user_id>/entries/<int:entry_id>/take-back", response=CreditHolderOut,
          body=TakeBackIn, permissions=MANAGE, tag=TAG)
def admin_credit_take_back(user_id, entry_id, body):
    """Give a sale back: the credit returns, and the seller no longer counts it."""
    user = _holder(user_id)
    credit_sales.take_back(current_user, credit.entry_of(user, entry_id), note=body.note)
    db.session.commit()
    return _holder_out(user)
