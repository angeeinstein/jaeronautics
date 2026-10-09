"""Money, the association's side: what every team's members paid and what was
passed on to the teams, the money on Stripe for a period, and one team's money
-- its transfers and where they go.

For whoever holds ``teams.money``: the association's treasurer and the
admins. The leads see their own team's money on its page. Amounts are in
cents. The rules are in services/team_money.py and services/money_overview.py.
Drawn by frontend/src/pages/admin/money/.
"""

from datetime import date
from typing import Literal

import stripe
from flask import current_app, url_for
from flask_login import current_user
from pydantic import Field

from ..db_models import db
from ..permissions import Permission
from ..services import ExternalServiceError
from ..services import money_overview as overview_service
from ..services import team_money as money
from ..services import teams as teams_service
from ..services.clock import get_membership_today
from ..services.notifications import flush_marked_notification_channels
from ._core import Model, UtcDateTime, endpoint

TAG = "Admin"
PERMISSIONS = [Permission.TEAMS_MONEY]


class TeamRef(Model):
    slug: str
    name: str
    status: Literal["active", "archived"]
    logo_url: str | None


def _team_ref(team):
    logo = url_for("teams.team_logo", token=team.logo_token) if team.logo_token else None
    return TeamRef(slug=team.slug, name=team.name, status=team.status, logo_url=logo)


class MoneyRow(Model):
    team: TeamRef
    #: What its members paid, less refunds and chargebacks.
    earned: int
    #: What the association transferred to the team.
    paid_out: int
    open: int
    last_transfer_on: date | None
    #: The account transfers go to, shortened ("AT61 … 3201"); ``None`` when the team has given none.
    account: str | None


class MoneyOut(Model):
    teams: list[MoneyRow]
    total_open: int
    today: date


@endpoint("GET", "/admin/money", response=MoneyOut, permissions=PERMISSIONS, tag=TAG)
def admin_money():
    """Every team that charges or ever earned anything: earned, transferred, open."""
    rows = [
        MoneyRow(team=_team_ref(row["team"]), earned=row["earned"], paid_out=row["paid_out"], open=row["open"],
                 last_transfer_on=row["last_payout"].paid_on if row["last_payout"] else None,
                 account=money.masked_iban(row["team"].bank_iban) or None)
        for row in money.all_teams_money()
    ]
    return MoneyOut(teams=rows, total_open=sum(row.open for row in rows), today=get_membership_today())


# --- On Stripe ---------------------------------------------------------------------------


class Totals(Model):
    received: int
    #: SEPA debits announced and then not honoured.
    returned: int
    refunded: int
    #: Chargebacks.
    taken_back: int
    #: Stripe's fees.
    fees: int
    net: int


class Books(Model):
    membership: Totals
    team: Totals
    #: Everything paid without a membership or team invoice: the webshop, events, payment links.
    other: Totals
    total: Totals
    #: Of the total fees, what Stripe billed on its own.
    stripe_fees: int
    #: Bookings that are no payment at all.
    other_bookings: int
    #: Paid out to the association's bank account in the period.
    paid_out: int
    #: What Stripe held at the end of the period, by its own bookings.
    balance_at_end: int


class InStripeNow(Model):
    available: int
    pending: int


class Mismatch(Model):
    """A paid invoice and the portal's records that do not agree."""

    kind: Literal["team", "membership"]
    what: str
    invoice: str | None
    amount: int | None
    #: Stripe's amount, where it differs from the portal's.
    stripe_amount: int | None
    who: str


class OverviewOut(Model):
    #: ``None``: from the first booking.
    since: date | None
    until: date
    books: Books
    #: What Stripe says it holds now; only for a period up to today.
    in_stripe_now: InStripeNow | None
    #: Of the net, what the teams' members paid.
    teams_share: int
    association_own: int
    owed_to_teams: int
    mismatches: list[Mismatch]
    checked_at: UtcDateTime


class OverviewQuery(Model):
    #: Empty for everything from the start.
    since: date | None = None
    #: Empty for today; never later.
    until: date | None = None


def _totals(values):
    return Totals(**{key: values[key] for key in ("received", "returned", "refunded", "taken_back", "fees", "net")})


def _mismatches(kind, problems):
    return [Mismatch(kind=kind, what=problem["what"], invoice=problem.get("invoice"), amount=problem.get("amount"),
                     stripe_amount=problem.get("stripe_amount"), who=problem.get("who") or "")
            for problem in problems]


@endpoint("GET", "/admin/money/overview", response=OverviewOut, query=OverviewQuery, permissions=PERMISSIONS,
          tag=TAG)
def admin_money_overview(query):
    """The money on Stripe for a period, by what it was for, checked invoice by invoice
    against the portal's records. Asks Stripe, which takes a few seconds."""
    since, until = overview_service.period_asked(query.since, query.until, get_membership_today())
    try:
        found = overview_service.overview(since, until)
    except stripe.StripeError as exc:
        current_app.logger.warning("Money overview: Stripe could not be asked: %s", exc)
        raise ExternalServiceError("Stripe could not be asked. Please try again in a few minutes.",
                                   code="stripe_unavailable") from exc
    books = found["books"]
    by_product = books["by_product"]
    now = found["in_stripe_now"]
    return OverviewOut(
        since=found["since"], until=found["until"],
        books=Books(
            membership=_totals(by_product[overview_service.MEMBERSHIP]), team=_totals(by_product[overview_service.TEAM]),
            other=_totals(by_product[overview_service.OTHER]), total=_totals(books["total"]),
            stripe_fees=books["account"]["stripe_fees"], other_bookings=books["account"]["other"],
            paid_out=books["paid_out"], balance_at_end=books["balance_at_end"],
        ),
        in_stripe_now=InStripeNow(**now) if now else None,
        teams_share=found["teams_share"], association_own=found["association_own"],
        owed_to_teams=found["owed_to_teams"],
        mismatches=_mismatches("team", found["checks"]["teams"])
        + _mismatches("membership", found["checks"]["membership"]),
        checked_at=found["checked_at"],
    )


# --- One team ------------------------------------------------------------------------------


class Bank(Model):
    account_holder: str | None
    iban: str | None
    bic: str | None


class Period(Model):
    #: The end of the period the payments paid for.
    paid_until: date | None
    earned: int


class Transfer(Model):
    id: int
    paid_on: date
    amount: int
    reference: str | None
    #: The account it went to, shortened.
    to: str | None


class TeamPayment(Model):
    id: int
    paid_at: UtcDateTime | None
    #: Blank once the account was erased.
    name: str | None
    paid_until: date | None
    amount: int
    refunded: int
    #: Taken back by a chargeback: counts nothing.
    disputed: bool
    #: Stripe's fee, where this payment carries it (Admin › Money, "Stripe's fees").
    fee: int | None = None
    #: What the team is owed of it; None while the fee it carries is not known yet.
    counts: int | None


class SoldItem(Model):
    name: str
    #: Sales that stand (taken back ones count for neither).
    count: int
    earned: int


class CreditSales(Model):
    """What the team sold for credit, less what was taken back and the association's share."""

    earned: int
    count: int
    by_item: list[SoldItem]


def credit_sales_out(found):
    sales = found["sales"]
    return CreditSales(earned=sales["earned"], count=sales["count"],
                       by_item=[SoldItem(**row) for row in sales["by_item"]])


def team_payment_out(payment):
    return TeamPayment(id=payment.id, paid_at=payment.paid_at, name=money.payer_name(payment) or None,
                       paid_until=payment.covers_until, amount=payment.amount_cents,
                       refunded=payment.refunded_cents or 0, disputed=payment.status == "disputed",
                       fee=payment.fee_cents if payment.team_bears_fee else None, counts=money.counts(payment))


class TeamMoneyOut(Model):
    team: TeamRef
    #: Fees and credit sales together.
    earned: int
    #: Of it, from fees.
    fees: int
    sales: CreditSales
    #: Paid, but carrying a Stripe fee not known yet: counted once Stripe says (overnight).
    waiting_for_fee: int
    paid_out: int
    open: int
    bank: Bank
    #: The reference a transfer gets unless another is typed.
    reference: str
    today: date
    by_period: list[Period]
    transfers: list[Transfer]
    payments: list[TeamPayment]
    #: The payments as a spreadsheet (CSV).
    export_url: str


def _team(slug):
    # Also an archived team, and while teams are switched off: what is owed stays owed.
    return teams_service.get_team(slug)


def _team_money_out(team):
    found = money.summary(team)
    return TeamMoneyOut(
        team=_team_ref(team), earned=found["earned"], fees=found["fees"], sales=credit_sales_out(found),
        waiting_for_fee=found["waiting_for_fee"], paid_out=found["paid_out"], open=found["open"],
        bank=Bank(account_holder=team.bank_account_holder, iban=team.bank_iban, bic=team.bank_bic),
        reference=money.default_reference(team), today=get_membership_today(),
        by_period=[Period(paid_until=until, earned=cents) for until, cents in found["by_period"]],
        transfers=[Transfer(id=payout.id, paid_on=payout.paid_on, amount=payout.amount_cents,
                            reference=payout.reference, to=money.masked_iban(payout.iban) or None)
                   for payout in found["payouts"]],
        payments=[team_payment_out(payment) for payment in found["payments"]],
        export_url=url_for("teams.team_money_export", slug=team.slug),
    )


@endpoint("GET", "/admin/money/<slug>", response=TeamMoneyOut, permissions=PERMISSIONS, tag=TAG)
def admin_team_money(slug):
    """One team's money: the balance, where transfers go, and every payment and transfer."""
    return _team_money_out(_team(slug))


class TransferIn(Model):
    #: In euros, as typed: "240", "240.00" or "240,00".
    amount: str = Field(max_length=20)
    #: The day it was sent; today when left out. Not in the future.
    paid_on: date | None = None
    #: Empty for the usual one.
    reference: str | None = Field(None, max_length=140)


@endpoint("POST", "/admin/money/<slug>/transfers", response=TeamMoneyOut, body=TransferIn,
          permissions=PERMISSIONS, status=201, tag=TAG)
def admin_team_money_transfer(slug, body):
    """Record that the association transferred money to the team -- once it has really been sent.
    Never more than is open; kept with the account it went to."""
    team = _team(slug)
    money.record_payout(current_user, team, amount=body.amount, paid_on=body.paid_on, reference=body.reference)
    db.session.commit()
    return _team_money_out(team)


class BankIn(Model):
    account_holder: str | None = Field(None, max_length=70)
    iban: str | None = Field(None, max_length=42)
    bic: str | None = Field(None, max_length=11)


@endpoint("PUT", "/admin/money/<slug>/bank", response=TeamMoneyOut, body=BankIn, permissions=PERMISSIONS,
          tag=TAG)
def admin_team_money_bank(slug, body):
    """Where the team is paid. Checked, logged, and the other treasurers are told."""
    team = _team(slug)
    money.update_bank_details(current_user, team, account_holder=body.account_holder, iban=body.iban, bic=body.bic)
    db.session.commit()
    flush_marked_notification_channels()
    return _team_money_out(team)


# --- Who carries Stripe's fees ----------------------------------------------------------


class MoneySettings(Model):
    #: Stripe's fee on a team fee: paid by the association, or taken from what the team is owed.
    fee_payer: Literal["association", "team"]
    #: Of what a team sells for credit, the association keeps this share, in hundredths of a percent.
    credit_share_bps: int = Field(ge=0, le=2000)


class MoneySettingsSavedOut(Model):
    changed: list[str]


@endpoint("GET", "/admin/money/settings", response=MoneySettings, permissions=PERMISSIONS, tag=TAG)
def admin_money_settings():
    """Who carries Stripe's fees. Each payment and sale keeps how it was when it was made."""
    return MoneySettings(fee_payer="team" if money.teams_bear_fees() else "association",
                         credit_share_bps=money.credit_share_bps())


@endpoint("PUT", "/admin/money/settings", response=MoneySettingsSavedOut, body=MoneySettings,
          permissions=PERMISSIONS, tag=TAG)
def admin_money_settings_save(body):
    """Change them, from now on: what was paid and sold before stays as it was counted."""
    changed = money.save_money_settings(current_user, fee_payer=body.fee_payer,
                                        credit_share_bps=body.credit_share_bps)
    db.session.commit()
    return MoneySettingsSavedOut(changed=changed)
