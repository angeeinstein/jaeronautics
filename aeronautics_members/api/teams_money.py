"""A team's money, the team's side: what its members paid, what the
association transferred and what is open, every payment and transfer, and
the account the money goes to -- for its leads and its treasurer, and for
the association's treasurer and admins on every team (also an archived one,
and while teams are switched off: what is owed stays owed).

Only who may change the bank details sees the whole IBAN. Transfers are
recorded on the association's side (api/admin_money.py). Drawn by
frontend/src/pages/teams/manage/Money.tsx.
"""

from datetime import date

from flask import abort, url_for
from flask_login import current_user
from pydantic import Field

from ..db_models import db
from ..permissions import Permission
from ..services import ServiceError
from ..services import team_money as money
from ..services import teams as teams_service
from ..services.notifications import flush_marked_notification_channels
from ._core import Model, endpoint
from .admin_money import CreditSales, Period, TeamPayment, Transfer, credit_sales_out, team_payment_out
from .team_manage import _may
from .teams import _team

TAG = "Teams"
P = teams_service.TeamPermission


class TeamBankOut(Model):
    account_holder: str | None
    #: Whole for whoever may change it; otherwise shortened ("AT61 … 3201").
    iban: str | None
    bic: str | None


class TeamFundsOut(Model):
    slug: str
    name: str
    #: Fees and credit sales together.
    earned: int
    fees: int
    sales: CreditSales
    #: Paid, but carrying a Stripe fee not known yet: counted once Stripe says (overnight).
    waiting_for_fee: int
    paid_out: int
    open: int
    last_transfer_on: date | None
    bank: TeamBankOut
    may_edit_bank: bool
    #: The association's treasurer and admins record transfers, on the admin side.
    records_transfers: bool
    by_period: list[Period]
    transfers: list[Transfer]
    payments: list[TeamPayment]
    #: The payments as a spreadsheet (CSV).
    export_url: str


def _money_team(slug):
    if current_user.can(Permission.TEAMS_MONEY):
        try:
            team = teams_service.get_team(slug)
        except ServiceError:
            abort(404)
    else:
        team = _team(slug)
    _may(team, P.VIEW_MONEY)
    return team


def _funds_out(team):
    found = money.summary(team)
    may_edit = teams_service.can_in_team(current_user, team, P.EDIT_BANK_DETAILS)
    last = found.get("last_payout")
    return TeamFundsOut(
        slug=team.slug, name=team.name, earned=found["earned"], fees=found["fees"],
        sales=credit_sales_out(found), waiting_for_fee=found["waiting_for_fee"],
        paid_out=found["paid_out"], open=found["open"],
        last_transfer_on=last.paid_on if last else None,
        bank=TeamBankOut(account_holder=team.bank_account_holder,
                         iban=team.bank_iban if may_edit else (money.masked_iban(team.bank_iban) or None),
                         bic=team.bank_bic),
        may_edit_bank=may_edit, records_transfers=current_user.can(Permission.TEAMS_MONEY),
        by_period=[Period(paid_until=until, earned=cents) for until, cents in found["by_period"]],
        transfers=[Transfer(id=payout.id, paid_on=payout.paid_on, amount=payout.amount_cents,
                            reference=payout.reference, to=money.masked_iban(payout.iban) or None)
                   for payout in found["payouts"]],
        payments=[team_payment_out(payment) for payment in found["payments"]],
        export_url=url_for("teams.team_money_export", slug=team.slug),
    )


@endpoint("GET", "/teams/<slug>/money", response=TeamFundsOut, tag=TAG)
def team_funds(slug):
    """The team's money: the balance, where it is paid, and every payment and transfer."""
    return _funds_out(_money_team(slug))


class TeamBankIn(Model):
    account_holder: str | None = Field(None, max_length=70)
    iban: str | None = Field(None, max_length=42)
    bic: str | None = Field(None, max_length=11)


class TeamBankSavedOut(Model):
    #: Whether anything changed.
    changed: bool
    funds: TeamFundsOut


@endpoint("PUT", "/teams/<slug>/money/bank", response=TeamBankSavedOut, body=TeamBankIn, tag=TAG)
def team_funds_bank(slug, body):
    """Where the association transfers the team's money. Checked, logged, and the association's treasurer is told."""
    team = _money_team(slug)
    _may(team, P.EDIT_BANK_DETAILS)
    changed = money.update_bank_details(current_user, team, account_holder=body.account_holder, iban=body.iban,
                                        bic=body.bic)
    db.session.commit()
    flush_marked_notification_channels()
    return TeamBankSavedOut(changed=changed is not False, funds=_funds_out(team))
