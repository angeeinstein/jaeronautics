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
RATELIMIT_REGISTER = os.getenv("RATELIMIT_REGISTER", "20 per hour")
# Keyed by IP, so a whole lecture hall on one campus NAT shares a single
# budget. Ten an hour is easily reached by legitimate members at a signup
# drive; this is generous enough for that while still bounding abuse.
RATELIMIT_MEMBERSHIP = os.getenv("RATELIMIT_MEMBERSHIP", "40 per hour")
RATELIMIT_PASSWORD_CHANGE = os.getenv("RATELIMIT_PASSWORD_CHANGE", "5 per 15 minute")
RATELIMIT_ADMIN_EMAIL = os.getenv("RATELIMIT_ADMIN_EMAIL", "5 per 10 minute")
# Data exports assemble a member's whole record, so they are cheap to request
# and expensive to serve; deletion requests send an email each time.
RATELIMIT_DATA_EXPORT = os.getenv("RATELIMIT_DATA_EXPORT", "10 per hour")
RATELIMIT_ACCOUNT_DELETION = os.getenv("RATELIMIT_ACCOUNT_DELETION", "5 per hour")
MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", str(20 * 1024 * 1024)))

SENSITIVE_SETTING_KEYS = {"stripe_secret_key", "stripe_webhook_secret", "discourse_api_key", "discourse_connect_secret"}
SENSITIVE_AUDIT_FIELD_NAMES = SENSITIVE_SETTING_KEYS | {"password", "pass", "secret", "smtp_password", "export_password"}
