"""Teams: the pages for site admins, for members, and for team leads.

The admin pages are reachable whether or not teams are switched on -- they are
where they get switched on. Everything else exists only while the switch is
on. The rules are in services/teams.py; what was discussed and why is in
docs/teams-plan.md.
"""

import csv
import io

from flask import (
    Blueprint, Response, abort, request, send_file, url_for,
)
from flask_login import current_user, login_required

from ..app import requires
from ..db_models import db
from ..permissions import Permission
from ..services import ServiceError
from ..services import legal_texts as legal
from ..services import teams as teams_service
from ..services.audit import log_audit_event
from ..services.clock import get_membership_today
from . import _legal_pages as legal_pages
from .app_shell import app_shell

teams_bp = Blueprint("teams", __name__)


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


@teams_bp.route("/teams", methods=["GET"])
@login_required
def teams_home():
    """The overview: drawn by the new front end (frontend/src/pages/teams/Teams.tsx)."""
    _teams_or_404()
    return app_shell()


@teams_bp.route("/teams/<slug>", methods=["GET"])
@teams_bp.route("/teams/<slug>/about", methods=["GET"])
@teams_bp.route("/teams/<slug>/leave", methods=["GET"])
@login_required
def team_page(slug):
    """A team's own page, what it is about (with joining), and leaving it:
    drawn by the new front end (frontend/src/pages/teams/)."""
    _team_or_404(slug)
    return app_shell()


@teams_bp.route("/teams/<slug>/rules", methods=["GET"])
@teams_bp.route("/teams/<slug>/rules/<any(de, en):language>", methods=["GET"])
@teams_bp.route("/teams/<slug>/rules/<any(de, en):language>/<version>", methods=["GET"])
@login_required
def team_rules_text(slug, language=None, version=None):
    """A team's rules -- the version in force or an earlier one, German or the
    English translation: drawn by the new front end (link in emails)."""
    _team_or_404(slug)
    return app_shell()


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


# --- For leads ----------------------------------------------------------------


@teams_bp.route("/teams/<slug>/manage", methods=["GET"])
@teams_bp.route("/teams/<slug>/manage/<any(members, former, page, applying, roles):section>", methods=["GET"])
@login_required
def team_manage(slug, section=None):
    """A team's management, for its leads: drawn by the new front end
    (frontend/src/pages/teams/manage/). An old link to a tab
    (``#manage-applying``) opens that section's page."""
    team = _team_or_404(slug)
    _may(team, teams_service.TeamPermission.VIEW_MEMBERS)
    return app_shell()


@teams_bp.route("/teams/<slug>/manage/people/<int:user_id>", methods=["GET"])
@login_required
def team_person(slug, user_id):
    """A person, as the team's leads see them: drawn by the new front end."""
    return team_manage(slug)


@teams_bp.route("/teams/<slug>/manage/access-list", methods=["GET"])
@login_required
def team_access_list(slug):
    """The access list -- who receives it, and the email as it would go out:
    drawn by the new front end."""
    team = _team_or_404(slug)
    _may(team, teams_service.TeamPermission.SEND_ACCESS_LIST)
    if not team.access_list_enabled:
        abort(404)
    return app_shell()


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
    """A team's money, the team's side: drawn by the new front end (frontend/src/pages/teams/manage/Money.tsx)."""
    _money_team_or_404(slug)
    return app_shell()


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
