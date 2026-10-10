import csv
import getpass
import hashlib
import json
import os
import secrets
import sys
import time
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps
from pathlib import Path
from subprocess import run
from urllib.parse import quote, quote_plus, urljoin, urlsplit
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
from sqlalchemy import and_, case, func, inspect, or_, text as sql_text
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
        MembershipPeriod,
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
        CreateMembershipProfileForm,
        IdentityChangeRequestForm,
        MemberProfileForm,
        MembershipForm,
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
        member_category_groups,
        normalize_forum_settings,
        UPLOAD_IMAGE_FORMATS,
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
        MembershipPeriod,
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
        CreateMembershipProfileForm,
        IdentityChangeRequestForm,
        MemberProfileForm,
        MembershipForm,
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
        member_category_groups,
        normalize_forum_settings,
        UPLOAD_IMAGE_FORMATS,
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
    members_whose_forum_state_has_drifted,
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
    old_forum_account_waiting,
    load_people,
)
from .services.forum_profiles import (  # noqa: E402
    ARCHIVE_GROUP,
    USERNAME_LENGTH_SETTING,
    YEAR_GROUP_FIELD_NAME,
    avatar_setting_state,
    groups_for_profiles,
    let_avatars_through,
    let_the_portal_own_address_and_name,
    make_room_for_usernames,
    portal_owned_settings_state,
    profiles_to_publish,
    publish_imported_profiles,
    sync_profile_groups,
    username_length_state,
    username_room_needed,
)
from .services.forum_mapping import (  # noqa: E402
    ARCHIVE_ROOT,
    mapping_plan,
    read_mapping,
    titles_for,
)
from .services.forum_board import (  # noqa: E402
    DEEPEST_CATEGORY_NESTING,
    MAX_CATEGORY_NESTING,
    Ledger,
    _sortable,
    audit_uploads,
    category_nesting_requirement,
    category_plan,
    category_worksheet,
    ledger_describes,
    migrate_board,
    settings_inventory,
)
from .services.forum_permissions import (  # noqa: E402
    STAFF_GROUP,
    access_groups,
    apply_permissions,
    describe,
    groups_wanted,
    let_authors_post,
    owned_roots,
    permission_plan,
    what_is_not_set_up,
)
from .services.portal_settings import export_settings, import_settings  # noqa: E402
from .services.forum_worksheet import render_worksheet  # noqa: E402
from .services.forum_content import (  # noqa: E402
    LOOSEN,
    REFUSE,
    ContentPoster,
    bbcode_to_markdown,
    check_site_settings,
    dates_survived,
    loosen_site_settings,
    mark_journal_restored,
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
    LIVE_SUBSCRIPTION_STATUSES,
    can_rejoin,
    checkout_completed_but_not_yet_confirmed,
    create_checkout_session_for_member,
    create_invoice_membership_for_member,
    get_member_by_stripe_or_email,
    subscription_has_scheduled_cancellation,
    subscription_period_bounds,
    sync_member_subscription_state_from_subscription,
)
from .services.signup import invoice_payments_allowed  # noqa: E402
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
    set_setting_value,
)
from .services.membership import (  # noqa: E402
    RESUMABLE_MEMBER_STATUSES,
    build_membership_cycle,
    format_date_display,
    format_datetime_display,
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
    TEST_SERVER,
    TRANSLATIONS_DIR,
)

babel = Babel()
login_manager = LoginManager()
csrf = CSRFProtect()
migrate = Migrate()


# Stripe's own defaults are an 80-second timeout and two automatic retries: a
# Stripe having a bad minute could hold a page for four. Five seconds to
# connect and fifteen to answer is ample for an API that normally answers in a
# fraction of one, and a single retry -- Stripe makes retried requests safe
# with idempotency keys -- keeps the worst case at about half a minute, inside
# the web server's 60-second limit.
STRIPE_TIMEOUT_SECONDS = (5, 15)
STRIPE_NETWORK_RETRIES = 1


def configure_stripe_http_client():
    stripe.default_http_client = stripe.new_default_http_client(timeout=STRIPE_TIMEOUT_SECONDS)
    stripe.max_network_retries = STRIPE_NETWORK_RETRIES


def get_rate_limit_identity():
    """Whom a limit counts against unless a route says otherwise.

    The account, for somebody signed in: a whole lecture hall behind one campus
    address would otherwise share one budget for resending a confirmation or
    downloading their data. The network address for everybody else.
    """
    if current_user and current_user.is_authenticated:
        return f"user:{current_user.get_id()}"
    return rate_limit_network()


def rate_limit_network():
    """The network address alone -- the loose, flood-stopping limits."""
    return request.remote_addr or "unknown"


def rate_limit_network_and_address():
    """The network address plus the email address typed into the form.

    The tight limit on a form that takes a password: ten wrong guesses at one
    account lock that account's guessing from that network, and nobody else's.
    The address is hashed so the limiter's store holds no email addresses.
    """
    body = request.get_json(silent=True) if request.is_json else None
    sent = body if isinstance(body, dict) else request.form
    address = str(sent.get("email") or sent.get("email_private") or "").strip().lower()
    digest = hashlib.sha256(address.encode("utf-8")).hexdigest()[:16] if address else "-"
    return f"{rate_limit_network()}|{digest}"


def rate_limit_network_and_path():
    """The network address plus the page -- for a reset link, the link itself."""
    return f"{rate_limit_network()}|{request.path}"


limiter = Limiter(
    key_func=get_rate_limit_identity,
    storage_uri=RATELIMIT_STORAGE_URI,
    default_limits=[],
    # If Redis, which keeps the counts, is unreachable, count in each worker's
    # memory instead of failing the request: logging in and signing up must
    # not stop working because the limiter lost its store. Limits still apply,
    # just per worker, until Redis is back.
    in_memory_fallback_enabled=True,
    swallow_errors=True,
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




# Log retention. 0 means keep forever. Audit logs default to keep-forever because
# they are the account/security trail; higher-churn notification delivery records
# default to a generous one-year window.
AUDIT_LOG_RETENTION_DAYS = int(os.getenv("AUDIT_LOG_RETENTION_DAYS", "0"))
NOTIFICATION_RETENTION_DAYS = int(os.getenv("NOTIFICATION_RETENTION_DAYS", "365"))


















def static_asset_version(app, filename):
    if not filename:
        return None

    asset_path = Path(app.static_folder) / filename
    try:
        return str(int(asset_path.stat().st_mtime))
    except OSError:
        return None






















































































































def _stand_down_while_paused(job_name):
    """Check a timer-run job in; True when background jobs are paused after a restore."""
    from .services.background_jobs import check_in

    if check_in(job_name):
        click.echo(
            "Background jobs are paused after a restore. Resume them under "
            "Settings > Maintenance > Backup & Restore. Not running."
        )
        return True
    return False


























































def is_safe_next_url(target):
    if not target:
        return False

    ref_url = urlsplit(request.host_url)
    test_url = urlsplit(urljoin(request.host_url, target))
    return test_url.scheme in {"http", "https"} and ref_url.netloc == test_url.netloc


















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






def get_member_portal_target(user):
    # Where signing in lands you. A capability, not a role name: an account
    # holding only super admin was being sent to the member page.
    if user.can(Permission.ADMIN_ACCESS):
        return "admin.admin_dashboard"
    return "account.account"

















































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
    reclaimed_avatar = service.get_reclaimed_avatar(member) if member else None
    reconnect_waiting = old_forum_account_waiting(member)
    # An approved picture is kept: members cannot change it on their own. An
    # admin can allow one replacement, and until the new picture is approved
    # the old one -- and the forum access that comes with it -- stays.
    has_picture = approved_submission is not None or reclaimed_avatar is not None
    replacement_allowed = bool(has_picture and member and member.avatar_replacement_allowed_at)
    replacement_pending = replacement_allowed and pending_submission is not None

    status_key = "disabled"
    status_message = _("The forum integration is not enabled yet.")
    can_upload_avatar = False
    can_enter_forum = False

    if member is None or member.user is None:
        status_key = "no_membership"
        status_message = _("You need a membership for the forum.")
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
    elif not member_has_active_access(member) and member.payment_status == "processing":
        # Paid, and waiting for the money to arrive: a SEPA debit takes days.
        # "Your membership is not active" would read as though something had
        # gone wrong.
        status_key = "payment_processing"
        status_message = _("Your forum access starts as soon as your payment has cleared.")
    elif not member_has_active_access(member):
        status_key = "inactive_membership"
        status_message = _("Forum access needs an active membership.")
    elif approved_submission is not None:
        status_key = "active"
        status_message = _("Your forum access is ready.")
        can_enter_forum = service.is_ready()
        can_upload_avatar = replacement_allowed
    elif reclaimed_avatar is not None:
        # They came back to an account that already has a face on it -- the one
        # they uploaded to the old forum, which is live on their profile right
        # now. Asking them to upload a picture would be asking them to redo
        # something already done, as the first thing they are told.
        status_key = "active"
        status_message = _("Your forum access is ready, with the profile picture from the old forum.")
        can_enter_forum = service.is_ready()
        can_upload_avatar = replacement_allowed
    elif reconnect_waiting:
        # Their old account comes back once they confirm the university
        # address -- with its username and, usually, its picture. Asking for a
        # photo first would be asking for one they may not need.
        status_key = "reconnect_waiting"
        status_message = _("You were on the old forum. Confirm your university email address to get "
                           "your old account back, with its username and posts.")
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
        status_message = _("Upload a profile picture to complete your forum access.")
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
        "has_picture": has_picture,
        "replacement_allowed": replacement_allowed,
        "replacement_pending": replacement_pending,
        "can_enter_forum": can_enter_forum,
        "reconnect_waiting": reconnect_waiting,
        "entry_url": url_for("forum.forum_entry"),
        "forum_error": forum_account.last_error if forum_account is not None else None,
        # What may be uploaded -- not what pictures are stored as, which is
        # what this line used to show, and which left AVIF out.
        "avatar_input_formats": ", ".join(
            {"JPEG": "JPG", "WEBP": "WebP"}.get(fmt, fmt) for fmt in UPLOAD_IMAGE_FORMATS
        ),
        "avatar_max_bytes": avatar_max_bytes,
        "avatar_max_bytes_display": format_bytes_human(avatar_max_bytes),
        "avatar_upload_request_limit": avatar_upload_request_limit,
        "avatar_upload_request_limit_display": format_bytes_human(avatar_upload_request_limit),
    }


def create_app(config_overrides=None):
    app = Flask(__name__)

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
    app.config["TEST_SERVER"] = TEST_SERVER

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
        supported = app.config["BABEL_SUPPORTED_LOCALES"]
        if len(supported) < 2:
            # One language: neither ?lang= nor the browser's preferences get a say.
            return app.config["BABEL_DEFAULT_LOCALE"]
        lang = request.args.get("lang")
        if lang in supported:
            return lang
        return request.accept_languages.best_match(supported) or app.config["BABEL_DEFAULT_LOCALE"]

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
    from .blueprints.teams import teams_bp
    from .blueprints.webhook import webhook_bp
    from .api import api_bp

    csrf.exempt(webhook_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(webhook_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(forum_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(teams_bp)

    # What else the association sells comes through the same Stripe webhook;
    # each purpose has its handler. See services/payments.py.
    from .services.credit import handle_event as handle_credit_event
    from .services.payments import PURPOSE_CREDIT, PURPOSE_TEAM, register_purpose
    from .services.team_payments import handle_event as handle_team_payment_event

    register_purpose(PURPOSE_TEAM, handle_team_payment_event)
    register_purpose(PURPOSE_CREDIT, handle_credit_event)

    # The emails say when they come from the test server (emails/_layout.html).
    @app.context_processor
    def inject_test_server():
        return dict(test_server=bool(app.config.get("TEST_SERVER")))

    # Dates and times on every page and email in one format and in Vienna
    # time: 31.12.2026, 31.12.2026 14:05.
    @app.template_filter("date_display")
    def date_display_filter(value):
        return format_date_display(value) if value else ""

    @app.template_filter("datetime_display")
    def datetime_display_filter(value):
        return format_datetime_display(value) if value else ""

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

    # The security policy of every answer (content_security.py): set here,
    # per answer, with a nonce for the new front end's style tags.
    from .content_security import apply as apply_content_security_policy

    app.after_request(apply_content_security_policy)

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
    configure_stripe_http_client()

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

    def _put_the_stale_ledger_aside(path):
        """Renamed rather than deleted. It is the only record of a run that
        happened, and the forum it describes may still be somewhere."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        moved = path.with_name(f"{path.name}.stale-{stamp}")
        path.rename(moved)
        return moved

    def _forum_label():
        """What to call this board on the worksheet. Never a reason to fail.

        It names the key the page keeps decisions under, so it wants to be the
        same from one run to the next -- but reading a dump and serving files
        needs no database, and dying with a connection traceback because the
        label could not be looked up is a poor trade for a string.
        """
        try:
            return get_forum_service().settings.get("forum_base_url", "") or "the old board"
        except Exception as exc:  # noqa: BLE001 -- a label, not the job
            click.echo(click.style(
                f"(Cannot reach the portal's database for the forum's address, "
                f"so the page is filed under 'the old board': {exc})",
                fg="yellow"), err=True)
            return "the old board"

    def _load_mybb_dump(dump_file):
        """The old board's tables, read once, with the prefix resolved."""
        import sys
        from pathlib import Path as _Path

        sys.path.insert(0, str(_Path(app.root_path).parent / "scripts"))
        from mybb_export import find_prefix, find_table, read_dump, rows_of  # noqa: E402

        # How it was read is printed, because reading it wrongly is silent and
        # ruinous: a latin1 board read as UTF-8 loses every umlaut in every
        # post, and the import then reports a clean run.
        dump = read_dump(dump_file, on_note=lambda note: click.echo(f"Dump read as {note}."))
        # With the prefix, so "users" cannot match mybb_tapatalk_users -- a
        # plugin table with no uid column, which silently made every post
        # anonymous rather than failing.
        prefix, _rejected = find_prefix(dump)
        return {
            name: list(rows_of(dump, find_table(dump, name, prefix)))
            for name in ("posts", "threads", "attachments", "users", "forums")
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

    def _author_finder(poster):
        """Given the old board's name for somebody, this forum's name for them.

        Discourse caps a username at twenty characters and adjusts anything
        longer as it creates the account, so four of this board's names -- the
        Niedergrottenthalers and Tschurtschenthalers -- exist there under names
        nobody wrote down. The portal's own user id is the handle that survived:
        every imported profile was published with it as its external id.
        """
        def find(username):
            profile = db.session.execute(
                db.select(ImportedForumProfile).filter_by(source_username=username)
            ).scalars().first()
            if profile is None:
                return None
            return poster.username_for_external_id(profile.user_id)

        return find

    def _check_the_key_can_post_as_others(poster, *, given):
        """Stop before anything changes if every post is going to be refused.

        The archive is posted as its authors. A key bound to one user -- which
        is exactly what the portal's own key should be -- can act as nobody
        else, and the first real run with one changed 23 settings, made 107
        categories and then had every post refused. Asked of somebody the
        forum is known to have, so that "no such person" cannot be mistaken
        for "not allowed".
        """
        profile = db.session.execute(
            db.select(ImportedForumProfile)
            .where(ImportedForumProfile.user_id.is_not(None))
            .order_by(ImportedForumProfile.id)
        ).scalars().first()
        if profile is None:
            return
        try:
            somebody = poster.username_for_external_id(profile.user_id)
        except ForumProviderError:
            return
        if not somebody or somebody.lower() == poster.admin_username.lower():
            return
        if poster.may_act_as(somebody) is not False:
            return
        whose = (
            "the key given in DISCOURSE_MIGRATION_API_KEY"
            if given else
            "the portal's own key, because no DISCOURSE_MIGRATION_API_KEY was "
            "given -- and that one is bound to a single user on purpose"
        )
        raise click.ClickException(
            f"This key may not post as anybody but {poster.admin_username}: "
            f"asked to act as {somebody}, who is on the forum, it was refused. "
            f"It is {whose}. Posting the archive needs a key whose User Level "
            f"is 'All Users' (Discourse: Admin -> API -> Keys -> New Key), "
            f"passed as DISCOURSE_MIGRATION_API_KEY and revoked afterwards. "
            f"Nothing has been changed."
        )

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
            colour = (
                "green" if row["outcome"] in ("restored", "already back")
                else "yellow"
            )
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
        mark_journal_restored(journal, results)

        stuck = [row for row in results if row["outcome"].startswith("left alone")]
        if stuck:
            click.echo(click.style(
                f"\n{len(stuck)} settings were left alone. Look at them, and "
                f"use --force if the import's value is the one to undo.",
                fg="yellow",
            ))
        else:
            click.echo(click.style("\nThe forum is back as it was.", fg="green"))

    @app.cli.command("inspect-forum-attachments")
    @click.argument("dump_file", type=click.Path(exists=True, dir_okay=False))
    @click.option("--thread", help="Only this thread's attachments (tid).")
    @click.option("--name", help="Only files whose stored name contains this.")
    @with_appcontext
    def inspect_forum_attachments_command(dump_file, thread, name):
        """What the old board recorded about each uploaded file.

        Every upload is on disk as post_<pid>_<time>_<hash>.attach -- the
        original bytes under a name that says nothing -- and the name somebody
        chose, with its type, is in the database. So a file that arrives on the
        new forum looking wrong cannot be checked by looking in the uploads
        folder; this is how to look it up.

        The on-disk file is the original bytes, so `file` on the path in the
        left column says what it really is, whatever the name claims.
        """
        tables = _load_mybb_dump(dump_file)
        wanted = None
        if thread:
            wanted = {
                row.get("pid") for row in tables["posts"]
                if row.get("tid") == str(thread)
            }

        rows = [
            row for row in tables["attachments"]
            if (wanted is None or row.get("pid") in wanted)
            and (not name or name.lower() in (row.get("filename") or "").lower())
        ]
        if not rows:
            raise click.ClickException("No attachments match that.")

        for row in sorted(rows, key=lambda row: int(row.get("pid") or 0)):
            size = int(row.get("filesize") or 0)
            click.echo(
                f"\npid {row.get('pid')}  {size / 1024:.1f} KB  "
                f"type={row.get('filetype') or '?'}"
            )
            click.echo(f"  on disk:  {row.get('attachname')}")
            click.echo(f"  name:     {row.get('filename')}")
        click.echo(f"\n{len(rows)} attachments.")

    @app.cli.command("inspect-forum-post")
    @click.argument("dump_file", type=click.Path(exists=True, dir_okay=False))
    @click.option("--pid", required=True, help="The old board's post id.")
    @with_appcontext
    def inspect_forum_post_command(dump_file, pid):
        """One post, as it is on the old board and as it would be sent.

        For when the forum refuses a post and its reason does not match what
        the archive appears to contain -- "Body is too short (minimum is 2
        characters)" against a post that plainly has more than two. Guessing at
        that twice is what this exists to stop: here is the message, its
        conversion, and every length either end could be counting.
        """
        tables = _load_mybb_dump(dump_file)
        post = next(
            (row for row in tables["posts"] if str(row.get("pid")) == str(pid)), None
        )
        if post is None:
            raise click.ClickException(f"There is no post {pid} in that dump.")

        raw = post.get("message") or ""
        body = bbcode_to_markdown(raw)
        thread = next(
            (row for row in tables["threads"] if row.get("tid") == post.get("tid")), None
        )
        author = next(
            (row for row in tables["users"] if row.get("uid") == post.get("uid")), None
        )
        files = [row for row in tables["attachments"] if row.get("pid") == str(pid)]

        click.echo(f"pid {pid}  tid {post.get('tid')}  uid {post.get('uid')}"
                   f"  ({(author or {}).get('username') or 'no longer in the users table'})")
        if thread:
            click.echo(f"thread: {thread.get('subject')}"
                       f"{'  (this is its opening post)' if thread.get('firstpost') == str(pid) else ''}")
        click.echo(f"files:  {len(files)}")
        click.echo(f"\nAs the old board holds it ({len(raw)} characters):")
        click.echo(repr(raw))
        click.echo(f"\nAs it would be sent ({len(body)} characters, "
                   f"{len(body.strip())} stripped, {len(set(body))} distinct):")
        click.echo(repr(body))
        if not body.strip():
            click.echo(click.style(
                "\nNothing at all after conversion: this is sent as a marked "
                "empty post.", fg="cyan"))

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
        poster.find_author = _author_finder(poster)

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
                    results = restore_site_settings(poster, changes)
                    _report_restore(results)
                    # So the next run is not blocked by a journal whose whole
                    # purpose has already been served.
                    mark_journal_restored(journal, results)
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
        if dry_run:
            click.echo(
                "Whether Discourse keeps these dates is what the real run "
                "answers; a dry run cannot."
            )
        elif kept is True:
            click.echo(click.style(f"Dates survived: {explanation}", fg="green"))
        elif kept is False:
            click.echo(click.style(f"Dates did NOT survive: {explanation}", fg="red"))
            click.echo("The importer needs a different shape; nothing else here matters yet.")
        else:
            click.echo(click.style(f"Undetermined: {explanation}", fg="yellow"))

        # Every one of them. On a dry run this list *is* the output -- it is
        # the set of files to copy across -- and a truncated list of files to
        # copy is worse than no list, because it looks complete.
        if report["problems"]:
            click.echo(click.style(
                f"\n{len(report['problems'])} problems:", fg="yellow"), err=True)
        for problem in report["problems"]:
            click.echo(click.style(f"  ! {problem}", fg="yellow"), err=True)
        if report.get("topic_id"):
            click.echo(f"\nTopic {report['topic_id']} — go and look at it.")

    @app.cli.command("serve-forum-worksheet")
    @click.argument("dump_file", type=click.Path(exists=True, dir_okay=False))
    @click.option("--uploads", required=True, type=click.Path(exists=True, file_okay=False),
                  help="The old board's uploads folder.")
    @click.option("--port", default=8765, show_default=True)
    @click.option("--host", default="127.0.0.1", show_default=True,
                  help="Use 0.0.0.0 to reach it from another machine, having "
                       "read what that means below.")
    @with_appcontext
    def serve_forum_worksheet_command(dump_file, uploads, port, host):
        """Serves the category worksheet, with the old board's files in it.

        One address, nothing to configure: the page is at / and every
        attachment is under /files/, so each document on the worksheet has an
        open button and two of them sit side by side. Deciding whether this
        year's exam and 2016's are the same course means looking at them.

        The files need serving because of what they are on disk: every upload
        is post_<pid>_<time>_<hash>.attach, the original bytes under a name
        that says nothing and an extension no browser will render. The name
        somebody chose and the type it really is are columns in the database,
        so those are read from the dump and each file is served under them.

        \b
        It answers to anybody who can reach it, with thirteen years of exam
        papers and no password. Two ways to use it:

        \b
          * --host 127.0.0.1 (the default) and an SSH tunnel from your machine:
                ssh -N -L 8765:127.0.0.1:8765 you@server
            then open http://127.0.0.1:8765/ in your browser. Nothing is
            exposed to the network at all.
          * --host 0.0.0.0, and it is reachable at the server's address from
            anywhere that can route to it. Simpler, and it means anybody on
            that network can read the archive while it runs.

        Either way, stop it when the curating is done. It is a tool for an
        afternoon, not a service.
        """
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from urllib.parse import unquote

        tables = _load_mybb_dump(dump_file)
        rows = category_worksheet(
            tables["forums"], tables["threads"], tables["posts"],
            tables["attachments"], users=tables["users"],
        )
        if not rows:
            raise click.ClickException("No forum in that dump holds any threads.")
        page = render_worksheet(
            rows, _forum_label(), _sortable, uploads_base="/files/",
        ).encode("utf-8")

        uploads_dir = Path(uploads).resolve()
        known = {}
        for row in tables["attachments"]:
            attachname = (row.get("attachname") or "").strip()
            if attachname:
                known[attachname] = (
                    (row.get("filename") or attachname).strip(),
                    row.get("filetype") or "application/octet-stream",
                )

        class Worksheet(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 -- http.server's spelling
                path = unquote(self.path.split("?")[0])
                if path in ("/", "/index.html"):
                    self._send(page, "text/html; charset=utf-8")
                    return
                if not path.startswith("/files/"):
                    self.send_error(404, "Only / and /files/ are here")
                    return
                wanted = path[len("/files/"):]
                entry = known.get(wanted)
                # Served only if the dump says it exists, which is also what
                # makes ".." pointless: nothing outside the board is in the
                # index, so nothing outside it can be asked for.
                on_disk = (uploads_dir / wanted).resolve() if entry else None
                if entry is None or not on_disk.is_file() \
                        or uploads_dir not in on_disk.parents:
                    self.send_error(404, "Not one of the old board's files")
                    return
                filename, content_type = entry
                # Inline, because the point is to look at it rather than to
                # collect it, and a viewer that downloads is not a viewer.
                self._send(
                    on_disk.read_bytes(), content_type,
                    f"inline; filename*=UTF-8''{quote_plus(filename)}",
                )

            def _send(self, body, content_type, disposition=None):
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                if disposition:
                    self.send_header("Content-Disposition", disposition)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        click.echo(f"{len(rows)} old forums, {len(known)} files from {uploads_dir}.")
        if host not in ("127.0.0.1", "localhost", "::1"):
            click.echo(click.style(
                f"Reachable from the network on {host}:{port}, with no password "
                f"and the whole archive behind it. Stop it when you are done.",
                fg="yellow",
            ))
            click.echo(f"Open http://<this machine>:{port}/ in your browser.")
        else:
            click.echo(f"Tunnel it:  ssh -N -L {port}:127.0.0.1:{port} you@<this machine>")
            click.echo(f"Then open:  http://127.0.0.1:{port}/")
        try:
            ThreadingHTTPServer((host, port), Worksheet).serve_forever()
        except KeyboardInterrupt:
            click.echo("\nStopped.")

    @app.cli.command("forum-category-worksheet")
    @click.argument("dump_file", type=click.Path(exists=True, dir_okay=False))
    @click.option("--out", type=click.Path(dir_okay=False),
                  default="forum-categories.html", show_default=True,
                  help="Where to write the worksheet.")
    @click.option("--csv", "as_csv", is_flag=True,
                  help="Write a plain CSV instead of the page.")
    @with_appcontext
    def forum_category_worksheet_command(dump_file, out, as_csv):
        """Where each of the old board's forums should end up, for you to decide.

        The new forum is not the old one rearranged. It is somewhere students
        look things up, and most of a decade-old board is lectures that no
        longer run in that form. So every old forum needs one of two answers:
        which current lecture it belongs to, or that it is archive.

        That is a curriculum question and this cannot answer it. What it can do
        is lay out the evidence -- how much is in each forum, and when it
        stopped -- and put every year's version of the same course on adjacent
        lines, so the question takes a minute instead of an afternoon.

        Fill in two columns and keep the file:

        \b
          target  the category its threads should end up in.
                  Left empty means archive.
          access  who should be able to see it, once the groups exist.
        """
        tables = _load_mybb_dump(dump_file)
        rows = category_worksheet(
            tables["forums"], tables["threads"], tables["posts"],
            tables["attachments"], users=tables["users"],
        )
        if not rows:
            raise click.ClickException("No forum in that dump holds any threads.")

        if as_csv:
            fields = ["old_fid", "old_path", "lecture", "threads", "posts",
                      "first_post", "last_post", "target", "access"]
            with open(out, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=fields, extrasaction="ignore"
                )
                writer.writeheader()
                writer.writerows(rows)
        else:
            Path(out).write_text(
                render_worksheet(rows, _forum_label(), _sortable),
                encoding="utf-8",
            )

        quiet_since = sorted(row["last_post"] for row in rows)
        click.echo(
            f"{len(rows)} forums hold threads, written to {out}.\n"
            f"The oldest stopped in {quiet_since[0][:4]}, the newest is from "
            f"{quiet_since[-1][:4]}."
        )
        stale = [row for row in rows if row["last_post"] < "2022"]
        if stale:
            click.echo(
                f"{len(stale)} of them have had nothing posted since 2021, "
                f"which is where I would start reading."
            )
        if as_csv:
            click.echo(
                "Open it in a spreadsheet. Same-named courses are on adjacent "
                "lines; fill in 'target' where a forum belongs to a lecture "
                "that still runs, and leave it empty for what is archive."
            )
        else:
            click.echo(
                "Copy it to your own machine and open it in a browser. Write "
                "the curriculum down the left, then work through the old "
                "forums: each one shows what is in it, and every version of a "
                "course sits together so they can be assigned at once. What "
                "you decide is kept in the browser as you go; Export JSON when "
                "it is done."
            )

    @app.cli.command("dump-forum-settings")
    @click.option("--out", type=click.Path(dir_okay=False),
                  default="forum-settings.json", show_default=True,
                  help="Where to write every setting the forum reports.")
    @click.option("--limits", is_flag=True,
                  help="Print the settings that constrain what can be posted.")
    @click.option("--api-key", envvar="DISCOURSE_MIGRATION_API_KEY",
                  help="Reads DISCOURSE_MIGRATION_API_KEY if not given.")
    @with_appcontext
    def dump_forum_settings_command(out, limits, api_key):
        """Everything this Discourse will tell you about itself.

        The same call the import already makes, keeping the parts it throws
        away: each setting's description, its default, and which ones exist at
        all. Two runs were spent on limits that were in this list the whole
        time -- a third level of categories the forum would not make, and a
        fifty-character cap on their names -- so this is the list to read
        before guessing at the next one.

        Values of settings Discourse marks secret, and of anything whose name
        mentions a password or a key, are left out. The file is meant to be
        readable and shareable.
        """
        service = get_forum_service()
        if not service.is_enabled() or service.config_errors:
            raise click.ClickException("The forum integration is not configured.")

        settings = dict(service.settings)
        if api_key:
            settings["discourse_api_key"] = api_key
        poster = ContentPoster(settings)
        _warn_about_the_key(poster)

        try:
            poster.site_settings()
        except ForumProviderError as exc:
            raise click.ClickException(f"Could not read the settings: {exc}") from exc

        everything, interesting = settings_inventory(poster.last_settings_rows)
        Path(out).write_text(
            json.dumps({"forum": poster.base_url, "settings": everything},
                       indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        click.echo(f"{len(everything)} settings written to {out}.")
        click.echo(
            f"{len(interesting)} of them constrain what can be posted."
            + ("" if limits else " Pass --limits to see them.")
        )

        if limits:
            click.echo("")
            for entry in interesting:
                click.echo(f"{entry['setting']:<42} {str(entry['value'])[:34]}")

    @app.cli.command("check-forum-uploads")
    @click.argument("dump_file", type=click.Path(exists=True, dir_okay=False))
    @click.option("--uploads", required=True, type=click.Path(file_okay=False),
                  help="Where the .attach files have been put so far.")
    @click.option("--missing-to", type=click.Path(dir_okay=False),
                  help="Write the paths still needed to this file, one per "
                       "line, ready to drive a copy.")
    @with_appcontext
    def check_forum_uploads_command(dump_file, uploads, missing_to):
        """Which of the old board's files are here, and which are still to come.

        Nine gigabytes fetched a directory at a time over several sittings is
        not something anybody can hold in their head, and the import reports a
        missing file per post -- the right shape for one thread and the wrong
        one for 2,347.

        Size is checked as well as presence. The usual way a file goes wrong
        here is not absence: a web server asked for something it has not got
        answers with a page saying so, and a fetch that does not check writes
        that page to disk under the name of the file it wanted.
        """
        tables = _load_mybb_dump(dump_file)
        report = audit_uploads(tables["attachments"], uploads)

        click.echo(
            f"{report['expected']} attachments on the old board.\n"
            f"  here:    {report['present']:>5}  "
            f"({report['bytes_present'] / 1024 ** 3:.2f} GB)\n"
            f"  missing: {len(report['missing']):>5}  "
            f"({report['bytes_missing'] / 1024 ** 3:.2f} GB still to fetch)"
        )

        if report["wrong_size"]:
            click.echo(click.style(
                f"\n{len(report['wrong_size'])} files are here but the wrong "
                f"size. Fetch these again -- a file of the wrong size is "
                f"usually an error page wearing its name:", fg="red",
            ))
            for row in report["wrong_size"][:20]:
                click.echo(
                    f"  {row['name']}  {row['on_disk']} bytes, "
                    f"should be {row['recorded']}"
                )
            if len(report["wrong_size"]) > 20:
                click.echo(f"  ... and {len(report['wrong_size']) - 20} more")

        if missing_to:
            wanted = report["missing"] + [row["name"] for row in report["wrong_size"]]
            Path(missing_to).write_text("\n".join(wanted) + "\n", encoding="utf-8")
            click.echo(f"\n{len(wanted)} paths written to {missing_to}.")

        if not report["missing"] and not report["wrong_size"]:
            click.echo(click.style("\nEverything is here.", fg="green"))
        else:
            raise SystemExit(1)

    @app.cli.command("import-forum-content")
    @click.argument("dump_file", type=click.Path(exists=True, dir_okay=False))
    @click.option("--uploads", required=True, type=click.Path(file_okay=False),
                  help="The old forum's uploads folder.")
    @click.option("--ledger", type=click.Path(dir_okay=False),
                  default="forum-import-ledger.jsonl", show_default=True,
                  help="What has already been posted. Keep it; it is how a "
                       "stopped run carries on instead of starting again.")
    @click.option("--dry-run", is_flag=True, help="Report and send nothing.")
    @click.option("--limit", type=int, default=0, metavar="N",
                  help="Stop after N threads, for a first careful run.")
    @click.option("--keep-duplicate-titles", is_flag=True,
                  help="Post the old subjects unchanged. Discourse refuses a "
                       "second topic with a title it already has, so this "
                       "loses every thread after the first of each name.")
    @click.option("--allow-missing-attachments", is_flag=True,
                  help="Post even where a file is not on this machine. Those "
                       "files are then lost: the post is written down as done "
                       "and never revisited.")
    @click.option("--adjust-settings", is_flag=True,
                  help="Loosen the settings this needs, and put them back after.")
    @click.option("--settings-file", type=click.Path(dir_okay=False),
                  help="Where to write the record of what was changed.")
    @click.option("--api-key", envvar="DISCOURSE_MIGRATION_API_KEY",
                  help="An 'All Users' Discourse API key.")
    @click.option("--mapping", type=click.Path(exists=True, dir_okay=False),
                  help="The worksheet's JSON: the lectures that still run, and "
                       "the archive for everything else. Without it the old "
                       "board's own categories are recreated, which is a test "
                       "shape rather than one to keep.")
    @click.option("--categories-only", is_flag=True,
                  help="Make the categories and post nothing, to try a "
                       "structure against a forum that already holds content.")
    @click.option("--reset-ledger", is_flag=True,
                  help="If the ledger turns out to describe a forum that has "
                       "since been reset, move it aside and start again "
                       "instead of stopping.")
    @with_appcontext
    def import_forum_content_command(dump_file, uploads, ledger, dry_run, limit,
                                     keep_duplicate_titles,
                                     allow_missing_attachments, adjust_settings,
                                     settings_file, api_key, mapping,
                                     categories_only, reset_ledger):
        """Moves the whole old board across, keeping the categories it had.

        The old structure is recreated rather than reorganised. That is a
        deliberately dull choice for a first full run: it makes the result
        comparable with the old board post for post, and rearranging it
        afterwards is something Discourse does well and a migration script
        does badly.

        Safe to run again. Every post that lands is written to the ledger
        before the next is attempted, so a run that stops -- and one this long
        will stop -- carries on rather than posting everything twice.
        """
        service = get_forum_service()
        if not service.is_enabled() or service.config_errors:
            raise click.ClickException("The forum integration is not configured.")

        tables = _load_mybb_dump(dump_file)
        if not tables.get("forums"):
            raise click.ClickException(
                "No forums table in that dump, so there is nothing to make the "
                "categories from."
            )

        settings = dict(service.settings)
        if api_key:
            settings["discourse_api_key"] = api_key
        poster = ContentPoster(settings)
        _warn_about_the_key(poster)
        # So that a post whose author's name was too long for Discourse is
        # retried under the name Discourse gave them, rather than lost.
        poster.find_author = _author_finder(poster)
        if not categories_only:
            _check_the_key_can_post_as_others(poster, given=bool(api_key))

        # Three levels only where the forum says it can do three. Where it
        # cannot, the setting is absent rather than false, and the refusal
        # arrives one category at a time long after every setting has been
        # changed -- so the shape is decided here instead.
        try:
            live = poster.site_settings()
        except ForumProviderError:
            live = {}
        max_depth = (
            DEEPEST_CATEGORY_NESTING if "max_category_nesting" in live
            else MAX_CATEGORY_NESTING
        )

        placement = None
        titles = None
        gatekeeper = None
        if mapping:
            worksheet = json.loads(Path(mapping).read_text(encoding="utf-8"))
            placement = read_mapping(
                worksheet, tables["forums"], tables["threads"],
            )
            # Applied once the categories exist and before a single post goes
            # into them, because a category nobody has restricted is one
            # anybody can read -- and an archive that was public for the two
            # hours of a run has been public.
            gatekeeper = _category_gatekeeper(
                service, owned_roots(worksheet), dry_run=dry_run,
                authors_group=None if categories_only else ARCHIVE_GROUP,
                mapping_file=mapping,
            )
            plan = mapping_plan(placement["paths"], tables["threads"])
            titles = titles_for(
                tables["forums"], tables["threads"], placement["archived"],
                paths=placement["paths"],
            )
            if dry_run:
                # The lectures that still run keep their subject unless it
                # clashes; these are the ones that do, for reading before the
                # run rather than on the forum after it. The archive's are
                # always labelled and are not listed.
                relabelled = sorted(
                    (titles[thread.get("tid")], thread.get("subject") or "")
                    for thread in tables["threads"]
                    if thread.get("fid") not in placement["archived"]
                    and thread.get("tid") in titles
                    and titles[thread.get("tid")] != (thread.get("subject") or "").strip()
                )
                click.echo(f"Titles in lectures that still run, where the subject "
                           f"is shared ({len(relabelled)}):")
                for title, subject in relabelled:
                    click.echo(f"  {subject.strip()}  ->  {title}")
            live_count = sum(
                1 for row in plan
                if row["depth"] == 2 and row["path"][0] != ARCHIVE_ROOT
            )
            click.echo(
                f"Mapping read: {live_count} lectures that still run, and an "
                f"archive of everything else."
            )
            for problem in placement["problems"]:
                click.echo(click.style(f"  ! {problem}", fg="yellow"), err=True)
        else:
            plan = category_plan(tables["forums"], tables["threads"], max_depth)

        requirements = plan_site_settings(
            tables["threads"], tables["posts"], tables["attachments"]
        )
        if "max_category_nesting" in live:
            requirements.append(category_nesting_requirement(plan))

        # Posts whose author is not in the users table -- people deleted from
        # the old board over thirteen years. They are attributed to the
        # fallback, which is worth knowing the size of before a run rather
        # than a thread at a time during one.
        known = {row.get("uid") for row in tables["users"]}
        orphaned = sum(1 for row in tables["posts"] if row.get("uid") not in known)

        click.echo(
            f"{len(tables['threads'])} threads, {len(tables['posts'])} posts, "
            f"{len(tables['attachments'])} attachments, into {len(plan)} "
            f"categories {max_depth} levels deep."
        )
        if orphaned:
            click.echo(
                f"{orphaned} of those posts were written by somebody no longer "
                f"in the old board's user table, and will be attributed to "
                f"{service.settings['discourse_api_username']}."
            )
        try:
            ready, anything = _report_site_settings(
                check_site_settings(poster, requirements)
            )
        except ForumProviderError as exc:
            click.echo(click.style(f"Could not read the site settings: {exc}", fg="yellow"))
            ready, anything = True, False

        changes = []
        journal = None
        decision = what_to_do_about_settings(
            ready=ready, anything=anything, adjust_settings=adjust_settings,
            # A categories-only run sends no posts, so what the forum would
            # refuse about a post cannot stop it -- the same reasoning as a dry
            # run, and the reason this mode exists is to try a structure
            # against a forum whose settings nobody wants touched.
            dry_run=dry_run or categories_only,
        )
        if decision == LOOSEN:
            journal = _settings_journal_path(settings_file)
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
                "The forum would refuse part of this archive. Run it again "
                "with --adjust-settings, or change them by hand first."
            )

        record = Ledger(ledger)
        if record.posts:
            # Checked, not trusted. A ledger from before the forum was reset
            # still says every post is done, so an import would skip all 1,517
            # of them and report a clean run against an empty archive.
            describes, why = ledger_describes(poster, record)
            if not describes:
                record.close()
                if not reset_ledger:
                    raise click.ClickException(
                        f"{ledger} does not describe this forum: {why}.\n\n"
                        f"Left alone it would skip every post it lists and "
                        f"leave the forum empty, reporting a clean run. Either "
                        f"point --ledger at a new file, or pass --reset-ledger "
                        f"to move this one aside and start a fresh record."
                    )
                moved = _put_the_stale_ledger_aside(Path(ledger))
                click.echo(click.style(
                    f"{ledger} was about a forum that is gone ({why}); moved "
                    f"to {moved.name} and starting a fresh record.", fg="yellow",
                ))
                record = Ledger(ledger)
        if record.posts:
            click.echo(click.style(
                f"Carrying on: {len(record.posts)} posts are already on the "
                f"forum according to {ledger}.", fg="cyan",
            ))

        def say(thread, report, summary):
            done = summary["posted"] + summary["already_there"]
            click.echo(
                f"  [{summary['threads']:>4}] {str(thread.get('subject'))[:48]:<50} "
                f"{done:>5} posts, {summary['waiting']:>4} waiting, "
            f"{summary['failed']:>3} failed"
            )

        try:
            summary = migrate_board(
                poster, tables, uploads, record,
                dry_run=dry_run, limit=limit,
                fallback_username=service.settings["discourse_api_username"],
                on_thread=say,
                require_attachments=not allow_missing_attachments,
                max_depth=max_depth,
                keep_duplicate_titles=keep_duplicate_titles,
                plan=plan, titles=titles, categories_only=categories_only,
                after_categories=gatekeeper,
            )
        finally:
            record.close()
            if gatekeeper is not None:
                gatekeeper.close()
            if changes:
                click.echo("\nPutting the settings back:")
                try:
                    results = restore_site_settings(poster, changes)
                    _report_restore(results)
                    # So the next run is not blocked by a journal whose whole
                    # purpose has already been served.
                    mark_journal_restored(journal, results)
                except ForumProviderError as exc:
                    click.echo(click.style(
                        f"COULD NOT RESTORE THE SETTINGS: {exc}\n"
                        f"The forum is still loosened. Run:\n"
                        f"  flask restore-forum-settings {journal}",
                        fg="red",
                    ), err=True)

        # Said first, and said even when nothing was posted: a categories-only
        # run that reports "0 threads: 0 posted" reads as though it did nothing
        # at all, when making the categories is the whole of what it was for.
        click.echo(
            f"\n{summary.get('categories_there', 0)} categories on the forum, "
            f"{summary.get('categories_made', 0)} of them made by this run."
        )
        if categories_only:
            click.echo(
                "Nothing was posted and no setting was changed. Look at the "
                "categories, and delete them if the shape is not right -- an "
                "empty category deletes cleanly."
            )
        else:
            click.echo(
                f"{summary['threads']} threads: {summary['posted']} posted, "
                f"{summary['already_there']} already there, "
                f"{summary['waiting']} waiting, "
                f"{summary['not_attempted']} not attempted, "
                f"{summary['failed']} failed."
            )
        if summary.get("renamed"):
            click.echo(
                f"{summary['renamed']} of the board's threads share a subject "
                f"with another, so their titles carry the lecture as well -- "
                f"Discourse will not take two topics with the same name. That "
                f"count is for the whole board, not only what this run posted."
            )
        if summary.get("retitled"):
            click.echo(
                f"{len(summary['retitled'])} topics posted by an earlier run "
                f"were renamed to the title this run gives them:"
            )
            for was, now in summary["retitled"]:
                click.echo(f"  {was}  ->  {now}")
        if summary["waiting"]:
            click.echo(
                f"{summary['waiting']} posts were left alone because a file they "
                f"carry is not on this machine or the forum would not take it. "
                f"Nothing is lost -- fix that and run the same command again."
            )
        if summary["not_attempted"]:
            click.echo(
                f"{summary['not_attempted']} posts are in threads whose opening "
                f"post was refused, so there was no topic to put them in. The "
                f"reason is against the post that failed."
            )
        if summary["problems"]:
            click.echo(click.style(
                f"\n{len(summary['problems'])} problems:", fg="yellow"), err=True)
            for problem in summary["problems"]:
                click.echo(click.style(f"  ! {problem}", fg="yellow"), err=True)
            left = sum(summary.get(key, 0)
                       for key in ("waiting", "failed", "not_attempted"))
            if left:
                click.echo(
                    f"\nFix what they say and run the same command again. What has "
                    f"landed is in {ledger} and will not be posted twice."
                )
            else:
                # The five posts whose authors left the old board are reported
                # on every run and need nothing doing; telling somebody to fix
                # them and run again sends them round in a circle.
                click.echo(click.style(
                    "\nEverything is on the forum. The lines above are notes, "
                    "not things left to do.", fg="green"))

    @app.cli.command("dump-portal-settings")
    @click.option("--out", type=click.Path(dir_okay=False), default="portal-settings.json",
                  show_default=True)
    @click.option("--with-secrets", is_flag=True,
                  help="Also export the Stripe keys, the Discourse credentials "
                       "and the SMTP passwords. The file is then a password "
                       "list: it is written 0600 and it is not encrypted.")
    @with_appcontext
    def dump_portal_settings_command(out, with_secrets):
        """Everything an administrator typed, as a file.

        A fresh install starts with a blank settings page, and filling it in is
        forty minutes of copying values that all have to be exactly right and
        none of which announce themselves when they are wrong. This makes
        rebuilding the portal a restore rather than a reconstruction.

        Not included: anything in .env. SECRET_KEY and the database password
        belong to the machine rather than to the configuration, and putting an
        old database password onto a new install would break the thing it was
        restoring. Copy .env separately if you want it.
        """
        payload = export_settings(with_secrets=with_secrets)
        path = Path(out)
        try:
            # Created empty and closed to this user alone before a single
            # secret is written into it, rather than written and then tightened
            # -- in between, the file would be readable by anybody.
            if with_secrets:
                path.touch(mode=0o600, exist_ok=True)
                path.chmod(0o600)
            path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            raise click.ClickException(
                f"Could not write {path}: {exc.strerror}.\n\n"
                f"This runs as the application user, which owns very little of "
                f"this machine -- /root in particular is not writable by it. "
                f"Write it somewhere that user can reach, such as "
                f"--out /var/tmp/portal-settings.json, and move it afterwards."
            )

        for section, values in payload["settings"].items():
            click.echo(f"  {section:<14} {len(values)} settings")
        click.echo(f"  {'mail accounts':<14} {len(payload['mail_accounts'])}")
        click.echo(f"\nWritten: {path}")

        if with_secrets:
            click.echo(click.style(
                "\nThis file holds the Stripe secret key, the Discourse "
                "credentials and every SMTP password, in the clear. Anywhere "
                "you copy it inherits that: encrypt it (gpg -c) before it "
                "leaves this machine, and delete it once the new install has "
                "it.", fg="yellow",
            ))
        elif payload["held_back"]:
            click.echo(
                f"\n{len(payload['held_back'])} credentials were left out and "
                f"have to be entered by hand on the new install: "
                + ", ".join(payload["held_back"])
                + "\nRun again with --with-secrets to include them."
            )

    @app.cli.command("restore-portal-settings")
    @click.argument("settings_file", type=click.Path(exists=True, dir_okay=False))
    @click.option("--dry-run", is_flag=True, help="Say what would change and change nothing.")
    @with_appcontext
    def restore_portal_settings_command(settings_file, dry_run):
        """Put an exported settings file back onto a fresh install.

        Never removes anything: a setting the file does not mention is left as
        it is. The file is a record of one machine rather than a description of
        every machine, and a restore that blanked what it did not know about
        would be one nobody could run twice.
        """
        try:
            payload = json.loads(Path(settings_file).read_text(encoding="utf-8"))
        except OSError as exc:
            raise click.ClickException(
                f"Could not read {settings_file}: {exc.strerror}. This runs as "
                f"the application user; a file exported with --with-secrets is "
                f"readable only by whoever wrote it."
            )
        except ValueError as exc:
            raise click.ClickException(f"{settings_file} is not valid JSON: {exc}")
        try:
            report = import_settings(payload, dry_run=dry_run)
        except ValueError as exc:
            raise click.ClickException(str(exc))

        click.echo(
            f"{len(report['set'])} settings set, "
            f"{len(report['already'])} already right, "
            f"{len(report['accounts'])} mail accounts"
        )
        for key in report["set"]:
            click.echo(f"  {key}")
        if report["skipped"]:
            click.echo(click.style(
                f"\n{len(report['skipped'])} were exported without their value "
                f"and have to be entered by hand: "
                + ", ".join(report["skipped"]), fg="yellow",
            ))
        for problem in report["problems"]:
            click.echo(click.style(f"  ! {problem}", fg="yellow"), err=True)

        if dry_run:
            click.echo(click.style("\nNothing was changed.", fg="cyan"))
            return
        db.session.commit()
        click.echo(click.style(
            "\nCheck the settings pages before relying on this, and remember "
            "the forum needs its Connect secret to match on both sides.",
            fg="cyan",
        ))

    @app.cli.command("create-backup")
    @click.option("--out", "out_path", type=click.Path(dir_okay=False),
                  help="Where to write it. Default: the backups folder, where the admin page lists it.")
    @click.option("--passphrase-stdin", is_flag=True,
                  help="Read the passphrase from standard input instead of asking.")
    @click.option("--created-by", default=None, help="Who asked for it, for the record.")
    @with_appcontext
    def create_backup_command(out_path, passphrase_stdin, created_by):
        """Write an encrypted backup of everything this installation holds.

        The accounts and memberships, every setting and credential, uploaded
        pictures waiting for review, and the SECRET_KEY, Stripe keys and mail
        accounts from .env. Restore it with ``install.sh --restore FILE``.
        """
        from .services import backup as backup_service

        if passphrase_stdin:
            passphrase = sys.stdin.readline().rstrip("\n")
        else:
            passphrase = click.prompt("Passphrase", hide_input=True, confirmation_prompt=True)

        keep_in_folder = out_path is None
        destination = Path(out_path) if out_path else backup_service.new_backup_path()
        recorder = backup_service.StatusRecorder(created_by)
        try:
            result = backup_service.create_backup(destination, passphrase, created_by=created_by, progress=recorder)
        except backup_service.BackupError as exc:
            recorder.fail(str(exc))
            raise click.ClickException(str(exc))
        except Exception as exc:  # noqa: BLE001 -- the page must not be left saying "running"
            recorder.fail(f"The backup failed: {exc}")
            raise
        if keep_in_folder:
            backup_service.prune_backups(keep=backup_service.BACKUPS_KEPT)
        recorder.finish(result)

        actor = db.session.execute(db.select(User).filter_by(email=created_by)).scalar_one_or_none() if created_by else None
        log_audit_event(category="system", event_type="backup_created", actor_user=actor, target_user=actor,
                        metadata={"file": result["file"], "size": result["size"], "sha256": result["sha256"]})
        db.session.commit()
        click.echo(f"Backup written: {destination} ({result['size']} bytes, {result['rows']} rows, "
                   f"{result['files']} files)")
        click.echo(f"SHA-256: {result['sha256']}")

    @app.cli.command("restore-backup")
    @click.argument("backup_file", type=click.Path(exists=True, dir_okay=False))
    @click.option("--passphrase-stdin", is_flag=True,
                  help="Read the passphrase from standard input instead of asking.")
    @click.option("--yes", is_flag=True, help="Do not ask before replacing everything.")
    @click.option("--env-out", type=click.Path(dir_okay=False),
                  help="Write the restored .env values here (JSON) for the installer to apply, "
                       "instead of writing .env directly.")
    @with_appcontext
    def restore_backup_command(backup_file, passphrase_stdin, yes, env_out):
        """Replace everything on this installation with a backup.

        Every account, setting and file here is replaced by the backup's --
        including any account made while installing. Afterwards every
        background job is paused until an administrator resumes them on the
        Backup & Restore page, because a copy restored onto a test machine
        would otherwise start emailing members and pushing to the real forum.

        Normally run through ``install.sh --restore FILE``, which also applies
        the .env values and restarts the portal.
        """
        from .services import backup as backup_service
        from .services.background_jobs import clear_heartbeats, pause

        env_path = backup_service.env_file_path()
        if not env_out and not os.access(env_path if env_path.exists() else env_path.parent, os.W_OK):
            raise click.ClickException(
                f"{env_path} cannot be written by this user. Run the restore through "
                "`install.sh --restore FILE`, or pass --env-out."
            )

        if passphrase_stdin:
            passphrase = sys.stdin.readline().rstrip("\n")
        else:
            passphrase = click.prompt("Passphrase", hide_input=True)

        try:
            checked = backup_service.inspect_backup(backup_file, passphrase)
            backup_service.check_restorable(checked["manifest"])
        except backup_service.BackupError as exc:
            raise click.ClickException(str(exc))
        manifest, contents = checked["manifest"], checked["contents"]
        click.echo(f"Backup made {manifest['created_at']}"
                   + (f" by {manifest['created_by']}" if manifest.get("created_by") else ""))
        click.echo(f"  {sum(contents['tables'].values())} rows in {len(contents['tables'])} tables, "
                   f"{len(contents['files'])} files, database version {manifest['schema_revision']}")
        if not yes:
            click.confirm("Replace EVERYTHING on this installation with this backup?", abort=True)

        def report(step, message):
            click.echo(f"  {message}")

        try:
            result = backup_service.restore_backup(backup_file, passphrase, progress=report)
        except backup_service.BackupError as exc:
            raise click.ClickException(str(exc))

        seed_default_roles()
        backfill_legacy_admin_roles()
        backfill_member_user_links()
        clear_heartbeats()
        pause("restore", backup_created_at=manifest["created_at"])
        log_audit_event(category="system", event_type="backup_restored",
                        metadata={"backup_created_at": manifest["created_at"],
                                  "backup_created_by": manifest.get("created_by"),
                                  "schema_revision": manifest["schema_revision"]})
        db.session.commit()

        if env_out:
            Path(env_out).write_text(json.dumps(result["env"]))
            os.chmod(env_out, 0o600)
        else:
            backup_service.apply_env_values(env_path, result["env"])
        click.echo(click.style(
            "Restored. Background jobs are paused: sign in with an account from the backup and "
            "resume them under Settings > Maintenance > Backup & Restore.", fg="cyan",
        ))

    @app.cli.command("forum-permissions")
    @click.argument("mapping_file", type=click.Path(exists=True, dir_okay=False))
    @click.option("--dry-run", is_flag=True,
                  help="Say what would change and change nothing.")
    @click.option("--staff-group", default=STAFF_GROUP, show_default=True,
                  help="The group that may post everywhere, including the "
                       "archive. Discourse's own, unless you have another.")
    @click.option("--verbose", is_flag=True,
                  help="A line per category rather than only the ones that "
                       "were open.")
    @click.option("--enforce", is_flag=True,
                  help="Also overwrite categories somebody has already given "
                       "permissions to. Without it those are reported and left "
                       "alone, because the forum is where that decision lives.")
    @click.option("--api-key", envvar="DISCOURSE_MIGRATION_API_KEY",
                  help="An 'All Users' Discourse API key.")
    @with_appcontext
    def forum_permissions_command(mapping_file, dry_run, staff_group,
                                  verbose, enforce, api_key):
        """Members only: who may read, reply and post in each category.

        A Discourse category with no group permission on it is public. Not
        "visible once you are logged in" -- public, to anybody and to every
        crawler. So the categories this import makes are open until something
        says otherwise, and what goes in them is thirteen years of exams and
        transcripts with students' names on them.

        The import does this itself once the categories exist and before it
        posts anything. This command is the same work on its own, for a forum
        that was imported before it did, for a structure that has been changed
        by hand since, and for checking -- with --dry-run -- what the position
        actually is.

        Live lectures: members may start topics and reply, because students
        keep adding to them. The archive: members may read and search it and
        nothing else. Categories that are not part of this mapping, including
        Discourse's own, are left exactly as they are and counted.
        """
        service = get_forum_service()
        if not service.is_enabled() or service.config_errors:
            raise click.ClickException("The forum integration is not configured.")

        settings = dict(service.settings)
        if api_key:
            settings["discourse_api_key"] = api_key
        poster = ContentPoster(settings)
        _warn_about_the_key(poster)

        worksheet = json.loads(Path(mapping_file).read_text(encoding="utf-8"))
        roots = owned_roots(worksheet)
        click.echo(
            f"{len(roots)} top-level categories belong to this mapping: "
            + ", ".join(sorted(roots))
        )
        _say_what_is_not_set_up(service)

        _make_the_groups(service, staff_group=staff_group, dry_run=dry_run)
        report = _restrict_the_categories(
            poster, service, roots, dry_run=dry_run, staff_group=staff_group,
            verbose=verbose, enforce=enforce,
        )
        if report is None:
            raise click.ClickException("Nothing was restricted.")

        if dry_run:
            click.echo(click.style(
                "\nNothing was changed. Run it again without --dry-run.",
                fg="cyan",
            ))
        else:
            click.echo(click.style(
                "\nCategory permissions are not the same thing as a private "
                "forum: with login_required off, anonymous visitors still see "
                "the site and anything still public on it.", fg="cyan",
            ))

    @app.cli.command("publish-forum-profiles")
    @click.option("--dry-run", is_flag=True,
                  help="Report what would be published and send nothing.")
    @click.option("--limit", type=int, default=0, metavar="N",
                  help="Publish at most N people, for a first careful run.")
    @click.option("--only-new", is_flag=True,
                  help="Skip people already published, to resume an interrupted run.")
    @click.option("--sample", type=int, default=0, metavar="N",
                  help="Show N of them in full.")
    @click.option("--groups-only", is_flag=True,
                  help="Publish nobody; only make the groups and put people in "
                       "them. Repairs a run whose groups came out empty.")
    @with_appcontext
    def publish_forum_profiles_command(dry_run, limit, only_new, sample, groups_only):
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
            if groups_only:
                # Nothing is published, so no profile field is written.
                pass
            elif dry_run:
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

        # The groups are made before anybody is published, and were not always:
        # Discourse's SSO add_groups matches the names it is given against the
        # groups that already exist and ignores the rest without complaining, so
        # making them afterwards -- "only for a cohort that has somebody in it"
        # -- produced a register of thirty-four empty groups and a report that
        # said otherwise, because a report counts what was sent.
        profiles = profiles_to_publish(only_unsynced=only_new)
        if limit:
            profiles = profiles[:limit]
        plan = groups_for_profiles(profiles)
        click.echo(
            f"{len(plan)} groups: "
            + ", ".join(f"{name} ({len(members)})"
                        for name, members in sorted(plan.items()))[:400]
        )

        def say_group(name, size, created):
            if created:
                click.echo(f"  created group {name} ({size})")

        if not dry_run and not groups_only:
            _report_group_problems(
                sync_profile_groups(provider, plan, add_members=False,
                                    on_group=say_group)
            )

        report = {"seen": 0, "published": 0, "failed": 0, "with_avatar": 0,
                  "groups": plan, "problems": [], "people": []}
        if not groups_only:
            settings_client = _forum_settings_client(service)
            if settings_client is not None:
                # Everything the forum has to allow before these people can be
                # published, done here rather than remembered: a forum rebuilt
                # from scratch comes back with Discourse's defaults, and the
                # step nobody can forget is the one nobody has to do.
                _mind_the_avatar_setting(settings_client, dry_run=dry_run)
                _mind_the_address_and_name(settings_client, dry_run=dry_run)
                _mind_the_username_length(
                    settings_client, username_room_needed(profiles), dry_run=dry_run
                )
            report = publish_imported_profiles(
                provider,
                dry_run=dry_run,
                limit=limit or None,
                only_unsynced=only_new,
                year_group_field=year_group_field,
                # A dry run sends nothing and is over in seconds, so thirty
                # progress lines would be thirty lines of noise.
                on_progress=None if dry_run else _profile_progress_reporter(),
            )

        if report.get("stopped"):
            # Nobody got through, so filling the groups would only be thirty-four
            # more refusals about people the forum has never heard of.
            for problem in report["problems"][:3]:
                click.echo(click.style(f"  ! {problem}", fg="yellow"), err=True)
            raise click.ClickException(report["stopped"])

        if not dry_run:
            # Membership is set here as well as in the SSO payload, because the
            # payload is not a way to make somebody a member -- it only works
            # for a group that was already there -- and this is the one step
            # that can be run on its own to repair a register that came out
            # empty. Eight calls per hundred people, against two per person.
            filled = sync_profile_groups(provider, plan, add_members=True)
            click.echo(
                f"groups: {filled['groups']} there, {filled['created']} made, "
                f"{filled['members']} memberships set, "
                f"{filled['already_in']} already in place"
            )
            _report_group_problems(filled)
            db.session.commit()

        click.echo(
            f"seen={report['seen']} published={report['published']} "
            f"failed={report['failed']} with_avatar={report['with_avatar']}"
        )
        if sample:
            _echo_profile_sample(report, sample)
        for problem in report["problems"][:20]:
            click.echo(click.style(f"  ! {problem}", fg="yellow"), err=True)
        if dry_run:
            click.echo(click.style("Dry run: nothing was sent.", fg="cyan"))

    def _forum_settings_client(service):
        """Something that can read and write the forum's own settings.

        The provider talks Connect and nothing else, and this needs the admin
        settings API, so it borrows the same credentials the provider uses.
        Returns None rather than raising: not being able to read a setting is a
        reason to say so and publish anyway.
        """
        settings = getattr(service, "settings", None)
        if not settings:
            click.echo(click.style(
                "Avatars: this forum integration does not expose its settings, "
                "so the setting that governs them cannot be checked.", fg="yellow"))
            return None
        return ContentPoster(dict(settings))

    def _make_the_groups(service, *, staff_group=STAFF_GROUP, dry_run=False):
        """Make sure every group this arrangement needs is on the forum.

        First, always. A Connect payload's ``add_groups`` is not a way to make
        a group: Discourse matches the names against what it already has and
        drops the rest without a word, so a person published before the group
        existed is in no group at all and re-sending the payload changes
        nothing. Thirty-four empty groups on a finished run is what that looks
        like from the outside.
        """
        settings = getattr(service, "settings", {}) or {}
        wanted = groups_wanted(
            settings, staff_group=staff_group,
            # Every group anything here names has to exist before a soul is put
            # in one: a Connect payload cannot make a group, only fill one that
            # is already there, and it says nothing when it cannot.
            extra=list(member_category_groups(settings).values())
            + access_groups(settings, "forum_lecture_groups")
            + access_groups(settings, "forum_archive_groups"),
        )
        if dry_run:
            click.echo(f"groups: would make sure {len(wanted)} exist: "
                       f"{', '.join(wanted)}")
            return wanted

        provider = service.provider
        if not hasattr(provider, "ensure_group"):
            # A provider that cannot make groups is a provider this cannot be
            # done through, which is worth a line rather than a traceback in
            # the middle of a two-hour import.
            click.echo(click.style(
                "This forum provider cannot make groups, so they have to exist "
                "already. Make them on the forum before publishing anybody.",
                fg="yellow",
            ), err=True)
            return wanted

        made = []
        for name in wanted:
            try:
                _group, created = provider.ensure_group(name)
            except ForumProviderError as exc:
                click.echo(click.style(f"  ! group {name}: {exc}", fg="yellow"),
                           err=True)
                continue
            if created:
                made.append(name)
        click.echo(
            f"groups: {len(wanted)} needed, {len(made)} made"
            + (f" ({', '.join(made)})" if made else "")
        )
        return wanted

    def _say_what_is_not_set_up(service):
        """Before the run, not deduced from its results afterwards."""
        missing = what_is_not_set_up(getattr(service, "settings", {}) or {})
        if not missing:
            return
        click.echo(click.style(
            f"\n{len(missing)} things are not decided yet, under "
            f"Admin -> Settings -> Forum:", fg="yellow",
        ))
        for name, why in missing:
            click.echo(click.style(f"  {name}\n    {why}", fg="yellow"))
        click.echo("")

    def _no_lecture_groups():
        return (
            "No group is named as being allowed to read the lecture material, "
            "so there is nobody to grant it to and every category would stay "
            "public.\n\nSet 'Groups That May Read The Lecture Material' under "
            "Admin -> Settings -> Forum. It is empty to begin with on purpose: "
            "the obvious answer, everybody who has paid, is the wrong one. "
            "Lecturers and company representatives are paying members of this "
            "association too, and the material is a decade of exams about the "
            "lectures they give."
        )

    def _restrict_the_categories(poster, service, roots, *, dry_run=False,
                                 staff_group=STAFF_GROUP, verbose=False,
                                 enforce=False):
        """Give every category this import owns to the members, and nobody else.

        A category with no group permission on it is public -- not "visible
        once you are logged in", public. So this is not a hardening step to do
        afterwards: it is the difference between an archive of exams with
        students' names in it being members-only and being indexed.
        """
        settings = getattr(service, "settings", {}) or {}
        lecture = access_groups(settings, "forum_lecture_groups")
        archive = access_groups(settings, "forum_archive_groups", lecture)
        portal_staff = (settings.get("forum_staff_group") or "").strip()
        if not lecture:
            click.echo(click.style(_no_lecture_groups(), fg="yellow"), err=True)
            return None

        try:
            categories = poster.categories()
        except ForumProviderError as exc:
            click.echo(click.style(
                f"Could not read the categories, so none were restricted: {exc}",
                fg="yellow",
            ), err=True)
            return None

        plan, untouched = permission_plan(
            categories, roots, lecture_groups=lecture, archive_groups=archive,
            staff_groups=(staff_group, portal_staff),
        )
        if not plan:
            click.echo(click.style(
                "None of the categories on this forum belong to this mapping, "
                "so nothing was restricted. Check that the categories were made "
                "before this ran.", fg="yellow",
            ), err=True)
            return None

        def say(name, grants, *, changed, was_public):
            if verbose or (changed and was_public):
                mark = "public until now" if was_public else "changed"
                click.echo(f"  {name}: {describe(grants)}"
                           + (f"  ({mark})" if changed else "  (already)"))

        report = apply_permissions(
            poster, plan, dry_run=dry_run, enforce=enforce, on_category=say,
        )
        report["category_ids"] = list(plan)
        click.echo(
            f"permissions: {report['categories']} categories, "
            f"{report['set']} set, {report['already']} already right"
            + (f", {report['opened']} of them public until now"
               if report["opened"] else "")
            + (f", {report['decided_elsewhere']} decided on the forum and "
               f"left alone" if report["decided_elsewhere"] else "")
        )
        if untouched:
            click.echo(
                f"  {len(untouched)} categories are not part of this mapping "
                f"and were left alone: "
                + ", ".join(name for _id, name in untouched[:5])
                + (" ..." if len(untouched) > 5 else "")
            )
        for problem in report["problems"]:
            click.echo(click.style(f"  ! {problem}", fg="yellow"), err=True)
        return report

    def _category_gatekeeper(service, roots, *, dry_run=False, authors_group=None,
                             mapping_file=None):
        """What the import calls once the categories are there.

        Bound here rather than written into ``migrate_board`` so that the board
        importer stays a thing that moves posts, and who may read them stays a
        question answered in one place.

        With ``authors_group``, that group may also post in every category for
        as long as the run lasts, and ``close()`` takes it away again. The
        authors are posted as, and Discourse checks each of them against the
        category: the first run with the categories restricted before posting
        had every post refused, because the old forum's people are not in the
        groups the material is granted to -- and should not be.
        """
        opened = {}

        def restrict(poster):
            _make_the_groups(service, dry_run=dry_run)
            report = _restrict_the_categories(poster, service, roots, dry_run=dry_run)
            if dry_run or not authors_group or not report:
                return
            ids = report.get("category_ids") or []
            result = let_authors_post(poster, ids, authors_group, allow=True)
            opened.update(poster=poster, ids=ids)
            click.echo(
                f"authors: {authors_group} may post in {len(ids)} categories "
                f"while this runs, so the archive can be posted as the people "
                f"who wrote it. It is taken away again at the end."
            )
            for problem in result["problems"]:
                click.echo(click.style(f"  ! {problem}", fg="yellow"), err=True)

        def close():
            if not opened:
                return
            result = let_authors_post(
                opened["poster"], opened["ids"], authors_group, allow=False,
            )
            opened.clear()
            if result["problems"]:
                click.echo(click.style(
                    f"COULD NOT TAKE POSTING AWAY FROM {authors_group} on "
                    f"{len(result['problems'])} categories. Put them back with:\n"
                    f"  flask forum-permissions {mapping_file or 'categories.json'} "
                    f"--enforce", fg="red",
                ), err=True)
                for problem in result["problems"]:
                    click.echo(click.style(f"  ! {problem}", fg="red"), err=True)
            else:
                click.echo(
                    f"\nauthors: {authors_group} may no longer post in the "
                    f"imported categories ({result['changed']} changed back)."
                )

        restrict.close = close
        return restrict

    def _mind_the_avatar_setting(client, *, dry_run):
        """Make sure the avatars we send are the avatars people see.

        Discourse will fetch the picture and then keep its letter unless
        ``discourse_connect_overrides_avatar`` is on. Nothing fails, nothing is
        logged, and the run reports ``with_avatar=676`` either way -- which is
        how 739 profiles came out blank on a run that said it had sent them all.

        Turned on and left on, because the portal is where a photograph is
        uploaded and approved, and the same setting governs members: with it
        off, an approved avatar is ignored there too.
        """
        try:
            state = avatar_setting_state(client)
        except ForumProviderError as exc:
            click.echo(click.style(
                f"Avatars: could not read the setting that governs them -- {exc}. "
                f"If the profiles come out with letters on them, that is why.",
                fg="yellow"))
            return None

        if state is None:
            click.echo("Avatars: this forum has no setting for them; sending them as they are.")
            return None

        name, value, in_use = state
        if in_use:
            click.echo(f"Avatars: {name} is on, so the pictures will be used.")
            return None
        if dry_run:
            click.echo(click.style(
                f"Avatars: {name} is {value}, so the forum would fetch every "
                f"picture and then show a letter instead. The real run turns it "
                f"on and leaves it on.", fg="cyan"))
            return None
        try:
            changed = let_avatars_through(client)
        except ForumProviderError as exc:
            click.echo(click.style(
                f"Avatars: {name} is {value} and could not be changed -- {exc}. "
                f"The profiles will come out with letters on them.", fg="yellow"))
            return None
        click.echo(
            f"Avatars: {changed} was {value}, turned on and left on -- the "
            f"portal is where avatars are uploaded and approved, so it is what "
            f"the forum shows. Members are governed by the same setting."
        )
        return changed

    def _mind_the_address_and_name(client, *, dry_run):
        """Make the forum take the address and name the portal sends. Left on."""
        try:
            rows = portal_owned_settings_state(client)
        except ForumProviderError as exc:
            click.echo(click.style(
                f"Address and name: could not read the settings -- {exc}.", fg="yellow"))
            return
        for what, name, on, why in rows:
            if name is None:
                click.echo(f"Address and name: this forum has no setting for the {what}.")
            elif on:
                click.echo(f"Address and name: {name} is on.")
            elif dry_run:
                click.echo(click.style(
                    f"Address and name: {name} is off, so {why}. The real run "
                    f"turns it on and leaves it on.", fg="cyan"))
        if dry_run:
            return
        try:
            changed = let_the_portal_own_address_and_name(client)
        except ForumProviderError as exc:
            click.echo(click.style(
                f"Address and name: could not be turned on -- {exc}.", fg="yellow"))
            return
        for name in changed:
            click.echo(f"Address and name: {name} turned on and left on -- the "
                       f"portal is where both are kept.")

    def _mind_the_username_length(client, needed, *, dry_run):
        """Make the forum accept the names these people actually have.

        Discourse stores twenty characters by default and shortens the rest as
        it creates the account, without a word. Four of this board's people are
        on the forum under names Discourse chose, and everything that addresses
        them by name -- posting their old messages as them -- failed.

        Raised and left raised: put back to twenty it would mangle the name of
        the next student called Niedergrottenthaler, which is a limit that
        punishes somebody for their surname.
        """
        try:
            state = username_length_state(client, needed)
        except ForumProviderError as exc:
            click.echo(click.style(
                f"Usernames: could not read {USERNAME_LENGTH_SETTING} -- {exc}. "
                f"Names longer than the forum allows will be shortened by it, "
                f"quietly.", fg="yellow"))
            return None

        if state is None:
            click.echo("Usernames: this forum publishes no limit on their length.")
            return None

        setting, value, big_enough = state
        if big_enough:
            click.echo(f"Usernames: {setting} is {value}, and {needed} is needed.")
            return None
        if dry_run:
            click.echo(click.style(
                f"Usernames: {setting} is {value} and {needed} is needed, so the "
                f"forum would shorten the longer names without saying so. The "
                f"real run raises it and leaves it raised.", fg="cyan"))
            return None
        try:
            changed = make_room_for_usernames(client, needed)
        except ForumProviderError as exc:
            click.echo(click.style(
                f"Usernames: {setting} is {value}, {needed} is needed, and it "
                f"could not be changed -- {exc}. The longer names will be "
                f"shortened by the forum.", fg="yellow"))
            return None
        click.echo(
            f"Usernames: {setting} raised from {value} to {needed} and left "
            f"there -- the portal's names are surname, initial and cohort, and "
            f"this board has surnames that need every character of it."
        )
        return changed

    def _report_group_problems(report):
        renamed = report.get("renamed") or {}
        if renamed:
            click.echo(
                f"  {len(renamed)} known on the forum by another name, and put "
                f"in their groups under it:"
            )
            for old, new in sorted(renamed.items()):
                click.echo(f"    {old} -> {new}")
        for problem in report["problems"]:
            click.echo(click.style(f"  ! group {problem}", fg="yellow"), err=True)

    def _profile_progress_reporter(every=25):
        """A line every ``every`` people, and a commit with it.

        Seven hundred profiles against the forum's rate limit is half an hour.
        The first real run printed one line and then nothing until it finished,
        which reads exactly like a command that has hung -- and the obvious way
        to find out, asking the forum how full the group is, spends the same
        admin rate limit the run is spending and so slows down the thing it is
        checking on.

        The commit is here rather than only at the end for the same reason the
        run is resumable at all: an interrupted run that never wrote down who it
        had already published has to start over, and ``--only-new`` would have
        nothing to skip.
        """
        started = time.monotonic()
        state = {"reported": 0}

        def report_progress(done, total, running):
            if done < total and done - state["reported"] < every:
                return
            state["reported"] = done
            db.session.commit()
            elapsed = time.monotonic() - started
            left = ""
            # Not in the first minute: an estimate drawn from the first few of
            # seven hundred calls is a guess dressed up as a number.
            if done and done < total and elapsed >= 60:
                remaining = elapsed / done * (total - done)
                left = f", about {round(remaining / 60)} min left"
            click.echo(
                f"  {done}/{total} -- published {running['published']}, "
                f"failed {running['failed']}{left}"
            )

        return report_progress

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
            if person.get("avatar_url"):
                # The forum fetches this itself, from its own container. When it
                # cannot, nothing says so and the profile just keeps its letter,
                # so the URL is printed to make that one curl away.
                click.echo(f"      the forum fetches it from: {person['avatar_url']}")

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
        if _stand_down_while_paused("billing-reconcile"):
            return
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
                    # The cached dates run past what the ledger covers: a paid
                    # year whose invoice.paid never arrived, or one still being
                    # collected. Without this, a renewal repaired only in the
                    # cache has a date far in the future and is never looked
                    # at again.
                    and_(
                        Member.membership_ends_on >= today,
                        ~db.select(MembershipPeriod.id)
                        .where(
                            MembershipPeriod.member_id == Member.id,
                            MembershipPeriod.revoked_at.is_(None),
                            MembershipPeriod.ends_on >= Member.membership_ends_on,
                        )
                        .exists(),
                    ),
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

        # The teams' nightly steps, in this order. Each on its own: one that
        # fails is logged and counted, and the others still run.
        from .services.team_payments import (
            end_finished_team_memberships, follow_association_ends, send_renewal_notices,
        )
        from .services.team_money import fill_in_fees
        from .services.mailings import forget_old as forget_old_mailing_recipients
        from .services.messages import forget_old as forget_old_messages
        from .services.teams import end_lapsed_team_memberships, lapse_unpaid_approvals, send_due_access_lists

        team_steps = (
            # Whoever has cancelled their association membership has their teams set
            # to end on the same day, in case the webhook that says so was missed.
            (follow_association_ends,
             "Brought {} team membership(s) in line with a cancelled or resumed association membership."),
            # Whoever is no longer in the association leaves their teams too.
            (end_lapsed_team_memberships, "Ended {} team membership(s) of people no longer in the association."),
            # Approvals for teams that charge, not paid for in time, lapse.
            (lapse_unpaid_approvals, "{} team approval(s) lapsed unpaid."),
            # Paid once per period: the reminder to pay for the next one.
            (send_renewal_notices, "Reminded {} team member(s) to pay for the next period."),
            # A leaving day passed, a period not paid for, or long unpaid.
            (end_finished_team_memberships, "Ended {} team membership(s) that ran out or were not paid."),
            # Then the access lists due today, now that the teams are current.
            (send_due_access_lists, "Sent {} team access list(s)."),
            # Stripe's fees of team payments that carry theirs (Admin › Money).
            (fill_in_fees, "Filled in Stripe's fee for {} team payment(s)."),
            # Messages done a year ago, and mailings' recipients after a year
            # (docs/messages-plan.md).
            (forget_old_messages, "Forgot {} old message(s)."),
            (forget_old_mailing_recipients, "Forgot {} mailing recipient(s) older than a year."),
        )
        for step, done_text in team_steps:
            try:
                count = step()
                db.session.commit()
                flush_marked_notification_channels()
            except Exception as exc:  # noqa: BLE001 -- logged; tried again tomorrow
                db.session.rollback()
                current_app.logger.exception("Nightly team step %s failed.", step.__name__)
                error_count += 1
                click.echo(click.style(f"Team step {step.__name__} failed: {exc}", fg="red"), err=True)
                continue
            if count:
                click.echo(done_text.format(count))

        # Signups never paid for: a notice, then removal (90 days; see
        # services/unfinished_signups.py). Never allowed to stop the rest.
        from .services.unfinished_signups import clean_up as clean_up_unfinished_signups

        try:
            unfinished = clean_up_unfinished_signups()
            db.session.commit()
            flush_marked_notification_channels()
        except Exception:  # noqa: BLE001 -- logged; tried again tomorrow
            db.session.rollback()
            current_app.logger.exception("Cleaning up unfinished signups failed.")
        else:
            if unfinished["noticed"] or unfinished["removed"]:
                click.echo(f"Unfinished signups: {unfinished['noticed']} told, {unfinished['removed']} removed.")

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

    def _sync_the_ones_that_drifted():
        """The daily sweep: only what the forum is now wrong about.

        Everything that changes a membership is an event somebody causes, and
        each of those syncs the forum where it happens. A membership ending
        because its last day passed is not an event: nobody does anything, so
        nothing tells the forum, and the person goes on reading the archive
        until somebody happens to touch their record.
        """
        drifted = members_whose_forum_state_has_drifted()
        if not drifted:
            click.echo("Nothing has drifted: the forum agrees with this portal.")
            return

        click.echo(f"{len(drifted)} members the forum is out of date about:")
        synced, failed = 0, 0
        for member, was, should_be in drifted:
            result, _service = sync_member_forum_state(member)
            if result is not None and result.error:
                failed += 1
                click.echo(click.style(
                    f"  ! member {member.id}: {was} -> {should_be} failed: "
                    f"{result.error}", fg="yellow"), err=True)
                continue
            synced += 1
            click.echo(f"  member {member.id}: {was} -> {should_be}")
        db.session.commit()
        flush_marked_notification_channels()
        click.echo(click.style(
            f"{synced} brought up to date"
            + (f", {failed} could not be" if failed else ""),
            fg="green" if not failed else "yellow",
        ))

    @app.cli.command("forum-likely-old-accounts")
    @with_appcontext
    def forum_likely_old_accounts_command():
        """Members whose old forum account probably did not reconnect by itself.

        The old forum never checked addresses, so a mistyped one there never
        matches. Lists, for every member without an old account, the unclaimed
        ones that differ only in dots or spelling from an address they
        confirmed, or carry the same name -- to reconnect by hand on the
        account page if they are theirs. Changes nothing.
        """
        from .services.forum_import import LIKELY_BY_ADDRESS, likely_old_accounts, unclaimed_profiles

        profiles = unclaimed_profiles()
        users = db.session.execute(
            db.select(User).join(Member, Member.user_id == User.id).where(User.deleted_at.is_(None))
        ).scalars().all()
        found = 0
        for user in users:
            for profile, reason in likely_old_accounts(user, profiles):
                why = "address differs only in dots or spelling" if reason == LIKELY_BY_ADDRESS else "same name"
                click.echo(f"{user.member.first_name} {user.member.last_name} <{user.email}> (account {user.id})"
                           f" -> {profile.source_username} <{profile.source_email or '-'}>: {why}")
                found += 1
        click.echo(f"{found} likely old account(s).")

    @app.cli.command("forum-explain")
    @click.argument("who")
    @with_appcontext
    def forum_explain_command(who):
        """Everything the portal and the forum hold about one person, side by side.

        WHO is an email address or a forum username. For "why did this not
        reach the forum": what the portal would send on the next sync, what the
        forum actually has, any other forum account holding the same address,
        and the queued work still waiting for this person. Reads only; changes
        nothing on either side.
        """
        from .db_models import ExternalWorkItem

        needle = who.strip()
        user = db.session.execute(
            db.select(User).where(
                (func.lower(User.email) == needle.lower())
                | (User.forum_username == needle)
            )
        ).scalars().first()
        if user is None:
            raise click.ClickException(f"Nobody here with the address or forum name {who}.")

        service = get_forum_service()
        member = user.member
        account = user.forum_account
        click.echo(click.style("In the portal", bold=True))
        click.echo(f"  account          {user.id}  {user.email}"
                   f"  ({'verified' if user.email_is_verified else 'address NOT verified'})")
        click.echo(f"  forum username   {user.forum_username or '-'}")
        click.echo(f"  roles            {', '.join(sorted(r.slug for r in user.roles)) or 'none'}")
        click.echo(f"  switched off     {'YES' if user.is_disabled else 'no'}")
        click.echo(f"  membership       {'none' if member is None else member.payment_status}")
        if account is not None:
            click.echo(f"  forum link       external_id={account.external_id}  "
                       f"remote_user_id={account.remote_user_id}  state={account.state}")
            if account.last_error:
                click.echo(click.style(f"  last error       {account.last_error}", fg="yellow"))

        if not service.is_ready():
            click.echo(click.style("\nThe forum integration is not ready, so nothing is sent.", fg="yellow"))
            return

        desired = service.get_desired_state(member) if member is not None else None
        payload = service.provider.build_sso_payload(user, member, desired, nonce="preview")
        click.echo(click.style("\nWhat the next sync sends", bold=True))
        for key in ("external_id", "username", "email", "name", "require_activation",
                    "add_groups", "remove_groups", "admin", "moderator"):
            if key in payload:
                click.echo(f"  {key:<18} {payload[key]}")
        if "admin" not in payload:
            click.echo("  admin/moderator    not sent -- the portal does not manage "
                       "them (Settings -> Forum)")

        click.echo(click.style("\nWhat the forum has", bold=True))
        remote = {}
        try:
            remote = service.provider.get_remote_user_by_external_id(
                account.external_id if account is not None else str(user.id)
            ) or {}
        except ForumProviderError as exc:
            click.echo(f"  no forum account for this person yet ({exc})")
        if remote:
            click.echo(f"  account          {remote.get('id')}  {remote.get('username')}")
            click.echo(f"  admin            {remote.get('admin')}")
            click.echo(f"  moderator        {remote.get('moderator')}")
            groups = [g.get("name") for g in remote.get("groups") or [] if not g.get("automatic")]
            click.echo(f"  groups           {', '.join(sorted(filter(None, groups))) or '-'}")

        try:
            others = service.provider._request(
                "GET", "/admin/users/list/all.json?show_emails=true&filter="
                + quote(user.email or needle),
            )
        except ForumProviderError:
            others = []
        others = [row for row in (others or []) if row.get("id") != remote.get("id")]
        if others:
            click.echo(click.style(
                "\nOther forum accounts with this address -- Discourse gives an "
                "address to one account only:", fg="yellow"))
            for row in others:
                click.echo(f"  {row.get('id')}  {row.get('username')}  "
                           f"active={row.get('active')}  posts={row.get('post_count')}")

        items = db.session.execute(
            db.select(ExternalWorkItem)
            .where(ExternalWorkItem.user_id == user.id)
            .order_by(ExternalWorkItem.created_at.desc())
        ).scalars().all()
        if items:
            click.echo(click.style("\nQueued work for this person", bold=True))
            for item in items[:10]:
                click.echo(f"  {format_datetime_display(item.created_at)}  {item.kind:<24} "
                           f"{item.status:<10} attempts={item.attempts}"
                           + (f"  {item.last_error[:80]}" if item.last_error else ""))

    @app.cli.command("sync-forum-members")
    @click.option("--only-active", is_flag=True, help="Only synchronize active members.")
    @click.option("--only-changed", is_flag=True,
                  help="Only the members whose forum state no longer matches "
                       "what this portal says it should be. Cheap enough to "
                       "run daily, which is what catches a membership that "
                       "ended simply because its last day passed.")
    @with_appcontext
    def sync_forum_members(only_active, only_changed):
        """Synchronizes forum state for many linked members."""
        if _stand_down_while_paused("forum-drift"):
            return
        if only_changed:
            return _sync_the_ones_that_drifted()
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
        if _stand_down_while_paused("notifications"):
            return
        email_summary = process_email_delivery_jobs(app)
        db.session.commit()
        summary = get_notification_service().deliver_pending_notifications()
        # Contact messages whose email has not gone out yet, and the
        # mailings' queue, within the provider's limits (services/mailings.py).
        from .services import mailings as mailings_service
        from .services import messages as messages_service

        for step, done_text in ((messages_service.deliver_waiting, "Delivered {} waiting message(s)."),
                                (mailings_service.process, "Sent {} mailing email(s).")):
            try:
                count = step()
            except Exception:  # noqa: BLE001 -- logged; tried again on the next run
                db.session.rollback()
                app.logger.exception("Notification step %s failed.", step.__name__)
                continue
            if count:
                click.echo(done_text.format(count))
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
    @with_appcontext
    def cleanup_pending_signups():
        """Tells, then removes, signups never paid for (also run nightly by reconcile-billing)."""
        from .services.unfinished_signups import clean_up as clean_up_unfinished_signups

        result = clean_up_unfinished_signups()
        db.session.commit()
        flush_marked_notification_channels()
        click.echo(click.style(
            f"Unfinished signups: {result['noticed']} told, {result['removed']} removed.", fg="green"))

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

        if health["pages"] and health["pages"]["note"]:
            click.echo(f"Pages: {health['pages']['note']}")

        for problem in health["problems"]:
            click.echo(f"PROBLEM: {problem}", err=True)
        for warning in health["warnings"]:
            click.echo(f"WARNING: {warning}")

        if health["healthy"]:
            click.echo("Everything looks healthy.")
        else:
            sys.exit(1)

    @app.cli.command("api-schema")
    @click.option("--out", default="-", show_default=True, help="File to write; - for the screen.")
    @with_appcontext
    def api_schema_command(out):
        """Write the API's OpenAPI description (from api/), for the front end's types."""
        from .api import openapi

        text = json.dumps(openapi.build(), indent=2, sort_keys=True) + "\n"
        if out == "-":
            click.echo(text, nl=False)
        else:
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_text(text, encoding="utf-8")
            click.echo(f"Written to {out}.")

    @app.cli.command("build-legal-pdfs")
    @click.option("--again", is_flag=True, help="Make each anew, even when a kept one looks current.")
    @with_appcontext
    def build_legal_pdfs_command(again):
        """Make the PDF of every legal text version shown (into storage/legal_pdf).

        The pages make each one when it is first asked for; this makes them all
        ahead, and says which text cannot be laid out.
        """
        from . import legal_pdf

        failed = 0
        for version, result in legal_pdf.build_all(again=again):
            name = f"{'teams/' + version.team + '/' if version.team else ''}{version.slug}/{version.version.isoformat()}"
            if isinstance(result, Exception):
                failed += 1
                click.echo(click.style(f"  {name}: {result}", fg="red"))
            else:
                click.echo(f"  {name}: {result // 1024} KB")
        if failed:
            raise click.ClickException(f"{failed} PDF(s) could not be made.")

    @app.cli.command("process-external-work")
    @click.option("--limit", default=50, show_default=True, type=int,
                  help="Maximum number of work items to process in this run.")
    @with_appcontext
    def process_external_work(limit):
        """Perform queued work against other systems (currently Discourse sync).

        Items are claimed under a lease, so running this while another copy is
        already running is safe -- a second worker simply finds nothing to claim.
        """
        if _stand_down_while_paused("external-work"):
            return
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
        if _stand_down_while_paused("cleanup-logs"):
            return
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






























    # The API answers its errors as JSON (api/_core.py); these handlers serve
    # the pages and pass the API's requests on.
    from .api import error as api_error, is_api_request
    from .blueprints.app_shell import app_shell

    def error_page(status, title, *lines):
        """The plain error page (templates/error.html), rendered without the
        site's context processors: it must still work when the database failed."""
        html = app.jinja_env.get_template("error.html").render(
            title=title, lines=lines, test_server=bool(app.config.get("TEST_SERVER")))
        return html, status

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        if is_api_request():
            return api_error(400, "csrf_failed", "The session has expired. Reload the page and try again.")
        return error_page(400, "This page was open too long",
                          "Your session has expired. Go back, reload the page and try again.")

    @app.errorhandler(RateLimitExceeded)
    def handle_rate_limit_error(e):
        if is_api_request():
            return api_error(429, "rate_limited", "Too many requests. Please wait a moment and try again.")
        # A page, not a redirect: browsers do not follow a Location header on a
        # 429, so a redirect left the member on an unstyled "Redirecting..." page.
        return error_page(
            429, "Too many attempts",
            "We have had a lot of requests from your network in a short time, so this one was held back.",
            "Please wait a few minutes and try again. On university WiFi, someone else on the same "
            "network may have caused this.")

    @app.errorhandler(413)
    def request_entity_too_large(e):
        if is_api_request():
            return api_error(413, "too_large", "The upload is too large.")
        return error_page(413, "Too large", "What was sent is too large. Please send a smaller file.")

    @app.errorhandler(404)
    def page_not_found(e):
        if is_api_request():
            return api_error(404, "not_found", "Not found.")
        # The app says so, in its own frame (pages/NotFound.tsx).
        response = app_shell()
        if response.status_code == 200:
            response.status_code = 404
        return response

    @app.errorhandler(405)
    def method_not_allowed(e):
        if is_api_request():
            return api_error(405, "method_not_allowed", "Not possible with this method.")
        return e

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
        if is_api_request():
            return api_error(500, "server_error", "Something went wrong on our side. Please try again later.")
        return error_page(500, "Something went wrong",
                          "Sorry, something went wrong on our side. We have been told and are looking into it.")

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





























