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
from .team_money import teams_bear_fees
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
    END_MEMBERSHIP_ENDED,
    END_NOT_RENEWED,
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
PAYMENT_ONE_TIME = "one_time"
PAYMENT_MODES = (PAYMENT_NONE, PAYMENT_SUBSCRIPTION, PAYMENT_ONE_TIME)
CHARGING_MODES = (PAYMENT_SUBSCRIPTION, PAYMENT_ONE_TIME)
PAYMENT_MODE_LABELS = {PAYMENT_NONE: "Free", PAYMENT_SUBSCRIPTION: "Subscription",
                       PAYMENT_ONE_TIME: "Once per period"}

# How the latest payment of a team membership went.
PROCESSING = "processing"
PAID = "paid"
FAILED = "failed"

#: Joining this close to a period start pays for the coming period instead.
NEXT_PERIOD_WITHIN_DAYS = 3
#: Paid once per period: the reminder to pay for the next one goes out this
#: many days before the paid period ends, and paying for it opens then.
RENEWAL_NOTICE_DAYS = 14
#: A membership paid only until longer ago than this has had every retry
#: Stripe makes; it ends even if Stripe's last word never arrived.
OVERDUE_DAYS = 35
#: Paid once per period by SEPA debit just before the period ended: the money
#: takes days to arrive, so the membership waits this long for it.
PROCESSING_GRACE_DAYS = 14

_PRICE_ID = re.compile(r"^price_[A-Za-z0-9]+$")


def charges(team):
    """Whether the team has a fee at all, however it is paid."""
    return team is not None and team.payment_mode in CHARGING_MODES


def by_subscription(team):
    return team is not None and team.payment_mode == PAYMENT_SUBSCRIPTION


def once_per_period(team):
    return team is not None and team.payment_mode == PAYMENT_ONE_TIME


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


def _one_time_price_problem(price):
    """Why a Stripe price cannot be a team's fee paid once per period, or None."""
    if not price.get("active", True):
        return "That price is archived in Stripe."
    if price.get("recurring"):
        return "That is a recurring price. A team paid once per period needs a one-time price."
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

    Something that is set up once and seldom touched, but a change to a team
    with members is handled rather than refused where it can be:

    - a new price: people joining pay it at once, and every running
      subscription moves to it from its next renewal, its holder told two
      weeks before (services/payments.py);
    - free from now on: every running subscription stops at the end of what is
      paid, and its holder stays in the team, for free;
    - charging from now on: members already in stay free until the next period
      starts, are told, and pay by then to stay.

    The period dates cannot change while subscriptions run on the old ones.

    Returns how many were affected: ``{"moving": n, "stopping": n, "asked_to_pay": n}``.
    """
    payment_mode = (payment_mode or PAYMENT_NONE).strip()
    if payment_mode not in PAYMENT_MODES:
        raise ValidationError("Choose how the team charges.", code="team_payment_mode_invalid")
    stripe_price_id = (stripe_price_id or "").strip() or None
    starts = parse_dates(period_starts)

    if payment_mode in CHARGING_MODES:
        if not stripe_price_id or not _PRICE_ID.match(stripe_price_id):
            raise ValidationError("Enter the Stripe price ID (price_...).", code="team_price_missing")
        if not starts:
            raise ValidationError("Enter the days the periods start, like 01.10, 01.04.",
                                  code="team_period_starts_missing")
        if payment_mode == PAYMENT_SUBSCRIPTION and not _evenly_spaced(starts):
            # Paid once per period, a winter and a summer semester of different
            # lengths are fine; a subscription renews at one fixed interval.
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
        problem = (_price_problem(price, starts) if payment_mode == PAYMENT_SUBSCRIPTION
                   else _one_time_price_problem(price))
        if problem:
            raise ValidationError(problem, code="team_price_unsuitable")
        team.fee_display = fee_text(team, price)
    else:
        team.fee_display = None

    # Locked before Stripe is told anything: the webhooks those changes cause
    # then wait until this is saved, and find the new way of paying rather
    # than taking a stopped subscription for somebody leaving.
    under_way = [_locked_membership(membership.id) for membership in list(team.memberships)
                 if membership.status in (APPROVED, ACTIVE)]
    running = [membership for membership in under_way if membership.stripe_subscription_id]
    was_charging = charges(team)
    switching = was_charging and payment_mode in CHARGING_MODES and payment_mode != team.payment_mode
    if (was_charging and payment_mode == PAYMENT_SUBSCRIPTION and running
            and starts != parse_dates(team.period_starts)):
        raise ValidationError(
            "The period dates cannot change while subscriptions run on the old ones. Switch the "
            "team to free first, and set the new dates once those have run out.",
            code="team_period_starts_locked",
        )
    moving = (
        payment_mode == PAYMENT_SUBSCRIPTION and by_subscription(team) and team.stripe_price_id
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

    stopping = []
    to_one_time = []
    renewing = [membership for membership in running
                if membership.ends_on is None and membership.payment_mode == PAYMENT_SUBSCRIPTION]
    if was_charging and payment_mode == PAYMENT_NONE:
        # Asked of Stripe first: if it cannot be reached, nothing is saved.
        stopping = _stop_renewing(renewing)
        # Paid once per period: nothing to stop, and nothing more to pay.
        stopping += [m for m in under_way if m.status == ACTIVE and m.ends_on is None
                     and m.payment_mode == PAYMENT_ONE_TIME]
    elif switching and payment_mode == PAYMENT_ONE_TIME:
        # Their subscriptions stop at the end of what is paid; then they pay once per period.
        to_one_time = _stop_renewing(renewing)
    resuming = []
    if not was_charging and payment_mode == PAYMENT_SUBSCRIPTION:
        # Charging again before the subscriptions from last time have run out:
        # they simply carry on, rather than being asked to pay a second time.
        resuming = [m for m in running if m.status == ACTIVE and m.payment_mode == PAYMENT_NONE
                    and m.ends_on is None]
        _keep_renewing(resuming)

    before = {"payment_mode": team.payment_mode, "stripe_price_id": team.stripe_price_id,
              "period_starts": team.period_starts}
    if (before["payment_mode"], before["stripe_price_id"]) != (payment_mode, stripe_price_id):
        # A payment page opened at the old fee, or the old way of paying, is
        # closed: the next click opens one at the new.
        for membership in under_way:
            if membership.stripe_checkout_session_id:
                payments.expire_checkout_session(membership.stripe_checkout_session_id)
                membership.stripe_checkout_session_id = None
    team.payment_mode = payment_mode
    team.stripe_price_id = stripe_price_id
    team.period_starts = format_dates(starts) or None
    after = {"payment_mode": team.payment_mode, "stripe_price_id": team.stripe_price_id,
             "period_starts": team.period_starts}
    if before != after:
        log_audit_event("teams", "team_payment_settings_changed", actor_user=actor,
                        before=before, after=after, metadata={"team": team.slug})
    outcome = {"moving": 0, "stopping": len(stopping), "asked_to_pay": 0, "switched": 0}
    if moving:
        for membership in running:
            payments.schedule_price_move(
                payments.PURPOSE_TEAM, membership.stripe_subscription_id, stripe_price_id,
                user=membership.user, team_id=team.id, team_membership_id=membership.id,
            )
        outcome["moving"] = len(running)
    if stopping:
        _now_free(team, stopping)
    if was_charging and payment_mode == PAYMENT_NONE:
        _settle_waiting_approvals(team)
    if resuming:
        for membership in resuming:
            membership.payment_mode = PAYMENT_SUBSCRIPTION
            payments.schedule_price_move(
                payments.PURPOSE_TEAM, membership.stripe_subscription_id, stripe_price_id,
                user=membership.user, team_id=team.id, team_membership_id=membership.id,
            )
            _audit("team_charging_again", None, membership)
            _tell_person("team_charging_again", membership, fee=team.fee_display)
        outcome["moving"] += len(resuming)
    if not was_charging and payment_mode in CHARGING_MODES:
        outcome["asked_to_pay"] = _now_charging(team)
    if switching:
        outcome["switched"] = _switch_way_of_paying(team, to_one_time)
    return outcome


def _stop_renewing(memberships):
    """Each subscription stops at the end of what is paid -- all of them, or,
    if Stripe fails on one, none: those already changed are changed back."""
    done = []
    try:
        for membership in memberships:
            payments.set_cancel_at_period_end(membership.stripe_subscription_id, True)
            done.append(membership)
    except Exception:
        _keep_renewing(done, undoing=True)
        raise
    return done


def _keep_renewing(memberships, undoing=False):
    """Take back a stop at the end of what is paid; the subscriptions renew as before."""
    for membership in memberships:
        try:
            payments.set_cancel_at_period_end(membership.stripe_subscription_id, False)
        except Exception as exc:  # noqa: BLE001
            if not undoing:
                raise
            current_app.logger.error("Could not undo stopping team subscription %s: %s",
                                     membership.stripe_subscription_id, exc)
            _tell_admins("team_subscription_not_restored",
                         "A team's fee change failed half-way, and one subscription could not be set to "
                         "renew again. In Stripe, set it to continue (it is marked to cancel at period end).",
                         team_membership_id=membership.id, subscription_id=membership.stripe_subscription_id)


def _switch_way_of_paying(team, stopped_subscriptions):
    """Members keep what they paid for, and pay the new way from then on."""
    switched = 0
    if team.payment_mode == PAYMENT_ONE_TIME:
        waiting = [m for m in team.memberships if m.status == ACTIVE and m.ends_on is None
                   and m.payment_mode == PAYMENT_SUBSCRIPTION and not m.stripe_subscription_id]
        for membership in list(stopped_subscriptions) + waiting:
            membership.payment_mode = PAYMENT_ONE_TIME
            # Asked to pay already, they may pay the new way at once.
            membership.renewal_notice_for = membership.paid_until if membership in waiting else None
            _audit("team_now_once_per_period", None, membership)
            _tell_person("team_now_once_per_period", membership, fee=team.fee_display,
                         until=format_membership_date_display(membership.paid_until) if membership.paid_until else None)
            switched += 1
    else:
        free_until = joining_period(team)["paid_until"]
        for membership in list(team.memberships):
            if membership.status != ACTIVE or membership.ends_on is not None or membership.payment_mode != PAYMENT_ONE_TIME:
                continue
            membership.payment_mode = PAYMENT_SUBSCRIPTION
            membership.paid_until = membership.paid_until or free_until
            membership.payment_state = None
            membership.renewal_notice_for = None
            _audit("team_now_subscription", None, membership)
            _tell_person("team_now_subscription", membership, fee=team.fee_display,
                         from_date=format_membership_date_display(membership.paid_until + timedelta(days=1)))
            switched += 1
    return switched


def _now_free(team, memberships):
    """Their subscriptions stop at the end of what is paid; they stay, for free."""
    for membership in memberships:
        membership.payment_mode = PAYMENT_NONE
        _audit("team_now_free", None, membership)
        until = membership.paid_until
        _tell_person("team_now_free", membership,
                     until=format_membership_date_display(until) if until else None)


def _settle_waiting_approvals(team):
    """Approved and not yet paying: a free team has nothing to wait for."""
    from .teams import _payment_step

    now = get_now_utc()
    for membership in list(team.memberships):
        if membership.status == APPROVED and not membership.stripe_subscription_id:
            stop_charging(membership, "team became free")
            if _payment_step(membership, now):
                _tell_person("team_approved", membership)


def _now_charging(team):
    """Members of a free team that starts charging: free until the next period, then pay."""
    free_until = joining_period(team)["paid_until"]
    asked = 0
    for membership in list(team.memberships):
        if membership.status != ACTIVE or membership.payment_mode in CHARGING_MODES:
            continue  # paying already, or carrying on with what they paid before (resuming)
        if membership.stripe_subscription_id and team.payment_mode == PAYMENT_ONE_TIME:
            # What they paid for before still runs; then once per period, reminded in time.
            membership.payment_mode = PAYMENT_ONE_TIME
            membership.renewal_notice_for = None
            _audit("team_now_charging", None, membership)
            _tell_person("team_now_once_per_period", membership, fee=team.fee_display,
                         until=format_membership_date_display(membership.paid_until) if membership.paid_until else None)
            asked += 1
            continue
        membership.payment_mode = team.payment_mode
        membership.paid_until = free_until
        membership.payment_state = None
        # Paid once per period, the email below is the renewal reminder too.
        membership.renewal_notice_for = free_until if team.payment_mode == PAYMENT_ONE_TIME else None
        _audit("team_now_charging", None, membership, free_until=free_until.isoformat())
        _tell_person("team_now_charges", membership, fee=team.fee_display,
                     from_date=format_membership_date_display(free_until + timedelta(days=1)))
        asked += 1
    return asked


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


def period_end_after(team, day):
    """The last day of the period starting the day after ``day``."""
    starts = [start for start in _period_start_dates(team, day) if start > day + timedelta(days=1)]
    return starts[0] - timedelta(days=1)


def renewal_open(membership, today=None):
    """Paid once per period: whether paying for the next period is open now.

    From RENEWAL_NOTICE_DAYS before the paid period ends -- or as soon as the
    reminder went out -- until it ends; paying in advance keeps the membership
    running without a gap.
    """
    if (membership is None or membership.status != ACTIVE or membership.payment_mode != PAYMENT_ONE_TIME
            or membership.paid_until is None or membership.ends_on is not None):
        return False
    today = today or get_membership_today()
    if membership.paid_until < today - timedelta(days=1):
        return False
    return ((membership.paid_until - today).days <= RENEWAL_NOTICE_DAYS
            or membership.renewal_notice_for == membership.paid_until)


def next_period_until(membership):
    """Paying for the next period now covers until this day."""
    return period_end_after(membership.team, membership.paid_until)


def _last_day(unix_timestamp):
    """The last day a period ending at ``unix_timestamp`` covers, in Vienna.

    Periods end at midnight. Stripe keeps a subscription's renewals at the same
    UTC time, so one that started in winter renews at 01:00 in summer (and one
    from summer at 23:00 the evening before in winter): the nearest midnight is
    the end that was meant.
    """
    moment = datetime.fromtimestamp(int(unix_timestamp), MEMBERSHIP_TIMEZONE)
    nearest_midnight = moment.date() + timedelta(days=1) if moment.time() >= time(12, 0) else moment.date()
    return nearest_midnight - timedelta(days=1)


def fee_text(team, price=None):
    """"€10.00 every 6 months", or "€10.00 per period" paid once per period."""
    price = price or payments.retrieve_price(team.stripe_price_id)
    if not price.get("recurring"):
        return f"{payments.format_amount(int(price['unit_amount']), price['currency'])} per period"
    return payments.fee_text(price)


# --- Paying ------------------------------------------------------------------------


def _metadata(membership):
    return {
        "purpose": payments.PURPOSE_TEAM,
        "team": membership.team.slug,
        "team_membership_id": str(membership.id),
        "user_id": str(membership.user_id),
    }


def _one_time_checkout(user, team, membership, member):
    """Pay once for a period: the one under way to join, the next one to renew."""
    price = payments.retrieve_price(team.stripe_price_id)
    amount = int(price["unit_amount"])
    if membership.status == APPROVED:
        covers_until = joining_period(team)["paid_until"]
    else:
        covers_until = next_period_until(membership)
    until = format_membership_date_display(covers_until)
    metadata = {**_metadata(membership), "covers_until": covers_until.isoformat()}
    description = f"{team.name}, until {until}"
    customer = payments.customer_params(member)
    if "customer_email" in customer:
        customer["customer_creation"] = "always"
    params = dict(
        mode="payment",
        metadata=metadata,
        payment_method_types=list(payments.PAYMENT_METHOD_TYPES),
        line_items=[{"price": team.stripe_price_id, "quantity": 1}],
        payment_intent_data={"metadata": metadata, "description": description},
        # A receipt the member can keep, from Stripe.
        invoice_creation={"enabled": True, "invoice_data": {"metadata": metadata, "description": description}},
        custom_text={"submit": {"message": (
            f"You pay {payments.format_amount(amount, price['currency'])} for {team.name} until {until}. "
            "Nothing renews by itself: before it ends, you are reminded to pay for the next period."
        )}},
        success_url=build_public_url("teams.teams_home", paid=team.slug),
        cancel_url=build_public_url("teams.teams_home"),
        **customer,
    )
    key = f"checkout:team:{membership.id}:{covers_until.isoformat()}:{payments.request_fingerprint(params)}"
    session = payments.create_checkout_session(params, idempotency_key=key)
    membership.stripe_checkout_session_id = session.get("id")
    membership.payment_state = None
    _audit("team_checkout_started", user, membership, covers_until=covers_until.isoformat())
    return session["url"]


def needs_to_pay(membership):
    """In a team that started charging after they joined, and not paying yet."""
    return (
        membership is not None and membership.status == ACTIVE
        and membership.payment_mode == PAYMENT_SUBSCRIPTION
        and not membership.stripe_subscription_id and membership.paid_until is not None
    )


def start_checkout(user, team):
    """The Stripe Checkout address where ``user`` pays to join ``team``.

    The same open Checkout again if there is one: two open ones could both be
    paid. A subscription left over from a payment that failed is cancelled
    first, so a second attempt does not leave two running.

    Also for a member of a team that has started charging (:func:`needs_to_pay`):
    paying before their free time ends starts the subscription with the next
    period and charges nothing today.
    """
    membership = ongoing_membership(user, team)
    if membership is None or not charges(team) or not (
        membership.status == APPROVED or needs_to_pay(membership) or renewal_open(membership)
    ):
        raise ConflictError("There is nothing to pay for this team.", code="team_nothing_to_pay")
    membership = _locked_membership(membership.id, team)
    if membership.payment_state == PROCESSING:
        raise ConflictError("Your payment is on its way. It can take a few days by SEPA debit.",
                            code="team_payment_processing")
    member = getattr(user, "member", None)
    if member is None:
        raise ConflictError("Teams are for members of the association.", code="team_not_joinable")

    if membership.status == APPROVED:
        membership.payment_mode = team.payment_mode  # the way the team charges now
    existing = payments.open_checkout_session(membership.stripe_checkout_session_id, what="team Checkout",
                                              email=member.email_private)
    if existing is not None:
        return existing["url"]

    if once_per_period(team):
        return _one_time_checkout(user, team, membership, member)

    replaced = membership.stripe_subscription_id
    if replaced:
        payments.cancel_subscription(replaced, reason=f"team membership {membership.id}: paying again")
        membership.stripe_subscription_id = None

    price = payments.retrieve_price(team.stripe_price_id)
    amount = int(price["unit_amount"])
    product = price.get("product")
    product = product if isinstance(product, str) else (product or {}).get("id")
    metadata = _metadata(membership)
    free_until = membership.paid_until if needs_to_pay(membership) else None
    if free_until is not None and (free_until - get_membership_today()).days >= NEXT_PERIOD_WITHIN_DAYS:
        # Free until then: the subscription starts with the next period.
        charged_from = free_until + timedelta(days=1)
        line_items = [{"price": team.stripe_price_id, "quantity": 1}]
        message = (f"Nothing is charged today. From {format_membership_date_display(charged_from)} "
                   f"{fee_text(team, price)} for {team.name}, until you leave.")
    else:
        period = joining_period(team)
        charged_from = period["charged_from"]
        line_items = [
            # The period under way, now; on the team's own product.
            {"price_data": {"currency": price["currency"], "product": product, "unit_amount": amount},
             "quantity": 1},
            {"price": team.stripe_price_id, "quantity": 1},
        ]
        message = (
            f"Today you pay {payments.format_amount(amount, price['currency'])} for {team.name} "
            f"until {format_membership_date_display(period['paid_until'])}. Then {fee_text(team, price)}, "
            f"from {format_membership_date_display(charged_from)}, until you leave."
        )
    params = dict(
        mode="subscription",
        metadata=metadata,
        payment_method_types=list(payments.PAYMENT_METHOD_TYPES),
        line_items=line_items,
        subscription_data={
            "trial_end": start_of_day_unix(charged_from),
            "metadata": metadata,
            "description": team.name,
        },
        custom_text={"submit": {"message": message}},
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


def _end(membership, reason, now, tell_leads=True):
    membership.status = ENDED
    membership.ended_at = now
    membership.end_reason = reason
    membership.ends_on = None
    _audit("team_membership_ended", None, membership, end_reason=reason)
    _sync_forum([membership.user], membership.team, f"left team {membership.team.slug}")
    name = _member_name(membership.user)
    team = membership.team
    if reason == END_NOT_RENEWED:
        _tell_person("team_not_renewed", membership)
        if tell_leads:
            _tell_leads(team, "team_member_left", f"{name} did not renew their membership in {team.name}.",
                        person_name=name)
    elif reason == END_PAYMENT_FAILED:
        _tell_person("team_payment_ended", membership)
        _tell_leads(team, "team_member_left", f"{name} is no longer in {team.name}: the payment did not go through.",
                    person_name=name)
    elif reason == END_MEMBERSHIP_ENDED:
        _tell_leads(team, "team_member_left", f"{name} left {team.name} with their association membership.",
                    person_name=name)
    else:
        _tell_leads(team, "team_member_left", f"{name} left {team.name}.", person_name=name)


def _leaving_reason(membership):
    return END_MEMBERSHIP_ENDED if membership.ends_with_association else END_LEFT


def _activate(membership, now, payment_mode):
    # How they actually paid -- the team may have changed its way of charging
    # since they were approved.
    membership.payment_mode = payment_mode
    membership.status = ACTIVE
    membership.started_at = now
    membership.payment_settled_at = now
    _audit("team_payment_settled", None, membership, payment_mode=membership.payment_mode)
    _sync_forum([membership.user], membership.team, f"joined team {membership.team.slug}")
    _tell_person("team_approved", membership)
    name = _member_name(membership.user)
    _tell_leads(membership.team, "team_member_joined", f"{name} joined {membership.team.name}.", person_name=name)


def _checkout_completed(session):
    if session.get("mode") == "payment":
        return _one_time_completed(session)
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
    if not subscription_id:
        return  # the receipt of a payment once per period; its Checkout recorded it
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
            paid_at=get_now_utc(), team_bears_fee=teams_bear_fees(),
        ))
    if covers_until and (membership.paid_until is None or covers_until > membership.paid_until):
        membership.paid_until = covers_until
    membership.payment_state = PAID
    membership.stripe_subscription_id = membership.stripe_subscription_id or subscription_id
    if membership.status == APPROVED:
        _activate(membership, get_now_utc(), PAYMENT_SUBSCRIPTION)
    elif membership.status == ACTIVE:
        _audit("team_payment_received", None, membership, invoice_id=invoice.get("id"))
    else:
        stop_charging(membership, "paid for a membership no longer under way")
        _tell_admins("team_paid_after_ending",
                     "A team fee was paid for a membership that had already ended. Refund it in Stripe.",
                     team=membership.team.slug, team_membership_id=membership.id, invoice_id=invoice.get("id"))


def _replaced(membership, subscription_id):
    """A subscription the membership has moved on from -- one cancelled when
    the person paid again, say. What happens to it changes nothing here."""
    return bool(membership.stripe_subscription_id) and membership.stripe_subscription_id != subscription_id


def _payment_failed(invoice):
    subscription_id = invoice_subscription_id(invoice)
    membership = _membership_for(invoice_subscription_metadata(invoice), subscription_id)
    if membership is not None and _replaced(membership, subscription_id):
        return
    if membership is not None and membership.status in (APPROVED, ACTIVE):
        membership.payment_state = FAILED
        _audit("team_payment_failed", None, membership, invoice_id=invoice.get("id"))


def _subscription_updated(subscription):
    membership = _membership_for(subscription.get("metadata"), subscription.get("id"))
    if membership is None or membership.status not in (APPROVED, ACTIVE):
        return
    if membership.stripe_subscription_id != subscription.get("id"):
        return  # an old one, or one not yet recorded: checkout or invoice brings it
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
    if membership.status != ACTIVE or membership.payment_mode != PAYMENT_SUBSCRIPTION:
        return  # free now, or paying once per period: the subscription running out is not leaving
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
    if membership is None or _replaced(membership, subscription.get("id")):
        return
    if membership.stripe_subscription_id == subscription.get("id"):
        membership.stripe_subscription_id = None
    reason = (subscription.get("cancellation_details") or {}).get("reason")
    if membership.status == ACTIVE and membership.payment_mode != PAYMENT_SUBSCRIPTION:
        return  # the team became free, or is paid once per period now; they stay
    if membership.status == ACTIVE:
        failed = reason in ("payment_failed", "payment_disputed")
        _end(membership, END_PAYMENT_FAILED if failed else _leaving_reason(membership), get_now_utc())
    elif membership.status == APPROVED:
        membership.payment_state = FAILED


def _dispute_closed(dispute):
    if dispute.get("status") != "lost":
        return
    import stripe

    payments.apply_runtime_stripe_config()
    charge = stripe.Charge.retrieve(dispute.get("charge"))
    payment = _payment_of_charge(charge, dispute.get("payment_intent"))
    invoice_id = payment.stripe_invoice_id if payment is not None else None
    if payment is None:
        _tell_admins("team_dispute_unmatched", "A lost chargeback on a team fee matches no recorded payment.",
                     charge_id=dispute.get("charge"), invoice_id=invoice_id)
        return
    payment.status = "disputed"
    membership = _locked_membership(payment.team_membership_id) if payment.team_membership_id else None
    if membership is not None and membership.status == ACTIVE:
        stop_charging(membership, "chargeback lost")
        _end(membership, END_PAYMENT_FAILED, get_now_utc())


def _payment_of_charge(charge, payment_intent=None):
    """The recorded payment a Stripe charge paid: by its payment, else by its invoice."""
    payment_intent = payment_intent or charge.get("payment_intent")
    payment_intent = payment_intent if isinstance(payment_intent, str) else (payment_intent or {}).get("id")
    if payment_intent:
        found = db.session.execute(
            db.select(Payment).filter_by(stripe_payment_intent_id=payment_intent)
        ).scalars().first()
        if found is not None:
            return found
    invoice_id = invoice_id_of_charge(charge) or invoice_id_of_payment_intent(payment_intent)
    if not invoice_id:
        return None
    return db.session.execute(db.select(Payment).filter_by(stripe_invoice_id=invoice_id)).scalars().first()


def _charge_refunded(charge):
    """Money given back in Stripe comes off what the team earned."""
    payment = _payment_of_charge(charge)
    if payment is None:
        return
    refunded = int(charge.get("amount_refunded") or 0)
    if refunded != payment.refunded_cents:
        payment.refunded_cents = min(refunded, payment.amount_cents)
        log_audit_event("payments", "team_payment_refunded", metadata={
            "payment_id": payment.id, "team_id": payment.team_id, "refunded_cents": payment.refunded_cents,
        })


# --- Paid once per period ----------------------------------------------------------


def _one_time_completed(session):
    membership = _membership_for(session.get("metadata"))
    if membership is None:
        _tell_admins("team_payment_unmatched", "A completed team Checkout matches no team membership.",
                     session_id=session.get("id"))
        return
    payments.remember_customer(getattr(membership.user, "member", None), session.get("customer"))
    membership.stripe_checkout_session_id = None
    if session.get("payment_status") == "paid":
        _one_time_paid(session, membership)
    elif membership.status in (APPROVED, ACTIVE):
        # A SEPA debit: confirmed days later, by the event below.
        membership.payment_state = PROCESSING


def _one_time_async_succeeded(session):
    membership = _membership_for(session.get("metadata"))
    if membership is not None:
        _one_time_paid(session, membership)


def _one_time_async_failed(session):
    membership = _membership_for(session.get("metadata"))
    if membership is not None and membership.status in (APPROVED, ACTIVE):
        membership.payment_state = FAILED
        _audit("team_payment_failed", None, membership, session_id=session.get("id"))


def _one_time_paid(session, membership):
    """The money is in: record it once, and the membership runs to the end of the period."""
    from .clock import parse_iso_date

    payment_intent = session.get("payment_intent")
    payment_intent = payment_intent if isinstance(payment_intent, str) else (payment_intent or {}).get("id")
    if payment_intent and db.session.execute(
        db.select(Payment.id).filter_by(stripe_payment_intent_id=payment_intent)
    ).first() is not None:
        return  # reported twice
    covers_until = parse_iso_date((session.get("metadata") or {}).get("covers_until"))
    invoice = session.get("invoice")
    db.session.add(Payment(
        purpose=payments.PURPOSE_TEAM, user_id=membership.user_id, team_id=membership.team_id,
        team_membership_id=membership.id, stripe_payment_intent_id=payment_intent,
        stripe_invoice_id=invoice if isinstance(invoice, str) else (invoice or {}).get("id"),
        amount_cents=int(session.get("amount_total") or 0),
        currency=(session.get("currency") or "eur")[:3], covers_until=covers_until, paid_at=get_now_utc(),
        team_bears_fee=teams_bear_fees(),
    ))
    if covers_until and (membership.paid_until is None or covers_until > membership.paid_until):
        membership.paid_until = covers_until
    membership.payment_state = PAID
    if membership.status == APPROVED:
        _activate(membership, get_now_utc(), PAYMENT_ONE_TIME)
    elif membership.status == ACTIVE:
        _audit("team_payment_received", None, membership, covers_until=str(covers_until))
    else:
        _tell_admins("team_paid_after_ending",
                     "A team fee was paid for a membership that had already ended. Refund it in Stripe.",
                     team=membership.team.slug, team_membership_id=membership.id, payment_intent=payment_intent)


_HANDLERS = {
    "checkout.session.completed": _checkout_completed,
    "checkout.session.async_payment_succeeded": _one_time_async_succeeded,
    "checkout.session.async_payment_failed": _one_time_async_failed,
    "charge.refunded": _charge_refunded,
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
    """End memberships whose leaving day has passed, or that are not paid for.

    Paid by subscription, Stripe reports both itself; this is for when its word
    never arrived. Paid once per period, this is what ends a period nobody paid
    the next one for -- and its leads hear of everybody at once.
    """
    today = today or get_membership_today()
    ended = 0
    not_renewed = {}
    for found in db.session.execute(
        db.select(TeamMembership).where(TeamMembership.status == ACTIVE,
                                        TeamMembership.payment_mode.in_(CHARGING_MODES))
    ).scalars().all():
        membership = _locked_membership(found.id)
        if membership.status != ACTIVE:
            continue
        subscription = membership.payment_mode == PAYMENT_SUBSCRIPTION
        if membership.ends_on is not None and membership.ends_on < today:
            stop_charging(membership, "leaving day passed")
            _end(membership, _leaving_reason(membership), get_now_utc())
            ended += 1
        elif membership.paid_until is None or membership.paid_until >= today:
            continue
        elif not subscription:
            if (membership.payment_state == PROCESSING
                    and (today - membership.paid_until).days <= PROCESSING_GRACE_DAYS):
                continue  # paid for the next period, the debit not yet through
            _end(membership, END_NOT_RENEWED, get_now_utc(), tell_leads=False)
            not_renewed.setdefault(membership.team, []).append(_member_name(membership.user))
            ended += 1
        elif membership.stripe_subscription_id is None:
            # The team started charging, and they did not pay by the period start.
            _end(membership, END_PAYMENT_FAILED, get_now_utc())
            ended += 1
        elif (today - membership.paid_until).days > OVERDUE_DAYS:
            stop_charging(membership, "long unpaid")
            _end(membership, END_PAYMENT_FAILED, get_now_utc())
            ended += 1
    for team, names in not_renewed.items():
        _tell_leads(
            team, "team_members_lapsed",
            f"{len(names)} member(s) of {team.name} did not pay for the new period: {', '.join(sorted(names))}.",
            names=sorted(names),
        )
    return ended


def send_renewal_notices(today=None):
    """Paid once per period: remind each member to pay for the next one, once."""
    today = today or get_membership_today()
    sent = 0
    for membership in db.session.execute(
        db.select(TeamMembership).where(TeamMembership.status == ACTIVE,
                                        TeamMembership.payment_mode == PAYMENT_ONE_TIME)
    ).scalars().all():
        if (membership.paid_until is None or membership.ends_on is not None
                or membership.renewal_notice_for == membership.paid_until
                or (membership.paid_until - today).days > RENEWAL_NOTICE_DAYS
                or membership.paid_until < today):
            continue
        membership.renewal_notice_for = membership.paid_until
        _tell_person("team_renewal_due", membership, fee=membership.team.fee_display,
                     until=format_membership_date_display(membership.paid_until),
                     next_until=format_membership_date_display(next_period_until(membership)))
        sent += 1
    return sent

# --- A new fee -----------------------------------------------------------------------


def _team_price_now(payload):
    from ..db_models import Team

    team = db.session.get(Team, payload.get("team_id"))
    return team.stripe_price_id if team is not None and by_subscription(team) else None


def _tell_about_new_fee(payload, notice):
    """The email, when it is due -- unless the person has left or is leaving."""
    membership = db.session.get(TeamMembership, payload.get("team_membership_id"))
    if membership is None or membership.status not in (APPROVED, ACTIVE):
        return
    if membership.stripe_subscription_id != payload.get("subscription_id") or membership.ends_on is not None:
        return
    _tell_person("team_fee_changed", membership, **notice)


payments.register_price_moves(payments.PURPOSE_TEAM, current_price=_team_price_now, tell=_tell_about_new_fee)


# --- Following the association membership ---------------------------------------------
#
# Somebody who cancels their association membership -- on Stripe's billing
# page, most likely -- stays a member to the end of what they paid for, and
# with it in their teams. Told nothing, their team would renew in between and
# then end on the first night after the association, the time paid for beyond
# that lost. So as soon as the cancellation is known, each of their team
# memberships is set to end on the same day: its subscription stops then
# without renewing, and they and the leads are told. Taking the cancellation
# back lifts that again. An end somebody chose by leaving is left alone.


def association_ends_on(member, today=None):
    """The last day of an association membership set to end, or None while it renews."""
    from .periods import coverage_end

    if member is None or getattr(member, "deleted_at", None) is not None:
        return None
    if not (member.cancel_at_period_end or member.payment_status in ("canceled", "cancel_scheduled")):
        return None
    last_day = coverage_end(member)
    today = today or get_membership_today()
    return last_day if last_day is not None and last_day >= today else None


def follow_association_end(member, today=None):
    """Bring this member's team memberships in line with their association membership.

    Returns how many changed. A team Stripe could not be told about is left as
    it was, for the next run to try again.
    """
    from . import ExternalServiceError

    user = getattr(member, "user", None)
    if user is None:
        return 0
    ends = association_ends_on(member, today)
    changed = 0
    for found in db.session.execute(
        db.select(TeamMembership).filter_by(user_id=user.id, status=ACTIVE)
    ).scalars().all():
        membership = _locked_membership(found.id)
        if membership.status != ACTIVE:
            continue
        try:
            if ends is not None:
                changed += _end_with_association(membership, ends)
            elif membership.ends_with_association:
                changed += _continue_with_association(membership)
        except ExternalServiceError as exc:
            current_app.logger.warning(
                "Team membership %s not brought in line with the association: %s", membership.id, exc,
            )
    return changed


def _end_with_association(membership, ends):
    if membership.ends_with_association and membership.ends_on == ends:
        return 0
    if membership.ends_on is not None and not membership.ends_with_association:
        return 0  # leaving on their own; that subscription renews no more anyway
    if membership.stripe_subscription_id and membership.payment_mode == PAYMENT_SUBSCRIPTION:
        payments.cancel_on(membership.stripe_subscription_id, start_of_day_unix(ends + timedelta(days=1)))
    membership.ends_on = ends
    membership.ends_with_association = True
    _audit("team_ends_with_association", None, membership, ends_on=ends.isoformat())
    day = format_membership_date_display(ends)
    _tell_person("team_ends_with_association", membership, ends_on=day)
    name = _member_name(membership.user)
    _tell_leads(membership.team, "team_member_leaving",
                f"{name} is leaving {membership.team.name} on {day}, with their association membership.",
                person_name=name)
    return 1


def _continue_with_association(membership):
    if membership.stripe_subscription_id and membership.payment_mode == PAYMENT_SUBSCRIPTION:
        payments.clear_cancel_on(membership.stripe_subscription_id)
    membership.ends_on = None
    membership.ends_with_association = False
    _audit("team_continues_with_association", None, membership)
    name = _member_name(membership.user)
    _tell_leads(membership.team, "team_member_staying",
                f"{name} stays in {membership.team.name}: their association membership continues.",
                person_name=name)
    return 1


def follow_association_ends(today=None):
    """Every night: the same for everybody in a team, in case a webhook was missed."""
    from ..db_models import User

    changed = 0
    users = db.session.execute(
        db.select(User).join(TeamMembership, TeamMembership.user_id == User.id)
        .where(TeamMembership.status == ACTIVE).distinct()
    ).scalars().all()
    for user in users:
        if user.member is not None:
            changed += follow_association_end(user.member, today)
    return changed
