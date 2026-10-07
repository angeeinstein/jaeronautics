"""The public addresses: the start page, joining, where Stripe comes back
to, the legal texts and their PDFs, and the health check. The pages are the
app's (blueprints/app_shell.py); the signup itself is api/signup.py.
"""

from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, jsonify, redirect, request, url_for
from flask_login import current_user
from sqlalchemy import text

from ..app import get_member_portal_target
from ..db_models import db
from ..services import legal_texts as legal
from . import _legal_pages as legal_pages
from .app_shell import app_shell

public_bp = Blueprint("public", __name__)


@public_bp.route("/", methods=["GET"])
def index():
    """The front door: what this is, and the way in -- joining or signing in.
    The app's page. Somebody signed in goes straight on to their start page."""
    if current_user.is_authenticated:
        return redirect(url_for(get_member_portal_target(current_user)))
    return app_shell()


@public_bp.route("/join", methods=["GET"])
def join():
    """The signup, at an address that can go on a poster: the app's page
    (POST /api/v1/signup). Signed in, a login without a membership starts one
    from My Account."""
    if current_user.is_authenticated:
        return redirect(url_for("account.account"))
    return app_shell()


@public_bp.route("/thank-you")
def thank_you():
    """Where Stripe's payment page -- or an invoice signup -- comes back to: the app's page."""
    return app_shell()


@public_bp.route("/cancel")
def cancel():
    """Where Stripe's payment page comes back to when cancelled: the app's page."""
    return app_shell()


@public_bp.route("/legal")
def legal_texts():
    """Every legal text in force: the app's page (GET /api/v1/legal)."""
    return app_shell()


@public_bp.route("/legal/<slug>")
@public_bp.route("/legal/<slug>/<language>")
@public_bp.route("/legal/<slug>/<language>/<version>")
def legal_text(slug, language=None, version=None):
    """One legal text: the app's page (GET /api/v1/legal/<slug>)."""
    if slug not in legal.BY_SLUG or language not in (None, *legal.LANGUAGES):
        abort(404)
    return app_shell()


@public_bp.route("/legal/<slug>/pdf")
@public_bp.route("/legal/<slug>/pdf/<version>")
def legal_text_pdf(slug, version=None):
    """A legal text as a PDF: the German version, then its English translation."""
    if slug not in legal.BY_SLUG:
        abort(404)
    return legal_pages.pdf(
        slug, version,
        back=url_for("public.legal_text", slug=slug, language=legal.AUTHORITATIVE, version=version),
    )


@public_bp.route("/__health", methods=["GET"])
def health_check():
    checks = {"app": "ok", "database": "ok"}
    status_code = 200
    try:
        db.session.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - exercised only when DB is down
        checks["database"] = "error"
        status_code = 503
        current_app.logger.error("Health check database probe failed: %s", exc)
    return (
        jsonify(
            {
                "status": "ok" if status_code == 200 else "degraded",
                "app": "jaeronautics",
                "checks": checks,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "host": request.host,
            }
        ),
        status_code,
    )
