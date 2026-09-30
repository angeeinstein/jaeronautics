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
from sqlalchemy import func

from ..config import SECRET_KEY
from ..db_models import Member, User, db
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


def user_for_email_token(token_data):
    """The account a verification or forum link was issued to, or None.

    Normally the one its ``user_id`` names. A returning student's first account
    is gone once they reconnect their old forum account, though: the claim moves
    everything onto the archived row and deletes the one the links were issued
    for. Every link sent before that -- the first verification mail, the forum
    link in the welcome mail -- would then say "invalid or has expired" to
    somebody who did nothing wrong.

    The claim carries the address and the verification nonce across, so a link
    still proves the same thing about the account that now holds them. Followed
    only when both match: the nonce is the secret part, and a bare address
    must never be enough to land in somebody's account.
    """
    if not isinstance(token_data, dict):
        return None
    user = db.session.get(User, int(token_data.get("user_id")))
    if user is not None:
        return user
    address = (token_data.get("email") or "").strip().lower()
    if not address or not token_data.get("nonce"):
        return None
    successor = db.session.execute(
        db.select(User).where(func.lower(User.email) == address)
    ).scalar_one_or_none()
    if successor is None or not email_verification_claims_match(token_data, successor):
        return None
    return successor


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
        subject=_("Confirm your email address"),
        preview_text=_("One click to confirm the address of your Joanneum Aeronautics account."),
        action_url=verify_url,
        action_label=_("Confirm Email Address"),
        heading=_("Confirm your email address"),
        body_lines=[
            _("Please confirm that this is your email address. It is the address you "
              "sign in with, and the one we use to reach you."),
        ],
        note=_("The link is valid for 7 days. If you did not sign up with Joanneum "
               "Aeronautics, you can ignore this email."),
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
    _unconfirm_elsewhere(member)
    return True


def _unconfirm_elsewhere(member):
    """The latest confirmation of a university address wins.

    A confirmed university address signs in, so it must lead to one account.
    The university reissues addresses, and a student may set up a second
    account; whoever confirmed the address last is the one reading that
    mailbox now. The other account keeps its private address to sign in with.
    """
    address = (member.email_work or "").strip().lower()
    others = db.session.execute(
        db.select(Member).where(
            func.lower(Member.email_work) == address,
            Member.id != member.id,
            Member.email_work_verified_at.is_not(None),
        )
    ).scalars()
    for other in others:
        other.email_work_verified_at = None


def user_for_login_address(address):
    """The account an address signs in to, or None.

    The private address, as always, or a confirmed university address --
    which is what most people type. Unconfirmed, it proves nothing and signs
    in nowhere. Confirmed on more than one account (from before the latest
    confirmation won) it is ambiguous and signs in nowhere either; the
    private address still works.
    """
    address = (address or "").strip().lower()
    if not address:
        return None
    user = db.session.execute(db.select(User).filter_by(email=address)).scalar_one_or_none()
    if user is not None:
        return user
    holders = db.session.execute(
        db.select(Member).where(
            func.lower(Member.email_work) == address,
            Member.email_work_verified_at.is_not(None),
            Member.deleted_at.is_(None),
        )
    ).scalars().all()
    if len(holders) != 1:
        return None
    return holders[0].user


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
        subject=_("Confirm your university or company email address"),
        preview_text=_("Confirm this address for your Joanneum Aeronautics membership."),
        action_url=verify_url,
        action_label=_("Confirm Address"),
        heading=_("Confirm your university or company address"),
        body_lines=[
            _("Please confirm this address for your Joanneum Aeronautics membership. "
              "It shows that you study or work here."),
            _("If you were on the old forum with this address, confirming it also "
              "gives you your old forum account and your posts back."),
            _("You keep signing in with your private address, and that is where we "
              "write to you."),
        ],
        note=_("The link is valid for 7 days."),
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
            _("Somebody asked to reset the password of your Joanneum Aeronautics account. "
              "If it was you, choose a new password with the button below."),
        ],
        note=_("The link is valid for 24 hours and works once. If you did not ask for "
               "this, you can ignore this email: your password stays as it is."),
        failure_event_type="password_reset_email_failed",
        failure_summary=_("A password reset email could not be sent."),
        failure_payload={"email_type": "password_reset"},
        target_user=user,
    )
