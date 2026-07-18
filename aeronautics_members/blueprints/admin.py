"""Admin blueprint.

Route handlers moved verbatim out of app.py (dedented; @app.route ->
@admin_bp.route; app.logger -> current_app.logger). Helpers are imported
from the app module, which is fully initialized before this is imported.
"""

from flask import Blueprint, current_app

from ..app import (
    build_account_directory_query,
    build_settings_page_context,
    decorate_pending_identity_requests,
    get_admin_dashboard_metrics,
    get_recent_audit_logs,
    ADMIN_DIRECTORY_PAGE_SIZE,
    ADMIN_ERROR_CHANNEL,
    APPROVAL_HISTORY_PAGE_SIZE,
    AUDIT_LOG_PAGE_SIZE,
    AuditLog,
    FORUM_AVATAR_STATUS_PENDING,
    FORUM_SETTING_KEYS,
    FORUM_STATE_SYNC_ERROR,
    ForumAccount,
    ForumAvatarSubmission,
    ForumProviderError,
    IDENTITY_MEMBER_FIELDS,
    IntegrityError,
    MailAccount,
    MailAccountForm,
    Member,
    MemberProfileChangeRequest,
    NOTIFICATION_SETTING_KEYS,
    RATELIMIT_ADMIN_EMAIL,
    STRIPE_SETTING_KEYS,
    Setting,
    TestEmailForm,
    User,
    _,
    admin_required,
    aliased,
    build_forum_context,
    build_forum_username_base,
    build_mail_accounts_export_payload,
    count_users_with_role,
    current_user,
    datetime,
    db,
    flash,
    generate_unique_forum_username,
    get_forum_service,
    get_now_utc,
    get_role,
    json,
    limiter,
    load_mail_accounts_config,
    log_audit_event,
    login_required,
    member_has_active_access,
    normalize_imported_mail_accounts_payload,
    or_,
    os,
    probe_mail_account_connection,
    queue_curated_admin_notification,
    queue_user_status_notification,
    redact_settings_states_for_audit,
    redirect,
    refresh_member_billing_state,
    render_template,
    request,
    selectinload,
    send_mail,
    set_setting_value,
    snapshot_forum_account_for_audit,
    snapshot_forum_avatar_submission_for_audit,
    snapshot_mail_account_for_audit,
    snapshot_member_for_audit,
    snapshot_user_for_audit,
    stripe,
    sync_member_forum_state,
    timezone,
    url_for,
)

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/admin", methods=["GET"])
@login_required
@admin_required
def admin_dashboard():
    metrics = get_admin_dashboard_metrics()
    pending_request_preview = db.session.execute(
        db.select(MemberProfileChangeRequest)
        .options(selectinload(MemberProfileChangeRequest.member))
        .where(MemberProfileChangeRequest.status == "pending")
        .order_by(MemberProfileChangeRequest.created_at.asc())
        .limit(5)
    ).scalars().all()
    recent_logs = get_recent_audit_logs(limit=10)
    return render_template(
        "admin_dashboard.html",
        active_admin_section="dashboard",
        metrics=metrics,
        pending_request_preview=pending_request_preview,
        recent_logs=recent_logs,
    )


@admin_bp.route("/admin/accounts", methods=["GET"])
@login_required
@admin_required
def admin_accounts():
    search_term = (request.args.get("q") or "").strip()
    role_filter = request.args.get("role", "all")
    membership_filter = request.args.get("membership_status", "all")
    active_filter = request.args.get("active", "all")
    page = request.args.get("page", 1, type=int)

    pagination = db.paginate(
        build_account_directory_query(search_term, role_filter, membership_filter, active_filter),
        page=page,
        per_page=ADMIN_DIRECTORY_PAGE_SIZE,
        error_out=False,
    )
    return render_template(
        "admin_accounts.html",
        active_admin_section="accounts",
        pagination=pagination,
        search_term=search_term,
        role_filter=role_filter,
        membership_filter=membership_filter,
        active_filter=active_filter,
    )


@admin_bp.route("/admin/accounts/<int:user_id>", methods=["GET"])
@login_required
@admin_required
def admin_account_detail(user_id):
    user = db.session.execute(
        db.select(User)
        .options(selectinload(User.member), selectinload(User.roles))
        .filter_by(id=user_id)
    ).scalar_one_or_none()
    if user is None:
        flash(_("The selected account could not be found."), "warning")
        return redirect(url_for("admin.admin_accounts"))

    conditions = [AuditLog.target_user_id == user.id, AuditLog.actor_user_id == user.id]
    if user.member is not None:
        conditions.append(AuditLog.target_member_id == user.member.id)

    recent_logs = db.session.execute(
        db.select(AuditLog)
        .options(
            selectinload(AuditLog.actor_user),
            selectinload(AuditLog.target_user),
            selectinload(AuditLog.target_member),
        )
        .where(or_(*conditions))
        .order_by(AuditLog.created_at.desc())
        .limit(15)
    ).scalars().all()

    return render_template(
        "admin_account_detail.html",
        active_admin_section="accounts",
        user_record=user,
        member=user.member,
        forum_account=user.forum_account,
        forum_context=build_forum_context(user.member),
        latest_forum_submission=get_forum_service().get_latest_submission(user.member) if user.member else None,
        recent_logs=recent_logs,
        can_grant_admin=not user.has_role("admin"),
        can_revoke_admin=user.has_role("admin") and current_user.id != user.id and count_users_with_role("admin") > 1,
        admin_count=count_users_with_role("admin"),
    )


@admin_bp.route("/admin/accounts/<int:user_id>/billing-sync", methods=["POST"])
@login_required
@admin_required
def admin_sync_billing_account(user_id):
    user = db.session.execute(
        db.select(User)
        .options(selectinload(User.member), selectinload(User.forum_account))
        .filter_by(id=user_id)
    ).scalar_one_or_none()
    if user is None:
        flash(_("The selected account could not be found."), "warning")
        return redirect(url_for("admin.admin_accounts"))

    next_url = request.form.get("next") or url_for("admin.admin_account_detail", user_id=user.id)
    if user.member is None:
        flash(_("This account does not have a linked membership profile yet."), "warning")
        return redirect(next_url)
    if not (user.member.stripe_customer_id or user.member.stripe_subscription_id):
        flash(_("No Stripe billing reference is stored for this membership yet."), "warning")
        return redirect(next_url)

    before_member = snapshot_member_for_audit(user.member)
    try:
        changed, stripe_subscription, forum_result = refresh_member_billing_state(user.member, force_stripe_sync=True, sync_forum=True)
    except stripe.StripeError as exc:
        db.session.rollback()
        current_app.logger.error("Manual Stripe billing sync failed for member_id=%s: %s", user.member.id, exc)
        queue_curated_admin_notification(
            ADMIN_ERROR_CHANNEL,
            "manual_billing_sync_failed",
            _("A manual Stripe billing sync failed for %(email)s.", email=user.member.email_private),
            payload={
                "member_email": user.member.email_private,
                "user_id": user.id,
                "member_id": user.member.id,
                "error": str(exc),
            },
            target_user=user,
            target_member=user.member,
            object_type="member",
            object_id=user.member.id,
            commit=True,
        )
        flash(_("Stripe billing sync failed right now. Please try again later."), "danger")
        return redirect(next_url)

    log_audit_event(
        category="billing",
        event_type="manual_billing_sync",
        actor_user=current_user,
        target_user=user,
        target_member=user.member,
        before=before_member,
        after=snapshot_member_for_audit(user.member),
        metadata={
            "changed": changed,
            "stripe_status": stripe_subscription.get("status") if stripe_subscription else None,
            "stripe_cancel_at_period_end": stripe_subscription.get("cancel_at_period_end") if stripe_subscription else None,
            "stripe_cancel_at": stripe_subscription.get("cancel_at") if stripe_subscription else None,
            "forum_sync_error": forum_result.error if forum_result else None,
        },
    )
    db.session.commit()

    if changed:
        flash(_("Billing state synchronized successfully."), "success")
    else:
        flash(_("Billing already matches the current Stripe state."), "info")
    if forum_result and forum_result.error:
        flash(_("Forum sync completed with an issue: %(message)s", message=forum_result.error), "warning")
    return redirect(next_url)


@admin_bp.route("/admin/forum", methods=["GET"])
@login_required
@admin_required
def admin_forum():
    page = request.args.get("page", 1, type=int)
    pending_avatar_pagination = db.paginate(
        db.select(ForumAvatarSubmission)
        .options(
            selectinload(ForumAvatarSubmission.user),
            selectinload(ForumAvatarSubmission.member),
            selectinload(ForumAvatarSubmission.reviewed_by),
        )
        .where(ForumAvatarSubmission.status == FORUM_AVATAR_STATUS_PENDING)
        .order_by(ForumAvatarSubmission.uploaded_at.asc()),
        page=page,
        per_page=20,
        error_out=False,
    )
    sync_error_accounts = db.session.execute(
        db.select(ForumAccount)
        .options(selectinload(ForumAccount.user), selectinload(ForumAccount.member))
        .where(or_(ForumAccount.state == FORUM_STATE_SYNC_ERROR, ForumAccount.last_error.is_not(None)))
        .order_by(ForumAccount.updated_at.desc())
        .limit(25)
    ).scalars().all()
    return render_template(
        "admin_forum.html",
        active_admin_section="forum",
        metrics=get_admin_dashboard_metrics(),
        pending_avatar_pagination=pending_avatar_pagination,
        sync_error_accounts=sync_error_accounts,
    )


@admin_bp.route("/admin/accounts/<int:user_id>/forum-resync", methods=["POST"])
@login_required
@admin_required
def admin_resync_forum_account(user_id):
    user = db.session.execute(
        db.select(User)
        .options(selectinload(User.member), selectinload(User.forum_account))
        .filter_by(id=user_id)
    ).scalar_one_or_none()
    if user is None:
        flash(_("The selected account could not be found."), "warning")
        return redirect(url_for("admin.admin_accounts"))

    if user.member is None:
        flash(_("This account does not have a linked membership profile yet."), "warning")
        return redirect(request.form.get("next") or url_for("admin.admin_account_detail", user_id=user.id))

    before_state = snapshot_forum_account_for_audit(user.forum_account)
    result, _service = sync_member_forum_state(user.member)
    log_audit_event(
        category="forum",
        event_type="manual_forum_resync",
        actor_user=current_user,
        target_user=user,
        target_member=user.member,
        before=before_state,
        after=snapshot_forum_account_for_audit(user.forum_account),
        metadata={
            "desired_state": result.desired_state if result else None,
            "error": result.error if result else None,
        },
    )
    db.session.commit()

    if result and result.error:
        flash(_("Forum sync completed with an issue: %(message)s", message=result.error), "warning")
    else:
        flash(_("Forum state synchronized successfully."), "success")
    return redirect(request.form.get("next") or url_for("admin.admin_account_detail", user_id=user.id))


@admin_bp.route("/admin/forum/submissions/<int:submission_id>/approve", methods=["POST"])
@login_required
@admin_required
def approve_forum_avatar_submission(submission_id):
    submission = db.session.execute(
        db.select(ForumAvatarSubmission)
        .options(
            selectinload(ForumAvatarSubmission.user),
            selectinload(ForumAvatarSubmission.member).selectinload(Member.user),
        )
        .where(ForumAvatarSubmission.id == submission_id)
    ).scalar_one_or_none()
    if submission is None:
        flash(_("The selected avatar submission could not be found."), "warning")
        return redirect(url_for("admin.admin_forum"))

    forum_service = get_forum_service()
    before_submission = snapshot_forum_avatar_submission_for_audit(submission)
    before_account = snapshot_forum_account_for_audit(submission.user.forum_account if submission.user else None)
    review_note = (request.form.get("review_note") or "").strip() or None

    try:
        result = forum_service.approve_avatar_submission(submission, reviewer=current_user, review_note=review_note)
    except ForumProviderError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.admin_forum"))

    event_type = "avatar_approved" if not result.error else "avatar_approval_failed"
    log_audit_event(
        category="forum",
        event_type=event_type,
        actor_user=current_user,
        target_user=submission.user,
        target_member=submission.member,
        before={"submission": before_submission, "forum_account": before_account},
        after={
            "submission": snapshot_forum_avatar_submission_for_audit(submission),
            "forum_account": snapshot_forum_account_for_audit(submission.user.forum_account if submission.user else None),
        },
        metadata={"review_note": review_note, "error": result.error, "desired_state": result.desired_state},
    )
    db.session.commit()

    if result.error:
        flash(_("The avatar review was saved, but syncing it to the forum failed: %(message)s", message=result.error), "warning")
    else:
        flash(_("The avatar was approved and the forum access was updated."), "success")
    return redirect(url_for("admin.admin_forum"))


@admin_bp.route("/admin/forum/submissions/<int:submission_id>/reject", methods=["POST"])
@login_required
@admin_required
def reject_forum_avatar_submission(submission_id):
    submission = db.session.execute(
        db.select(ForumAvatarSubmission)
        .options(
            selectinload(ForumAvatarSubmission.user),
            selectinload(ForumAvatarSubmission.member).selectinload(Member.user),
        )
        .where(ForumAvatarSubmission.id == submission_id)
    ).scalar_one_or_none()
    if submission is None:
        flash(_("The selected avatar submission could not be found."), "warning")
        return redirect(url_for("admin.admin_forum"))

    forum_service = get_forum_service()
    before_submission = snapshot_forum_avatar_submission_for_audit(submission)
    before_account = snapshot_forum_account_for_audit(submission.user.forum_account if submission.user else None)
    review_note = (request.form.get("review_note") or "").strip() or None

    try:
        result = forum_service.reject_avatar_submission(submission, reviewer=current_user, review_note=review_note)
    except ForumProviderError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.admin_forum"))

    log_audit_event(
        category="forum",
        event_type="avatar_rejected",
        actor_user=current_user,
        target_user=submission.user,
        target_member=submission.member,
        before={"submission": before_submission, "forum_account": before_account},
        after={
            "submission": snapshot_forum_avatar_submission_for_audit(submission),
            "forum_account": snapshot_forum_account_for_audit(submission.user.forum_account if submission.user else None),
        },
        metadata={"review_note": review_note, "error": result.error if result else None},
    )
    queue_user_status_notification(
        "forum_avatar_rejected",
        _("Your forum profile picture was rejected."),
        recipient_email=(submission.user.email if submission.user is not None else (submission.member.email_private if submission.member is not None else None)),
        payload={
            "first_name": submission.member.first_name if submission.member is not None else None,
            "review_note": review_note,
        },
        target_user=submission.user,
        target_member=submission.member,
        object_type="forum_avatar_submission",
        object_id=submission.id,
    )
    db.session.commit()
    flash(_("The avatar submission was rejected."), "success")
    return redirect(url_for("admin.admin_forum"))


@admin_bp.route("/admin/settings/test-forum-connection", methods=["POST"])
@login_required
@admin_required
def test_forum_connection():
    service = get_forum_service()
    try:
        success, message = service.test_connection()
    except ForumProviderError as exc:
        success = False
        message = str(exc)

    log_audit_event(
        category="forum",
        event_type="forum_connection_tested",
        actor_user=current_user,
        target_user=current_user,
        before=None,
        after={"ready": service.is_ready(), "enabled": service.is_enabled()},
        metadata={"success": success, "message": message},
    )
    db.session.commit()
    flash(message, "success" if success else "danger")
    return redirect(f"{url_for('admin.admin_settings')}#settings-forum")


@admin_bp.route("/admin/accounts/<int:user_id>/grant-admin", methods=["POST"])
@login_required
@admin_required
def grant_admin_access(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        flash(_("The selected account could not be found."), "warning")
        return redirect(url_for("admin.admin_accounts"))

    if not user.has_role("admin"):
        admin_role = get_role("admin", label="Admin", description="Can access the admin workspace.")
        before_user = snapshot_user_for_audit(user)
        user.grant_role(admin_role)
        log_audit_event(
            category="access",
            event_type="admin_role_granted",
            actor_user=current_user,
            target_user=user,
            target_member=user.member,
            before=before_user,
            after=snapshot_user_for_audit(user),
            metadata={"granted_role": "admin"},
        )
        db.session.commit()
        flash(_("Admin access granted."), "success")
    else:
        flash(_("This account already has admin access."), "info")

    return redirect(request.form.get("next") or url_for("admin.admin_account_detail", user_id=user.id))


@admin_bp.route("/admin/accounts/<int:user_id>/revoke-admin", methods=["POST"])
@login_required
@admin_required
def revoke_admin_access(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        flash(_("The selected account could not be found."), "warning")
        return redirect(url_for("admin.admin_accounts"))

    if not user.has_role("admin"):
        flash(_("This account does not currently have admin access."), "info")
        return redirect(request.form.get("next") or url_for("admin.admin_account_detail", user_id=user.id))

    if current_user.id == user.id:
        flash(_("You cannot remove your own admin access from the UI."), "danger")
        return redirect(request.form.get("next") or url_for("admin.admin_account_detail", user_id=user.id))

    if count_users_with_role("admin") <= 1:
        flash(_("You cannot remove the last remaining admin account."), "danger")
        return redirect(request.form.get("next") or url_for("admin.admin_account_detail", user_id=user.id))

    before_user = snapshot_user_for_audit(user)
    user.revoke_role("admin")
    log_audit_event(
        category="access",
        event_type="admin_role_revoked",
        actor_user=current_user,
        target_user=user,
        target_member=user.member,
        before=before_user,
        after=snapshot_user_for_audit(user),
        metadata={"revoked_role": "admin"},
    )
    db.session.commit()
    flash(_("Admin access revoked."), "success")
    return redirect(request.form.get("next") or url_for("admin.admin_account_detail", user_id=user.id))


@admin_bp.route("/admin/approvals", methods=["GET"])
@login_required
@admin_required
def admin_approvals():
    pending_identity_requests = db.session.execute(
        db.select(MemberProfileChangeRequest)
        .options(
            selectinload(MemberProfileChangeRequest.member).selectinload(Member.user),
            selectinload(MemberProfileChangeRequest.requested_by),
            selectinload(MemberProfileChangeRequest.reviewed_by),
        )
        .where(MemberProfileChangeRequest.status == "pending")
        .order_by(MemberProfileChangeRequest.created_at.asc())
    ).scalars().all()
    decorate_pending_identity_requests(pending_identity_requests)

    history_page = request.args.get("page", 1, type=int)
    history_pagination = db.paginate(
        db.select(MemberProfileChangeRequest)
        .options(
            selectinload(MemberProfileChangeRequest.member).selectinload(Member.user),
            selectinload(MemberProfileChangeRequest.requested_by),
            selectinload(MemberProfileChangeRequest.reviewed_by),
        )
        .where(MemberProfileChangeRequest.status != "pending")
        .order_by(MemberProfileChangeRequest.reviewed_at.desc(), MemberProfileChangeRequest.created_at.desc()),
        page=history_page,
        per_page=APPROVAL_HISTORY_PAGE_SIZE,
        error_out=False,
    )
    recent_logs = db.session.execute(
        db.select(AuditLog)
        .options(
            selectinload(AuditLog.actor_user),
            selectinload(AuditLog.target_user),
            selectinload(AuditLog.target_member),
        )
        .where(AuditLog.category.in_(["profile", "profile_change_request"]))
        .order_by(AuditLog.created_at.desc())
        .limit(20)
    ).scalars().all()
    return render_template(
        "admin_approvals.html",
        active_admin_section="approvals",
        pending_identity_requests=pending_identity_requests,
        history_pagination=history_pagination,
        recent_logs=recent_logs,
    )


@admin_bp.route("/admin/settings", methods=["GET", "POST"])
@login_required
@admin_required
def admin_settings():
    edit_mail_account_id = request.args.get("edit_mail_account", type=int)
    context = build_settings_page_context(edit_mail_account_id=edit_mail_account_id)

    if edit_mail_account_id and context["editing_mail_account"] is None:
        flash(_("The selected mail account could not be found."), "warning")
        return redirect(url_for("admin.admin_settings"))

    if request.method == "POST" and "save_settings" in request.form:
        valid_senders = {choice for choice, _label in context["sender_choices"]}
        valid_templates = {choice for choice, _label in context["template_choices"]}
        valid_forum_providers = {choice for choice, _label in context["forum_provider_choices"]}
        valid_forum_auth_strategies = {choice for choice, _label in context["forum_auth_strategy_choices"]}
        tracked_setting_keys = [
            "invoice_payments_enabled",
            "automatic_emails_enabled",
            "welcome_email_sender",
            "automatic_email_template",
            *STRIPE_SETTING_KEYS,
            *FORUM_SETTING_KEYS,
            *NOTIFICATION_SETTING_KEYS,
        ]
        before_settings = {
            key: db.session.get(Setting, key).value if db.session.get(Setting, key) is not None else None
            for key in tracked_setting_keys
        }
        settings_section = (request.form.get("settings_section") or "general").strip().lower()
        if settings_section not in {"general", "notifications", "billing", "forum", "mail", "test"}:
            settings_section = "general"
        settings_redirect = f"{url_for('admin.admin_settings')}#settings-{settings_section}"
        welcome_sender = request.form.get("welcome_email_sender")
        auto_email_template = request.form.get("automatic_email_template")
        notification_sender = request.form.get("notification_sender")
        stripe_publishable_key = ((request.form.get("stripe_publishable_key") if settings_section == "billing" else before_settings.get("stripe_publishable_key")) or "").strip()
        stripe_price_id = ((request.form.get("stripe_price_id") if settings_section == "billing" else before_settings.get("stripe_price_id")) or "").strip()
        forum_provider = (request.form.get("forum_provider") or before_settings.get("forum_provider") or "discourse").strip() or "discourse"
        forum_auth_strategy = (request.form.get("forum_auth_strategy") or before_settings.get("forum_auth_strategy") or "discourse_connect").strip() or "discourse_connect"
        forum_avatar_max_bytes = (request.form.get("forum_avatar_max_bytes") or before_settings.get("forum_avatar_max_bytes") or "").strip()
        forum_avatar_allowed_types = (request.form.get("forum_avatar_allowed_types") or before_settings.get("forum_avatar_allowed_types") or "").strip()

        if welcome_sender and welcome_sender not in valid_senders:
            flash(_("Invalid sender account selected."), "danger")
            return redirect(settings_redirect)

        if auto_email_template and auto_email_template not in valid_templates:
            flash(_("Invalid email template selected."), "danger")
            return redirect(settings_redirect)

        if notification_sender and notification_sender not in valid_senders:
            flash(_("Invalid sender account selected."), "danger")
            return redirect(settings_redirect)

        if forum_provider not in valid_forum_providers:
            flash(_("Invalid forum provider selected."), "danger")
            return redirect(settings_redirect)

        if forum_auth_strategy not in valid_forum_auth_strategies:
            flash(_("Invalid forum authentication strategy selected."), "danger")
            return redirect(settings_redirect)

        if forum_avatar_max_bytes:
            try:
                if int(forum_avatar_max_bytes) <= 0:
                    raise ValueError
            except ValueError:
                flash(_("The forum avatar size limit must be a positive number of bytes."), "danger")
                return redirect(settings_redirect)

        invoice_enabled = (request.form.get("invoice_payments_enabled") == "on") if settings_section == "general" else str(before_settings.get("invoice_payments_enabled") or "False") == "True"
        emails_enabled = (request.form.get("automatic_emails_enabled") == "on") if settings_section == "general" else str(before_settings.get("automatic_emails_enabled") or "False") == "True"
        forum_enabled = (request.form.get("forum_integration_enabled") == "on") if settings_section == "forum" else str(before_settings.get("forum_integration_enabled") or "False") == "True"
        notification_admin_general_enabled = (request.form.get("notification_admin_general_enabled") == "on") if settings_section == "notifications" else str(before_settings.get("notification_admin_general_enabled") or "True") == "True"
        notification_admin_error_enabled = (request.form.get("notification_admin_error_enabled") == "on") if settings_section == "notifications" else str(before_settings.get("notification_admin_error_enabled") or "True") == "True"
        notification_user_status_enabled = (request.form.get("notification_user_status_enabled") == "on") if settings_section == "notifications" else str(before_settings.get("notification_user_status_enabled") or "True") == "True"

        set_setting_value("invoice_payments_enabled", str(invoice_enabled))
        set_setting_value("automatic_emails_enabled", str(emails_enabled))
        set_setting_value("welcome_email_sender", welcome_sender if settings_section == "general" else before_settings.get("welcome_email_sender"))
        set_setting_value("automatic_email_template", auto_email_template if settings_section == "general" else before_settings.get("automatic_email_template"))
        set_setting_value("notification_admin_general_enabled", str(notification_admin_general_enabled))
        set_setting_value("notification_admin_error_enabled", str(notification_admin_error_enabled))
        set_setting_value("notification_user_status_enabled", str(notification_user_status_enabled))
        set_setting_value("notification_sender", (notification_sender if settings_section == "notifications" else before_settings.get("notification_sender")) or None)
        set_setting_value("stripe_publishable_key", stripe_publishable_key or None)
        set_setting_value("stripe_price_id", stripe_price_id or None)
        set_setting_value("forum_integration_enabled", str(forum_enabled))
        set_setting_value("forum_provider", forum_provider)
        set_setting_value("forum_auth_strategy", forum_auth_strategy)
        set_setting_value("forum_base_url", ((request.form.get("forum_base_url") if settings_section == "forum" else before_settings.get("forum_base_url")) or "").strip() or None)
        set_setting_value("discourse_api_username", ((request.form.get("discourse_api_username") if settings_section == "forum" else before_settings.get("discourse_api_username")) or "").strip() or None)
        set_setting_value("forum_onboarding_group", ((request.form.get("forum_onboarding_group") if settings_section == "forum" else before_settings.get("forum_onboarding_group")) or "").strip() or None)
        set_setting_value("forum_member_group", ((request.form.get("forum_member_group") if settings_section == "forum" else before_settings.get("forum_member_group")) or "").strip() or None)
        set_setting_value("forum_inactive_group", ((request.form.get("forum_inactive_group") if settings_section == "forum" else before_settings.get("forum_inactive_group")) or "").strip() or None)
        set_setting_value("forum_onboarding_path", ((request.form.get("forum_onboarding_path") if settings_section == "forum" else before_settings.get("forum_onboarding_path")) or "").strip() or "/")
        set_setting_value("forum_avatar_max_bytes", forum_avatar_max_bytes or None)
        set_setting_value("forum_avatar_allowed_types", forum_avatar_allowed_types or None)

        existing_api_key = before_settings.get("discourse_api_key")
        submitted_api_key = ((request.form.get("discourse_api_key") if settings_section == "forum" else "") or "").strip()
        set_setting_value("discourse_api_key", submitted_api_key or existing_api_key)

        existing_connect_secret = before_settings.get("discourse_connect_secret")
        submitted_connect_secret = ((request.form.get("discourse_connect_secret") if settings_section == "forum" else "") or "").strip()
        set_setting_value("discourse_connect_secret", submitted_connect_secret or existing_connect_secret)

        existing_stripe_secret = before_settings.get("stripe_secret_key")
        submitted_stripe_secret = ((request.form.get("stripe_secret_key") if settings_section == "billing" else "") or "").strip()
        set_setting_value("stripe_secret_key", submitted_stripe_secret or existing_stripe_secret)

        existing_webhook_secret = before_settings.get("stripe_webhook_secret")
        submitted_webhook_secret = ((request.form.get("stripe_webhook_secret") if settings_section == "billing" else "") or "").strip()
        set_setting_value("stripe_webhook_secret", submitted_webhook_secret or existing_webhook_secret)

        after_settings = {
            key: db.session.get(Setting, key).value if db.session.get(Setting, key) is not None else None
            for key in tracked_setting_keys
        }
        changed_keys = sorted(
            key for key in after_settings.keys()
            if before_settings.get(key) != after_settings.get(key)
        )
        logged_before_settings, logged_after_settings = redact_settings_states_for_audit(before_settings, after_settings)
        log_audit_event(
            category="settings",
            event_type="settings_updated",
            actor_user=current_user,
            target_user=current_user,
            before=logged_before_settings,
            after=logged_after_settings,
            metadata={"changed_keys": changed_keys},
        )
        db.session.commit()
        flash(_("Settings updated successfully!"), "success")
        return redirect(settings_redirect)

    return render_template(
        "admin_settings.html",
        active_admin_section="settings",
        **context,
    )


@admin_bp.route("/admin/logs", methods=["GET"])
@login_required
@admin_required
def admin_logs():
    actor_user = aliased(User)
    target_user = aliased(User)
    target_member = aliased(Member)
    search_term = (request.args.get("q") or "").strip()
    category = request.args.get("category", "all")
    page = request.args.get("page", 1, type=int)

    query = (
        db.select(AuditLog)
        .options(
            selectinload(AuditLog.actor_user),
            selectinload(AuditLog.target_user),
            selectinload(AuditLog.target_member),
        )
        .outerjoin(actor_user, AuditLog.actor_user_id == actor_user.id)
        .outerjoin(target_user, AuditLog.target_user_id == target_user.id)
        .outerjoin(target_member, AuditLog.target_member_id == target_member.id)
    )

    if search_term:
        pattern = f"%{search_term}%"
        query = query.where(
            or_(
                actor_user.email.ilike(pattern),
                target_user.email.ilike(pattern),
                target_member.email_private.ilike(pattern),
                AuditLog.category.ilike(pattern),
                AuditLog.event_type.ilike(pattern),
            )
        )

    if category != "all":
        query = query.where(AuditLog.category == category)

    pagination = db.paginate(
        query.order_by(AuditLog.created_at.desc()),
        page=page,
        per_page=AUDIT_LOG_PAGE_SIZE,
        error_out=False,
    )
    categories = db.session.execute(
        db.select(AuditLog.category).distinct().order_by(AuditLog.category.asc())
    ).scalars().all()
    return render_template(
        "admin_logs.html",
        active_admin_section="logs",
        pagination=pagination,
        categories=categories,
        category=category,
        search_term=search_term,
    )


@admin_bp.route("/admin/profile-requests/<int:request_id>/approve", methods=["POST"])
@login_required
@admin_required
def approve_profile_change_request(request_id):
    request_record = db.session.get(MemberProfileChangeRequest, request_id)
    if request_record is None or request_record.status != "pending":
        flash(_("The selected change request could not be found."), "warning")
        return redirect(url_for("admin.admin_approvals"))

    member = request_record.member
    before_member = snapshot_member_for_audit(member, fields=IDENTITY_MEMBER_FIELDS)
    before_user = snapshot_user_for_audit(member.user)
    previous_forum_username = member.user.forum_username if member.user is not None else None

    member.salutation = request_record.requested_salutation
    member.title = request_record.requested_title
    member.first_name = request_record.requested_first_name
    member.last_name = request_record.requested_last_name
    member.year_group = request_record.requested_year_group

    if member.user is not None and request.form.get("override_forum_username") == "1":
        preferred_username = (request.form.get("forum_username_override") or "").strip()
        if not preferred_username:
            preferred_username = build_forum_username_base(member.first_name, member.last_name, member.year_group)
        member.user.forum_username = generate_unique_forum_username(
            member.first_name,
            member.last_name,
            member.year_group,
            exclude_user_id=member.user.id,
            preferred=preferred_username,
        )

    request_record.status = "approved"
    request_record.admin_note = (request.form.get("admin_note") or "").strip() or None
    request_record.reviewed_by = current_user
    request_record.reviewed_at = get_now_utc()
    forum_result = None
    if member.user is not None and (member.user.forum_account is not None or member_has_active_access(member)):
        forum_result, _forum_service = sync_member_forum_state(member)
    log_audit_event(
        category="profile_change_request",
        event_type="identity_request_approved",
        actor_user=current_user,
        target_user=member.user,
        target_member=member,
        before={"request_status": "pending", "user": before_user, "member": before_member},
        after={"request_status": request_record.status, "user": snapshot_user_for_audit(member.user), "member": snapshot_member_for_audit(member, fields=IDENTITY_MEMBER_FIELDS)},
        metadata={
            "request_id": request_record.id,
            "member_note": request_record.member_note,
            "admin_note": request_record.admin_note,
            "previous_forum_username": previous_forum_username,
            "new_forum_username": member.user.forum_username if member.user is not None else None,
            "forum_sync_error": forum_result.error if forum_result else None,
        },
    )
    queue_user_status_notification(
        "identity_request_approved",
        _("Your identity change request was approved."),
        recipient_email=(member.user.email if member.user is not None else member.email_private),
        payload={
            "first_name": member.first_name,
            "admin_note": request_record.admin_note,
        },
        target_user=member.user,
        target_member=member,
        object_type="member_profile_change_request",
        object_id=request_record.id,
    )
    db.session.commit()
    flash(_("Identity change request approved."), "success")
    if forum_result and forum_result.error:
        flash(_("The forum profile could not be synchronized right now. Please run a forum resync after checking the settings."), "warning")
    return redirect(url_for("admin.admin_approvals"))


@admin_bp.route("/admin/profile-requests/<int:request_id>/reject", methods=["POST"])
@login_required
@admin_required
def reject_profile_change_request(request_id):
    request_record = db.session.get(MemberProfileChangeRequest, request_id)
    if request_record is None or request_record.status != "pending":
        flash(_("The selected change request could not be found."), "warning")
        return redirect(url_for("admin.admin_approvals"))

    request_record.status = "rejected"
    request_record.admin_note = (request.form.get("admin_note") or "").strip() or None
    request_record.reviewed_by = current_user
    request_record.reviewed_at = get_now_utc()
    log_audit_event(
        category="profile_change_request",
        event_type="identity_request_rejected",
        actor_user=current_user,
        target_user=request_record.member.user,
        target_member=request_record.member,
        before={"request_id": request_record.id, "status": "pending"},
        after={"request_id": request_record.id, "status": request_record.status},
        metadata={"member_note": request_record.member_note, "admin_note": request_record.admin_note},
    )
    queue_user_status_notification(
        "identity_request_rejected",
        _("Your identity change request was rejected."),
        recipient_email=((request_record.member.user.email if request_record.member.user is not None else request_record.member.email_private) if request_record.member is not None else None),
        payload={
            "first_name": request_record.member.first_name if request_record.member is not None else None,
            "admin_note": request_record.admin_note,
        },
        target_user=request_record.member.user if request_record.member is not None else None,
        target_member=request_record.member,
        object_type="member_profile_change_request",
        object_id=request_record.id,
    )
    db.session.commit()
    flash(_("Identity change request rejected."), "success")
    return redirect(url_for("admin.admin_approvals"))


@admin_bp.route("/admin/settings/mail-accounts", methods=["POST"])
@login_required
@admin_required
def save_mail_account():
    form = MailAccountForm(prefix="mail")
    account_id = int(form.mail_account_id.data) if form.mail_account_id.data else None

    if not form.validate_on_submit():
        flash(_("Please correct the mail account form and try again."), "danger")
        for field_name, errors in form.errors.items():
            if field_name == "csrf_token":
                for error in errors:
                    flash(error, "danger")
                continue
            label = getattr(form, field_name).label.text if hasattr(form, field_name) else field_name
            for error in errors:
                flash(f"{label}: {error}", "danger")
        redirect_kwargs = {"edit_mail_account": account_id} if account_id else {}
        return redirect(url_for("admin.admin_settings", **redirect_kwargs))

    account_key = form.account_key.data.strip()
    existing_account = db.session.execute(
        db.select(MailAccount).filter_by(account_key=account_key)
    ).scalar_one_or_none()

    if existing_account is not None and existing_account.id != account_id:
        flash(_("A mail account with this key already exists."), "danger")
        target_id = account_id or existing_account.id
        return redirect(url_for("admin.admin_settings", edit_mail_account=target_id))

    if account_id:
        mail_account = db.session.get(MailAccount, account_id)
        if mail_account is None:
            flash(_("The selected mail account could not be found."), "warning")
            return redirect(f"{url_for('admin.admin_settings')}#settings-mail")
    else:
        if not form.password.data:
            flash(_("A password is required for new mail accounts."), "danger")
            return redirect(f"{url_for('admin.admin_settings')}#settings-mail")
        mail_account = MailAccount()
        db.session.add(mail_account)

    before_mail_account = snapshot_mail_account_for_audit(mail_account)
    is_new_mail_account = mail_account.id is None
    mail_account.account_key = account_key
    mail_account.host = form.host.data.strip()
    mail_account.port = int(form.port.data)
    mail_account.username = form.username.data.strip()
    if form.password.data:
        mail_account.password = form.password.data
    mail_account.starttls = bool(form.starttls.data)

    try:
        db.session.flush()
        log_audit_event(
            category="settings",
            event_type="mail_account_created" if is_new_mail_account else "mail_account_updated",
            actor_user=current_user,
            target_user=current_user,
            before=before_mail_account,
            after=snapshot_mail_account_for_audit(mail_account),
            metadata={"mail_account_id": mail_account.id, "account_key": mail_account.account_key},
        )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash(_("A mail account with this key already exists."), "danger")
        redirect_kwargs = {"edit_mail_account": account_id} if account_id else {}
        return redirect(url_for("admin.admin_settings", **redirect_kwargs))

    flash(_("Mail account saved successfully."), "success")
    return redirect(f"{url_for('admin.admin_settings')}#settings-mail")


@admin_bp.route("/admin/settings/mail-accounts/<int:mail_account_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_mail_account(mail_account_id):
    mail_account = db.session.get(MailAccount, mail_account_id)
    if mail_account is None:
        flash(_("The selected mail account could not be found."), "warning")
        return redirect(f"{url_for('admin.admin_settings')}#settings-mail")

    before_mail_account = snapshot_mail_account_for_audit(mail_account)
    welcome_sender_setting = Setting.query.get("welcome_email_sender")
    removed_welcome_sender = False
    if welcome_sender_setting and welcome_sender_setting.value == mail_account.account_key:
        db.session.delete(welcome_sender_setting)
        removed_welcome_sender = True

    log_audit_event(
        category="settings",
        event_type="mail_account_deleted",
        actor_user=current_user,
        target_user=current_user,
        before=before_mail_account,
        after=None,
        metadata={"mail_account_id": mail_account.id, "account_key": mail_account.account_key, "removed_welcome_sender": removed_welcome_sender},
    )
    db.session.delete(mail_account)
    db.session.commit()
    flash(_("Mail account deleted successfully."), "success")
    return redirect(f"{url_for('admin.admin_settings')}#settings-mail")


@admin_bp.route("/admin/settings/mail-accounts/import", methods=["POST"])
@login_required
@admin_required
def import_mail_accounts():
    upload = request.files.get("mail_accounts_file")
    overwrite_existing = request.form.get("overwrite_existing") == "1"

    if upload is None or not upload.filename:
        flash(_("Please choose a JSON file to import."), "warning")
        return redirect(f"{url_for('admin.admin_settings')}#settings-mail")

    try:
        raw_payload = upload.stream.read()
        payload = json.loads(raw_payload.decode("utf-8-sig"))
        imported_accounts = normalize_imported_mail_accounts_payload(payload)
    except UnicodeDecodeError:
        flash(_("The uploaded file is not valid UTF-8 JSON."), "danger")
        return redirect(f"{url_for('admin.admin_settings')}#settings-mail")
    except json.JSONDecodeError:
        flash(_("The uploaded file is not valid JSON."), "danger")
        return redirect(f"{url_for('admin.admin_settings')}#settings-mail")
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(f"{url_for('admin.admin_settings')}#settings-mail")

    created_count = 0
    updated_count = 0
    skipped_keys = []

    try:
        for imported_account in imported_accounts:
            mail_account = db.session.execute(
                db.select(MailAccount).filter_by(account_key=imported_account["account_key"])
            ).scalar_one_or_none()

            if mail_account is not None and not overwrite_existing:
                skipped_keys.append(imported_account["account_key"])
                continue

            before_mail_account = snapshot_mail_account_for_audit(mail_account)
            is_new_mail_account = mail_account is None
            if mail_account is None:
                mail_account = MailAccount()
                db.session.add(mail_account)

            mail_account.account_key = imported_account["account_key"]
            mail_account.host = imported_account["host"]
            mail_account.port = imported_account["port"]
            mail_account.username = imported_account["username"]
            mail_account.password = imported_account["password"]
            mail_account.starttls = imported_account["starttls"]
            db.session.flush()

            log_audit_event(
                category="settings",
                event_type="mail_account_created" if is_new_mail_account else "mail_account_updated",
                actor_user=current_user,
                target_user=current_user,
                before=before_mail_account,
                after=snapshot_mail_account_for_audit(mail_account),
                metadata={
                    "mail_account_id": mail_account.id,
                    "account_key": mail_account.account_key,
                    "source": "json_import",
                    "overwrite_existing": overwrite_existing,
                },
            )

            if is_new_mail_account:
                created_count += 1
            else:
                updated_count += 1

        log_audit_event(
            category="settings",
            event_type="mail_accounts_imported",
            actor_user=current_user,
            target_user=current_user,
            before=None,
            after={"created": created_count, "updated": updated_count, "skipped": len(skipped_keys)},
            metadata={
                "overwrite_existing": overwrite_existing,
                "imported_keys": [account["account_key"] for account in imported_accounts],
                "skipped_keys": skipped_keys,
            },
        )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash(_("Import failed because one of the account keys already exists."), "danger")
        return redirect(f"{url_for('admin.admin_settings')}#settings-mail")

    if created_count or updated_count:
        flash(
            _(
                "Mail account import finished. Created: %(created)s, updated: %(updated)s, skipped: %(skipped)s.",
                created=created_count,
                updated=updated_count,
                skipped=len(skipped_keys),
            ),
            "success",
        )
    else:
        flash(_("No mail accounts were imported."), "info")

    if skipped_keys:
        flash(
            _(
                "Skipped existing account keys: %(keys)s",
                keys=", ".join(skipped_keys),
            ),
            "warning",
        )

    return redirect(f"{url_for('admin.admin_settings')}#settings-mail")


@admin_bp.route("/admin/settings/mail-accounts/export", methods=["POST"])
@login_required
@admin_required
def export_mail_accounts():
    confirm_password = request.form.get("export_password", "")
    if not current_user.check_password(confirm_password):
        flash(_("Please confirm your current password to export sender accounts."), "danger")
        return redirect(f"{url_for('admin.admin_settings')}#settings-mail")

    payload = build_mail_accounts_export_payload()
    log_audit_event(
        category="settings",
        event_type="mail_accounts_exported",
        actor_user=current_user,
        target_user=current_user,
        metadata={"count": len(payload["mail_accounts"]), "format": payload["format"], "version": payload["version"]},
    )
    db.session.commit()

    export_timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    response = current_app.response_class(
        json.dumps(payload, indent=2),
        mimetype="application/json",
    )
    response.headers["Content-Disposition"] = f'attachment; filename="jaeronautics-mail-accounts-{export_timestamp}.json"'
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@admin_bp.route("/admin/settings/mail-accounts/<int:mail_account_id>/test-connection", methods=["POST"])
@login_required
@admin_required
@limiter.limit(RATELIMIT_ADMIN_EMAIL)
def test_mail_account_connection(mail_account_id):
    mail_account = db.session.get(MailAccount, mail_account_id)
    if mail_account is None:
        flash(_("The selected mail account could not be found."), "warning")
        return redirect(f"{url_for('admin.admin_settings')}#settings-mail")

    success, message = probe_mail_account_connection(mail_account.to_config())
    log_audit_event(
        category="settings",
        event_type="mail_account_connection_tested",
        actor_user=current_user,
        target_user=current_user,
        before=snapshot_mail_account_for_audit(mail_account),
        after=None,
        metadata={"mail_account_id": mail_account.id, "account_key": mail_account.account_key, "success": success, "message": message},
    )
    db.session.commit()

    if success:
        flash(_("Connection test succeeded for %(account_key)s.", account_key=mail_account.account_key), "success")
    else:
        flash(_("Connection test failed for %(account_key)s: %(message)s", account_key=mail_account.account_key, message=message), "danger")
    return redirect(url_for("admin.admin_settings", edit_mail_account=mail_account.id))


@admin_bp.route("/admin/settings/send-test-email", methods=["POST"])
@login_required
@admin_required
@limiter.limit(RATELIMIT_ADMIN_EMAIL)
def send_test_email():
    form = TestEmailForm()

    try:
        mail_accounts = load_mail_accounts_config()
        form.sender.choices = [(acc, acc) for acc in mail_accounts.keys()]

        email_template_dir = os.path.join(current_app.root_path, "templates", "emails")
        if os.path.isdir(email_template_dir):
            form.template.choices = [(f, f) for f in os.listdir(email_template_dir) if f.endswith(".html")]
    except Exception as exc:
        current_app.logger.error(f"Could not load email accounts or templates for test form validation: {exc}")
        form.sender.choices = []
        form.template.choices = []

    if form.validate_on_submit():
        sender = form.sender.data
        recipient = form.recipient.data
        template = form.template.data

        logo_path = os.path.join(current_app.root_path, "static", "logo_joanneum_aeronautics_negativ.png")
        attachments = [{"path": logo_path, "cid": "logo"}]

        success = send_mail(
            from_account=sender,
            to_email=recipient,
            subject=f"Test: {template}",
            template_name=template,
            attachments=attachments,
            first_name="Test User",
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            now=datetime.now(timezone.utc),
        )

        if success:
            flash(_("Test email sent successfully to %(recipient)s!", recipient=recipient), "success")
        else:
            flash(_("Failed to send test email. Please check the server logs."), "danger")
    else:
        flash(_("Invalid form submission. Please check the fields and try again."), "warning")

    return redirect(f"{url_for('admin.admin_settings')}#settings-test")
