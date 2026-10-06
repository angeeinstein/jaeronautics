"""Admin blueprint.

Route handlers moved verbatim out of app.py (dedented; @app.route ->
@admin_bp.route; app.logger -> current_app.logger). Helpers are imported
from the app module, which is fully initialized before this is imported.
"""

from pathlib import Path

from flask import Blueprint, current_app, jsonify

from ..member_categories import CATEGORY_ORDER
from ..permissions import (
    Permission,
)
from ..config import (
    RATELIMIT_ADMIN_EMAIL,
    STRIPE_SETTING_KEYS,
)
from ..services.audit import (
    log_audit_event,
    redact_settings_states_for_audit,
    snapshot_mail_account_for_audit,
)
from ..services.institutional_email import SETTING_KEY as INSTITUTIONAL_EMAIL_SETTING_KEY
from ..services.forum import (
    get_forum_service,
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
from ..forum_service import (
    FORUM_SETTING_KEYS,
    ForumProviderError,
)
from ..mail_utils import (
    load_mail_accounts_config,
    probe_mail_account_connection,
    send_mail,
)
from ..notification_service import (
    NOTIFICATION_SETTING_KEYS,
)
from ..app import (
    build_settings_page_context,
    limiter,
    requires,
    set_setting_value,
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


@admin_bp.route("/admin/settings/test-forum-connection", methods=["POST"])
@login_required
@requires(Permission.SETTINGS_CREDENTIALS)
def test_forum_connection():
    service = get_forum_service()
    try:
        success, message = service.test_connection()
    except ForumProviderError as exc:
        success = False
        message = str(exc)

    log_audit_event(
        category="forum",
        event_type="forum_connection_tested",
        actor_user=current_user,
        target_user=current_user,
        before=None,
        after={"ready": service.is_ready(), "enabled": service.is_enabled()},
        metadata={"success": success, "message": message},
    )
    db.session.commit()
    flash(message, "success" if success else "danger")
    return redirect(f"{url_for('admin.admin_settings')}#settings-forum")


@admin_bp.route("/admin/settings", methods=["GET", "POST"])
@login_required
@requires(Permission.SETTINGS_GENERAL)
def admin_settings():
    edit_mail_account_id = request.args.get("edit_mail_account", type=int)
    context = build_settings_page_context(edit_mail_account_id=edit_mail_account_id)

    if edit_mail_account_id and context["editing_mail_account"] is None:
        flash(_("The selected mail account could not be found."), "warning")
        return redirect(url_for("admin.admin_settings"))

    if request.method == "POST" and "save_settings" in request.form:
        valid_senders = {choice for choice, _label in context["sender_choices"]}
        valid_templates = {choice for choice, _label in context["template_choices"]}
        valid_forum_providers = {choice for choice, _label in context["forum_provider_choices"]}
        valid_forum_auth_strategies = {choice for choice, _label in context["forum_auth_strategy_choices"]}
        tracked_setting_keys = [
            "invoice_payments_enabled",
            "automatic_emails_enabled",
            "legal_pdfs_in_welcome_emails",
            "welcome_email_sender",
            "automatic_email_template",
            INSTITUTIONAL_EMAIL_SETTING_KEY,
            *STRIPE_SETTING_KEYS,
            *FORUM_SETTING_KEYS,
            *NOTIFICATION_SETTING_KEYS,
        ]
        before_settings = {
            key: db.session.get(Setting, key).value if db.session.get(Setting, key) is not None else None
            for key in tracked_setting_keys
        }
        settings_section = (request.form.get("settings_section") or "general").strip().lower()
        if settings_section not in {"general", "notifications", "billing", "forum", "mail", "test"}:
            settings_section = "general"

        # Billing and Forum hold third-party credentials -- the Stripe secret and
        # webhook secret, the Discourse API key and connect secret. Those tabs are
        # not rendered for an ordinary administrator, but the form they would have
        # posted is trivial to reconstruct, so the decision is made here as well.
        # Every other section only touches the settings a plain admin may change,
        # because the unselected ones are written back from before_settings.
        if settings_section in CREDENTIAL_SETTINGS_SECTIONS and not current_user.can(Permission.SETTINGS_CREDENTIALS):
            flash(_("You do not have permission to change those settings."), "danger")
            return redirect(url_for("admin.admin_settings"))

        settings_redirect = f"{url_for('admin.admin_settings')}#settings-{settings_section}"
        welcome_sender = request.form.get("welcome_email_sender")
        auto_email_template = request.form.get("automatic_email_template")
        institutional_domains = request.form.get(INSTITUTIONAL_EMAIL_SETTING_KEY)
        notification_sender = request.form.get("notification_sender")
        stripe_publishable_key = ((request.form.get("stripe_publishable_key") if settings_section == "billing" else before_settings.get("stripe_publishable_key")) or "").strip()
        stripe_price_id = ((request.form.get("stripe_price_id") if settings_section == "billing" else before_settings.get("stripe_price_id")) or "").strip()
        forum_provider = (request.form.get("forum_provider") or before_settings.get("forum_provider") or "discourse").strip() or "discourse"
        forum_auth_strategy = (request.form.get("forum_auth_strategy") or before_settings.get("forum_auth_strategy") or "discourse_connect").strip() or "discourse_connect"
        forum_avatar_max_bytes = (request.form.get("forum_avatar_max_bytes") or before_settings.get("forum_avatar_max_bytes") or "").strip()
        forum_avatar_allowed_types = (request.form.get("forum_avatar_allowed_types") or before_settings.get("forum_avatar_allowed_types") or "").strip()

        if welcome_sender and welcome_sender not in valid_senders:
            flash(_("Invalid sender account selected."), "danger")
            return redirect(settings_redirect)

        if auto_email_template and auto_email_template not in valid_templates:
            flash(_("Invalid email template selected."), "danger")
            return redirect(settings_redirect)

        if notification_sender and notification_sender not in valid_senders:
            flash(_("Invalid sender account selected."), "danger")
            return redirect(settings_redirect)

        if forum_provider not in valid_forum_providers:
            flash(_("Invalid forum provider selected."), "danger")
            return redirect(settings_redirect)

        if forum_auth_strategy not in valid_forum_auth_strategies:
            flash(_("Invalid forum authentication strategy selected."), "danger")
            return redirect(settings_redirect)

        if forum_avatar_max_bytes:
            try:
                if int(forum_avatar_max_bytes) <= 0:
                    raise ValueError
            except ValueError:
                flash(_("The forum avatar size limit must be a positive number of bytes."), "danger")
                return redirect(settings_redirect)

        invoice_enabled = (request.form.get("invoice_payments_enabled") == "on") if settings_section == "general" else str(before_settings.get("invoice_payments_enabled") or "False") == "True"
        emails_enabled = (request.form.get("automatic_emails_enabled") == "on") if settings_section == "general" else str(before_settings.get("automatic_emails_enabled") or "False") == "True"
        forum_enabled = (request.form.get("forum_integration_enabled") == "on") if settings_section == "forum" else str(before_settings.get("forum_integration_enabled") or "False") == "True"
        notification_admin_general_enabled = (request.form.get("notification_admin_general_enabled") == "on") if settings_section == "notifications" else str(before_settings.get("notification_admin_general_enabled") or "True") == "True"
        notification_admin_error_enabled = (request.form.get("notification_admin_error_enabled") == "on") if settings_section == "notifications" else str(before_settings.get("notification_admin_error_enabled") or "True") == "True"
        notification_user_status_enabled = (request.form.get("notification_user_status_enabled") == "on") if settings_section == "notifications" else str(before_settings.get("notification_user_status_enabled") or "True") == "True"

        set_setting_value("invoice_payments_enabled", str(invoice_enabled))
        set_setting_value("automatic_emails_enabled", str(emails_enabled))
        set_setting_value(
            "legal_pdfs_in_welcome_emails",
            str(request.form.get("legal_pdfs_in_welcome_emails") == "on") if settings_section == "general"
            else before_settings.get("legal_pdfs_in_welcome_emails"),
        )
        set_setting_value("welcome_email_sender", welcome_sender if settings_section == "general" else before_settings.get("welcome_email_sender"))
        set_setting_value("automatic_email_template", auto_email_template if settings_section == "general" else before_settings.get("automatic_email_template"))
        set_setting_value(
            INSTITUTIONAL_EMAIL_SETTING_KEY,
            # Stored as the admin typed it; institutional_email.py is what
            # makes sense of commas, newlines and stray @ signs.
            institutional_domains if settings_section == "general"
            else before_settings.get(INSTITUTIONAL_EMAIL_SETTING_KEY),
        )
        set_setting_value("notification_admin_general_enabled", str(notification_admin_general_enabled))
        set_setting_value("notification_admin_error_enabled", str(notification_admin_error_enabled))
        set_setting_value("notification_user_status_enabled", str(notification_user_status_enabled))
        set_setting_value("notification_sender", (notification_sender if settings_section == "notifications" else before_settings.get("notification_sender")) or None)
        moving_to_new_fee = 0
        if settings_section == "billing" and stripe_price_id:
            # A new fee: checked with Stripe, and every running subscription
            # moves to it from its next renewal (services/billing.py).
            from ..services.billing import change_membership_price

            try:
                moving_to_new_fee = change_membership_price(current_user, stripe_price_id)
            except ServiceError as exc:
                db.session.rollback()
                flash(exc.message, "danger")
                return redirect(settings_redirect)
        set_setting_value("stripe_publishable_key", stripe_publishable_key or None)
        set_setting_value("stripe_price_id", stripe_price_id or None)
        set_setting_value("forum_integration_enabled", str(forum_enabled))
        set_setting_value("forum_provider", forum_provider)
        set_setting_value("forum_auth_strategy", forum_auth_strategy)
        set_setting_value("forum_base_url", ((request.form.get("forum_base_url") if settings_section == "forum" else before_settings.get("forum_base_url")) or "").strip() or None)
        set_setting_value("discourse_api_username", ((request.form.get("discourse_api_username") if settings_section == "forum" else before_settings.get("discourse_api_username")) or "").strip() or None)
        set_setting_value("forum_onboarding_group", ((request.form.get("forum_onboarding_group") if settings_section == "forum" else before_settings.get("forum_onboarding_group")) or "").strip() or None)
        set_setting_value("forum_member_group", ((request.form.get("forum_member_group") if settings_section == "forum" else before_settings.get("forum_member_group")) or "").strip() or None)
        set_setting_value("forum_inactive_group", ((request.form.get("forum_inactive_group") if settings_section == "forum" else before_settings.get("forum_inactive_group")) or "").strip() or None)
        set_setting_value("forum_staff_group", ((request.form.get("forum_staff_group") if settings_section == "forum" else before_settings.get("forum_staff_group")) or "").strip() or None)
        manage_staff_flags = (request.form.get("forum_manage_staff_flags") == "on") if settings_section == "forum" else str(before_settings.get("forum_manage_staff_flags") or "False") == "True"
        set_setting_value("forum_manage_staff_flags", str(manage_staff_flags))
        # Stored as the lines the rest of the application reads, composed from
        # one box per kind of member: the left-hand side is fixed, so nobody
        # should have to type it correctly.
        if settings_section == "forum":
            written = "\n".join(
                f"{kind} = {name}" for kind, name in (
                    (kind, (request.form.get(f"forum_group_{kind}") or "").strip())
                    for kind in CATEGORY_ORDER
                ) if name
            )
        else:
            written = before_settings.get("forum_category_groups") or ""
        set_setting_value("forum_category_groups", written.strip() or None)
        set_setting_value("forum_lecture_groups", ((request.form.get("forum_lecture_groups") if settings_section == "forum" else before_settings.get("forum_lecture_groups")) or "").strip() or None)
        set_setting_value("forum_archive_groups", ((request.form.get("forum_archive_groups") if settings_section == "forum" else before_settings.get("forum_archive_groups")) or "").strip() or None)
        set_setting_value("forum_onboarding_path", ((request.form.get("forum_onboarding_path") if settings_section == "forum" else before_settings.get("forum_onboarding_path")) or "").strip() or "/")
        set_setting_value("forum_avatar_max_bytes", forum_avatar_max_bytes or None)
        set_setting_value("forum_avatar_allowed_types", forum_avatar_allowed_types or None)

        existing_api_key = before_settings.get("discourse_api_key")
        submitted_api_key = ((request.form.get("discourse_api_key") if settings_section == "forum" else "") or "").strip()
        set_setting_value("discourse_api_key", submitted_api_key or existing_api_key)

        existing_connect_secret = before_settings.get("discourse_connect_secret")
        submitted_connect_secret = ((request.form.get("discourse_connect_secret") if settings_section == "forum" else "") or "").strip()
        set_setting_value("discourse_connect_secret", submitted_connect_secret or existing_connect_secret)

        existing_stripe_secret = before_settings.get("stripe_secret_key")
        submitted_stripe_secret = ((request.form.get("stripe_secret_key") if settings_section == "billing" else "") or "").strip()
        set_setting_value("stripe_secret_key", submitted_stripe_secret or existing_stripe_secret)

        existing_webhook_secret = before_settings.get("stripe_webhook_secret")
        submitted_webhook_secret = ((request.form.get("stripe_webhook_secret") if settings_section == "billing" else "") or "").strip()
        set_setting_value("stripe_webhook_secret", submitted_webhook_secret or existing_webhook_secret)

        after_settings = {
            key: db.session.get(Setting, key).value if db.session.get(Setting, key) is not None else None
            for key in tracked_setting_keys
        }
        changed_keys = sorted(
            key for key in after_settings.keys()
            if before_settings.get(key) != after_settings.get(key)
        )
        logged_before_settings, logged_after_settings = redact_settings_states_for_audit(before_settings, after_settings)
        log_audit_event(
            category="settings",
            event_type="settings_updated",
            actor_user=current_user,
            target_user=current_user,
            before=logged_before_settings,
            after=logged_after_settings,
            metadata={"changed_keys": changed_keys},
        )
        db.session.commit()
        flash(_("Settings updated successfully!"), "success")
        if moving_to_new_fee:
            flash(_("%(count)s running subscription(s) move to the new price from their next renewal, "
                    "in the background over the next minutes. Each member is emailed two weeks before their renewal.",
                    count=moving_to_new_fee), "info")
        return redirect(settings_redirect)

    return render_template(
        "admin_settings.html",
        active_admin_section="settings",
        **context,
    )


@admin_bp.route("/admin/legal", methods=["GET", "POST"])
@login_required
@requires(Permission.SETTINGS_GENERAL)
def admin_legal():
    """The legal texts in force, the teams' rules, what is wrong with the files
    on this server -- and a preview of a new text as its PDF, from uploaded
    files that are checked as CI checks them and not kept."""
    import io

    from .. import legal_pdf
    from ..services import legal_texts as legal
    from ..services import teams as teams_service

    preview_problems = None
    if request.method == "POST":
        german = request.files.get("german")
        english = request.files.get("english")
        if german is None or not german.filename:
            flash(_("Choose the German file."), "warning")
            return redirect(url_for("admin.admin_legal"))
        uploads = [(german.read(legal_pdf.PREVIEW_MAX_BYTES + 1), german.filename)]
        if english is not None and english.filename:
            uploads.append((english.read(legal_pdf.PREVIEW_MAX_BYTES + 1), english.filename))
        try:
            data, preview_problems, name = legal_pdf.preview(*uploads)
        except Exception:  # noqa: BLE001 -- a text WeasyPrint cannot lay out, say
            current_app.logger.exception("Could not make a preview PDF")
            data, preview_problems = None, [_("The PDF could not be made. See the log for why.")]
        if data is not None:
            return send_file(io.BytesIO(data), mimetype="application/pdf", download_name=f"VORSCHAU_{name}")

    all_teams = {team.slug: team for team in teams_service.all_teams(include_archived=True)}
    team_slugs = set(all_teams)
    waiting = []
    for slug, team in [(text.slug, None) for text in legal.LEGAL_TEXTS] + [
        (text.slug, team_slug) for team_slug in legal.teams_with_texts() for text in legal.TEAM_TEXTS
    ]:
        for version in legal.waiting(slug, team=team):
            waiting.append({
                "version": version,
                "team_name": all_teams[team].name if team in all_teams else team,
                "has_english": legal.find_any(slug, "en", version.version, team) is not None,
                "url": url_for("admin.admin_legal_waiting_pdf", document=slug, version=version.version.isoformat(),
                               team=team),
            })
    return render_template(
        "admin_legal.html",
        active_admin_section="legal",
        page_title=_("Legal Texts"),
        page_description=_("The texts in force, each team's rules, and a preview of a new text as a PDF."),
        texts=legal.available(),
        team_rules=[(team, teams_service.team_rules(team)) for team in teams_service.all_teams(include_archived=False)],
        repo_problems=legal.problems(),
        orphan_folders=[slug for slug in legal.teams_with_texts() if slug not in team_slugs],
        waiting=waiting,
        pdf_jobs=[{
            "key": legal_pdf.job_key(version),
            "version": version,
            "team_name": all_teams[version.team].name if version.team in all_teams else version.team,
        } for version, _team in legal_pdf.all_jobs()],
        preview_problems=preview_problems,
        max_kb=legal_pdf.PREVIEW_MAX_BYTES // 1024,
    )


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
        flash(_("The PDF could not be made. See the log for why."), "danger")
        return redirect(url_for("admin.admin_legal"))
    if data is None:
        return legal_pages.prepared(digest)
    return send_file(io.BytesIO(data), mimetype="application/pdf", download_name=name)


@admin_bp.route("/admin/legal/pdfs/remake", methods=["POST"])
@login_required
@requires(Permission.SETTINGS_GENERAL)
def admin_legal_remake_pdfs():
    """Make the kept PDFs again.

    The page's script sends one ``item`` at a time -- ``stored`` to remove the
    kept files, then each version by its key -- and ticks it off with the
    answer, so every PDF is one short request. Without the script, the form
    comes without an item and everything is done in this one.
    """
    from .. import legal_pdf

    item = request.form.get("item")
    if item is None:
        legal_pdf.forget_all()
        made = legal_pdf.build_all(again=True)
        failed = [version for version, result in made if isinstance(result, Exception)]
        _audit_pdfs_remade(len(made))
        if failed:
            flash(_("%(count)s PDF(s) could not be made. See the log for why.", count=len(failed)), "warning")
        else:
            flash(_("All %(count)s PDFs were made again.", count=len(made)), "success")
        return redirect(url_for("admin.admin_legal"))

    if item == "stored":
        removed = legal_pdf.forget_all()
        _audit_pdfs_remade(len(legal_pdf.all_jobs()))
        return jsonify(state="ok", detail=_("%(count)s removed", count=removed))
    for version, team in legal_pdf.all_jobs():
        if legal_pdf.job_key(version) != item:
            continue
        if isinstance(team, Exception):
            return jsonify(state="failed", detail=str(team))
        try:
            size = legal_pdf.remake(version, team)
        except Exception as exc:  # noqa: BLE001 -- shown on its line; the others go on
            current_app.logger.exception("Could not make the PDF of %s", version.path)
            return jsonify(state="failed", detail=str(exc) or exc.__class__.__name__)
        return jsonify(state="ok", detail=f"{max(1, size // 1024)} KB")
    return jsonify(state="failed", detail=_("No longer in legal/.")), 404


def _audit_pdfs_remade(count):
    log_audit_event(category="system", event_type="legal_pdfs_remade", actor_user=current_user,
                    target_user=current_user, metadata={"pdfs": count})
    db.session.commit()


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
