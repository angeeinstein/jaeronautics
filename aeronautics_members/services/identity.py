"""Account identity: who someone is, and proving it.

The signed links this module issues -- verify an address, reset a password,
enter the forum -- are bearer credentials sent by email, so each is scoped as
narrowly as the thing it authorises.

A verification link in particular is bound to the address it was sent to and to
a nonce that rotates when the address changes. Without that binding a link keeps
working after the account moves to a different address, which would let an
unproven address be marked verified -- and DiscourseConnect associates forum
accounts by email, so the forum would inherit the mistake.
"""

import secrets

from flask_babel import _
from itsdangerous import URLSafeTimedSerializer

from ..config import SECRET_KEY
from ..security_utils import build_public_url
from .clock import get_now_utc
from .notifications import send_account_action_email

TOKEN_MAX_AGE_VERIFY_EMAIL = 60 * 60 * 24 * 7
TOKEN_MAX_AGE_PASSWORD_RESET = 60 * 60 * 24
TOKEN_MAX_AGE_FORUM_ENTRY = 60 * 60 * 24 * 30
# A forum link may sign someone in, so that window is much shorter than the
# window in which the link still verifies the address.
TOKEN_MAX_AGE_FORUM_ENTRY_AUTO_LOGIN = 60 * 60




def get_token_serializer():
    return URLSafeTimedSerializer(SECRET_KEY)


def generate_token(purpose, **payload):
    return get_token_serializer().dumps(payload, salt=f"jaeronautics-{purpose}")


def read_token(token, purpose, max_age):
    return get_token_serializer().loads(token, salt=f"jaeronautics-{purpose}", max_age=max_age)


def rotate_password_reset_nonce(user):
    user.password_reset_nonce = secrets.token_urlsafe(24)
    return user.password_reset_nonce


def build_password_reset_token(user):
    nonce = user.password_reset_nonce or rotate_password_reset_nonce(user)
    return generate_token("reset-password", user_id=user.id, nonce=nonce)


def rotate_email_verification_nonce(user):
    user.email_verification_nonce = secrets.token_urlsafe(24)
    return user.email_verification_nonce


def build_email_verification_claims(user):
    """Claims that bind a verification link to one address on one account.

    A token carrying only ``user_id`` proves nothing about *which* address was
    confirmed: it stays valid after the account's email changes, so an old link
    could be used to mark a newly entered (unproven) address as verified. Binding
    the address itself plus a rotating nonce scopes each link to the address it
    was actually sent to.
    """
    nonce = user.email_verification_nonce or rotate_email_verification_nonce(user)
    return {"user_id": user.id, "email": (user.email or "").strip().lower(), "nonce": nonce}


def email_verification_claims_match(token_data, user):
    """True when a decoded token still proves ownership of the user's address."""
    if user is None or not isinstance(token_data, dict):
        return False

    token_email = (token_data.get("email") or "").strip().lower()
    current_email = (user.email or "").strip().lower()
    if not token_email or token_email != current_email:
        return False

    expected_nonce = user.email_verification_nonce
    # Tokens predating the nonce carry none; require one so old links cannot be
    # replayed against an account that has since been issued a fresh link.
    return bool(expected_nonce) and token_data.get("nonce") == expected_nonce


def mark_email_verified_from_token(token_data, user):
    """Verify ``user``'s address if the token really proves ownership of it.

    Returns True when the address was newly marked verified. The nonce is
    deliberately NOT rotated here: a verification and a forum magic link can be
    outstanding at the same time, and re-using a link for an already verified
    address is harmless. Rotation happens when the address changes, which is the
    event that must invalidate links issued for the previous address.
    """
    if not email_verification_claims_match(token_data, user):
        return False
    if user.email_is_verified:
        return False
    user.email_verified_at = get_now_utc()
    return True


def send_email_verification_email(app, user):
    token = generate_token("verify-email", **build_email_verification_claims(user))
    verify_url = build_public_url("auth.verify_email", token=token)
    return send_account_action_email(
        app,
        to_email=user.email,
        subject=_("Verify your Joanneum Aeronautics email"),
        preview_text=_("Confirm your email address for your Joanneum Aeronautics account."),
        action_url=verify_url,
        action_label=_("Verify Email"),
        heading=_("Confirm your email address"),
        body_lines=[
            _("Please confirm your email address for your Joanneum Aeronautics account."),
            _("This helps us keep your account secure and reach you when needed."),
        ],
        failure_event_type="verification_email_failed",
        failure_summary=_("A verification email could not be sent."),
        failure_payload={"email_type": "verification"},
        target_user=user,
    )


def rotate_work_email_verification_nonce(member):
    member.email_work_verification_nonce = secrets.token_urlsafe(24)
    return member.email_work_verification_nonce


def build_work_email_verification_claims(member):
    """Claims binding a link to one institutional address on one membership.

    Same reasoning as the account address: a token carrying only ``member_id``
    would stay valid after the address changed, so an old link could mark a
    newly typed and unproven address as verified.
    """
    nonce = (
        member.email_work_verification_nonce
        or rotate_work_email_verification_nonce(member)
    )
    return {
        "member_id": member.id,
        "email": (member.email_work or "").strip().lower(),
        "nonce": nonce,
    }


def work_email_verification_claims_match(token_data, member):
    if member is None or not isinstance(token_data, dict):
        return False

    token_email = (token_data.get("email") or "").strip().lower()
    current_email = (member.email_work or "").strip().lower()
    if not token_email or token_email != current_email:
        return False

    expected_nonce = member.email_work_verification_nonce
    return bool(expected_nonce) and token_data.get("nonce") == expected_nonce


def mark_work_email_verified_from_token(token_data, member):
    """True when the institutional address was newly marked verified."""
    if not work_email_verification_claims_match(token_data, member):
        return False
    if member.email_work_is_verified:
        return False
    member.email_work_verified_at = get_now_utc()
    return True


def send_work_email_verification_email(app, member):
    """Sent to the institutional address, which is the whole point.

    It goes to ``email_work`` and never to the account address: what is being
    established is that this person can read mail at the university or company,
    and sending it anywhere else would establish nothing.
    """
    if not (member.email_work or "").strip():
        return None
    token = generate_token(
        "verify-work-email", **build_work_email_verification_claims(member)
    )
    verify_url = build_public_url("auth.verify_work_email", token=token)
    return send_account_action_email(
        app,
        to_email=member.email_work,
        subject=_("Confirm your university or company email"),
        preview_text=_("Confirm this address for your Joanneum Aeronautics membership."),
        action_url=verify_url,
        action_label=_("Confirm Address"),
        heading=_("Confirm your university or company address"),
        body_lines=[
            _("Please confirm this address for your Joanneum Aeronautics membership."),
            _("We use it to confirm that you currently study or work here. Your "
              "private address stays your login and is how we reach you later."),
        ],
        failure_event_type="work_verification_email_failed",
        failure_summary=_("A university email confirmation could not be sent."),
        failure_payload={"email_type": "work_verification"},
        target_user=member.user,
    )


def send_password_reset_email(app, user):
    token = build_password_reset_token(user)
    reset_url = build_public_url("auth.reset_password", token=token)
    return send_account_action_email(
        app,
        to_email=user.email,
        subject=_("Reset your Joanneum Aeronautics password"),
        preview_text=_("Use this link to choose a new password for your account."),
        action_url=reset_url,
        action_label=_("Reset Password"),
        heading=_("Reset your password"),
        body_lines=[
            _("A password reset was requested for your Joanneum Aeronautics account."),
            _("If this was you, use the link below to set a new password. If not, you can ignore this email."),
        ],
        failure_event_type="password_reset_email_failed",
        failure_summary=_("A password reset email could not be sent."),
        failure_payload={"email_type": "password_reset"},
        target_user=user,
    )
