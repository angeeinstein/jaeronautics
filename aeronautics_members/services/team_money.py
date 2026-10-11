"""A team's money: what its members paid, and what the association passed on.

All of it lands on the association's Stripe account. A team is owed what its
members paid for it, less what was refunded or taken back by a lost
chargeback; and what it sold for credit (services/credit_sales.py), less what
was taken back and the association's share where it keeps one. When the
teams carry Stripe's fees (the money setting, for every payment past and
future), Stripe's fee comes off each payment -- and what Stripe keeps
afterwards, on a chargeback -- and each credit sale carries its share of the
fee of the top-up that paid for it (services/credit_fees.py). The
association transfers what is owed to the team's own account whenever
suits, and records each transfer; what is still open is what was earned
less what was transferred.

**Every figure shows how it came about.** :func:`statement` lists each line
-- a payment, a refund, Stripe's fee, a sale, its fee share, a transfer --
and :func:`summary` adds them up the same way, so the totals are the sum of
the lines the pages show (docs/money-ledger-plan.md). A line whose fee
Stripe has not said yet is held back, shown as waiting, until it has.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from ..db_models import CreditEntry, Payment, TeamPayout, db
from . import ValidationError
from .audit import log_audit_event
from .clock import get_membership_today
from .membership import format_date_display, format_membership_date_display
# Who carries Stripe's fees: its own module (selling for credit reads it too).
from .money_settings import (  # noqa: F401
    credit_share_bps, difference_emails, save_money_settings, teams_bear_fees,
)

# What a line is, for the pages to group and explain them.
FEE_PAID = "fee_paid"
REFUNDED = "refunded"
TAKEN_BACK = "taken_back"
STRIPE_FEE = "stripe_fee"
STRIPE_LATER_FEE = "stripe_later_fee"
SOLD = "sold"
SALE_TAKEN_BACK = "sale_taken_back"
KEPT = "kept"
TOP_UP_FEE = "top_up_fee"
TRANSFER = "transfer"

#: The summary's rows, in order: the lines of each kind added up.
SUMMARY_ROWS = (
    (FEE_PAID, "Fees paid"),
    (REFUNDED, "Refunded"),
    (TAKEN_BACK, "Taken back by the bank"),
    (STRIPE_FEE, "Stripe's fees on fees"),
    (STRIPE_LATER_FEE, "Stripe's chargeback fees"),
    (SOLD, "Sold for credit"),
    (SALE_TAKEN_BACK, "Sales taken back"),
    (KEPT, "Kept by the association"),
    (TOP_UP_FEE, "Stripe's fees on top-ups"),
)


@dataclass
class Line:
    day: date | None
    kind: str
    text: str
    amount: int
    #: A payment or a sale this line belongs to, to keep its lines together.
    group: str
    detail: str = ""
    #: Held back until Stripe says the fee it carries.
    waiting: bool = False


@dataclass
class _Group:
    key: str
    lines: list = field(default_factory=list)
    waiting: bool = False


def _day(moment):
    from .clock import datetime_to_membership_date

    return datetime_to_membership_date(moment)


def team_payments(team):
    return db.session.execute(
        db.select(Payment).filter_by(team_id=team.id, purpose="team").order_by(Payment.paid_at.desc())
    ).scalars().all()


def team_payouts(team):
    return db.session.execute(
        db.select(TeamPayout).filter_by(team_id=team.id).order_by(TeamPayout.paid_on.desc(), TeamPayout.id.desc())
    ).scalars().all()


def _team_sales(team):
    from . import credit

    return db.session.execute(
        db.select(CreditEntry).where(CreditEntry.team_id == team.id,
                                     CreditEntry.kind.in_((credit.PURCHASE, credit.TAKEN_BACK)))
        .order_by(CreditEntry.created_at, CreditEntry.id)
    ).scalars().all()


def _payment_groups(payments, carry):
    from .stripe_books import fees_of_payments

    stripe_fees = fees_of_payments([payment.id for payment in payments])
    groups = []
    for payment in sorted(payments, key=lambda found: (found.paid_at, found.id)):
        group = _Group(key=f"payment:{payment.id}")
        day = _day(payment.paid_at)
        name = payer_name(payment) or "a former member"
        until = f", paid until {format_membership_date_display(payment.covers_until)}" if payment.covers_until else ""
        group.lines.append(Line(day, FEE_PAID, f"Fee paid by {name}{until}", payment.amount_cents, group.key))
        refunded = min(payment.refunded_cents or 0, payment.amount_cents)
        if refunded:
            group.lines.append(Line(day, REFUNDED, "Refunded", -refunded, group.key))
        if payment.status == "disputed":
            group.lines.append(Line(day, TAKEN_BACK, "Taken back by the bank (chargeback)",
                                    -(payment.amount_cents - refunded), group.key))
        if carry:
            found = stripe_fees.get(payment.id) or {}
            fee = found.get("fee") if found.get("fee") is not None else payment.fee_cents
            if fee is None:
                group.waiting = True
                group.lines.append(Line(day, STRIPE_FEE, "Stripe's fee: not known yet", 0, group.key, waiting=True))
            else:
                group.lines.append(Line(day, STRIPE_FEE, "Stripe's fee", -fee, group.key,
                                        detail="kept by Stripe also when the payment is refunded" if refunded else ""))
            later = found.get("later_fees") or 0
            if later:
                group.lines.append(Line(day, STRIPE_LATER_FEE, "Stripe's fee on the chargeback", -later, group.key))
        if group.waiting:
            for line in group.lines:
                line.waiting = True
        groups.append(group)
    return groups


def _sale_groups(team, carry):
    from . import credit
    from .credit_fees import parts_for_people

    sales = _team_sales(team)
    parts = parts_for_people({entry.user_id for entry in sales}) if carry else {}
    top_ups = {}
    if carry:
        ids = {part.source_id for entry in sales for part in parts.get(entry.id, []) if part.payment_id}
        if ids:
            top_ups = {entry.id: entry for entry in db.session.execute(
                db.select(CreditEntry).where(CreditEntry.id.in_(list(ids)))
            ).scalars()}
    groups = []
    for entry in sales:
        group = _Group(key=f"sale:{entry.id}")
        day = _day(entry.created_at)
        name = entry.item.name if entry.item is not None else entry.description
        price = -entry.amount_cents  # a sale takes credit (negative); taken back gives it again
        if entry.kind == credit.PURCHASE:
            group.lines.append(Line(day, SOLD, f"{name} sold", price, group.key))
        else:
            group.lines.append(Line(day, SALE_TAKEN_BACK, f"{name} taken back", price, group.key))
        if entry.kept_cents:
            share = credit_share_text(entry.kept_cents, abs(price))
            group.lines.append(Line(day, KEPT, f"Kept by the association{share}", -entry.kept_cents, group.key))
        if carry:
            for part in parts.get(entry.id, []):
                if part.payment_id is None:
                    continue  # paid from cash: no fee
                top_up = top_ups.get(part.source_id)
                about = (f"the top-up of {format_date_display(top_up.created_at)} "
                         f"({euros(top_up.amount_cents)}") if top_up is not None else "a top-up ("
                if part.fee is None:
                    group.waiting = True
                    group.lines.append(Line(day, TOP_UP_FEE, f"Share of Stripe's fee on {about}): not known yet",
                                            0, group.key, waiting=True))
                    continue
                fee_total = _top_up_fee(top_up)
                about += f", fee {euros(fee_total)})" if fee_total is not None else ")"
                group.lines.append(Line(day, TOP_UP_FEE, f"Share of Stripe's fee on {about}", -part.fee, group.key,
                                        detail=f"{euros(abs(part.amount))} of it paid for this"))
        if group.waiting:
            for line in group.lines:
                line.waiting = True
        groups.append(group)
    return groups


def _top_up_fee(top_up):
    if top_up is None or top_up.payment is None:
        return None
    return top_up.payment.fee_cents


def credit_share_text(kept, price):
    if not price:
        return ""
    return f" ({kept * 100 / price:.1f} %)".replace(".0 %", " %")


def statement(team):
    """Every line of the team's money, oldest first, and the totals they add up to."""
    carry = teams_bear_fees()
    payments = team_payments(team)
    payouts = team_payouts(team)
    groups = _payment_groups(payments, carry) + _sale_groups(team, carry)
    for payout in sorted(payouts, key=lambda found: (found.paid_on, found.id)):
        key = f"transfer:{payout.id}"
        groups.append(_Group(key=key, lines=[Line(payout.paid_on, TRANSFER, "Transferred to the team",
                                                  -payout.amount_cents, key, detail=payout.reference or "")]))
    lines = sorted((line for group in groups for line in group.lines),
                   key=lambda line: (line.day or date.min, line.group))
    totals = {kind: 0 for kind, _label in SUMMARY_ROWS}
    waiting = 0
    for group in groups:
        if group.waiting:
            waiting += sum(line.amount for line in group.lines if line.kind in (FEE_PAID, SOLD, SALE_TAKEN_BACK))
            continue
        for line in group.lines:
            if line.kind in totals:
                totals[line.kind] += line.amount
    earned = sum(totals.values())
    transferred = sum(payout.amount_cents for payout in payouts)
    return {
        "lines": lines,
        "rows": [(kind, label, totals[kind]) for kind, label in SUMMARY_ROWS if totals[kind]],
        "earned": earned,
        "waiting": waiting,
        "transferred": transferred,
        "open": earned - transferred,
        "carry": carry,
        "groups": {group.key: group for group in groups},
    }


def counts(payment, groups=None):
    """What of ``payment`` the team is owed, in cents; None while Stripe's fee it carries is not known."""
    if groups is None:
        groups = {group.key: group for group in _payment_groups([payment], teams_bear_fees())}
    group = groups.get(f"payment:{payment.id}")
    if group is None or group.waiting:
        return None
    return sum(line.amount for line in group.lines)


def summary(team):
    """The statement's totals, as the pages use them: earned, of it from fees and from credit
    sales, waiting, paid out and open; the fees by the period they paid for."""
    from .credit_sales import sales_summary

    found = statement(team)
    payments = team_payments(team)
    payouts = team_payouts(team)
    groups = found["groups"]
    by_period = {}
    fees = 0
    for payment in payments:
        cents = counts(payment, groups)
        if cents is None:
            continue
        fees += cents
        by_period[payment.covers_until] = by_period.get(payment.covers_until, 0) + cents
    sales = sales_summary(team)
    sales["earned"] = found["earned"] - fees
    return {
        "earned": found["earned"],
        "fees": fees,
        "sales": sales,
        "waiting_for_fee": found["waiting"],
        "paid_out": found["transferred"],
        "open": found["open"],
        "statement": found,
        "by_period": sorted(by_period.items(), key=lambda item: item[0] or date.min, reverse=True),
        "payments": payments,
        "payouts": payouts,
        "last_payout": payouts[0] if payouts else None,
    }


def euros(cents):
    """"€1,234.50", as the portal shows money (and the new front end, lib/format.ts)."""
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}€{cents // 100:,}.{cents % 100:02d}"


def payer_name(payment):
    """Who paid, as far as the record still knows: blank once the account was erased."""
    member = getattr(payment.user, "member", None) if payment.user is not None else None
    return f"{member.first_name or ''} {member.last_name or ''}".strip() if member is not None else ""


EXPORT_COLUMNS = ("Paid on", "Name", "Paid until", "Amount", "Refunded", "Counts", "Status")


def export_rows(team):
    from .teams import csv_cell

    groups = statement(team)["groups"]
    for payment in team_payments(team):
        yield (
            format_date_display(payment.paid_at) if payment.paid_at else "",
            csv_cell(payer_name(payment)),
            format_membership_date_display(payment.covers_until) if payment.covers_until else "",
            f"{payment.amount_cents / 100:.2f}",
            f"{(payment.refunded_cents or 0) / 100:.2f}",
            f"{counts(payment, groups) / 100:.2f}" if counts(payment, groups) is not None else "",
            payment.status,
        )


# --- Payouts -----------------------------------------------------------------------


def _amount_cents(text):
    cleaned = (text or "").strip().replace("€", "").replace(" ", "")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "")
    cleaned = cleaned.replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", cleaned):
        raise ValidationError("Enter the amount in euros, like 240.00.", code="team_payout_amount_invalid")
    cents = int(round(float(cleaned) * 100))
    if cents <= 0:
        raise ValidationError("The amount must be above zero.", code="team_payout_amount_invalid")
    return cents


def default_reference(team, today=None):
    today = today or get_membership_today()
    return f"Teambeiträge {team.name} bis {format_membership_date_display(today)}"[:140]


def _paid_on(value):
    today = get_membership_today()
    if value in (None, ""):
        return today
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value.strip())
        except ValueError:
            raise ValidationError("Enter the date of the transfer.", code="team_payout_date_invalid") from None
    if value > today:
        raise ValidationError("The transfer cannot be in the future.", code="team_payout_date_invalid")
    return value


def record_payout(actor, team, *, amount, paid_on=None, reference=None):
    """The association transferred ``amount`` to the team. Recorded with the
    account it went to, so the record stays right if the account changes."""
    from ..db_models import Team

    cents = _amount_cents(amount)
    paid_on = _paid_on(paid_on)
    # One payout at a time per team, so two clicks cannot both take what is open.
    db.session.execute(db.select(Team.id).where(Team.id == team.id).with_for_update())
    open_cents = summary(team)["open"]
    if cents > open_cents:
        raise ValidationError(
            f"That is more than is open ({euros(open_cents)}).", code="team_payout_more_than_open",
        )
    payout = TeamPayout(
        team_id=team.id, amount_cents=cents, currency="eur",
        paid_on=paid_on,
        reference=(reference or "").strip()[:140] or default_reference(team),
        account_holder=team.bank_account_holder, iban=team.bank_iban,
        recorded_by_user_id=getattr(actor, "id", None),
    )
    db.session.add(payout)
    db.session.flush()
    log_audit_event("payments", "team_payout_recorded", actor_user=actor, metadata={
        "team": team.slug, "payout_id": payout.id, "amount_cents": cents, "iban": team.bank_iban,
    })
    return payout


def all_teams_money():
    """Every team that ever earned or was paid anything, for the treasurer."""
    from .teams import all_teams

    rows = []
    for team in all_teams():
        money = summary(team)
        if money["earned"] or money["paid_out"] or team.payment_mode != "none":
            rows.append({"team": team, **money})
    return rows


def open_transfers():
    """What is waiting to be passed on: how many teams are owed something, how
    much in all, and how many of those have given no account to send it to."""
    owed = [row for row in all_teams_money() if row["open"] > 0]
    return {
        "teams": len(owed),
        "open": sum(row["open"] for row in owed),
        "without_account": sum(1 for row in owed if not row["team"].bank_iban),
    }


# --- The team's bank account -------------------------------------------------------


def normalise_iban(text):
    return re.sub(r"\s+", "", text or "").upper()


def iban_problem(iban):
    """Why ``iban`` is not a valid IBAN, or None. Checked by its check digits."""
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        return "That does not look like an IBAN."
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(char, 36)) for char in rearranged)
    if int(digits) % 97 != 1:
        return "That IBAN is mistyped: its check digits do not match."
    return None


def masked_iban(iban):
    return f"{iban[:4]} … {iban[-4:]}" if iban else ""


def update_bank_details(actor, team, *, account_holder, iban, bic):
    """Where the team is paid. Every change is logged, and the association's
    treasurers are told of one somebody else made."""
    account_holder = " ".join((account_holder or "").split())[:70] or None
    iban = normalise_iban(iban) or None
    bic = normalise_iban(bic) or None
    if iban:
        problem = iban_problem(iban)
        if problem:
            raise ValidationError(problem, code="team_iban_invalid")
        if not account_holder:
            raise ValidationError("Enter the account holder as the bank knows them.", code="team_account_holder_missing")
    if bic and not re.fullmatch(r"[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?", bic):
        raise ValidationError("That does not look like a BIC (8 or 11 characters).", code="team_bic_invalid")
    before = {"account_holder": team.bank_account_holder, "iban": team.bank_iban, "bic": team.bank_bic}
    after = {"account_holder": account_holder, "iban": iban, "bic": bic}
    if before == after:
        return False
    team.bank_account_holder, team.bank_iban, team.bank_bic = account_holder, iban, bic
    log_audit_event("payments", "team_bank_details_changed", actor_user=actor, before=before, after=after,
                    metadata={"team": team.slug})
    _tell_treasurers(actor, team)
    return True


def association_treasurers():
    from ..db_models import Role, User, UserRole

    return db.session.execute(
        db.select(User).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id)
        .where(Role.slug == "treasurer", User.deleted_at.is_(None), User.disabled_at.is_(None))
    ).scalars().all()


def _tell_treasurers(actor, team):
    from .notifications import queue_user_status_notification

    who = getattr(actor, "email", None) or "somebody"
    for treasurer in association_treasurers():
        if getattr(actor, "id", None) == treasurer.id:
            continue
        queue_user_status_notification(
            "team_bank_details_changed", f"The bank details of {team.name} were changed by {who}.",
            treasurer.email,
            payload={"team_name": team.name, "team_slug": team.slug, "changed_by": who,
                     "account_holder": team.bank_account_holder, "iban": masked_iban(team.bank_iban)},
            target_user=treasurer, object_type="team", object_id=team.id,
        )


# --- Paying it out by banking app -----------------------------------------------------


def epc_payload(team, cents, reference):
    """The text of an EPC ("GiroCode") QR code: a SEPA transfer, filled in.

    EPC069-12, version 002: service tag, version, UTF-8, SEPA credit transfer,
    BIC (optional in 002), name, IBAN, amount, purpose, structured and
    unstructured remittance. Banking apps across the SEPA area read it.
    """
    lines = [
        "BCD", "002", "1", "SCT",
        team.bank_bic or "",
        (team.bank_account_holder or "")[:70],
        team.bank_iban or "",
        f"EUR{cents / 100:.2f}",
        "",
        "",
        (reference or "")[:140],
    ]
    return "\n".join(lines)


def payout_qr_svg(team, cents, reference):
    """The GiroCode as an SVG file of its own (scalable: no size, a viewBox), or
    None when there is nothing to pay or nowhere to pay it."""
    import io

    import segno

    if cents <= 0 or not (team.bank_iban and team.bank_account_holder):
        return None
    code = segno.make(epc_payload(team, cents, reference), error="m", micro=False)
    out = io.BytesIO()
    code.save(out, kind="svg", xmldecl=False, svgns=True, scale=4, border=4, dark="#000000", light="#ffffff",
              omitsize=True)
    return out.getvalue().decode()
