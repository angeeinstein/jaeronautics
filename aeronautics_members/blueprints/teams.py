"""Teams: the pages for site admins, for members, and for team leads.

The admin pages are reachable whether or not teams are switched on -- they are
where they get switched on. Everything else exists only while the switch is
on. The rules are in services/teams.py; what was discussed and why is in
docs/teams-plan.md.
"""

import csv
import io
from datetime import timedelta

from flask import (
    Blueprint, Response, abort, current_app, flash, redirect, render_template, request, send_file, url_for,
)
from flask_babel import gettext as _
from flask_login import current_user, login_required

from ..app import requires
from ..db_models import User, db
from ..permissions import Permission
from ..services import ServiceError
from ..services import legal_texts as legal
from ..services import team_payments
from ..services import teams as teams_service
from ..services.audit import log_audit_event
from ..services.clock import get_membership_today
from . import _legal_pages as legal_pages
from .app_shell import app_shell

teams_bp = Blueprint("teams", __name__)


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


def _apply_page(team, form, files):
    """The team's page: its longer text and its picture -- each only if the
    form has it, so a form for one leaves the other be. (Its rules are files
    the association keeps: teams_service.team_rules.)"""
    if "about" in form:
        teams_service.update_team_page(current_user, team, about=form.get("about"))
    upload = files.get("picture")
    if upload is not None and upload.filename:
        teams_service.set_team_picture(current_user, team, upload.read())
    elif form.get("remove_picture") == "on":
        teams_service.remove_team_picture(current_user, team)


@teams_bp.route("/teams/picture/<token>", methods=["GET"])
def team_picture(token):
    """The picture on a team's page; like the logo, not secret."""
    path = teams_service.picture_file(teams_service.team_by_picture_token(token))
    if path is None:
        abort(404)
    return send_file(path, mimetype="image/jpeg", conditional=True, max_age=86400)


@teams_bp.route("/teams/logo/<token>", methods=["GET"])
def team_logo(token):
    """A team's logo. Not secret, so not behind a login: the token only keeps
    the address from being guessed while a logo is being replaced."""
    path = teams_service.logo_file(teams_service.team_by_logo_token(token))
    if path is None:
        abort(404)
    return send_file(path, mimetype="image/png", conditional=True, max_age=86400)


@teams_bp.route("/admin/teams", methods=["GET"])
@teams_bp.route("/admin/teams/new", methods=["GET"])
@teams_bp.route("/admin/teams/<slug>", methods=["GET"])
@login_required
@requires(Permission.TEAMS_MANAGE)
def admin_teams(slug=None):
    """Teams, the admins' part: drawn by the new front end (frontend/src/pages/admin/teams/)."""
    return app_shell()


def _teams_or_404():
    if not teams_service.teams_enabled():
        abort(404)


def _team_or_404(slug):
    # Site admins set a team up -- its page, its settings -- before teams are
    # switched on for everybody, so its pages are open to them already.
    if not current_user.can(Permission.TEAMS_MANAGE):
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


def _manages_people(team):
    """Money alone -- a treasurer's -- is not a say over the team's people."""
    return teams_service.can_in_team(current_user, team, teams_service.TeamPermission.VIEW_MEMBERS)


def _sees_team_page(team):
    return _manages_people(team) or (
        teams_service.is_active_association_member(current_user)
        and teams_service.active_team_membership(current_user, team) is not None
    )


def _membership_context():
    """What the status and buttons of a person's team membership need --
    on the overview and on each team's page alike."""
    singular, plural = teams_service.team_labels()
    mine = {}
    for membership in teams_service.memberships_of(current_user):  # newest first
        # The latest attempt per team: somebody who left and applied again is
        # an applicant, not a former member.
        mine.setdefault(membership.team_id, membership)
    led = teams_service.teams_led_by(current_user)
    return dict(
        label_singular=singular,
        label_plural=plural,
        latest=mine,
        ongoing={team_id: membership for team_id, membership in mine.items()
                 if membership.status in teams_service.ONGOING},
        is_member=teams_service.is_active_association_member(current_user),
        why_not_joinable=lambda team: teams_service.why_not_joinable(current_user, team),
        # Ended unpaid not long ago: back by paying, no application.
        rejoin_until=lambda team: teams_service.rejoin_by_paying_until(current_user, team),
        status_labels=teams_service.STATUS_LABELS,
        manageable={team.id for team in led if _manages_people(team)},
        money_of={team.id for team in led
                  if teams_service.can_in_team(current_user, team, teams_service.TeamPermission.VIEW_MONEY)},
        charges=team_payments.charges,
        needs_to_pay=team_payments.needs_to_pay,
        team_rules=teams_service.team_rules,
        accepted_rules_in_force=teams_service.accepted_rules_in_force,
        renewal_open=team_payments.renewal_open,
        next_period_until=team_payments.next_period_until,
        timedelta_one_day=timedelta(days=1),
        joining_period=team_payments.joining_period,
        just_paid=request.args.get("paid"),
    )


@teams_bp.route("/teams", methods=["GET"])
@login_required
def teams_home():
    _teams_or_404()
    return render_template(
        "teams/home.html",
        teams=teams_service.all_teams(include_archived=False),
        **_membership_context(),
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


@teams_bp.route("/teams/<slug>/about", methods=["GET"])
@login_required
def team_about(slug):
    """What a team does, its rules, and applying or joining -- for everyone
    signed in. Its members' own page is team_page."""
    team = _team_or_404(slug)
    return render_template(
        "teams/about.html",
        team=team,
        sees_members=_sees_team_page(team),
        **_membership_context(),
    )


@teams_bp.route("/teams/<slug>/rules", methods=["GET"])
@teams_bp.route("/teams/<slug>/rules/<language>", methods=["GET"])
@teams_bp.route("/teams/<slug>/rules/<language>/<version>", methods=["GET"])
@login_required
def team_rules_text(slug, language=None, version=None):
    """A team's rules, as the association's texts are shown: the version in
    force or an earlier one, German or the English translation. ``?part=body``
    for the window over the join form."""
    team = _team_or_404(slug)
    return legal_pages.text_page(
        legal.TEAM_RULES, language, version, team=team.slug,
        url=lambda language, version: url_for("teams.team_rules_text", slug=team.slug,
                                              language=language, version=version),
        pdf_url=lambda version: url_for("teams.team_rules_pdf", slug=team.slug, version=version),
        crumbs=[(teams_service.team_labels()[1], url_for("teams.teams_home")),
                (team.name, url_for("teams.team_about", slug=team.slug))],
        label="Rules",
    )


@teams_bp.route("/teams/<slug>/rules/pdf", methods=["GET"])
@teams_bp.route("/teams/<slug>/rules/pdf/<version>", methods=["GET"])
@login_required
def team_rules_pdf(slug, version=None):
    """A team's rules as a PDF, with its logo: German, then the English translation."""
    team = _team_or_404(slug)
    return legal_pages.pdf(
        legal.TEAM_RULES, version, team=team.slug, owner=team,
        back=url_for("teams.team_rules_text", slug=team.slug, language=legal.AUTHORITATIVE, version=version),
    )


@teams_bp.route("/teams/<slug>/join", methods=["POST"])
@login_required
def team_join(slug):
    team, done = _member_action(
        slug, lambda team: teams_service.join_or_apply(
            current_user, team, request.form.get("application_text"),
            accepted_terms=request.form.get("accept_terms") == "on",
        )
    )
    if not done:
        return redirect(url_for("teams.team_about", slug=team.slug) + "#join")
    current = teams_service.ongoing_membership(current_user, team)
    if current is not None and current.status == teams_service.APPROVED:
        flash(_("One step left: pay the team fee."), "success")
    elif team.admission_mode == teams_service.ADMISSION_OPEN:
        flash(_("Welcome to %(team)s.", team=team.name), "success")
        return redirect(url_for("teams.team_page", slug=team.slug))
    else:
        flash(_("Application sent. The leads will be in touch."), "success")
    return redirect(url_for("teams.team_about", slug=team.slug))


@teams_bp.route("/teams/<slug>/pay", methods=["POST"])
@login_required
def team_pay(slug):
    """Off to Stripe Checkout, to pay the team fee."""
    from ..services.team_payments import start_checkout

    team = _team_or_404(slug)
    try:
        checkout_url = start_checkout(current_user, team)
    except ServiceError as error:
        db.session.rollback()
        flash(error.message, "danger")
        return redirect(url_for("teams.teams_home"))
    except Exception:  # noqa: BLE001 -- Stripe unreachable or refusing; logged
        db.session.rollback()
        current_app.logger.exception("Could not open a team Checkout for %s.", slug)
        flash(_("The payment page could not be opened. Please try again in a few minutes."), "danger")
        return redirect(url_for("teams.teams_home"))
    db.session.commit()
    return redirect(checkout_url, code=303)


@teams_bp.route("/teams/<slug>/stay", methods=["POST"])
@login_required
def team_stay(slug):
    _team, done = _member_action(slug, lambda team: teams_service.stay(current_user, team))
    if done:
        flash(_("You stay. Nothing changes."), "success")
    return redirect(url_for("teams.teams_home"))


@teams_bp.route("/teams/<slug>/withdraw", methods=["POST"])
@login_required
def team_withdraw(slug):
    _team, done = _member_action(slug, lambda team: teams_service.withdraw(current_user, team))
    if done:
        flash(_("Application withdrawn."), "success")
    return redirect(url_for("teams.teams_home"))


@teams_bp.route("/teams/<slug>/leave", methods=["GET", "POST"])
@login_required
def team_leave(slug):
    """Leaving is a page of its own: what it means, then a deliberate yes."""
    team = _team_or_404(slug)
    current = teams_service.ongoing_membership(current_user, team)
    if current is None or current.status != teams_service.ACTIVE:
        flash(_("You are not a member of this team."), "info")
        return redirect(url_for("teams.teams_home"))
    if request.method == "GET" or request.form.get("confirm") != "on":
        if request.method == "POST":
            flash(_("Tick the box to confirm that you want to leave."), "warning")
        return render_template(
            "teams/leave.html",
            team=team,
            membership=current,
            is_lead=any(team_role.role == teams_service.ROLE_LEAD
                        for team_role in teams_service.roles_of(current_user) if team_role.team_id == team.id),
            runs_to_end=bool(current.stripe_subscription_id and current.payment_mode == "subscription"),
            paid_once_until=(current.paid_until if current.payment_mode == "one_time" and current.paid_until
                             and current.paid_until >= get_membership_today() else None),
            message=request.form.get("message", ""),
        )
    team, done = _member_action(
        slug, lambda team: teams_service.leave(current_user, team, request.form.get("message"))
    )
    if done:
        current = teams_service.ongoing_membership(current_user, team)
        if current is not None and current.ends_on is not None:
            from ..services.membership import format_membership_date_display

            flash(_("You leave %(team)s on %(day)s. Until then nothing changes.",
                    team=team.name, day=format_membership_date_display(current.ends_on)), "success")
        else:
            flash(_("You have left %(team)s.", team=team.name), "success")
    return redirect(url_for("teams.teams_home"))


@teams_bp.route("/teams/<slug>", methods=["GET"])
@login_required
def team_page(slug):
    """The team's own page, for its members: their membership and who is in
    the team. Anybody else is shown what the team does and how to join.

    Built as sections so that more can be added -- documents, dates, a drinks
    balance -- without reworking it.
    """
    team = _team_or_404(slug)
    if not _sees_team_page(team):
        return redirect(url_for("teams.team_about", slug=team.slug))
    return render_template(
        "teams/team.html",
        team=team,
        roster=teams_service.roster(team),
        my_membership=teams_service.active_team_membership(current_user, team),
        can_manage=_manages_people(team),
        **_membership_context(),
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
        former=teams_service.former_members(team),
        status_labels=teams_service.STATUS_LABELS,
        end_reasons=teams_service.END_REASON_LABELS,
        person_details=teams_service.person_details,
        has_lead_in_force=teams_service.has_lead_in_force(team),
        access_list_recipients=teams_service.parse_recipients(team.access_list_recipients),
        next_access_list_date=teams_service.next_access_list_date(team),
        charges=team_payments.charges(team),
        renewal_open=team_payments.renewal_open,
        needs_to_pay=team_payments.needs_to_pay,
        leads=teams_service.role_holders(team, teams_service.ROLE_LEAD),
        treasurers=teams_service.role_holders(team, teams_service.ROLE_TREASURER),
        role_counts=teams_service.role_counts,
        team_rules=teams_service.team_rules,
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


#: The parts of the leads' settings, each saved by a form of its own.
SETTINGS_SECTIONS = ("page", "applying", "access_list")


@teams_bp.route("/teams/<slug>/manage/settings", methods=["POST"])
@login_required
def team_lead_settings(slug):
    """Save one section of the leads' settings -- or, without a section, all."""
    form = request.form
    section = form.get("section")
    sections = {section} if section in SETTINGS_SECTIONS else set(SETTINGS_SECTIONS)

    def save(team):
        keep = teams_service.KEEP
        if "page" in sections:
            teams_service.update_team_by_lead(current_user, team, description=form.get("description"))
            _apply_logo(team, form, request.files)
        if "applying" in sections:
            teams_service.update_team_by_lead(
                current_user, team,
                application_prompt=form.get("application_prompt") if section or "application_prompt" in form else keep,
                applications_open=form.get("applications_open") == "on",
            )
        # The texts and the picture: whichever of them the form carries.
        _apply_page(team, form, request.files)
        if "access_list" in sections and team.access_list_enabled:
            _apply_access_list(team, form)

    anchor = {"page": "#manage-page", "applying": "#manage-applying", "access_list": "#manage-access-list"}
    return _lead_action(
        slug, teams_service.TeamPermission.EDIT_SETTINGS, save, _("Saved."),
        back=url_for("teams.team_manage", slug=slug) + anchor.get(section, ""),
    )


@teams_bp.route("/teams/<slug>/manage/treasurer", methods=["POST"])
@login_required
def team_appoint_treasurer(slug):
    return _lead_action(
        slug, teams_service.TeamPermission.APPOINT_TREASURER,
        lambda team: teams_service.appoint_treasurer(current_user, team, request.form.get("user_id", type=int)),
        _("Treasurer appointed."),
        back=url_for("teams.team_manage", slug=slug) + "#manage-roles",
    )


@teams_bp.route("/teams/<slug>/manage/treasurer/remove", methods=["POST"])
@login_required
def team_dismiss_treasurer(slug):
    return _lead_action(
        slug, teams_service.TeamPermission.APPOINT_TREASURER,
        lambda team: teams_service.dismiss_treasurer(current_user, team, request.form.get("user_id", type=int)),
        _("No longer treasurer."),
        back=url_for("teams.team_manage", slug=slug) + "#manage-roles",
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
    if not team.access_list_enabled:
        abort(404)
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


# --- Money ---------------------------------------------------------------------
#
# What a team's members paid and what the association passed on. Open to the
# team's leads and treasurers, and to the association's treasurer and admins
# for every team -- also for an archived one, and while teams are switched
# off, since what is owed stays owed.


def _money_team_or_404(slug):
    if not current_user.can(Permission.TEAMS_MONEY):
        team = _team_or_404(slug)
    else:
        try:
            team = teams_service.get_team(slug)
        except ServiceError:
            abort(404)
    _may(team, teams_service.TeamPermission.VIEW_MONEY)
    return team


@teams_bp.route("/teams/<slug>/money", methods=["GET"])
@login_required
def team_money(slug):
    from ..services import team_money as money

    team = _money_team_or_404(slug)
    permissions = teams_service.team_permissions(current_user, team)
    return render_template(
        "teams/money.html",
        team=team,
        summary=money.summary(team),
        permissions=permissions,
        TeamPermission=teams_service.TeamPermission,
        manages_people=_manages_people(team),
        # Transfers are recorded on the association's side of it.
        pays_out=current_user.can(Permission.TEAMS_MONEY),
        euros=money.euros,
        counts=money.counts,
        payer_name=money.payer_name,
        masked_iban=money.masked_iban,
    )


@teams_bp.route("/teams/<slug>/money.csv", methods=["GET"])
@login_required
def team_money_export(slug):
    from ..services import team_money as money

    team = _money_team_or_404(slug)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(money.EXPORT_COLUMNS)
    writer.writerows(money.export_rows(team))
    log_audit_event("payments", "team_payments_exported", actor_user=current_user, metadata={"team": team.slug})
    db.session.commit()
    filename = f"{team.slug}-payments-{get_membership_today().isoformat()}.csv"
    return Response(
        "﻿" + buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _money_action(slug, permission, action, success_message):
    team = _money_team_or_404(slug)
    _may(team, permission)
    try:
        changed = action(team)
    except ServiceError as error:
        db.session.rollback()
        flash(error.message, "danger")
    else:
        db.session.commit()
        from ..services.notifications import flush_marked_notification_channels

        flush_marked_notification_channels()
        flash(success_message if changed is not False else _("Nothing changed."),
              "success" if changed is not False else "info")
    return redirect(url_for("teams.team_money", slug=team.slug))


@teams_bp.route("/teams/<slug>/money/bank", methods=["POST"])
@login_required
def team_money_bank(slug):
    from ..services.team_money import update_bank_details

    return _money_action(
        slug, teams_service.TeamPermission.EDIT_BANK_DETAILS,
        lambda team: update_bank_details(
            current_user, team,
            account_holder=request.form.get("account_holder"),
            iban=request.form.get("iban"),
            bic=request.form.get("bic"),
        ),
        _("Bank details saved."),
    )


@teams_bp.route("/admin/money", methods=["GET"])
@login_required
@requires(Permission.TEAMS_MONEY)
def admin_money():
    """Money, the association's side: drawn by the new front end (frontend/src/pages/admin/money/)."""
    return app_shell()


@teams_bp.route("/admin/money/<slug>", methods=["GET"])
@login_required
@requires(Permission.TEAMS_MONEY)
def admin_team_money(slug):
    return app_shell()


@teams_bp.route("/admin/money/<slug>/transfer-code.svg", methods=["GET"])
@login_required
@requires(Permission.TEAMS_MONEY)
def admin_team_money_code(slug):
    """The GiroCode for a transfer to the team: ``amount`` in cents, and the ``reference``.
    A banking app that scans it has the whole transfer filled in."""
    from ..services import team_money as money

    try:
        team = teams_service.get_team(slug)
    except ServiceError:
        abort(404)
    cents = request.args.get("amount", type=int) or 0
    svg = money.payout_qr_svg(team, cents, (request.args.get("reference") or "")[:140])
    if svg is None:
        abort(404)
    return Response(svg, mimetype="image/svg+xml", headers={"Cache-Control": "private, no-store"})
