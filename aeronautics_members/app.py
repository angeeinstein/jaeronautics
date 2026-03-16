import json
import os
import sys
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps
from pathlib import Path
from subprocess import run
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import click
import stripe
from dotenv import load_dotenv
from flask import (
    Flask,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask.cli import with_appcontext
from flask_babel import Babel, _, format_currency, format_date, get_locale
from flask_limiter import Limiter
from flask_limiter.errors import RateLimitExceeded
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from flask_wtf.csrf import CSRFError, CSRFProtect
from sqlalchemy import inspect, text as sql_text
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.routing import BuildError

try:
    from .db_models import db, MailAccount, Member, MemberProfileChangeRequest, Setting, User
    from .forms import (
        ChangePasswordForm,
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
    from .mail_utils import load_mail_accounts_config, send_mail
except ImportError:
    from db_models import db, MailAccount, Member, MemberProfileChangeRequest, Setting, User
    from forms import (
        ChangePasswordForm,
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
    from mail_utils import load_mail_accounts_config, send_mail

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
        if not current_user.is_authenticated or current_user.role != "admin":
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
PENDING_SIGNUP_RETENTION_DAYS = int(os.getenv("PENDING_SIGNUP_RETENTION_DAYS", "14"))


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
    price = stripe.Price.retrieve(STRIPE_PRICE_ID, expand=["product"])
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
            _("Please confirm your email address so future account services like forum SSO can be enabled safely."),
            _("You can already use the membership portal, but verification will be required for additional integrations later on."),
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
    if user.role == "admin":
        return "admin"
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
    price_details = get_stripe_membership_price()
    join_date = get_membership_today()
    cycle = build_membership_cycle(join_date, price_details["unit_amount"])
    activation_mode = "free_period" if cycle["free_period"] else "paid_now"
    membership_metadata = build_membership_metadata(member, cycle, activation_mode)
    line_items = [{"price": STRIPE_PRICE_ID, "quantity": 1}]
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
        "items": [{"price": STRIPE_PRICE_ID}],
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
    if member is None or not member.stripe_customer_id:
        return None

    if member.stripe_subscription_id:
        return stripe.Subscription.retrieve(member.stripe_subscription_id)

    subscription_list = stripe.Subscription.list(customer=member.stripe_customer_id, status="all", limit=1)
    subscriptions = subscription_list.get("data", []) if hasattr(subscription_list, "get") else []
    return subscriptions[0] if subscriptions else None



def sync_member_subscription_state_from_stripe(member):
    if member is None or not member.stripe_customer_id:
        return False

    subscription = get_latest_stripe_subscription_for_member(member)
    if not subscription:
        return False

    changed = backfill_member_stripe_references(
        member,
        customer_id=subscription.get("customer") or member.stripe_customer_id,
        subscription_id=subscription.get("id"),
    )

    cancel_at_period_end = subscription_has_scheduled_cancellation(subscription)
    if member.cancel_at_period_end != cancel_at_period_end:
        member.cancel_at_period_end = cancel_at_period_end
        changed = True

    subscription_status = subscription.get("status")
    if subscription_status == "canceled":
        desired_status = "canceled"
        desired_active = member_has_active_access(member)
    elif cancel_at_period_end and member_has_active_access(member):
        desired_status = "cancel_scheduled"
        desired_active = True
    elif not cancel_at_period_end and member.payment_status == "cancel_scheduled":
        desired_status = "paid" if member.is_active else member.payment_status
        desired_active = member.is_active
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



def get_portal_session(member):
    if not member or not member.stripe_customer_id:
        raise ValueError(_("No Stripe billing profile is available for this membership yet."))

    refresh_token = int(datetime.now(timezone.utc).timestamp())
    return stripe.billing_portal.Session.create(
        customer=member.stripe_customer_id,
        return_url=url_for("account", _external=True, refresh_billing=1, rt=refresh_token),
    )



def send_member_welcome_email(app, member, force_send=False):
    settings = {s.key: s.value for s in Setting.query.all()}
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

        connection.execute(sql_text("UPDATE users SET role = 'member' WHERE role IS NULL OR role = 'user'"))

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



def backfill_member_user_links():
    changed = False
    members = db.session.execute(db.select(Member).order_by(Member.id.asc())).scalars().all()
    for member in members:
        if member.user is None:
            matched_user = db.session.execute(db.select(User).filter_by(email=member.email_private)).scalar_one_or_none()
            if matched_user is not None:
                member.user = matched_user
                if matched_user.role != "admin":
                    matched_user.role = "member"
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
            ensure_user_schema()
            ensure_member_schema()
            backfill_member_user_links()
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
        user = db.session.execute(db.select(User).filter_by(email=email)).scalar_one_or_none()
        created = user is None

        if created:
            user = User(email=email, role="admin")
            db.session.add(user)
        else:
            user.role = "admin"

        user.set_password(password)
        db.session.commit()

        if created:
            click.echo(click.style(f"Created admin user: {email}", fg="green"))
        else:
            click.echo(click.style(f"Promoted existing user to admin: {email}", fg="green"))

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

        changed = False
        stripe_subscription = None
        if member.stripe_customer_id:
            try:
                stripe_subscription = get_latest_stripe_subscription_for_member(member)
                changed = sync_member_subscription_state_from_stripe(member)
            except stripe.StripeError as exc:
                click.echo(click.style(f"Stripe sync failed: {exc}", fg="red"), err=True)
                sys.exit(1)
        else:
            click.echo(click.style("Member has no Stripe customer ID yet; nothing to sync from Stripe.", fg="yellow"))

        if sync_member_active_state(member):
            changed = True

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
        click.echo(f"  changed: {changed}")

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
            if user is not None and user.role == "member":
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
            if current_user.role == "admin":
                return redirect(url_for("admin"))
            flash(_("No membership profile is linked to this account yet."), "warning")
            return redirect(url_for("index"))

        if member.stripe_customer_id:
            try:
                stripe_state_changed = sync_member_subscription_state_from_stripe(member)
                local_state_changed = sync_member_active_state(member)
                if stripe_state_changed or local_state_changed:
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

        return render_template(
            "account/index.html",
            member=member,
            profile_form=profile_form,
            identity_form=identity_form,
            pending_request=pending_request,
            suggested_username_from_request=suggested_username_from_request,
            can_manage_billing=bool(member.stripe_customer_id),
            can_resume_payment=can_resume_payment(member),
        )

    @app.route("/", methods=["GET"])
    def index():
        form = MembershipForm()
        return render_template(
            "index.html",
            form=form,
            stripe_key=STRIPE_PUBLISHABLE_KEY,
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
                role="member",
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
                    db.session.commit()
                    if cycle["free_period"]:
                        send_member_welcome_email(app, member)
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
            try:
                email_changed = sync_member_primary_email(member, profile_form.email_private.data)
            except ValueError as exc:
                flash(str(exc), "danger")
                return render_account_dashboard(profile_form=profile_form, identity_form=identity_form)

            for field_name in DIRECT_MEMBER_PROFILE_FIELDS:
                if field_name == "email_private":
                    continue
                setattr(member, field_name, normalize_optional_member_value(field_name, getattr(profile_form, field_name).data))

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
                create_identity_change_request(member, current_user, form_data)
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

    @app.route("/admin", methods=["GET", "POST"])
    @login_required
    @admin_required
    def admin():
        test_email_form = TestEmailForm()
        mail_account_form = MailAccountForm(prefix="mail")
        editing_mail_account = None

        sender_choices = []
        template_choices = get_email_template_choices(app)
        mail_account_records = get_db_mail_accounts()
        pending_identity_requests = db.session.execute(
            db.select(MemberProfileChangeRequest)
            .filter_by(status="pending")
            .order_by(MemberProfileChangeRequest.created_at.asc())
        ).scalars().all()
        decorate_pending_identity_requests(pending_identity_requests)
        try:
            mail_accounts = load_mail_accounts_config()
            sender_choices = [(acc, acc) for acc in mail_accounts.keys()]
        except Exception as e:
            mail_accounts = {}
            app.logger.error(f"Could not load email accounts for admin page: {e}")

        edit_mail_account_id = request.args.get("edit_mail_account", type=int)
        if edit_mail_account_id:
            editing_mail_account = db.session.get(MailAccount, edit_mail_account_id)
            if editing_mail_account is not None:
                mail_account_form.mail_account_id.data = str(editing_mail_account.id)
                mail_account_form.account_key.data = editing_mail_account.account_key
                mail_account_form.host.data = editing_mail_account.host
                mail_account_form.port.data = editing_mail_account.port
                mail_account_form.username.data = editing_mail_account.username
                mail_account_form.starttls.data = editing_mail_account.starttls
            else:
                flash(_("The selected mail account could not be found."), "warning")
                return redirect(url_for("admin"))

        test_email_form.sender.choices = sender_choices
        test_email_form.template.choices = template_choices

        if request.method == "POST" and "save_settings" in request.form:
            valid_senders = {choice for choice, _label in sender_choices}
            valid_templates = {choice for choice, _label in template_choices}
            welcome_sender = request.form.get("welcome_email_sender")
            auto_email_template = request.form.get("automatic_email_template")

            if welcome_sender and welcome_sender not in valid_senders:
                flash(_("Invalid sender account selected."), "danger")
                return redirect(url_for("admin"))

            if auto_email_template and auto_email_template not in valid_templates:
                flash(_("Invalid email template selected."), "danger")
                return redirect(url_for("admin"))

            invoice_enabled = "invoice_payments_enabled" in request.form
            setting = Setting.query.get("invoice_payments_enabled")
            if setting:
                setting.value = str(invoice_enabled)
            else:
                setting = Setting(key="invoice_payments_enabled", value=str(invoice_enabled))
                db.session.add(setting)

            emails_enabled = "automatic_emails_enabled" in request.form
            setting = Setting.query.get("automatic_emails_enabled")
            if setting:
                setting.value = str(emails_enabled)
            else:
                setting = Setting(key="automatic_emails_enabled", value=str(emails_enabled))
                db.session.add(setting)

            if welcome_sender:
                setting = Setting.query.get("welcome_email_sender")
                if setting:
                    setting.value = welcome_sender
                else:
                    setting = Setting(key="welcome_email_sender", value=welcome_sender)
                    db.session.add(setting)
            else:
                setting = Setting.query.get("welcome_email_sender")
                if setting:
                    db.session.delete(setting)

            if auto_email_template:
                setting = Setting.query.get("automatic_email_template")
                if setting:
                    setting.value = auto_email_template
                else:
                    setting = Setting(key="automatic_email_template", value=auto_email_template)
                    db.session.add(setting)
            else:
                setting = Setting.query.get("automatic_email_template")
                if setting:
                    db.session.delete(setting)

            db.session.commit()
            flash(_("Settings updated successfully!"), "success")
            return redirect(url_for("admin"))

        return render_template(
            "admin/index.html",
            test_email_form=test_email_form,
            mail_account_form=mail_account_form,
            mail_account_records=mail_account_records,
            editing_mail_account=editing_mail_account,
            sender_choices=sender_choices,
            template_choices=template_choices,
            pending_identity_requests=pending_identity_requests,
        )

    @app.route("/admin/profile-requests/<int:request_id>/approve", methods=["POST"])
    @login_required
    @admin_required
    def approve_profile_change_request(request_id):
        request_record = db.session.get(MemberProfileChangeRequest, request_id)
        if request_record is None or request_record.status != "pending":
            flash(_("The selected change request could not be found."), "warning")
            return redirect(url_for("admin"))

        member = request_record.member
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
        db.session.commit()
        flash(_("Identity change request approved."), "success")
        return redirect(url_for("admin"))

    @app.route("/admin/profile-requests/<int:request_id>/reject", methods=["POST"])
    @login_required
    @admin_required
    def reject_profile_change_request(request_id):
        request_record = db.session.get(MemberProfileChangeRequest, request_id)
        if request_record is None or request_record.status != "pending":
            flash(_("The selected change request could not be found."), "warning")
            return redirect(url_for("admin"))

        request_record.status = "rejected"
        request_record.admin_note = (request.form.get("admin_note") or "").strip() or None
        request_record.reviewed_by = current_user
        request_record.reviewed_at = get_now_utc()
        db.session.commit()
        flash(_("Identity change request rejected."), "success")
        return redirect(url_for("admin"))

    @app.route("/admin/mail-accounts", methods=["POST"])
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
            return redirect(url_for("admin", **redirect_kwargs))

        account_key = form.account_key.data.strip()
        existing_account = db.session.execute(
            db.select(MailAccount).filter_by(account_key=account_key)
        ).scalar_one_or_none()

        if existing_account is not None and existing_account.id != account_id:
            flash(_("A mail account with this key already exists."), "danger")
            target_id = account_id or existing_account.id
            return redirect(url_for("admin", edit_mail_account=target_id))

        if account_id:
            mail_account = db.session.get(MailAccount, account_id)
            if mail_account is None:
                flash(_("The selected mail account could not be found."), "warning")
                return redirect(url_for("admin"))
        else:
            if not form.password.data:
                flash(_("A password is required for new mail accounts."), "danger")
                return redirect(url_for("admin"))
            mail_account = MailAccount()
            db.session.add(mail_account)

        mail_account.account_key = account_key
        mail_account.host = form.host.data.strip()
        mail_account.port = int(form.port.data)
        mail_account.username = form.username.data.strip()
        if form.password.data:
            mail_account.password = form.password.data
        mail_account.starttls = bool(form.starttls.data)

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash(_("A mail account with this key already exists."), "danger")
            redirect_kwargs = {"edit_mail_account": account_id} if account_id else {}
            return redirect(url_for("admin", **redirect_kwargs))

        flash(_("Mail account saved successfully."), "success")
        return redirect(url_for("admin"))

    @app.route("/admin/mail-accounts/<int:mail_account_id>/delete", methods=["POST"])
    @login_required
    @admin_required
    def delete_mail_account(mail_account_id):
        mail_account = db.session.get(MailAccount, mail_account_id)
        if mail_account is None:
            flash(_("The selected mail account could not be found."), "warning")
            return redirect(url_for("admin"))

        welcome_sender_setting = Setting.query.get("welcome_email_sender")
        if welcome_sender_setting and welcome_sender_setting.value == mail_account.account_key:
            db.session.delete(welcome_sender_setting)

        db.session.delete(mail_account)
        db.session.commit()
        flash(_("Mail account deleted successfully."), "success")
        return redirect(url_for("admin"))

    @app.route("/send-test-email", methods=["POST"])
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
        except Exception as e:
            app.logger.error(f"Could not load email accounts or templates for test form validation: {e}")
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

        return redirect(url_for("admin"))

    @app.route("/login", methods=["POST", "GET"])
    @limiter.limit(RATELIMIT_LOGIN, methods=["POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for(get_member_portal_target(current_user)))
        form = LoginForm()
        if form.validate_on_submit():
            user = db.session.execute(db.select(User).filter_by(email=form.email.data.strip().lower())).scalar_one_or_none()
            if user and user.check_password(form.password.data):
                login_user(user)
                return redirect(url_for(get_member_portal_target(user)))
            flash(_("Invalid email or password"), "danger")
        return render_template("admin/login.html", form=form)

    @app.route("/register", methods=["GET", "POST"])
    def register():
        flash(_("Accounts are created automatically when you sign up for a membership."), "info")
        return redirect(url_for("index"))

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("index"))

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
        return render_template("admin/change_password.html", form=form)

    @app.route("/stripe-webhook", methods=["POST"])
    @csrf.exempt
    def stripe_webhook():
        payload = request.data
        sig_header = request.headers.get("stripe-signature")

        try:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
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
                    if member.user.role != "admin":
                        member.user.role = "member"
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
                db.session.commit()
                app.logger.info(
                    "SUCCESS: Payment confirmed and member coverage updated for Stripe Customer ID: %s",
                    customer_id,
                )

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
                backfill_member_stripe_references(member, customer_id=customer_id, subscription_id=subscription_id)
                member.cancel_at_period_end = subscription_has_scheduled_cancellation(subscription)
                if member.cancel_at_period_end and member_has_active_access(member):
                    member.payment_status = "cancel_scheduled"
                elif not member.cancel_at_period_end and member.payment_status == "cancel_scheduled":
                    member.payment_status = "paid" if member.is_active else member.payment_status
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
                db.session.commit()
                app.logger.warning("Payment failed for Stripe Customer ID: %s", customer_id)
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
                db.session.commit()
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
