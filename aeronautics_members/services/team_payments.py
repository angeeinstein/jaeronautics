"""A team's fee: what is special about it. Talking to Stripe is services/payments.py.

A team that charges does it by subscription, as the membership does: at
joining the full fee for the period under way, then Stripe charges the fee at
every period start until the person leaves. The periods are the team's own
("01.10, 01.04"), and the price is a recurring price in Stripe on a product of
the team's own, set by a site admin. Every team uses this same code; a new
team is settings, not code.

**Joining.** Approved (or joining an open team), the person pays on the Teams
page. Stripe Checkout charges the current period at once and starts the
subscription with a trial up to the next period start. Somebody joining in the
last days before a period starts pays for the coming one instead: Stripe will
not end a trial sooner than 48 hours away, and paying a full fee for three
days would be unfair anyway.

**Being a member** follows the money: the first paid invoice makes the person
a member, each renewal moves "paid until" on, and one recorded ``Payment``
per invoice keeps the books. Nothing here touches the association membership:
the events arrive marked ``purpose: team`` and never reach its code.

**Ending.** Leaving stops the subscription at the end of what is paid, and the
membership runs to then. A lead removing somebody, the association membership
ending, and erasing the account cancel it at once, without refund. A renewal
that is never paid ends it too; whoever ended that way may come back within
about one period by simply paying again, without applying.
"""

import re
from datetime import date, datetime, time, timedelta

from flask import current_app

from ..db_models import Payment, TeamMembership, db
from ..security_utils import build_public_url
from . import ConflictError, ValidationError
from . import payments
from .audit import log_audit_event
from ..config import MEMBERSHIP_TIMEZONE
from .clock import get_membership_today, get_now_utc, start_of_day_unix
from .membership import format_membership_date_display
from .stripe_scope import (
    invoice_id_of_charge,
    invoice_id_of_payment_intent,
    invoice_subscription_id,
    invoice_subscription_metadata,
)
from .teams import (
    ACTIVE,
    APPROVED,
    END_LEFT,
    END_PAYMENT_FAILED,
    ENDED,
    PAYMENT_NONE,
    _audit,
    _locked_membership,
    _member_name,
    _sync_forum,
    _tell_leads,
    _tell_person,
    format_dates,
    ongoing_membership,
    parse_dates,
    stop_charging,
)

PAYMENT_SUBSCRIPTION = "subscription"
PAYMENT_MODES = (PAYMENT_NONE, PAYMENT_SUBSCRIPTION)
PAYMENT_MODE_LABELS = {PAYMENT_NONE: "Free", PAYMENT_SUBSCRIPTION: "Subscription"}

# How the latest payment of a team membership went.
PROCESSING = "processing"
PAID = "paid"
FAILED = "failed"

#: Joining this close to a period start pays for the coming period instead.
NEXT_PERIOD_WITHIN_DAYS = 3
#: A membership paid only until longer ago than this has had every retry
#: Stripe makes; it ends even if Stripe's last word never arrived.
OVERDUE_DAYS = 35

_PRICE_ID = re.compile(r"^price_[A-Za-z0-9]+$")


def charges(team):
    return team is not None and team.payment_mode == PAYMENT_SUBSCRIPTION


# --- Settings ------------------------------------------------------------------


def _price_problem(price, starts):
    """Why a Stripe price cannot be this team's fee, or None."""
    if not price.get("active", True):
        return "That price is archived in Stripe."
    recurring = price.get("recurring") or {}
    months = {"month": 1, "year": 12}.get(recurring.get("interval"), 0) * int(recurring.get("interval_count") or 1)
    if not months:
        return "That price is not a recurring price in months or years."
    if months * len(starts) != 12:
        return (f"That price renews every {months} month(s), but {len(starts)} period start(s) "
                f"a year means every {12 // len(starts) if 12 % len(starts) == 0 else '?'} month(s).")
    if price.get("unit_amount") in (None, 0):
        return "That price has no fixed amount."
    return None


def _evenly_spaced(starts):
    """Periods of equal length: the same day of the month, evenly many months apart."""
    if len(starts) < 2:
        return True
    if 12 % len(starts) or len({day for day, _month in starts}) != 1:
        return False
    step = 12 // len(starts)
    months = sorted(month for _day, month in starts)
    return all((months[(i + 1) % len(months)] - month) % 12 == step for i, month in enumerate(months))


def update_payment_settings(actor, team, *, payment_mode, stripe_price_id, period_starts):
    """How a team charges. Checked against Stripe before it is saved.

    A new price is a new fee: people joining pay it at once, and every running
    subscription moves to it from its next renewal, its holder told by email
    two weeks before (services/payments.py). Returns how many will move.
    """
    payment_mode = (payment_mode or PAYMENT_NONE).strip()
    if payment_mode not in PAYMENT_MODES:
        raise ValidationError("Choose how the team charges.", code="team_payment_mode_invalid")
    stripe_price_id = (stripe_price_id or "").strip() or None
    starts = parse_dates(period_starts)

    if payment_mode == PAYMENT_NONE and charges(team):
        running = db.session.scalar(
            db.select(db.func.count()).select_from(TeamMembership).where(
                TeamMembership.team_id == team.id, TeamMembership.stripe_subscription_id.isnot(None),
            )
        )
        if running:
            raise ConflictError(
                f"{running} subscription(s) for this team are still running in Stripe. "
                "A free team would leave them charging.",
                code="team_payment_subscriptions_running",
            )

    if payment_mode == PAYMENT_SUBSCRIPTION:
        if not stripe_price_id or not _PRICE_ID.match(stripe_price_id):
            raise ValidationError("Enter the Stripe price ID (price_...).", code="team_price_missing")
        if not starts:
            raise ValidationError("Enter the days the periods start, like 01.10, 01.04.",
                                  code="team_period_starts_missing")
        if not _evenly_spaced(starts):
            raise ValidationError("The periods must be of equal length, like 01.10, 01.04.",
                                  code="team_period_starts_uneven")
        from .stripe_scope import _membership_price_id

        if stripe_price_id == _membership_price_id():
            raise ValidationError("That is the association membership's price.", code="team_price_is_membership")
        try:
            price = payments.retrieve_price(stripe_price_id)
        except Exception as exc:  # noqa: BLE001 -- shown to the admin, nothing saved
            current_app.logger.warning("Could not check Stripe price %s: %s", stripe_price_id, exc)
            raise ValidationError(
                "Stripe does not know that price, or could not be asked. Check the ID and the Stripe keys.",
                code="team_price_unknown",
            ) from None
        problem = _price_problem(price, starts)
        if problem:
            raise ValidationError(problem, code="team_price_unsuitable")
        team.fee_display = fee_text(team, price)
    else:
        team.fee_display = None

    running = [membership for membership in team.memberships
               if membership.stripe_subscription_id and membership.status in (APPROVED, ACTIVE)]
    moving = (
        payment_mode == PAYMENT_SUBSCRIPTION and charges(team) and team.stripe_price_id
        and stripe_price_id != team.stripe_price_id and running
    )
    if moving:
        try:
            old_price = payments.retrieve_price(team.stripe_price_id)
        except Exception:  # noqa: BLE001 -- the old one gone; the move checks again
            old_price = None
        if old_price is not None and payments.interval_months(old_price) != payments.interval_months(price):
            # Stripe would restart every running subscription and charge it now.
            raise ValidationError(
                "The new price renews at another interval than the old one, and subscriptions are "
                "running on it. Keep the interval, or end those memberships first.",
                code="team_price_interval_changed",
            )

    before = {"payment_mode": team.payment_mode, "stripe_price_id": team.stripe_price_id,
              "period_starts": team.period_starts}
    team.payment_mode = payment_mode
    team.stripe_price_id = stripe_price_id
    team.period_starts = format_dates(starts) or None
    after = {"payment_mode": team.payment_mode, "stripe_price_id": team.stripe_price_id,
             "period_starts": team.period_starts}
    if before != after:
        log_audit_event("teams", "team_payment_settings_changed", actor_user=actor,
                        before=before, after=after, metadata={"team": team.slug})
    if not moving:
        return 0
    for membership in running:
        payments.schedule_price_move(
            payments.PURPOSE_TEAM, membership.stripe_subscription_id, stripe_price_id,
            user=membership.user, team_id=team.id, team_membership_id=membership.id,
        )
    return len(running)


# --- Periods ---------------------------------------------------------------------


def _period_start_dates(team, around):
    found = []
    for day, month in parse_dates(team.period_starts):
        for year in range(around.year - 1, around.year + 3):
            try:
                found.append(date(year, month, day))
            except ValueError:
                continue
    return sorted(found)


def joining_period(team, today=None):
    """What paying today buys: ``paid_until`` and ``charged_from`` (the first renewal)."""
    today = today or get_membership_today()
    starts = _period_start_dates(team, today)
    upcoming = [start for start in starts if start > today]
    charged_from = upcoming[0]
    if (charged_from - today).days < NEXT_PERIOD_WITHIN_DAYS:
        charged_from = upcoming[1]
    return {"paid_until": charged_from - timedelta(days=1), "charged_from": charged_from}


def _last_day(unix_timestamp):
    """The last day a period ending at ``unix_timestamp`` covers, in Vienna."""
    moment = datetime.fromtimestamp(int(unix_timestamp), MEMBERSHIP_TIMEZONE)
    return moment.date() - timedelta(days=1) if moment.time() == time(0, 0) else moment.date()


def fee_text(team, price=None):
    """"€10.00 every 6 months", for pages and Checkout."""
    return payments.fee_text(price or payments.retrieve_price(team.stripe_price_id))


# --- Paying ------------------------------------------------------------------------


def _metadata(membership):
    return {
        "purpose": payments.PURPOSE_TEAM,
        "team": membership.team.slug,
        "team_membership_id": str(membership.id),
        "user_id": str(membership.user_id),
    }


def start_checkout(user, team):
    """The Stripe Checkout address where ``user`` pays to join ``team``.

    The same open Checkout again if there is one: two open ones could both be
    paid. A subscription left over from a payment that failed is cancelled
    first, so a second attempt does not leave two running.
    """
    membership = ongoing_membership(user, team)
    if membership is None or membership.status != APPROVED or not charges(team):
        raise ConflictError("There is nothing to pay for this team.", code="team_nothing_to_pay")
    membership = _locked_membership(membership.id, team)
    if membership.payment_state == PROCESSING:
        raise ConflictError("Your payment is on its way. It can take a few days by SEPA debit.",
                            code="team_payment_processing")
    member = getattr(user, "member", None)
    if member is None:
        raise ConflictError("Teams are for members of the association.", code="team_not_joinable")

    existing = payments.open_checkout_session(membership.stripe_checkout_session_id, what="team Checkout")
    if existing is not None:
        return existing["url"]

    replaced = membership.stripe_subscription_id
    if replaced:
        payments.cancel_subscription(replaced, reason=f"team membership {membership.id}: paying again")
        membership.stripe_subscription_id = None

    price = payments.retrieve_price(team.stripe_price_id)
    period = joining_period(team)
    amount = int(price["unit_amount"])
    product = price.get("product")
    product = product if isinstance(product, str) else (product or {}).get("id")
    metadata = _metadata(membership)
    paid_until = format_membership_date_display(period["paid_until"])
    params = dict(
        mode="subscription",
        metadata=metadata,
        payment_method_types=list(payments.PAYMENT_METHOD_TYPES),
        line_items=[
            # The period under way, now; on the team's own product.
            {"price_data": {"currency": price["currency"], "product": product, "unit_amount": amount},
             "quantity": 1},
            {"price": team.stripe_price_id, "quantity": 1},
        ],
        subscription_data={
            "trial_end": start_of_day_unix(period["charged_from"]),
            "metadata": metadata,
            "description": team.name,
        },
        custom_text={"submit": {"message": (
            f"Today you pay {payments.format_amount(amount, price['currency'])} for {team.name} "
            f"until {paid_until}. Then {fee_text(team, price)}, from "
            f"{format_membership_date_display(period['charged_from'])}, until you leave."
        )}},
        payment_method_collection="always",
        success_url=build_public_url("teams.teams_home", paid=team.slug),
        cancel_url=build_public_url("teams.teams_home"),
        **payments.customer_params(member),
    )
    key = f"checkout:team:{membership.id}:{payments.request_fingerprint(params)}"
    if replaced:
        key += f":after:{replaced}"
    session = payments.create_checkout_session(params, idempotency_key=key)
    membership.stripe_checkout_session_id = session.get("id")
    membership.payment_state = None
    _audit("team_checkout_started", user, membership)
    return session["url"]


# --- What Stripe reports --------------------------------------------------------------


def _membership_for(metadata=None, subscription_id=None):
    membership_id = str((metadata or {}).get("team_membership_id") or "")
    if membership_id.isdigit():
        found = db.session.get(TeamMembership, int(membership_id))
        if found is not None:
            return _locked_membership(found.id)
    if subscription_id:
        found = db.session.execute(
            db.select(TeamMembership).filter_by(stripe_subscription_id=subscription_id)
        ).scalars().first()
        if found is not None:
            return _locked_membership(found.id)
    return None


def _tell_admins(event_type, text, **payload):
    from ..notification_service import ADMIN_ERROR_CHANNEL
    from .notifications import queue_curated_admin_notification

    queue_curated_admin_notification(ADMIN_ERROR_CHANNEL, event_type, text, payload=payload, severity="warning")


def _end(membership, reason, now):
    membership.status = ENDED
    membership.ended_at = now
    membership.end_reason = reason
    membership.ends_on = None
    _audit("team_membership_ended", None, membership, end_reason=reason)
    _sync_forum([membership.user], membership.team, f"left team {membership.team.slug}")
    name = _member_name(membership.user)
    team = membership.team
    if reason == END_PAYMENT_FAILED:
        _tell_person("team_payment_ended", membership)
        _tell_leads(team, "team_member_left", f"{name} is no longer in {team.name}: the payment did not go through.",
                    person_name=name)
    else:
        _tell_leads(team, "team_member_left", f"{name} left {team.name}.", person_name=name)


def _activate(membership, now):
    membership.status = ACTIVE
    membership.started_at = now
    membership.payment_settled_at = now
    _audit("team_payment_settled", None, membership, payment_mode=membership.payment_mode)
    _sync_forum([membership.user], membership.team, f"joined team {membership.team.slug}")
    _tell_person("team_approved", membership)
    name = _member_name(membership.user)
    _tell_leads(membership.team, "team_member_joined", f"{name} joined {membership.team.name}.", person_name=name)


def _checkout_completed(session):
    membership = _membership_for(session.get("metadata"))
    if membership is None:
        _tell_admins("team_payment_unmatched", "A completed team Checkout matches no team membership.",
                     session_id=session.get("id"))
        return
    payments.remember_customer(getattr(membership.user, "member", None), session.get("customer"))
    membership.stripe_checkout_session_id = None
    subscription_id = session.get("subscription")
    if subscription_id:
        membership.stripe_subscription_id = subscription_id if isinstance(subscription_id, str) else subscription_id.get("id")
    if membership.status == APPROVED and membership.payment_state != PAID:
        # A card is confirmed in seconds by the invoice; a SEPA debit takes days.
        membership.payment_state = PROCESSING
    elif membership.status not in (APPROVED, ACTIVE):
        # Paid after the approval lapsed or was withdrawn: nothing to charge for.
        stop_charging(membership, "paid for a membership no longer under way")
        _tell_admins("team_paid_after_ending",
                     "Somebody completed a team payment for a membership that had already ended. Refund it in Stripe.",
                     team=membership.team.slug, team_membership_id=membership.id)


def _invoice_paid(invoice):
    amount = int(invoice.get("amount_paid") or 0)
    subscription_id = invoice_subscription_id(invoice)
    membership = _membership_for(invoice_subscription_metadata(invoice), subscription_id)
    if membership is None:
        _tell_admins("team_payment_unmatched", "A paid team invoice matches no team membership.",
                     invoice_id=invoice.get("id"), subscription_id=subscription_id)
        return
    if amount <= 0:
        return  # nothing was paid
    period_end = payments.invoice_period_end(invoice)
    covers_until = _last_day(period_end) if period_end else None
    if db.session.execute(db.select(Payment.id).filter_by(stripe_invoice_id=invoice.get("id"))).first() is None:
        db.session.add(Payment(
            purpose=payments.PURPOSE_TEAM, user_id=membership.user_id, team_id=membership.team_id,
            team_membership_id=membership.id, stripe_invoice_id=invoice.get("id"),
            stripe_subscription_id=subscription_id, amount_cents=amount,
            currency=(invoice.get("currency") or "eur")[:3], covers_until=covers_until,
            paid_at=get_now_utc(),
        ))
    if covers_until and (membership.paid_until is None or covers_until > membership.paid_until):
        membership.paid_until = covers_until
    membership.payment_state = PAID
    membership.stripe_subscription_id = membership.stripe_subscription_id or subscription_id
    if membership.status == APPROVED:
        _activate(membership, get_now_utc())
    elif membership.status == ACTIVE:
        _audit("team_payment_received", None, membership, invoice_id=invoice.get("id"))
    else:
        stop_charging(membership, "paid for a membership no longer under way")
        _tell_admins("team_paid_after_ending",
                     "A team fee was paid for a membership that had already ended. Refund it in Stripe.",
                     team=membership.team.slug, team_membership_id=membership.id, invoice_id=invoice.get("id"))


def _payment_failed(invoice):
    membership = _membership_for(invoice_subscription_metadata(invoice), invoice_subscription_id(invoice))
    if membership is not None and membership.status in (APPROVED, ACTIVE):
        membership.payment_state = FAILED
        _audit("team_payment_failed", None, membership, invoice_id=invoice.get("id"))


def _subscription_updated(subscription):
    membership = _membership_for(subscription.get("metadata"), subscription.get("id"))
    if membership is None or membership.status not in (APPROVED, ACTIVE):
        return
    status = subscription.get("status")
    if status in ("unpaid", "incomplete_expired"):
        if membership.status == ACTIVE:
            stop_charging(membership, "never paid")
            _end(membership, END_PAYMENT_FAILED, get_now_utc())
        else:
            membership.payment_state = FAILED
        return
    if status == "past_due":
        membership.payment_state = FAILED
    if membership.status != ACTIVE:
        return
    if payments.subscription_has_scheduled_cancellation(subscription):
        _period_start, period_end = payments.subscription_period_bounds(subscription)
        ends_at = subscription.get("cancel_at") or period_end
        ends_on = _last_day(ends_at) if ends_at else membership.paid_until
        if membership.ends_on is None:
            # Left through Stripe's billing page rather than the portal.
            name = _member_name(membership.user)
            _tell_leads(membership.team, "team_member_leaving",
                        f"{name} is leaving {membership.team.name} on {format_membership_date_display(ends_on)}.",
                        person_name=name)
        membership.ends_on = ends_on
    elif membership.ends_on is not None:
        membership.ends_on = None
        _audit("team_leaving_cancelled", None, membership)


def _subscription_deleted(subscription):
    membership = _membership_for(subscription.get("metadata"), subscription.get("id"))
    if membership is None:
        return
    if membership.stripe_subscription_id == subscription.get("id"):
        membership.stripe_subscription_id = None
    reason = (subscription.get("cancellation_details") or {}).get("reason")
    if membership.status == ACTIVE:
        failed = reason in ("payment_failed", "payment_disputed")
        _end(membership, END_PAYMENT_FAILED if failed else END_LEFT, get_now_utc())
    elif membership.status == APPROVED:
        membership.payment_state = FAILED


def _dispute_closed(dispute):
    if dispute.get("status") != "lost":
        return
    import stripe

    payments.apply_runtime_stripe_config()
    charge = stripe.Charge.retrieve(dispute.get("charge"))
    invoice_id = invoice_id_of_charge(charge) or invoice_id_of_payment_intent(
        dispute.get("payment_intent") or charge.get("payment_intent")
    )
    payment = db.session.execute(db.select(Payment).filter_by(stripe_invoice_id=invoice_id)).scalars().first() \
        if invoice_id else None
    if payment is None:
        _tell_admins("team_dispute_unmatched", "A lost chargeback on a team fee matches no recorded payment.",
                     charge_id=dispute.get("charge"), invoice_id=invoice_id)
        return
    payment.status = "disputed"
    membership = _locked_membership(payment.team_membership_id) if payment.team_membership_id else None
    if membership is not None and membership.status == ACTIVE:
        stop_charging(membership, "chargeback lost")
        _end(membership, END_PAYMENT_FAILED, get_now_utc())


_HANDLERS = {
    "checkout.session.completed": _checkout_completed,
    "invoice.paid": _invoice_paid,
    "invoice.payment_succeeded": _invoice_paid,
    "invoice.payment_failed": _payment_failed,
    "customer.subscription.updated": _subscription_updated,
    "customer.subscription.deleted": _subscription_deleted,
    "charge.dispute.closed": _dispute_closed,
}


def handle_event(event):
    """A webhook event marked ``purpose: team``; see services/payments.py.

    Payment intents are left alone: the invoice each one pays reports the
    same payment, with what it paid for.
    """
    handler = _HANDLERS.get(event["type"])
    if handler is None:
        return "Not needed for teams", 200
    try:
        handler(event["data"]["object"])
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Team payment event %s (%s) failed.", event.get("id"), event["type"])
        return "Team payment event failed; deliver it again", 500
    return "Success", 200


# --- Every night ---------------------------------------------------------------------


def end_finished_team_memberships(today=None):
    """End memberships whose leaving day has passed, or that are long unpaid.

    Stripe reports both itself; this is for when its word never arrived.
    """
    today = today or get_membership_today()
    ended = 0
    for found in db.session.execute(
        db.select(TeamMembership).where(TeamMembership.status == ACTIVE, TeamMembership.payment_mode == PAYMENT_SUBSCRIPTION)
    ).scalars().all():
        membership = _locked_membership(found.id)
        if membership.status != ACTIVE:
            continue
        if membership.ends_on is not None and membership.ends_on < today:
            stop_charging(membership, "leaving day passed")
            _end(membership, END_LEFT, get_now_utc())
            ended += 1
        elif membership.paid_until is not None and (today - membership.paid_until).days > OVERDUE_DAYS:
            stop_charging(membership, "long unpaid")
            _end(membership, END_PAYMENT_FAILED, get_now_utc())
            ended += 1
    return ended


# --- A new fee -----------------------------------------------------------------------


def _team_price_now(payload):
    from ..db_models import Team

    team = db.session.get(Team, payload.get("team_id"))
    return team.stripe_price_id if team is not None and charges(team) else None


def _tell_about_new_fee(payload, notice):
    """The email, when it is due -- unless the person has left or is leaving."""
    membership = db.session.get(TeamMembership, payload.get("team_membership_id"))
    if membership is None or membership.status not in (APPROVED, ACTIVE):
        return
    if membership.stripe_subscription_id != payload.get("subscription_id") or membership.ends_on is not None:
        return
    _tell_person("team_fee_changed", membership, **notice)


payments.register_price_moves(payments.PURPOSE_TEAM, current_price=_team_price_now, tell=_tell_about_new_fee)
