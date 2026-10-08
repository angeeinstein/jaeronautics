"""My Account's addresses. The pages are the app's (api/account.py,
api/signup.py); the data download stays Flask's.
"""

from flask import Blueprint, Response, abort, redirect, url_for
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


@account_bp.route("/account/profile", methods=["GET"])
@account_bp.route("/account/membership", methods=["GET"])
@account_bp.route("/account/forum", methods=["GET"])
@account_bp.route("/account/data", methods=["GET"])
@login_required
def account_section():
    """A part of My Account, in its side menu: drawn by the app (api/account.py)."""
    return app_shell()


@account_bp.route("/account/credit", methods=["GET"])
@login_required
def credit_page():
    """Credit: the balance, topping up, the history (api/account_credit.py).
    Stripe Checkout comes back here."""
    return app_shell()


@account_bp.route("/account/credit/receipt/<int:entry_id>", methods=["GET"])
@login_required
def credit_receipt(entry_id):
    """Stripe's receipt for one of your top-ups."""
    from ..services import NotFoundError, credit

    try:
        url = credit.receipt_url(credit.entry_of(current_user, entry_id))
    except NotFoundError:
        url = None
    if not url:
        abort(404)
    return redirect(url)


@account_bp.route("/account/credit/history.csv", methods=["GET"])
@login_required
def credit_history_csv():
    """Your credit's history as a spreadsheet, oldest first."""
    import csv
    import io

    from ..services import credit
    from ..services.clock import get_membership_today

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(credit.EXPORT_COLUMNS)
    writer.writerows(credit.export_rows(current_user))
    return Response(
        "\ufeff" + buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="credit-{get_membership_today().isoformat()}.csv"'},
    )


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
