"""A team's money: what its members paid, and what the association passed on.

All of it lands on the association's Stripe account. A team is owed exactly
what its members paid for it -- Stripe's fees are the association's, a
sponsorship on top -- less what was refunded or taken back by a lost
chargeback. The association transfers it to the team's own account whenever
suits, and records each transfer; what is still open is what was earned less
what was transferred. No cut-off dates: the open balance is simply everything
not passed on yet, shown by the period it paid for.

Later sources (a drinks reader belonging to a team, say) add to what a team
earned in the same way; the payouts and the balance stay as they are.
"""

import re
from datetime import date

from ..db_models import Payment, TeamPayout, db
from . import ValidationError
from .audit import log_audit_event
from .clock import get_membership_today
from .membership import format_date_display, format_membership_date_display


def _earned(payment):
    if payment.status == "disputed":
        return 0
    return max(payment.amount_cents - (payment.refunded_cents or 0), 0)


def team_payments(team):
    return db.session.execute(
        db.select(Payment).filter_by(team_id=team.id, purpose="team").order_by(Payment.paid_at.desc())
    ).scalars().all()


def team_payouts(team):
    return db.session.execute(
        db.select(TeamPayout).filter_by(team_id=team.id).order_by(TeamPayout.paid_on.desc(), TeamPayout.id.desc())
    ).scalars().all()


def summary(team):
    """Earned, paid out and open, in cents; earnings by the period they paid for."""
    payments = team_payments(team)
    payouts = team_payouts(team)
    earned = sum(_earned(payment) for payment in payments)
    paid_out = sum(payout.amount_cents for payout in payouts)
    by_period = {}
    for payment in payments:
        by_period[payment.covers_until] = by_period.get(payment.covers_until, 0) + _earned(payment)
    return {
        "earned": earned,
        "paid_out": paid_out,
        "open": earned - paid_out,
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


def counts(payment):
    """What of ``payment`` the team is owed, in cents."""
    return _earned(payment)


EXPORT_COLUMNS = ("Paid on", "Name", "Paid until", "Amount", "Refunded", "Counts", "Status")


def export_rows(team):
    from .teams import csv_cell

    for payment in team_payments(team):
        yield (
            format_date_display(payment.paid_at) if payment.paid_at else "",
            csv_cell(payer_name(payment)),
            format_membership_date_display(payment.covers_until) if payment.covers_until else "",
            f"{payment.amount_cents / 100:.2f}",
            f"{(payment.refunded_cents or 0) / 100:.2f}",
            f"{_earned(payment) / 100:.2f}",
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
