"""My Account's addresses. The pages are the app's (api/account.py,
api/signup.py); the data download stays Flask's.
"""

from flask import Blueprint, redirect, url_for
from flask_login import current_user, login_required

from ..app import limiter
from ..config import RATELIMIT_DATA_EXPORT
from ..db_models import db
from ..services.audit import log_audit_event
from ..services.privacy import (
    export_account_data,
    export_filename_for,
)
from ._responses import json_download_response
from .app_shell import app_shell

account_bp = Blueprint("account", __name__)


@account_bp.route("/account", methods=["GET"])
@login_required
def account():
    """My Account, drawn by the app (api/account.py)."""
    return app_shell()


@account_bp.route("/account/create-membership", methods=["GET"])
@login_required
def create_membership_profile():
    """A membership for a login without one: the app's page
    (POST /api/v1/account/membership). With one already, My Account."""
    if current_user.member is not None:
        return redirect(url_for("account.account"))
    return app_shell()


@account_bp.route("/account/data-export", methods=["GET"])
@login_required
@limiter.limit(RATELIMIT_DATA_EXPORT)
def export_my_data():
    """Download everything the association holds about you, as JSON.

    GDPR Art. 15 and 20. Self-service because the alternative -- emailing an
    administrator who then assembles it by hand -- is slower and reliably
    incomplete.
    """
    payload = export_account_data(current_user)

    log_audit_event(
        category="privacy",
        event_type="data_exported",
        actor_user=current_user,
        target_user=current_user,
        target_member=current_user.member,
        metadata={"self_service": True, "sections": sorted(payload)},
    )
    db.session.commit()

    return json_download_response(payload, export_filename_for(current_user))


@account_bp.route("/account/delete/<token>", methods=["GET"])
@login_required
def confirm_account_deletion(token):
    """The link in the deletion email: the app's page says what deleting does,
    and its button does it (api/account.py). Opening it changes nothing --
    mail clients and link scanners open links without being asked."""
    return app_shell()
