"""Payments, subscriptions and the Stripe boundary.

Everything that speaks to Stripe lives here, so the rest of the application
works with membership concepts rather than Stripe's object shapes. That matters
in two directions: Stripe changes its API (billing periods moved onto
subscription items in the Basil version, which this module absorbs), and the
association's rules must not be re-derived differently by each caller.

The central rule is that **subscription state is not proof of payment**. A
subscription Stripe charges itself only stays ``active`` while its invoices are
paid, so there the lifecycle is usable evidence. An invoice-billed subscription
goes ``active`` when its trial ends and stays ``active`` while the invoice is
merely outstanding -- so for those, payment must come from an actual paid
invoice, never from the status.
"""

import json
from datetime import datetime, timezone
from decimal import Decimal

import stripe
from babel.numbers import format_currency
from flask import current_app
from flask_babel import _, get_locale

from ..config import STRIPE_PRICE_ID, STRIPE_SECRET_KEY
from ..db_models import MembershipPeriod
from ..security_utils import build_public_url
from .clock import (
    first_day_of_year,
    get_membership_today,
    get_now_utc,
    last_day_of_year,
    parse_iso_date,
    to_membership_date,
)
from .members import (
    build_member_payload,
    get_member_by_email,
    get_member_by_stripe_reference,
)
from .membership import (
    PAYMENT_EVIDENCE_STATUSES,
    build_membership_cycle,
    format_membership_date_display,
    set_member_membership_window,
    sync_member_active_state,
)
from .periods import grant_period
from .settings import get_stripe_settings_map




def apply_runtime_stripe_config():
    stripe_settings = get_stripe_settings_map()
    stripe.api_key = stripe_settings.get("stripe_secret_key") or STRIPE_SECRET_KEY
    return stripe_settings


def subscription_has_scheduled_cancellation(subscription):
    if not subscription:
        return False

    if bool(subscription.get("cancel_at_period_end")):
        return True

    cancel_at = subscription.get("cancel_at")
    if cancel_at is None:
        return False

    try:
        return int(cancel_at) > int(datetime.now(timezone.utc).timestamp())
    except (TypeError, ValueError):
        return False


def subscription_period_bounds(subscription):
    """Return the (start, end) unix timestamps of a subscription's current period.

    Stripe's Basil API version (2025-03-31) removed the top-level
    ``current_period_start`` / ``current_period_end`` fields and moved them onto
    each subscription item. Read the item-level values first and fall back to the
    legacy top-level fields, so this keeps working whichever API version the
    installed library and the account negotiate.
    """
    if not subscription:
        return None, None

    items = ((subscription.get("items") or {}).get("data")) or []
    starts = [item.get("current_period_start") for item in items if item.get("current_period_start")]
    ends = [item.get("current_period_end") for item in items if item.get("current_period_end")]

    period_start = min(starts) if starts else subscription.get("current_period_start")
    period_end = max(ends) if ends else subscription.get("current_period_end")
    return period_start, period_end


def subscription_collects_payment_automatically(subscription):
    """True when Stripe itself collects payment for this subscription.

    A ``charge_automatically`` subscription only becomes (and stays) ``active``
    once the latest invoice was actually paid, so its status is usable as
    evidence of payment. A ``send_invoice`` subscription goes ``active`` when the
    trial ends and stays ``active`` while the invoice is merely outstanding, so
    its status proves nothing about whether money arrived.
    """
    if not subscription:
        return False
    collection_method = subscription.get("collection_method") or "charge_automatically"
    return collection_method == "charge_automatically"


def get_stripe_membership_price():
    stripe_settings = apply_runtime_stripe_config()
    price_id = stripe_settings.get("stripe_price_id") or STRIPE_PRICE_ID
    if not price_id:
        raise ValueError("Stripe membership pricing is not configured.")
    price = stripe.Price.retrieve(price_id, expand=["product"])
    recurring = price.get("recurring") or {}
    interval = recurring.get("interval")
    interval_count = recurring.get("interval_count", 1)
    is_yearly = (interval == "year" and interval_count == 1) or (interval == "month" and interval_count == 12)
    if not is_yearly:
        raise ValueError(
            f"STRIPE_PRICE_ID must point to an annual recurring Stripe price. "
            f"Got interval={interval!r}, interval_count={interval_count!r}."
        )

    unit_amount = price.get("unit_amount")
    if unit_amount is None:
        raise ValueError("The Stripe membership price must have a fixed unit_amount.")

    return {
        "id": price["id"],
        "currency": price["currency"],
        "unit_amount": int(unit_amount),
        "interval": interval,
        "interval_count": int(interval_count),
    }


def format_checkout_amount(amount_cents, currency):
    locale = str(get_locale()) if get_locale() else None
    amount = Decimal(amount_cents) / Decimal("100")
    try:
        return format_currency(amount, currency.upper(), locale=locale)
    except Exception:
        return f"{amount:.2f} {currency.upper()}"


def build_checkout_submit_message(cycle, price_details):
    coverage_end = format_membership_date_display(cycle["coverage_end"])
    renewal_due_on = format_membership_date_display(cycle["renewal_due_on"])
    annual_fee = format_checkout_amount(price_details["unit_amount"], price_details["currency"])

    if cycle["free_period"]:
        return _(
            "No payment is due today. Your membership is active through %(coverage_end)s. "
            "The annual fee of %(annual_fee)s will be charged on %(renewal_due_on)s unless you cancel beforehand.",
            coverage_end=coverage_end,
            annual_fee=annual_fee,
            renewal_due_on=renewal_due_on,
        )

    prorated_fee = format_checkout_amount(cycle["prorated_amount_cents"], price_details["currency"])
    return _(
        "Today you pay %(prorated_fee)s for membership through %(coverage_end)s. "
        "The annual fee of %(annual_fee)s will be charged on %(renewal_due_on)s unless you cancel beforehand.",
        prorated_fee=prorated_fee,
        coverage_end=coverage_end,
        annual_fee=annual_fee,
        renewal_due_on=renewal_due_on,
    )


def build_prorated_line_item(cycle, price_details):
    if cycle["prorated_amount_cents"] <= 0:
        return None

    return {
        "price_data": {
            "currency": price_details["currency"],
            "product_data": {
                "name": _(
                    "Membership through %(coverage_end)s (prorated)",
                    coverage_end=format_membership_date_display(cycle["coverage_end"]),
                ),
            },
            "unit_amount": cycle["prorated_amount_cents"],
        },
        "quantity": 1,
    }


def build_membership_metadata(member, cycle, activation_mode):
    return {
        "membership_starts_on": cycle["coverage_start"].isoformat(),
        "membership_ends_on": cycle["coverage_end"].isoformat(),
        "renewal_due_on": cycle["renewal_due_on"].isoformat(),
        "activation_mode": activation_mode,
        "member_email": member.email_private,
        "member_id": str(member.id),
        "user_id": str(member.user_id) if member.user_id else "",
    }


def backfill_member_stripe_references(member, customer_id=None, subscription_id=None):
    changed = False

    if customer_id and isinstance(customer_id, str) and customer_id.startswith("cus_") and member.stripe_customer_id != customer_id:
        member.stripe_customer_id = customer_id
        changed = True

    if (
        subscription_id
        and isinstance(subscription_id, str)
        and subscription_id.startswith("sub_")
        and member.stripe_subscription_id != subscription_id
    ):
        member.stripe_subscription_id = subscription_id
        changed = True

    return changed


def get_member_by_stripe_or_email(
    customer_id=None,
    subscription_id=None,
    member_id=None,
    user_id=None,
    email=None,
    fetch_customer_email=False,
):
    member = get_member_by_stripe_reference(
        customer_id=customer_id,
        subscription_id=subscription_id,
        member_id=member_id,
        user_id=user_id,
    )
    if member is not None:
        return member

    member = get_member_by_email(email)
    if member is not None:
        return member

    if customer_id and fetch_customer_email:
        try:
            apply_runtime_stripe_config()
            customer = stripe.Customer.retrieve(customer_id)
        except Exception as exc:
            current_app.logger.warning(
                "Could not retrieve Stripe customer %s while resolving a pending member: %s",
                customer_id,
                exc,
            )
            return None

        member = get_member_by_email(customer.get("email"))
        if member is not None:
            return member

    return None


def get_latest_stripe_subscription_for_member(member):
    if member is None:
        return None

    if member.stripe_subscription_id:
        apply_runtime_stripe_config()
        try:
            return stripe.Subscription.retrieve(member.stripe_subscription_id)
        except stripe.StripeError as exc:
            if getattr(exc, "code", None) != "resource_missing":
                raise
            # The subscription no longer exists in Stripe (e.g. deleted). Clear the
            # dead reference and fall through to a customer lookup instead of failing.
            current_app.logger.warning(
                "Stripe subscription %s for member_id=%s no longer exists; clearing the stale reference.",
                member.stripe_subscription_id,
                member.id,
            )
            member.stripe_subscription_id = None

    if not member.stripe_customer_id:
        return None

    apply_runtime_stripe_config()
    try:
        subscription_list = stripe.Subscription.list(customer=member.stripe_customer_id, status="all", limit=1)
    except stripe.StripeError as exc:
        if getattr(exc, "code", None) != "resource_missing":
            raise
        current_app.logger.warning(
            "Stripe customer %s for member_id=%s no longer exists; clearing the stale reference.",
            member.stripe_customer_id,
            member.id,
        )
        member.stripe_customer_id = None
        return None
    subscriptions = subscription_list.get("data", []) if hasattr(subscription_list, "get") else []
    return subscriptions[0] if subscriptions else None


def create_checkout_session_for_member(member):
    stripe_settings = apply_runtime_stripe_config()
    price_id = stripe_settings.get("stripe_price_id") or STRIPE_PRICE_ID
    price_details = get_stripe_membership_price()
    join_date = get_membership_today()
    cycle = build_membership_cycle(join_date, price_details["unit_amount"])
    activation_mode = "free_period" if cycle["free_period"] else "paid_now"
    membership_metadata = build_membership_metadata(member, cycle, activation_mode)
    line_items = [{"price": price_id, "quantity": 1}]
    prorated_line_item = build_prorated_line_item(cycle, price_details)
    if prorated_line_item is not None:
        line_items.insert(0, prorated_line_item)

    checkout_payload = build_member_payload(member)
    checkout_session = stripe.checkout.Session.create(
        payment_method_types=["card", "sepa_debit"],
        line_items=line_items,
        mode="subscription",
        metadata={**membership_metadata, "member_data": json.dumps(checkout_payload)},
        subscription_data={
            "trial_end": cycle["trial_end_unix"],
            "metadata": membership_metadata,
        },
        custom_text={
            "submit": {
                "message": build_checkout_submit_message(cycle, price_details),
            }
        },
        payment_method_collection="always",
        customer_email=member.email_private,
        success_url=build_public_url(
            "public.thank_you",
            method="checkout",
            phase=cycle["thank_you_phase"],
        ),
        cancel_url=build_public_url("public.cancel"),
    )
    member.pending_checkout_started_at = get_now_utc()
    return checkout_session, cycle


def create_invoice_membership_for_member(member):
    stripe_settings = apply_runtime_stripe_config()
    price_id = stripe_settings.get("stripe_price_id") or STRIPE_PRICE_ID
    price_details = get_stripe_membership_price()
    join_date = get_membership_today()
    cycle = build_membership_cycle(join_date, price_details["unit_amount"])
    activation_mode = "free_period" if cycle["free_period"] else "paid_now"
    membership_metadata = build_membership_metadata(member, cycle, activation_mode)

    customer = stripe.Customer.create(
        email=member.email_private,
        name=f"{member.first_name} {member.last_name}",
    )

    subscription_params = {
        "customer": customer.id,
        "items": [{"price": price_id}],
        "collection_method": "send_invoice",
        "days_until_due": 30,
        "trial_end": cycle["trial_end_unix"],
        "metadata": membership_metadata,
    }
    prorated_line_item = build_prorated_line_item(cycle, price_details)
    if prorated_line_item is not None:
        subscription_params["add_invoice_items"] = [prorated_line_item]

    subscription = stripe.Subscription.create(**subscription_params)
    member.pending_checkout_started_at = get_now_utc()
    member.stripe_customer_id = customer.id
    member.stripe_subscription_id = subscription.id

    if cycle["free_period"]:
        set_member_membership_window(
            member,
            starts_on=cycle["coverage_start"],
            ends_on=cycle["coverage_end"],
            renewal_due_on=cycle["renewal_due_on"],
            payment_status="free_period",
            is_active=True,
            cancel_at_period_end=False,
        )
        # This grants access, so it needs a record saying why. Without one the
        # member would be covered with nothing in the ledger to support it.
        grant_period(
            member,
            cycle["coverage_start"],
            cycle["coverage_end"],
            MembershipPeriod.REASON_FREE_PERIOD,
            stripe_subscription_id=subscription.id,
            note="Free rest-of-year period for an October or later signup (invoice billing).",
        )
    else:
        set_member_membership_window(
            member,
            starts_on=cycle["coverage_start"],
            ends_on=cycle["coverage_end"],
            renewal_due_on=cycle["renewal_due_on"],
            payment_status="unpaid",
            is_active=False,
            cancel_at_period_end=False,
        )

    return subscription, cycle


def backfill_member_coverage_from_subscription(member, subscription):
    if member is None or not subscription:
        return False

    metadata = subscription.get("metadata", {}) or {}
    starts_on = parse_iso_date(metadata.get("membership_starts_on"))
    ends_on = parse_iso_date(metadata.get("membership_ends_on"))
    renewal_due_on = parse_iso_date(metadata.get("renewal_due_on"))
    changed = False

    # Subscription metadata is written once at signup and never refreshed on
    # renewal, so it is stale for any member past their first year. Treat it as a
    # backfill for MISSING dates only, and never move coverage backwards:
    # membership_ends_on / renewal_due_on may be filled in or extended, but never
    # regressed to an older signup-year value (which would expire a paid member).
    if starts_on and member.membership_starts_on is None:
        member.membership_starts_on = starts_on
        changed = True
    if ends_on and (member.membership_ends_on is None or ends_on > member.membership_ends_on):
        member.membership_ends_on = ends_on
        changed = True
    if renewal_due_on and (member.renewal_due_on is None or renewal_due_on > member.renewal_due_on):
        member.renewal_due_on = renewal_due_on
        changed = True

    return changed


def sync_member_subscription_state_from_subscription(member, subscription):
    if member is None or not subscription:
        return False

    changed = backfill_member_stripe_references(
        member,
        customer_id=subscription.get("customer") or member.stripe_customer_id,
        subscription_id=subscription.get("id"),
    )

    if backfill_member_coverage_from_subscription(member, subscription):
        changed = True

    cancel_at_period_end = subscription_has_scheduled_cancellation(subscription)
    if member.cancel_at_period_end != cancel_at_period_end:
        member.cancel_at_period_end = cancel_at_period_end
        changed = True

    subscription_status = subscription.get("status")
    activation_mode = ((subscription.get("metadata", {}) or {}).get("activation_mode") or "").strip()

    # Safety net for a missed renewal webhook: an active (charged) subscription's
    # current period is the authoritative paid coverage year, so advance coverage
    # from it (extend-only) before deciding active state. Without this, a missed
    # invoice.paid would leave stale (prior-year) coverage and expire a paid member
    # on the next reconcile.
    automatic_payment = subscription_collects_payment_automatically(subscription)

    # Safety net for a missed renewal webhook: an active *automatically charged*
    # subscription's current period is the authoritative paid coverage year, so
    # advance coverage from it (extend-only) before deciding active state. Without
    # this, a missed invoice.paid would leave stale (prior-year) coverage and
    # expire a paid member on the next reconcile. Invoice-billed subscriptions are
    # excluded: they go active on an unpaid invoice, so their period is not
    # evidence of paid coverage.
    if subscription_status == "active" and automatic_payment:
        period_start, _period_end = subscription_period_bounds(subscription)
        active_year = to_membership_date(period_start).year if period_start else None
        if active_year is not None:
            active_end = last_day_of_year(active_year)
            if member.membership_ends_on is None or active_end > member.membership_ends_on:
                member.membership_starts_on = first_day_of_year(active_year)
                member.membership_ends_on = active_end
                member.renewal_due_on = first_day_of_year(active_year + 1)
                changed = True

    coverage_is_current = bool(member.membership_ends_on and member.membership_ends_on >= get_membership_today())

    if subscription_status == "canceled":
        desired_status = "canceled"
        desired_active = coverage_is_current
    elif cancel_at_period_end:
        desired_status = "cancel_scheduled" if coverage_is_current else member.payment_status
        desired_active = coverage_is_current
    elif subscription_status in {"active", "trialing"} and coverage_is_current:
        # activation_mode is frozen at signup, so it only describes the first
        # (trial) period: an Oct+ joiner trials for free until Jan 1, a prorated
        # joiner has already paid. Once the subscription is "active" the annual
        # fee has actually been charged, so the member is paid regardless of the
        # stale signup metadata (otherwise a paid renewal reverts to free_period).
        if subscription_status == "trialing" and activation_mode == "free_period":
            # An explicitly granted free rest-of-year period.
            desired_status = "free_period"
            desired_active = True
        elif automatic_payment:
            # Stripe collects payment itself here, so "active" means the annual fee
            # was really charged and "trialing" follows a completed Checkout. Trust
            # the lifecycle over the stale signup metadata, otherwise a paid renewal
            # would revert to free_period.
            desired_status = "paid"
            desired_active = True
        elif member.payment_status in PAYMENT_EVIDENCE_STATUSES:
            # Invoice-billed, and real evidence (invoice.paid) already established
            # the status. Preserve it, but never promote into it from here.
            desired_status = member.payment_status
            desired_active = True
        else:
            # Invoice-billed and still unpaid: Stripe marks the subscription active
            # once the trial ends even while the invoice is merely outstanding, so
            # this is not proof of payment and must not grant access. Leave the
            # local state for the invoice webhooks to settle.
            desired_status = None
            desired_active = member.is_active
    else:
        desired_status = None
        desired_active = member.is_active

    if desired_status and member.payment_status != desired_status:
        member.payment_status = desired_status
        changed = True

    if member.is_active != desired_active:
        member.is_active = desired_active
        changed = True

    if sync_member_active_state(member):
        changed = True

    return changed


def sync_member_subscription_state_from_stripe(member):
    if member is None or not (member.stripe_customer_id or member.stripe_subscription_id):
        return False

    subscription = get_latest_stripe_subscription_for_member(member)
    if not subscription:
        return False

    return sync_member_subscription_state_from_subscription(member, subscription)
