import json
import os
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
        ForumAccount,
        ForumAvatarSubmission,
        MailAccount,
        Member,
        MemberProfileChangeRequest,
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
        normalize_forum_settings,
    )
    from .mail_utils import load_mail_accounts_config, probe_mail_account_connection, send_mail
except ImportError:
    from db_models import (
        AuditLog,
        ForumAccount,
        ForumAvatarSubmission,
        MailAccount,
        Member,
        MemberProfileChangeRequest,
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
        normalize_forum_settings,
    )
    from mail_utils import load_mail_accounts_config, probe_mail_account_connection, send_mail

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

babel = Babel()
login_manager = LoginManager()
csrf = CSRFProtect()


def get_rate_limit_identity():
    cf_connecting_ip = request.headers.get("CF-Connecting-IP", "").split(",", 1)[0].strip()
    if cf_connecting_ip:
        return cf_connecting_ip

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
            return redirect(url_for("index"))
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
PENDING_SIGNUP_RETENTION_DAYS = int(os.getenv("PENDING_SIGNUP_RETENTION_DAYS", "14"))
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
            stripe.api_key = STRIPE_SECRET_KEY
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



def send_account_action_email(app, to_email, subject, preview_text, action_url, action_label, heading, body_lines):
    sender_account = get_default_sender_account()
    if not sender_account:
        app.logger.warning("Could not send account email to %s because no sender account is configured.", to_email)
        return False

    logo_path = os.path.join(app.root_path, "static", "Logo_Aeronautics_signature-logo.png")
    attachments = [{"path": logo_path, "cid": "logo"}] if os.path.exists(logo_path) else None
    return send_mail(
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
    )



def get_token_serializer():
    return URLSafeTimedSerializer(SECRET_KEY)



def generate_token(purpose, **payload):
    return get_token_serializer().dumps(payload, salt=f"jaeronautics-{purpose}")



def read_token(token, purpose, max_age):
    return get_token_serializer().loads(token, salt=f"jaeronautics-{purpose}", max_age=max_age)



def send_email_verification_email(app, user):
    token = generate_token("verify-email", user_id=user.id)
    verify_url = url_for("verify_email", token=token, _external=True)
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
    )



def send_password_reset_email(app, user):
    token = generate_token("reset-password", user_id=user.id)
    reset_url = url_for("reset_password", token=token, _external=True)
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
        route_values["token"] = generate_token("forum-entry", user_id=user.id)
    return url_for("forum_entry", _external=True, **route_values)



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
        "entry_url": url_for("forum_entry"),
        "forum_error": forum_account.last_error if forum_account is not None else None,
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
            before_state=serialize_audit_value(before) if before is not None else None,
            after_state=serialize_audit_value(after) if after is not None else None,
            event_metadata=serialize_audit_value(metadata) if metadata is not None else None,
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
        return "admin_dashboard"
    return "account"



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
        success_url=url_for(
            "thank_you",
            _external=True,
            method="checkout",
            phase=cycle["thank_you_phase"],
        ),
        cancel_url=url_for("cancel", _external=True),
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

    if starts_on and member.membership_starts_on != starts_on:
        member.membership_starts_on = starts_on
        changed = True
    if ends_on and member.membership_ends_on != ends_on:
        member.membership_ends_on = ends_on
        changed = True
    if renewal_due_on and member.renewal_due_on != renewal_due_on:
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
        return_url=url_for("account", _external=True, refresh_billing=1, rt=refresh_token),
    )



def send_member_welcome_email(app, member, force_send=False):
    settings = get_settings_map()
    if not force_send and settings.get("automatic_emails_enabled") != "True":
        return False

    sender_account = settings.get("welcome_email_sender")
    template_name = settings.get("automatic_email_template")
    if not sender_account or not template_name:
        if force_send:
            raise ValueError("Email sender or template is not configured in the admin settings.")
        return False

    suggested_username = member.user.forum_username if member.user and member.user.forum_username else generate_suggested_username(member)
    logo_path = os.path.join(app.root_path, "static", "Logo_Aeronautics_signature-logo.png")
    attachments = [{"path": logo_path, "cid": "logo"}] if os.path.exists(logo_path) else None

    forum_service = get_forum_service()
    forum_entry_url = None
    if forum_service.is_enabled() and member.user is not None:
        forum_entry_url = build_forum_entry_url(member.user, include_token=True)

    return send_mail(
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
    )



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


def create_app():
    app = Flask(__name__)

    @app.context_processor
    def inject_language_switcher():
        def switch_lang_url(lang):
            endpoint = request.endpoint or "index"
            values = dict(request.view_args or {})
            values.update(request.args.to_dict(flat=True))
            values["lang"] = lang
            try:
                return url_for(endpoint, **values)
            except BuildError:
                fallback_values = request.args.to_dict(flat=True)
                fallback_values["lang"] = lang
                return url_for("index", **fallback_values)

        return dict(switch_lang_url=switch_lang_url)

    @app.context_processor
    def inject_settings():
        settings = {s.key: s.value for s in Setting.query.all()}
        return dict(settings=settings)

    app.config["SECRET_KEY"] = SECRET_KEY
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["STRIPE_PUBLISHABLE_KEY"] = STRIPE_PUBLISHABLE_KEY
    app.config["STRIPE_SECRET_KEY"] = STRIPE_SECRET_KEY
    app.config["STRIPE_PRICE_ID"] = STRIPE_PRICE_ID

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

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "login"
    babel.init_app(app, locale_selector=select_locale)
    csrf.init_app(app)
    limiter.init_app(app)

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

    @app.url_defaults
    def add_static_file_version(endpoint, values):
        if endpoint != "static":
            return
        if "v" in values:
            return

        version = static_asset_version(app, values.get("filename"))
        if version:
            values["v"] = version

    @app.after_request
    def disable_dynamic_page_caching(response):
        if request.endpoint == "static":
            return response

        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["Surrogate-Control"] = "no-store"
        response.vary.add("Cookie")
        return response

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1, x_proto=1)
    stripe.api_key = STRIPE_SECRET_KEY

    @app.cli.command("db-init")
    @with_appcontext
    def db_init():
        """Creates database tables if they do not exist and upgrades newer account and membership columns when needed."""
        click.echo("Creating database tables...")
        try:
            db.create_all()
            seed_default_roles()
            ensure_user_schema()
            ensure_member_schema()
            backfill_legacy_admin_roles()
            backfill_member_user_links()
            db.session.commit()
            click.echo("Database tables created successfully.")
        except Exception as e:
            click.echo(f"Error creating tables: {e}", err=True)
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
                    changed_count += 1
                else:
                    db.session.rollback()
                    unchanged_count += 1
                if forum_result and forum_result.error:
                    forum_warning_count += 1
                    click.echo(click.style(f"Forum sync warning for {member.email_private}: {forum_result.error}", fg="yellow"))
            except stripe.StripeError as exc:
                db.session.rollback()
                error_count += 1
                click.echo(click.style(f"Stripe sync failed for {member.email_private}: {exc}", fg="red"), err=True)
            except Exception as exc:
                db.session.rollback()
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
        click.echo(click.style(f"Synchronized {len(members)} member(s). Changed: {changed_count}. Errors: {error_count}.", fg="green" if error_count == 0 else "yellow"))

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
            return redirect(url_for("create_membership_profile"))

        has_stripe_reference = bool(member.stripe_customer_id or member.stripe_subscription_id)
        if has_stripe_reference:
            try:
                billing_changed, _stripe_subscription, _forum_result = refresh_member_billing_state(member, force_stripe_sync=True, sync_forum=False)
                if billing_changed:
                    db.session.commit()
            except stripe.StripeError as exc:
                app.logger.warning("Could not refresh Stripe billing state for member_id=%s: %s", member.id, exc)
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

    @app.route("/", methods=["GET"])
    def index():
        form = MembershipForm()
        return render_template(
            "index.html",
            form=form,
            stripe_key=get_stripe_settings_map().get("stripe_publishable_key") or STRIPE_PUBLISHABLE_KEY,
        )

    @app.route("/process-membership", methods=["POST"])
    @limiter.limit(RATELIMIT_MEMBERSHIP)
    def process_membership():
        form = MembershipForm()
        settings = {s.key: s.value for s in Setting.query.all()}

        if form.validate_on_submit():
            form_data = form.data
            form_data.pop("csrf_token", None)
            form_data.pop("submit", None)
            password = form_data.pop("password")
            form_data.pop("confirm_password", None)

            payment_method = form_data.pop("payment_method", "checkout")
            form_data["email_private"] = form_data["email_private"].strip().lower()
            email_address = form_data["email_private"]

            existing_member = db.session.execute(db.select(Member).filter_by(email_private=email_address)).scalar_one_or_none()
            existing_user = db.session.execute(db.select(User).filter_by(email=email_address)).scalar_one_or_none()

            if existing_member and sync_member_active_state(existing_member):
                db.session.commit()

            if existing_member is not None:
                if existing_member.user_id:
                    flash(_("An account with this email address already exists. Please log in to manage or resume your membership."), "warning")
                    return redirect(url_for("login"))
                flash(_("A membership profile with this email address already exists without a linked login. Please contact the club so we can resolve it."), "warning")
                return redirect(url_for("index"))

            if existing_user is not None:
                flash(_("An account with this email address already exists. Please log in instead."), "warning")
                return redirect(url_for("login"))

            if settings.get("invoice_payments_enabled") != "True":
                payment_method = "checkout"

            member = Member(
                created_at=get_now_utc(),
                payment_status="pending_checkout",
                is_active=False,
                pending_checkout_started_at=get_now_utc(),
            )
            apply_member_profile(member, {**form_data, "terms_accepted": True})

            user = User(
                email=email_address,
                forum_username=generate_unique_forum_username(
                    member.first_name,
                    member.last_name,
                    member.year_group,
                ),
            )
            user.set_password(password)
            member.user = user

            db.session.add(user)
            db.session.add(member)
            db.session.flush()
            log_audit_event(
                category="membership",
                event_type="public_membership_signup_started",
                actor_user=user,
                target_user=user,
                target_member=member,
                before=None,
                after={"user": snapshot_user_for_audit(user), "member": snapshot_member_for_audit(member)},
                metadata={"payment_method": payment_method},
            )
            db.session.commit()
            login_user(user)

            try:
                try:
                    send_email_verification_email(app, user)
                except Exception as email_exc:
                    app.logger.warning("Could not send verification email for user_id=%s: %s", user.id, email_exc)

                if payment_method == "checkout":
                    session, _cycle = create_checkout_session_for_member(member)
                    db.session.commit()
                    return redirect(session.url, code=303)

                if payment_method == "invoice":
                    _subscription, cycle = create_invoice_membership_for_member(member)
                    forum_result = None
                    if cycle["free_period"]:
                        forum_result, _forum_service = sync_member_forum_state(member)
                    db.session.commit()
                    if cycle["free_period"]:
                        send_member_welcome_email(app, member)
                        if forum_result and forum_result.error:
                            app.logger.warning("Forum sync reported an issue after invoice activation for member_id=%s: %s", member.id, forum_result.error)
                    return redirect(
                        url_for(
                            "thank_you",
                            method="invoice",
                            phase=cycle["thank_you_phase"],
                        )
                    )

            except stripe.StripeError as e:
                error_body = getattr(e, "json_body", {}) or {}
                error_details = error_body.get("error", {}) if isinstance(error_body, dict) else {}
                app.logger.error(
                    "Stripe Error during membership signup: type=%s message=%s user_message=%s code=%s param=%s request_id=%s http_status=%s payment_method=%s email=%s member_id=%s",
                    type(e).__name__,
                    str(e),
                    error_details.get("message"),
                    error_details.get("code"),
                    error_details.get("param"),
                    getattr(e, "request_id", None),
                    getattr(e, "http_status", None),
                    payment_method,
                    email_address,
                    member.id,
                )
                flash(_("Your account was created, but payment could not be started. Please log in and resume your membership from your account page."), "warning")
            except Exception:
                app.logger.exception(
                    "Unexpected error during membership signup for email=%s payment_method=%s member_id=%s",
                    email_address,
                    payment_method,
                    member.id,
                )
                flash(_("Your account was created, but an unexpected error occurred while starting billing. Please log in and resume your membership from your account page."), "warning")

            return redirect(url_for("account"))

        app.logger.warning(f"Form validation failed. Errors: {form.errors}")
        flash(_("Please correct the errors below and try again."), "danger")
        return render_template("index.html", form=form)

    @app.route("/thank-you")
    def thank_you():
        method = request.args.get("method", "checkout")
        phase = request.args.get("phase", "prorated")
        return render_template("thank_you.html", method=method, phase=phase)

    @app.route("/cancel")
    def cancel():
        return render_template("cancel.html")

    @app.route("/legal")
    def legal_texts():
        return render_template("legal_texts.html")

    @app.route("/__health", methods=["GET"])
    def health_check():
        return jsonify(
            {
                "status": "ok",
                "app": "jaeronautics",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "host": request.host,
            }
        )

    @app.route("/account", methods=["GET"])
    @login_required
    def account():
        if not request.args.get("rt"):
            redirect_args = {"rt": str(int(datetime.now(timezone.utc).timestamp() * 1000))}
            if request.args.get("refresh_billing") == "1":
                redirect_args["refresh_billing"] = "1"
            return redirect(url_for("account", **redirect_args))
        return render_account_dashboard()

    @app.route("/account/create-membership", methods=["GET", "POST"])
    @login_required
    def create_membership_profile():
        if current_user.member is not None:
            return redirect(url_for("account"))

        form = CreateMembershipProfileForm()
        if request.method == "GET":
            form.email_private.data = current_user.email

        if form.validate_on_submit():
            settings = {s.key: s.value for s in Setting.query.all()}
            form_data = form.data
            form_data.pop("csrf_token", None)
            form_data.pop("submit", None)

            payment_method = form_data.pop("payment_method", "checkout")
            if settings.get("invoice_payments_enabled") != "True":
                payment_method = "checkout"

            member_email = (current_user.email or "").strip().lower()
            existing_member = db.session.execute(db.select(Member).filter_by(email_private=member_email)).scalar_one_or_none()
            if existing_member is not None:
                if existing_member.user_id == current_user.id:
                    return redirect(url_for("account"))
                flash(_("A membership profile with this email address already exists. Please contact the club so we can resolve it."), "warning")
                return redirect(url_for("admin_dashboard" if current_user.has_role("admin") else "index"))

            member = Member(
                created_at=get_now_utc(),
                payment_status="pending_checkout",
                is_active=False,
                pending_checkout_started_at=get_now_utc(),
            )
            apply_member_profile(member, {**form_data, "email_private": member_email, "terms_accepted": True})
            member.user = current_user
            current_user.email = member_email
            if not current_user.forum_username:
                current_user.forum_username = generate_unique_forum_username(
                    member.first_name,
                    member.last_name,
                    member.year_group,
                    exclude_user_id=current_user.id,
                )

            before_user = snapshot_user_for_audit(current_user)
            db.session.add(member)
            db.session.flush()
            log_audit_event(
                category="membership",
                event_type="linked_membership_created",
                actor_user=current_user,
                target_user=current_user,
                target_member=member,
                before={"user": before_user, "member": None},
                after={"user": snapshot_user_for_audit(current_user), "member": snapshot_member_for_audit(member)},
                metadata={"payment_method": payment_method},
            )
            db.session.commit()

            try:
                if not current_user.email_is_verified:
                    send_email_verification_email(app, current_user)
            except Exception as email_exc:
                app.logger.warning("Could not send verification email for linked membership user_id=%s: %s", current_user.id, email_exc)

            try:
                if payment_method == "checkout":
                    session, _cycle = create_checkout_session_for_member(member)
                    db.session.commit()
                    return redirect(session.url, code=303)

                if payment_method == "invoice":
                    _subscription, cycle = create_invoice_membership_for_member(member)
                    forum_result = None
                    if cycle["free_period"]:
                        forum_result, _forum_service = sync_member_forum_state(member)
                    db.session.commit()
                    if cycle["free_period"]:
                        send_member_welcome_email(app, member)
                        if forum_result and forum_result.error:
                            app.logger.warning("Forum sync reported an issue after invoice activation for member_id=%s: %s", member.id, forum_result.error)
                    return redirect(
                        url_for(
                            "thank_you",
                            method="invoice",
                            phase=cycle["thank_you_phase"],
                        )
                    )
            except stripe.StripeError as exc:
                error_body = getattr(exc, "json_body", {}) or {}
                error_details = error_body.get("error", {}) if isinstance(error_body, dict) else {}
                app.logger.error(
                    "Stripe Error during linked membership signup: type=%s message=%s user_message=%s code=%s param=%s request_id=%s http_status=%s payment_method=%s email=%s member_id=%s user_id=%s",
                    type(exc).__name__,
                    str(exc),
                    error_details.get("message"),
                    error_details.get("code"),
                    error_details.get("param"),
                    getattr(exc, "request_id", None),
                    getattr(exc, "http_status", None),
                    payment_method,
                    member_email,
                    member.id,
                    current_user.id,
                )
                flash(_("Your membership profile was created, but payment could not be started. You can resume it from your account page."), "warning")
            except Exception:
                app.logger.exception(
                    "Unexpected error during linked membership signup for user_id=%s email=%s payment_method=%s member_id=%s",
                    current_user.id,
                    member_email,
                    payment_method,
                    member.id,
                )
                flash(_("Your membership profile was created, but billing could not be started right now. You can resume it from your account page."), "warning")

            return redirect(url_for("account"))

        return render_template("account/create_membership.html", form=form)

    @app.route("/account/profile", methods=["POST"])
    @login_required
    def save_member_profile():
        member = get_current_member_for_user(current_user)
        if member is None:
            flash(_("No membership profile is linked to this account yet."), "warning")
            return redirect(url_for("index"))

        profile_form = MemberProfileForm(prefix="profile")
        identity_form = IdentityChangeRequestForm(prefix="identity")
        if profile_form.validate_on_submit():
            before_user = snapshot_user_for_audit(current_user)
            before_member = snapshot_member_for_audit(member, fields=DIRECT_MEMBER_PROFILE_FIELDS)
            try:
                email_changed = sync_member_primary_email(member, profile_form.email_private.data)
            except ValueError as exc:
                flash(str(exc), "danger")
                return render_account_dashboard(profile_form=profile_form, identity_form=identity_form)

            for field_name in DIRECT_MEMBER_PROFILE_FIELDS:
                if field_name == "email_private":
                    continue
                setattr(member, field_name, normalize_optional_member_value(field_name, getattr(profile_form, field_name).data))

            log_audit_event(
                category="profile",
                event_type="contact_details_updated",
                actor_user=current_user,
                target_user=current_user,
                target_member=member,
                before={"user": before_user, "member": before_member},
                after={"user": snapshot_user_for_audit(current_user), "member": snapshot_member_for_audit(member, fields=DIRECT_MEMBER_PROFILE_FIELDS)},
                metadata={"email_changed": email_changed},
            )
            forum_result = None
            if member.user is not None and (member.user.forum_account is not None or member_has_active_access(member)):
                forum_result, _forum_service = sync_member_forum_state(member)
            db.session.commit()
            if email_changed:
                try:
                    send_email_verification_email(app, current_user)
                    flash(_("Your profile was updated. Please verify your new email address using the link we sent you."), "success")
                except Exception as exc:
                    app.logger.warning("Could not send verification email after profile update for user_id=%s: %s", current_user.id, exc)
                    flash(_("Your profile was updated."), "success")
            else:
                flash(_("Your profile was updated."), "success")
            if forum_result and forum_result.error:
                flash(_("Your forum profile could not be synchronized right now. Please try again later."), "warning")
            return redirect(url_for("account"))

        flash(_("Please correct the profile form and try again."), "danger")
        return render_account_dashboard(profile_form=profile_form, identity_form=identity_form)

    @app.route("/account/identity-request", methods=["POST"])
    @login_required
    def submit_identity_change_request():
        member = get_current_member_for_user(current_user)
        if member is None:
            flash(_("No membership profile is linked to this account yet."), "warning")
            return redirect(url_for("index"))

        identity_form = IdentityChangeRequestForm(prefix="identity")
        profile_form = MemberProfileForm(prefix="profile")
        if identity_form.validate_on_submit():
            form_data = identity_form.data
            form_data.pop("csrf_token", None)
            form_data.pop("submit", None)

            if not has_identity_changes(member, form_data):
                flash(_("There are no identity changes to request."), "warning")
                return redirect(url_for("account"))

            try:
                request_record = create_identity_change_request(member, current_user, form_data)
                db.session.flush()
                log_audit_event(
                    category="profile_change_request",
                    event_type="identity_request_submitted",
                    actor_user=current_user,
                    target_user=current_user,
                    target_member=member,
                    before=snapshot_member_for_audit(member, fields=IDENTITY_MEMBER_FIELDS),
                    after={
                        "requested_salutation": request_record.requested_salutation,
                        "requested_title": request_record.requested_title,
                        "requested_first_name": request_record.requested_first_name,
                        "requested_last_name": request_record.requested_last_name,
                        "requested_year_group": request_record.requested_year_group,
                    },
                    metadata={"request_id": request_record.id, "member_note": request_record.member_note},
                )
                db.session.commit()
                flash(_("Your identity change request has been submitted for admin review."), "success")
                return redirect(url_for("account"))
            except ValueError as exc:
                flash(str(exc), "warning")
                return render_account_dashboard(profile_form=profile_form, identity_form=identity_form)

        flash(_("Please correct the identity change form and try again."), "danger")
        return render_account_dashboard(profile_form=profile_form, identity_form=identity_form)

    @app.route("/account/identity-request/<int:request_id>/cancel", methods=["POST"])
    @login_required
    def cancel_identity_change_request(request_id):
        member = get_current_member_for_user(current_user)
        request_record = db.session.get(MemberProfileChangeRequest, request_id)
        if request_record is None or member is None or request_record.member_id != member.id or request_record.status != "pending":
            flash(_("The selected change request could not be canceled."), "warning")
            return redirect(url_for("account"))

        request_record.status = "canceled"
        request_record.reviewed_by = current_user
        request_record.reviewed_at = get_now_utc()
        log_audit_event(
            category="profile_change_request",
            event_type="identity_request_canceled",
            actor_user=current_user,
            target_user=current_user,
            target_member=member,
            before={"request_id": request_record.id, "status": "pending"},
            after={"request_id": request_record.id, "status": request_record.status},
            metadata={"member_note": request_record.member_note},
        )
        db.session.commit()
        flash(_("Your pending identity change request was canceled."), "success")
        return redirect(url_for("account"))

    @app.route("/account/billing", methods=["POST"])
    @login_required
    def manage_member_billing():
        member = get_current_member_for_user(current_user)
        if member is None:
            flash(_("No membership profile is linked to this account yet."), "warning")
            return redirect(url_for("index"))

        try:
            portal_session = get_portal_session(member)
            return redirect(portal_session.url, code=303)
        except ValueError as exc:
            flash(str(exc), "warning")
        except stripe.StripeError as exc:
            app.logger.error("Could not create Stripe portal session for member_id=%s: %s", member.id, exc)
            flash(_("Could not open the Stripe customer portal right now. Please try again later."), "danger")
        return redirect(url_for("account"))

    @app.route("/account/resume-payment", methods=["POST"])
    @login_required
    def resume_member_payment():
        member = get_current_member_for_user(current_user)
        if member is None:
            flash(_("No membership profile is linked to this account yet."), "warning")
            return redirect(url_for("index"))
        if not can_resume_payment(member):
            flash(_("This membership cannot be resumed from here. Use billing management instead if a Stripe customer already exists."), "warning")
            return redirect(url_for("account"))

        try:
            session, _cycle = create_checkout_session_for_member(member)
            db.session.commit()
            return redirect(session.url, code=303)
        except stripe.StripeError as exc:
            app.logger.error("Could not resume Checkout for member_id=%s: %s", member.id, exc)
            flash(_("Could not restart the Stripe Checkout session right now. Please try again later."), "danger")
        except Exception:
            app.logger.exception("Unexpected error while resuming payment for member_id=%s", member.id)
            flash(_("Could not restart the membership payment right now."), "danger")
        return redirect(url_for("account"))

    @app.route("/account/resend-verification", methods=["POST"])
    @login_required
    def resend_verification_email():
        if current_user.email_is_verified:
            flash(_("Your email address is already verified."), "info")
            return redirect(url_for("account"))

        try:
            if send_email_verification_email(app, current_user):
                flash(_("We sent you a new verification email."), "success")
            else:
                flash(_("We could not send a verification email because no sender account is configured yet."), "warning")
        except Exception as exc:
            app.logger.warning("Could not resend verification email for user_id=%s: %s", current_user.id, exc)
            flash(_("We could not send a verification email right now."), "danger")
        return redirect(url_for("account"))
    @app.route("/forum", methods=["GET"])
    def forum_entry():
        token = (request.args.get("token") or "").strip()
        if token:
            try:
                token_data = read_token(token, "forum-entry", TOKEN_MAX_AGE_FORUM_ENTRY)
                token_user = db.session.get(User, int(token_data.get("user_id")))
            except (BadSignature, SignatureExpired, ValueError, TypeError):
                token_user = None
                flash(_("This forum access link is invalid or has expired."), "warning")

            if token_user is not None:
                if not current_user.is_authenticated or current_user.id != token_user.id:
                    login_user(token_user)
                if not token_user.email_is_verified:
                    token_user.email_verified_at = get_now_utc()
                    db.session.commit()

        if not current_user.is_authenticated:
            flash(_("Please log in to continue to the forum."), "warning")
            next_url = request.full_path[:-1] if request.full_path.endswith("?") else request.full_path
            return redirect(url_for("login", next=next_url))

        member = get_current_member_for_user(current_user)
        if member is None:
            flash(_("A linked membership profile is required before you can access the forum."), "warning")
            return redirect(url_for("create_membership_profile"))

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
                app.logger.warning("Could not hand off to Discourse for member_id=%s: %s", member.id, exc)
                flash(_("The forum could not be opened right now. Please try again later."), "danger")

        return render_template("account/forum.html", member=member, forum_context=forum_context)

    @app.route("/forum/avatar", methods=["POST"])
    @login_required
    def upload_forum_avatar():
        member = get_current_member_for_user(current_user)
        if member is None:
            flash(_("A linked membership profile is required before you can upload a forum profile picture."), "warning")
            return redirect(url_for("create_membership_profile"))

        forum_service = get_forum_service()
        if not forum_service.is_enabled():
            flash(_("The forum integration is not enabled yet."), "warning")
            return redirect(url_for("account"))

        if not member_has_active_access(member):
            flash(_("Your membership must be active before you can upload a forum profile picture."), "warning")
            return redirect(url_for("account"))

        upload = request.files.get("avatar")
        try:
            submission = forum_service.create_avatar_submission(upload, current_user, member)
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
            db.session.commit()
            flash(_("Your profile picture was uploaded and is now waiting for admin approval."), "success")
        except ForumProviderError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("forum_entry"))

    @app.route("/forum/avatar/public/<token>", methods=["GET"])
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

    @app.route("/forum/discourse/connect", methods=["GET"])
    def forum_discourse_connect():
        if not current_user.is_authenticated:
            next_url = request.full_path[:-1] if request.full_path.endswith("?") else request.full_path
            return redirect(url_for("login", next=next_url))

        member = get_current_member_for_user(current_user)
        if member is None:
            flash(_("A linked membership profile is required before you can access the forum."), "warning")
            return redirect(url_for("create_membership_profile"))

        if not member_has_active_access(member):
            flash(_("Your membership is not active, so forum access is unavailable right now."), "warning")
            return redirect(url_for("forum_entry"))

        forum_result, service = sync_member_forum_state(member)
        if forum_result and forum_result.changed:
            db.session.commit()

        try:
            redirect_url = service.handle_provider_request(request.args, current_user, member)
            return redirect(redirect_url, code=303)
        except ForumProviderError as exc:
            app.logger.warning("DiscourseConnect handoff failed for member_id=%s: %s", member.id, exc)
            flash(_("The forum sign-in could not be completed right now."), "danger")
            return redirect(url_for("forum_entry"))

    @app.route("/forgot-password", methods=["GET", "POST"])
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
                    send_password_reset_email(app, user)
                except Exception as exc:
                    app.logger.warning("Could not send password reset email for user_id=%s: %s", user.id, exc)
            flash(_("If an account with that email address exists and email sending is configured, a password reset link is available."), "info")
            return redirect(url_for("login"))
        return render_template(
            "account/email_request.html",
            form=form,
            title=_("Reset Password"),
            heading=_("Reset your password"),
            description=_("Enter the email address of your Joanneum Aeronautics account and we will send you a reset link."),
        )

    @app.route("/reset-password/<token>", methods=["GET", "POST"])
    def reset_password(token):
        try:
            token_data = read_token(token, "reset-password", TOKEN_MAX_AGE_PASSWORD_RESET)
            user = db.session.get(User, int(token_data.get("user_id")))
        except (BadSignature, SignatureExpired, ValueError, TypeError):
            user = None

        if user is None:
            flash(_("This password reset link is invalid or has expired."), "danger")
            return redirect(url_for("forgot_password"))

        form = SetPasswordForm()
        if form.validate_on_submit():
            user.set_password(form.password.data)
            db.session.commit()
            flash(_("Your password has been updated. You can log in now."), "success")
            return redirect(url_for("login"))
        return render_template(
            "account/set_password.html",
            form=form,
            title=_("Choose a New Password"),
            heading=_("Choose a new password"),
            description=_("Set a new password for your Joanneum Aeronautics account."),
        )

    @app.route("/verify-email/<token>")
    def verify_email(token):
        try:
            token_data = read_token(token, "verify-email", TOKEN_MAX_AGE_VERIFY_EMAIL)
            user = db.session.get(User, int(token_data.get("user_id")))
        except (BadSignature, SignatureExpired, ValueError, TypeError):
            user = None

        if user is None:
            flash(_("This verification link is invalid or has expired."), "danger")
            return redirect(url_for("login"))

        if not user.email_is_verified:
            user.email_verified_at = get_now_utc()
            db.session.commit()
        flash(_("Your email address has been verified."), "success")
        if current_user.is_authenticated and current_user.id == user.id:
            return redirect(url_for(get_member_portal_target(current_user)))
        return redirect(url_for("login"))

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
        template_choices = get_email_template_choices(app)
        mail_account_records = get_db_mail_accounts()
        forum_settings = normalize_forum_settings(get_forum_settings_map())
        stripe_settings = get_stripe_settings_map()
        forum_service = ForumService(forum_settings)
        try:
            mail_accounts = load_mail_accounts_config()
            sender_choices = [(account_key, account_key) for account_key in mail_accounts.keys()]
        except Exception as exc:
            app.logger.error(f"Could not load email accounts for admin settings: {exc}")

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
            "forum_settings": forum_settings,
            "stripe_settings": stripe_settings,
            "forum_service": forum_service,
            "forum_provider_choices": [("discourse", _("Discourse"))],
            "forum_auth_strategy_choices": [
                ("discourse_connect", _("DiscourseConnect")),
                ("oauth2_provider", _("OAuth2 Provider (reserved)")),
            ],
        }

    @app.route("/admin", methods=["GET"])
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

    app.add_url_rule("/admin", endpoint="admin", view_func=admin_dashboard, methods=["GET"])

    @app.route("/admin/accounts", methods=["GET"])
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

    @app.route("/admin/accounts/<int:user_id>", methods=["GET"])
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
            return redirect(url_for("admin_accounts"))

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

    @app.route("/admin/accounts/<int:user_id>/billing-sync", methods=["POST"])
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
            return redirect(url_for("admin_accounts"))

        next_url = request.form.get("next") or url_for("admin_account_detail", user_id=user.id)
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
            app.logger.error("Manual Stripe billing sync failed for member_id=%s: %s", user.member.id, exc)
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

    @app.route("/admin/forum", methods=["GET"])
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

    @app.route("/admin/accounts/<int:user_id>/forum-resync", methods=["POST"])
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
            return redirect(url_for("admin_accounts"))

        if user.member is None:
            flash(_("This account does not have a linked membership profile yet."), "warning")
            return redirect(request.form.get("next") or url_for("admin_account_detail", user_id=user.id))

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
        return redirect(request.form.get("next") or url_for("admin_account_detail", user_id=user.id))

    @app.route("/admin/forum/submissions/<int:submission_id>/approve", methods=["POST"])
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
            return redirect(url_for("admin_forum"))

        forum_service = get_forum_service()
        before_submission = snapshot_forum_avatar_submission_for_audit(submission)
        before_account = snapshot_forum_account_for_audit(submission.user.forum_account if submission.user else None)
        review_note = (request.form.get("review_note") or "").strip() or None

        try:
            result = forum_service.approve_avatar_submission(submission, reviewer=current_user, review_note=review_note)
        except ForumProviderError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("admin_forum"))

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
        return redirect(url_for("admin_forum"))

    @app.route("/admin/forum/submissions/<int:submission_id>/reject", methods=["POST"])
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
            return redirect(url_for("admin_forum"))

        forum_service = get_forum_service()
        before_submission = snapshot_forum_avatar_submission_for_audit(submission)
        before_account = snapshot_forum_account_for_audit(submission.user.forum_account if submission.user else None)
        review_note = (request.form.get("review_note") or "").strip() or None

        try:
            result = forum_service.reject_avatar_submission(submission, reviewer=current_user, review_note=review_note)
        except ForumProviderError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("admin_forum"))

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
        db.session.commit()
        flash(_("The avatar submission was rejected."), "success")
        return redirect(url_for("admin_forum"))

    @app.route("/admin/settings/test-forum-connection", methods=["POST"])
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
        return redirect(f"{url_for('admin_settings')}#settings-forum")

    @app.route("/admin/accounts/<int:user_id>/grant-admin", methods=["POST"])
    @login_required
    @admin_required
    def grant_admin_access(user_id):
        user = db.session.get(User, user_id)
        if user is None:
            flash(_("The selected account could not be found."), "warning")
            return redirect(url_for("admin_accounts"))

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

        return redirect(request.form.get("next") or url_for("admin_account_detail", user_id=user.id))

    @app.route("/admin/accounts/<int:user_id>/revoke-admin", methods=["POST"])
    @login_required
    @admin_required
    def revoke_admin_access(user_id):
        user = db.session.get(User, user_id)
        if user is None:
            flash(_("The selected account could not be found."), "warning")
            return redirect(url_for("admin_accounts"))

        if not user.has_role("admin"):
            flash(_("This account does not currently have admin access."), "info")
            return redirect(request.form.get("next") or url_for("admin_account_detail", user_id=user.id))

        if current_user.id == user.id:
            flash(_("You cannot remove your own admin access from the UI."), "danger")
            return redirect(request.form.get("next") or url_for("admin_account_detail", user_id=user.id))

        if count_users_with_role("admin") <= 1:
            flash(_("You cannot remove the last remaining admin account."), "danger")
            return redirect(request.form.get("next") or url_for("admin_account_detail", user_id=user.id))

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
        return redirect(request.form.get("next") or url_for("admin_account_detail", user_id=user.id))

    @app.route("/admin/approvals", methods=["GET"])
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

    @app.route("/admin/settings", methods=["GET", "POST"])
    @login_required
    @admin_required
    def admin_settings():
        edit_mail_account_id = request.args.get("edit_mail_account", type=int)
        context = build_settings_page_context(edit_mail_account_id=edit_mail_account_id)

        if edit_mail_account_id and context["editing_mail_account"] is None:
            flash(_("The selected mail account could not be found."), "warning")
            return redirect(url_for("admin_settings"))

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
            ]
            before_settings = {
                key: db.session.get(Setting, key).value if db.session.get(Setting, key) is not None else None
                for key in tracked_setting_keys
            }
            settings_section = (request.form.get("settings_section") or "general").strip().lower()
            if settings_section not in {"general", "billing", "forum", "mail", "test"}:
                settings_section = "general"
            settings_redirect = f"{url_for('admin_settings')}#settings-{settings_section}"
            welcome_sender = request.form.get("welcome_email_sender")
            auto_email_template = request.form.get("automatic_email_template")
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

            set_setting_value("invoice_payments_enabled", str(invoice_enabled))
            set_setting_value("automatic_emails_enabled", str(emails_enabled))
            set_setting_value("welcome_email_sender", welcome_sender if settings_section == "general" else before_settings.get("welcome_email_sender"))
            set_setting_value("automatic_email_template", auto_email_template if settings_section == "general" else before_settings.get("automatic_email_template"))
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
            log_audit_event(
                category="settings",
                event_type="settings_updated",
                actor_user=current_user,
                target_user=current_user,
                before=before_settings,
                after=after_settings,
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

    @app.route("/admin/logs", methods=["GET"])
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

    @app.route("/admin/profile-requests/<int:request_id>/approve", methods=["POST"])
    @login_required
    @admin_required
    def approve_profile_change_request(request_id):
        request_record = db.session.get(MemberProfileChangeRequest, request_id)
        if request_record is None or request_record.status != "pending":
            flash(_("The selected change request could not be found."), "warning")
            return redirect(url_for("admin_approvals"))

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
        db.session.commit()
        flash(_("Identity change request approved."), "success")
        if forum_result and forum_result.error:
            flash(_("The forum profile could not be synchronized right now. Please run a forum resync after checking the settings."), "warning")
        return redirect(url_for("admin_approvals"))

    @app.route("/admin/profile-requests/<int:request_id>/reject", methods=["POST"])
    @login_required
    @admin_required
    def reject_profile_change_request(request_id):
        request_record = db.session.get(MemberProfileChangeRequest, request_id)
        if request_record is None or request_record.status != "pending":
            flash(_("The selected change request could not be found."), "warning")
            return redirect(url_for("admin_approvals"))

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
        db.session.commit()
        flash(_("Identity change request rejected."), "success")
        return redirect(url_for("admin_approvals"))

    @app.route("/admin/settings/mail-accounts", methods=["POST"])
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
            return redirect(url_for("admin_settings", **redirect_kwargs))

        account_key = form.account_key.data.strip()
        existing_account = db.session.execute(
            db.select(MailAccount).filter_by(account_key=account_key)
        ).scalar_one_or_none()

        if existing_account is not None and existing_account.id != account_id:
            flash(_("A mail account with this key already exists."), "danger")
            target_id = account_id or existing_account.id
            return redirect(url_for("admin_settings", edit_mail_account=target_id))

        if account_id:
            mail_account = db.session.get(MailAccount, account_id)
            if mail_account is None:
                flash(_("The selected mail account could not be found."), "warning")
                return redirect(f"{url_for('admin_settings')}#settings-mail")
        else:
            if not form.password.data:
                flash(_("A password is required for new mail accounts."), "danger")
                return redirect(f"{url_for('admin_settings')}#settings-mail")
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
            return redirect(url_for("admin_settings", **redirect_kwargs))

        flash(_("Mail account saved successfully."), "success")
        return redirect(f"{url_for('admin_settings')}#settings-mail")

    @app.route("/admin/settings/mail-accounts/<int:mail_account_id>/delete", methods=["POST"])
    @login_required
    @admin_required
    def delete_mail_account(mail_account_id):
        mail_account = db.session.get(MailAccount, mail_account_id)
        if mail_account is None:
            flash(_("The selected mail account could not be found."), "warning")
            return redirect(f"{url_for('admin_settings')}#settings-mail")

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
        return redirect(f"{url_for('admin_settings')}#settings-mail")

    @app.route("/admin/settings/mail-accounts/import", methods=["POST"])
    @login_required
    @admin_required
    def import_mail_accounts():
        upload = request.files.get("mail_accounts_file")
        overwrite_existing = request.form.get("overwrite_existing") == "1"

        if upload is None or not upload.filename:
            flash(_("Please choose a JSON file to import."), "warning")
            return redirect(f"{url_for('admin_settings')}#settings-mail")

        try:
            raw_payload = upload.stream.read()
            payload = json.loads(raw_payload.decode("utf-8-sig"))
            imported_accounts = normalize_imported_mail_accounts_payload(payload)
        except UnicodeDecodeError:
            flash(_("The uploaded file is not valid UTF-8 JSON."), "danger")
            return redirect(f"{url_for('admin_settings')}#settings-mail")
        except json.JSONDecodeError:
            flash(_("The uploaded file is not valid JSON."), "danger")
            return redirect(f"{url_for('admin_settings')}#settings-mail")
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(f"{url_for('admin_settings')}#settings-mail")

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
            return redirect(f"{url_for('admin_settings')}#settings-mail")

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

        return redirect(f"{url_for('admin_settings')}#settings-mail")

    @app.route("/admin/settings/mail-accounts/export", methods=["POST"])
    @login_required
    @admin_required
    def export_mail_accounts():
        confirm_password = request.form.get("export_password", "")
        if not current_user.check_password(confirm_password):
            flash(_("Please confirm your current password to export sender accounts."), "danger")
            return redirect(f"{url_for('admin_settings')}#settings-mail")

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

    @app.route("/admin/settings/mail-accounts/<int:mail_account_id>/test-connection", methods=["POST"])
    @login_required
    @admin_required
    @limiter.limit(RATELIMIT_ADMIN_EMAIL)
    def test_mail_account_connection(mail_account_id):
        mail_account = db.session.get(MailAccount, mail_account_id)
        if mail_account is None:
            flash(_("The selected mail account could not be found."), "warning")
            return redirect(f"{url_for('admin_settings')}#settings-mail")

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
        return redirect(url_for("admin_settings", edit_mail_account=mail_account.id))

    @app.route("/admin/settings/send-test-email", methods=["POST"])
    @login_required
    @admin_required
    @limiter.limit(RATELIMIT_ADMIN_EMAIL)
    def send_test_email():
        form = TestEmailForm()

        try:
            mail_accounts = load_mail_accounts_config()
            form.sender.choices = [(acc, acc) for acc in mail_accounts.keys()]

            email_template_dir = os.path.join(app.root_path, "templates", "emails")
            if os.path.isdir(email_template_dir):
                form.template.choices = [(f, f) for f in os.listdir(email_template_dir) if f.endswith(".html")]
        except Exception as exc:
            app.logger.error(f"Could not load email accounts or templates for test form validation: {exc}")
            form.sender.choices = []
            form.template.choices = []

        if form.validate_on_submit():
            sender = form.sender.data
            recipient = form.recipient.data
            template = form.template.data

            logo_path = os.path.join(app.root_path, "static", "Logo_Aeronautics_signature-logo.png")
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

        return redirect(f"{url_for('admin_settings')}#settings-test")

    @app.route("/login", methods=["POST", "GET"])
    @limiter.limit(RATELIMIT_LOGIN, methods=["POST"])
    def login():
        next_url = request.values.get("next") or session.get("login_next")
        safe_next_url = next_url if is_safe_next_url(next_url) else None
        if request.method == "GET":
            session.pop("login_next", None)
            if safe_next_url:
                session["login_next"] = safe_next_url
        elif safe_next_url:
            session["login_next"] = safe_next_url

        if current_user.is_authenticated:
            destination = session.pop("login_next", None)
            destination = destination if is_safe_next_url(destination) else None
            return redirect(destination or url_for(get_member_portal_target(current_user)))
        form = LoginForm()
        forum_login_hint = bool(safe_next_url and urlsplit(safe_next_url).path.startswith("/forum"))
        if form.validate_on_submit():
            user = db.session.execute(db.select(User).filter_by(email=form.email.data.strip().lower())).scalar_one_or_none()
            if user and user.check_password(form.password.data):
                login_user(user)
                destination = session.pop("login_next", None)
                destination = destination if is_safe_next_url(destination) else None
                return redirect(destination or url_for(get_member_portal_target(user)))
            flash(_("Invalid email or password"), "danger")
        return render_template("account/login.html", form=form, next_url=session.get("login_next"), forum_login_hint=forum_login_hint)



    @app.route("/register", methods=["GET", "POST"])
    def register():
        flash(_("Accounts are created automatically when you sign up for a membership."), "info")
        return redirect(url_for("index"))

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout():
        user = current_user._get_current_object()
        forum_logout_attempted, forum_logout_error = log_out_forum_session_if_possible(user)
        logout_user()
        session.pop("login_next", None)

        next_url = request.form.get("next") or url_for("index")
        if not is_safe_next_url(next_url):
            next_url = url_for("index")

        if forum_logout_error:
            flash(_("You have been logged out here, but the forum session could not be ended automatically."), "warning")
        elif forum_logout_attempted:
            flash(_("You have been logged out from both the website and the forum."), "info")
        else:
            flash(_("You have been logged out."), "info")
        return redirect(next_url)

    @app.route("/forum/logout", methods=["GET"])
    def forum_logout():
        forum_logout_error = None
        forum_logout_attempted = False
        if current_user.is_authenticated:
            user = current_user._get_current_object()
            forum_logout_attempted, forum_logout_error = log_out_forum_session_if_possible(user)
            logout_user()
            session.pop("login_next", None)
            if forum_logout_error:
                flash(_("You have been logged out here, but the forum session could not be ended automatically."), "warning")
            elif forum_logout_attempted:
                flash(_("You have been logged out from the forum and this website. Sign in again if you want to continue with a different account."), "info")
            else:
                flash(_("You have been logged out. Sign in again if you want to continue."), "info")
        else:
            flash(_("You have been logged out from the forum. Sign in again if you want to continue."), "info")
        return redirect(url_for("login", next=url_for("forum_entry")))

    @app.route("/change-password", methods=["GET", "POST"])
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

    @app.route("/stripe-webhook", methods=["POST"])
    @csrf.exempt
    def stripe_webhook():
        payload = request.data
        sig_header = request.headers.get("stripe-signature")

        try:
            stripe_settings = get_stripe_settings_map()
            stripe.api_key = stripe_settings.get("stripe_secret_key") or STRIPE_SECRET_KEY
            webhook_secret = stripe_settings.get("stripe_webhook_secret") or STRIPE_WEBHOOK_SECRET
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except ValueError as e:
            app.logger.error(f"Webhook Error: Invalid payload: {e}")
            return "Invalid payload", 400
        except stripe.SignatureVerificationError as e:
            app.logger.error(f"Webhook Error: Invalid signature: {e}")
            return "Invalid signature", 400

        event_type = event["type"]

        if event_type == "checkout.session.completed":
            session = event["data"]["object"]
            metadata = session.get("metadata", {})
            member_data_json = metadata.get("member_data")
            if not member_data_json:
                app.logger.error("Webhook received without member_data metadata.")
                return "Missing metadata", 400

            try:
                member_data = json.loads(member_data_json)
                customer_id = session.get("customer")
                subscription_id = session.get("subscription")
                member = get_member_by_stripe_or_email(
                    customer_id=customer_id,
                    subscription_id=subscription_id,
                    member_id=metadata.get("member_id"),
                    user_id=metadata.get("user_id"),
                    email=member_data.get("email_private"),
                )
                previously_active = member_has_active_access(member)
                if member is None:
                    member = Member(created_at=get_now_utc(), payment_status="pending_checkout", is_active=False)
                    db.session.add(member)

                apply_member_profile(member, {**member_data, "terms_accepted": True})
                member.pending_checkout_started_at = member.pending_checkout_started_at or get_now_utc()
                backfill_member_stripe_references(member, customer_id=customer_id, subscription_id=subscription_id)

                if member.user is not None:
                    member.user.email = member.email_private
                    if not member.user.forum_username:
                        member.user.forum_username = generate_unique_forum_username(
                            member.first_name,
                            member.last_name,
                            member.year_group,
                            exclude_user_id=member.user.id,
                        )

                starts_on = parse_iso_date(metadata.get("membership_starts_on")) or get_membership_today()
                ends_on = parse_iso_date(metadata.get("membership_ends_on")) or last_day_of_year(starts_on.year)
                renewal_due_on = parse_iso_date(metadata.get("renewal_due_on")) or first_day_of_year(ends_on.year + 1)
                activation_mode = metadata.get("activation_mode", "paid_now")
                session_payment_status = session.get("payment_status")

                if activation_mode == "free_period":
                    set_member_membership_window(
                        member,
                        starts_on=starts_on,
                        ends_on=ends_on,
                        renewal_due_on=renewal_due_on,
                        payment_status="free_period",
                        is_active=True,
                        cancel_at_period_end=False,
                    )
                    member.pending_checkout_started_at = None
                elif session_payment_status == "paid":
                    set_member_membership_window(
                        member,
                        starts_on=starts_on,
                        ends_on=ends_on,
                        renewal_due_on=renewal_due_on,
                        payment_status="paid",
                        is_active=True,
                        cancel_at_period_end=False,
                    )
                    member.pending_checkout_started_at = None
                else:
                    set_member_membership_window(
                        member,
                        starts_on=starts_on,
                        ends_on=ends_on,
                        renewal_due_on=renewal_due_on,
                        payment_status="processing",
                        is_active=False,
                        cancel_at_period_end=False,
                    )

                db.session.commit()

                if member_has_active_access(member) and not previously_active:
                    send_member_welcome_email(app, member)

                app.logger.info(
                    "SUCCESS: Membership checkout completed for %s. Session ID: %s",
                    member.email_private,
                    session.get("id"),
                )
            except Exception:
                app.logger.exception("FATAL DB ERROR on Webhook for session %s", session.get("id"))
                db.session.rollback()
                return "Database save failed", 500

        elif event_type == "payment_intent.processing":
            payment_intent = event["data"]["object"]
            customer_id = payment_intent.get("customer")
            member = get_member_by_stripe_or_email(
                customer_id=customer_id,
                email=payment_intent.get("receipt_email"),
                fetch_customer_email=True,
            )
            if member and not member_has_active_access(member):
                backfill_member_stripe_references(member, customer_id=customer_id)
                member.payment_status = "processing"
                db.session.commit()
                app.logger.info("Payment is processing for Stripe Customer ID: %s", customer_id)

        elif event_type in ["payment_intent.succeeded", "invoice.paid", "invoice.payment_succeeded"]:
            data_object = event["data"]["object"]
            customer_id = data_object.get("customer")
            subscription_id = data_object.get("subscription")
            customer_email = data_object.get("customer_email") or data_object.get("receipt_email")
            member = get_member_by_stripe_or_email(
                customer_id=customer_id,
                subscription_id=subscription_id,
                email=customer_email,
                fetch_customer_email=True,
            )
            if member:
                previous_status = member.payment_status
                backfill_member_stripe_references(member, customer_id=customer_id, subscription_id=subscription_id)

                paid_timestamp = None
                if event_type.startswith("invoice"):
                    paid_timestamp = (data_object.get("status_transitions") or {}).get("paid_at") or data_object.get("created")
                else:
                    paid_timestamp = data_object.get("created")
                paid_on = to_membership_date(paid_timestamp)

                update_member_paid_coverage(member, paid_on)
                member.pending_checkout_started_at = None
                forum_result, _forum_service = sync_member_forum_state(member)
                db.session.commit()
                app.logger.info(
                    "SUCCESS: Payment confirmed and member coverage updated for Stripe Customer ID: %s",
                    customer_id,
                )
                if forum_result and forum_result.error:
                    app.logger.warning("Forum sync reported an issue after payment success for member_id=%s: %s", member.id, forum_result.error)

                if member_has_active_access(member) and previous_status in {"unpaid", "processing", "pending_checkout", "failed"}:
                    send_member_welcome_email(app, member)
            else:
                app.logger.error(
                    "Webhook for successful payment received, but no member found for Stripe reference customer=%s subscription=%s email=%s",
                    customer_id,
                    subscription_id,
                    customer_email,
                )
                return "Member not found", 400

        elif event_type == "customer.subscription.updated":
            subscription = event["data"]["object"]
            customer_id = subscription.get("customer")
            subscription_id = subscription.get("id")
            member = get_member_by_stripe_or_email(
                customer_id=customer_id,
                subscription_id=subscription_id,
                fetch_customer_email=True,
            )
            if member:
                state_changed = sync_member_subscription_state_from_subscription(member, subscription)
                forum_result, _forum_service = sync_member_forum_state(member)
                if state_changed or (forum_result and forum_result.changed):
                    db.session.commit()
                app.logger.info(
                    "Subscription updated for member_id=%s customer=%s subscription=%s status=%s cancel_at_period_end=%s cancel_at=%s",
                    member.id,
                    customer_id,
                    subscription_id,
                    subscription.get("status"),
                    subscription.get("cancel_at_period_end"),
                    subscription.get("cancel_at"),
                )
                if forum_result and forum_result.error:
                    app.logger.warning("Forum sync reported an issue after subscription update for member_id=%s: %s", member.id, forum_result.error)
            else:
                app.logger.warning(
                    "Subscription update webhook received, but no member was found for customer=%s subscription=%s status=%s cancel_at_period_end=%s cancel_at=%s",
                    customer_id,
                    subscription_id,
                    subscription.get("status"),
                    subscription.get("cancel_at_period_end"),
                    subscription.get("cancel_at"),
                )

        elif event_type in ["payment_intent.payment_failed", "invoice.payment_failed"]:
            data_object = event["data"]["object"]
            customer_id = data_object.get("customer")
            subscription_id = data_object.get("subscription")
            customer_email = data_object.get("customer_email") or data_object.get("receipt_email")
            member = get_member_by_stripe_or_email(
                customer_id=customer_id,
                subscription_id=subscription_id,
                email=customer_email,
                fetch_customer_email=True,
            )
            if member:
                backfill_member_stripe_references(member, customer_id=customer_id, subscription_id=subscription_id)
                member.payment_status = "failed"
                if not member_has_active_access(member):
                    member.is_active = False
                forum_result, _forum_service = sync_member_forum_state(member)
                db.session.commit()
                app.logger.warning("Payment failed for Stripe Customer ID: %s", customer_id)
                if forum_result and forum_result.error:
                    app.logger.warning("Forum sync reported an issue after payment failure for member_id=%s: %s", member.id, forum_result.error)
            else:
                app.logger.warning(
                    "Webhook for failed payment received, but no Stripe Customer ID was provided."
                )

        elif event_type == "customer.subscription.deleted":
            subscription = event["data"]["object"]
            customer_id = subscription.get("customer")
            subscription_id = subscription.get("id")
            member = get_member_by_stripe_or_email(
                customer_id=customer_id,
                subscription_id=subscription_id,
                fetch_customer_email=True,
            )
            if member:
                backfill_member_stripe_references(member, customer_id=customer_id, subscription_id=subscription_id)
                backfill_member_coverage_from_subscription(member, subscription)
                member.cancel_at_period_end = False
                cancellation_details = subscription.get("cancellation_details", {})
                reason = cancellation_details.get("reason")
                event_date = to_membership_date(event.get("created"))

                if reason == "payment_failed":
                    member.payment_status = "failed"
                    member.is_active = False
                    app.logger.warning(
                        "Subscription for Stripe Customer ID: %s was canceled due to failed payment.",
                        customer_id,
                    )
                else:
                    member.payment_status = "canceled"
                    member.is_active = member_has_active_access(member, event_date)
                    app.logger.info(
                        "Subscription canceled for Stripe Customer ID: %s. Coverage remains valid until the covered year ends.",
                        customer_id,
                    )

                sync_member_active_state(member, event_date)
                forum_result, _forum_service = sync_member_forum_state(member)
                db.session.commit()
                if forum_result and forum_result.error:
                    app.logger.warning("Forum sync reported an issue after subscription deletion for member_id=%s: %s", member.id, forum_result.error)
            else:
                app.logger.warning(
                    "Webhook for subscription cancellation received, but no member found for Stripe reference customer=%s subscription=%s",
                    customer_id,
                    subscription_id,
                )

        elif event_type == "charge.dispute.closed":
            dispute = event["data"]["object"]
            if dispute["status"] == "lost":
                charge_id = dispute.get("charge")
                try:
                    apply_runtime_stripe_config()
                    charge = stripe.Charge.retrieve(charge_id)
                    customer_id = charge.get("customer")
                    if customer_id:
                        member = Member.query.filter_by(stripe_customer_id=customer_id).first()
                        if member:
                            member.is_active = False
                            member.payment_status = "dispute_lost"
                            db.session.commit()
                            app.logger.error(
                                "DISPUTE LOST for Stripe Customer ID: %s. Member has been deactivated.",
                                customer_id,
                            )
                except Exception as e:
                    app.logger.error(f"Error handling dispute for charge {charge_id}: {e}")

        return "Success", 200

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        flash(_("Your session has expired or the form is invalid. Please try submitting again."), "warning")
        return redirect(request.referrer or url_for("index"))

    @app.errorhandler(RateLimitExceeded)
    def handle_rate_limit_error(e):
        flash(_("Too many requests from your IP address. Please wait a moment and try again."), "warning")
        return redirect(request.referrer or url_for("index")), 429

    @app.errorhandler(404)
    def page_not_found(e):
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def internal_server_error(e):
        app.logger.error(f"Internal Server Error: {e}")
        db.session.rollback()
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




