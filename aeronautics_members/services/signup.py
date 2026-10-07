"""Starting a membership once its profile exists -- one way, from both doors.

There are two ways in: the public signup, which makes the login and the
membership together, and "create membership profile", for somebody who already
has a login. Each used to carry its own copy of what happens next, and the
copies drifted: the second never sent the confirmation link to the university
or company address -- the link that shows somebody studies or works here, and
the one that gives a returning student their old forum account back. Nobody
coming in through that door could ever be reconnected.

So what happens after the profile is saved lives in one place, and every door
calls it: begin_membership, here. The API (api/signup.py, api/account.py)
turns its answer into an address for the browser to go to.
"""

import stripe
from flask import current_app, url_for

from ..db_models import Member, User, db
from . import ConflictError, ExternalServiceError
from .audit import log_audit_event, snapshot_member_for_audit, snapshot_user_for_audit
from .billing import (
    can_resume_payment,
    create_checkout_session_for_member,
    create_invoice_membership_for_member,
)
from .clock import get_now_utc
from .forum import generate_unique_forum_username, sync_member_forum_state
from .members import apply_member_profile
from .membership import sync_member_active_state
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


# --- Signing up ----------------------------------------------------------------------------
#
# Two doors: the public signup, which makes the login and the membership together,
# and "become a member" for somebody signed in without a membership. Both take
# values already checked against the form rules (forms.py, through api/_forms.py)
# and end in begin_membership above.


class SignupConflict(ConflictError):
    """The address already has an account: sign in instead."""


def sign_up(values, password, payment_method):
    """A new login and membership from the public signup. Returns (user, member,
    continuing): ``continuing`` is True when the same form was sent again for a
    signup whose payment never started -- then nothing new is made and the user
    is the one made the first time. The caller signs the user in.

    The same form sent twice -- a double click while Stripe is asked for the
    payment page, or filling it in again after cancelling the payment -- would
    otherwise answer "already exists". With the right password that is
    somebody signing in would let in anyway.
    """
    address = values["email_private"].strip().lower()
    existing_member = db.session.execute(db.select(Member).filter_by(email_private=address)).scalar_one_or_none()
    existing_user = db.session.execute(db.select(User).filter_by(email=address)).scalar_one_or_none()
    if existing_member is not None and sync_member_active_state(existing_member):
        db.session.commit()

    if existing_member is not None:
        user = existing_member.user
        if user is None:
            raise ConflictError("A membership profile with this email address already exists without a login. "
                                "Please contact us so we can sort it out.", code="profile_without_login")
        if (not user.is_disabled and user.check_password(password) and can_resume_payment(existing_member)
                and chosen_payment_method(payment_method) == "checkout"):
            return user, existing_member, True
        raise SignupConflict("An account with this email address already exists. Please sign in to manage or "
                             "resume your membership.", code="account_exists")
    if existing_user is not None:
        raise SignupConflict("An account with this email address already exists. Please sign in instead.",
                             code="account_exists")

    member = Member(created_at=get_now_utc(), payment_status="pending_checkout", is_active=False,
                    pending_checkout_started_at=get_now_utc())
    apply_member_profile(member, {**values, "email_private": address, "terms_accepted": True})
    user = User(email=address, forum_username=generate_unique_forum_username(
        member.first_name, member.last_name, member.year_group))
    user.set_password(password)
    member.user = user
    db.session.add(user)
    db.session.add(member)
    db.session.flush()
    log_audit_event(
        category="membership",
        event_type="public_membership_signup_started",
        actor_user=user,
        target_user=user,
        target_member=member,
        before=None,
        after={"user": snapshot_user_for_audit(user), "member": snapshot_member_for_audit(member)},
        metadata={"payment_method": chosen_payment_method(payment_method)},
    )
    db.session.commit()
    return user, member, False


def continue_signup(member):
    """Stripe's payment page again, for a signup sent a second time."""
    try:
        checkout, _cycle = create_checkout_session_for_member(member)
        db.session.commit()
        return checkout.url
    except stripe.StripeError as exc:
        db.session.rollback()
        current_app.logger.error("Could not continue an unfinished signup to Checkout for member_id=%s: %s",
                                 member.id, exc)
        raise ExternalServiceError("The payment page could not be opened right now. You can continue from your "
                                   "account.", code="payment_not_started") from None


def become_member(user, values, payment_method):
    """A membership for a login without one. Returns the new member, or None when
    the login has one already (a form sent twice: the caller goes on from there)."""
    if user.member is not None:
        return None
    address = (user.email or "").strip().lower()
    taken = db.session.execute(db.select(Member).filter_by(email_private=address)).scalar_one_or_none()
    if taken is not None:
        raise ConflictError("A membership profile with this email address already exists. Please contact us so "
                            "we can sort it out.", code="profile_exists")
    member = Member(created_at=get_now_utc(), payment_status="pending_checkout", is_active=False,
                    pending_checkout_started_at=get_now_utc())
    apply_member_profile(member, {**values, "email_private": address, "terms_accepted": True})
    # Added before anything queries: the username check below would otherwise
    # autoflush a membership the session does not hold yet.
    db.session.add(member)
    member.user = user
    user.email = address
    if not user.forum_username:
        user.forum_username = generate_unique_forum_username(
            member.first_name, member.last_name, member.year_group, exclude_user_id=user.id)
    before_user = snapshot_user_for_audit(user)
    db.session.flush()
    log_audit_event(
        category="membership",
        event_type="linked_membership_created",
        actor_user=user,
        target_user=user,
        target_member=member,
        before={"user": before_user, "member": None},
        after={"user": snapshot_user_for_audit(user), "member": snapshot_member_for_audit(member)},
        metadata={"payment_method": chosen_payment_method(payment_method)},
    )
    db.session.commit()
    return member
