"""Auth blueprint.

Route handlers moved verbatim out of app.py (dedented; @app.route ->
@auth_bp.route; app.logger -> current_app.logger). Helpers are imported
from the app module, which is fully initialized before this is imported.
"""

from flask import Blueprint, current_app

from ..services.forum_import import claim_archived_account
from ..services.forum import (
    log_out_forum_session_if_possible,
    sync_member_forum_state,
)
from ..services.identity import (
    TOKEN_MAX_AGE_VERIFY_EMAIL,
    email_verification_claims_match,
    mark_email_verified_from_token,
    mark_work_email_verified_from_token,
    work_email_verification_claims_match,
    read_token,
    user_for_email_token,
)
from flask import (
    flash,
    redirect,
    request,
    session,
    url_for,
)
from flask_babel import (
    _,
)
from flask_login import (
    current_user,
    login_required,
    login_user,
    logout_user,
)
from itsdangerous import (
    BadSignature,
    SignatureExpired,
)
from ..db_models import (
    db,
)
from ..app import (
    is_safe_next_url,
    get_member_portal_target,
)
from .app_shell import app_shell

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/forgot-password", methods=["GET"])
def forgot_password():
    """Asking for a new password: the app's page (POST /api/v1/password-reset)."""
    if current_user.is_authenticated:
        return redirect(url_for(get_member_portal_target(current_user)))
    return app_shell()


@auth_bp.route("/reset-password/<token>", methods=["GET"])
def reset_password(token):
    """The link in the reset email: the app's page checks it and takes the new
    password (GET|PUT /api/v1/password-reset/<token>)."""
    return app_shell()


@auth_bp.route("/verify-email/<token>")
def verify_email(token):
    try:
        token_data = read_token(token, "verify-email", TOKEN_MAX_AGE_VERIFY_EMAIL)
        user = user_for_email_token(token_data)
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        token_data = None
        user = None

    # The link must still prove ownership of the address currently on the
    # account: a token issued for a previous address must not verify a new one.
    if user is not None and not email_verification_claims_match(token_data, user):
        user = None

    if user is None:
        flash(_("This verification link is invalid or has expired."), "danger")
        return redirect(url_for("auth.login"))

    # Decided before the claim, which deletes this row.
    signed_in_here = current_user.is_authenticated and current_user.id == user.id
    claimed = None
    if mark_email_verified_from_token(token_data, user):
        # A returning student gets their old forum identity back here, because
        # here is where they have just proved they can read the address the
        # archived account was registered under.
        #
        # Wrapped, and deliberately after the verification is decided: a fault
        # in the claim must not cost somebody a verified address. They end up
        # with a working new account and an unclaimed archive, which an admin
        # can link -- not with a link they cannot use.
        try:
            # A savepoint, not a plain rollback: the verification was recorded
            # a line ago and is not committed yet, so undoing the whole session
            # would throw it away along with the half-done claim.
            with db.session.begin_nested():
                claimed = claim_archived_account(user)
        except Exception:  # noqa: BLE001 -- never block a verification
            claimed = None
            current_app.logger.exception("Forum account claim failed for user %s", user.id)
        db.session.commit()
        _stay_signed_in(claimed, signed_in_here)
        if claimed is None:
            _tell_the_forum_the_address_is_confirmed(user)
            _point_out_a_likely_old_account(user)

    flash(_("Your email address has been verified."), "success")
    if claimed is not None:
        flash(
            _("Welcome back — your old forum account %(username)s has been "
              "reconnected, so your posts and profile picture are yours again.",
              username=claimed.source_username),
            "success",
        )
    if signed_in_here:
        return redirect(url_for(get_member_portal_target(current_user)))
    return redirect(url_for("auth.login"))


def _point_out_a_likely_old_account(user):
    """No old forum account matched the confirmed address exactly; if one
    nearly does -- the old forum never checked what was typed -- the admins
    are told, to reconnect it by hand. Never fails the confirmation."""
    from ..services.forum_import import report_likely_old_accounts
    from ..services.notifications import flush_marked_notification_channels

    if report_likely_old_accounts(user):
        db.session.commit()
        flush_marked_notification_channels()


@auth_bp.route("/verify-work-email/<token>")
def verify_work_email(token):
    """Confirms the university or company address on a membership.

    This is the link that matters for a returning student: the archived forum
    account was registered under their university address, so confirming they
    can read it is what reconnects the two.
    """
    from ..db_models import Member

    try:
        token_data = read_token(token, "verify-work-email", TOKEN_MAX_AGE_VERIFY_EMAIL)
        member = db.session.get(Member, int(token_data.get("member_id")))
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        token_data = None
        member = None

    if member is not None and not work_email_verification_claims_match(token_data, member):
        member = None

    if member is None:
        flash(_("This confirmation link is invalid or has expired."), "danger")
        return redirect(url_for("auth.login"))

    signed_in_here = current_user.is_authenticated and member.user_id == current_user.id
    claimed = None
    if mark_work_email_verified_from_token(token_data, member):
        try:
            # Same savepoint reasoning as the account address: a fault in the
            # claim must not cost somebody the confirmation recorded a line ago.
            with db.session.begin_nested():
                claimed = claim_archived_account(member.user)
        except Exception:  # noqa: BLE001 -- never block a confirmation
            claimed = None
            current_app.logger.exception(
                "Forum account claim failed for member %s", member.id
            )
        db.session.commit()
        _stay_signed_in(claimed, signed_in_here)
        if claimed is None:
            _point_out_a_likely_old_account(member.user)

    flash(_("Your university or company email address has been confirmed."), "success")
    if claimed is not None:
        flash(
            _("Welcome back \u2014 your old forum account %(username)s has been "
              "reconnected, so your posts and profile picture are yours again.",
              username=claimed.source_username),
            "success",
        )
    if signed_in_here:
        return redirect(url_for(get_member_portal_target(current_user)))
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET"])
def login():
    """Signing in: the app's page (POST /api/v1/session), which goes on to
    ``next`` -- such as the forum's sign-in -- when it is on this site."""
    if current_user.is_authenticated:
        wanted = request.args.get("next")
        return redirect(wanted if is_safe_next_url(wanted) else url_for(get_member_portal_target(current_user)))
    return app_shell()


def _reconnect_on_sign_in(user):
    """Give somebody their old forum account back if verifying did not.

    The claim normally happens the moment a university address is confirmed.
    That is a single instant, and if anything goes wrong in it -- a slow
    forum, a guard that should not have fired, a link clicked twice -- there is
    no second chance and nothing tells the student anything is outstanding.

    Around 250 people walk that path in one October, so it cannot be a
    one-shot. Signing in is the natural place to try again: the evidence is
    already on file, and somebody who was missed simply reconnects the next
    time they log in rather than writing in to ask why their account is gone.

    Deliberately quiet on failure. A returning student getting into their
    account matters more than the reconnection, so nothing here may keep them
    out.

    Returns the account they are now signed in as, which after a reconnect is
    the archived one: the row they signed in with no longer exists.
    """
    user_id = user.id
    try:
        profile = claim_archived_account(user)
        if profile is not None:
            db.session.commit()
            _stay_signed_in(profile, True)
            user = profile.user
            current_app.logger.info(
                "Reconnected %s at sign-in; the claim had not happened at verification.",
                profile.source_username,
            )
            flash(
                _("Welcome back. Your old forum account %(username)s is yours again.",
                  username=profile.source_username),
                "success",
            )
    except Exception as exc:  # noqa: BLE001 -- never block a sign-in
        db.session.rollback()
        current_app.logger.warning(
            "Reconnection attempt at sign-in failed for user_id=%s: %s", user_id, exc
        )
    return user


def _tell_the_forum_the_address_is_confirmed(user):
    """Reactivate the forum account now that its address is confirmed.

    A changed address reaches the forum at once, unconfirmed, and the forum
    pauses the account until it is. Nothing told it about the confirmation,
    so the account stayed paused -- no notifications -- until the member next
    opened the forum from here.

    Not after a reconnect: that leaves the old forum account holding this
    address until it is cleaned up, and the forum entry does that first.
    A failure is logged and the next sync puts it right; it must never cost
    somebody their confirmation.
    """
    member = user.member
    if member is None or user.forum_account is None:
        return
    try:
        sync_member_forum_state(member)
        db.session.commit()
    except Exception:  # noqa: BLE001 -- never block a verification
        db.session.rollback()
        current_app.logger.exception("Forum sync after email confirmation failed for user %s", user.id)


def _stay_signed_in(claimed, was_signed_in_as_the_retired_row):
    """Carry a session across a reconnect, onto the account that survived it.

    The claim deletes the row somebody signed up with and keeps them on the
    archived one. A session still naming the deleted row is a session for
    nobody: the next page load finds no such user and they are silently
    signed out -- straight after being told "welcome back".
    """
    if claimed is not None and was_signed_in_as_the_retired_row and claimed.user is not None:
        login_user(claimed.user)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    flash(_("Accounts are created automatically when you sign up for a membership."), "info")
    return redirect(url_for("public.index"))


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    user = current_user._get_current_object()
    forum_logout_attempted, forum_logout_error = log_out_forum_session_if_possible(user)
    logout_user()
    session.pop("login_next", None)
    session.pop("login_source", None)

    next_url = request.form.get("next") or url_for("public.index")
    if not is_safe_next_url(next_url):
        next_url = url_for("public.index")

    if forum_logout_error:
        flash(_("You have been logged out here, but the forum session could not be ended automatically."), "warning")
    elif forum_logout_attempted:
        flash(_("You have been logged out from both the website and the forum."), "info")
    else:
        flash(_("You have been logged out."), "info")
    return redirect(next_url)


@auth_bp.route("/change-password", methods=["GET"])
@login_required
def change_password():
    """Changing the password: the app's page (PUT /api/v1/account/password)."""
    return app_shell()
