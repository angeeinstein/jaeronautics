import json
import os
import secrets
import sys
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps
from pathlib import Path
from subprocess import run
from urllib.parse import quote_plus, urljoin, urlsplit
from zoneinfo import ZoneInfo

import click
import stripe
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask.cli import with_appcontext
from flask_babel import Babel, _, format_currency, format_date, get_locale
from flask_limiter import Limiter
from flask_migrate import Migrate
from flask_limiter.errors import RateLimitExceeded
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from flask_wtf.csrf import CSRFError, CSRFProtect
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, inspect, or_, text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased, selectinload
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.routing import BuildError

try:
    from .db_models import (
        AuditLog,
        EmailDeliveryJob,
        ForumAccount,
        ForumAvatarSubmission,
        MailAccount,
        Member,
        MemberProfileChangeRequest,
        NotificationBatch,
        NotificationEvent,
        ProcessedStripeEvent,
        Role,
        Setting,
        User,
        UserRole,
        db,
    )
    from .forms import (
        ChangePasswordForm,
        CreateMembershipProfileForm,
        EmailRequestForm,
        IdentityChangeRequestForm,
        LoginForm,
        MailAccountForm,
        MemberProfileForm,
        MembershipForm,
        RegistrationForm,
        SetPasswordForm,
        TestEmailForm,
    )
    from .forum_service import (
        FORUM_AVATAR_STATUS_APPROVED,
        FORUM_AVATAR_STATUS_PENDING,
        FORUM_AVATAR_STATUS_REJECTED,
        FORUM_AVATAR_STATUS_SUPERSEDED,
        FORUM_SETTING_KEYS,
        FORUM_STATE_ACTIVE,
        FORUM_STATE_INACTIVE,
        FORUM_STATE_ONBOARDING,
        FORUM_STATE_SYNC_ERROR,
        ForumProviderError,
        ForumService,
        delete_submission_file,
        format_bytes_human,
        normalize_forum_settings,
    )
    from .mail_utils import load_mail_accounts_config, probe_mail_account_connection, send_mail
    from .notification_service import (
        ADMIN_ERROR_CHANNEL,
        ADMIN_GENERAL_CHANNEL,
        NOTIFICATION_SETTING_KEYS,
        USER_STATUS_CHANNEL,
        NotificationService,
        normalize_notification_settings,
    )
    from .security_utils import build_public_url, is_trusted_host, normalize_public_base_url
except ImportError:
    from db_models import (
        AuditLog,
        EmailDeliveryJob,
        ForumAccount,
        ForumAvatarSubmission,
        MailAccount,
        Member,
        MemberProfileChangeRequest,
        NotificationBatch,
        NotificationEvent,
        ProcessedStripeEvent,
        Role,
        Setting,
        User,
        UserRole,
        db,
    )
    from forms import (
        ChangePasswordForm,
        CreateMembershipProfileForm,
        EmailRequestForm,
        IdentityChangeRequestForm,
        LoginForm,
        MailAccountForm,
        MemberProfileForm,
        MembershipForm,
        RegistrationForm,
        SetPasswordForm,
        TestEmailForm,
    )
    from forum_service import (
        FORUM_AVATAR_STATUS_APPROVED,
        FORUM_AVATAR_STATUS_PENDING,
        FORUM_AVATAR_STATUS_REJECTED,
        FORUM_AVATAR_STATUS_SUPERSEDED,
        FORUM_SETTING_KEYS,
        FORUM_STATE_ACTIVE,
        FORUM_STATE_INACTIVE,
        FORUM_STATE_ONBOARDING,
        FORUM_STATE_SYNC_ERROR,
        ForumProviderError,
        ForumService,
        delete_submission_file,
        format_bytes_human,
        normalize_forum_settings,
    )
    from mail_utils import load_mail_accounts_config, probe_mail_account_connection, send_mail
    from notification_service import (
        ADMIN_ERROR_CHANNEL,
        ADMIN_GENERAL_CHANNEL,
        NOTIFICATION_SETTING_KEYS,
        USER_STATUS_CHANNEL,
        NotificationService,
        normalize_notification_settings,
    )
    from security_utils import build_public_url, is_trusted_host, normalize_public_base_url

# Configuration lives in config.py, a leaf module the service layer can import
# without depending on this one. Re-exported here so existing imports keep working.
from .services.diagnostics import collect_system_health  # noqa: E402
from .services.system_update import describe_update_state  # noqa: E402
from .services.outbox import (  # noqa: E402
    failed_items,
    pending_count,
    process_pending,
)
from .services.identity import (  # noqa: E402
    TOKEN_MAX_AGE_FORUM_ENTRY,
    TOKEN_MAX_AGE_FORUM_ENTRY_AUTO_LOGIN,
    TOKEN_MAX_AGE_PASSWORD_RESET,
    TOKEN_MAX_AGE_VERIFY_EMAIL,
    build_email_verification_claims,
    email_verification_claims_match,
    generate_token,
    mark_email_verified_from_token,
    read_token,
    rotate_email_verification_nonce,
    rotate_password_reset_nonce,
    send_email_verification_email,
    send_password_reset_email,
)
from .services.forum import (  # noqa: E402
    build_forum_username_base,
    generate_unique_forum_username,
    get_forum_service,
    get_forum_settings_map,
    log_out_forum_session_if_possible,
    sync_member_forum_state,
)
from .services.notifications import (  # noqa: E402
    build_mail_accounts_export_payload,
    flush_marked_notification_channels,
    get_db_mail_accounts,
    get_email_template_choices,
    get_notification_service,
    get_notification_settings_map,
    normalize_imported_mail_accounts_payload,
    queue_curated_admin_notification,
    queue_user_status_notification,
)
from .services.workflows import (  # noqa: E402
    process_email_delivery_jobs,
    refresh_member_billing_state,
    send_member_welcome_email,
    sync_member_primary_email,
)
from .services.audit import (  # noqa: E402
    get_recent_audit_logs,
    log_audit_event,
    redact_sensitive_audit_value,
    redact_settings_states_for_audit,
    snapshot_forum_account_for_audit,
    snapshot_forum_avatar_submission_for_audit,
    snapshot_mail_account_for_audit,
    snapshot_member_for_audit,
    snapshot_user_for_audit,
)
from .services.billing import (  # noqa: E402
    apply_runtime_stripe_config,
    backfill_member_coverage_from_subscription,
    backfill_member_stripe_references,
    create_checkout_session_for_member,
    create_invoice_membership_for_member,
    get_member_by_stripe_or_email,
    subscription_has_scheduled_cancellation,
    subscription_period_bounds,
    sync_member_subscription_state_from_subscription,
)
from .services.webhook_inbox import (  # noqa: E402
    STRIPE_EVENT_LEASE,
    claim_stripe_event,
    complete_stripe_event,
    release_stripe_event,
    stripe_event_already_processed,
)
from .services.members import (  # noqa: E402
    DIRECT_MEMBER_PROFILE_FIELDS,
    IDENTITY_MEMBER_FIELDS,
    apply_member_profile,
    normalize_optional_member_value,
)
from .services.settings import (  # noqa: E402
    get_settings_map,
    get_stripe_settings_map,
)
from .services.membership import (  # noqa: E402
    RESUMABLE_MEMBER_STATUSES,
    build_membership_cycle,
    invoice_coverage_year,
    member_has_active_access,
    set_member_membership_window,
    sync_member_active_state,
    update_member_paid_coverage,
)
from .services.clock import (  # noqa: E402
    first_day_of_year,
    get_membership_today,
    get_now_utc,
    last_day_of_year,
    parse_iso_date,
    start_of_day_unix,
    to_membership_date,
)
from .config import (  # noqa: E402
    ADDITIONAL_ALLOWED_HOSTS,
    DB_HOST,
    DB_NAME,
    DB_PASSWORD,
    DB_PORT,
    DB_USER,
    LANGUAGES,
    MAX_CONTENT_LENGTH,
    MESSAGES_POT,
    PACKAGE_DIR,
    PUBLIC_BASE_URL,
    PYBABEL_CONFIG,
    RATELIMIT_ADMIN_EMAIL,
    RATELIMIT_LOGIN,
    RATELIMIT_MEMBERSHIP,
    RATELIMIT_PASSWORD_CHANGE,
    RATELIMIT_REGISTER,
    RATELIMIT_STORAGE_URI,
    REPO_ROOT,
    SECRET_KEY,
    STRIPE_PRICE_ID,
    STRIPE_PUBLISHABLE_KEY,
    STRIPE_SECRET_KEY,
    STRIPE_SETTING_KEYS,
    STRIPE_WEBHOOK_SECRET,
    TRANSLATIONS_DIR,
)

babel = Babel()
login_manager = LoginManager()
csrf = CSRFProtect()
migrate = Migrate()


def get_rate_limit_identity():
    return request.remote_addr or "unknown"


limiter = Limiter(
    key_func=get_rate_limit_identity,
    storage_uri=RATELIMIT_STORAGE_URI,
    default_limits=[],
)


@login_manager.user_loader
def load_user(user_id):
    user = db.session.get(User, int(user_id))
    # An erased account must not keep browsing on a session issued before the
    # erasure. Clearing the password hash stops new logins but says nothing
    # about sessions that already exist, and an expelled member being signed in
    # somewhere else is exactly when that matters. Returning None here ends
    # every one of them at the next request.
    if user is not None and user.deleted_at is not None:
        return None
    return user



def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.has_role("admin"):
            flash(_("You do not have permission to access this page."), "danger")
            return redirect(url_for("public.index"))
        return f(*args, **kwargs)

    return decorated_function




PENDING_SIGNUP_RETENTION_DAYS = int(os.getenv("PENDING_SIGNUP_RETENTION_DAYS", "14"))
# Log retention. 0 means keep forever. Audit logs default to keep-forever because
# they are the account/security trail; higher-churn notification delivery records
# default to a generous one-year window.
AUDIT_LOG_RETENTION_DAYS = int(os.getenv("AUDIT_LOG_RETENTION_DAYS", "0"))
NOTIFICATION_RETENTION_DAYS = int(os.getenv("NOTIFICATION_RETENTION_DAYS", "365"))
ADMIN_DIRECTORY_PAGE_SIZE = 50
AUDIT_LOG_PAGE_SIZE = 50
APPROVAL_HISTORY_PAGE_SIZE = 25


















def static_asset_version(app, filename):
    if not filename:
        return None

    asset_path = Path(app.static_folder) / filename
    try:
        return str(int(asset_path.stat().st_mtime))
    except OSError:
        return None






















































































































def set_setting_value(key, value):
    setting = db.session.get(Setting, key)
    if value is None or value == "":
        if setting is not None:
            db.session.delete(setting)
        return

    normalized_value = str(value)
    if setting is None:
        db.session.add(Setting(key=key, value=normalized_value))
    else:
        setting.value = normalized_value
























































def is_safe_next_url(target):
    if not target:
        return False

    ref_url = urlsplit(request.host_url)
    test_url = urlsplit(urljoin(request.host_url, target))
    return test_url.scheme in {"http", "https"} and ref_url.netloc == test_url.netloc












def create_identity_change_request(member, requested_by_user, form_data):
    if member.open_identity_change_request is not None:
        raise ValueError(_("You already have a pending identity change request."))

    request_record = MemberProfileChangeRequest(
        member=member,
        requested_by=requested_by_user,
        requested_salutation=form_data["salutation"],
        requested_title=normalize_optional_member_value("title", form_data.get("title")),
        requested_first_name=form_data["first_name"],
        requested_last_name=form_data["last_name"],
        requested_year_group=form_data["year_group"],
        member_note=(form_data.get("member_note") or "").strip() or None,
        status="pending",
    )
    db.session.add(request_record)
    return request_record



def has_identity_changes(member, form_data):
    for field_name in IDENTITY_MEMBER_FIELDS:
        current_value = getattr(member, field_name)
        requested_value = normalize_optional_member_value(field_name, form_data.get(field_name))
        if current_value != requested_value:
            return True
    member_note = (form_data.get("member_note") or "").strip()
    return bool(member_note)



def get_role(slug, label=None, description=None):
    role = db.session.execute(db.select(Role).filter_by(slug=slug)).scalar_one_or_none()
    if role is None:
        role = Role(slug=slug, label=label or slug.replace("_", " ").title(), description=description)
        db.session.add(role)
        db.session.flush()
    elif label and role.label != label:
        role.label = label
    if description is not None and role.description != description:
        role.description = description
    return role



def seed_default_roles():
    get_role("admin", label="Admin", description="Can access the admin workspace.")



def count_users_with_role(role_slug):
    return db.session.scalar(
        db.select(func.count()).select_from(User).where(User.roles.any(Role.slug == role_slug))
    ) or 0










































def get_current_member_for_user(user):
    if user is None:
        return None
    return user.member



def can_resume_payment(member):
    if member is None:
        return False
    if member_has_active_access(member):
        return False
    if member.payment_status not in RESUMABLE_MEMBER_STATUSES:
        return False
    return not member.stripe_customer_id



def get_member_portal_target(user):
    if user.has_role("admin"):
        return "admin.admin_dashboard"
    return "account.account"











































def get_portal_session(member):
    if not member or not member.stripe_customer_id:
        raise ValueError(_("No Stripe billing profile is available for this membership yet."))

    refresh_token = int(datetime.now(timezone.utc).timestamp())
    apply_runtime_stripe_config()
    return stripe.billing_portal.Session.create(
        customer=member.stripe_customer_id,
        return_url=build_public_url("account.account", refresh_billing=1, rt=refresh_token),
    )






def backfill_legacy_admin_roles():
    inspector = inspect(db.engine)
    if "users" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("users")}
    if "role" not in columns:
        return

    admin_role = get_role("admin", label="Admin", description="Can access the admin workspace.")
    admin_user_ids = [
        row[0]
        for row in db.session.execute(sql_text("SELECT id FROM users WHERE role = 'admin'"))
    ]
    if not admin_user_ids:
        return

    users = db.session.execute(db.select(User).where(User.id.in_(admin_user_ids))).scalars().all()
    changed = False
    for user in users:
        if not user.has_role("admin"):
            user.grant_role(admin_role)
            changed = True
    if changed:
        db.session.commit()



def backfill_member_user_links():
    changed = False
    members = db.session.execute(db.select(Member).order_by(Member.id.asc())).scalars().all()
    for member in members:
        if member.user is None:
            matched_user = db.session.execute(db.select(User).filter_by(email=member.email_private)).scalar_one_or_none()
            if matched_user is not None:
                member.user = matched_user
                changed = True

        if member.user is not None and not member.user.forum_username:
            member.user.forum_username = generate_unique_forum_username(
                member.first_name,
                member.last_name,
                member.year_group,
                exclude_user_id=member.user.id,
            )
            changed = True

        if member.payment_status in RESUMABLE_MEMBER_STATUSES and member.pending_checkout_started_at is None:
            member.pending_checkout_started_at = member.created_at
            changed = True

    if changed:
        db.session.commit()



def populate_member_profile_form(form, member):
    for field_name in DIRECT_MEMBER_PROFILE_FIELDS:
        getattr(form, field_name).data = getattr(member, field_name)


def populate_identity_change_form(form, member, pending_request=None):
    if pending_request is not None:
        form.salutation.data = pending_request.requested_salutation
        form.title.data = pending_request.requested_title
        form.first_name.data = pending_request.requested_first_name
        form.last_name.data = pending_request.requested_last_name
        form.year_group.data = pending_request.requested_year_group
        form.member_note.data = pending_request.member_note
        return

    form.salutation.data = member.salutation
    form.title.data = member.title
    form.first_name.data = member.first_name
    form.last_name.data = member.last_name
    form.year_group.data = member.year_group


def decorate_pending_identity_requests(requests_):
    for request_record in requests_:
        request_record.current_forum_username = (
            request_record.member.user.forum_username if request_record.member and request_record.member.user else None
        )
        request_record.suggested_forum_username = generate_unique_forum_username(
            request_record.requested_first_name,
            request_record.requested_last_name,
            request_record.requested_year_group,
            exclude_user_id=request_record.member.user.id if request_record.member and request_record.member.user else None,
        )
        request_record.username_would_change = bool(
            request_record.current_forum_username
            and request_record.current_forum_username != request_record.suggested_forum_username
        )
    return requests_


def render_account_dashboard(profile_form=None, identity_form=None):
    member = get_current_member_for_user(current_user)
    if member is None:
        return redirect(url_for("account.create_membership_profile"))

    has_stripe_reference = bool(member.stripe_customer_id or member.stripe_subscription_id)
    if has_stripe_reference:
        try:
            billing_changed, _stripe_subscription, _forum_result = refresh_member_billing_state(member, force_stripe_sync=True, sync_forum=False)
            if billing_changed:
                db.session.commit()
        except stripe.StripeError as exc:
            current_app.logger.warning("Could not refresh Stripe billing state for member_id=%s: %s", member.id, exc)
    elif sync_member_active_state(member):
        db.session.commit()

    pending_request = member.open_identity_change_request
    profile_form = profile_form or MemberProfileForm(prefix="profile")
    identity_form = identity_form or IdentityChangeRequestForm(prefix="identity")

    if not profile_form.is_submitted():
        populate_member_profile_form(profile_form, member)
    if not identity_form.is_submitted():
        populate_identity_change_form(identity_form, member, pending_request=pending_request)

    suggested_username_from_request = None
    if pending_request is not None and member.user is not None:
        suggested_username_from_request = generate_unique_forum_username(
            pending_request.requested_first_name,
            pending_request.requested_last_name,
            pending_request.requested_year_group,
            exclude_user_id=member.user.id,
        )

    forum_context = build_forum_context(member)

    return render_template(
        "account/index.html",
        member=member,
        profile_form=profile_form,
        identity_form=identity_form,
        pending_request=pending_request,
        suggested_username_from_request=suggested_username_from_request,
        can_manage_billing=bool(member.stripe_customer_id),
        can_resume_payment=can_resume_payment(member),
        forum_context=forum_context,
    )


def get_admin_dashboard_metrics():
    return {
        "total_accounts": db.session.scalar(db.select(func.count()).select_from(User)) or 0,
        "linked_members": db.session.scalar(db.select(func.count()).select_from(Member).where(Member.user_id.is_not(None))) or 0,
        "active_memberships": db.session.scalar(db.select(func.count()).select_from(Member).where(Member.is_active.is_(True))) or 0,
        "pending_checkouts": db.session.scalar(db.select(func.count()).select_from(Member).where(Member.payment_status == "pending_checkout")) or 0,
        "pending_identity_requests": db.session.scalar(db.select(func.count()).select_from(MemberProfileChangeRequest).where(MemberProfileChangeRequest.status == "pending")) or 0,
        "cancel_scheduled_memberships": db.session.scalar(db.select(func.count()).select_from(Member).where(Member.cancel_at_period_end.is_(True))) or 0,
        "forum_onboarding_accounts": db.session.scalar(db.select(func.count()).select_from(ForumAccount).where(ForumAccount.state == FORUM_STATE_ONBOARDING)) or 0,
        "forum_active_accounts": db.session.scalar(db.select(func.count()).select_from(ForumAccount).where(ForumAccount.state == FORUM_STATE_ACTIVE)) or 0,
        "forum_sync_errors": db.session.scalar(db.select(func.count()).select_from(ForumAccount).where(ForumAccount.state == FORUM_STATE_SYNC_ERROR)) or 0,
        "pending_forum_avatars": db.session.scalar(db.select(func.count()).select_from(ForumAvatarSubmission).where(ForumAvatarSubmission.status == FORUM_AVATAR_STATUS_PENDING)) or 0,
    }




def build_account_directory_query(search_term, role_filter, membership_filter, active_filter):
    query = (
        db.select(User)
        .options(selectinload(User.member), selectinload(User.roles))
        .outerjoin(Member, Member.user_id == User.id)
    )

    if search_term:
        pattern = f"%{search_term}%"
        query = query.where(
            or_(
                User.email.ilike(pattern),
                User.forum_username.ilike(pattern),
                Member.email_private.ilike(pattern),
                Member.first_name.ilike(pattern),
                Member.last_name.ilike(pattern),
            )
        )

    if role_filter == "admin":
        query = query.where(User.roles.any(Role.slug == "admin"))
    elif role_filter == "member":
        query = query.where(User.member.has(), ~User.roles.any(Role.slug == "admin"))
    elif role_filter == "admin_member":
        query = query.where(User.roles.any(Role.slug == "admin"), User.member.has())
    elif role_filter == "no_membership":
        query = query.where(~User.member.has())

    if membership_filter == "none":
        query = query.where(~User.member.has())
    elif membership_filter == "inactive":
        query = query.where(User.member.has(Member.is_active.is_(False)))
    elif membership_filter != "all":
        query = query.where(User.member.has(Member.payment_status == membership_filter))

    if active_filter == "active":
        query = query.where(User.member.has(Member.is_active.is_(True)))
    elif active_filter == "inactive":
        query = query.where(User.member.has(Member.is_active.is_(False)))

    return query.order_by(User.email.asc()).distinct()


def build_settings_page_context(edit_mail_account_id=None):
    test_email_form = TestEmailForm()
    mail_account_form = MailAccountForm(prefix="mail")
    editing_mail_account = None
    sender_choices = []
    template_choices = get_email_template_choices(current_app._get_current_object())
    mail_account_records = get_db_mail_accounts()
    general_settings = get_settings_map([
        "invoice_payments_enabled",
        "automatic_emails_enabled",
        "welcome_email_sender",
        "automatic_email_template",
    ])
    notification_settings = normalize_notification_settings(get_notification_settings_map())
    forum_settings = normalize_forum_settings(get_forum_settings_map())
    stripe_settings = get_stripe_settings_map()
    forum_service = ForumService(forum_settings)
    notification_service = NotificationService(current_app._get_current_object())
    try:
        mail_accounts = load_mail_accounts_config()
        sender_choices = [(account_key, account_key) for account_key in mail_accounts.keys()]
    except Exception as exc:
        current_app.logger.error(f"Could not load email accounts for admin settings: {exc}")

    if edit_mail_account_id:
        editing_mail_account = db.session.get(MailAccount, edit_mail_account_id)
        if editing_mail_account is not None:
            mail_account_form.mail_account_id.data = str(editing_mail_account.id)
            mail_account_form.account_key.data = editing_mail_account.account_key
            mail_account_form.host.data = editing_mail_account.host
            mail_account_form.port.data = editing_mail_account.port
            mail_account_form.username.data = editing_mail_account.username
            mail_account_form.starttls.data = editing_mail_account.starttls

    test_email_form.sender.choices = sender_choices
    test_email_form.template.choices = template_choices
    return {
        # Version/update state for the Maintenance tab. Same service call the
        # JSON status endpoint uses, so the page and the API cannot disagree.
        "update_state": describe_update_state(),
        "system_health": collect_system_health(),
        "test_email_form": test_email_form,
        "mail_account_form": mail_account_form,
        "mail_account_records": mail_account_records,
        "editing_mail_account": editing_mail_account,
        "sender_choices": sender_choices,
        "template_choices": template_choices,
        "general_settings": general_settings,
        "notification_settings": notification_settings,
        "notification_health": notification_service.get_health_snapshot(),
        "forum_settings": forum_settings,
        "stripe_settings": stripe_settings,
        "forum_service": forum_service,
        "forum_endpoint_urls": {
            "entry": build_public_url("forum.forum_entry"),
            "connect": build_public_url("forum.forum_discourse_connect"),
            "logout": build_public_url("forum.forum_logout"),
        },
        "public_base_url": current_app.config.get("PUBLIC_BASE_URL") or "",
        "forum_provider_choices": [("discourse", _("Discourse"))],
        "forum_auth_strategy_choices": [
            ("discourse_connect", _("DiscourseConnect")),
            ("oauth2_provider", _("OAuth2 Provider (reserved)")),
        ],
    }



def build_forum_context(member):
    service = get_forum_service()
    forum_account = member.user.forum_account if member and member.user else None
    pending_submission = service.get_pending_submission(member) if member else None
    approved_submission = service.get_current_approved_submission(member) if member else None
    latest_submission = service.get_latest_submission(member) if member else None

    status_key = "disabled"
    status_message = _("The forum integration is not enabled yet.")
    can_upload_avatar = False
    can_enter_forum = False

    if member is None or member.user is None:
        status_key = "no_membership"
        status_message = _("A linked membership profile is required before forum access can be prepared.")
    elif not service.is_enabled():
        status_key = "disabled"
        status_message = _("The forum integration is not enabled yet.")
    elif not member_has_active_access(member):
        status_key = "inactive_membership"
        status_message = _("Your forum access is currently unavailable because your membership is not active.")
    elif approved_submission is not None:
        status_key = "active"
        status_message = _("Your forum access is ready.")
        can_enter_forum = service.is_ready()
    elif pending_submission is not None:
        status_key = "pending_avatar"
        status_message = _("Your profile picture is under review. You will get full forum access as soon as it is approved.")
        can_upload_avatar = True
    elif latest_submission is not None and latest_submission.status == FORUM_AVATAR_STATUS_REJECTED:
        status_key = "rejected_avatar"
        status_message = _("Your profile picture was rejected. Please upload a new one to continue.")
        can_upload_avatar = True
    else:
        status_key = "needs_avatar"
        status_message = _("Upload a profile picture to continue with forum onboarding.")
        can_upload_avatar = True

    avatar_max_bytes = service.settings["forum_avatar_max_bytes"]
    avatar_upload_request_limit = service.get_upload_request_limit()
    return {
        "service": service,
        "forum_account": forum_account,
        "pending_submission": pending_submission,
        "approved_submission": approved_submission,
        "latest_submission": latest_submission,
        "status_key": status_key,
        "status_message": status_message,
        "can_upload_avatar": can_upload_avatar,
        "can_enter_forum": can_enter_forum,
        "entry_url": url_for("forum.forum_entry"),
        "forum_error": forum_account.last_error if forum_account is not None else None,
        "avatar_max_bytes": avatar_max_bytes,
        "avatar_max_bytes_display": format_bytes_human(avatar_max_bytes),
        "avatar_upload_request_limit": avatar_upload_request_limit,
        "avatar_upload_request_limit_display": format_bytes_human(avatar_upload_request_limit),
    }


def create_app(config_overrides=None):
    app = Flask(__name__)

    @app.context_processor
    def inject_language_switcher():
        def switch_lang_url(lang):
            endpoint = request.endpoint or "public.index"
            values = dict(request.view_args or {})
            values.update(request.args.to_dict(flat=True))
            values["lang"] = lang
            try:
                return url_for(endpoint, **values)
            except BuildError:
                fallback_values = request.args.to_dict(flat=True)
                fallback_values["lang"] = lang
                return url_for("public.index", **fallback_values)

        return dict(switch_lang_url=switch_lang_url)

    app.config["SECRET_KEY"] = SECRET_KEY
    # A DATABASE_URL override lets tests (and alternative deployments) point at a
    # different backend such as SQLite without touching the MySQL defaults.
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    else:
        app.config["SQLALCHEMY_DATABASE_URI"] = (
            f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["STRIPE_PUBLISHABLE_KEY"] = STRIPE_PUBLISHABLE_KEY
    app.config["STRIPE_SECRET_KEY"] = STRIPE_SECRET_KEY
    app.config["STRIPE_PRICE_ID"] = STRIPE_PRICE_ID
    app.config["PUBLIC_BASE_URL"] = PUBLIC_BASE_URL
    app.config["ADDITIONAL_ALLOWED_HOSTS"] = ADDITIONAL_ALLOWED_HOSTS
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    )

    app.config["BABEL_DEFAULT_LOCALE"] = "en"
    app.config["BABEL_SUPPORTED_LOCALES"] = LANGUAGES
    app.config["BABEL_DEFAULT_TIMEZONE"] = "UTC"

    def select_locale():
        lang = request.args.get("lang")
        if lang in app.config["BABEL_SUPPORTED_LOCALES"]:
            return lang
        return request.accept_languages.best_match(app.config["BABEL_SUPPORTED_LOCALES"])

    if config_overrides:
        app.config.update(config_overrides)

    db.init_app(app)
    # Anchor the migrations directory to the repo root so Alembic commands work
    # regardless of the process working directory (e.g. when invoked from
    # install.sh / systemd rather than a shell sitting in the checkout).
    migrate.init_app(app, db, directory=str(REPO_ROOT / "migrations"))
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    babel.init_app(app, locale_selector=select_locale)
    csrf.init_app(app)
    limiter.init_app(app)

    # Blueprints are imported here (deferred) so their modules can import helpers
    # from this fully-initialized module without a circular import.
    from .blueprints.account import account_bp
    from .blueprints.admin import admin_bp, admin_dashboard
    from .blueprints.auth import auth_bp
    from .blueprints.forum import forum_bp
    from .blueprints.public import public_bp
    from .blueprints.webhook import webhook_bp

    csrf.exempt(webhook_bp)
    app.register_blueprint(webhook_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(forum_bp)
    app.register_blueprint(admin_bp)

    @app.context_processor
    def inject_babel_globals():
        cleaned_args = {}
        try:
            if request.args is not None:
                cleaned_args = {key: value for key, value in request.args.items() if key != "lang"}
        except Exception:
            pass

        return dict(
            babel=babel,
            get_locale=get_locale,
            cleaned_args=cleaned_args,
        )

    @app.template_filter("redact_audit_payload")
    def redact_audit_payload_filter(value):
        return redact_sensitive_audit_value(value)

    @app.url_defaults
    def add_static_file_version(endpoint, values):
        if endpoint != "static":
            return
        if "v" in values:
            return

        version = static_asset_version(app, values.get("filename"))
        if version:
            values["v"] = version

    @app.before_request
    def validate_request_host():
        host = (request.host or "").split(":", 1)[0]
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        if not is_trusted_host(host):
            abort(400)

    @app.after_request
    def disable_dynamic_page_caching(response):
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            response.headers["Surrogate-Control"] = "no-store"
            response.vary.add("Cookie")
        flush_marked_notification_channels()
        return response

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1, x_proto=1)
    stripe.api_key = STRIPE_SECRET_KEY

    @app.cli.command("db-init")
    @with_appcontext
    def db_init():
        """Bring the database schema to the latest Alembic revision, then seed roles.

        Two situations, both safe to run on every deploy:

        * Fresh database -> run migrations to build the full schema.
        * Already migrated -> apply any pending migrations.

        A third case used to exist: a database created before migrations, which
        was reconciled column by column and then stamped at head. That is gone
        deliberately. Stamping records migrations as applied without running
        them, which is only truthful while every migration merely adds tables
        and columns -- ``create_all`` cannot reproduce one that transforms data,
        and the coverage-ledger migration backfills rows. A database stamped
        past that would look migrated while holding none of the evidence.

        Since no such database will be migrated into this system -- members
        re-subscribe through the new portal instead -- the branch has no
        legitimate user left, only the chance to silently skip a migration on a
        database restored from a partial backup. It now refuses and says so.
        """
        from flask_migrate import upgrade

        click.echo("Preparing database schema...")
        try:
            inspector = inspect(db.engine)
            existing_tables = set(inspector.get_table_names())

            if "users" in existing_tables and "alembic_version" not in existing_tables:
                click.echo(
                    "This database has application tables but no Alembic version "
                    "record, so there is no way to tell which migrations it has "
                    "already had.\n"
                    "Refusing to guess: stamping it would mark every migration as "
                    "applied without running any of them.\n"
                    "If this is an empty or throwaway database, drop it and run "
                    "db-init again. If it holds data you need, restore it into a "
                    "staging copy and migrate that first.",
                    err=True,
                )
                sys.exit(1)

            upgrade()

            seed_default_roles()
            backfill_legacy_admin_roles()
            backfill_member_user_links()
            db.session.commit()
            click.echo("Database schema is ready.")
        except Exception as e:
            click.echo(f"Error preparing database: {e}", err=True)
            sys.exit(1)

    @app.cli.command("i18n-init")
    def i18n_init():
        """Initializes or updates the translation files."""
        run(
            [
                "pybabel",
                "extract",
                "-F",
                str(PYBABEL_CONFIG),
                "-o",
                str(MESSAGES_POT),
                str(PACKAGE_DIR),
            ],
            check=True,
        )
        run(
            [
                "pybabel",
                "update",
                "-i",
                str(MESSAGES_POT),
                "-d",
                str(TRANSLATIONS_DIR),
            ],
            check=True,
        )
        if MESSAGES_POT.exists():
            MESSAGES_POT.unlink()
        click.echo("Translation files updated.")

    @app.cli.command("create-admin")
    @click.argument("email")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    @with_appcontext
    def create_admin(email, password):
        """Creates or promotes an admin user."""
        normalized_email = (email or "").strip().lower()
        seed_default_roles()
        admin_role = get_role("admin", label="Admin", description="Can access the admin workspace.")
        user = db.session.execute(db.select(User).filter_by(email=normalized_email)).scalar_one_or_none()
        created = user is None

        if created:
            user = User(email=normalized_email)
            db.session.add(user)

        before_user = snapshot_user_for_audit(user)
        user.grant_role(admin_role)
        user.set_password(password)
        db.session.flush()
        log_audit_event(
            category="access",
            event_type="admin_role_granted",
            actor_user=None,
            target_user=user,
            target_member=user.member,
            before=before_user,
            after=snapshot_user_for_audit(user),
            metadata={"granted_role": "admin", "source": "create_admin_cli", "created_user": created},
        )
        db.session.commit()

        if created:
            click.echo(click.style(f"Created admin user: {normalized_email}", fg="green"))
        else:
            click.echo(click.style(f"Granted admin access to: {normalized_email}", fg="green"))

    @app.cli.command("sync-member-billing")
    @click.argument("email")
    @with_appcontext
    def sync_member_billing(email):
        """Refreshes one member's billing state from Stripe and prints the result."""
        normalized_email = (email or "").strip().lower()
        member = db.session.execute(db.select(Member).filter_by(email_private=normalized_email)).scalar_one_or_none()
        if member is None:
            click.echo(click.style(f"No member found for {normalized_email}", fg="red"), err=True)
            sys.exit(1)

        try:
            changed, stripe_subscription, forum_result = refresh_member_billing_state(member, force_stripe_sync=True, sync_forum=True)
        except stripe.StripeError as exc:
            click.echo(click.style(f"Stripe sync failed: {exc}", fg="red"), err=True)
            sys.exit(1)
        if not (member.stripe_customer_id or member.stripe_subscription_id):
            click.echo(click.style("Member has no Stripe customer or subscription reference yet; nothing to sync from Stripe.", fg="yellow"))

        if changed:
            db.session.commit()
            flush_marked_notification_channels()
        else:
            db.session.rollback()

        click.echo(click.style(f"Member: {member.email_private}", fg="green"))
        click.echo(f"  payment_status: {member.payment_status}")
        click.echo(f"  is_active: {member.is_active}")
        click.echo(f"  cancel_at_period_end: {member.cancel_at_period_end}")
        click.echo(f"  stripe_customer_id: {member.stripe_customer_id or '-'}")
        click.echo(f"  stripe_subscription_id: {member.stripe_subscription_id or '-'}")
        click.echo(f"  membership_ends_on: {member.membership_ends_on or '-'}")
        click.echo(f"  renewal_due_on: {member.renewal_due_on or '-'}")
        if stripe_subscription is not None:
            cancellation_details = stripe_subscription.get("cancellation_details") or {}
            click.echo(f"  stripe_status: {stripe_subscription.get('status') or '-'}")
            click.echo(f"  stripe_cancel_at_period_end: {stripe_subscription.get('cancel_at_period_end')}")
            click.echo(f"  stripe_cancel_at: {stripe_subscription.get('cancel_at') or '-'}")
            click.echo(f"  stripe_canceled_at: {stripe_subscription.get('canceled_at') or '-'}")
            click.echo(f"  stripe_trial_end: {stripe_subscription.get('trial_end') or '-'}")
            _period_start, period_end = subscription_period_bounds(stripe_subscription)
            click.echo(f"  stripe_current_period_end: {period_end or '-'}")
            click.echo(f"  stripe_cancellation_reason: {cancellation_details.get('reason') or '-'}")
            click.echo(f"  derived_cancel_scheduled: {subscription_has_scheduled_cancellation(stripe_subscription)}")
        if member.user and member.user.forum_account:
            click.echo(f"  forum_state: {member.user.forum_account.state}")
            click.echo(f"  forum_last_error: {member.user.forum_account.last_error or '-'}")
        if forum_result is not None:
            click.echo(f"  forum_desired_state: {forum_result.desired_state or '-'}")
            click.echo(f"  forum_sync_error: {forum_result.error or '-'}")
        click.echo(f"  changed: {changed}")


    @app.cli.command("reconcile-billing")
    @click.option("--all", "reconcile_all", is_flag=True, help="Synchronize all Stripe-linked members instead of only higher-risk records.")
    @click.option("--lookahead-days", default=3, show_default=True, type=int)
    @with_appcontext
    def reconcile_billing(reconcile_all, lookahead_days):
        """Reconciles local billing state with Stripe for Stripe-managed memberships."""
        today = get_membership_today()
        cutoff = today + timedelta(days=max(0, lookahead_days))
        stripe_linked_filter = or_(Member.stripe_customer_id.is_not(None), Member.stripe_subscription_id.is_not(None))
        query = db.select(Member.id).where(stripe_linked_filter)
        if not reconcile_all:
            risky_statuses = tuple(sorted(RESUMABLE_MEMBER_STATUSES | {"cancel_scheduled", "canceled", "failed", "processing", "unpaid"}))
            query = query.where(
                or_(
                    Member.payment_status.in_(risky_statuses),
                    Member.cancel_at_period_end.is_(True),
                    Member.membership_ends_on.is_(None),
                    Member.renewal_due_on.is_(None),
                    Member.membership_ends_on <= cutoff,
                )
            )

        member_ids = db.session.execute(query.order_by(Member.id.asc())).scalars().all()
        changed_count = 0
        unchanged_count = 0
        error_count = 0
        forum_warning_count = 0

        for member_id in member_ids:
            member = db.session.get(Member, member_id)
            if member is None:
                continue
            try:
                changed, _stripe_subscription, forum_result = refresh_member_billing_state(member, force_stripe_sync=True, sync_forum=True, on_date=today)
                if changed:
                    db.session.commit()
                    flush_marked_notification_channels()
                    changed_count += 1
                else:
                    db.session.rollback()
                    unchanged_count += 1
                if forum_result and forum_result.error:
                    forum_warning_count += 1
                    click.echo(click.style(f"Forum sync warning for {member.email_private}: {forum_result.error}", fg="yellow"))
            except stripe.StripeError as exc:
                db.session.rollback()
                queue_curated_admin_notification(
                    ADMIN_ERROR_CHANNEL,
                    "billing_reconcile_failed",
                    _("Billing reconciliation failed for %(email)s.", email=member.email_private),
                    payload={
                        "member_email": member.email_private,
                        "member_id": member.id,
                        "error": str(exc),
                    },
                    target_user=member.user,
                    target_member=member,
                    object_type="member",
                    object_id=member.id,
                    commit=True,
                )
                error_count += 1
                click.echo(click.style(f"Stripe sync failed for {member.email_private}: {exc}", fg="red"), err=True)
            except Exception as exc:
                db.session.rollback()
                queue_curated_admin_notification(
                    ADMIN_ERROR_CHANNEL,
                    "billing_reconcile_failed",
                    _("Billing reconciliation failed for %(email)s.", email=member.email_private),
                    payload={
                        "member_email": member.email_private,
                        "member_id": member.id,
                        "error": str(exc),
                    },
                    target_user=member.user,
                    target_member=member,
                    object_type="member",
                    object_id=member.id,
                    commit=True,
                )
                error_count += 1
                click.echo(click.style(f"Billing reconciliation failed for {member.email_private}: {exc}", fg="red"), err=True)

        summary_color = "green" if error_count == 0 else "yellow"
        click.echo(click.style(
            f"Processed {len(member_ids)} Stripe-linked membership(s). Changed: {changed_count}. Unchanged: {unchanged_count}. Errors: {error_count}. Forum warnings: {forum_warning_count}.",
            fg=summary_color,
        ))
        if error_count:
            sys.exit(1)

    @app.cli.command("sync-member-forum")
    @click.argument("email")
    @with_appcontext
    def sync_member_forum(email):
        """Refreshes one member's forum state and prints the result."""
        normalized_email = (email or "").strip().lower()
        member = db.session.execute(db.select(Member).filter_by(email_private=normalized_email)).scalar_one_or_none()
        if member is None:
            click.echo(click.style(f"No member found for {normalized_email}", fg="red"), err=True)
            sys.exit(1)

        result, service = sync_member_forum_state(member)
        if result and result.changed:
            db.session.commit()
            flush_marked_notification_channels()
        else:
            db.session.rollback()

        click.echo(click.style(f"Member: {member.email_private}", fg="green"))
        click.echo(f"  forum_enabled: {service.is_enabled()}")
        click.echo(f"  forum_ready: {service.is_ready()}")
        click.echo(f"  forum_desired_state: {result.desired_state if result else '-'}")
        if member.user and member.user.forum_account:
            click.echo(f"  forum_state: {member.user.forum_account.state}")
            click.echo(f"  forum_last_synced_at: {member.user.forum_account.last_synced_at or '-'}")
            click.echo(f"  forum_last_error: {member.user.forum_account.last_error or '-'}")
        latest_submission = service.get_latest_submission(member)
        if latest_submission is not None:
            click.echo(f"  latest_avatar_status: {latest_submission.status}")
            click.echo(f"  latest_avatar_reviewed_at: {latest_submission.reviewed_at or '-'}")
        click.echo(f"  changed: {bool(result and result.changed)}")
        if result and result.error:
            sys.exit(1)

    @app.cli.command("sync-forum-members")
    @click.option("--only-active", is_flag=True, help="Only synchronize active members.")
    @with_appcontext
    def sync_forum_members(only_active):
        """Synchronizes forum state for many linked members."""
        query = db.select(Member).where(Member.user_id.is_not(None))
        if only_active:
            query = query.where(Member.is_active.is_(True))
        members = db.session.execute(query.order_by(Member.id.asc())).scalars().all()

        changed_count = 0
        error_count = 0
        for member in members:
            result, _service = sync_member_forum_state(member)
            if result and result.changed:
                changed_count += 1
            if result and result.error:
                error_count += 1
        db.session.commit()
        flush_marked_notification_channels()
        click.echo(click.style(f"Synchronized {len(members)} member(s). Changed: {changed_count}. Errors: {error_count}.", fg="green" if error_count == 0 else "yellow"))

    @app.cli.command("deliver-notifications")
    @with_appcontext
    def deliver_notifications_command():
        """Delivers queued admin and user notification emails."""
        email_summary = process_email_delivery_jobs(app)
        db.session.commit()
        summary = get_notification_service().deliver_pending_notifications()
        click.echo(
            click.style(
                "Notification delivery summary: "
                f"queued email retries processed={email_summary.get('processed', 0)}, "
                f"email retries sent={email_summary.get('sent', 0)}, "
                f"email retries exhausted={email_summary.get('exhausted', 0)}, "
                f"sent batches={summary.get('sent_batches', 0)}, "
                f"failed batches={summary.get('failed_batches', 0)}, "
                f"sent events={summary.get('sent_events', 0)}, "
                f"failed events={summary.get('failed_events', 0)}, "
                f"deferred channels={', '.join(summary.get('deferred_channels', [])) or '-' }.",
                fg="green" if summary.get("failed_batches", 0) == 0 else "yellow",
            )
        )
        if summary.get("failed_batches", 0):
            sys.exit(1)

    @app.cli.command("cleanup-pending-signups")
    @click.option("--days", default=PENDING_SIGNUP_RETENTION_DAYS, show_default=True, type=int)
    @with_appcontext
    def cleanup_pending_signups(days):
        """Deletes stale pending signups that never completed Checkout."""
        cutoff = get_now_utc() - timedelta(days=days)
        stale_members = db.session.execute(
            db.select(Member)
            .filter(Member.payment_status == "pending_checkout")
            .filter(Member.is_active.is_(False))
            .filter(Member.pending_checkout_started_at.is_not(None))
            .filter(Member.pending_checkout_started_at < cutoff)
            .filter(Member.stripe_customer_id.is_(None))
            .filter(Member.stripe_subscription_id.is_(None))
        ).scalars().all()

        deleted_count = 0
        for member in stale_members:
            user = member.user
            db.session.delete(member)
            if user is not None and not user.roles:
                db.session.delete(user)
            deleted_count += 1

        if deleted_count:
            db.session.commit()
        click.echo(click.style(f"Deleted {deleted_count} stale pending signup(s).", fg="green"))

    @app.cli.command("system-check")
    @with_appcontext
    def system_check():
        """Report whether this installation is healthy.

        The same facts the admin Maintenance tab shows, for anyone who does have
        shell access.
        """
        health = collect_system_health()

        click.echo("Schema:")
        schema = health["schema"]
        click.echo(f"  applied revision : {schema['applied'] or 'unknown'}")
        click.echo(f"  expected revision: {schema['expected'] or 'unknown'}")
        click.echo(f"  up to date       : {'yes' if schema['up_to_date'] else 'NO'}")

        click.echo("Membership:")
        for key, value in health["membership"].items():
            click.echo(f"  {key.replace('_', ' '):26s}: {value}")

        click.echo("Background work:")
        for key, value in health["queues"].items():
            click.echo(f"  {key.replace('_', ' '):26s}: {value}")

        for problem in health["problems"]:
            click.echo(f"PROBLEM: {problem}", err=True)
        for warning in health["warnings"]:
            click.echo(f"WARNING: {warning}")

        if health["healthy"]:
            click.echo("Everything looks healthy.")
        else:
            sys.exit(1)

    @app.cli.command("process-external-work")
    @click.option("--limit", default=50, show_default=True, type=int,
                  help="Maximum number of work items to process in this run.")
    @with_appcontext
    def process_external_work(limit):
        """Perform queued work against other systems (currently Discourse sync).

        Items are claimed under a lease, so running this while another copy is
        already running is safe -- a second worker simply finds nothing to claim.
        """
        completed, failed = process_pending(limit=limit)
        outstanding = pending_count()
        click.echo(f"External work: {completed} completed, {failed} failed, {outstanding} still queued.")

        stuck = failed_items(limit=10)
        if stuck:
            click.echo("Items that exhausted their retries:")
            for item in stuck:
                click.echo(f"  #{item.id} {item.kind} member_id={item.member_id}: {item.last_error}")

    @app.cli.command("cleanup-logs")
    @click.option("--audit-days", default=AUDIT_LOG_RETENTION_DAYS, show_default=True, type=int,
                  help="Delete audit logs older than this many days. 0 keeps them forever.")
    @click.option("--notification-days", default=NOTIFICATION_RETENTION_DAYS, show_default=True, type=int,
                  help="Delete notification records older than this many days. 0 keeps them forever.")
    @with_appcontext
    def cleanup_logs(audit_days, notification_days):
        """Prunes old audit logs and notification delivery records.

        Retention is generous by design: pass 0 for either window to keep those
        records forever. Only truly old rows are removed, so recent history is
        always preserved.
        """
        audit_deleted = 0
        if audit_days > 0:
            cutoff = get_now_utc() - timedelta(days=audit_days)
            audit_deleted = db.session.query(AuditLog).filter(AuditLog.created_at < cutoff).delete(synchronize_session=False)

        events_deleted = 0
        batches_deleted = 0
        if notification_days > 0:
            cutoff = get_now_utc() - timedelta(days=notification_days)
            # Delete old events first, then only batches that no longer have events
            # so the foreign key from events to batches is never violated.
            events_deleted = db.session.query(NotificationEvent).filter(
                NotificationEvent.queued_at < cutoff
            ).delete(synchronize_session=False)
            batches_deleted = db.session.query(NotificationBatch).filter(
                NotificationBatch.created_at < cutoff,
                ~NotificationBatch.events.any(),
            ).delete(synchronize_session=False)

        db.session.commit()
        click.echo(click.style(
            f"Deleted {audit_deleted} audit log(s), {events_deleted} notification event(s), "
            f"{batches_deleted} notification batch(es).",
            fg="green",
        ))




























    app.add_url_rule("/admin", endpoint="admin", view_func=admin_dashboard, methods=["GET"])






























    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        flash(_("Your session has expired or the form is invalid. Please try submitting again."), "warning")
        return redirect(request.referrer or url_for("public.index"))

    @app.errorhandler(RateLimitExceeded)
    def handle_rate_limit_error(e):
        # Rendered, not redirected. Browsers do not follow a Location header on
        # a 429, so returning a redirect left the member on an unstyled
        # "Redirecting..." page that never went anywhere and explained nothing.
        return render_template("429.html"), 429

    @app.errorhandler(413)
    def request_entity_too_large(e):
        flash(_("The submitted data is too large to process. Please reduce the file size and try again."), "danger")
        if request.path == url_for("forum.upload_forum_avatar"):
            return redirect(url_for("forum.forum_entry"))
        return redirect(request.referrer or url_for("public.index"))

    @app.errorhandler(404)
    def page_not_found(e):
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def internal_server_error(e):
        app.logger.error(f"Internal Server Error: {e}")
        db.session.rollback()
        queue_curated_admin_notification(
            ADMIN_ERROR_CHANNEL,
            "internal_server_error",
            _("An internal server error occurred while processing %(path)s.", path=request.path),
            payload={
                "path": request.path,
                "method": request.method,
                "endpoint": request.endpoint,
                "error": str(e),
            },
            target_user=current_user._get_current_object() if current_user.is_authenticated else None,
            target_member=current_user.member if current_user.is_authenticated else None,
            severity="critical",
            commit=True,
        )
        return render_template("500.html"), 500

    @app.cli.command("send-welcome-email")
    @with_appcontext
    @click.argument("email")
    def send_welcome_email_command(email):
        """Manually sends a welcome email to a member by their private email address."""
        member = Member.query.filter_by(email_private=email).first()
        if not member:
            click.echo(click.style(f"Error: No member found with email '{email}'.", fg="red"))
            return

        click.echo(f"Found member: {member.first_name} {member.last_name}. Preparing to send email...")

        try:
            with app.test_request_context():
                success = send_member_welcome_email(app, member, force_send=True)
                if success:
                    click.echo(click.style(f"Successfully sent welcome email to {email}.", fg="green"))
                else:
                    click.echo(click.style(f"Failed to send welcome email to {email}. Check logs for details.", fg="red"))
        except Exception as e:
            click.echo(click.style(f"An unexpected error occurred: {e}", fg="red"))

    return app


if __name__ == "__main__":
    create_app().run(debug=False)





























