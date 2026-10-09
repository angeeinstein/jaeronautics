"""Process configuration, read once from the environment.

This is deliberately a leaf module: it imports nothing from the application, so
every other module -- services, blueprints, the app factory -- can read
configuration without importing ``app``. Keeping these values here is what lets
the service layer sit *below* the routes instead of beside them.

Values are read at import time, matching the previous behaviour. ``create_app``
still layers per-app overrides on top (see ``config_overrides``), so tests can
point the application at a different database without touching this module.
"""

import os
from datetime import timezone
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

try:
    from .security_utils import normalize_public_base_url
except ImportError:  # pragma: no cover - direct-script fallback, as elsewhere
    from security_utils import normalize_public_base_url

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
# English only, whatever LANGUAGES says in an older .env. The German
# translation has not kept up with the portal, so offering it -- or letting a
# German browser pick it -- showed a half-translated site. Add "de" back here
# once the translation is brought up to date.
LANGUAGES = ["en"]
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

# The footer on every page. The Impressum, privacy policy and statutes lead to
# this portal's own legal texts (legal/); IMPRESSUM_URL, PRIVACY_URL and
# STATUTES_URL send them somewhere else instead -- the website, say.
ASSOCIATION_WEBSITE_URL = os.getenv("ASSOCIATION_WEBSITE_URL", "https://joanneum-aeronautics.at")
IMPRESSUM_URL = os.getenv("IMPRESSUM_URL") or None
PRIVACY_URL = os.getenv("PRIVACY_URL") or None
STATUTES_URL = os.getenv("STATUTES_URL") or None
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "office@joanneum-aeronautics.at")
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
# Two limits guard each form that takes a password or an address. The tight
# one counts attempts for one email address from one network: it is what stops
# somebody guessing a password. The loose one counts everything from that
# network, whatever the address: it only stops a flood. A lecture hall behind
# one campus address shares that second budget, so it is set for an intake
# evening, not for one person: 250 students on the university Wi-Fi, each
# logging in more than once and resubmitting a form that came back with a
# mistake, all counted against one address. Raised from 300 for that; the
# per-address limits above are what stop guessing, and Cloudflare is in front.
RATELIMIT_LOGIN = os.getenv("RATELIMIT_LOGIN", "10 per 15 minute")
RATELIMIT_LOGIN_PER_IP = os.getenv("RATELIMIT_LOGIN_PER_IP", "1000 per 15 minute")
# The "forgot password" form (the name is historical).
RATELIMIT_REGISTER = os.getenv("RATELIMIT_REGISTER", "5 per hour")
RATELIMIT_REGISTER_PER_IP = os.getenv("RATELIMIT_REGISTER_PER_IP", "1000 per hour")
# The signup form. Per address too: an address that already has an account is
# signed in by it when the password matches, so it is also a password check.
RATELIMIT_MEMBERSHIP = os.getenv("RATELIMIT_MEMBERSHIP", "10 per 15 minute")
RATELIMIT_MEMBERSHIP_PER_IP = os.getenv("RATELIMIT_MEMBERSHIP_PER_IP", "1000 per hour")
# Changing a password (signed in: counted per account) and setting a new one
# from a reset link (counted per link).
RATELIMIT_PASSWORD_CHANGE = os.getenv("RATELIMIT_PASSWORD_CHANGE", "5 per 15 minute")
RATELIMIT_ADMIN_EMAIL = os.getenv("RATELIMIT_ADMIN_EMAIL", "5 per 10 minute")
# Data exports assemble a member's whole record, so they are cheap to request
# and expensive to serve; deletion requests send an email each time.
RATELIMIT_DATA_EXPORT = os.getenv("RATELIMIT_DATA_EXPORT", "10 per hour")
RATELIMIT_ACCOUNT_DELETION = os.getenv("RATELIMIT_ACCOUNT_DELETION", "5 per hour")
# "Send the confirmation again": a cap on top of the per-browser minute between
# two of them (blueprints/_email_cooldown.py).
RATELIMIT_EMAIL_RESEND = os.getenv("RATELIMIT_EMAIL_RESEND", "10 per hour")
# Going to the forum through the portal, per account. Every click asks the
# forum to bring the account up to date, and retries anything still waiting
# for this person, so it is the one button that makes the forum do work.
# A person signs in to the forum a few times a day; this only stops a loop.
RATELIMIT_FORUM_CONNECT = os.getenv("RATELIMIT_FORUM_CONNECT", "20 per 10 minute")
# The contact form and messages to a team's leads (services/messages.py):
# somebody signed in counts as themselves, a visitor as their address; on top,
# a looser cap per address so a lecture hall can still write.
RATELIMIT_CONTACT = os.getenv("RATELIMIT_CONTACT", "5 per hour")
RATELIMIT_CONTACT_PER_IP = os.getenv("RATELIMIT_CONTACT_PER_IP", "30 per hour")
# A page in use saying so (services/presence.py): about twice a minute per open
# tab, so this only stops a flood. Somebody signed in counts as themselves; a
# lecture hall of visitors behind one campus address shares it.
RATELIMIT_PRESENCE = os.getenv("RATELIMIT_PRESENCE", "600 per 10 minute")
MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", str(20 * 1024 * 1024)))
# A test server says so: a red bar under the header on every page and in
# every email, and "[TEST]" before each email's subject and the tab title.
TEST_SERVER = os.getenv("TEST_SERVER", "").strip().lower() in {"1", "true", "yes", "on"}

# Domains that prove somebody is currently a student, and those that prove
# somebody works at the institute. An admin can edit both lists at runtime;
# these are only the fallbacks for a fresh install, and the reason they are
# lists at all is that a renamed domain must not need a code change.
DEFAULT_INSTITUTIONAL_EMAIL_DOMAINS = os.getenv("INSTITUTIONAL_EMAIL_DOMAINS", "edu.fh-joanneum.at")
DEFAULT_STAFF_EMAIL_DOMAINS = os.getenv("STAFF_EMAIL_DOMAINS", "fh-joanneum.at")

SENSITIVE_SETTING_KEYS = {"stripe_secret_key", "stripe_webhook_secret", "discourse_api_key", "discourse_connect_secret"}
SENSITIVE_AUDIT_FIELD_NAMES = SENSITIVE_SETTING_KEYS | {"password", "pass", "secret", "smtp_password", "export_password"}
