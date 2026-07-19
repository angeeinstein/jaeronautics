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

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
LEGACY_APP_DIR = REPO_ROOT / "var" / "www" / "aeronautics-members"
ROOT_ENV_PATH = REPO_ROOT / ".env"
LEGACY_ENV_PATH = LEGACY_APP_DIR / ".env"
TRANSLATIONS_DIR = PACKAGE_DIR / "translations"
PYBABEL_CONFIG = PACKAGE_DIR / "babel.cfg"
MESSAGES_POT = PACKAGE_DIR / "messages.pot"

# Prefer the new root-level .env file, but keep the legacy location as a fallback.
load_dotenv(LEGACY_ENV_PATH)
load_dotenv(ROOT_ENV_PATH, override=True)

# --- Configuration Setup ---
SECRET_KEY = os.getenv("SECRET_KEY")
LANGUAGES = os.getenv("LANGUAGES", "en,de").split(",")
DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = quote_plus(os.getenv("DB_PASSWORD", ""))
DB_PORT = os.getenv("DB_PORT", "3306")

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
PUBLIC_BASE_URL = normalize_public_base_url(os.getenv("PUBLIC_BASE_URL"))
ADDITIONAL_ALLOWED_HOSTS = os.getenv("ADDITIONAL_ALLOWED_HOSTS", "")
STRIPE_SETTING_KEYS = ("stripe_publishable_key", "stripe_secret_key", "stripe_price_id", "stripe_webhook_secret")
DEFAULT_STRIPE_SETTINGS = {
    "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY or "",
    "stripe_secret_key": STRIPE_SECRET_KEY or "",
    "stripe_price_id": STRIPE_PRICE_ID or "",
    "stripe_webhook_secret": STRIPE_WEBHOOK_SECRET or "",
}
MEMBERSHIP_TIMEZONE_NAME = os.getenv("MEMBERSHIP_TIMEZONE", "Europe/Vienna")
try:
    MEMBERSHIP_TIMEZONE = ZoneInfo(MEMBERSHIP_TIMEZONE_NAME)
except Exception:
    MEMBERSHIP_TIMEZONE = timezone.utc
    MEMBERSHIP_TIMEZONE_NAME = "UTC"
RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "redis://127.0.0.1:6379/0")
RATELIMIT_LOGIN = os.getenv("RATELIMIT_LOGIN", "10 per 15 minute")
RATELIMIT_REGISTER = os.getenv("RATELIMIT_REGISTER", "5 per hour")
RATELIMIT_MEMBERSHIP = os.getenv("RATELIMIT_MEMBERSHIP", "10 per hour")
RATELIMIT_PASSWORD_CHANGE = os.getenv("RATELIMIT_PASSWORD_CHANGE", "5 per 15 minute")
RATELIMIT_ADMIN_EMAIL = os.getenv("RATELIMIT_ADMIN_EMAIL", "5 per 10 minute")
MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", str(20 * 1024 * 1024)))

SENSITIVE_SETTING_KEYS = {"stripe_secret_key", "stripe_webhook_secret", "discourse_api_key", "discourse_connect_secret"}
SENSITIVE_AUDIT_FIELD_NAMES = SENSITIVE_SETTING_KEYS | {"password", "pass", "secret", "smtp_password", "export_password"}

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
    return db.session.get(User, int(user_id))



def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.has_role("admin"):
            flash(_("You do not have permission to access this page."), "danger")
            return redirect(url_for("public.index"))
        return f(*args, **kwargs)

    return decorated_function


DIRECT_MEMBER_PROFILE_FIELDS = (
    "street",
    "house_number",
    "postal_code",
    "city",
    "country",
    "phone_private",
    "email_private",
    "phone_work",
    "email_work",
)

IDENTITY_MEMBER_FIELDS = (
    "salutation",
    "title",
    "first_name",
    "last_name",
    "year_group",
)

MEMBER_PROFILE_FIELDS = IDENTITY_MEMBER_FIELDS + DIRECT_MEMBER_PROFILE_FIELDS
ACTIVE_MEMBER_STATUSES = {"paid", "free_period", "canceled", "cancel_scheduled"}
RESUMABLE_MEMBER_STATUSES = {"pending_checkout", "processing", "failed", "unpaid"}
TOKEN_MAX_AGE_VERIFY_EMAIL = 60 * 60 * 24 * 7
TOKEN_MAX_AGE_PASSWORD_RESET = 60 * 60 * 24
TOKEN_MAX_AGE_FORUM_ENTRY = 60 * 60 * 24 * 30
TOKEN_MAX_AGE_FORUM_ENTRY_AUTO_LOGIN = 60 * 60
EMAIL_JOB_TYPE_WELCOME = "welcome_email"
EMAIL_JOB_STATUS_PENDING = "pending"
EMAIL_JOB_STATUS_SENT = "sent"
EMAIL_JOB_STATUS_EXHAUSTED = "exhausted"
EMAIL_JOB_STATUS_CANCELED = "canceled"
WELCOME_EMAIL_RETRY_DELAYS = (
    timedelta(minutes=15),
    timedelta(hours=24),
)
PENDING_SIGNUP_RETENTION_DAYS = int(os.getenv("PENDING_SIGNUP_RETENTION_DAYS", "14"))
# Log retention. 0 means keep forever. Audit logs default to keep-forever because
# they are the account/security trail; higher-churn notification delivery records
# default to a generous one-year window.
AUDIT_LOG_RETENTION_DAYS = int(os.getenv("AUDIT_LOG_RETENTION_DAYS", "0"))
NOTIFICATION_RETENTION_DAYS = int(os.getenv("NOTIFICATION_RETENTION_DAYS", "365"))
ADMIN_DIRECTORY_PAGE_SIZE = 50
AUDIT_LOG_PAGE_SIZE = 50
APPROVAL_HISTORY_PAGE_SIZE = 25



def build_forum_username_base(first_name, last_name, year_group):
    last_name_cleaned = "".join(filter(str.isalnum, last_name or "")).capitalize()
    first_name_initial = first_name[0].upper() if first_name else ""
    study_field_initial = year_group[0].upper() if year_group else ""
    year_short = year_group[-2:] if year_group and len(year_group) > 2 else ""
    return f"{last_name_cleaned}{first_name_initial}_{study_field_initial}{year_short}"



def generate_suggested_username(member):
    """Generates the base forum username using the legacy welcome-email scheme."""
    return build_forum_username_base(member.first_name, member.last_name, member.year_group)



def generate_unique_forum_username(first_name, last_name, year_group, exclude_user_id=None, preferred=None):
    base = preferred or build_forum_username_base(first_name, last_name, year_group)
    if not base:
        base = "Member"

    candidate = base
    suffix = 2
    while True:
        query = db.select(User).filter_by(forum_username=candidate)
        if exclude_user_id is not None:
            query = query.filter(User.id != exclude_user_id)
        existing_user = db.session.execute(query).scalar_one_or_none()
        if existing_user is None:
            return candidate
        candidate = f"{base}-{suffix}"
        suffix += 1



def get_email_template_choices(app):
    template_choices = []
    email_template_dir = os.path.join(app.root_path, "templates", "emails")
    if os.path.isdir(email_template_dir):
        template_choices = [(f, f) for f in os.listdir(email_template_dir) if f.endswith(".html")]
    return template_choices



def get_db_mail_accounts():
    try:
        return db.session.execute(
            db.select(MailAccount).order_by(MailAccount.account_key.asc())
        ).scalars().all()
    except Exception:
        return []



def static_asset_version(app, filename):
    if not filename:
        return None

    asset_path = Path(app.static_folder) / filename
    try:
        return str(int(asset_path.stat().st_mtime))
    except OSError:
        return None



def get_membership_now():
    return datetime.now(timezone.utc).astimezone(MEMBERSHIP_TIMEZONE)



def get_membership_today():
    return get_membership_now().date()



def get_now_utc():
    return datetime.now(timezone.utc)



def parse_iso_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None



def to_membership_date(unix_timestamp):
    if not unix_timestamp:
        return get_membership_today()
    return datetime.fromtimestamp(unix_timestamp, timezone.utc).astimezone(MEMBERSHIP_TIMEZONE).date()



def subscription_has_scheduled_cancellation(subscription):
    if not subscription:
        return False

    if bool(subscription.get("cancel_at_period_end")):
        return True

    cancel_at = subscription.get("cancel_at")
    if cancel_at is None:
        return False

    try:
        return int(cancel_at) > int(datetime.now(timezone.utc).timestamp())
    except (TypeError, ValueError):
        return False



def first_day_of_year(year):
    return date(year, 1, 1)



def last_day_of_year(year):
    return date(year, 12, 31)



def start_of_day_unix(day_value):
    local_start = datetime.combine(day_value, datetime.min.time(), tzinfo=MEMBERSHIP_TIMEZONE)
    return int(local_start.astimezone(timezone.utc).timestamp())



def build_membership_cycle(join_date, annual_amount_cents):
    current_year = join_date.year
    next_year_start = first_day_of_year(current_year + 1)
    current_year_end = last_day_of_year(current_year)
    total_days = (first_day_of_year(current_year + 1) - first_day_of_year(current_year)).days
    remaining_days = (current_year_end - join_date).days + 1
    free_period = join_date >= date(current_year, 10, 1)
    prorated_amount_cents = 0
    if not free_period:
        prorated_amount_cents = int(
            (Decimal(annual_amount_cents) * Decimal(remaining_days) / Decimal(total_days)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )

    return {
        "join_date": join_date,
        "coverage_start": join_date,
        "coverage_end": current_year_end,
        "renewal_due_on": next_year_start,
        "trial_end_unix": start_of_day_unix(next_year_start),
        "trial_end_iso": next_year_start.isoformat(),
        "free_period": free_period,
        "prorated_amount_cents": prorated_amount_cents,
        "remaining_days": remaining_days,
        "total_days": total_days,
        "current_year": current_year,
        "thank_you_phase": "free_period" if free_period else "prorated",
    }



def get_stripe_membership_price():
    stripe_settings = apply_runtime_stripe_config()
    price_id = stripe_settings.get("stripe_price_id") or STRIPE_PRICE_ID
    if not price_id:
        raise ValueError("Stripe membership pricing is not configured.")
    price = stripe.Price.retrieve(price_id, expand=["product"])
    recurring = price.get("recurring") or {}
    interval = recurring.get("interval")
    interval_count = recurring.get("interval_count", 1)
    is_yearly = (interval == "year" and interval_count == 1) or (interval == "month" and interval_count == 12)
    if not is_yearly:
        raise ValueError(
            f"STRIPE_PRICE_ID must point to an annual recurring Stripe price. "
            f"Got interval={interval!r}, interval_count={interval_count!r}."
        )

    unit_amount = price.get("unit_amount")
    if unit_amount is None:
        raise ValueError("The Stripe membership price must have a fixed unit_amount.")

    return {
        "id": price["id"],
        "currency": price["currency"],
        "unit_amount": int(unit_amount),
        "interval": interval,
        "interval_count": int(interval_count),
    }



def format_membership_date_display(value):
    locale = str(get_locale()) if get_locale() else None
    try:
        return format_date(value, format="long", locale=locale)
    except Exception:
        return value.isoformat()



def format_checkout_amount(amount_cents, currency):
    locale = str(get_locale()) if get_locale() else None
    amount = Decimal(amount_cents) / Decimal("100")
    try:
        return format_currency(amount, currency.upper(), locale=locale)
    except Exception:
        return f"{amount:.2f} {currency.upper()}"



def build_checkout_submit_message(cycle, price_details):
    coverage_end = format_membership_date_display(cycle["coverage_end"])
    renewal_due_on = format_membership_date_display(cycle["renewal_due_on"])
    annual_fee = format_checkout_amount(price_details["unit_amount"], price_details["currency"])

    if cycle["free_period"]:
        return _(
            "No payment is due today. Your membership is active through %(coverage_end)s. "
            "The annual fee of %(annual_fee)s will be charged on %(renewal_due_on)s unless you cancel beforehand.",
            coverage_end=coverage_end,
            annual_fee=annual_fee,
            renewal_due_on=renewal_due_on,
        )

    prorated_fee = format_checkout_amount(cycle["prorated_amount_cents"], price_details["currency"])
    return _(
        "Today you pay %(prorated_fee)s for membership through %(coverage_end)s. "
        "The annual fee of %(annual_fee)s will be charged on %(renewal_due_on)s unless you cancel beforehand.",
        prorated_fee=prorated_fee,
        coverage_end=coverage_end,
        annual_fee=annual_fee,
        renewal_due_on=renewal_due_on,
    )



def build_prorated_line_item(cycle, price_details):
    if cycle["prorated_amount_cents"] <= 0:
        return None

    return {
        "price_data": {
            "currency": price_details["currency"],
            "product_data": {
                "name": _(
                    "Membership through %(coverage_end)s (prorated)",
                    coverage_end=format_membership_date_display(cycle["coverage_end"]),
                ),
            },
            "unit_amount": cycle["prorated_amount_cents"],
        },
        "quantity": 1,
    }



def normalize_optional_member_value(field_name, value):
    if value == "" and field_name in {"title", "phone_work", "email_work"}:
        return None
    return value



def apply_member_profile(member, form_data, fields=MEMBER_PROFILE_FIELDS):
    for field_name in fields:
        value = normalize_optional_member_value(field_name, form_data.get(field_name))
        setattr(member, field_name, value)
    if "terms_accepted" in form_data:
        member.terms_accepted = bool(form_data.get("terms_accepted"))



def build_member_payload(member):
    payload = {field_name: getattr(member, field_name) for field_name in MEMBER_PROFILE_FIELDS}
    payload["terms_accepted"] = True
    return payload



def member_has_active_access(member, on_date=None):
    if member is None:
        return False
    today = on_date or get_membership_today()
    if not member.membership_ends_on or member.membership_ends_on < today:
        return False
    return member.payment_status in ACTIVE_MEMBER_STATUSES or member.is_active



def sync_member_active_state(member, on_date=None):
    if member is None:
        return False

    today = on_date or get_membership_today()
    changed = False

    if member.membership_ends_on and member.membership_ends_on < today and member.is_active:
        member.is_active = False
        changed = True
        if member.payment_status in ACTIVE_MEMBER_STATUSES:
            member.payment_status = "expired"
    elif member.membership_ends_on and member.membership_ends_on >= today and member.payment_status in ACTIVE_MEMBER_STATUSES and not member.is_active:
        member.is_active = True
        changed = True

    return changed



def set_member_membership_window(member, starts_on, ends_on, renewal_due_on, payment_status, is_active, cancel_at_period_end=False):
    member.membership_starts_on = starts_on
    member.membership_ends_on = ends_on
    member.renewal_due_on = renewal_due_on
    member.payment_status = payment_status
    member.is_active = is_active
    member.cancel_at_period_end = cancel_at_period_end



def get_member_by_stripe_reference(customer_id=None, subscription_id=None, member_id=None, user_id=None):
    if member_id:
        member = db.session.get(Member, int(member_id))
        if member is not None:
            return member
    if user_id:
        member = db.session.execute(db.select(Member).filter_by(user_id=int(user_id))).scalar_one_or_none()
        if member is not None:
            return member
    if subscription_id:
        member = Member.query.filter_by(stripe_subscription_id=subscription_id).first()
        if member is not None:
            return member
    if customer_id:
        return Member.query.filter_by(stripe_customer_id=customer_id).first()
    return None



def get_member_by_email(email):
    if not email:
        return None
    normalized_email = str(email).strip()
    if not normalized_email:
        return None
    return Member.query.filter_by(email_private=normalized_email).first()



def get_member_by_stripe_or_email(
    customer_id=None,
    subscription_id=None,
    member_id=None,
    user_id=None,
    email=None,
    fetch_customer_email=False,
):
    member = get_member_by_stripe_reference(
        customer_id=customer_id,
        subscription_id=subscription_id,
        member_id=member_id,
        user_id=user_id,
    )
    if member is not None:
        return member

    member = get_member_by_email(email)
    if member is not None:
        return member

    if customer_id and fetch_customer_email:
        try:
            apply_runtime_stripe_config()
            customer = stripe.Customer.retrieve(customer_id)
        except Exception as exc:
            current_app.logger.warning(
                "Could not retrieve Stripe customer %s while resolving a pending member: %s",
                customer_id,
                exc,
            )
            return None

        member = get_member_by_email(customer.get("email"))
        if member is not None:
            return member

    return None



def backfill_member_stripe_references(member, customer_id=None, subscription_id=None):
    changed = False

    if customer_id and isinstance(customer_id, str) and customer_id.startswith("cus_") and member.stripe_customer_id != customer_id:
        member.stripe_customer_id = customer_id
        changed = True

    if (
        subscription_id
        and isinstance(subscription_id, str)
        and subscription_id.startswith("sub_")
        and member.stripe_subscription_id != subscription_id
    ):
        member.stripe_subscription_id = subscription_id
        changed = True

    return changed



def update_member_paid_coverage(member, paid_on):
    coverage_year = paid_on.year
    if member.membership_ends_on and member.membership_ends_on >= paid_on:
        coverage_year = member.membership_ends_on.year

    starts_on = member.membership_starts_on
    if starts_on is None or starts_on.year != coverage_year:
        starts_on = first_day_of_year(coverage_year) if paid_on == first_day_of_year(coverage_year) else paid_on

    set_member_membership_window(
        member,
        starts_on=starts_on,
        ends_on=last_day_of_year(coverage_year),
        renewal_due_on=first_day_of_year(coverage_year + 1),
        payment_status="paid",
        is_active=True,
        cancel_at_period_end=member.cancel_at_period_end,
    )



def get_default_sender_account():
    settings = {s.key: s.value for s in Setting.query.all()}
    preferred_sender = settings.get("welcome_email_sender")
    if preferred_sender:
        return preferred_sender

    try:
        mail_accounts = load_mail_accounts_config()
        return next(iter(mail_accounts.keys()), None)
    except Exception:
        return None



def send_account_action_email(
    app,
    to_email,
    subject,
    preview_text,
    action_url,
    action_label,
    heading,
    body_lines,
    failure_event_type="account_action_email_failed",
    failure_summary=None,
    failure_payload=None,
    target_user=None,
    target_member=None,
    notify_on_failure=True,
):
    sender_account = get_default_sender_account()
    failure_summary = failure_summary or _("An account-related email could not be sent.")
    payload = {
        "recipient": to_email,
        "subject": subject,
        "sender_account": sender_account or None,
        **(failure_payload or {}),
    }
    if not sender_account:
        app.logger.warning("Could not send account email to %s because no sender account is configured.", to_email)
        if notify_on_failure:
            queue_curated_admin_notification(
                ADMIN_ERROR_CHANNEL,
                failure_event_type,
                failure_summary,
                payload=payload,
                target_user=target_user,
                target_member=target_member,
                commit=True,
            )
        return False

    logo_path = os.path.join(app.root_path, "static", "logo_joanneum_aeronautics_negativ.png")
    attachments = [{"path": logo_path, "cid": "logo"}] if os.path.exists(logo_path) else None
    success, error_message = send_mail(
        from_account=sender_account,
        to_email=to_email,
        subject=subject,
        template_name="member_account_action.html",
        attachments=attachments,
        preview_text=preview_text,
        action_url=action_url,
        action_label=action_label,
        heading=heading,
        body_lines=body_lines,
        now=get_now_utc(),
        return_error=True,
    )
    if not success and notify_on_failure:
        queue_curated_admin_notification(
            ADMIN_ERROR_CHANNEL,
            failure_event_type,
            failure_summary,
            payload={**payload, "error": error_message},
            target_user=target_user,
            target_member=target_member,
            commit=True,
        )
    return success



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



def send_email_verification_email(app, user):
    token = generate_token("verify-email", user_id=user.id)
    verify_url = build_public_url("auth.verify_email", token=token)
    return send_account_action_email(
        app,
        to_email=user.email,
        subject=_("Verify your Joanneum Aeronautics email"),
        preview_text=_("Confirm your email address for your Joanneum Aeronautics account."),
        action_url=verify_url,
        action_label=_("Verify Email"),
        heading=_("Confirm your email address"),
        body_lines=[
            _("Please confirm your email address for your Joanneum Aeronautics account."),
            _("This helps us keep your account secure and reach you when needed."),
        ],
        failure_event_type="verification_email_failed",
        failure_summary=_("A verification email could not be sent."),
        failure_payload={"email_type": "verification"},
        target_user=user,
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
            _("A password reset was requested for your Joanneum Aeronautics account."),
            _("If this was you, use the link below to set a new password. If not, you can ignore this email."),
        ],
        failure_event_type="password_reset_email_failed",
        failure_summary=_("A password reset email could not be sent."),
        failure_payload={"email_type": "password_reset"},
        target_user=user,
    )

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



def get_settings_map(keys=None):
    query = db.select(Setting)
    if keys:
        query = query.where(Setting.key.in_(list(keys)))
    return {setting.key: setting.value for setting in db.session.execute(query).scalars().all()}



def get_forum_settings_map():
    return get_settings_map(FORUM_SETTING_KEYS)



def get_stripe_settings_map():
    values = dict(DEFAULT_STRIPE_SETTINGS)
    values.update(get_settings_map(STRIPE_SETTING_KEYS))
    return values



def apply_runtime_stripe_config():
    stripe_settings = get_stripe_settings_map()
    stripe.api_key = stripe_settings.get("stripe_secret_key") or STRIPE_SECRET_KEY
    return stripe_settings



def get_forum_service():
    return ForumService(get_forum_settings_map())



def get_notification_settings_map():
    return get_settings_map(NOTIFICATION_SETTING_KEYS)



def get_notification_service():
    return NotificationService(current_app._get_current_object())



def flush_marked_notification_channels():
    channels = sorted(db.session.info.pop("notification_channels_to_flush", set()))
    if not channels:
        return {}
    try:
        return get_notification_service().deliver_pending_notifications(channels=channels)
    except Exception as exc:
        current_app.logger.error("Could not flush queued notification emails: %s", exc)
        return {}



def queue_curated_admin_notification(channel, event_type, summary, payload=None, target_user=None, target_member=None, object_type=None, object_id=None, severity="error", commit=False):
    if channel not in {ADMIN_GENERAL_CHANNEL, ADMIN_ERROR_CHANNEL}:
        return None
    try:
        service = get_notification_service()
        if channel == ADMIN_GENERAL_CHANNEL:
            event = service.queue_admin_general(
                event_type=event_type,
                summary=summary,
                payload=payload,
                target_user=target_user,
                target_member=target_member,
                object_type=object_type,
                object_id=object_id,
            )
        else:
            event = service.queue_admin_error(
                event_type=event_type,
                summary=summary,
                payload=payload,
                target_user=target_user,
                target_member=target_member,
                object_type=object_type,
                object_id=object_id,
                severity=severity,
            )
        if commit and event is not None:
            db.session.commit()
            flush_marked_notification_channels()
        return event
    except Exception as exc:
        current_app.logger.error("Could not queue admin notification '%s': %s", event_type, exc)
        if commit:
            db.session.rollback()
        return None



def queue_user_status_notification(event_type, summary, recipient_email, payload=None, target_user=None, target_member=None, object_type=None, object_id=None):
    try:
        return get_notification_service().queue_user_status(
            event_type=event_type,
            summary=summary,
            recipient_email=recipient_email,
            payload=payload,
            target_user=target_user,
            target_member=target_member,
            object_type=object_type,
            object_id=object_id,
        )
    except Exception as exc:
        current_app.logger.error("Could not queue user notification '%s': %s", event_type, exc)
        return None


def log_out_forum_session_if_possible(user):
    if user is None or getattr(user, "forum_account", None) is None:
        return False, None

    service = get_forum_service()
    did_log_out, error = service.log_out_user(user)
    if error:
        current_app.logger.warning("Forum logout sync failed for user_id=%s: %s", user.id, error)
    return did_log_out, error



def snapshot_forum_account_for_audit(forum_account):
    if forum_account is None:
        return None
    return serialize_audit_value(
        {
            "id": forum_account.id,
            "provider": forum_account.provider,
            "external_id": forum_account.external_id,
            "remote_user_id": forum_account.remote_user_id,
            "state": forum_account.state,
            "last_synced_email": forum_account.last_synced_email,
            "last_synced_username": forum_account.last_synced_username,
            "last_synced_at": forum_account.last_synced_at,
            "last_error": forum_account.last_error,
            "member_id": forum_account.member_id,
            "user_id": forum_account.user_id,
        }
    )



def snapshot_forum_avatar_submission_for_audit(submission):
    if submission is None:
        return None
    return serialize_audit_value(
        {
            "id": submission.id,
            "status": submission.status,
            "original_filename": submission.original_filename,
            "content_type": submission.content_type,
            "file_size": submission.file_size,
            "file_hash": submission.file_hash,
            "storage_path": submission.storage_path,
            "review_note": submission.review_note,
            "sync_error": submission.sync_error,
            "forum_synced_at": submission.forum_synced_at,
            "uploaded_at": submission.uploaded_at,
            "reviewed_at": submission.reviewed_at,
            "member_id": submission.member_id,
            "user_id": submission.user_id,
            "reviewed_by_user_id": submission.reviewed_by_user_id,
        }
    )



def build_forum_entry_url(user, include_token=False):
    route_values = {}
    if include_token and user is not None:
        route_values["token"] = generate_token(
            "forum-entry",
            user_id=user.id,
            issued_at=int(get_now_utc().timestamp()),
        )
    return build_public_url("forum.forum_entry", **route_values)



def queue_email_delivery_job(email_type, recipient_email=None, target_user=None, target_member=None, payload=None, initial_delay=None, error_message=None):
    normalized_recipient = (recipient_email or "").strip().lower() or None
    if initial_delay is None:
        initial_delay = timedelta()

    query = db.select(EmailDeliveryJob).where(
        EmailDeliveryJob.email_type == email_type,
        EmailDeliveryJob.status == EMAIL_JOB_STATUS_PENDING,
    )
    if target_member is not None and target_member.id is not None:
        query = query.where(EmailDeliveryJob.target_member_id == target_member.id)
    elif target_user is not None and target_user.id is not None:
        query = query.where(EmailDeliveryJob.target_user_id == target_user.id)
    elif normalized_recipient:
        query = query.where(EmailDeliveryJob.recipient_email == normalized_recipient)
    else:
        return None, False

    existing_job = db.session.execute(
        query.order_by(EmailDeliveryJob.created_at.asc(), EmailDeliveryJob.id.asc())
    ).scalars().first()
    if existing_job is not None:
        if normalized_recipient:
            existing_job.recipient_email = normalized_recipient
        if payload is not None:
            existing_job.payload = payload
        if error_message:
            existing_job.last_error = str(error_message)[:4000]
        return existing_job, False

    job = EmailDeliveryJob(
        email_type=email_type,
        recipient_email=normalized_recipient,
        target_user_id=target_user.id if target_user is not None else None,
        target_member_id=target_member.id if target_member is not None else None,
        payload=payload,
        status=EMAIL_JOB_STATUS_PENDING,
        retry_count=0,
        next_attempt_at=get_now_utc() + initial_delay,
        last_error=str(error_message)[:4000] if error_message else None,
    )
    db.session.add(job)
    return job, True



def queue_welcome_email_retry_job(member, error_message=None):
    if member is None:
        return None, False
    return queue_email_delivery_job(
        EMAIL_JOB_TYPE_WELCOME,
        recipient_email=member.email_private,
        target_user=member.user,
        target_member=member,
        initial_delay=WELCOME_EMAIL_RETRY_DELAYS[0],
        error_message=error_message,
    )



def mark_email_delivery_jobs_sent(email_type, target_user=None, target_member=None):
    query = db.select(EmailDeliveryJob).where(
        EmailDeliveryJob.email_type == email_type,
        EmailDeliveryJob.status == EMAIL_JOB_STATUS_PENDING,
    )
    if target_member is not None and target_member.id is not None:
        query = query.where(EmailDeliveryJob.target_member_id == target_member.id)
    elif target_user is not None and target_user.id is not None:
        query = query.where(EmailDeliveryJob.target_user_id == target_user.id)
    else:
        return 0

    jobs = db.session.execute(query).scalars().all()
    if not jobs:
        return 0

    now = get_now_utc()
    for job in jobs:
        job.status = EMAIL_JOB_STATUS_SENT
        job.sent_at = now
        job.next_attempt_at = None
        job.last_error = None
    return len(jobs)



def process_email_delivery_jobs(app):
    now = get_now_utc()
    summary = {
        "processed": 0,
        "sent": 0,
        "exhausted": 0,
        "canceled": 0,
        "failed": 0,
    }
    jobs = db.session.execute(
        db.select(EmailDeliveryJob)
        .where(
            EmailDeliveryJob.status == EMAIL_JOB_STATUS_PENDING,
            or_(EmailDeliveryJob.next_attempt_at.is_(None), EmailDeliveryJob.next_attempt_at <= now),
        )
        .order_by(EmailDeliveryJob.next_attempt_at.asc(), EmailDeliveryJob.id.asc())
    ).scalars().all()
    if not jobs:
        return summary

    automatic_emails_enabled = get_settings_map().get("automatic_emails_enabled") == "True"

    for job in jobs:
        summary["processed"] += 1
        job.last_attempted_at = now

        if job.email_type != EMAIL_JOB_TYPE_WELCOME:
            job.status = EMAIL_JOB_STATUS_CANCELED
            job.next_attempt_at = None
            job.last_error = _("This queued email type is no longer supported.")
            summary["canceled"] += 1
            continue

        member = db.session.get(Member, job.target_member_id) if job.target_member_id else None
        if member is None:
            job.status = EMAIL_JOB_STATUS_CANCELED
            job.next_attempt_at = None
            job.last_error = _("The linked member profile no longer exists.")
            summary["canceled"] += 1
            continue

        job.recipient_email = member.email_private

        if not automatic_emails_enabled:
            job.status = EMAIL_JOB_STATUS_CANCELED
            job.next_attempt_at = None
            job.last_error = _("Automatic emails were disabled before this retry could be sent.")
            summary["canceled"] += 1
            continue

        success, error_message = send_member_welcome_email(
            app,
            member,
            force_send=False,
            notify_on_failure=False,
            queue_retry_on_failure=False,
            return_error=True,
        )
        if success:
            mark_email_delivery_jobs_sent(EMAIL_JOB_TYPE_WELCOME, target_member=member)
            job.status = EMAIL_JOB_STATUS_SENT
            job.sent_at = now
            job.next_attempt_at = None
            job.last_error = None
            summary["sent"] += 1
            continue

        summary["failed"] += 1
        job.last_error = (error_message or _("The welcome email could not be sent."))[:4000]
        job.retry_count += 1
        if job.retry_count >= len(WELCOME_EMAIL_RETRY_DELAYS):
            job.status = EMAIL_JOB_STATUS_EXHAUSTED
            job.next_attempt_at = None
            summary["exhausted"] += 1
            queue_curated_admin_notification(
                ADMIN_ERROR_CHANNEL,
                "welcome_email_retry_exhausted",
                _("A welcome email could not be delivered after automatic retries."),
                payload={
                    "recipient": member.email_private,
                    "last_error": job.last_error,
                    "retry_count": job.retry_count,
                },
                target_user=member.user,
                target_member=member,
                commit=False,
            )
            continue

        job.next_attempt_at = now + WELCOME_EMAIL_RETRY_DELAYS[job.retry_count]

    return summary



def is_safe_next_url(target):
    if not target:
        return False

    ref_url = urlsplit(request.host_url)
    test_url = urlsplit(urljoin(request.host_url, target))
    return test_url.scheme in {"http", "https"} and ref_url.netloc == test_url.netloc



def sync_member_forum_state(member, raise_on_error=False):
    service = get_forum_service()
    if member is None or member.user is None:
        return None, service

    result = service.sync_member(member)
    if result and result.changed:
        db.session.flush()

    if result and result.error:
        current_app.logger.warning(
            "Forum sync reported an issue for member_id=%s user_id=%s desired_state=%s: %s",
            member.id,
            member.user_id,
            result.desired_state,
            result.error,
        )
        queue_curated_admin_notification(
            ADMIN_ERROR_CHANNEL,
            "forum_sync_failed",
            _("A forum synchronization attempt failed for %(email)s.", email=member.email_private),
            payload={
                "member_email": member.email_private,
                "forum_username": member.user.forum_username,
                "desired_state": result.desired_state,
                "error": result.error,
            },
            target_user=member.user,
            target_member=member,
            object_type="forum_account",
            object_id=result.forum_account.id if result and result.forum_account is not None else None,
        )
        if raise_on_error:
            raise ForumProviderError(result.error)

    return result, service



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



def sync_member_primary_email(member, new_email):
    new_email = (new_email or "").strip().lower()
    if not new_email:
        raise ValueError(_("The private email address is required."))

    existing_member = db.session.execute(
        db.select(Member).filter(Member.email_private == new_email, Member.id != member.id)
    ).scalar_one_or_none()
    if existing_member is not None:
        raise ValueError(_("A membership profile with this email address already exists."))

    if member.user is not None:
        existing_user = db.session.execute(
            db.select(User).filter(User.email == new_email, User.id != member.user.id)
        ).scalar_one_or_none()
        if existing_user is not None:
            raise ValueError(_("An account with this email address already exists."))

    email_changed = member.email_private != new_email
    member.email_private = new_email

    if member.user is not None and member.user.email != new_email:
        member.user.email = new_email
        member.user.email_verified_at = None

    if email_changed and member.stripe_customer_id:
        try:
            apply_runtime_stripe_config()
            stripe.Customer.modify(member.stripe_customer_id, email=new_email)
        except Exception as exc:
            current_app.logger.warning(
                "Could not sync Stripe customer email for member_id=%s customer_id=%s: %s",
                member.id,
                member.stripe_customer_id,
                exc,
            )

    return email_changed



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



def serialize_audit_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: serialize_audit_value(inner_value) for key, inner_value in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [serialize_audit_value(inner_value) for inner_value in value]
    return value



def is_sensitive_audit_field_name(field_name):
    normalized_name = str(field_name or "").strip().lower()
    if not normalized_name:
        return False
    if normalized_name in SENSITIVE_AUDIT_FIELD_NAMES:
        return True
    return any(token in normalized_name for token in ("secret", "password", "api_key", "webhook_secret"))



def redact_sensitive_audit_value(value, placeholder="<configured>"):
    serialized = serialize_audit_value(value)
    if isinstance(serialized, dict):
        redacted = {}
        for key, inner_value in serialized.items():
            if is_sensitive_audit_field_name(key):
                has_secret_value = inner_value is not None and inner_value != "" and inner_value != [] and inner_value != {}
                redacted[key] = placeholder if has_secret_value else None
            else:
                redacted[key] = redact_sensitive_audit_value(inner_value, placeholder=placeholder)
        return redacted
    if isinstance(serialized, list):
        return [redact_sensitive_audit_value(item, placeholder=placeholder) for item in serialized]
    return serialized



def redact_settings_states_for_audit(before_settings, after_settings):
    redacted_before = dict(before_settings or {})
    redacted_after = dict(after_settings or {})
    for key in SENSITIVE_SETTING_KEYS:
        before_value = redacted_before.get(key)
        after_value = redacted_after.get(key)
        before_present = before_value not in {None, ""}
        after_present = after_value not in {None, ""}
        redacted_before[key] = "<configured>" if before_present else None
        if not after_present:
            redacted_after[key] = "<cleared>" if before_present else None
        elif before_present and before_value != after_value:
            redacted_after[key] = "<changed>"
        else:
            redacted_after[key] = "<configured>"
    return redacted_before, redacted_after



def snapshot_user_for_audit(user):
    if user is None:
        return None
    return serialize_audit_value(
        {
            "id": user.id,
            "email": user.email,
            "forum_username": user.forum_username,
            "roles": sorted(role.slug for role in user.roles),
            "email_verified_at": user.email_verified_at,
        }
    )



def snapshot_member_for_audit(member, fields=None):
    if member is None:
        return None
    snapshot_fields = fields or MEMBER_PROFILE_FIELDS
    payload = {field_name: getattr(member, field_name) for field_name in snapshot_fields}
    payload.update(
        {
            "id": member.id,
            "payment_status": member.payment_status,
            "is_active": member.is_active,
            "membership_starts_on": member.membership_starts_on,
            "membership_ends_on": member.membership_ends_on,
            "renewal_due_on": member.renewal_due_on,
            "cancel_at_period_end": member.cancel_at_period_end,
            "stripe_customer_id": member.stripe_customer_id,
            "stripe_subscription_id": member.stripe_subscription_id,
        }
    )
    return serialize_audit_value(payload)



def snapshot_mail_account_for_audit(mail_account):
    if mail_account is None:
        return None
    return serialize_audit_value(
        {
            "id": mail_account.id,
            "account_key": mail_account.account_key,
            "host": mail_account.host,
            "port": mail_account.port,
            "username": mail_account.username,
            "starttls": mail_account.starttls,
        }
    )



def normalize_mail_account_key(raw_key):
    if raw_key is None:
        return ""
    normalized = "".join(
        character if (character.isalnum() or character in {"-", "_"}) else "_"
        for character in str(raw_key).strip()
    )
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized.strip("_")



def parse_imported_starttls(value, security_hint=None):
    if value is not None:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "starttls"}
        return bool(value)

    security_value = (security_hint or "").strip().lower()
    if security_value in {"starttls", "tls-starttls", "smtp-starttls", "explicit_tls"}:
        return True
    if security_value in {"ssl", "ssl/tls", "tls", "implicit_tls"}:
        return False
    return False



def normalize_imported_mail_account_record(raw_record, fallback_key=None):
    if not isinstance(raw_record, dict):
        raise ValueError("Each imported mail account entry must be a JSON object.")

    account_key = normalize_mail_account_key(
        raw_record.get("account_key")
        or raw_record.get("key")
        or raw_record.get("name")
        or fallback_key
    )
    host = (raw_record.get("host") or raw_record.get("smtp_host") or raw_record.get("server") or "").strip()
    username = (
        raw_record.get("username")
        or raw_record.get("user")
        or raw_record.get("email")
        or raw_record.get("login")
        or ""
    ).strip()
    password = (
        raw_record.get("password")
        or raw_record.get("pass")
        or raw_record.get("secret")
        or raw_record.get("smtp_password")
        or ""
    )
    port_value = raw_record.get("port") or raw_record.get("smtp_port")
    security_hint = raw_record.get("security") or raw_record.get("encryption") or raw_record.get("transport_security")
    starttls = parse_imported_starttls(raw_record.get("starttls"), security_hint=security_hint)

    if not account_key:
        raise ValueError("Every imported mail account needs a valid account key.")
    if not host:
        raise ValueError(f"Mail account '{account_key}' is missing the SMTP host.")
    if not username:
        raise ValueError(f"Mail account '{account_key}' is missing the SMTP username.")
    if not password:
        raise ValueError(f"Mail account '{account_key}' is missing the SMTP password.")
    if port_value in (None, ""):
        raise ValueError(f"Mail account '{account_key}' is missing the SMTP port.")

    try:
        port = int(port_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Mail account '{account_key}' has an invalid SMTP port.") from exc

    if port < 1 or port > 65535:
        raise ValueError(f"Mail account '{account_key}' has an invalid SMTP port.")

    return {
        "account_key": account_key,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "starttls": starttls,
    }



def normalize_imported_mail_accounts_payload(payload):
    raw_records = []

    if isinstance(payload, dict) and isinstance(payload.get("mail_accounts"), list):
        raw_records = [(None, entry) for entry in payload.get("mail_accounts", [])]
    elif isinstance(payload, list):
        raw_records = [(None, entry) for entry in payload]
    elif isinstance(payload, dict):
        raw_records = [
            (key, value)
            for key, value in payload.items()
            if isinstance(value, dict)
        ]
    else:
        raise ValueError("The uploaded JSON must be a Jaeronautics export, a legacy mail-account mapping, or a list of mail account objects.")

    if not raw_records:
        raise ValueError("The uploaded file does not contain any mail accounts.")

    normalized_records = []
    seen_keys = set()
    for fallback_key, raw_record in raw_records:
        normalized = normalize_imported_mail_account_record(raw_record, fallback_key=fallback_key)
        if normalized["account_key"] in seen_keys:
            raise ValueError(f"The uploaded file contains the account key '{normalized['account_key']}' more than once.")
        seen_keys.add(normalized["account_key"])
        normalized_records.append(normalized)

    return normalized_records



def build_mail_accounts_export_payload():
    return {
        "format": "jaeronautics_mail_accounts",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "mail_accounts": [
            {
                "account_key": mail_account.account_key,
                "host": mail_account.host,
                "port": mail_account.port,
                "username": mail_account.username,
                "password": mail_account.password,
                "starttls": mail_account.starttls,
            }
            for mail_account in get_db_mail_accounts()
        ],
    }



def log_audit_event(category, event_type, actor_user=None, target_user=None, target_member=None, before=None, after=None, metadata=None):
    db.session.add(
        AuditLog(
            actor_user=actor_user,
            target_user=target_user,
            target_member=target_member,
            category=category,
            event_type=event_type,
            before_state=redact_sensitive_audit_value(before) if before is not None else None,
            after_state=redact_sensitive_audit_value(after) if after is not None else None,
            event_metadata=redact_sensitive_audit_value(metadata) if metadata is not None else None,
        )
    )



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



def stripe_event_already_processed(event_id):
    """Return True when a Stripe webhook event id already has a marker row."""
    if not event_id:
        return False
    return (
        db.session.execute(
            db.select(ProcessedStripeEvent.id).filter_by(event_id=event_id)
        ).first()
        is not None
    )


def claim_stripe_event(event_id, event_type=None):
    """Atomically claim a Stripe event before processing it.

    Inserts the idempotency marker up front and commits it, so two concurrent
    deliveries of the same event cannot both proceed: the unique constraint on
    ``event_id`` lets exactly one committer win. Returns False if the event was
    already claimed (duplicate), in which case the caller must skip processing.
    Events without an id cannot be deduplicated, so they are allowed through.
    """
    if not event_id:
        return True
    db.session.add(ProcessedStripeEvent(event_id=event_id, event_type=event_type))
    try:
        db.session.commit()
        return True
    except IntegrityError:
        db.session.rollback()
        return False


def release_stripe_event(event_id):
    """Release a previously claimed event so Stripe's retry can reprocess it.

    Called when processing failed after the event was claimed, so the marker
    must not permanently suppress the (now unhandled) event.
    """
    if not event_id:
        return
    marker = db.session.execute(
        db.select(ProcessedStripeEvent).filter_by(event_id=event_id)
    ).scalar_one_or_none()
    if marker is not None:
        db.session.delete(marker)
        db.session.commit()


def build_membership_metadata(member, cycle, activation_mode):
    return {
        "membership_starts_on": cycle["coverage_start"].isoformat(),
        "membership_ends_on": cycle["coverage_end"].isoformat(),
        "renewal_due_on": cycle["renewal_due_on"].isoformat(),
        "activation_mode": activation_mode,
        "member_email": member.email_private,
        "member_id": str(member.id),
        "user_id": str(member.user_id) if member.user_id else "",
    }



def create_checkout_session_for_member(member):
    stripe_settings = apply_runtime_stripe_config()
    price_id = stripe_settings.get("stripe_price_id") or STRIPE_PRICE_ID
    price_details = get_stripe_membership_price()
    join_date = get_membership_today()
    cycle = build_membership_cycle(join_date, price_details["unit_amount"])
    activation_mode = "free_period" if cycle["free_period"] else "paid_now"
    membership_metadata = build_membership_metadata(member, cycle, activation_mode)
    line_items = [{"price": price_id, "quantity": 1}]
    prorated_line_item = build_prorated_line_item(cycle, price_details)
    if prorated_line_item is not None:
        line_items.insert(0, prorated_line_item)

    checkout_payload = build_member_payload(member)
    session = stripe.checkout.Session.create(
        payment_method_types=["card", "sepa_debit"],
        line_items=line_items,
        mode="subscription",
        metadata={**membership_metadata, "member_data": json.dumps(checkout_payload)},
        subscription_data={
            "trial_end": cycle["trial_end_unix"],
            "metadata": membership_metadata,
        },
        custom_text={
            "submit": {
                "message": build_checkout_submit_message(cycle, price_details),
            }
        },
        payment_method_collection="always",
        customer_email=member.email_private,
        success_url=build_public_url(
            "public.thank_you",
            method="checkout",
            phase=cycle["thank_you_phase"],
        ),
        cancel_url=build_public_url("public.cancel"),
    )
    member.pending_checkout_started_at = get_now_utc()
    return session, cycle



def create_invoice_membership_for_member(member):
    stripe_settings = apply_runtime_stripe_config()
    price_id = stripe_settings.get("stripe_price_id") or STRIPE_PRICE_ID
    price_details = get_stripe_membership_price()
    join_date = get_membership_today()
    cycle = build_membership_cycle(join_date, price_details["unit_amount"])
    activation_mode = "free_period" if cycle["free_period"] else "paid_now"
    membership_metadata = build_membership_metadata(member, cycle, activation_mode)

    customer = stripe.Customer.create(
        email=member.email_private,
        name=f"{member.first_name} {member.last_name}",
    )

    subscription_params = {
        "customer": customer.id,
        "items": [{"price": price_id}],
        "collection_method": "send_invoice",
        "days_until_due": 30,
        "trial_end": cycle["trial_end_unix"],
        "metadata": membership_metadata,
    }
    prorated_line_item = build_prorated_line_item(cycle, price_details)
    if prorated_line_item is not None:
        subscription_params["add_invoice_items"] = [prorated_line_item]

    subscription = stripe.Subscription.create(**subscription_params)
    member.pending_checkout_started_at = get_now_utc()
    member.stripe_customer_id = customer.id
    member.stripe_subscription_id = subscription.id

    if cycle["free_period"]:
        set_member_membership_window(
            member,
            starts_on=cycle["coverage_start"],
            ends_on=cycle["coverage_end"],
            renewal_due_on=cycle["renewal_due_on"],
            payment_status="free_period",
            is_active=True,
            cancel_at_period_end=False,
        )
    else:
        set_member_membership_window(
            member,
            starts_on=cycle["coverage_start"],
            ends_on=cycle["coverage_end"],
            renewal_due_on=cycle["renewal_due_on"],
            payment_status="unpaid",
            is_active=False,
            cancel_at_period_end=False,
        )

    return subscription, cycle



def get_latest_stripe_subscription_for_member(member):
    if member is None:
        return None

    if member.stripe_subscription_id:
        apply_runtime_stripe_config()
        return stripe.Subscription.retrieve(member.stripe_subscription_id)

    if not member.stripe_customer_id:
        return None

    apply_runtime_stripe_config()
    subscription_list = stripe.Subscription.list(customer=member.stripe_customer_id, status="all", limit=1)
    subscriptions = subscription_list.get("data", []) if hasattr(subscription_list, "get") else []
    return subscriptions[0] if subscriptions else None



def backfill_member_coverage_from_subscription(member, subscription):
    if member is None or not subscription:
        return False

    metadata = subscription.get("metadata", {}) or {}
    starts_on = parse_iso_date(metadata.get("membership_starts_on"))
    ends_on = parse_iso_date(metadata.get("membership_ends_on"))
    renewal_due_on = parse_iso_date(metadata.get("renewal_due_on"))
    changed = False

    # Subscription metadata is written once at signup and never refreshed on
    # renewal, so it is stale for any member past their first year. Treat it as a
    # backfill for MISSING dates only, and never move coverage backwards:
    # membership_ends_on / renewal_due_on may be filled in or extended, but never
    # regressed to an older signup-year value (which would expire a paid member).
    if starts_on and member.membership_starts_on is None:
        member.membership_starts_on = starts_on
        changed = True
    if ends_on and (member.membership_ends_on is None or ends_on > member.membership_ends_on):
        member.membership_ends_on = ends_on
        changed = True
    if renewal_due_on and (member.renewal_due_on is None or renewal_due_on > member.renewal_due_on):
        member.renewal_due_on = renewal_due_on
        changed = True

    return changed



def sync_member_subscription_state_from_subscription(member, subscription):
    if member is None or not subscription:
        return False

    changed = backfill_member_stripe_references(
        member,
        customer_id=subscription.get("customer") or member.stripe_customer_id,
        subscription_id=subscription.get("id"),
    )

    if backfill_member_coverage_from_subscription(member, subscription):
        changed = True

    cancel_at_period_end = subscription_has_scheduled_cancellation(subscription)
    if member.cancel_at_period_end != cancel_at_period_end:
        member.cancel_at_period_end = cancel_at_period_end
        changed = True

    subscription_status = subscription.get("status")
    activation_mode = ((subscription.get("metadata", {}) or {}).get("activation_mode") or "").strip()
    coverage_is_current = bool(member.membership_ends_on and member.membership_ends_on >= get_membership_today())

    if subscription_status == "canceled":
        desired_status = "canceled"
        desired_active = coverage_is_current
    elif cancel_at_period_end:
        desired_status = "cancel_scheduled" if coverage_is_current else member.payment_status
        desired_active = coverage_is_current
    elif subscription_status in {"active", "trialing"} and coverage_is_current:
        desired_status = "free_period" if activation_mode == "free_period" else "paid"
        desired_active = True
    else:
        desired_status = None
        desired_active = member.is_active

    if desired_status and member.payment_status != desired_status:
        member.payment_status = desired_status
        changed = True

    if member.is_active != desired_active:
        member.is_active = desired_active
        changed = True

    if sync_member_active_state(member):
        changed = True

    return changed



def sync_member_subscription_state_from_stripe(member):
    if member is None or not (member.stripe_customer_id or member.stripe_subscription_id):
        return False

    subscription = get_latest_stripe_subscription_for_member(member)
    if not subscription:
        return False

    return sync_member_subscription_state_from_subscription(member, subscription)



def refresh_member_billing_state(member, force_stripe_sync=False, sync_forum=False, on_date=None):
    if member is None:
        return False, None, None

    changed = False
    stripe_subscription = None
    has_stripe_reference = bool(member.stripe_customer_id or member.stripe_subscription_id)

    if has_stripe_reference and force_stripe_sync:
        stripe_subscription = get_latest_stripe_subscription_for_member(member)
        if stripe_subscription and sync_member_subscription_state_from_subscription(member, stripe_subscription):
            changed = True

    if sync_member_active_state(member, on_date=on_date):
        changed = True

    forum_result = None
    forum_service = get_forum_service()
    if sync_forum and member.user is not None and (forum_service.is_enabled() or member.user.forum_account is not None):
        forum_result, _forum_service = sync_member_forum_state(member)
        if forum_result and forum_result.changed:
            changed = True

    return changed, stripe_subscription, forum_result



def get_portal_session(member):
    if not member or not member.stripe_customer_id:
        raise ValueError(_("No Stripe billing profile is available for this membership yet."))

    refresh_token = int(datetime.now(timezone.utc).timestamp())
    apply_runtime_stripe_config()
    return stripe.billing_portal.Session.create(
        customer=member.stripe_customer_id,
        return_url=build_public_url("account.account", refresh_billing=1, rt=refresh_token),
    )



def send_member_welcome_email(app, member, force_send=False, notify_on_failure=True, queue_retry_on_failure=None, return_error=False):
    settings = get_settings_map()
    if queue_retry_on_failure is None:
        queue_retry_on_failure = not force_send

    if not force_send and settings.get("automatic_emails_enabled") != "True":
        return (False, _("Automatic welcome emails are disabled.")) if return_error else False

    sender_account = settings.get("welcome_email_sender")
    template_name = settings.get("automatic_email_template")
    if not sender_account or not template_name:
        error_message = _("Email sender or template is not configured in the admin settings.")
        if force_send:
            raise ValueError(error_message)
        if queue_retry_on_failure:
            queue_welcome_email_retry_job(member, error_message=error_message)
        if notify_on_failure:
            queue_curated_admin_notification(
                ADMIN_ERROR_CHANNEL,
                "welcome_email_failed",
                _("A welcome email could not be sent because the sender or template is not configured."),
                payload={
                    "recipient": member.email_private,
                    "sender_account": sender_account or None,
                    "template_name": template_name or None,
                    "error": error_message,
                },
                target_user=member.user,
                target_member=member,
                commit=True,
            )
        return (False, error_message) if return_error else False

    suggested_username = member.user.forum_username if member.user and member.user.forum_username else generate_suggested_username(member)
    logo_path = os.path.join(app.root_path, "static", "logo_joanneum_aeronautics_negativ.png")
    attachments = [{"path": logo_path, "cid": "logo"}] if os.path.exists(logo_path) else None

    forum_service = get_forum_service()
    forum_entry_url = None
    if forum_service.is_enabled() and member.user is not None:
        forum_entry_url = build_forum_entry_url(member.user, include_token=True)

    success, error_message = send_mail(
        from_account=sender_account,
        to_email=member.email_private,
        subject=_("Welcome to Joanneum Aeronautics!"),
        template_name=template_name,
        attachments=attachments,
        first_name=member.first_name,
        suggested_username=suggested_username,
        membership_starts_on=member.membership_starts_on,
        membership_ends_on=member.membership_ends_on,
        renewal_due_on=member.renewal_due_on,
        forum_integration_enabled=forum_service.is_enabled(),
        forum_entry_url=forum_entry_url,
        now=get_now_utc(),
        return_error=True,
    )
    if success:
        mark_email_delivery_jobs_sent(EMAIL_JOB_TYPE_WELCOME, target_member=member)
        return (True, None) if return_error else True

    if queue_retry_on_failure:
        queue_welcome_email_retry_job(member, error_message=error_message)
    if notify_on_failure:
        queue_curated_admin_notification(
            ADMIN_ERROR_CHANNEL,
            "welcome_email_failed",
            _("A welcome email could not be sent."),
            payload={
                "recipient": member.email_private,
                "sender_account": sender_account,
                "template_name": template_name,
                "error": error_message,
            },
            target_user=member.user,
            target_member=member,
            commit=True,
        )
    return (False, error_message) if return_error else False



def ensure_user_schema():
    inspector = inspect(db.engine)
    if "users" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("users")}
    alter_statements = []
    if "forum_username" not in columns:
        alter_statements.append("ALTER TABLE users ADD COLUMN forum_username VARCHAR(255) NULL")
    if "email_verified_at" not in columns:
        alter_statements.append("ALTER TABLE users ADD COLUMN email_verified_at DATETIME NULL")
    if "password_reset_nonce" not in columns:
        alter_statements.append("ALTER TABLE users ADD COLUMN password_reset_nonce VARCHAR(255) NULL")

    with db.engine.begin() as connection:
        for statement in alter_statements:
            connection.execute(sql_text(statement))

        inspector = inspect(connection)
        unique_constraints = inspector.get_unique_constraints("users")
        indexes = inspector.get_indexes("users")
        has_forum_username_unique = any(
            constraint.get("column_names") == ["forum_username"]
            for constraint in unique_constraints
        ) or any(
            index.get("unique") and index.get("column_names") == ["forum_username"]
            for index in indexes
        )
        if not has_forum_username_unique:
            connection.execute(
                sql_text("CREATE UNIQUE INDEX uq_users_forum_username ON users (forum_username)")
            )



def ensure_member_schema():
    inspector = inspect(db.engine)
    if "member" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("member")}
    alter_statements = []

    if "user_id" not in columns:
        alter_statements.append("ALTER TABLE member ADD COLUMN user_id INTEGER NULL")
    if "pending_checkout_started_at" not in columns:
        alter_statements.append("ALTER TABLE member ADD COLUMN pending_checkout_started_at DATETIME NULL")
    if "stripe_subscription_id" not in columns:
        alter_statements.append("ALTER TABLE member ADD COLUMN stripe_subscription_id VARCHAR(255) NULL")
    if "membership_starts_on" not in columns:
        alter_statements.append("ALTER TABLE member ADD COLUMN membership_starts_on DATE NULL")
    if "membership_ends_on" not in columns:
        alter_statements.append("ALTER TABLE member ADD COLUMN membership_ends_on DATE NULL")
    if "renewal_due_on" not in columns:
        alter_statements.append("ALTER TABLE member ADD COLUMN renewal_due_on DATE NULL")
    if "cancel_at_period_end" not in columns:
        alter_statements.append("ALTER TABLE member ADD COLUMN cancel_at_period_end BOOLEAN NOT NULL DEFAULT 0")

    with db.engine.begin() as connection:
        for statement in alter_statements:
            connection.execute(sql_text(statement))

        inspector = inspect(connection)
        unique_constraints = inspector.get_unique_constraints("member")
        indexes = inspector.get_indexes("member")
        has_subscription_unique = any(
            constraint.get("column_names") == ["stripe_subscription_id"]
            for constraint in unique_constraints
        ) or any(
            index.get("unique") and index.get("column_names") == ["stripe_subscription_id"]
            for index in indexes
        )
        if not has_subscription_unique:
            connection.execute(
                sql_text("CREATE UNIQUE INDEX uq_member_stripe_subscription_id ON member (stripe_subscription_id)")
            )

        has_user_unique = any(
            constraint.get("column_names") == ["user_id"]
            for constraint in unique_constraints
        ) or any(
            index.get("unique") and index.get("column_names") == ["user_id"]
            for index in indexes
        )
        if not has_user_unique:
            connection.execute(sql_text("CREATE UNIQUE INDEX uq_member_user_id ON member (user_id)"))



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


def get_recent_audit_logs(limit=10, category=None):
    query = db.select(AuditLog).options(
        selectinload(AuditLog.actor_user),
        selectinload(AuditLog.target_user),
        selectinload(AuditLog.target_member),
    )
    if category:
        query = query.where(AuditLog.category == category)
    return db.session.execute(query.order_by(AuditLog.created_at.desc()).limit(limit)).scalars().all()


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

        Handles three situations so it stays safe to run on every deploy:

        * Fresh database  -> run migrations to build the full schema.
        * Legacy database created before migrations existed -> converge its
          columns with the historical ``ensure_*`` helpers, create any newly
          added tables, then stamp it at the baseline revision so future
          migrations apply cleanly.
        * Already migrated -> apply any pending migrations.
        """
        from flask_migrate import stamp, upgrade

        click.echo("Preparing database schema...")
        try:
            inspector = inspect(db.engine)
            existing_tables = set(inspector.get_table_names())

            if "alembic_version" in existing_tables:
                upgrade()
            elif "users" in existing_tables:
                # Pre-migration install: reconcile legacy columns, add any new
                # tables, and record that the schema now matches the baseline.
                ensure_user_schema()
                ensure_member_schema()
                db.create_all()
                stamp()
            else:
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
            click.echo(f"  stripe_current_period_end: {stripe_subscription.get('current_period_end') or '-'}")
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
        flash(_("Too many requests from your IP address. Please wait a moment and try again."), "warning")
        return redirect(request.referrer or url_for("public.index")), 429

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


application = create_app()

if __name__ == "__main__":
    application.run(debug=False)





























