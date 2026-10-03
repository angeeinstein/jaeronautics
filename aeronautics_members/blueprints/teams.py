"""Teams: the site admins' pages for creating teams and appointing their leads.

Reachable whether or not teams are switched on -- they are where they get
switched on. Everything about members and leads using teams comes later, and
only while the switch is on. See services/teams.py and docs/teams-plan.md.
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user, login_required

from ..app import requires
from ..db_models import User, db
from ..permissions import Permission
from ..services import ServiceError
from ..services import teams as teams_service

teams_bp = Blueprint("teams", __name__)


def _team_form_fields(form):
    return {
        "name": form.get("name"),
        "description": form.get("description"),
        "admission_mode": form.get("admission_mode"),
        "applications_open": form.get("applications_open") == "on",
        "application_prompt": form.get("application_prompt"),
        "max_members": form.get("max_members"),
        "forum_group": form.get("forum_group"),
    }


def _render_admin_teams(**context):
    singular, plural = teams_service.team_labels()
    return render_template(
        "admin_teams.html",
        active_admin_section="teams",
        page_title=plural,
        page_description=_("Create teams and appoint their leads."),
        teams_enabled=teams_service.teams_enabled(),
        label_singular=singular,
        label_plural=plural,
        teams=teams_service.all_teams(),
        has_lead_in_force=teams_service.has_lead_in_force,
        **context,
    )


@teams_bp.route("/admin/teams", methods=["GET", "POST"])
@login_required
@requires(Permission.TEAMS_MANAGE)
def admin_teams():
    if request.method == "POST":
        changed = teams_service.save_team_settings(
            current_user,
            enabled=request.form.get("teams_enabled") == "on",
            label_singular=request.form.get("label_singular"),
            label_plural=request.form.get("label_plural"),
        )
        db.session.commit()
        flash(_("Saved.") if changed else _("Nothing changed."), "success" if changed else "info")
        return redirect(url_for("teams.admin_teams"))
    return _render_admin_teams()


@teams_bp.route("/admin/teams/new", methods=["GET", "POST"])
@login_required
@requires(Permission.TEAMS_MANAGE)
def admin_team_new():
    singular, _plural = teams_service.team_labels()
    form = request.form if request.method == "POST" else {}
    if request.method == "POST":
        try:
            team = teams_service.create_team(
                current_user, slug=request.form.get("slug"), **_team_form_fields(request.form)
            )
        except ServiceError as error:
            db.session.rollback()
            flash(error.message, "danger")
        else:
            db.session.commit()
            flash(_("Created."), "success")
            return redirect(url_for("teams.admin_team_detail", slug=team.slug))
    return render_template(
        "admin_team_form.html",
        active_admin_section="teams",
        page_title=_("New %(label)s", label=singular),
        page_description=_("Give it a lead once it exists."),
        team=None,
        form=form,
        admission_modes=teams_service.ADMISSION_MODES,
        label_plural=teams_service.team_labels()[1],
    )


@teams_bp.route("/admin/teams/<slug>", methods=["GET", "POST"])
@login_required
@requires(Permission.TEAMS_MANAGE)
def admin_team_detail(slug):
    try:
        team = teams_service.get_team(slug)
    except ServiceError as error:
        flash(error.message, "warning")
        return redirect(url_for("teams.admin_teams"))

    form = request.form if request.method == "POST" else {}
    if request.method == "POST":
        try:
            teams_service.update_team(current_user, team, **_team_form_fields(request.form))
        except ServiceError as error:
            db.session.rollback()
            flash(error.message, "danger")
        else:
            db.session.commit()
            flash(_("Saved."), "success")
            return redirect(url_for("teams.admin_team_detail", slug=team.slug))

    return render_template(
        "admin_team_form.html",
        active_admin_section="teams",
        page_title=team.name,
        page_description=_("Details and roles."),
        team=team,
        form=form,
        admission_modes=teams_service.ADMISSION_MODES,
        role_holders=teams_service.role_holders(team),
        role_counts=teams_service.role_counts,
        has_lead_in_force=teams_service.has_lead_in_force(team),
        team_roles=teams_service.TEAM_ROLE_LABELS,
        label_plural=teams_service.team_labels()[1],
    )


@teams_bp.route("/admin/teams/<slug>/archive", methods=["POST"])
@login_required
@requires(Permission.TEAMS_MANAGE)
def admin_team_archive(slug):
    try:
        team = teams_service.get_team(slug)
    except ServiceError as error:
        flash(error.message, "warning")
        return redirect(url_for("teams.admin_teams"))
    archived = request.form.get("archived") == "1"
    teams_service.set_team_archived(current_user, team, archived)
    db.session.commit()
    flash(_("Archived.") if archived else _("Restored."), "success")
    return redirect(url_for("teams.admin_team_detail", slug=team.slug))


@teams_bp.route("/admin/teams/<slug>/roles", methods=["POST"])
@login_required
@requires(Permission.TEAMS_MANAGE)
def admin_team_grant_role(slug):
    back = url_for("teams.admin_team_detail", slug=slug)
    try:
        team = teams_service.get_team(slug)
        user = teams_service.find_account(request.form.get("email"))
        teams_service.grant_team_role(current_user, team, user, request.form.get("role") or teams_service.ROLE_LEAD)
    except ServiceError as error:
        db.session.rollback()
        flash(error.message, "danger")
        return redirect(back)
    db.session.commit()
    flash(_("Role given."), "success")
    return redirect(back)


@teams_bp.route("/admin/teams/<slug>/roles/revoke", methods=["POST"])
@login_required
@requires(Permission.TEAMS_MANAGE)
def admin_team_revoke_role(slug):
    back = url_for("teams.admin_team_detail", slug=slug)
    try:
        team = teams_service.get_team(slug)
        user = db.session.get(User, request.form.get("user_id", type=int) or 0)
        if user is None:
            raise ServiceError(_("That account does not exist."))
        teams_service.revoke_team_role(
            current_user, team, user, request.form.get("role"),
            confirmed=request.form.get("confirmed") == "1",
        )
    except ServiceError as error:
        db.session.rollback()
        flash(error.message, "danger")
        return redirect(back)
    db.session.commit()
    flash(_("Role removed."), "success")
    return redirect(back)
