"""What happens once a membership profile is saved -- one way, from both doors.

The public signup and "create membership profile" each used to carry their own
copy of this, and the copies drifted: the second never sent the confirmation
link to the university or company address, so nobody coming in that way could
be reconnected to their old forum account. See services/signup.py.

Here rather than in services because it flashes and redirects.
"""

import stripe
from flask import current_app, flash, redirect, url_for
from flask_babel import _

from ..db_models import db
from ..services.billing import (
    create_checkout_session_for_member,
    create_invoice_membership_for_member,
)
from ..services.forum import sync_member_forum_state
from ..services.identity import (
    send_email_verification_email,
    send_work_email_verification_email,
)
from ..services.workflows import send_member_welcome_email


def start_membership(member, payment_method, *, what):
    """Send the confirmation links, then start paying. Returns the response.

    ``what`` is "account" for a signup that made the login too, "profile" for
    somebody who already had one, and "rejoin" for a membership starting again
    after it ended; it only changes the wording of a message about something
    that went wrong after the membership was saved.
    """
    app = current_app._get_current_object()
    user = member.user

    try:
        if user is not None and not user.email_is_verified:
            send_email_verification_email(app, user)
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
            send_work_email_verification_email(app, member)
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
            return redirect(url_for(
                "public.thank_you", method="invoice", phase=cycle["thank_you_phase"],
            ))

        session, _cycle = create_checkout_session_for_member(member)
        db.session.commit()
        return redirect(session.url, code=303)

    except stripe.StripeError as exc:
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
        if what == "rejoin":
            flash(_("Payment could not be started right now. Please try rejoining again later."), "warning")
        elif what == "account":
            flash(_("Your account was created, but payment could not be started. Please log in and resume your membership from your account page."), "warning")
        else:
            flash(_("Your membership profile was created, but payment could not be started. You can resume it from your account page."), "warning")
    except Exception:  # noqa: BLE001
        current_app.logger.exception(
            "Unexpected error while starting a membership: payment_method=%s member_id=%s",
            payment_method, member.id,
        )
        if what == "rejoin":
            flash(_("Billing could not be started right now. Please try rejoining again later."), "warning")
        elif what == "account":
            flash(_("Your account was created, but an unexpected error occurred while starting billing. Please log in and resume your membership from your account page."), "warning")
        else:
            flash(_("Your membership profile was created, but billing could not be started right now. You can resume it from your account page."), "warning")

    return redirect(url_for("account.account"))
