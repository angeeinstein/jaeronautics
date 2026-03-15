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
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask.cli import with_appcontext
from flask_babel import Babel, _, get_locale
from flask_limiter import Limiter
from flask_limiter.errors import RateLimitExceeded
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from flask_wtf.csrf import CSRFError, CSRFProtect
from sqlalchemy import inspect, text as sql_text
from sqlalchemy.exc import IntegrityError
from werkzeug.middleware.proxy_fix import ProxyFix

try:
    from .db_models import db, MailAccount, Member, Setting, User
    from .forms import (
        ChangePasswordForm,
        LoginForm,
        MailAccountForm,
        MembershipForm,
        RegistrationForm,
        TestEmailForm,
    )
    from .mail_utils import load_mail_accounts_config, send_mail
except ImportError:
    from db_models import db, MailAccount, Member, Setting, User
    from forms import (
        ChangePasswordForm,
        LoginForm,
        MailAccountForm,
        MembershipForm,
        RegistrationForm,
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



def generate_suggested_username(member):
    """Generates a suggested username for LAV-Board."""
    last_name_cleaned = "".join(filter(str.isalnum, member.last_name)).capitalize()
    first_name_initial = member.first_name[0].upper() if member.first_name else ""
    study_field_initial = member.year_group[0].upper() if member.year_group else ""
    year_short = member.year_group[-2:] if member.year_group and len(member.year_group) > 2 else ""
    return f"{last_name_cleaned}{first_name_initial}_{study_field_initial}{year_short}"



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




MEMBER_PROFILE_FIELDS = (
    "salutation",
    "title",
    "first_name",
    "last_name",
    "street",
    "house_number",
    "postal_code",
    "city",
    "country",
    "phone_private",
    "email_private",
    "phone_work",
    "email_work",
    "year_group",
)


ACTIVE_MEMBER_STATUSES = {"paid", "free_period", "canceled", "cancel_scheduled"}


def get_membership_now():
    return datetime.now(timezone.utc).astimezone(MEMBERSHIP_TIMEZONE)


def get_membership_today():
    return get_membership_now().date()


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
    if recurring.get("interval") != "year" or recurring.get("interval_count", 1) != 1:
        raise ValueError("STRIPE_PRICE_ID must point to a yearly recurring Stripe price.")

    unit_amount = price.get("unit_amount")
    if unit_amount is None:
        raise ValueError("The Stripe membership price must have a fixed unit_amount.")

    return {
        "id": price["id"],
        "currency": price["currency"],
        "unit_amount": int(unit_amount),
    }


def build_prorated_line_item(cycle, price_details):
    if cycle["prorated_amount_cents"] <= 0:
        return None

    return {
        "price_data": {
            "currency": price_details["currency"],
            "product_data": {
                "name": f"Membership fee {cycle['current_year']} (prorated)",
            },
            "unit_amount": cycle["prorated_amount_cents"],
        },
        "quantity": 1,
    }


def apply_member_profile(member, form_data):
    for field_name in MEMBER_PROFILE_FIELDS:
        value = form_data.get(field_name)
        if value == "" and field_name in {"title", "phone_work", "email_work"}:
            value = None
        setattr(member, field_name, value)
    member.terms_accepted = True


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


def get_member_by_stripe_reference(customer_id=None, subscription_id=None):
    if subscription_id:
        member = Member.query.filter_by(stripe_subscription_id=subscription_id).first()
        if member is not None:
            return member
    if customer_id:
        return Member.query.filter_by(stripe_customer_id=customer_id).first()
    return None


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

    suggested_username = generate_suggested_username(member)
    logo_path = os.path.join(app.root_path, "static", "Logo_Aeronautics_signature-logo.png")
    attachments = [{"path": logo_path, "cid": "logo"}]

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
        now=datetime.now(timezone.utc),
    )


def ensure_member_schema():
    inspector = inspect(db.engine)
    if "member" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("member")}
    alter_statements = []

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
                sql_text(
                    "CREATE UNIQUE INDEX uq_member_stripe_subscription_id ON member (stripe_subscription_id)"
                )
            )


def create_app():
    app = Flask(__name__)

    @app.context_processor
    def inject_language_switcher():
        def switch_lang_url(lang):
            args = request.args.copy()
            args["lang"] = lang
            endpoint = request.endpoint or "index"
            return url_for(endpoint, **args)

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

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1, x_proto=1)
    stripe.api_key = STRIPE_SECRET_KEY

    @app.cli.command("db-init")
    @with_appcontext
    def db_init():
        """Creates database tables if they do not exist and adds newer membership columns when needed."""
        click.echo("Creating database tables...")
        try:
            db.create_all()
            ensure_member_schema()
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

            payment_method = form_data.pop("payment_method", "checkout")
            existing_member = Member.query.filter_by(email_private=form_data["email_private"]).first()

            if existing_member and sync_member_active_state(existing_member):
                db.session.commit()

            if existing_member and member_has_active_access(existing_member):
                flash(_("An active membership already exists for this email address."), "warning")
                return redirect(url_for("index"))

            if settings.get("invoice_payments_enabled") != "True":
                payment_method = "checkout"

            try:
                price_details = get_stripe_membership_price()
                join_date = get_membership_today()
                cycle = build_membership_cycle(join_date, price_details["unit_amount"])
                activation_mode = "free_period" if cycle["free_period"] else "paid_now"
                membership_metadata = {
                    "membership_starts_on": cycle["coverage_start"].isoformat(),
                    "membership_ends_on": cycle["coverage_end"].isoformat(),
                    "renewal_due_on": cycle["renewal_due_on"].isoformat(),
                    "activation_mode": activation_mode,
                    "member_email": form_data["email_private"],
                }

                if payment_method == "checkout":
                    line_items = [{"price": STRIPE_PRICE_ID, "quantity": 1}]
                    prorated_line_item = build_prorated_line_item(cycle, price_details)
                    if prorated_line_item is not None:
                        line_items.insert(0, prorated_line_item)

                    session = stripe.checkout.Session.create(
                        payment_method_types=["card", "sepa_debit"],
                        line_items=line_items,
                        mode="subscription",
                        metadata={**membership_metadata, "member_data": json.dumps(form_data)},
                        subscription_data={
                            "trial_end": cycle["trial_end_unix"],
                            "metadata": membership_metadata,
                        },
                        payment_method_collection="always",
                        customer_email=form_data["email_private"],
                        success_url=url_for(
                            "thank_you",
                            _external=True,
                            method="checkout",
                            phase=cycle["thank_you_phase"],
                        ),
                        cancel_url=url_for("cancel", _external=True),
                    )
                    return redirect(session.url, code=303)

                if payment_method == "invoice":
                    customer = stripe.Customer.create(
                        email=form_data["email_private"],
                        name=f"{form_data['first_name']} {form_data['last_name']}",
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

                    member = existing_member or Member(created_at=datetime.now(timezone.utc))
                    apply_member_profile(member, form_data)
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

                    if existing_member is None:
                        db.session.add(member)

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
                    "Stripe Error during membership signup: type=%s message=%s user_message=%s code=%s param=%s request_id=%s http_status=%s payment_method=%s email=%s join_date=%s phase=%s prorated_amount_cents=%s renewal_due_on=%s",
                    type(e).__name__,
                    str(e),
                    error_details.get("message"),
                    error_details.get("code"),
                    error_details.get("param"),
                    getattr(e, "request_id", None),
                    getattr(e, "http_status", None),
                    payment_method,
                    form_data.get("email_private"),
                    cycle.get("join_date") if 'cycle' in locals() else None,
                    cycle.get("thank_you_phase") if 'cycle' in locals() else None,
                    cycle.get("prorated_amount_cents") if 'cycle' in locals() else None,
                    cycle.get("renewal_due_on") if 'cycle' in locals() else None,
                )
                flash(_("Error processing payment. Please try again."), "danger")
            except Exception as e:
                app.logger.exception(
                    "Unexpected error during membership signup for email=%s payment_method=%s",
                    form_data.get("email_private"),
                    payment_method,
                )
                flash(_("An unexpected error occurred. Please try again."), "danger")

            return redirect(url_for("index"))

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
        )

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
            if current_user.role == "admin":
                return redirect(url_for("admin"))
            return redirect(url_for("index"))
        form = LoginForm()
        if form.validate_on_submit():
            user = db.session.execute(db.select(User).filter_by(email=form.email.data)).scalar_one_or_none()
            if user and user.check_password(form.password.data):
                login_user(user)
                if user.role == "admin":
                    return redirect(url_for("admin"))
                flash(_("Login successful, but this account does not have admin access."), "info")
                return redirect(url_for("index"))
            flash(_("Invalid email or password"), "danger")
        return render_template("admin/login.html", form=form)

    @app.route("/register", methods=["GET", "POST"])
    @limiter.limit(RATELIMIT_REGISTER, methods=["POST"])
    def register():
        if current_user.is_authenticated:
            if current_user.role == "admin":
                return redirect(url_for("admin"))
            return redirect(url_for("index"))
        form = RegistrationForm()
        if form.validate_on_submit():
            existing_user = db.session.execute(db.select(User).filter_by(email=form.email.data)).scalar_one_or_none()
            if existing_user is not None:
                flash(_("An account with this email address already exists. Please log in instead."), "warning")
                return redirect(url_for("login"))

            user = User(email=form.email.data)
            user.set_password(form.password.data)
            db.session.add(user)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash(_("An account with this email address already exists. Please log in instead."), "warning")
                return redirect(url_for("login"))
            flash(_("Congratulations, you are now a registered user!"), "success")
            return redirect(url_for("login"))
        return render_template("admin/register.html", form=form)

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
                if current_user.role == "admin":
                    return redirect(url_for("admin"))
                return redirect(url_for("index"))
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
                email = member_data.get("email_private")
                customer_id = session.get("customer")
                subscription_id = session.get("subscription")
                if not email or not customer_id:
                    app.logger.error("Webhook missing email or customer ID in session.")
                    return "Missing data", 400

                member = Member.query.filter_by(email_private=email).first()
                previously_active = member_has_active_access(member)
                if member is None:
                    member = Member(created_at=datetime.now(timezone.utc))
                    db.session.add(member)

                apply_member_profile(member, member_data)
                member.stripe_customer_id = customer_id
                if subscription_id and isinstance(subscription_id, str) and subscription_id.startswith("sub_"):
                    member.stripe_subscription_id = subscription_id

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
                    f"SUCCESS: Membership checkout completed for {member.email_private}. Session ID: {session.get('id')}"
                )
            except Exception as e:
                app.logger.error(f"FATAL DB ERROR on Webhook for session {session.get('id')}: {e}")
                db.session.rollback()
                return "Database save failed", 500

        elif event_type == "payment_intent.processing":
            payment_intent = event["data"]["object"]
            customer_id = payment_intent.get("customer")
            member = get_member_by_stripe_reference(customer_id=customer_id)
            if member and not member_has_active_access(member):
                member.payment_status = "processing"
                db.session.commit()
                app.logger.info(f"Payment is processing for Stripe Customer ID: {customer_id}")

        elif event_type in ["payment_intent.succeeded", "invoice.paid", "invoice.payment_succeeded"]:
            data_object = event["data"]["object"]
            customer_id = data_object.get("customer")
            subscription_id = data_object.get("subscription")
            member = get_member_by_stripe_reference(customer_id=customer_id, subscription_id=subscription_id)
            if member:
                previous_status = member.payment_status
                if subscription_id and isinstance(subscription_id, str) and subscription_id.startswith("sub_"):
                    member.stripe_subscription_id = subscription_id

                paid_timestamp = None
                if event_type.startswith("invoice"):
                    paid_timestamp = (data_object.get("status_transitions") or {}).get("paid_at") or data_object.get("created")
                else:
                    paid_timestamp = data_object.get("created")
                paid_on = to_membership_date(paid_timestamp)

                update_member_paid_coverage(member, paid_on)
                db.session.commit()
                app.logger.info(
                    f"SUCCESS: Payment confirmed and member coverage updated for Stripe Customer ID: {customer_id}"
                )

                if member_has_active_access(member) and previous_status in {"unpaid", "processing"}:
                    send_member_welcome_email(app, member)
            else:
                app.logger.warning(
                    f"Webhook for successful payment received, but no member found for Stripe reference customer={customer_id} subscription={subscription_id}"
                )

        elif event_type == "customer.subscription.updated":
            subscription = event["data"]["object"]
            customer_id = subscription.get("customer")
            subscription_id = subscription.get("id")
            member = get_member_by_stripe_reference(customer_id=customer_id, subscription_id=subscription_id)
            if member:
                member.stripe_subscription_id = subscription_id
                member.cancel_at_period_end = bool(subscription.get("cancel_at_period_end"))
                if member.cancel_at_period_end and member_has_active_access(member):
                    member.payment_status = "cancel_scheduled"
                elif not member.cancel_at_period_end and member.payment_status == "cancel_scheduled":
                    member.payment_status = "paid" if member.is_active else member.payment_status
                db.session.commit()

        elif event_type in ["payment_intent.payment_failed", "invoice.payment_failed"]:
            data_object = event["data"]["object"]
            customer_id = data_object.get("customer")
            subscription_id = data_object.get("subscription")
            member = get_member_by_stripe_reference(customer_id=customer_id, subscription_id=subscription_id)
            if member:
                member.payment_status = "failed"
                if not member_has_active_access(member):
                    member.is_active = False
                db.session.commit()
                app.logger.warning(f"Payment failed for Stripe Customer ID: {customer_id}")
            else:
                app.logger.warning(
                    "Webhook for failed payment received, but no Stripe Customer ID was provided."
                )

        elif event_type == "customer.subscription.deleted":
            subscription = event["data"]["object"]
            customer_id = subscription.get("customer")
            subscription_id = subscription.get("id")
            member = get_member_by_stripe_reference(customer_id=customer_id, subscription_id=subscription_id)
            if member:
                member.stripe_subscription_id = subscription_id or member.stripe_subscription_id
                member.cancel_at_period_end = False
                cancellation_details = subscription.get("cancellation_details", {})
                reason = cancellation_details.get("reason")
                event_date = to_membership_date(event.get("created"))

                if reason == "payment_failed":
                    member.payment_status = "failed"
                    member.is_active = False
                    app.logger.warning(
                        f"Subscription for Stripe Customer ID: {customer_id} was canceled due to failed payment."
                    )
                else:
                    member.payment_status = "canceled"
                    member.is_active = member_has_active_access(member, event_date)
                    app.logger.info(
                        f"Subscription canceled for Stripe Customer ID: {customer_id}. Coverage remains valid until the covered year ends."
                    )

                if sync_member_active_state(member, event_date):
                    pass
                db.session.commit()
            else:
                app.logger.warning(
                    f"Webhook for subscription cancellation received, but no member found for Stripe reference customer={customer_id} subscription={subscription_id}"
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
                                f"DISPUTE LOST for Stripe Customer ID: {customer_id}. Member has been deactivated."
                            )
                except Exception as e:
                    app.logger.error(f"Error handling dispute for charge {charge_id}: {e}")

        return "Success", 200

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        flash(_("Your session has expired or the form is invalid. Please try submitting again."), "warning")
        return redirect(url_for("index"))

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
