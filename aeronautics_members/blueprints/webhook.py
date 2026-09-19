"""Stripe webhook blueprint.

Handles incoming Stripe events (checkout, invoices, subscriptions, disputes) and
keeps membership billing state in sync. Moved verbatim out of app.py; the only
changes are the blueprint route decorator and app.logger -> current_app.logger.
CSRF exemption is applied at registration time in create_app.
"""


import stripe
from flask import Blueprint, current_app, request
from flask_babel import _

from ..db_models import Member, db
from ..config import (
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
)
from ..services.billing import (
    apply_runtime_stripe_config,
    backfill_member_coverage_from_subscription,
    backfill_member_stripe_references,
    get_member_by_stripe_or_email,
    sync_member_subscription_state_from_subscription,
)
from ..services.clock import (
    first_day_of_year,
    get_membership_today,
    get_now_utc,
    last_day_of_year,
    parse_iso_date,
    to_membership_date,
)
from ..services.forum import (
    generate_unique_forum_username,
)
from ..db_models import MembershipPeriod
from ..services.outbox import enqueue_forum_sync
from ..services.periods import (
    grant_calendar_year,
    grant_period,
    revoke_periods_for_subscription,
)
from ..services.membership import (
    invoice_coverage_year,
    member_has_active_access,
    set_member_membership_window,
    sync_member_active_state,
    update_member_paid_coverage,
)
from ..services.notifications import (
    queue_curated_admin_notification,
)
from ..services.settings import (
    get_stripe_settings_map,
)
from ..services.webhook_inbox import (
    claim_stripe_event,
    complete_stripe_event,
    release_stripe_event,
)
from ..services.workflows import (
    send_member_welcome_email,
)
from ..notification_service import (
    ADMIN_ERROR_CHANNEL,
)

webhook_bp = Blueprint("webhook", __name__)


@webhook_bp.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get("stripe-signature")

    stripe_settings = get_stripe_settings_map()
    webhook_secret = stripe_settings.get("stripe_webhook_secret") or STRIPE_WEBHOOK_SECRET
    if not webhook_secret:
        # Fail closed: without a configured signing secret, construct_event would
        # verify against an empty key, which an unauthenticated caller can forge.
        current_app.logger.error("Stripe webhook secret is not configured; rejecting webhook.")
        return "Webhook signing secret not configured", 500

    try:
        stripe.api_key = stripe_settings.get("stripe_secret_key") or STRIPE_SECRET_KEY
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError as e:
        current_app.logger.error(f"Webhook Error: Invalid payload: {e}")
        return "Invalid payload", 400
    except stripe.SignatureVerificationError as e:
        current_app.logger.error(f"Webhook Error: Invalid signature: {e}")
        return "Invalid signature", 400

    event_type = event["type"]
    event_id = event.get("id")

    # Claim the event up front so two concurrent duplicate deliveries cannot
    # both proceed; the loser gets a duplicate-key rejection and is skipped.
    if not claim_stripe_event(event_id, event_type):
        current_app.logger.info("Ignoring duplicate Stripe webhook event %s (%s).", event_id, event_type)
        return "Already processed", 200

    try:
        body, status = process_stripe_event(event)
    except Exception as exc:
        # Processing failed after the claim; release it so Stripe can retry.
        release_stripe_event(event_id, error=exc)
        raise

    if status >= 400:
        release_stripe_event(event_id, error=body)
    else:
        # Only a handler that ran to completion may suppress redeliveries.
        complete_stripe_event(event_id)
    return body, status


def process_stripe_event(event):
    """Apply a verified Stripe event and return (body, status).

    Idempotency (claim/release of the event id) is handled by the caller.
    """
    event_type = event["type"]

    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        metadata = session.get("metadata", {})
        customer_id = session.get("customer")
        subscription_id = session.get("subscription")

        try:
            # The member already exists: signup commits the profile before it
            # asks Stripe for a Checkout session, so these identifiers always
            # resolve. The profile deliberately does not travel through Stripe
            # -- see create_checkout_session_for_member -- and the copy held
            # here is in any case fresher than one snapshotted at checkout time.
            member = get_member_by_stripe_or_email(
                customer_id=customer_id,
                subscription_id=subscription_id,
                member_id=metadata.get("member_id"),
                user_id=metadata.get("user_id"),
                email=metadata.get("member_email")
                or (session.get("customer_details") or {}).get("email"),
            )
            if member is None:
                # Nothing can be done here without inventing a member, and a
                # blank one would be worse than a loud failure: it would hold a
                # paid subscription that nobody can match to a person.
                current_app.logger.error(
                    "Checkout completed for a member that cannot be resolved (session=%s, customer=%s).",
                    session.get("id"), customer_id,
                )
                queue_curated_admin_notification(
                    ADMIN_ERROR_CHANNEL,
                    "stripe_webhook_unknown_member",
                    _("A completed Stripe checkout could not be matched to a member."),
                    payload={
                        "event_type": event_type,
                        "session_id": session.get("id"),
                        "customer_id": customer_id,
                        "subscription_id": subscription_id,
                    },
                    severity="warning",
                    commit=True,
                )
                return "Unknown member", 400

            previously_active = member_has_active_access(member)
            member.pending_checkout_started_at = member.pending_checkout_started_at or get_now_utc()
            # The attempt is finished, so stop pointing resume at its session.
            member.stripe_checkout_session_id = None
            backfill_member_stripe_references(member, customer_id=customer_id, subscription_id=subscription_id)

            if member.user is not None:
                if not member.user.forum_username:
                    member.user.forum_username = generate_unique_forum_username(
                        member.first_name,
                        member.last_name,
                        member.year_group,
                        exclude_user_id=member.user.id,
                    )

            starts_on = parse_iso_date(metadata.get("membership_starts_on")) or get_membership_today()
            ends_on = parse_iso_date(metadata.get("membership_ends_on")) or last_day_of_year(starts_on.year)
            renewal_due_on = parse_iso_date(metadata.get("renewal_due_on")) or first_day_of_year(ends_on.year + 1)
            activation_mode = metadata.get("activation_mode", "paid_now")
            session_payment_status = session.get("payment_status")

            if activation_mode == "free_period":
                set_member_membership_window(
                    member,
                    starts_on=starts_on,
                    ends_on=ends_on,
                    renewal_due_on=renewal_due_on,
                    payment_status="free_period",
                    is_active=True,
                    cancel_at_period_end=False,
                )
                # Record why: joined on/after Oct 1, so the rest of the year is free.
                grant_period(
                    member, starts_on, ends_on, MembershipPeriod.REASON_FREE_PERIOD,
                    stripe_subscription_id=subscription_id,
                    note="Free rest-of-year period for an October or later signup.",
                )
                member.pending_checkout_started_at = None
            elif session_payment_status == "paid":
                set_member_membership_window(
                    member,
                    starts_on=starts_on,
                    ends_on=ends_on,
                    renewal_due_on=renewal_due_on,
                    payment_status="paid",
                    is_active=True,
                    cancel_at_period_end=False,
                )
                grant_period(
                    member, starts_on, ends_on, MembershipPeriod.REASON_PAID,
                    stripe_invoice_id=session.get("invoice"),
                    stripe_subscription_id=subscription_id,
                    note="Prorated membership paid during Checkout.",
                )
                member.pending_checkout_started_at = None
            else:
                set_member_membership_window(
                    member,
                    starts_on=starts_on,
                    ends_on=ends_on,
                    renewal_due_on=renewal_due_on,
                    payment_status="processing",
                    is_active=False,
                    cancel_at_period_end=False,
                )

            db.session.commit()

            if member_has_active_access(member) and not previously_active:
                send_member_welcome_email(current_app._get_current_object(), member)

            current_app.logger.info(
                "SUCCESS: Membership checkout completed for %s. Session ID: %s",
                member.email_private,
                session.get("id"),
            )
        except Exception as exc:
            current_app.logger.exception("FATAL DB ERROR on Webhook for session %s", session.get("id"))
            db.session.rollback()
            queue_curated_admin_notification(
                ADMIN_ERROR_CHANNEL,
                "stripe_webhook_processing_failed",
                _("A Stripe checkout webhook could not be processed."),
                payload={
                    "event_type": event_type,
                    "session_id": session.get("id"),
                    "customer_id": session.get("customer"),
                    "subscription_id": session.get("subscription"),
                    "error": str(exc),
                },
                severity="critical",
                commit=True,
            )
            return "Database save failed", 500

    elif event_type == "payment_intent.processing":
        payment_intent = event["data"]["object"]
        customer_id = payment_intent.get("customer")
        member = get_member_by_stripe_or_email(
            customer_id=customer_id,
            email=payment_intent.get("receipt_email"),
            fetch_customer_email=True,
        )
        if member and not member_has_active_access(member):
            backfill_member_stripe_references(member, customer_id=customer_id)
            member.payment_status = "processing"
            db.session.commit()
            current_app.logger.info("Payment is processing for Stripe Customer ID: %s", customer_id)

    elif event_type in ["payment_intent.succeeded", "invoice.paid", "invoice.payment_succeeded"]:
        data_object = event["data"]["object"]
        customer_id = data_object.get("customer")
        subscription_id = data_object.get("subscription")
        customer_email = data_object.get("customer_email") or data_object.get("receipt_email")
        member = get_member_by_stripe_or_email(
            customer_id=customer_id,
            subscription_id=subscription_id,
            email=customer_email,
            fetch_customer_email=True,
        )
        if member:
            previous_status = member.payment_status
            backfill_member_stripe_references(member, customer_id=customer_id, subscription_id=subscription_id)

            paid_timestamp = None
            coverage_year = None
            if event_type.startswith("invoice"):
                paid_timestamp = (data_object.get("status_transitions") or {}).get("paid_at") or data_object.get("created")
                # Advance coverage by the invoice's billing period, not the payment
                # instant, so a renewal charge always lands in the right year.
                coverage_year = invoice_coverage_year(data_object)
            else:
                paid_timestamp = data_object.get("created")
            paid_on = to_membership_date(paid_timestamp)

            update_member_paid_coverage(member, paid_on, coverage_year=coverage_year)
            # The paid invoice is the evidence for this coverage year, and its id
            # is the idempotency key: a redelivered event cannot grant twice.
            grant_calendar_year(
                member,
                coverage_year or paid_on.year,
                MembershipPeriod.REASON_PAID,
                stripe_invoice_id=data_object.get("id") if event_type.startswith("invoice") else None,
                stripe_subscription_id=subscription_id,
            )
            member.pending_checkout_started_at = None
            # Queued rather than called here: the sync commits with the coverage
            # change, so it cannot be lost, and Discourse being slow no longer
            # makes Stripe's webhook time out.
            enqueue_forum_sync(member, reason="Payment confirmed.")
            db.session.commit()
            current_app.logger.info(
                "SUCCESS: Payment confirmed and member coverage updated for Stripe Customer ID: %s",
                customer_id,
            )

            if member_has_active_access(member) and previous_status in {"unpaid", "processing", "pending_checkout", "failed"}:
                send_member_welcome_email(current_app._get_current_object(), member)
        else:
            current_app.logger.error(
                "Webhook for successful payment received, but no member found for Stripe reference customer=%s subscription=%s email=%s",
                customer_id,
                subscription_id,
                customer_email,
            )
            queue_curated_admin_notification(
                ADMIN_ERROR_CHANNEL,
                "stripe_webhook_member_not_found",
                _("A successful Stripe payment webhook could not be matched to a member."),
                payload={
                    "event_type": event_type,
                    "customer_id": customer_id,
                    "subscription_id": subscription_id,
                    "customer_email": customer_email,
                },
                severity="warning",
                commit=True,
            )
            return "Member not found", 400

    elif event_type == "customer.subscription.updated":
        subscription = event["data"]["object"]
        customer_id = subscription.get("customer")
        subscription_id = subscription.get("id")
        member = get_member_by_stripe_or_email(
            customer_id=customer_id,
            subscription_id=subscription_id,
            fetch_customer_email=True,
        )
        if member:
            sync_member_subscription_state_from_subscription(member, subscription)
            enqueue_forum_sync(member, reason="Subscription updated.")
            db.session.commit()
            current_app.logger.info(
                "Subscription updated for member_id=%s customer=%s subscription=%s status=%s cancel_at_period_end=%s cancel_at=%s",
                member.id,
                customer_id,
                subscription_id,
                subscription.get("status"),
                subscription.get("cancel_at_period_end"),
                subscription.get("cancel_at"),
            )
        else:
            current_app.logger.warning(
                "Subscription update webhook received, but no member was found for customer=%s subscription=%s status=%s cancel_at_period_end=%s cancel_at=%s",
                customer_id,
                subscription_id,
                subscription.get("status"),
                subscription.get("cancel_at_period_end"),
                subscription.get("cancel_at"),
            )

    elif event_type in ["payment_intent.payment_failed", "invoice.payment_failed"]:
        data_object = event["data"]["object"]
        customer_id = data_object.get("customer")
        subscription_id = data_object.get("subscription")
        customer_email = data_object.get("customer_email") or data_object.get("receipt_email")
        member = get_member_by_stripe_or_email(
            customer_id=customer_id,
            subscription_id=subscription_id,
            email=customer_email,
            fetch_customer_email=True,
        )
        if member:
            backfill_member_stripe_references(member, customer_id=customer_id, subscription_id=subscription_id)
            member.payment_status = "failed"
            if not member_has_active_access(member):
                member.is_active = False
            enqueue_forum_sync(member, reason="Payment failed.")
            db.session.commit()
            current_app.logger.warning("Payment failed for Stripe Customer ID: %s", customer_id)
        else:
            current_app.logger.warning(
                "Webhook for failed payment received, but no Stripe Customer ID was provided."
            )

    elif event_type == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        customer_id = subscription.get("customer")
        subscription_id = subscription.get("id")
        member = get_member_by_stripe_or_email(
            customer_id=customer_id,
            subscription_id=subscription_id,
            fetch_customer_email=True,
        )
        if member:
            backfill_member_stripe_references(member, customer_id=customer_id, subscription_id=subscription_id)
            backfill_member_coverage_from_subscription(member, subscription)
            member.cancel_at_period_end = False
            cancellation_details = subscription.get("cancellation_details", {})
            reason = cancellation_details.get("reason")
            event_date = to_membership_date(event.get("created"))

            if reason == "payment_failed":
                member.payment_status = "failed"
                member.is_active = False
                current_app.logger.warning(
                    "Subscription for Stripe Customer ID: %s was canceled due to failed payment.",
                    customer_id,
                )
            else:
                member.payment_status = "canceled"
                member.is_active = member_has_active_access(member, event_date)
                current_app.logger.info(
                    "Subscription canceled for Stripe Customer ID: %s. Coverage remains valid until the covered year ends.",
                    customer_id,
                )

            sync_member_active_state(member, event_date)
            enqueue_forum_sync(member, reason="Subscription canceled.")
            db.session.commit()
        else:
            current_app.logger.warning(
                "Webhook for subscription cancellation received, but no member found for Stripe reference customer=%s subscription=%s",
                customer_id,
                subscription_id,
            )

    elif event_type == "charge.dispute.closed":
        dispute = event["data"]["object"]
        if dispute["status"] == "lost":
            charge_id = dispute.get("charge")
            try:
                apply_runtime_stripe_config()
                charge = stripe.Charge.retrieve(charge_id)
            except Exception as exc:
                # Swallowing this and reporting success would drop the event for
                # good: the member stays active on a charge we lost. Fail so the
                # claim is released and Stripe retries the delivery.
                current_app.logger.error("Could not load charge %s for lost dispute: %s", charge_id, exc)
                return "Could not resolve disputed charge", 500

            customer_id = charge.get("customer")
            if customer_id:
                member = Member.query.filter_by(stripe_customer_id=customer_id).first()
                if member:
                    member.is_active = False
                    member.payment_status = "dispute_lost"
                    # The payment was reversed, so revoke the coverage it bought
                    # rather than leaving an unsupported grant on the record.
                    revoke_periods_for_subscription(
                        member, member.stripe_subscription_id,
                        reason=f"Chargeback lost for charge {charge_id}.",
                    )
                    db.session.commit()
                    current_app.logger.error(
                        "DISPUTE LOST for Stripe Customer ID: %s. Member has been deactivated.",
                        customer_id,
                    )
                    # Revoke forum access to match the local state change. Queued
                    # so a forum outage cannot leave the revocation undone.
                    enqueue_forum_sync(member, reason="Chargeback lost.")
                    db.session.commit()
                else:
                    current_app.logger.warning(
                        "Lost dispute for Stripe customer %s, but no member holds that reference.",
                        customer_id,
                    )

    return "Success", 200
