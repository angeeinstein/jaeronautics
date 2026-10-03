"""Teams: the pages for site admins, for members, and for team leads.

The admin pages are reachable whether or not teams are switched on -- they are
where they get switched on. Everything else exists only while the switch is
on. The rules are in services/teams.py; what was discussed and why is in
docs/teams-plan.md.
"""

import csv
import io

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, send_file, url_for
from flask_babel import gettext as _
from flask_login import current_user, login_required

from ..app import requires
from ..db_models import User, db
from ..permissions import Permission
from ..services import ServiceError
from ..services import teams as teams_service
from ..services.audit import log_audit_event
from ..services.clock import get_membership_today

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


def _apply_access_list(team, form):
    teams_service.update_access_list(
        current_user, team,
        recipients=form.get("access_list_recipients"),
        dates=form.get("access_list_dates"),
        auto_send=form.get("access_list_auto_send") == "on",
    )


def _apply_logo(team, form, files):
    """Replace or remove the logo, if the form asks for either."""
    upload = files.get("logo")
    if upload is not None and upload.filename:
        teams_service.set_team_logo(current_user, team, upload.read())
    elif form.get("remove_logo") == "on":
        teams_service.remove_team_logo(current_user, team)


@teams_bp.route("/teams/logo/<token>", methods=["GET"])
def team_logo(token):
    """A team's logo. Not secret, so not behind a login: the token only keeps
    the address from being guessed while a logo is being replaced."""
    path = teams_service.logo_file(teams_service.team_by_logo_token(token))
    if path is None:
        abort(404)
    return send_file(path, mimetype="image/png", conditional=True, max_age=86400)


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
            _apply_access_list(team, request.form)
            _apply_logo(team, request.form, request.files)
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
            _apply_access_list(team, request.form)
            _apply_logo(team, request.form, request.files)
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


# --- For members --------------------------------------------------------------
#
# None of this exists while teams are switched off: every page answers 404, so
# a link somebody kept does not lead into a half-working feature.


def _teams_or_404():
    if not teams_service.teams_enabled():
        abort(404)


def _team_or_404(slug):
    _teams_or_404()
    try:
        team = teams_service.get_team(slug)
    except ServiceError:
        abort(404)
    if team.status != teams_service.STATUS_ACTIVE and not current_user.can(Permission.TEAMS_MANAGE):
        abort(404)
    return team


def _may(team, permission):
    if not teams_service.can_in_team(current_user, team, permission):
        abort(403)


def _sees_team_page(team):
    return bool(teams_service.team_permissions(current_user, team)) or (
        teams_service.is_active_association_member(current_user)
        and teams_service.active_team_membership(current_user, team) is not None
    )


@teams_bp.route("/teams", methods=["GET"])
@login_required
def teams_home():
    _teams_or_404()
    singular, plural = teams_service.team_labels()
    mine = {membership.team_id: membership for membership in teams_service.memberships_of(current_user)}
    ongoing = {
        team_id: membership for team_id, membership in mine.items()
        if membership.status in teams_service.ONGOING
    }
    teams = teams_service.all_teams(include_archived=False)
    return render_template(
        "teams/home.html",
        label_singular=singular,
        label_plural=plural,
        teams=teams,
        latest=mine,
        ongoing=ongoing,
        is_member=teams_service.is_active_association_member(current_user),
        why_not_joinable=lambda team: teams_service.why_not_joinable(current_user, team),
        status_labels=teams_service.STATUS_LABELS,
        manageable={team.id for team in teams_service.teams_led_by(current_user)},
    )


def _member_action(slug, action):
    team = _team_or_404(slug)
    try:
        action(team)
    except ServiceError as error:
        db.session.rollback()
        flash(error.message, "danger")
    else:
        db.session.commit()
        from ..services.notifications import flush_marked_notification_channels

        flush_marked_notification_channels()
        return team, True
    return team, False


@teams_bp.route("/teams/<slug>/join", methods=["POST"])
@login_required
def team_join(slug):
    team, done = _member_action(
        slug, lambda team: teams_service.join_or_apply(current_user, team, request.form.get("application_text"))
    )
    if done:
        if team.admission_mode == teams_service.ADMISSION_OPEN:
            flash(_("Welcome to %(team)s.", team=team.name), "success")
        else:
            flash(_("Application sent. The leads will be in touch."), "success")
    return redirect(url_for("teams.teams_home"))


@teams_bp.route("/teams/<slug>/withdraw", methods=["POST"])
@login_required
def team_withdraw(slug):
    _team, done = _member_action(slug, lambda team: teams_service.withdraw(current_user, team))
    if done:
        flash(_("Application withdrawn."), "success")
    return redirect(url_for("teams.teams_home"))


@teams_bp.route("/teams/<slug>/leave", methods=["POST"])
@login_required
def team_leave(slug):
    team, done = _member_action(slug, lambda team: teams_service.leave(current_user, team))
    if done:
        flash(_("You have left %(team)s.", team=team.name), "success")
    return redirect(url_for("teams.teams_home"))


@teams_bp.route("/teams/<slug>", methods=["GET"])
@login_required
def team_page(slug):
    """The team's own page, for its members: who is in it.

    Built as sections so that more can be added -- documents, dates, a drinks
    balance -- without reworking it.
    """
    team = _team_or_404(slug)
    if not _sees_team_page(team):
        abort(404)
    return render_template(
        "teams/team.html",
        team=team,
        roster=teams_service.roster(team),
        leads=[team_role.user for team_role in teams_service.role_holders(team, teams_service.ROLE_LEAD)
               if teams_service.role_counts(team_role)],
        my_membership=teams_service.active_team_membership(current_user, team),
        can_manage=bool(teams_service.team_permissions(current_user, team)),
        label_plural=teams_service.team_labels()[1],
    )


# --- For leads ----------------------------------------------------------------


@teams_bp.route("/teams/<slug>/manage", methods=["GET"])
@login_required
def team_manage(slug):
    team = _team_or_404(slug)
    _may(team, teams_service.TeamPermission.VIEW_MEMBERS)
    permissions = teams_service.team_permissions(current_user, team)
    return render_template(
        "teams/manage.html",
        team=team,
        permissions=permissions,
        TeamPermission=teams_service.TeamPermission,
        applications=teams_service.team_memberships(
            team, {teams_service.APPLIED, teams_service.INVITED, teams_service.APPROVED}
        ),
        roster=teams_service.roster(team),
        former=teams_service.team_memberships(team, {teams_service.ENDED}),
        status_labels=teams_service.STATUS_LABELS,
        end_reasons=teams_service.END_REASON_LABELS,
        person_details=teams_service.person_details,
        has_lead_in_force=teams_service.has_lead_in_force(team),
        access_list_recipients=teams_service.parse_recipients(team.access_list_recipients),
        next_access_list_date=teams_service.next_access_list_date(team),
    )


@teams_bp.route("/teams/<slug>/manage/people/<int:user_id>", methods=["GET"])
@login_required
def team_person(slug, user_id):
    team = _team_or_404(slug)
    _may(team, teams_service.TeamPermission.VIEW_MEMBERS)
    person = db.session.get(User, user_id)
    history = teams_service.history_of(team, person) if person is not None else []
    if not history:
        abort(404)
    return render_template(
        "teams/person.html",
        team=team,
        person=person,
        details=teams_service.person_details(person),
        history=history,
        current=next((membership for membership in history if membership.status in teams_service.ONGOING), None),
        notes=teams_service.notes_about(team, person),
        permissions=teams_service.team_permissions(current_user, team),
        TeamPermission=teams_service.TeamPermission,
        status_labels=teams_service.STATUS_LABELS,
        end_reasons=teams_service.END_REASON_LABELS,
    )


def _lead_action(slug, permission, action, success_message, back=None):
    team = _team_or_404(slug)
    _may(team, permission)
    try:
        result = action(team)
    except ServiceError as error:
        db.session.rollback()
        flash(error.message, "danger")
    else:
        db.session.commit()
        from ..services.notifications import flush_marked_notification_channels

        flush_marked_notification_channels()
        flash(success_message, "success")
        if back is None and result is not None and hasattr(result, "user_id"):
            back = url_for("teams.team_person", slug=team.slug, user_id=result.user_id)
    return redirect(back or request.referrer or url_for("teams.team_manage", slug=team.slug))


@teams_bp.route("/teams/<slug>/manage/memberships/<int:membership_id>/invite", methods=["POST"])
@login_required
def team_invite(slug, membership_id):
    return _lead_action(
        slug, teams_service.TeamPermission.REVIEW_APPLICATIONS,
        lambda team: teams_service.invite(current_user, team, membership_id, request.form.get("meeting_details")),
        _("Invitation sent."),
    )


@teams_bp.route("/teams/<slug>/manage/memberships/<int:membership_id>/approve", methods=["POST"])
@login_required
def team_approve(slug, membership_id):
    return _lead_action(
        slug, teams_service.TeamPermission.REVIEW_APPLICATIONS,
        lambda team: teams_service.approve(current_user, team, membership_id),
        _("Approved."),
    )


@teams_bp.route("/teams/<slug>/manage/memberships/<int:membership_id>/reject", methods=["POST"])
@login_required
def team_reject(slug, membership_id):
    return _lead_action(
        slug, teams_service.TeamPermission.REVIEW_APPLICATIONS,
        lambda team: teams_service.reject(current_user, team, membership_id),
        _("Not accepted."),
    )


@teams_bp.route("/teams/<slug>/manage/memberships/<int:membership_id>/remove", methods=["POST"])
@login_required
def team_remove(slug, membership_id):
    return _lead_action(
        slug, teams_service.TeamPermission.REMOVE_MEMBERS,
        lambda team: teams_service.remove(current_user, team, membership_id, request.form.get("reason")),
        _("Removed from the team."),
    )


@teams_bp.route("/teams/<slug>/manage/people/<int:user_id>/notes", methods=["POST"])
@login_required
def team_add_note(slug, user_id):
    def add(team):
        person = db.session.get(User, user_id)
        if person is None:
            raise ServiceError(_("That person does not exist."))
        teams_service.add_note(current_user, team, person, request.form.get("body"))

    return _lead_action(
        slug, teams_service.TeamPermission.WRITE_NOTES, add, _("Note saved."),
        back=url_for("teams.team_person", slug=slug, user_id=user_id),
    )


@teams_bp.route("/teams/<slug>/manage/settings", methods=["POST"])
@login_required
def team_lead_settings(slug):
    return _lead_action(
        slug, teams_service.TeamPermission.EDIT_SETTINGS,
        lambda team: (
            teams_service.update_team_by_lead(
                current_user, team,
                description=request.form.get("description"),
                application_prompt=request.form.get("application_prompt"),
                applications_open=request.form.get("applications_open") == "on",
            ),
            _apply_access_list(team, request.form),
            _apply_logo(team, request.form, request.files),
        ),
        _("Saved."),
        back=url_for("teams.team_manage", slug=slug),
    )


@teams_bp.route("/teams/<slug>/manage/export.csv", methods=["GET"])
@login_required
def team_export(slug):
    team = _team_or_404(slug)
    _may(team, teams_service.TeamPermission.EXPORT)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(teams_service.EXPORT_COLUMNS)
    writer.writerows(teams_service.export_rows(team))
    log_audit_event("teams", "team_members_exported", actor_user=current_user, metadata={"team": team.slug})
    db.session.commit()
    filename = f"{team.slug}-members-{get_membership_today().isoformat()}.csv"
    # A byte-order mark, so Excel opens umlauts as umlauts.
    return Response(
        "﻿" + buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@teams_bp.route("/teams/<slug>/manage/access-list", methods=["GET"])
@login_required
def team_access_list(slug):
    """The email exactly as it would go out, with a button to send it now."""
    team = _team_or_404(slug)
    _may(team, teams_service.TeamPermission.SEND_ACCESS_LIST)
    subject, message = teams_service.access_list_message(team)
    return render_template(
        "teams/access_list.html",
        team=team,
        subject=subject,
        message=message,
        recipients=teams_service.parse_recipients(team.access_list_recipients),
        cc=teams_service.access_list_cc(team),
        missing_university_email=sum(1 for row in message["rows"] if not row["email"]),
    )


@teams_bp.route("/teams/<slug>/manage/access-list/send", methods=["POST"])
@login_required
def team_access_list_send(slug):
    return _lead_action(
        slug, teams_service.TeamPermission.SEND_ACCESS_LIST,
        lambda team: teams_service.send_access_list(current_user, team),
        _("Sent."),
        back=url_for("teams.team_manage", slug=slug),
    )
