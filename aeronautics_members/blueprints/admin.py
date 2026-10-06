"""Admin blueprint.

Route handlers moved verbatim out of app.py (dedented; @app.route ->
@admin_bp.route; app.logger -> current_app.logger). Helpers are imported
from the app module, which is fully initialized before this is imported.
"""

from pathlib import Path

from flask import Blueprint, current_app, jsonify

from ..permissions import (
    Permission,
)
from ..config import (
    RATELIMIT_ADMIN_EMAIL,
)
from ..services.audit import (
    log_audit_event,
    snapshot_mail_account_for_audit,
)
from ..services.privacy import (
    export_account_data,
    export_filename_for,
)
from . import _legal_pages as legal_pages
from .app_shell import app_shell
from ._responses import json_download_response
from ..services.notifications import (
    build_mail_accounts_export_payload,
    dismiss_email_delivery_job,
    normalize_imported_mail_accounts_payload,
    requeue_email_delivery_job,
    sample_email_for,
)
from ..services import (
    ServiceError,
)
from ..services import backup as backup_service
from ..services import background_jobs, outbox, resume_checks, reviews
from ..services.system_update import (
    describe_update_state,
    request_update,
)
import json
import os
from datetime import (
    datetime,
    timezone,
)
from flask import (
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
    ngettext,
)
from flask_login import (
    current_user,
    login_required,
)
from sqlalchemy.exc import (
    IntegrityError,
)
from sqlalchemy.orm import (
    selectinload,
)
from ..db_models import (
    EmailDeliveryJob,
    ImportedForumProfile,
    MailAccount,
    Setting,
    User,
    db,
)
from ..forms import (
    MailAccountForm,
    TestEmailForm,
)
from ..mail_utils import (
    load_mail_accounts_config,
    probe_mail_account_connection,
    send_mail,
)
from ..app import (
    build_settings_page_context,
    limiter,
    requires,
)

admin_bp = Blueprint("admin", __name__)

# Settings tabs holding third-party credentials. The page itself only needs
# SETTINGS_GENERAL, so these sections carry their own check; kept beside the
# route that enforces it so the list and the check cannot drift apart.
CREDENTIAL_SETTINGS_SECTIONS = {"billing", "forum"}


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
    """The settings not moved to the new front end yet: mail accounts, the test
    email and maintenance (docs/frontend-routes.md, 4.8)."""
    edit_mail_account_id = request.args.get("edit_mail_account", type=int)
    context = build_settings_page_context(edit_mail_account_id=edit_mail_account_id)
    if edit_mail_account_id and context["editing_mail_account"] is None:
        flash(_("The selected mail account could not be found."), "warning")
        return redirect(url_for("admin.admin_settings"))
    return render_template("admin_settings.html", active_admin_section="settings", **context)


@admin_bp.route("/admin/settings/<any(general, notifications, billing, forum):section>", methods=["GET"])
@login_required
@requires(Permission.SETTINGS_GENERAL)
def admin_settings_section(section):
    """A section of the settings: drawn by the new front end (frontend/src/pages/admin/settings/)."""
    if section in CREDENTIAL_SETTINGS_SECTIONS and not current_user.can(Permission.SETTINGS_CREDENTIALS):
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


@admin_bp.route("/admin/settings/mail-accounts", methods=["POST"])
@login_required
@requires(Permission.SETTINGS_CREDENTIALS)
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
        return redirect(url_for("admin.admin_settings", **redirect_kwargs))

    account_key = form.account_key.data.strip()
    existing_account = db.session.execute(
        db.select(MailAccount).filter_by(account_key=account_key)
    ).scalar_one_or_none()

    if existing_account is not None and existing_account.id != account_id:
        flash(_("A mail account with this key already exists."), "danger")
        target_id = account_id or existing_account.id
        return redirect(url_for("admin.admin_settings", edit_mail_account=target_id))

    if account_id:
        mail_account = db.session.get(MailAccount, account_id)
        if mail_account is None:
            flash(_("The selected mail account could not be found."), "warning")
            return redirect(f"{url_for('admin.admin_settings')}#settings-mail")
    else:
        if not form.password.data:
            flash(_("A password is required for new mail accounts."), "danger")
            return redirect(f"{url_for('admin.admin_settings')}#settings-mail")
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
    mail_account.from_email = (form.from_email.data or "").strip().lower() or None
    mail_account.from_name = (form.from_name.data or "").strip() or None

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
        return redirect(url_for("admin.admin_settings", **redirect_kwargs))

    flash(_("Mail account saved successfully."), "success")
    return redirect(f"{url_for('admin.admin_settings')}#settings-mail")


@admin_bp.route("/admin/settings/mail-accounts/<int:mail_account_id>/delete", methods=["POST"])
@login_required
@requires(Permission.SETTINGS_CREDENTIALS)
def delete_mail_account(mail_account_id):
    mail_account = db.session.get(MailAccount, mail_account_id)
    if mail_account is None:
        flash(_("The selected mail account could not be found."), "warning")
        return redirect(f"{url_for('admin.admin_settings')}#settings-mail")

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
    return redirect(f"{url_for('admin.admin_settings')}#settings-mail")


@admin_bp.route("/admin/settings/mail-accounts/import", methods=["POST"])
@login_required
@requires(Permission.SETTINGS_CREDENTIALS)
def import_mail_accounts():
    upload = request.files.get("mail_accounts_file")
    overwrite_existing = request.form.get("overwrite_existing") == "1"

    if upload is None or not upload.filename:
        flash(_("Please choose a JSON file to import."), "warning")
        return redirect(f"{url_for('admin.admin_settings')}#settings-mail")

    try:
        raw_payload = upload.stream.read()
        payload = json.loads(raw_payload.decode("utf-8-sig"))
        imported_accounts = normalize_imported_mail_accounts_payload(payload)
    except UnicodeDecodeError:
        flash(_("The uploaded file is not valid UTF-8 JSON."), "danger")
        return redirect(f"{url_for('admin.admin_settings')}#settings-mail")
    except json.JSONDecodeError:
        flash(_("The uploaded file is not valid JSON."), "danger")
        return redirect(f"{url_for('admin.admin_settings')}#settings-mail")
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(f"{url_for('admin.admin_settings')}#settings-mail")

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
            mail_account.from_email = imported_account["from_email"]
            mail_account.from_name = imported_account["from_name"]
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
        return redirect(f"{url_for('admin.admin_settings')}#settings-mail")

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

    return redirect(f"{url_for('admin.admin_settings')}#settings-mail")


@admin_bp.route("/admin/settings/mail-accounts/export", methods=["POST"])
@login_required
@requires(Permission.SETTINGS_CREDENTIALS)
def export_mail_accounts():
    confirm_password = request.form.get("export_password", "")
    if not current_user.check_password(confirm_password):
        flash(_("Please confirm your current password to export sender accounts."), "danger")
        return redirect(f"{url_for('admin.admin_settings')}#settings-mail")

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


@admin_bp.route("/admin/settings/mail-accounts/<int:mail_account_id>/test-connection", methods=["POST"])
@login_required
@requires(Permission.SETTINGS_CREDENTIALS)
@limiter.limit(RATELIMIT_ADMIN_EMAIL)
def test_mail_account_connection(mail_account_id):
    mail_account = db.session.get(MailAccount, mail_account_id)
    if mail_account is None:
        flash(_("The selected mail account could not be found."), "warning")
        return redirect(f"{url_for('admin.admin_settings')}#settings-mail")

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
    return redirect(url_for("admin.admin_settings", edit_mail_account=mail_account.id))


@admin_bp.route("/admin/settings/send-test-email", methods=["POST"])
@login_required
@requires(Permission.NOTIFICATIONS_MANAGE)
@limiter.limit(RATELIMIT_ADMIN_EMAIL)
def send_test_email():
    form = TestEmailForm()

    try:
        mail_accounts = load_mail_accounts_config()
        form.sender.choices = [(acc, acc) for acc in mail_accounts.keys()]

        email_template_dir = os.path.join(current_app.root_path, "templates", "emails")
        if os.path.isdir(email_template_dir):
            form.template.choices = [(f, f) for f in sorted(os.listdir(email_template_dir)) if f.endswith(".html") and not f.startswith("_")]
    except Exception as exc:
        current_app.logger.error(f"Could not load email accounts or templates for test form validation: {exc}")
        form.sender.choices = []
        form.template.choices = []

    if form.validate_on_submit():
        sender = form.sender.data
        recipient = form.recipient.data
        template = form.template.data

        subject, template_vars = sample_email_for(template)
        success = send_mail(
            from_account=sender,
            to_email=recipient,
            subject=f"Test: {subject}",
            template_name=template,
            **template_vars,
        )

        if success:
            flash(_("Test email sent successfully to %(recipient)s!", recipient=recipient), "success")
        else:
            flash(_("Failed to send test email. Please check the server logs."), "danger")
    else:
        flash(_("Invalid form submission. Please check the fields and try again."), "warning")

    return redirect(f"{url_for('admin.admin_settings')}#settings-test")


@admin_bp.route("/admin/undelivered-emails/<int:job_id>/<any(retry, dismiss):action>", methods=["POST"])
@login_required
@requires(Permission.NOTIFICATIONS_MANAGE)
def admin_resolve_undelivered_email(job_id, action):
    """Retry or dismiss an email that gave up.

    Without this the health report is a dead end: it says an email could not be
    delivered and there is no way to see which, fix it, or make it stop saying
    so. Nothing prunes these rows, so one mistyped address would leave the panel
    permanently red.
    """
    job = db.session.get(EmailDeliveryJob, job_id)
    if job is None:
        flash(_("That queued email no longer exists."), "warning")
        return redirect(f"{url_for('admin.admin_settings')}#settings-maintenance")

    recipient = job.recipient_email
    if action == "retry":
        changed = requeue_email_delivery_job(job)
        message = (
            _("Queued for another delivery attempt to %(email)s.", email=recipient)
            if changed
            else _("That email is no longer waiting to be resolved.")
        )
    else:
        changed = dismiss_email_delivery_job(job)
        message = (
            _("Dismissed the undelivered email to %(email)s.", email=recipient)
            if changed
            else _("That email is no longer waiting to be resolved.")
        )

    if changed:
        log_audit_event(
            category="notification",
            event_type=f"undelivered_email_{action}",
            actor_user=current_user,
            target_user=job.target_user,
            target_member=job.target_member,
            metadata={"job_id": job.id, "email_type": job.email_type, "recipient": recipient},
        )
    db.session.commit()
    flash(message, "success" if changed else "warning")
    return redirect(f"{url_for('admin.admin_settings')}#settings-maintenance")


@admin_bp.route("/admin/forum-tasks/retry", methods=["POST"])
@login_required
@requires(Permission.SYSTEM_UPDATE)
def admin_retry_failed_forum_tasks():
    """Put background tasks that gave up retrying back in the queue.

    They give up after about seven hours of failing -- in practice a forum that
    was down that long. Once it is back, this sends them again rather than
    leaving each member's forum out of date until something else changes.
    """
    count = outbox.retry_failed()
    if count:
        log_audit_event(
            category="system",
            event_type="forum_tasks_retried",
            actor_user=current_user,
            metadata={"count": count},
        )
    db.session.commit()
    flash(
        ngettext("%(num)s background task will be tried again within a few minutes.",
                 "%(num)s background tasks will be tried again within a few minutes.", count)
        if count else _("No background tasks are waiting to be retried."),
        "success" if count else "info",
    )
    return redirect(f"{url_for('admin.admin_settings')}#settings-maintenance")


@admin_bp.route("/admin/system-update/status", methods=["GET"])
@login_required
@requires(Permission.SYSTEM_UPDATE)
def admin_system_update_status():
    """Current and available version, as JSON.

    The page polls this while an update runs. It returns exactly what the HTML
    panel renders, from the same service call, so the two cannot drift.
    """
    force = request.args.get("refresh") == "1"
    return jsonify(describe_update_state(force_remote_check=force))


@admin_bp.route("/admin/system-update", methods=["POST"])
@login_required
@requires(Permission.SYSTEM_UPDATE)
@limiter.limit(RATELIMIT_ADMIN_EMAIL, methods=["POST"])
def admin_request_system_update():
    """Ask the privileged runner to install the available update.

    This deliberately does not run the update: the web process is unprivileged
    and must stay that way. It records a request that the root-side watcher
    picks up, which is also why there is nothing to wait for here.
    """
    action = "rollback" if request.form.get("action") == "rollback" else "update"
    before = describe_update_state()
    try:
        request_update(requested_by_user_id=current_user.id, action=action)
    except ServiceError as exc:
        flash(exc.message, "warning" if exc.http_status < 500 else "danger")
        return redirect(url_for("admin.admin_settings", _anchor="settings-maintenance"))

    log_audit_event(
        category="system",
        event_type="rollback_requested" if action == "rollback" else "update_requested",
        actor_user=current_user,
        target_user=current_user,
        before={"revision": (before.get("local") or {}).get("revision")},
        after={"revision": before.get("remote_revision")},
        metadata={"branch": (before.get("local") or {}).get("branch")},
    )
    db.session.commit()

    if action == "rollback":
        flash(
            _("The rollback has started. The site restarts while it runs, so this "
              "page may be briefly unavailable; it reloads by itself when it is done."),
            "info",
        )
    else:
        flash(
            _("The update has started. The site restarts while it runs, so this page "
              "may be briefly unavailable; it reloads by itself when the update is done."),
            "info",
        )
    return redirect(url_for("admin.admin_settings", _anchor="settings-maintenance"))


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


# ---- Backup & Restore -----------------------------------------------------------------


def _start_backup_process(passphrase, requested_by):
    """Run ``flask create-backup`` in the background and hand it the passphrase.

    A separate process, because a backup takes longer than a request should
    and must not die with a gunicorn worker. The passphrase goes through a
    pipe: never on a command line, where other users of the machine could
    read it, and never into a file.
    """
    import subprocess
    import sys

    from ..config import REPO_ROOT

    if current_app.config.get("BACKUP_RUN_INLINE"):
        # Tests: the same command, in this process.
        current_app.test_cli_runner().invoke(
            args=["create-backup", "--passphrase-stdin", "--created-by", requested_by],
            input=passphrase + "\n",
        )
        return

    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPO_ROOT)
    process = subprocess.Popen(
        [sys.executable, "-m", "flask", "--app", "aeronautics_members.app:create_app",
         "create-backup", "--passphrase-stdin", "--created-by", requested_by],
        cwd=str(REPO_ROOT),
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    backup_service.record_pid(process.pid)
    process.stdin.write((passphrase + "\n").encode("utf-8"))
    process.stdin.close()


@admin_bp.route("/admin/backup", methods=["POST"])
@login_required
@requires(Permission.SYSTEM_BACKUP)
@limiter.limit(RATELIMIT_ADMIN_EMAIL, methods=["POST"])
def admin_start_backup():
    back = redirect(url_for("admin.admin_settings", _anchor="settings-maintenance"))
    passphrase = request.form.get("passphrase", "")
    if len(passphrase) < backup_service.MIN_PASSPHRASE_LENGTH:
        flash(_("The passphrase must be at least %(count)s characters long.",
                count=backup_service.MIN_PASSPHRASE_LENGTH), "warning")
        return back
    if passphrase != request.form.get("passphrase_confirm", ""):
        flash(_("The two passphrases are not the same."), "warning")
        return back
    status = backup_service.read_status()
    if status and status.get("state") == "running":
        flash(_("A backup is already being made."), "warning")
        return back

    backup_service.start_status(current_user.email)
    log_audit_event(category="system", event_type="backup_requested", actor_user=current_user,
                    target_user=current_user)
    db.session.commit()
    _start_backup_process(passphrase, current_user.email)
    return back


@admin_bp.route("/admin/backup/status", methods=["GET"])
@login_required
@requires(Permission.SYSTEM_BACKUP)
def admin_backup_status():
    status = backup_service.read_status() or {}
    return jsonify({
        "state": status.get("state"),
        "steps": status.get("steps", []),
        "log": status.get("log", []),
        "error": status.get("error"),
        "result": status.get("result"),
    })


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


@admin_bp.route("/admin/backup/files/<name>/delete", methods=["POST"])
@login_required
@requires(Permission.SYSTEM_BACKUP)
def admin_delete_backup(name):
    path = backup_service.backup_path(name)
    if path is not None:
        path.unlink()
        log_audit_event(category="system", event_type="backup_deleted", actor_user=current_user,
                        target_user=current_user, metadata={"file": name})
        db.session.commit()
        flash(_("Deleted %(name)s.", name=name), "success")
    return redirect(url_for("admin.admin_settings", _anchor="settings-maintenance"))


@admin_bp.route("/admin/background-jobs/resume", methods=["POST"])
@login_required
@requires(Permission.SYSTEM_BACKUP)
def admin_resume_background_jobs():
    """Say "this is the real portal": let every background job run again."""
    if request.form.get("confirm") != "resume":
        flash(_("Tick the box to confirm this is the server members use."), "warning")
        return redirect(url_for("admin.admin_settings", _anchor="settings-maintenance"))
    paused = background_jobs.pause_state()
    background_jobs.resume(current_user.email)
    resume_checks.reset()
    log_audit_event(category="system", event_type="background_jobs_resumed", actor_user=current_user,
                    target_user=current_user, metadata={"paused": paused})
    db.session.commit()
    return redirect(url_for("admin.admin_settings", _anchor="settings-maintenance"))


@admin_bp.route("/admin/background-jobs/checklist", methods=["GET"])
@login_required
@requires(Permission.SYSTEM_BACKUP)
def admin_background_jobs_checklist():
    """The resume checklist, running the next outstanding service check first."""
    items = resume_checks.checklist(run_next=request.args.get("run") != "0")
    db.session.commit()
    return jsonify({"items": items, "done": resume_checks.all_done(items),
                    "paused": background_jobs.is_paused()})


@admin_bp.route("/admin/background-jobs/checklist/again", methods=["POST"])
@login_required
@requires(Permission.SYSTEM_BACKUP)
def admin_background_jobs_check_again():
    resume_checks.reset()
    db.session.commit()
    return redirect(url_for("admin.admin_settings", _anchor="settings-maintenance"))


@admin_bp.route("/admin/background-jobs/checklist/dismiss", methods=["POST"])
@login_required
@requires(Permission.SYSTEM_BACKUP)
def admin_background_jobs_dismiss_checklist():
    background_jobs.clear_resumed()
    resume_checks.reset()
    db.session.commit()
    return redirect(url_for("admin.admin_settings", _anchor="settings-maintenance"))
