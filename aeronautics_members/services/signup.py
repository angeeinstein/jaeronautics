"""Starting a membership once its profile exists -- one way, from both doors.

There are two ways in: the public signup, which makes the login and the
membership together, and "create membership profile", for somebody who already
has a login. Each used to carry its own copy of what happens next, and the
copies drifted: the second never sent the confirmation link to the university
or company address -- the link that shows somebody studies or works here, and
the one that gives a returning student their old forum account back. Nobody
coming in through that door could ever be reconnected.

So what happens after the profile is saved lives in one place, and every door
calls it: begin_membership, here. The pages turn its answer into a redirect
(blueprints/_signup.py), the API into an address for the browser.
"""

import stripe
from flask import current_app, url_for

from ..db_models import db
from . import ExternalServiceError
from .billing import create_checkout_session_for_member, create_invoice_membership_for_member
from .forum import sync_member_forum_state
from .identity import send_email_verification_email, send_work_email_verification_email
from .settings import get_settings_map
from .workflows import send_member_welcome_email

#: What went wrong after the membership was saved, by the door it came through.
_NOT_STARTED = {
    "account": "Your account was created, but payment could not be started. Please sign in and resume your "
               "membership from your account page.",
    "profile": "Your membership profile was created, but payment could not be started. You can resume it from "
               "your account page.",
    "rejoin": "Payment could not be started right now. Please try rejoining again later.",
}


def invoice_payments_allowed():
    """Whether paying by invoice is offered. Asked by the forms and the routes."""
    setting = get_settings_map(["invoice_payments_enabled"]).get("invoice_payments_enabled")
    return setting == "True"


def chosen_payment_method(requested):
    """What the person asked for, unless it is not on offer."""
    if requested == "invoice" and invoice_payments_allowed():
        return "invoice"
    return "checkout"


def begin_membership(member, payment_method, *, what, sent=None):
    """Send the confirmation links, then start paying. Returns where to go next:
    Stripe's payment page, or the thank-you page for an invoice.

    ``what`` is "account" for a signup that made the login too, "profile" for
    somebody who already had one, and "rejoin" for a membership starting again
    after it ended; it only changes the wording when paying could not start --
    an ExternalServiceError, with the membership saved all the same.
    ``sent(kind, address)`` is told of each confirmation email sent, so the
    caller can hold back a second one sent a moment later.
    """
    app = current_app._get_current_object()
    user = member.user
    sent = sent or (lambda kind, address: None)

    try:
        if user is not None and not user.email_is_verified:
            if send_email_verification_email(app, user):
                sent("verify-email", user.email)
    except Exception as email_exc:  # noqa: BLE001 -- the membership stands
        current_app.logger.warning(
            "Could not send verification email for user_id=%s: %s",
            getattr(user, "id", None), email_exc,
        )

    # The second link goes to the university or company address, which is
    # what confirms they study or work here -- and, for somebody who was on the
    # old forum, what reconnects their archived account.
    if member.email_work:
        try:
            if send_work_email_verification_email(app, member):
                sent("verify-work-email", member.email_work)
            db.session.commit()
        except Exception as email_exc:  # noqa: BLE001
            db.session.rollback()
            current_app.logger.warning(
                "Could not send university email confirmation for member_id=%s: %s",
                member.id, email_exc,
            )

    try:
        if payment_method == "invoice":
            _subscription, cycle = create_invoice_membership_for_member(member)
            forum_result = None
            if cycle["free_period"]:
                forum_result, _forum_service = sync_member_forum_state(member)
            db.session.commit()
            if cycle["free_period"]:
                send_member_welcome_email(app, member)
                if forum_result and forum_result.error:
                    current_app.logger.warning(
                        "Forum sync reported an issue after invoice activation for member_id=%s: %s",
                        member.id, forum_result.error,
                    )
            return url_for("public.thank_you", method="invoice", phase=cycle["thank_you_phase"])

        session, _cycle = create_checkout_session_for_member(member)
        db.session.commit()
        return session.url

    except stripe.StripeError as exc:
        db.session.rollback()
        error_body = getattr(exc, "json_body", {}) or {}
        error_details = error_body.get("error", {}) if isinstance(error_body, dict) else {}
        current_app.logger.error(
            "Stripe Error while starting a membership: type=%s message=%s user_message=%s "
            "code=%s param=%s request_id=%s http_status=%s payment_method=%s member_id=%s",
            type(exc).__name__, str(exc), error_details.get("message"),
            error_details.get("code"), error_details.get("param"),
            getattr(exc, "request_id", None), getattr(exc, "http_status", None),
            payment_method, member.id,
        )
    except Exception:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.exception(
            "Unexpected error while starting a membership: payment_method=%s member_id=%s",
            payment_method, member.id,
        )
    raise ExternalServiceError(_NOT_STARTED[what], code="payment_not_started")
