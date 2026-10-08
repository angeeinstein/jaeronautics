"""Signing in and out, and choosing a new password from the emailed link.

The pages are the app's (frontend/src/pages/public/); these endpoints are
what they send. Signing in answers where to go next: the page that asked for
it (``next``, checked to stay on this site) -- such as the forum's sign-in --
or the person's start page. The rate limits are the old form's: per network,
and per network and address typed. Messages for the page after a redirect go
through Flask's flash (GET /api/v1/messages).
"""

from flask import current_app, flash, session, url_for
from flask_login import current_user, login_user, logout_user
from itsdangerous import BadSignature, SignatureExpired
from pydantic import Field

from ..app import (
    get_member_portal_target,
    is_safe_next_url,
    limiter,
    rate_limit_network,
    rate_limit_network_and_address,
    rate_limit_network_and_path,
)
from ..blueprints._email_cooldown import remember_sent, sent_just_now
from ..blueprints.auth import _reconnect_on_sign_in
from ..config import (
    RATELIMIT_LOGIN,
    RATELIMIT_LOGIN_PER_IP,
    RATELIMIT_PASSWORD_CHANGE,
    RATELIMIT_REGISTER,
    RATELIMIT_REGISTER_PER_IP,
)
from ..db_models import User, db
from ..services import PermissionError_, ValidationError
from ..services.forum import log_out_forum_session_if_possible
from ..services.identity import (
    TOKEN_MAX_AGE_PASSWORD_RESET,
    read_token,
    rotate_password_reset_nonce,
    send_password_reset_email,
    user_for_login_address,
)
from ._core import Model, endpoint

TAG = "Session"


class SignInIn(Model):
    #: The private address, or a confirmed university one.
    email: str = Field(max_length=255)
    password: str = Field(max_length=128)
    #: Where to go afterwards, when somewhere asked for the sign-in.
    next: str | None = Field(None, max_length=2000)


class GoOnOut(Model):
    #: Where the browser goes next.
    go_to: str


def _destination(user, wanted):
    return wanted if wanted and is_safe_next_url(wanted) else url_for(get_member_portal_target(user))


@endpoint("POST", "/session", response=GoOnOut, body=SignInIn, public=True, tag=TAG)
@limiter.limit(RATELIMIT_LOGIN_PER_IP, key_func=rate_limit_network)
@limiter.limit(RATELIMIT_LOGIN, key_func=rate_limit_network_and_address)
def sign_in(body):
    """Sign in; the answer says where to go next."""
    if current_user.is_authenticated:
        return GoOnOut(go_to=_destination(current_user, body.next))
    user = user_for_login_address(body.email)
    if user is not None and user.check_password(body.password):
        if user.is_disabled:
            # Told apart from a wrong password on purpose: the credentials were
            # right, so "invalid" would send somebody into password resets that
            # cannot help them.
            raise PermissionError_("This account has been deactivated. Please contact the association if you "
                                   "think this is a mistake.", code="account_disabled")
        login_user(user)
        # A returning student missed at verification is reconnected now; the
        # next page says so (flashed).
        user = _reconnect_on_sign_in(user)
        return GoOnOut(go_to=_destination(user, body.next))
    raise ValidationError("Invalid email or password.", code="invalid_credentials")


@endpoint("DELETE", "/session", response=GoOnOut, tag=TAG)
def sign_out():
    """Sign out here, and on the forum where that can be done. The start page says how it went."""
    user = current_user._get_current_object()
    attempted, problem = log_out_forum_session_if_possible(user)
    logout_user()
    session.pop("login_next", None)
    session.pop("login_source", None)
    if problem:
        flash("You have been signed out here, but the forum session could not be ended by itself.", "warning")
    elif attempted:
        flash("You have been signed out of the website and the forum.", "info")
    else:
        flash("You have been signed out.", "info")
    return GoOnOut(go_to=url_for("public.index"))


# --- A new password, by email ------------------------------------------------------------


class ResetAskIn(Model):
    email: str = Field(max_length=255)


class SaidOut(Model):
    text: str


@endpoint("POST", "/password-reset", response=SaidOut, body=ResetAskIn, public=True, tag=TAG)
@limiter.limit(RATELIMIT_REGISTER_PER_IP, key_func=rate_limit_network)
@limiter.limit(RATELIMIT_REGISTER, key_func=rate_limit_network_and_address)
def password_reset_ask(body):
    """Email a link to choose a new password. The answer is the same whether or not the account exists."""
    # A university address finds the account too; the link goes to the address
    # the account belongs to (identity.send_password_reset_email decides).
    user = user_for_login_address(body.email)
    address = user.email if user is not None else None
    # Asked again within the minute: nothing is sent. Every request makes a new
    # link and kills the last, so a double click left the email opened first
    # saying "invalid".
    if user is not None and not sent_just_now("password-reset", address):
        try:
            rotate_password_reset_nonce(user)
            db.session.commit()
            send_password_reset_email(current_app._get_current_object(), user, requested_with=body.email)
            remember_sent("password-reset", address)
        except Exception as exc:  # noqa: BLE001 -- the answer must not tell
            db.session.rollback()
            current_app.logger.warning("Could not send password reset email for user_id=%s: %s", user.id, exc)
    return SaidOut(text="If we found your account, we have emailed you a link to choose a new password. "
                        "Not in your inbox? Please check your spam folder.")


def _reset_user(token):
    try:
        data = read_token(token, "reset-password", TOKEN_MAX_AGE_PASSWORD_RESET)
        user = db.session.get(User, int(data.get("user_id")))
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        data, user = None, None
    nonce = (data or {}).get("nonce")
    if user is None or not nonce or nonce != user.password_reset_nonce:
        raise ValidationError("This link is invalid or has expired. Please ask for a new one.", code="link_invalid")
    return user


class LinkOut(Model):
    valid: bool


@endpoint("GET", "/password-reset/<token>", response=LinkOut, public=True, tag=TAG)
def password_reset_link(token):
    """Whether the link still works, before a new password is typed."""
    _reset_user(token)
    return LinkOut(valid=True)


class ResetIn(Model):
    password: str = Field(max_length=128)


@endpoint("PUT", "/password-reset/<token>", response=SaidOut, body=ResetIn, public=True, tag=TAG)
@limiter.limit(RATELIMIT_PASSWORD_CHANGE, key_func=rate_limit_network_and_path)
def password_reset(token, body):
    """Choose the new password. The link works once."""
    user = _reset_user(token)
    if len(body.password) < 8:
        raise ValidationError("Please check the form.", details={"fields": {"password": "At least 8 characters, please."}})
    user.set_password(body.password)
    db.session.commit()
    return SaidOut(text="Your password has been changed. You can sign in now.")
