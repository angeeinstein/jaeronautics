import os
import sys
from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path
from subprocess import run
from urllib.parse import quote_plus

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
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from flask_wtf.csrf import CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix

try:
    from .db_models import db, Member, Setting, User
    from .forms import (
        ChangePasswordForm,
        LoginForm,
        MembershipForm,
        RegistrationForm,
        TestEmailForm,
    )
    from .mail_utils import send_mail
except ImportError:
    from db_models import db, Member, Setting, User
    from forms import (
        ChangePasswordForm,
        LoginForm,
        MembershipForm,
        RegistrationForm,
        TestEmailForm,
    )
    from mail_utils import send_mail

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

babel = Babel()
login_manager = LoginManager()


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

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1, x_proto=1)
    stripe.api_key = STRIPE_SECRET_KEY

    @app.cli.command("db-init")
    @with_appcontext
    def db_init():
        """Creates database tables if they do not exist."""
        click.echo("Creating database tables...")
        try:
            db.create_all()
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
    def process_membership():
        form = MembershipForm()
        settings = {s.key: s.value for s in Setting.query.all()}

        if form.validate_on_submit():
            form_data = form.data
            form_data.pop("csrf_token", None)
            form_data.pop("submit", None)

            payment_method = form_data.pop("payment_method", "checkout")

            if settings.get("invoice_payments_enabled") != "True":
                payment_method = "checkout"

            try:
                if payment_method == "checkout":
                    import json

                    member_data_json = json.dumps(form_data)

                    session = stripe.checkout.Session.create(
                        payment_method_types=["card", "sepa_debit"],
                        line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
                        mode="subscription",
                        metadata={"member_data": member_data_json},
                        customer_email=form_data["email_private"],
                        success_url=url_for("thank_you", _external=True, method="checkout"),
                        cancel_url=url_for("cancel", _external=True),
                    )
                    return redirect(session.url, code=303)

                if payment_method == "invoice":
                    existing_member = Member.query.filter_by(email_private=form_data["email_private"]).first()
                    if existing_member and existing_member.is_active:
                        flash(_("An active membership already exists for this email address."), "warning")
                        return redirect(url_for("index"))

                    customer = stripe.Customer.create(
                        email=form_data["email_private"],
                        name=f"{form_data['first_name']} {form_data['last_name']}",
                    )

                    stripe.Subscription.create(
                        customer=customer.id,
                        items=[{"price": STRIPE_PRICE_ID}],
                        collection_method="send_invoice",
                        days_until_due=30,
                    )

                    if existing_member:
                        existing_member.stripe_customer_id = customer.id
                        existing_member.payment_status = "unpaid"
                        db.session.commit()
                    else:
                        form_data["stripe_customer_id"] = customer.id
                        new_member = Member(**form_data)
                        db.session.add(new_member)
                        db.session.commit()

                    return redirect(url_for("thank_you", method="invoice"))

            except stripe.StripeError as e:
                app.logger.error(f"Stripe Error: {str(e)}")
                flash(_("Error processing payment. Please try again."), "danger")
            except Exception as e:
                app.logger.error(f"An unexpected error occurred: {str(e)}")
                flash(_("An unexpected error occurred. Please try again."), "danger")

            return redirect(url_for("index"))

        app.logger.warning(f"Form validation failed. Errors: {form.errors}")
        flash(_("Please correct the errors below and try again."), "danger")
        return render_template("index.html", form=form)

    @app.route("/thank-you")
    def thank_you():
        method = request.args.get("method", "checkout")
        return render_template("thank_you.html", method=method)

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

        sender_choices = []
        template_choices = []
        try:
            import json

            mail_accounts_json = os.getenv("MAIL_ACCOUNTS_JSON", "{}")
            mail_accounts = json.loads(mail_accounts_json)
            sender_choices = [(acc, acc) for acc in mail_accounts.keys()]

            email_template_dir = os.path.join(app.root_path, "templates", "emails")
            if os.path.isdir(email_template_dir):
                template_choices = [(f, f) for f in os.listdir(email_template_dir) if f.endswith(".html")]
        except Exception as e:
            app.logger.error(f"Could not load email accounts or templates for admin page: {e}")

        test_email_form.sender.choices = sender_choices
        test_email_form.template.choices = template_choices

        if request.method == "POST" and "save_settings" in request.form:
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

            welcome_sender = request.form.get("welcome_email_sender")
            if welcome_sender:
                setting = Setting.query.get("welcome_email_sender")
                if setting:
                    setting.value = welcome_sender
                else:
                    setting = Setting(key="welcome_email_sender", value=welcome_sender)
                    db.session.add(setting)

            auto_email_template = request.form.get("automatic_email_template")
            if auto_email_template:
                setting = Setting.query.get("automatic_email_template")
                if setting:
                    setting.value = auto_email_template
                else:
                    setting = Setting(key="automatic_email_template", value=auto_email_template)
                    db.session.add(setting)

            db.session.commit()
            flash(_("Settings updated successfully!"), "success")
            return redirect(url_for("admin"))

        return render_template(
            "admin/index.html",
            test_email_form=test_email_form,
            sender_choices=sender_choices,
            template_choices=template_choices,
        )

    @app.route("/send-test-email", methods=["POST"])
    @login_required
    @admin_required
    def send_test_email():
        form = TestEmailForm()

        try:
            import json

            mail_accounts_json = os.getenv("MAIL_ACCOUNTS_JSON", "{}")
            mail_accounts = json.loads(mail_accounts_json)
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
    def register():
        if current_user.is_authenticated:
            if current_user.role == "admin":
                return redirect(url_for("admin"))
            return redirect(url_for("index"))
        form = RegistrationForm()
        if form.validate_on_submit():
            user = User(email=form.email.data)
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.commit()
            flash(_("Congratulations, you are now a registered user!"), "success")
            return redirect(url_for("login"))
        return render_template("admin/register.html", form=form)

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("index"))

    @app.route("/change-password", methods=["GET", "POST"])
    @login_required
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

        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            member_data_json = session.get("metadata", {}).get("member_data")
            if not member_data_json:
                app.logger.error("Webhook received without member_data metadata.")
                return "Missing metadata", 400

            try:
                import json

                member_data = json.loads(member_data_json)
                email = member_data.get("email_private")
                customer_id = session.get("customer")

                if not email or not customer_id:
                    app.logger.error("Webhook missing email or customer ID in session.")
                    return "Missing data", 400

                existing_member = Member.query.filter_by(email_private=email).first()

                if existing_member:
                    if not existing_member.is_active:
                        existing_member.stripe_customer_id = customer_id
                        existing_member.payment_status = "unpaid"
                        db.session.commit()
                        app.logger.info(
                            f"Re-subscription initiated for {email}. Awaiting payment confirmation."
                        )
                    else:
                        app.logger.warning(
                            f"Duplicate subscription attempt for active member: {email}. No action taken."
                        )
                    return "Handling existing member", 200

                db_fields = {
                    "salutation",
                    "first_name",
                    "last_name",
                    "street",
                    "house_number",
                    "postal_code",
                    "city",
                    "country",
                    "phone_private",
                    "email_private",
                    "year_group",
                    "title",
                    "phone_work",
                    "email_work",
                }

                data_to_save = {}
                for field in db_fields:
                    value = member_data.get(field)
                    if value == "" and field in ["title", "phone_work", "email_work"]:
                        data_to_save[field] = None
                    else:
                        data_to_save[field] = value

                data_to_save["terms_accepted"] = True
                data_to_save["created_at"] = datetime.now(timezone.utc)

                customer_id = session.get("customer")
                if customer_id and isinstance(customer_id, str) and customer_id.startswith("cus_"):
                    data_to_save["stripe_customer_id"] = customer_id
                else:
                    app.logger.warning(
                        f"Webhook for session {session.get('id')} received with a missing or invalid Stripe Customer ID: {customer_id}"
                    )
                    data_to_save["stripe_customer_id"] = None

                new_member = Member(**data_to_save)
                db.session.add(new_member)
                db.session.commit()

                app.logger.info(
                    f"SUCCESS: Member created with unpaid status for email: {new_member.email_private}. Session ID: {session['id']}"
                )

            except Exception as e:
                app.logger.error(f"FATAL DB ERROR on Webhook for session {session['id']}: {e}")
                db.session.rollback()
                return "Database save failed", 500

        elif event["type"] == "payment_intent.processing":
            payment_intent = event["data"]["object"]
            customer_id = payment_intent.get("customer")
            if customer_id:
                member = Member.query.filter_by(stripe_customer_id=customer_id).first()
                if member:
                    member.payment_status = "processing"
                    db.session.commit()
                    app.logger.info(f"Payment is processing for Stripe Customer ID: {customer_id}")

        elif event["type"] in ["payment_intent.succeeded", "invoice.paid", "invoice.payment_succeeded"]:
            data_object = event["data"]["object"]
            customer_id = data_object.get("customer")
            if customer_id:
                member = Member.query.filter_by(stripe_customer_id=customer_id).first()
                if member:
                    if not member.is_active:
                        member.payment_status = "paid"
                        member.is_active = True
                        db.session.commit()
                        app.logger.info(
                            f"SUCCESS: Payment confirmed and member activated for Stripe Customer ID: {customer_id}"
                        )

                        settings = {s.key: s.value for s in Setting.query.all()}
                        if settings.get("automatic_emails_enabled") == "True":
                            sender_account = settings.get("welcome_email_sender", "office")
                            template_name = settings.get("automatic_email_template", "welcome_email.html")

                            suggested_username = generate_suggested_username(member)

                            logo_path = os.path.join(
                                app.root_path,
                                "static",
                                "Logo_Aeronautics_signature-logo.png",
                            )
                            attachments = [{"path": logo_path, "cid": "logo"}]

                            send_mail(
                                from_account=sender_account,
                                to_email=member.email_private,
                                subject=_("Welcome to Joanneum Aeronautics!"),
                                template_name=template_name,
                                attachments=attachments,
                                first_name=member.first_name,
                                suggested_username=suggested_username,
                                now=datetime.now(timezone.utc),
                            )
                    else:
                        app.logger.info(
                            f"Webhook received for already active member: {customer_id}. No action taken."
                        )
                else:
                    app.logger.warning(
                        f"Webhook for successful payment received, but no member found for Stripe Customer ID: {customer_id}"
                    )
            else:
                app.logger.warning(
                    "Webhook for successful payment received, but no Stripe Customer ID was provided."
                )

        elif event["type"] in ["payment_intent.payment_failed", "invoice.payment_failed"]:
            data_object = event["data"]["object"]
            customer_id = data_object.get("customer")
            if customer_id:
                member = Member.query.filter_by(stripe_customer_id=customer_id).first()
                if member:
                    member.payment_status = "failed"
                    member.is_active = False
                    db.session.commit()
                    app.logger.warning(f"Payment failed for Stripe Customer ID: {customer_id}")
            else:
                app.logger.warning(
                    "Webhook for failed payment received, but no Stripe Customer ID was provided."
                )

        elif event["type"] == "customer.subscription.deleted":
            subscription = event["data"]["object"]
            customer_id = subscription.get("customer")
            if customer_id:
                member = Member.query.filter_by(stripe_customer_id=customer_id).first()
                if member:
                    member.is_active = False
                    cancellation_details = subscription.get("cancellation_details", {})
                    reason = cancellation_details.get("reason")

                    if reason == "payment_failed":
                        member.payment_status = "failed"
                        db.session.commit()
                        app.logger.warning(
                            f"Subscription for Stripe Customer ID: {customer_id} was canceled due to failed payment. Member deactivated."
                        )
                    else:
                        member.payment_status = "canceled"
                        db.session.commit()
                        app.logger.info(
                            f"Subscription canceled for Stripe Customer ID: {customer_id}. Member deactivated."
                        )
                else:
                    app.logger.warning(
                        f"Webhook for subscription cancellation received, but no member found for Stripe Customer ID: {customer_id}"
                    )

        elif event["type"] == "charge.dispute.closed":
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
            with application.test_request_context():
                settings = {s.key: s.value for s in Setting.query.all()}
                sender_account = settings.get("welcome_email_sender")
                template_name = settings.get("automatic_email_template")

                if not sender_account or not template_name:
                    click.echo(
                        click.style(
                            "Error: Email sender or template is not configured in the admin settings.",
                            fg="red",
                        )
                    )
                    return

                suggested_username = generate_suggested_username(member)
                logo_path = os.path.join(
                    application.root_path,
                    "static",
                    "Logo_Aeronautics_signature-logo.png",
                )
                attachments = [{"path": logo_path, "cid": "logo"}]

                success = send_mail(
                    from_account=sender_account,
                    to_email=member.email_private,
                    subject=_("Welcome to Joanneum Aeronautics!"),
                    template_name=template_name,
                    attachments=attachments,
                    first_name=member.first_name,
                    suggested_username=suggested_username,
                    now=datetime.now(timezone.utc),
                )

                if success:
                    click.echo(click.style(f"Successfully sent welcome email to {email}.", fg="green"))
                else:
                    click.echo(
                        click.style(
                            f"Failed to send welcome email to {email}. Check logs for details.",
                            fg="red",
                        )
                    )

        except Exception as e:
            click.echo(click.style(f"An unexpected error occurred: {e}", fg="red"))

    return app


application = create_app()

if __name__ == "__main__":
    application.run(debug=False)
