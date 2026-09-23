import getpass
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
    has_request_context,
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
        ImportedForumProfile,
        MailAccount,
        Member,
        MemberProfileChangeRequest,
        NotificationBatch,
        NotificationEvent,
        ProcessedStripeEvent,
        ROLE_ADMIN,
        ROLE_SUPERADMIN,
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
        ImportedForumProfile,
        MailAccount,
        Member,
        MemberProfileChangeRequest,
        NotificationBatch,
        NotificationEvent,
        ProcessedStripeEvent,
        ROLE_ADMIN,
        ROLE_SUPERADMIN,
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
from .services.institutional_email import (  # noqa: E402
    SETTING_KEY as INSTITUTIONAL_EMAIL_SETTING_KEY,
    get_institutional_domains,
)
from .member_categories import (  # noqa: E402
    CATEGORY_ORDER,
    categories_showing_year_group,
    category_label,
    requires_year_group,
)
from .permissions import (  # noqa: E402
    Permission,
    ROLE_PERMISSIONS,
    role_description,
    role_label,
    roles_with,
)
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
    list_undelivered_emails,
    normalize_imported_mail_accounts_payload,
    queue_curated_admin_notification,
    queue_user_status_notification,
)
from .services.forum_import import (  # noqa: E402
    import_forum_people,
    load_people,
)
from .services.forum_profiles import (  # noqa: E402
    YEAR_GROUP_FIELD_NAME,
    publish_imported_profiles,
)
from .services.forum_content import (  # noqa: E402
    LOOSEN,
    REFUSE,
    ContentPoster,
    check_site_settings,
    dates_survived,
    loosen_site_settings,
    migrate_thread,
    plan_site_settings,
    read_settings_journal,
    rehearsal_threads,
    restore_site_settings,
    what_to_do_about_settings,
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
    # Same reasoning for a switched-off account, and the same urgency: an
    # account is usually disabled *because* somebody should stop using it now,
    # and leaving their open sessions alive would mean waiting for a logout
    # that may never come.
    if user is not None and user.is_disabled:
        return None
    return user



def requires(*permissions):
    """Gate a route on capabilities rather than on who somebody is.

    ``@requires(Permission.SYSTEM_UPDATE)`` says what the route does; which
    roles carry that is permissions.py's business alone. A role added there
    reaches every route its bundle lists without one of them being edited,
    which is the whole point -- the alternative is finding each route a new
    role should reach and hoping none is missed.

    The interface hides what it will not offer, but hiding a link is not access
    control: the URL is still there to be typed, and somebody who had the role
    yesterday knows it. This is the check that decides. Somebody who may reach
    the admin workspace at all is sent back to the dashboard rather than to the
    public page, because they are legitimately signed in here.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if current_user.is_authenticated and all(
                current_user.can(permission) for permission in permissions
            ):
                return f(*args, **kwargs)

            flash(_("You do not have permission to access this page."), "danger")
            if current_user.is_authenticated and current_user.can(Permission.ADMIN_ACCESS):
                return redirect(url_for("admin.admin_dashboard"))
            return redirect(url_for("public.index"))

        return decorated_function

    return decorator




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
        requested_member_category=form_data["member_category"],
        requested_year_group=normalize_optional_member_value("year_group", form_data.get("year_group")),
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
    """Create a row for every role the permission table defines.

    Driven off ROLE_PERMISSIONS so adding a role there is genuinely the only
    edit: the row appears on the next start, ready to be granted.
    """
    for slug in ROLE_PERMISSIONS:
        get_role(slug, label=role_label(slug), description=role_description(slug))



def count_users_with_permission(permission, active_only=True):
    """How many accounts could still do this, whatever role gives it to them.

    The lockout guards ask this rather than counting super admins, so a role
    added to permissions.py with SYSTEM_UPDATE starts counting towards "somebody
    can still install an update" without the guards being touched.
    """
    slugs = roles_with(permission)
    if not slugs:
        return 0
    query = db.select(func.count()).select_from(User).where(User.roles.any(Role.slug.in_(slugs)))
    if active_only:
        query = query.where(User.deleted_at.is_(None))
    return db.session.scalar(query) or 0










































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
    # Where signing in lands you. A capability, not a role name: an account
    # holding only super admin was being sent to the member page.
    if user.can(Permission.ADMIN_ACCESS):
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
        form.member_category.data = pending_request.requested_member_category
        form.member_note.data = pending_request.member_note
        return

    form.salutation.data = member.salutation
    form.title.data = member.title
    form.first_name.data = member.first_name
    form.last_name.data = member.last_name
    form.year_group.data = member.year_group
    form.member_category.data = member.member_category


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
    profile_form.member_category_value = member.member_category
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
    # Erased rows stay -- they are the payment record -- but they are not people
    # the association has any more, so they are excluded here exactly as they
    # are in the health report. Counting them made the dashboard disagree with
    # the Maintenance panel by the number of erasures, and any future import of
    # non-member accounts would widen that gap until neither number meant
    # anything.
    present_user = User.deleted_at.is_(None)
    present_member = Member.deleted_at.is_(None)
    return {
        # Portal accounts only. Counting the archive here would say the
        # association has 760 accounts when it has twenty, and this number is
        # read as "how many people use this".
        #
        # Somebody who has reconnected counts, though: they signed up, they
        # pay, they are here. Excluding them on the grounds that they were once
        # imported would leave this figure hundreds short after an intake, and
        # permanently.
        "total_accounts": db.session.scalar(
            db.select(func.count()).select_from(User).where(
                present_user,
                ~User.imported_forum_profile.has(
                    ImportedForumProfile.claimed_at.is_(None)
                ),
            )
        ) or 0,
        "archived_forum_accounts": db.session.scalar(
            db.select(func.count()).select_from(ImportedForumProfile)
        ) or 0,
        "archived_forum_claimed": db.session.scalar(
            db.select(func.count()).select_from(ImportedForumProfile).where(
                ImportedForumProfile.claimed_at.is_not(None)
            )
        ) or 0,
        "linked_members": db.session.scalar(
            db.select(func.count()).select_from(Member).where(present_member, Member.user_id.is_not(None))
        ) or 0,
        "active_memberships": db.session.scalar(db.select(func.count()).select_from(Member).where(present_member, Member.is_active.is_(True))) or 0,
        "pending_checkouts": db.session.scalar(db.select(func.count()).select_from(Member).where(present_member, Member.payment_status == "pending_checkout")) or 0,
        "pending_identity_requests": db.session.scalar(db.select(func.count()).select_from(MemberProfileChangeRequest).where(MemberProfileChangeRequest.status == "pending")) or 0,
        "cancel_scheduled_memberships": db.session.scalar(db.select(func.count()).select_from(Member).where(present_member, Member.cancel_at_period_end.is_(True))) or 0,
        "forum_onboarding_accounts": db.session.scalar(db.select(func.count()).select_from(ForumAccount).where(ForumAccount.state == FORUM_STATE_ONBOARDING)) or 0,
        "forum_active_accounts": db.session.scalar(db.select(func.count()).select_from(ForumAccount).where(ForumAccount.state == FORUM_STATE_ACTIVE)) or 0,
        "forum_sync_errors": db.session.scalar(db.select(func.count()).select_from(ForumAccount).where(ForumAccount.state == FORUM_STATE_SYNC_ERROR)) or 0,
        "pending_forum_avatars": db.session.scalar(db.select(func.count()).select_from(ForumAvatarSubmission).where(ForumAvatarSubmission.status == FORUM_AVATAR_STATUS_PENDING)) or 0,
    }




def build_account_directory_query(
    search_term, role_filter, membership_filter, active_filter,
    kind_filter="all", account_filter="all",
):
    query = (
        db.select(User)
        .options(
            selectinload(User.member),
            selectinload(User.roles),
            selectinload(User.imported_forum_profile),
        )
        .outerjoin(Member, Member.user_id == User.id)
        .outerjoin(ImportedForumProfile, ImportedForumProfile.user_id == User.id)
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
                # A person carried over from the old forum has no name and a
                # placeholder address, so the only things worth searching them
                # by are what the archive recorded.
                ImportedForumProfile.display_name.ilike(pattern),
                ImportedForumProfile.source_email.ilike(pattern),
            )
        )

    # Former forum people live in this same list -- a former member is a member
    # the association still has a record of, and reconnecting one is the same
    # action as anything else done from an account page. This only narrows it.
    #
    # "Archived" means still only an archive: a profile nobody has claimed.
    # Once somebody comes back they are an ordinary account that happens to
    # carry its history, and filing them under "from the old forum" for the
    # next decade would be describing where they came from rather than what
    # they are.
    unclaimed = User.imported_forum_profile.has(ImportedForumProfile.claimed_at.is_(None))
    if kind_filter == "archived":
        query = query.where(unclaimed)
    elif kind_filter == "portal":
        query = query.where(~unclaimed)

    # "Who can administer" is a capability question, not a role-name one. Asking
    # for Role.slug == "admin" would file an account holding only a future role
    # under "member only", and would have to be edited every time a role is
    # added -- which is the thing permissions.py exists to avoid.
    staff_roles = roles_with(Permission.ADMIN_ACCESS)
    if role_filter == "staff":
        query = query.where(User.roles.any(Role.slug.in_(staff_roles)))
    elif role_filter.startswith("role:"):
        query = query.where(User.roles.any(Role.slug == role_filter.split(":", 1)[1]))
    elif role_filter == "member":
        query = query.where(User.member.has(), ~User.roles.any(Role.slug.in_(staff_roles)))
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

    # The account's own state, which is a different question from the
    # membership's and filtered separately for that reason.
    if account_filter == "active":
        query = query.where(
            User.disabled_at.is_(None),
            User.deleted_at.is_(None),
            User.password_hash.is_not(None),
        )
    elif account_filter == "disabled":
        query = query.where(User.disabled_at.is_not(None))
    elif account_filter == "no_sign_in":
        query = query.where(User.password_hash.is_(None), User.deleted_at.is_(None))

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
        INSTITUTIONAL_EMAIL_SETTING_KEY,
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
        # The health report counts undelivered emails; this is what an admin
        # needs to actually resolve one -- who it was for, and why it failed.
        "undelivered_emails": list_undelivered_emails(),
        "test_email_form": test_email_form,
        "mail_account_form": mail_account_form,
        "mail_account_records": mail_account_records,
        "editing_mail_account": editing_mail_account,
        "sender_choices": sender_choices,
        "template_choices": template_choices,
        "general_settings": general_settings,
        # The list actually in force, which is not the same as the stored text
        # when the box is empty and the built-in default applies.
        "institutional_email_domains": get_institutional_domains(),
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
    # The avatar a returning student already has: imported from the old forum,
    # published to their profile, and theirs since long before they signed up
    # here. It is not a ForumAvatarSubmission -- nobody submitted it for review
    # -- so nothing else in this function would notice it.
    reclaimed_profile = (
        member.user.imported_forum_profile
        if member and member.user is not None
        else None
    )
    reclaimed_avatar = (
        reclaimed_profile.avatar_path
        if reclaimed_profile is not None and reclaimed_profile.claimed_at is not None
        else None
    )

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
    elif member.user.is_disabled:
        # Checked before the membership, because it is the stronger statement:
        # a switched-off account stays out whether or not the subscription is
        # paid up, and saying "your membership is not active" to somebody who
        # has paid for the year would simply be untrue.
        status_key = "account_disabled"
        status_message = _("Your account has been deactivated, so forum access is not available.")
    elif not member_has_active_access(member):
        status_key = "inactive_membership"
        status_message = _("Your forum access is currently unavailable because your membership is not active.")
    elif approved_submission is not None:
        status_key = "active"
        status_message = _("Your forum access is ready.")
        can_enter_forum = service.is_ready()
    elif reclaimed_avatar is not None:
        # They came back to an account that already has a face on it -- the one
        # they uploaded to the old forum, which is live on their profile right
        # now. Asking them to upload a picture would be asking them to redo
        # something already done, as the first thing they are told.
        status_key = "active"
        status_message = _("Your forum access is ready, with the profile picture from the old forum.")
        can_enter_forum = service.is_ready()
        can_upload_avatar = True  # still free to replace it
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
        # Babel calls this for every _() -- including from the notification
        # timer, the CLI commands and the webhook worker, where there is no
        # request to read a language from. Touching `request` there raised
        # "Working outside of request context" and took the whole pass down
        # with it, which is a strange way for a translated log line to fail.
        if not has_request_context():
            return app.config["BABEL_DEFAULT_LOCALE"]
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

    @app.context_processor
    def inject_member_category_rules():
        """Hands the year group rules to the page so JavaScript need not know them.

        Rendered into data attributes and read back by member-kind-toggle.js,
        which keeps member_categories.py the only place the rule is written
        down.
        """
        return dict(
            year_group_categories=" ".join(categories_showing_year_group()),
            year_group_required_categories=" ".join(
                category for category in CATEGORY_ORDER if requires_year_group(category)
            ),
            member_category_label=category_label,
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
    @click.option(
        "--superadmin/--no-superadmin",
        default=None,
        help="Force the super administrator role on or off. The default takes it "
             "only when the installation has no super administrator yet.",
    )
    @with_appcontext
    def create_admin(email, password, superadmin):
        """Creates or promotes an admin user.

        On a fresh installation this is the bootstrap: with no super
        administrator in the database, the first account created here takes that
        role, because otherwise nobody could ever install an update. On an
        installation that already has one, it creates an ordinary administrator
        and the existing super administrator decides whether to promote them.
        """
        normalized_email = (email or "").strip().lower()
        seed_default_roles()
        admin_role = get_role("admin", label="Admin", description="Can access the admin workspace.")
        superadmin_role = get_role(ROLE_SUPERADMIN)
        user = db.session.execute(db.select(User).filter_by(email=normalized_email)).scalar_one_or_none()
        created = user is None

        if superadmin is None:
            # The bootstrap: with nobody able to install an update, the first
            # account created here has to be able to, or nothing ever can.
            superadmin = count_users_with_permission(Permission.SYSTEM_UPDATE) == 0

        if created:
            user = User(email=normalized_email)
            db.session.add(user)

        before_user = snapshot_user_for_audit(user)
        user.grant_role(admin_role)
        if superadmin:
            user.grant_role(superadmin_role)
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
            metadata={
                "granted_role": ROLE_SUPERADMIN if superadmin else ROLE_ADMIN,
                "source": "create_admin_cli",
                "created_user": created,
            },
        )
        db.session.commit()

        what = "super admin" if superadmin else "admin"
        if created:
            click.echo(click.style(f"Created {what} user: {normalized_email}", fg="green"))
        else:
            click.echo(click.style(f"Granted {what} access to: {normalized_email}", fg="green"))

    @app.cli.command("grant-superadmin")
    @click.argument("email")
    @with_appcontext
    def grant_superadmin(email):
        """Grants super administrator access to an existing account.

        The recovery path, for when the last super administrator leaves the
        association or erases their account. It needs shell access on the
        server, which is the right bar: anyone with that can already read this
        database and change this code, so the command hands out nothing they
        could not take anyway.
        """
        normalized_email = (email or "").strip().lower()
        seed_default_roles()
        user = db.session.execute(db.select(User).filter_by(email=normalized_email)).scalar_one_or_none()
        if user is None:
            click.echo(click.style(f"No account found for {normalized_email}", fg="red"), err=True)
            sys.exit(1)
        if user.deleted_at is not None:
            click.echo(
                click.style(
                    f"{normalized_email} was erased and cannot sign in; create a new account instead.",
                    fg="red",
                ),
                err=True,
            )
            sys.exit(1)
        if user.has_role(ROLE_SUPERADMIN):
            click.echo(f"{normalized_email} is already a super admin.")
            return

        before_user = snapshot_user_for_audit(user)
        user.grant_role(get_role(ROLE_ADMIN))
        user.grant_role(get_role(ROLE_SUPERADMIN))
        db.session.flush()
        log_audit_event(
            category="access",
            event_type="superadmin_role_granted",
            actor_user=None,
            target_user=user,
            target_member=user.member,
            before=before_user,
            after=snapshot_user_for_audit(user),
            metadata={"granted_role": ROLE_SUPERADMIN, "source": "grant_superadmin_cli"},
        )
        db.session.commit()
        click.echo(click.style(f"Granted super admin access to: {normalized_email}", fg="green"))

    @app.cli.command("import-forum-people")
    @click.argument("export_file", type=click.Path(exists=True, dir_okay=False))
    # Deliberately not click.Path(exists=True): this command is run as the
    # application user against a directory somebody unpacked as root, and a
    # folder inside /root is unreadable rather than absent. Click cannot tell
    # the difference and reports "does not exist" about a directory that
    # plainly does, which sends people looking for the wrong problem.
    @click.option("--avatar-dir", type=click.Path(file_okay=False),
                  help="Directory holding the old forum's avatar files.")
    @click.option("--dry-run", is_flag=True,
                  help="Report what would happen and write nothing.")
    @click.option("--year-groups", is_flag=True,
                  help="List every year group found, and everyone left without one.")
    @click.option("--sample", type=int, default=0, metavar="N",
                  help="Show N people in full, spread across the import, to check "
                       "the result looks right before running it for real.")
    @with_appcontext
    def import_forum_people_command(export_file, avatar_dir, dry_run, year_groups, sample):
        """Imports people from the old forum's export. See docs/forum-import.md.

        Safe to run more than once: people are matched on the old forum's own
        user id, so a second run updates rather than duplicates. Run it with
        --dry-run first; the report is the same either way.
        """
        if avatar_dir:
            _check_avatar_dir_is_readable(avatar_dir)

        people = load_people(export_file)
        report = import_forum_people(people, avatar_dir=avatar_dir, dry_run=dry_run)

        if dry_run:
            db.session.rollback()
        else:
            db.session.commit()

        click.echo(
            f"seen={report['seen']} created={report['created']} "
            f"updated={report['updated']} skipped={report['skipped']} "
            f"avatars={report['avatars_stored']} "
            f"year_groups_derived={report['year_groups_derived']}"
        )
        if report["year_groups_disagreeing"]:
            click.echo(
                f"  {report['year_groups_disagreeing']} people have a year group that "
                f"differs from the one their username spells; the exported field was "
                f"kept (usually somebody who went on to the master's)."
            )
        if year_groups:
            _echo_year_groups(report)
        else:
            unplaced = len(report["unknown_year_group"])
            click.echo(
                f"{len(report['year_group_counts'])} distinct year groups; "
                f"{unplaced} people without one. Re-run with --year-groups to list them."
            )

        _echo_reclaim_outlook(report)
        if sample:
            _echo_sample(report, sample)

        for problem in report["problems"]:
            click.echo(click.style(f"  ! {problem}", fg="yellow"), err=True)
        if dry_run:
            click.echo(click.style("Dry run: nothing was written.", fg="cyan"))
        elif report["created"] or report["updated"]:
            click.echo(click.style("Imported.", fg="green"))

    def _check_avatar_dir_is_readable(avatar_dir):
        """Fail before importing 740 people with none of their avatars.

        Says which of the three things is actually wrong, because they look
        identical from the error Click would otherwise give and lead to three
        different fixes.
        """
        path = Path(avatar_dir)
        whoami = getpass.getuser()

        if not path.exists():
            # Either genuinely absent, or somewhere this user cannot traverse.
            # A parent that cannot be entered is the common case on a server,
            # and is not what "does not exist" leads somebody to check.
            unreadable_parent = next(
                (
                    parent for parent in path.parents
                    if parent.exists() and not os.access(parent, os.R_OK | os.X_OK)
                ),
                None,
            )
            if unreadable_parent is not None:
                raise click.ClickException(
                    f"{avatar_dir} cannot be reached as {whoami}: {unreadable_parent} "
                    f"is not readable by that user. Move the avatars somewhere it can "
                    f"read, such as /var/tmp, rather than running this as root -- "
                    f"avatars written by root are files the application cannot manage."
                )
            raise click.ClickException(f"No such directory: {avatar_dir}")

        if not path.is_dir():
            raise click.ClickException(f"Not a directory: {avatar_dir}")

        if not os.access(path, os.R_OK | os.X_OK):
            raise click.ClickException(
                f"{avatar_dir} is not readable by {whoami}. Fix the permissions "
                f"rather than running this as root."
            )

        # An empty directory imports every person with no picture and reports
        # 677 missing files, which reads as data loss rather than a wrong path.
        if not any(path.iterdir()):
            raise click.ClickException(
                f"{avatar_dir} is empty. Point --avatar-dir at the directory that "
                f"holds the avatar files themselves."
            )

    def _load_mybb_dump(dump_file):
        """The old board's tables, read once, with the prefix resolved."""
        import sys
        from pathlib import Path as _Path

        sys.path.insert(0, str(_Path(app.root_path).parent / "scripts"))
        from mybb_export import find_prefix, find_table, read_dump, rows_of  # noqa: E402

        dump = read_dump(dump_file)
        # With the prefix, so "users" cannot match mybb_tapatalk_users -- a
        # plugin table with no uid column, which silently made every post
        # anonymous rather than failing.
        prefix, _rejected = find_prefix(dump)
        return {
            name: list(rows_of(dump, find_table(dump, name, prefix)))
            for name in ("posts", "threads", "attachments", "users")
        }

    def _report_site_settings(rows):
        """Print the settings check.

        Returns (nothing_would_be_refused, there_is_something_to_change). A
        setting that is merely unwanted -- mail going out, titles being
        rewritten -- is reported and changed, but is not a reason to stop. Only
        the ones that make Discourse refuse a post are.
        """
        wrong = [row for row in rows if row["ok"] is False]
        blocking = [row for row in wrong if row.get("blocks", True)]
        unreadable = [row for row in rows if row["ok"] is None]

        click.echo(f"\n{'setting':<30} {'now':<22} {'needs to be':<22} ")
        for row in rows:
            mark = {True: "ok", False: "CHANGE", None: "?"}[row["ok"]]
            if row["ok"] is False and not row.get("blocks", True):
                mark = "change (not fatal)"
            needed = row["needed"]
            if row["compare"] == "at_most":
                needed = f"{needed} or less"
            elif row["compare"] == "at_least":
                needed = f"{needed} or more"
            elif row["compare"] == "includes":
                needed = f"also allow {needed}"
            colour = {True: "green", False: "red", None: "yellow"}[row["ok"]]
            click.echo(
                click.style(
                    f"{row['setting']:<30} {str(row['now'])[:20]:<22} {str(needed)[:20]:<22} {mark}",
                    fg=colour,
                )
            )

        if not wrong and not unreadable:
            click.echo(click.style("\nThe forum will accept this archive.", fg="green"))
            return True, False

        click.echo("")
        for row in wrong + unreadable:
            click.echo(f"  {row['setting']}: {row['why']}")
        click.echo(
            "\nRun the import with --adjust-settings and it will change these "
            "itself and put them back when it is done. By hand it is Admin -> "
            "Settings, where the search box takes the name verbatim, and then "
            "remembering the 'now' column afterwards: these are loosened for "
            "the import only, and leaving min_post_length at 2 or duplicate "
            "titles allowed for ever is not what this forum wants."
        )
        if not blocking and not unreadable:
            click.echo(click.style(
                "\nNone of these would make the forum refuse a post, so the "
                "import can run without them. It would go better with them.",
                fg="yellow",
            ))
            return True, True
        return False, True

    def _warn_about_the_key(poster):
        """Say so early if the key is the wrong shape.

        Otherwise this surfaces much later as a 404 on an admin route, which
        is what Discourse answers when it does not recognise a key -- the
        request becomes anonymous, and admin routes are hidden rather than
        refused. It reads as a missing feature.
        """
        complaint = poster._key_complaint()
        if complaint:
            click.echo(click.style(f"Warning: {complaint}.", fg="yellow"), err=True)

    def _settings_journal_path(given=None):
        """Where the record of what was changed goes."""
        from datetime import datetime as _datetime

        if given:
            return Path(given)
        stamp = _datetime.now().strftime("%Y%m%d-%H%M%S")
        return Path.cwd() / f"forum-settings-{stamp}.json"

    def _report_restore(results):
        for row in results:
            colour = "green" if row["outcome"] == "restored" else "yellow"
            click.echo(click.style(
                f"  {row['setting']:<30} {str(row['set_to'])[:18]:<20} -> "
                f"{str(row['was'])[:18]:<20} {row['outcome']}",
                fg=colour,
            ))

    @app.cli.command("restore-forum-settings")
    @click.argument("journal", type=click.Path(exists=True, dir_okay=False))
    @click.option("--force", is_flag=True,
                  help="Restore even settings somebody has changed since.")
    @click.option("--api-key", envvar="DISCOURSE_MIGRATION_API_KEY",
                  help="Reads DISCOURSE_MIGRATION_API_KEY if not given.")
    @with_appcontext
    def restore_forum_settings_command(journal, force, api_key):
        """Puts the forum's settings back after an import.

        The import does this itself when it finishes. This is for when it did
        not finish: a dropped connection, a full disk, somebody's Ctrl-C. The
        file it takes is the one the import wrote before it changed anything,
        so the forum can be put right by somebody who was not there.

        A setting that no longer holds the value the import gave it was changed
        by somebody else since, and is left alone unless you pass --force.
        """
        service = get_forum_service()
        if not service.is_enabled() or service.config_errors:
            raise click.ClickException("The forum integration is not configured.")

        try:
            payload = read_settings_journal(journal)
        except (ValueError, json.JSONDecodeError) as exc:
            raise click.ClickException(f"{journal}: {exc}") from exc

        settings = dict(service.settings)
        if api_key:
            settings["discourse_api_key"] = api_key
        poster = ContentPoster(settings)

        if payload.get("forum") and payload["forum"] != poster.base_url:
            raise click.ClickException(
                f"That file was written for {payload['forum']}, and this is "
                f"{poster.base_url}. Restoring one forum's settings onto "
                f"another would be a mess to unpick."
            )

        click.echo(f"Written {payload.get('written_at', 'at an unknown time')}.")
        try:
            results = restore_site_settings(poster, payload["changes"], force=force)
        except ForumProviderError as exc:
            raise click.ClickException(f"Could not restore: {exc}") from exc
        _report_restore(results)

        stuck = [row for row in results if row["outcome"].startswith("left alone")]
        if stuck:
            click.echo(click.style(
                f"\n{len(stuck)} settings were left alone. Look at them, and "
                f"use --force if the import's value is the one to undo.",
                fg="yellow",
            ))
        else:
            click.echo(click.style("\nThe forum is back as it was.", fg="green"))

    @app.cli.command("check-forum-settings")
    @click.argument("dump_file", type=click.Path(exists=True, dir_okay=False))
    @click.option("--api-key", envvar="DISCOURSE_MIGRATION_API_KEY",
                  help="Reads DISCOURSE_MIGRATION_API_KEY if not given.")
    @with_appcontext
    def check_forum_settings_command(dump_file, api_key):
        """Will the forum accept the old board? Asks it before anything is posted.

        Discourse's defaults are written for somebody typing into a box today:
        a title has to be 15 characters, a post 20, and two threads may not
        share a name. The archive does not look like that -- half its subjects
        are shorter than 15 characters and ninety-one of them are called
        "Klausuren" -- so the import would be refused post by post, hours in.

        This measures the archive, asks the forum what it currently allows, and
        prints what to change. The 'now' column is what to put back afterwards.
        """
        service = get_forum_service()
        if not service.is_enabled() or service.config_errors:
            raise click.ClickException("The forum integration is not configured.")

        tables = _load_mybb_dump(dump_file)
        requirements = plan_site_settings(
            tables["threads"], tables["posts"], tables["attachments"]
        )
        settings = dict(service.settings)
        if api_key:
            settings["discourse_api_key"] = api_key

        total_bytes = sum(int(row.get("filesize") or 0) for row in tables["attachments"])
        click.echo(
            f"{len(tables['threads'])} threads, {len(tables['posts'])} posts, "
            f"{len(tables['attachments'])} attachments "
            f"({total_bytes / 1024 ** 3:.1f} GB -- the forum needs room for that "
            f"on top of what it already holds)."
        )
        poster = ContentPoster(settings)
        _warn_about_the_key(poster)
        try:
            rows = check_site_settings(poster, requirements)
        except ForumProviderError as exc:
            raise click.ClickException(
                f"Could not read the forum's settings: {exc}"
            ) from exc
        ready, _anything = _report_site_settings(rows)

        # A rehearsal is only worth running on a thread that exercises the
        # things that break, and there is no way to pick one by eye out of 720.
        candidates = rehearsal_threads(
            tables["threads"], tables["posts"], tables["attachments"]
        )
        if candidates:
            click.echo("\nThreads worth rehearsing with (--thread), hardest first:")
            click.echo(f"  {'tid':<8} {'posts':>5} {'people':>7} {'files':>6}  subject")
            for row in candidates:
                click.echo(
                    f"  {row['tid']:<8} {row['posts']:>5} {row['authors']:>7} "
                    f"{row['attachments']:>6}  {row['subject'][:44]}"
                )

        if not ready:
            raise SystemExit(1)

    @app.cli.command("migrate-forum-thread")
    @click.argument("dump_file", type=click.Path(exists=True, dir_okay=False))
    @click.option("--thread", required=True, help="The old forum's thread id (tid).")
    @click.option("--uploads", required=True, type=click.Path(file_okay=False),
                  help="The old forum's uploads folder, holding the .attach files.")
    @click.option("--category", type=int, help="Discourse category id to post into.")
    @click.option("--dry-run", is_flag=True, help="Show what would be posted and send nothing.")
    @click.option("--adjust-settings", is_flag=True,
                  help="Loosen the settings this needs, and put them back after.")
    @click.option("--settings-file", type=click.Path(dir_okay=False),
                  help="Where to write the record of what was changed.")
    @click.option("--api-key", envvar="DISCOURSE_MIGRATION_API_KEY",
                  help="An 'All Users' Discourse API key. Reads "
                       "DISCOURSE_MIGRATION_API_KEY if not given, which keeps it "
                       "out of the shell history and out of ps.")
    @with_appcontext
    def migrate_forum_thread_command(dump_file, thread, uploads, category, dry_run,
                                     adjust_settings, settings_file, api_key):
        """Moves ONE old thread onto the forum, to find out whether it can be.

        A rehearsal for the content migration, not the migration. It answers
        the two questions the real importer depends on and cannot be reasoned
        out: whether Discourse keeps the dates it is given when posting on
        somebody else's behalf, and whether an imported person -- never signed
        in, trust level 0, unreachable address -- is allowed to post at all.

        Not idempotent. Running it twice posts the thread twice.
        """
        service = get_forum_service()
        if not service.is_enabled() or service.config_errors:
            raise click.ClickException("The forum integration is not configured.")
        if not dry_run and not category:
            raise click.ClickException("--category is required for a real run.")

        tables = _load_mybb_dump(dump_file)
        posts = [row for row in tables["posts"] if row.get("tid") == str(thread)]
        if not posts:
            raise click.ClickException(f"No posts found for thread {thread}.")
        posts.sort(key=lambda row: int(row.get("dateline") or 0))

        threads = {row["tid"]: row for row in tables["threads"]}
        this_thread = threads.get(str(thread), {"tid": thread})
        pids = {row.get("pid") for row in posts}
        attachments_by_post = {}
        for row in tables["attachments"]:
            attachments_by_post.setdefault(row.get("pid"), []).append(row)
        # The name the old forum knew each author by is the name they have here,
        # because that is exactly what the profile import published.
        usernames_by_uid = {
            row.get("uid"): row.get("username") for row in tables["users"]
        }

        # Posting as each author needs a key Discourse will let act as anybody.
        # The portal's own key is deliberately not that: it only ever acts as
        # one user, and widening it would leave something able to impersonate
        # every member of the forum running all year for the sake of an
        # afternoon's migration.
        settings = dict(service.settings)
        if api_key:
            settings["discourse_api_key"] = api_key
        poster = ContentPoster(settings)
        _warn_about_the_key(poster)

        # Ask the forum whether it will take this thread before posting any of
        # it. A run that gets three posts in and is then refused for a title
        # two characters too short leaves half a thread behind, and this spike
        # is not idempotent.
        requirements = plan_site_settings(
            [this_thread], posts,
            [row for pid in pids for row in attachments_by_post.get(pid, [])],
        )
        try:
            ready, anything = _report_site_settings(
                check_site_settings(poster, requirements)
            )
        except ForumProviderError as exc:
            # Not every key can read the settings. Worth saying, not worth
            # refusing over: the run itself will still report what happened.
            click.echo(click.style(f"Could not read the site settings: {exc}", fg="yellow"))
            ready, anything = True, False

        changes = []
        journal = None
        decision = what_to_do_about_settings(
            ready=ready, anything=anything,
            adjust_settings=adjust_settings, dry_run=dry_run,
        )
        if dry_run and anything:
            click.echo(click.style(
                "Nothing is sent by a dry run, so none of the above stops it. "
                "It is what the real run will need.", fg="cyan",
            ))
        if decision == LOOSEN:
            journal = _settings_journal_path(settings_file)
            # Said before anything is touched rather than after, because the
            # run that most needs this printed is the one that never gets as
            # far as printing anything else.
            click.echo(click.style(
                f"\nLoosening the settings above. What they were goes in\n"
                f"  {journal}\n"
                f"If this run does not finish, put them back with:\n"
                f"  flask restore-forum-settings {journal}",
                fg="cyan",
            ))
            changes = loosen_site_settings(poster, requirements, journal)
            click.echo(f"Changed {len(changes)} settings.")
        elif decision == REFUSE:
            raise click.ClickException(
                "The forum would refuse part of this thread. Run it again with "
                "--adjust-settings to have it change these itself and put them "
                "back, or change them by hand first."
            )

        try:
            report = migrate_thread(
                poster, this_thread, posts,
                attachments_by_post, usernames_by_uid, uploads, category,
                dry_run=dry_run,
                fallback_username=service.settings["discourse_api_username"],
            )
        finally:
            if changes:
                click.echo("\nPutting the settings back:")
                try:
                    _report_restore(restore_site_settings(poster, changes))
                except ForumProviderError as exc:
                    # Never swallowed: a forum left wide open has to be said
                    # out loud, with the one command that fixes it.
                    click.echo(click.style(
                        f"COULD NOT RESTORE THE SETTINGS: {exc}\n"
                        f"The forum is still loosened. Run:\n"
                        f"  flask restore-forum-settings {journal}",
                        fg="red",
                    ), err=True)

        click.echo(f"\n{report['thread']}")
        click.echo(f"{'date asked for':<12} {'recorded':<12} {'author':<22} files  result")
        for person in report["posts"]:
            click.echo(
                f"{(person['asked_for'] or '')[:10]:<12} "
                f"{(person['recorded'] or '-')[:10]:<12} "
                f"{(person['author'] or '?'):<22} {person['attachments']:>5}  {person['result']}"
            )

        kept, explanation = dates_survived(report)
        click.echo("")
        if kept is True:
            click.echo(click.style(f"Dates survived: {explanation}", fg="green"))
        elif kept is False:
            click.echo(click.style(f"Dates did NOT survive: {explanation}", fg="red"))
            click.echo("The importer needs a different shape; nothing else here matters yet.")
        else:
            click.echo(click.style(f"Undetermined: {explanation}", fg="yellow"))

        for problem in report["problems"][:20]:
            click.echo(click.style(f"  ! {problem}", fg="yellow"), err=True)
        if report.get("topic_id"):
            click.echo(f"\nTopic {report['topic_id']} — go and look at it.")

    @app.cli.command("publish-forum-profiles")
    @click.option("--dry-run", is_flag=True,
                  help="Report what would be published and send nothing.")
    @click.option("--limit", type=int, default=0, metavar="N",
                  help="Publish at most N people, for a first careful run.")
    @click.option("--only-new", is_flag=True,
                  help="Skip people already published, to resume an interrupted run.")
    @click.option("--sample", type=int, default=0, metavar="N",
                  help="Show N of them in full.")
    @with_appcontext
    def publish_forum_profiles_command(dry_run, limit, only_new, sample):
        """Publishes imported people to the forum so they can be found there.

        The old board is the association's register of everyone who was ever a
        member, and people use it to find somebody from an earlier cohort. This
        recreates that: a profile per imported person, with their name, year
        group and the face they had, grouped by cohort.

        These are not usable accounts. Nobody can sign in to one.
        """
        service = get_forum_service()
        if not service.is_enabled():
            raise click.ClickException(
                "The forum integration is switched off, so there is nowhere to "
                "publish to. Turn it on in the admin settings first."
            )
        # config_errors is empty for a disabled integration, so it is not on its
        # own enough to know the settings are usable.
        if service.config_errors:
            raise click.ClickException(
                "The forum integration is not fully configured: "
                + "; ".join(service.config_errors)
            )
        provider = service.provider
        if provider is None:
            raise click.ClickException("No forum provider is configured.")

        # The profile field is a nicety: it puts the year group on the profile
        # page. The cohort groups are the mechanism, and they do not depend on
        # it -- so a forum that will not hand over its user fields is a reason
        # to say so and carry on, not to publish nobody.
        year_group_field = None
        try:
            if dry_run:
                # Read-only: says whether the field is there without making one.
                year_group_field = provider.find_user_field(YEAR_GROUP_FIELD_NAME)
                click.echo(
                    f"Year group field: {year_group_field or 'not present, would be created'}"
                )
            else:
                year_group_field, created = provider.ensure_user_field(
                    YEAR_GROUP_FIELD_NAME, "Which year group they studied with."
                )
                click.echo(
                    f"Year group field: {year_group_field}"
                    f"{' (created)' if created else ''}"
                )
        except ForumProviderError as exc:
            click.echo(click.style(f"Year group field: unavailable -- {exc}", fg="yellow"))
            click.echo(
                "Publishing without it. Names, avatars and cohort groups are "
                "unaffected, and re-running later fills the field in."
            )

        report = publish_imported_profiles(
            provider,
            dry_run=dry_run,
            limit=limit or None,
            only_unsynced=only_new,
            year_group_field=year_group_field,
        )

        if not dry_run:
            # After the people, so a group is only made for a cohort that has
            # somebody in it.
            for group in sorted(report["groups"]):
                try:
                    _info, created = provider.ensure_group(group)
                    if created:
                        click.echo(f"  created group {group}")
                except Exception as exc:  # noqa: BLE001 -- one group must not end the run
                    click.echo(click.style(f"  ! group {group}: {exc}", fg="yellow"), err=True)
            db.session.commit()

        click.echo(
            f"seen={report['seen']} published={report['published']} "
            f"failed={report['failed']} with_avatar={report['with_avatar']}"
        )
        click.echo(f"{len(report['groups'])} groups: "
                   + ", ".join(f"{name} ({count})"
                               for name, count in sorted(report["groups"].items()))[:400])
        if sample:
            _echo_profile_sample(report, sample)
        for problem in report["problems"][:20]:
            click.echo(click.style(f"  ! {problem}", fg="yellow"), err=True)
        if dry_run:
            click.echo(click.style("Dry run: nothing was sent.", fg="cyan"))

    def _echo_profile_sample(report, wanted):
        people = report.get("people") or []
        if not people:
            return
        wanted = max(1, min(wanted, len(people)))
        step = len(people) / wanted
        click.echo(f"\nA sample of {wanted}, spread across the run:")
        for index in range(wanted):
            person = people[int(index * step)]
            click.echo(f"  {person['username']}  ({person['result']})")
            click.echo(f"      shown as   : {person['name']}")
            click.echo(f"      year group : {person['year_group'] or '-'}")
            click.echo(f"      groups     : {person['groups']}")
            click.echo(f"      avatar     : {person['avatar']}")

    def _echo_reclaim_outlook(report):
        """How many can get their old account back without asking anybody.

        Worth knowing before the import rather than in October: everyone in the
        second number is somebody who will have to be linked up by hand, and
        that is a size worth seeing while there is still time to do something
        about it.
        """
        people = report.get("people") or []
        if not people:
            return
        blocked = [person for person in people if person["can_reclaim"] != "yes"]
        click.echo(
            f"{len(people) - len(blocked)} of {len(people)} can reclaim their account "
            f"from their university address alone."
        )
        if not blocked:
            return
        reasons = {}
        for person in blocked:
            reasons[person["note"]] = reasons.get(person["note"], 0) + 1
        for reason, count in sorted(reasons.items(), key=lambda item: -item[1]):
            click.echo(f"    {count:4}  {reason}")
        click.echo("    These need linking by hand if they come back.")

    def _echo_sample(report, wanted):
        """A few people in full, spread across the import.

        Spread rather than random, and rather than the first N: the export is
        ordered by the old forum's user id, so the first rows are all from 2014
        and would show nothing about how the recent cohorts turn out. Spreading
        it also makes the rehearsal and the real run show the same people, which
        is what makes them comparable.
        """
        people = report.get("people") or []
        if not people:
            return
        wanted = max(1, min(wanted, len(people)))
        step = len(people) / wanted
        chosen = [people[int(index * step)] for index in range(wanted)]

        click.echo(f"\nA sample of {len(chosen)}, spread across the import:")
        for person in chosen:
            year_group = person["year_group"] or "-"
            if person["year_group"] and person["year_group_from"]:
                year_group += f" (from the {person['year_group_from']})"
            reclaim = person["can_reclaim"]
            if person["note"]:
                reclaim += f"  -- {person['note']}"

            click.echo(f"  {person['source_username']}  ({person['action'] or 'no change'})")
            click.echo(f"      year group : {year_group}")
            click.echo(f"      address    : {person['source_email'] or '-'}")
            click.echo(f"      avatar     : {person['avatar']}")
            click.echo(f"      posts      : {person['post_count']}"
                       f"    old group: {person['source_group'] or '-'}")
            click.echo(f"      can reclaim: {reclaim}")

    def _echo_year_groups(report):
        """The year groups as a table, then everyone the rules could not place.

        Sorted by name rather than by count, because the reason to read this is
        to spot the one that looks wrong -- a typo sorts next to the value it
        was meant to be, where by frequency it would sit alone at the bottom.
        """
        counts = report["year_group_counts"]
        if counts:
            widest = max(len(name) for name in counts)
            total = sum(counts.values())
            click.echo(f"\nYear groups ({len(counts)} distinct, {total} people):")
            for name in sorted(counts):
                count = counts[name]
                bar = "#" * count
                click.echo(f"  {name:<{widest}}  {count:>4}  {bar}")

        unplaced = report["unknown_year_group"]
        if not unplaced:
            click.echo("\nEveryone has a year group.")
            return

        click.echo(click.style(
            f"\n{len(unplaced)} people with no year group "
            f"(nothing in the export's field, nothing readable in the username):",
            fg="yellow",
        ))
        for person in sorted(unplaced, key=lambda p: (p["joined_on"] or "", p["source_username"])):
            joined = (person["joined_on"] or "")[:4] or "????"
            click.echo(
                f"  uid {person['source_user_id']:>5}  "
                f"{person['source_username']:<24}  registered {joined}"
            )
        click.echo(
            "\nTo fix any of these, set year_group on that person in the export "
            "JSON and run the import again -- it updates rather than duplicates."
        )

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





























