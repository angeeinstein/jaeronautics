"""The admin area's addresses. The pages are the app's (api/admin_*.py): these
routes keep their permission checks and hand out the app (blueprints/app_shell.py).
Flask still serves the files: an account's data export, an archived forum
picture, the legal texts' waiting-versions PDF and Markdown template, backup
files, and the update's progress for the admin page.
"""

from pathlib import Path

from flask import Blueprint, current_app, jsonify

from ..permissions import (
    Permission,
)
from ..services.audit import (
    log_audit_event,
)
from ..services.privacy import (
    export_account_data,
    export_filename_for,
)
from . import _legal_pages as legal_pages
from .app_shell import app_shell
from ._responses import json_download_response
from ..services import backup as backup_service
from ..services import reviews
from ..services.system_update import (
    describe_update_state,
)
from flask import (
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_babel import (
    _,
)
from flask_login import (
    current_user,
    login_required,
)
from sqlalchemy.orm import (
    selectinload,
)
from ..db_models import (
    ImportedForumProfile,
    User,
    db,
)
from ..app import (
    requires,
)

admin_bp = Blueprint("admin", __name__)



@admin_bp.route("/admin", methods=["GET"])
@login_required
@requires(Permission.ADMIN_ACCESS)
def admin_dashboard():
    """The dashboard, drawn by the new front end (frontend/src/pages/admin/AdminDashboard.tsx)."""
    return app_shell()


@admin_bp.route("/admin/accounts", methods=["GET"])
@login_required
@requires(Permission.ACCOUNTS_VIEW)
def admin_accounts():
    """The account list, drawn by the new front end (frontend/src/pages/admin/Accounts.tsx)."""
    return app_shell()


@admin_bp.route("/admin/accounts/<int:user_id>/archived-avatar", methods=["GET"])
@requires(Permission.ACCOUNTS_VIEW)
def admin_archived_avatar(user_id):
    """Serves the avatar an imported person had on the old forum.

    Admin-only and served through the application rather than from a static
    directory: the staging directory holds files for pending avatar reviews as
    well, and nothing there should be reachable by guessing a filename.
    """
    profile = db.session.execute(
        db.select(ImportedForumProfile).filter_by(user_id=user_id)
    ).scalar_one_or_none()
    if profile is None or not profile.avatar_path:
        abort(404)

    path = Path(profile.avatar_path)
    if not path.exists():
        abort(404)
    return send_file(path, conditional=True)


@admin_bp.route("/admin/accounts/<int:user_id>", methods=["GET"])
@login_required
@requires(Permission.ACCOUNTS_VIEW)
def admin_account_detail(user_id):
    """One account, drawn by the new front end (frontend/src/pages/admin/Account.tsx)."""
    return app_shell()


@admin_bp.route("/admin/credit", methods=["GET"])
@admin_bp.route("/admin/credit/<int:user_id>", methods=["GET"])
@login_required
@requires(Permission.CREDIT_MANAGE)
def admin_credit(user_id=None):
    """Everybody's credit, and one person's (frontend/src/pages/admin/credit/)."""
    return app_shell()


@admin_bp.route("/admin/credit/receipt/<int:entry_id>", methods=["GET"])
@login_required
@requires(Permission.CREDIT_MANAGE)
def credit_receipt(entry_id):
    """Stripe's receipt for somebody's top-up."""
    from ..services import NotFoundError, credit

    try:
        url = credit.receipt_url(credit.entry_of(None, entry_id))
    except NotFoundError:
        url = None
    if not url:
        abort(404)
    return redirect(url)


@admin_bp.route("/admin/credit/export.csv", methods=["GET"])
@login_required
@requires(Permission.CREDIT_MANAGE)
def credit_export():
    """Every entry of everybody's credit, oldest first; accounts by number."""
    import csv
    import io

    from ..services import credit
    from ..services.clock import get_membership_today

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(credit.EXPORT_COLUMNS_ALL)
    writer.writerows(credit.export_rows_all())
    log_audit_event("credit", "credit_exported", actor_user=current_user)
    db.session.commit()
    return Response(
        "\ufeff" + buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="credit-{get_membership_today().isoformat()}.csv"'},
    )


@admin_bp.route("/admin/reviews", methods=["GET"])
@login_required
@requires(Permission.ADMIN_ACCESS)
def admin_reviews():
    """Reviews, drawn by the new front end (frontend/src/pages/admin/Reviews.tsx).

    For anybody who decides change requests or pictures; somebody in the
    admin area who decides neither goes back to the dashboard.
    """
    if not reviews.can_review_anything(current_user):
        return redirect(url_for("admin.admin_dashboard"))
    return app_shell()


@admin_bp.route("/admin/settings", methods=["GET"])
@login_required
@requires(Permission.SETTINGS_GENERAL)
def admin_settings():
    """Settings: the app opens its first section -- or, for an old link to a tab
    of the page this used to be (``#settings-forum``), that section's page."""
    return app_shell()


#: A section of the settings, and what it needs beyond the general settings permission.
SETTINGS_SECTIONS = {
    "general": None, "notifications": None, "credit": None, "billing": Permission.SETTINGS_CREDENTIALS,
    "forum": Permission.SETTINGS_CREDENTIALS, "mail": Permission.SETTINGS_CREDENTIALS,
    "test-email": Permission.NOTIFICATIONS_MANAGE,
    "health": Permission.SYSTEM_UPDATE, "updates": Permission.SYSTEM_UPDATE, "backup": Permission.SYSTEM_BACKUP,
}


@admin_bp.route("/admin/settings/<any(general, notifications, billing, credit, forum, mail, 'test-email', health, updates, backup)"
                ":section>", methods=["GET"])
@login_required
@requires(Permission.SETTINGS_GENERAL)
def admin_settings_section(section):
    """A section of the settings: drawn by the new front end (frontend/src/pages/admin/settings/)."""
    needed = SETTINGS_SECTIONS[section]
    if needed is not None and not current_user.can(needed):
        return redirect(url_for("admin.admin_settings_section", section="general"))
    return app_shell()


@admin_bp.route("/admin/legal", methods=["GET"])
@login_required
@requires(Permission.SETTINGS_GENERAL)
def admin_legal():
    """Legal texts: drawn by the new front end (frontend/src/pages/admin/LegalTexts.tsx)."""
    return app_shell()


@admin_bp.route("/admin/legal/waiting.pdf", methods=["GET"])
@login_required
@requires(Permission.SETTINGS_GENERAL)
def admin_legal_waiting_pdf():
    """A version in legal/ not shown yet -- a draft, or one whose day has not
    come -- as its PDF, marked ENTWURF or VORSCHAU on every page."""
    import io
    from datetime import date

    from .. import legal_pdf
    from ..services import NotFoundError
    from ..services import legal_texts as legal
    from ..services import teams as teams_service

    document = request.args.get("document", "")
    team_slug = request.args.get("team") or None
    if document not in (legal.TEAM_BY_SLUG if team_slug else legal.BY_SLUG):
        abort(404)
    try:
        german = legal.find_any(document, legal.AUTHORITATIVE, date.fromisoformat(request.args.get("version", "")),
                                team=team_slug)
    except ValueError:
        abort(404)
    if german is None:
        abort(404)
    team = None
    if team_slug:
        try:
            team = teams_service.get_team(team_slug)
        except NotFoundError:
            team = None
    try:
        path, digest, name = legal_pdf.waiting_ready(german, team)
        data = None if legal_pages.preparing() else path.read_bytes()
    except Exception:  # noqa: BLE001
        current_app.logger.exception("Could not make the PDF of the waiting version %s", german.path)
        if legal_pages.preparing():
            return legal_pages.not_prepared()
        abort(500)
    if data is None:
        return legal_pages.prepared(digest)
    return send_file(io.BytesIO(data), mimetype="application/pdf", download_name=name)


@admin_bp.route("/admin/legal/template", methods=["GET"])
@login_required
@requires(Permission.SETTINGS_GENERAL)
def admin_legal_template():
    """A Markdown file showing what a legal text needs and everything it can do,
    dated today and a draft -- so it previews as it is."""
    from ..services.clock import get_membership_today

    day = get_membership_today().isoformat()
    response = current_app.response_class(
        render_template("legal/template.md", day=day), mimetype="text/markdown",
    )
    response.headers["Content-Disposition"] = f'attachment; filename="{day}.md"'
    return response


@admin_bp.route("/admin/logs", methods=["GET"])
@login_required
@requires(Permission.LOGS_VIEW)
def admin_logs():
    """The log: drawn by the new front end (frontend/src/pages/admin/Logs.tsx)."""
    return app_shell()


@admin_bp.route("/admin/system-update/status", methods=["GET"])
@login_required
@requires(Permission.SYSTEM_UPDATE)
def admin_system_update_status():
    """Current and available version, as JSON -- for a Maintenance page opened
    before the update that brought the new front end: it asks here until the
    update is done, then loads itself again. The new page asks
    /api/v1/admin/settings/updates."""
    force = request.args.get("refresh") == "1"
    return jsonify(describe_update_state(force_remote_check=force))


@admin_bp.route("/admin/accounts/<int:user_id>/data-export", methods=["GET"])
@login_required
@requires(Permission.ACCOUNTS_PRIVACY)
def admin_export_account_data(user_id):
    """Download everything held about one account, as JSON.

    Members can do this themselves, but an administrator handling a request that
    arrived by post or email needs the same file, and answering it by hand from
    the database is how a subject access request ends up incomplete.
    """
    user = db.session.execute(
        db.select(User).options(selectinload(User.member), selectinload(User.roles)).filter_by(id=user_id)
    ).scalar_one_or_none()
    if user is None:
        flash(_("The selected account could not be found."), "warning")
        return redirect(url_for("admin.admin_accounts"))

    payload = export_account_data(user)

    # Handing over someone's personal data is itself worth recording -- and the
    # entry names the file, not its contents.
    log_audit_event(
        category="privacy",
        event_type="data_exported",
        actor_user=current_user,
        target_user=user,
        target_member=user.member,
        metadata={"sections": sorted(payload)},
    )
    db.session.commit()

    return json_download_response(payload, export_filename_for(user))


# ---- Backup and restore: the page is the app's (api/admin_backup.py) -------------------


@admin_bp.route("/admin/backup/files/<name>", methods=["GET"])
@login_required
@requires(Permission.SYSTEM_BACKUP)
def admin_download_backup(name):
    path = backup_service.backup_path(name)
    if path is None:
        abort(404)
    log_audit_event(category="system", event_type="backup_downloaded", actor_user=current_user,
                    target_user=current_user, metadata={"file": name})
    db.session.commit()
    return send_file(path, as_attachment=True, download_name=name, mimetype="application/octet-stream")


