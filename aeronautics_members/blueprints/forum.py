"""Forum blueprint.

Route handlers moved verbatim out of app.py (dedented; @app.route ->
@forum_bp.route; app.logger -> current_app.logger). Helpers are imported
from the app module, which is fully initialized before this is imported.
"""

from flask import Blueprint, current_app

from ..app import (
    ADMIN_GENERAL_CHANNEL,
    BadSignature,
    ForumAvatarSubmission,
    ForumProviderError,
    Path,
    SignatureExpired,
    TOKEN_MAX_AGE_FORUM_ENTRY,
    TOKEN_MAX_AGE_FORUM_ENTRY_AUTO_LOGIN,
    User,
    _,
    abort,
    build_forum_context,
    current_user,
    db,
    flash,
    flush_marked_notification_channels,
    format_bytes_human,
    get_current_member_for_user,
    get_forum_service,
    get_member_portal_target,
    get_now_utc,
    log_audit_event,
    log_out_forum_session_if_possible,
    login_required,
    login_user,
    logout_user,
    member_has_active_access,
    queue_curated_admin_notification,
    read_token,
    redirect,
    render_template,
    request,
    send_file,
    session,
    snapshot_forum_avatar_submission_for_audit,
    sync_member_forum_state,
    url_for,
)

forum_bp = Blueprint("forum", __name__)


@forum_bp.route("/forum", methods=["GET"])
def forum_entry():
    token = (request.args.get("token") or "").strip()
    token_user = None
    token_verified_email = False
    if token:
        try:
            token_data = read_token(token, "forum-entry", TOKEN_MAX_AGE_FORUM_ENTRY)
            token_user = db.session.get(User, int(token_data.get("user_id")))
        except (BadSignature, SignatureExpired, ValueError, TypeError):
            token_user = None
            flash(_("This forum access link is invalid or has expired."), "warning")

        if token_user is not None and not token_user.email_is_verified:
            token_user.email_verified_at = get_now_utc()
            db.session.commit()
            token_verified_email = True

        if token_user is not None and current_user.is_authenticated and current_user.id != token_user.id:
            flash(
                _("This forum link belongs to a different account. Please log out and sign in with the account that received the email."),
                "warning",
            )
            return redirect(url_for(get_member_portal_target(current_user)))

        if token_user is not None and not current_user.is_authenticated:
            issued_at_raw = token_data.get("issued_at") if isinstance(token_data, dict) else None
            auto_login_allowed = False
            try:
                issued_at = int(issued_at_raw) if issued_at_raw is not None else None
                if issued_at is not None:
                    age_seconds = int(get_now_utc().timestamp()) - issued_at
                    auto_login_allowed = 0 <= age_seconds <= TOKEN_MAX_AGE_FORUM_ENTRY_AUTO_LOGIN
            except (TypeError, ValueError):
                auto_login_allowed = False

            if auto_login_allowed:
                login_user(token_user)
            else:
                flash(
                    _("Your email address has been verified. Please log in to continue to the forum.") if token_verified_email else _("Please log in to continue to the forum."),
                    "success" if token_verified_email else "warning",
                )
                return redirect(url_for("auth.login", next=url_for("forum.forum_entry"), forum_login_source="welcome_email"))

        if token_user is not None and token_verified_email:
            flash(_("Your email address has been verified."), "success")

    if not current_user.is_authenticated:
        flash(_("Please log in to continue to the forum."), "warning")
        return redirect(url_for("auth.login", next=url_for("forum.forum_entry")))

    member = get_current_member_for_user(current_user)
    if member is None:
        flash(_("A linked membership profile is required before you can access the forum."), "warning")
        return redirect(url_for("account.create_membership_profile"))

    forum_result, service = sync_member_forum_state(member)
    if forum_result and forum_result.changed:
        db.session.commit()

    forum_context = build_forum_context(member)
    if forum_context["can_enter_forum"]:
        try:
            return redirect(
                service.build_forum_redirect(destination_path=service.settings.get("forum_onboarding_path")),
                code=303,
            )
        except ForumProviderError as exc:
            current_app.logger.warning("Could not hand off to Discourse for member_id=%s: %s", member.id, exc)
            flash(_("The forum could not be opened right now. Please try again later."), "danger")

    return render_template("account/forum.html", member=member, forum_context=forum_context)


@forum_bp.route("/forum/avatar", methods=["POST"])
@login_required
def upload_forum_avatar():
    member = get_current_member_for_user(current_user)
    if member is None:
        flash(_("A linked membership profile is required before you can upload a forum profile picture."), "warning")
        return redirect(url_for("account.create_membership_profile"))

    forum_service = get_forum_service()
    if not forum_service.is_enabled():
        flash(_("The forum integration is not enabled yet."), "warning")
        return redirect(url_for("account.account"))

    if not member_has_active_access(member):
        flash(_("Your membership must be active before you can upload a forum profile picture."), "warning")
        return redirect(url_for("account.account"))

    upload_request_limit = forum_service.get_upload_request_limit()
    if request.content_length and request.content_length > upload_request_limit:
        flash(
            _("The selected image is too large to upload. Please keep it below %(size)s.", size=format_bytes_human(upload_request_limit)),
            "danger",
        )
        return redirect(url_for("forum.forum_entry"))

    upload = request.files.get("avatar")
    crop_options = {
        "crop_mode": request.form.get("crop_mode"),
        "crop_zoom": request.form.get("crop_zoom"),
        "crop_center_x": request.form.get("crop_center_x"),
        "crop_center_y": request.form.get("crop_center_y"),
    }
    try:
        submission = forum_service.create_avatar_submission(upload, current_user, member, crop_options=crop_options)
        forum_result = forum_service.sync_member(member)
        log_audit_event(
            category="forum",
            event_type="avatar_uploaded",
            actor_user=current_user,
            target_user=current_user,
            target_member=member,
            before=None,
            after=snapshot_forum_avatar_submission_for_audit(submission),
            metadata={"forum_state": forum_result.desired_state if forum_result else None},
        )
        queue_curated_admin_notification(
            ADMIN_GENERAL_CHANNEL,
            "forum_avatar_uploaded",
            _("A new forum avatar approval request was submitted by %(email)s.", email=member.email_private),
            payload={
                "member_email": member.email_private,
                "forum_username": current_user.forum_username,
                "submission_id": submission.id,
            },
            target_user=current_user,
            target_member=member,
            object_type="forum_avatar_submission",
            object_id=submission.id,
        )
        db.session.commit()
        flush_marked_notification_channels()
        flash(_("Your profile picture was uploaded and is now waiting for admin approval."), "success")
    except ForumProviderError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("forum.forum_entry"))


@forum_bp.route("/forum/avatar/public/<token>", methods=["GET"])
def forum_avatar_public_file(token):
    submission = db.session.execute(
        db.select(ForumAvatarSubmission).where(ForumAvatarSubmission.public_token == token)
    ).scalar_one_or_none()
    if submission is None or not submission.storage_path:
        abort(404)

    storage_path = Path(submission.storage_path)
    if not storage_path.exists():
        abort(404)

    return send_file(storage_path, mimetype=submission.content_type or "application/octet-stream", conditional=True)


@forum_bp.route("/forum/discourse/connect", methods=["GET"])
def forum_discourse_connect():
    if not current_user.is_authenticated:
        next_url = request.full_path[:-1] if request.full_path.endswith("?") else request.full_path
        return redirect(url_for("auth.login", next=next_url))

    member = get_current_member_for_user(current_user)
    if member is None:
        flash(_("A linked membership profile is required before you can access the forum."), "warning")
        return redirect(url_for("account.create_membership_profile"))

    if not member_has_active_access(member):
        flash(_("Your membership is not active, so forum access is unavailable right now."), "warning")
        return redirect(url_for("forum.forum_entry"))

    forum_result, service = sync_member_forum_state(member)
    if forum_result and forum_result.changed:
        db.session.commit()

    try:
        redirect_url = service.handle_provider_request(request.args, current_user, member)
        return redirect(redirect_url, code=303)
    except ForumProviderError as exc:
        current_app.logger.warning("DiscourseConnect handoff failed for member_id=%s: %s", member.id, exc)
        flash(_("The forum sign-in could not be completed right now."), "danger")
        return redirect(url_for("forum.forum_entry"))


@forum_bp.route("/forum/logout", methods=["GET"])
def forum_logout():
    forum_logout_error = None
    forum_logout_attempted = False
    if current_user.is_authenticated:
        user = current_user._get_current_object()
        forum_logout_attempted, forum_logout_error = log_out_forum_session_if_possible(user)
        logout_user()
        session.pop("login_next", None)
        session.pop("login_source", None)
        if forum_logout_error:
            flash(_("You have been logged out here, but the forum session could not be ended automatically."), "warning")
        elif forum_logout_attempted:
            flash(_("You have been logged out from the forum and this website. Sign in again if you want to continue with a different account."), "info")
        else:
            flash(_("You have been logged out. Sign in again if you want to continue."), "info")
    else:
        flash(_("You have been logged out from the forum. Sign in again if you want to continue."), "info")
    return redirect(url_for("auth.login", next=url_for("forum.forum_entry")))
