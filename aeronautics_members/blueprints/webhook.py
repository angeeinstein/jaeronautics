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
    cancel_member_subscription,
    get_member_by_stripe_or_email,
    is_free_period_trial_invoice,
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
    has_coverage,
    revoke_periods_for_payment,
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
from ..services.stripe_scope import (
    NO_INVOICE,
    invoice_id_of_charge,
    invoice_id_of_payment_intent,
    invoice_subscription_id,
    scope_of_event,
)
from ..services.webhook_inbox import (
    claim_stripe_event,
    complete_stripe_event,
    release_stripe_event,
    stripe_event_already_processed,
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
        if stripe_event_already_processed(event_id):
            current_app.logger.info("Ignoring duplicate Stripe webhook event %s (%s).", event_id, event_type)
            return "Already processed", 200
        # Claimed but not finished: another delivery is working on it, or one
        # died holding the claim. A 2xx here would tell Stripe to stop
        # retrying, and if that other delivery never finishes, nothing ever
        # applies the event. Ask for a retry instead; by then it has either
        # completed (and gets the 200 above) or its lease has run out and the
        # retry takes it over.
        current_app.logger.warning(
            "Stripe webhook event %s (%s) is still being processed; asking Stripe to retry.",
            event_id, event_type,
        )
        return "Event is still being processed; retry later", 409

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


def _stop_charging_after_lost_dispute(member, charge_id):
    """Cancel the subscription of somebody who lost a chargeback.

    A lost dispute takes the year's access away, but the subscription went on:
    on 1 January Stripe would charge the same card again -- one its holder had
    just reported as not authorised -- and a payment that went through would
    hand the year back. Cancelled now, so coming back is a decision to rejoin.

    After the revocation is committed, and never failing the event: the
    revocation is what matters and is done. If Stripe cannot be reached, an
    admin is told to cancel it by hand instead of the event being processed
    again.
    """
    if not member.stripe_subscription_id:
        return
    try:
        cancel_member_subscription(member, reason="dispute_lost")
        db.session.commit()
    except Exception as exc:  # noqa: BLE001 -- reported, never retried here
        db.session.rollback()
        current_app.logger.error(
            "Could not cancel the subscription of member_id=%s after a lost dispute: %s", member.id, exc
        )
        queue_curated_admin_notification(
            ADMIN_ERROR_CHANNEL,
            "dispute_subscription_not_cancelled",
            _("A chargeback was lost, but the subscription could not be cancelled. Cancel it in Stripe."),
            payload={"member_id": member.id, "charge_id": charge_id,
                     "subscription_id": member.stripe_subscription_id},
            target_member=member,
            commit=True,
        )


def process_stripe_event(event):
    """Apply a verified Stripe event and return (body, status).

    Idempotency (claim/release of the event id) is handled by the caller.
    """
    event_type = event["type"]

    # The account sells more than the membership, or will: a team's fee, a
    # payment link. Only what is recognisably the membership's is acted on;
    # see services/stripe_scope.py for why the doubtful ones are safe to leave.
    scope = scope_of_event(event)
    if scope.is_unreachable:
        # Answered with an error so the event is not marked done, and Stripe
        # delivers it again later -- by then it can be asked.
        current_app.logger.warning(
            "Stripe event %s (%s) left for Stripe to deliver again: %s.",
            event.get("id"), event_type, scope.reason,
        )
        return "Could not ask Stripe what this is; deliver it again later", 503
    if scope.is_foreign and not (
        # A SEPA debit starting is reported before, or without, its invoice
        # being easy to find, and marking a member "processing" by mistake
        # costs nothing; missing it hides the "your payment is on its way"
        # page from somebody who has just paid.
        event_type == "payment_intent.processing" and scope.reason == NO_INVOICE
    ):
        current_app.logger.info(
            "Ignoring Stripe event %s (%s): not the membership -- %s.",
            event.get("id"), event_type, scope.reason,
        )
        return "Not a membership event", 200
    if scope.is_unclear:
        current_app.logger.warning(
            "Ignoring Stripe event %s (%s): nothing to tell whether it is the membership -- %s.",
            event.get("id"), event_type, scope.reason,
        )
        queue_curated_admin_notification(
            ADMIN_ERROR_CHANNEL,
            "stripe_event_scope_unclear",
            _("A Stripe event could not be told apart from the membership and was left alone. If it was a membership payment -- made by hand in Stripe, say -- grant the period to the member yourself."),
            payload={"event_id": event.get("id"), "event_type": event_type, "reason": scope.reason},
            severity="warning",
            commit=True,
        )
        return "Not recognisably a membership event", 200

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

            # The forum learns about the membership here too, not only from
            # invoice.paid: an October joiner's free period has no payment, so
            # no invoice.paid that counts, and somebody already on the forum --
            # a returning student, a member rejoining -- would otherwise keep
            # their old groups until they happened to open it.
            enqueue_forum_sync(member, reason="Checkout completed.")
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
        # Newer API versions moved an invoice's subscription under parent.
        subscription_id = invoice_subscription_id(data_object)
        customer_email = data_object.get("customer_email") or data_object.get("receipt_email")
        member = get_member_by_stripe_or_email(
            customer_id=customer_id,
            subscription_id=subscription_id,
            email=customer_email,
            fetch_customer_email=True,
        )
        if member and event_type.startswith("invoice") and is_free_period_trial_invoice(data_object, member):
            # The EUR 0 invoice that opens an October joiner's free period. The
            # free period itself is recorded at signup, and checkout completing
            # is what activates it; this is not a payment and records none.
            backfill_member_stripe_references(member, customer_id=customer_id, subscription_id=subscription_id)
            db.session.commit()
            current_app.logger.info(
                "Free-period opening invoice %s for member_id=%s left as a free period, not a payment.",
                data_object.get("id"), member.id,
            )
        elif member:
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

            # A welcome is for joining. A renewal can pass through the same
            # statuses -- a SEPA debit reads "processing" for days -- and
            # every renewing member was welcomed to the association again.
            this_year = coverage_year or paid_on.year
            joining = not any(
                period.ends_on.year < this_year for period in member.membership_periods or []
            )
            if (
                joining
                and member_has_active_access(member)
                and previous_status in {"unpaid", "processing", "pending_checkout", "failed"}
            ):
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
        # Newer API versions moved an invoice's subscription under parent.
        subscription_id = invoice_subscription_id(data_object)
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
            # Decided afresh rather than kept: a first payment that failed must
            # not carry forward an is_active nothing ever paid for. A member
            # with a paid year still running keeps it -- the ledger says so.
            member.is_active = False
            if member_has_active_access(member):
                member.is_active = True
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
                # A subscription cancelled because a chargeback was lost is
                # still "dispute lost": that is what the member, and an admin
                # looking at the account, need to read -- not a plain "canceled".
                if member.payment_status != "dispute_lost":
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
                # Which invoice the money paid, so the year it bought is the one
                # revoked. Older API versions name it on the charge; newer ones
                # only through the invoice's payment record.
                disputed_invoice_id = invoice_id_of_charge(charge) or invoice_id_of_payment_intent(
                    dispute.get("payment_intent") or charge.get("payment_intent")
                )
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
                    # The payment was reversed, so revoke the coverage it bought
                    # rather than leaving an unsupported grant on the record.
                    revoke_periods_for_payment(
                        member, disputed_invoice_id, member.stripe_subscription_id,
                        reason=f"Chargeback lost for charge {charge_id}.",
                    )
                    # A dispute over an earlier year's payment leaves the year
                    # paid for since; only when nothing covers today any more
                    # does the membership end here.
                    still_covered = has_coverage(member)
                    if not still_covered:
                        member.is_active = False
                        member.payment_status = "dispute_lost"
                    db.session.commit()
                    current_app.logger.error(
                        "DISPUTE LOST for Stripe Customer ID: %s (invoice %s). %s",
                        customer_id, disputed_invoice_id or "unknown",
                        "Another paid period still covers today." if still_covered
                        else "Member has been deactivated.",
                    )
                    # Revoke forum access to match the local state change. Queued
                    # so a forum outage cannot leave the revocation undone.
                    enqueue_forum_sync(member, reason="Chargeback lost.")
                    db.session.commit()
                    _stop_charging_after_lost_dispute(member, charge_id)
                else:
                    current_app.logger.warning(
                        "Lost dispute for Stripe customer %s, but no member holds that reference.",
                        customer_id,
                    )

    return "Success", 200
