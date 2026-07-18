"""Auth blueprint.

Route handlers moved verbatim out of app.py (dedented; @app.route ->
@auth_bp.route; app.logger -> current_app.logger). Helpers are imported
from the app module, which is fully initialized before this is imported.
"""

from flask import Blueprint, current_app

from ..app import (
    BadSignature,
    ChangePasswordForm,
    EmailRequestForm,
    LoginForm,
    RATELIMIT_LOGIN,
    RATELIMIT_PASSWORD_CHANGE,
    RATELIMIT_REGISTER,
    SetPasswordForm,
    SignatureExpired,
    TOKEN_MAX_AGE_PASSWORD_RESET,
    TOKEN_MAX_AGE_VERIFY_EMAIL,
    User,
    _,
    current_user,
    db,
    flash,
    get_member_portal_target,
    get_now_utc,
    is_safe_next_url,
    limiter,
    log_out_forum_session_if_possible,
    login_required,
    login_user,
    logout_user,
    read_token,
    redirect,
    render_template,
    request,
    rotate_password_reset_nonce,
    send_password_reset_email,
    session,
    url_for,
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
        user = None

    if user is None:
        flash(_("This verification link is invalid or has expired."), "danger")
        return redirect(url_for("auth.login"))

    if not user.email_is_verified:
        user.email_verified_at = get_now_utc()
        db.session.commit()
    flash(_("Your email address has been verified."), "success")
    if current_user.is_authenticated and current_user.id == user.id:
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
        if user and user.check_password(form.password.data):
            login_user(user)
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
