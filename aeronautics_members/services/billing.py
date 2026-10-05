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


import stripe
from flask import current_app
from flask_babel import _

from ..config import STRIPE_PRICE_ID
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
    get_member_by_email,
    get_member_by_stripe_reference,
)
from .membership import (
    PAYMENT_EVIDENCE_STATUSES,
    build_membership_cycle,
    format_membership_date_display,
    has_payment_evidence,
    invoice_coverage_year,
    member_has_active_access,
    set_member_membership_window,
    sync_member_active_state,
)
from .periods import active_periods, coverage_end, grant_calendar_year, grant_period
from .settings import get_stripe_settings_map
from .stripe_scope import scope_of_subscription
from .payments import (  # noqa: F401 -- shared with the teams; imported from here too
    apply_runtime_stripe_config,
    cancel_subscription,
    format_amount as format_checkout_amount,
    open_checkout_session,
    request_fingerprint,
    reusable_stripe_customer_id,
    subscription_has_scheduled_cancellation,
    subscription_period_bounds,
)




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
        # What this is, for the day the account sells something else too; see
        # services/stripe_scope.py.
        "purpose": "membership",
        "membership_starts_on": cycle["coverage_start"].isoformat(),
        "membership_ends_on": cycle["coverage_end"].isoformat(),
        "renewal_due_on": cycle["renewal_due_on"].isoformat(),
        "activation_mode": activation_mode,
        "member_email": member.email_private,
        "member_id": str(member.id),
        "user_id": str(member.user_id) if member.user_id else "",
    }


def backfill_member_stripe_references(member, customer_id=None, subscription_id=None):
    # Never re-attach a subscription to an erased member. Erasure cancels the
    # subscription and clears the reference; Stripe then reports the
    # cancellation back, and backfilling from that event would write the dead
    # subscription id straight back onto the row it was just removed from.
    if getattr(member, "deleted_at", None) is not None:
        return False

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
        subscription_list = stripe.Subscription.list(customer=member.stripe_customer_id, status="all", limit=20)
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
    # Newest first. A team fee on the same customer is not the membership.
    for subscription in subscriptions:
        if not scope_of_subscription(subscription).is_foreign:
            return subscription
    return None


# A membership has ended, rather than never started or still running: the
# subscription was cancelled ("canceled"), Stripe gave up on its payments
# ("failed"), or the coverage simply ran out ("expired").
REJOINABLE_MEMBER_STATUSES = {"canceled", "expired", "failed"}

# Stripe subscription statuses for a subscription Stripe is still running, and
# might still charge. Anything else -- "canceled", "incomplete_expired" -- is
# over, and cannot be restarted: Stripe treats both as final.
LIVE_SUBSCRIPTION_STATUSES = {"active", "trialing", "past_due", "unpaid", "incomplete", "paused"}


def can_rejoin(member):
    """Whether the account page should offer to start the membership again.

    Decided on what the portal knows, so the page does not ask Stripe on every
    load. Clicking asks Stripe before charging anything; see
    find_live_stripe_subscription.
    """
    if member is None or getattr(member, "deleted_at", None) is not None:
        return False
    if member_has_active_access(member):
        return False
    return member.payment_status in REJOINABLE_MEMBER_STATUSES


def find_live_stripe_subscription(member):
    """A subscription Stripe is still running for this member, or None.

    The last check before starting another one. The portal's own status can
    lag: on Jan 1 a paying member reads "expired" until the SEPA renewal
    clears, while Stripe is busy collecting it -- and a second subscription
    then would charge them twice. Raises StripeError when Stripe cannot be
    asked, because "could not tell" must not be taken for "none".
    """
    if member is None or not member.stripe_customer_id:
        return None
    apply_runtime_stripe_config()
    try:
        listing = stripe.Subscription.list(
            customer=member.stripe_customer_id, status="all", limit=20,
        )
    except stripe.StripeError as exc:
        if getattr(exc, "code", None) == "resource_missing":
            return None  # the customer itself is gone, and everything with it
        raise
    for subscription in listing.get("data", []) if hasattr(listing, "get") else []:
        if scope_of_subscription(subscription).is_foreign:
            continue  # a team fee running is not the membership running
        if subscription.get("status") in LIVE_SUBSCRIPTION_STATUSES:
            return subscription
    return None


def get_open_checkout_session(member):
    """The member's previous Checkout session, if it is still usable.

    Returns None when there is none, when it has expired, or when Stripe cannot
    be asked -- in every case the caller should simply start a new one, so a
    lookup failure must not be an error.
    """
    if member is None or not member.stripe_checkout_session_id:
        return None
    return open_checkout_session(
        member.stripe_checkout_session_id, what=f"stored Checkout session of member_id={member.id}",
        email=member.email_private,
    )


def checkout_completed_but_not_yet_confirmed(member):
    """Whether they finished Checkout and Stripe's confirmation has not arrived yet.

    Stripe confirms a payment to the portal separately, usually seconds after
    the payment page closes. In between the account page read "payment not
    finished" and offered to resume it -- to somebody who had just paid. Only
    detected here, not acted on: whether the money is actually in is what that
    confirmation says (a SEPA debit, for one, is still pending), so activating
    now would be guessing.
    """
    if member is None or member.payment_status != "pending_checkout":
        return False
    if not member.stripe_checkout_session_id or member.stripe_customer_id:
        return False
    apply_runtime_stripe_config()
    try:
        session = stripe.checkout.Session.retrieve(member.stripe_checkout_session_id)
    except stripe.StripeError as exc:
        current_app.logger.warning(
            "Could not check Checkout session %s for member_id=%s: %s",
            member.stripe_checkout_session_id, member.id, exc,
        )
        return False
    return session.get("status") == "complete"


def checkout_idempotency_key(member, cycle, checkout_params):
    """The key that makes a repeated Checkout request return the same session.

    A double-submitted form or a retried request sends the same thing twice,
    and must get the one session back rather than open a second -- two open
    sessions can both be paid. Stripe remembers a key for 24 hours.

    The request's content is part of the key. Stripe refuses a key reused with
    different parameters, and those change for legitimate reasons: a member's
    old Stripe customer was cleaned up, their address was corrected, the
    price was changed. With the member and year alone as the key, every one
    of those locked the member out of paying for a day ("Keys for idempotent
    requests can only be used with the same parameters"), which is how it was
    found on the test server.

    Somebody rejoining carries their ended subscription in the key as well:
    they may have joined earlier the same year with an identical request, and
    that key would hand back the first, completed session.
    """
    key = f"checkout:member:{member.id}:{cycle['current_year']}:{request_fingerprint(checkout_params)}"
    if member.stripe_subscription_id:
        key += f":after:{member.stripe_subscription_id}"
    return key


def create_checkout_session_for_member(member):
    """Start (or resume) the member's Checkout session for the current year.

    Sending someone back to a Checkout session they already have open is the
    point: two open sessions for one member can both be completed, which buys
    the association two subscriptions and one confused student.
    """
    existing = get_open_checkout_session(member)
    if existing is not None:
        current_app.logger.info(
            "Reusing open Checkout session %s for member_id=%s.", existing.get("id"), member.id
        )
        join_date = get_membership_today()
        price_details = get_stripe_membership_price()
        return existing, build_membership_cycle(join_date, price_details["unit_amount"])

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

    # One or the other: Stripe refuses both. An existing customer is kept, so
    # a returning member is one customer in Stripe, not two.
    customer_id = reusable_stripe_customer_id(member)
    customer_params = (
        {"customer": customer_id} if customer_id else {"customer_email": member.email_private}
    )

    checkout_params = dict(
        # Identifiers only. The profile itself is deliberately NOT sent: Stripe
        # caps a metadata value at 500 characters, and a perfectly ordinary
        # profile -- a double-barrelled surname, a title, two long university
        # addresses -- serialises past that and makes Session.create fail, so
        # the member is left unable to pay. The webhook reads the profile from
        # this database instead, which is also the copy that is actually
        # current: a member who edits their address mid-checkout used to have
        # it silently overwritten by the snapshot taken when checkout began.
        metadata=membership_metadata,
        payment_method_types=["card", "sepa_debit"],
        line_items=line_items,
        mode="subscription",
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
        **customer_params,
        success_url=build_public_url(
            "public.thank_you",
            method="checkout",
            phase=cycle["thank_you_phase"],
        ),
        cancel_url=build_public_url("public.cancel"),
    )
    checkout_session = stripe.checkout.Session.create(
        idempotency_key=checkout_idempotency_key(member, cycle, checkout_params),
        **checkout_params,
    )
    member.pending_checkout_started_at = get_now_utc()
    member.stripe_checkout_session_id = checkout_session.get("id")
    return checkout_session, cycle


def is_free_period_trial_invoice(invoice, member):
    """Whether ``invoice`` is the EUR 0 one Stripe issues when a free period starts.

    Somebody joining from October gets the rest of the year free, which Stripe
    models as a trial until Jan 1 -- and a trial still opens with an invoice,
    for nothing, marked paid at once. Taken as a payment it turned every October
    joiner into a paying member with a paid coverage record beside their free
    one, though the portal had already recorded the free period at signup.

    Deliberately narrow. Only the invoice that opens a subscription, only when
    it came to nothing, and only when that subscription is known to be a free
    period -- from its signup metadata, or because the free period is already
    on record for that year. A renewal settled by a coupon or account credit
    also comes to nothing, and that one must still extend the membership.
    """
    if not invoice or member is None:
        return False
    if invoice.get("billing_reason") != "subscription_create":
        return False
    # Compared with 0 rather than tested for falsiness: an invoice that does not
    # say what it came to is not evidence that it came to nothing.
    if invoice.get("total") != 0 or (invoice.get("amount_paid") or 0) != 0:
        return False

    # Where Stripe puts the subscription's metadata depends on the API version:
    # subscription_details on older ones, parent.subscription_details on newer.
    details = invoice.get("subscription_details") or (
        (invoice.get("parent") or {}).get("subscription_details")
    ) or {}
    if ((details.get("metadata") or {}).get("activation_mode") or "").strip() == "free_period":
        return True

    year = invoice_coverage_year(invoice)
    return any(
        period.reason == MembershipPeriod.REASON_FREE_PERIOD
        and (year is None or period.ends_on.year == year)
        for period in active_periods(member, include_future=True)
    )


def create_invoice_membership_for_member(member):
    stripe_settings = apply_runtime_stripe_config()
    price_id = stripe_settings.get("stripe_price_id") or STRIPE_PRICE_ID
    price_details = get_stripe_membership_price()
    join_date = get_membership_today()
    cycle = build_membership_cycle(join_date, price_details["unit_amount"])
    activation_mode = "free_period" if cycle["free_period"] else "paid_now"
    membership_metadata = build_membership_metadata(member, cycle, activation_mode)

    customer_id = reusable_stripe_customer_id(member)
    if customer_id is None:
        customer_id = stripe.Customer.create(
            email=member.email_private,
            name=f"{member.first_name} {member.last_name}",
        ).id

    subscription_params = {
        "customer": customer_id,
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
    member.stripe_customer_id = customer_id
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
    # Nothing in the subscription's lifecycle proves a payment, so every branch
    # below that would make somebody a member needs this as well. The dates
    # above are no help: they come from the signup's metadata, written before
    # anything was paid.
    evidence = has_payment_evidence(member)

    if not evidence and (
        subscription_status == "canceled"
        or cancel_at_period_end
        or (subscription_status in {"active", "trialing"} and activation_mode != "free_period")
    ):
        # A SEPA debit still being collected (the subscription is already
        # "trialing"), or a subscription cancelled because its first payment
        # never arrived. Neither is a membership. The payment webhooks settle
        # it: invoice.paid records the period, a failure records the failure.
        desired_status = None
        desired_active = False
    elif subscription_status == "canceled":
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


def record_paid_invoices_missing_from_ledger(member, subscription):
    """Record paid years the ledger missed. Returns how many periods were added.

    The safety net above advances the cached dates when an invoice.paid
    webhook never arrived -- but access is decided by the ledger, and nothing
    wrote the year into it. The member then showed paid through December and
    lost access three weeks into January, when the renewal grace ran out; and
    the nightly run, seeing a date far in the future, never looked at them
    again.

    So when the cached end runs past what the ledger covers, ask Stripe for
    the subscription's paid invoices and record each one exactly as
    invoice.paid would have: keyed on the invoice, so nothing is granted twice,
    and a year revoked for a lost chargeback is not granted again. Only a paid
    invoice counts -- the subscription being active is not evidence, and
    neither is an unpaid invoice.

    Asks Stripe nothing when the ledger already agrees, which is almost always:
    the account page runs this on every visit.
    """
    if member is None or not subscription or getattr(member, "deleted_at", None) is not None:
        return 0
    cached_end = member.membership_ends_on
    ledger_end = coverage_end(member)
    if cached_end is None or (ledger_end is not None and ledger_end >= cached_end):
        return 0
    subscription_id = subscription.get("id")
    if not subscription_id:
        return 0

    listing = stripe.Invoice.list(subscription=subscription_id, status="paid", limit=10)
    recorded = 0
    for invoice in (listing.get("data") or []):
        if (invoice.get("amount_paid") or 0) <= 0:
            # The EUR 0 invoice that opens an October free period. Not a payment.
            continue
        paid_at = (invoice.get("status_transitions") or {}).get("paid_at") or invoice.get("created")
        year = invoice_coverage_year(invoice) or (to_membership_date(paid_at).year if paid_at else None)
        if year is None:
            continue
        period = grant_calendar_year(
            member, year, MembershipPeriod.REASON_PAID,
            stripe_invoice_id=invoice.get("id"),
            stripe_subscription_id=subscription_id,
        )
        if period.id is None:
            recorded += 1
            current_app.logger.warning(
                "Recorded paid invoice %s for member_id=%s (year %s): its invoice.paid webhook never did.",
                invoice.get("id"), member.id, year,
            )
    return recorded


def cancel_member_subscription(member, *, reason=None):
    """End the member's subscription in Stripe now. Returns whether one was cancelled.

    Immediate rather than at period end, because the callers are erasure and
    expulsion: there will be nobody left to bill, and a subscription set to lapse
    "later" is one an unnoticed webhook failure can quietly keep alive.

    Cancelling does not refund anything -- Stripe only stops future invoices.
    Refunding the unused part of a year is a separate decision (and a separate
    booking for the treasurer), so it is deliberately not bundled in here.

    Already-cancelled and already-deleted subscriptions count as success: the
    caller asked for the subscription to be gone, and it is.
    """
    if member is None or not member.stripe_subscription_id:
        return False

    cancel_subscription(member.stripe_subscription_id, reason=f"member_id={member.id}: {reason or 'no reason given'}")

    member.stripe_subscription_id = None
    member.cancel_at_period_end = False
    return True


def sync_member_subscription_state_from_stripe(member):
    if member is None or not (member.stripe_customer_id or member.stripe_subscription_id):
        return False

    subscription = get_latest_stripe_subscription_for_member(member)
    if not subscription:
        return False

    return sync_member_subscription_state_from_subscription(member, subscription)


# --- A new membership fee -----------------------------------------------------------
#
# Entered as a new price ID under Settings -> Billing. New members pay it at
# once; every running membership subscription moves to it from its next
# renewal, in the background (services/payments.py), and its member is told
# two weeks before that renewal.


def _membership_price_id_now(payload=None):
    return get_stripe_settings_map().get("stripe_price_id") or STRIPE_PRICE_ID or None


def check_membership_price(new_price_id, old_price_id=None):
    """The new price, if it can be the membership's; ValidationError otherwise."""
    from . import ValidationError
    from .payments import interval_months, retrieve_price

    try:
        price = retrieve_price(new_price_id)
    except Exception as exc:  # noqa: BLE001 -- shown to the admin, nothing saved
        current_app.logger.warning("Could not check Stripe price %s: %s", new_price_id, exc)
        raise ValidationError(
            _("Stripe does not know that price, or could not be asked. Check the ID and the Stripe keys."),
            code="membership_price_unknown",
        ) from None
    if not price.get("active", True):
        raise ValidationError(_("That price is archived in Stripe."), code="membership_price_unsuitable")
    if interval_months(price) != 12 or price.get("unit_amount") in (None, 0):
        raise ValidationError(_("The membership needs a yearly recurring price with a fixed amount."),
                              code="membership_price_unsuitable")
    if old_price_id:
        try:
            old_product = (retrieve_price(old_price_id) or {}).get("product")
        except Exception:  # noqa: BLE001 -- the old one gone is no reason to refuse the new
            old_product = None
        new_product = price.get("product")
        new_product = new_product if isinstance(new_product, str) else (new_product or {}).get("id")
        old_product = old_product if isinstance(old_product, str) else (old_product or {}).get("id")
        if old_product and new_product != old_product:
            raise ValidationError(
                _("Create the new price on the same product as the old one, so Stripe and the portal "
                  "keep recognising it as the membership."),
                code="membership_price_other_product",
            )
    return price


def change_membership_price(actor, new_price_id):
    """Check a new membership price and move every running subscription to it.

    Returns how many subscriptions will move. Call before the setting is
    saved, in the same transaction.
    """
    from ..db_models import Member, db
    from .audit import log_audit_event
    from .payments import PURPOSE_MEMBERSHIP, schedule_price_move

    old_price_id = _membership_price_id_now()
    if not new_price_id or new_price_id == old_price_id:
        return 0
    check_membership_price(new_price_id, old_price_id)
    members = db.session.execute(
        db.select(Member).where(Member.stripe_subscription_id.isnot(None), Member.deleted_at.is_(None))
    ).scalars().all()
    for member in members:
        schedule_price_move(PURPOSE_MEMBERSHIP, member.stripe_subscription_id, new_price_id,
                            member=member, member_id=member.id)
    log_audit_event("billing", "membership_price_changed", actor_user=actor,
                    before={"stripe_price_id": old_price_id}, after={"stripe_price_id": new_price_id},
                    metadata={"subscriptions_to_move": len(members)})
    return len(members)


def _tell_member_about_new_fee(payload, notice):
    """The email, when it is due -- unless the member has left since."""
    from ..db_models import Member, db
    from .notifications import queue_user_status_notification

    member = db.session.get(Member, payload.get("member_id"))
    if member is None or member.deleted_at is not None or member.user is None:
        return
    if member.stripe_subscription_id != payload.get("subscription_id") or member.cancel_at_period_end:
        return  # cancelled, or on another subscription: the new fee never reaches them
    queue_user_status_notification(
        "membership_fee_changed", "The membership fee changes", member.user.email,
        payload={"first_name": member.first_name, **notice},
        target_user=member.user, target_member=member,
    )


def _register_membership_price_moves():
    from .payments import PURPOSE_MEMBERSHIP, register_price_moves

    register_price_moves(PURPOSE_MEMBERSHIP, current_price=_membership_price_id_now,
                         tell=_tell_member_about_new_fee)


_register_membership_price_moves()
