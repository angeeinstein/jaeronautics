"""Auth blueprint.

Route handlers moved verbatim out of app.py (dedented; @app.route ->
@auth_bp.route; app.logger -> current_app.logger). Helpers are imported
from the app module, which is fully initialized before this is imported.
"""

from flask import Blueprint, current_app

from ..config import (
    RATELIMIT_LOGIN,
    RATELIMIT_PASSWORD_CHANGE,
    RATELIMIT_REGISTER,
)
from ..services.forum_import import claim_archived_account
from ..services.forum import (
    log_out_forum_session_if_possible,
)
from ..services.identity import (
    TOKEN_MAX_AGE_PASSWORD_RESET,
    TOKEN_MAX_AGE_VERIFY_EMAIL,
    email_verification_claims_match,
    mark_email_verified_from_token,
    mark_work_email_verified_from_token,
    work_email_verification_claims_match,
    read_token,
    rotate_password_reset_nonce,
    send_password_reset_email,
)
from flask import (
    flash,
    redirect,
    render_template,
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
    User,
    db,
)
from ..forms import (
    ChangePasswordForm,
    EmailRequestForm,
    LoginForm,
    SetPasswordForm,
)
from ..app import (
    is_safe_next_url,
    get_member_portal_target,
    limiter,
    urlsplit,
)

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit(RATELIMIT_REGISTER, methods=["POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for(get_member_portal_target(current_user)))

    form = EmailRequestForm()
    if form.validate_on_submit():
        email_address = form.email.data.strip().lower()
        user = db.session.execute(db.select(User).filter_by(email=email_address)).scalar_one_or_none()
        if user is not None:
            try:
                rotate_password_reset_nonce(user)
                db.session.commit()
                send_password_reset_email(current_app._get_current_object(), user)
            except Exception as exc:
                db.session.rollback()
                current_app.logger.warning("Could not send password reset email for user_id=%s: %s", user.id, exc)
        flash(_("If an account with that email address exists and email sending is configured, a password reset link is available."), "info")
        return redirect(url_for("auth.login"))
    return render_template(
        "account/email_request.html",
        form=form,
        title=_("Reset Password"),
        heading=_("Reset your password"),
        description=_("Enter the email address of your Joanneum Aeronautics account and we will send you a reset link."),
    )


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit(RATELIMIT_PASSWORD_CHANGE, methods=["POST"])
def reset_password(token):
    try:
        token_data = read_token(token, "reset-password", TOKEN_MAX_AGE_PASSWORD_RESET)
        user = db.session.get(User, int(token_data.get("user_id")))
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        token_data = None
        user = None

    token_nonce = (token_data or {}).get("nonce") if token_data else None
    if user is None or not token_nonce or token_nonce != user.password_reset_nonce:
        flash(_("This password reset link is invalid or has expired."), "danger")
        return redirect(url_for("auth.forgot_password"))

    form = SetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()
        flash(_("Your password has been updated. You can log in now."), "success")
        return redirect(url_for("auth.login"))
    return render_template(
        "account/set_password.html",
        form=form,
        title=_("Choose a New Password"),
        heading=_("Choose a new password"),
        description=_("Set a new password for your Joanneum Aeronautics account."),
    )


@auth_bp.route("/verify-email/<token>")
def verify_email(token):
    try:
        token_data = read_token(token, "verify-email", TOKEN_MAX_AGE_VERIFY_EMAIL)
        user = db.session.get(User, int(token_data.get("user_id")))
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

    flash(_("Your email address has been verified."), "success")
    if claimed is not None:
        flash(
            _("Welcome back — your old forum account %(username)s has been "
              "reconnected, so your posts and profile picture are yours again.",
              username=claimed.source_username),
            "success",
        )
    if current_user.is_authenticated and current_user.id == user.id:
        return redirect(url_for(get_member_portal_target(current_user)))
    return redirect(url_for("auth.login"))


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

    flash(_("Your university or company email address has been confirmed."), "success")
    if claimed is not None:
        flash(
            _("Welcome back \u2014 your old forum account %(username)s has been "
              "reconnected, so your posts and profile picture are yours again.",
              username=claimed.source_username),
            "success",
        )
    if current_user.is_authenticated and member.user_id == current_user.id:
        return redirect(url_for(get_member_portal_target(current_user)))
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["POST", "GET"])
@limiter.limit(RATELIMIT_LOGIN, methods=["POST"])
def login():
    next_url = request.values.get("next") or session.get("login_next")
    safe_next_url = next_url if is_safe_next_url(next_url) else None
    login_source = (request.values.get("forum_login_source") or session.get("login_source") or "").strip().lower()
    if request.method == "GET":
        session.pop("login_next", None)
        session.pop("login_source", None)
        if safe_next_url:
            session["login_next"] = safe_next_url
        if login_source:
            session["login_source"] = login_source
    else:
        if safe_next_url:
            session["login_next"] = safe_next_url
        if login_source:
            session["login_source"] = login_source

    if current_user.is_authenticated:
        destination = session.pop("login_next", None)
        session.pop("login_source", None)
        destination = destination if is_safe_next_url(destination) else None
        return redirect(destination or url_for(get_member_portal_target(current_user)))
    form = LoginForm()
    next_parts = urlsplit(safe_next_url) if safe_next_url else None
    forum_login_hint = bool(
        next_parts
        and next_parts.path.startswith("/forum")
        and login_source != "welcome_email"
    )
    if form.validate_on_submit():
        user = db.session.execute(db.select(User).filter_by(email=form.email.data.strip().lower())).scalar_one_or_none()
        if user and user.is_disabled and user.check_password(form.password.data):
            # Told apart from a wrong password on purpose. The credentials were
            # right, so "invalid email or password" would send somebody into
            # password resets that cannot help them; this is a thing to ask an
            # admin about.
            flash(
                _("This account has been deactivated. Please contact the "
                  "association if you think this is a mistake."),
                "warning",
            )
            return redirect(url_for("auth.login"))
        if user and user.check_password(form.password.data):
            login_user(user)
            _reconnect_on_sign_in(user)
            destination = session.pop("login_next", None)
            session.pop("login_source", None)
            destination = destination if is_safe_next_url(destination) else None
            return redirect(destination or url_for(get_member_portal_target(user)))
        flash(_("Invalid email or password"), "danger")
    return render_template(
        "account/login.html",
        form=form,
        next_url=session.get("login_next"),
        forum_login_hint=forum_login_hint,
        forum_login_source=session.get("login_source"),
    )


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
    """
    try:
        profile = claim_archived_account(user)
        if profile is not None:
            db.session.commit()
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
            "Reconnection attempt at sign-in failed for user_id=%s: %s", user.id, exc
        )


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


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
@limiter.limit(RATELIMIT_PASSWORD_CHANGE, methods=["POST"])
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if current_user.check_password(form.current_password.data):
            current_user.set_password(form.new_password.data)
            db.session.commit()
            flash(_("Your password has been updated!"), "success")
            return redirect(url_for(get_member_portal_target(current_user)))
        flash(_("Invalid current password"), "danger")
    return render_template("change_password.html", form=form)
