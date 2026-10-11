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
from ..services import money_checks
from ..services import money_overview as overview_service
from ..services import stripe_books
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
    #: The check against Stripe reaches no further back: the day the portal took over.
    records_since: date | None = None


@endpoint("GET", "/admin/money", response=MoneyOut, permissions=PERMISSIONS, tag=TAG)
def admin_money():
    """Every team that charges or ever earned anything: earned, transferred, open."""
    rows = [
        MoneyRow(team=_team_ref(row["team"]), earned=row["earned"], paid_out=row["paid_out"], open=row["open"],
                 last_transfer_on=row["last_payout"].paid_on if row["last_payout"] else None,
                 account=money.masked_iban(row["team"].bank_iban) or None)
        for row in money.all_teams_money()
    ]
    return MoneyOut(teams=rows, total_open=sum(row.open for row in rows), today=get_membership_today(),
                    records_since=overview_service.records_since())


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
    credit: Totals
    #: Everything else paid into the account: the webshop, events, payment links.
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


class CreditHeld(Model):
    #: Topped up through Stripe: in the balance above.
    top_ups: int
    #: Handed over in cash or booked by hand: not in Stripe.
    cash: int


class Difference(Model):
    """Where Stripe's books and the portal's records disagree (services/money_checks.py)."""

    id: int
    product: Literal["membership", "team", "credit", "other"]
    what: str
    #: The invoice, payment or charge to look up in Stripe.
    reference: str | None
    amount: int | None
    #: Stripe's amount, where it differs from the portal's.
    stripe_amount: int | None
    who: str
    first_seen_at: UtcDateTime
    emailed_at: UtcDateTime | None
    resolved_at: UtcDateTime | None


class OverviewOut(Model):
    #: ``None``: from the first booking.
    since: date | None
    until: date
    books: Books
    #: At the end of the period.
    owed_to_teams: int
    credit_held: CreditHeld
    #: What is left in Stripe once the teams and the members have what is theirs.
    association_own: int
    #: When Stripe's book was last fetched; ``None``: never yet.
    fetched_at: UtcDateTime | None
    #: A fetch is waiting or under way: the figures change when it is done.
    fetching: bool
    #: What Stripe said it holds then, and what the copy's bookings add up to.
    stripe_balance: int | None
    books_balance: int | None
    #: Why the last fetch failed, if it did.
    last_error: str | None
    checked_at: UtcDateTime | None
    differences: list[Difference]
    #: Resolved in the last 30 days.
    resolved: list[Difference]
    #: Whether differences are emailed to the admins and the treasurer.
    emails: bool


class OverviewQuery(Model):
    #: Empty for everything from the start.
    since: date | None = None
    #: Empty for today; never later.
    until: date | None = None


def _totals(values):
    return Totals(**{key: values[key] for key in ("received", "returned", "refunded", "taken_back", "fees", "net")})


def _difference_out(row):
    return Difference(id=row.id, product=row.product, what=row.what, reference=row.reference,
                      amount=row.amount_cents, stripe_amount=row.stripe_amount_cents, who=row.who or "",
                      first_seen_at=row.first_seen_at, emailed_at=row.emailed_at, resolved_at=row.resolved_at)


def _overview_out(since, until):
    found = overview_service.overview(since, until)
    books = found["books"]
    by_product = books["by_product"]
    return OverviewOut(
        since=found["since"], until=found["until"],
        books=Books(
            membership=_totals(by_product["membership"]), team=_totals(by_product["team"]),
            credit=_totals(by_product["credit"]), other=_totals(by_product["other"]), total=_totals(books["total"]),
            stripe_fees=books["account"]["stripe_fees"], other_bookings=books["account"]["other"],
            paid_out=books["paid_out"], balance_at_end=books["balance_at_end"],
        ),
        owed_to_teams=found["owed_to_teams"], credit_held=CreditHeld(**found["credit_held"]),
        association_own=found["association_own"], fetched_at=found["fetched_at"],
        fetching=stripe_books.fetching(),
        stripe_balance=found["stripe_balance"], books_balance=found["books_balance"],
        last_error=found["last_error"], checked_at=found["checked_at"],
        differences=[_difference_out(row) for row in money_checks.open_differences()],
        resolved=[_difference_out(row) for row in money_checks.recently_resolved()],
        emails=money.difference_emails(),
    )


@endpoint("GET", "/admin/money/overview", response=OverviewOut, query=OverviewQuery, permissions=PERMISSIONS,
          tag=TAG)
def admin_money_overview(query):
    """The money on Stripe for a period, by what it was for, from the portal's copy of Stripe's
    book -- at once; and where Stripe and the portal's records differ."""
    since, until = overview_service.period_asked(query.since, query.until, get_membership_today())
    return _overview_out(since, until)


@endpoint("POST", "/admin/money/fetch", response=OverviewOut, query=OverviewQuery, permissions=PERMISSIONS,
          tag=TAG)
def admin_money_fetch(query):
    """Fetch what is new in Stripe's book now, check it, and answer with the overview.
    The very first fetch reads the whole book and runs in the background instead."""
    if stripe_books.state().fetched_through is None:
        stripe_books.request_sync("The first fetch, asked for on Admin › Money")
        db.session.commit()
    else:
        try:
            stripe_books.fetch_now()
        except stripe.StripeError as exc:
            current_app.logger.warning("Money: Stripe's book could not be fetched: %s", exc)
            raise ExternalServiceError("Stripe could not be asked. Please try again in a few minutes.",
                                       code="stripe_unavailable") from exc
        db.session.commit()
    since, until = overview_service.period_asked(query.since, query.until, get_membership_today())
    return _overview_out(since, until)


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
    #: Stripe's fee for it; ``None`` while Stripe has not said. Who carries it: ``fee_carried_by``.
    fee: int | None = None
    fee_carried_by: Literal["team", "association"] = "association"
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


def team_payment_out(payment, found):
    return TeamPayment(id=payment.id, paid_at=payment.paid_at, name=money.payer_name(payment) or None,
                       paid_until=payment.covers_until, amount=payment.amount_cents,
                       refunded=payment.refunded_cents or 0, disputed=payment.status == "disputed",
                       fee=payment.fee_cents,
                       fee_carried_by="team" if found["statement"]["carry"] else "association",
                       counts=money.counts(payment, found["statement"]["groups"]))


class StatementLine(Model):
    """One line of a team's money: a payment, a refund, Stripe's fee, a sale, its share of a
    top-up's fee, a transfer. Lines of one payment or sale share their ``group``."""

    day: date | None
    kind: Literal["fee_paid", "refunded", "taken_back", "stripe_fee", "stripe_later_fee", "sold",
                  "sale_taken_back", "kept", "top_up_fee", "transfer"]
    text: str
    amount: int
    group: str
    detail: str
    #: Held back until Stripe says the fee it carries.
    waiting: bool


class StatementRow(Model):
    kind: str
    label: str
    amount: int


class Statement(Model):
    """How the team's figures come about: each kind of line added up, then every line."""

    #: Whether the teams carry Stripe's fees (Admin › Money, the settings).
    teams_carry_fees: bool
    rows: list[StatementRow]
    earned: int
    #: Held back until Stripe says a fee: counted once it has.
    waiting: int
    transferred: int
    open: int
    lines: list[StatementLine]


def statement_out(found):
    st = found["statement"]
    return Statement(
        teams_carry_fees=st["carry"], earned=st["earned"], waiting=st["waiting"], transferred=st["transferred"],
        open=st["open"],
        rows=[StatementRow(kind=kind, label=label, amount=amount) for kind, label, amount in st["rows"]],
        lines=[StatementLine(day=line.day, kind=line.kind, text=line.text, amount=line.amount, group=line.group,
                             detail=line.detail, waiting=line.waiting) for line in st["lines"]],
    )


class TeamMoneyOut(Model):
    team: TeamRef
    #: Fees and credit sales together.
    earned: int
    #: Of it, from fees.
    fees: int
    sales: CreditSales
    #: Paid, but carrying a Stripe fee not known yet: counted once Stripe says (seconds, for a card).
    waiting_for_fee: int
    paid_out: int
    open: int
    statement: Statement
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
        statement=statement_out(found),
        bank=Bank(account_holder=team.bank_account_holder, iban=team.bank_iban, bic=team.bank_bic),
        reference=money.default_reference(team), today=get_membership_today(),
        by_period=[Period(paid_until=until, earned=cents) for until, cents in found["by_period"]],
        transfers=[Transfer(id=payout.id, paid_on=payout.paid_on, amount=payout.amount_cents,
                            reference=payout.reference, to=money.masked_iban(payout.iban) or None)
                   for payout in found["payouts"]],
        payments=[team_payment_out(payment, found) for payment in found["payments"]],
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


# --- Who carries Stripe's fees -------------------------------------------------------------


class MoneySettings(Model):
    #: Stripe's fees -- on team fees, and on the top-ups behind teams' credit sales: carried by
    #: the association, or taken from what the teams are owed. For every payment, past and future.
    fee_payer: Literal["association", "team"]
    #: Differences between Stripe and the portal emailed to the admins and the treasurer.
    difference_emails: bool = True


class MoneySettingsSavedOut(Model):
    changed: list[str]


@endpoint("GET", "/admin/money/settings", response=MoneySettings, permissions=PERMISSIONS, tag=TAG)
def admin_money_settings():
    """Who carries Stripe's fees, and the emails about differences."""
    return MoneySettings(fee_payer="team" if money.teams_bear_fees() else "association",
                         difference_emails=money.difference_emails())


@endpoint("PUT", "/admin/money/settings", response=MoneySettingsSavedOut, body=MoneySettings,
          permissions=PERMISSIONS, tag=TAG)
def admin_money_settings_save(body):
    """Change them. Who carries the fees counts for all money at once."""
    changed = money.save_money_settings(current_user, fee_payer=body.fee_payer,
                                        difference_emails=body.difference_emails)
    db.session.commit()
    return MoneySettingsSavedOut(changed=changed)
