"""Teams, step one: the switch, the teams themselves, and who leads them.

Teams are off until an admin switches them on, are created in the portal
rather than in code, and are led by people who hold a role in one team only.
A role counts while its holder is a member of the association and of the team;
site admins can always do everything, which is what keeps a team whose leads
have all lapsed manageable. See docs/teams-plan.md.
"""
from datetime import timedelta

import pytest

from conftest import db, make_member
from aeronautics_members.db_models import AuditLog, TeamMembership, TeamRole
from aeronautics_members.services import ConflictError, ValidationError
from aeronautics_members.services import teams
from aeronautics_members.services.clock import get_membership_today
from aeronautics_members.services.teams import TeamPermission
from test_admin_reviews import _login, _staff


def _association_member(email, *, active=True):
    today = get_membership_today()
    member = make_member(
        email=email,
        payment_status="paid" if active else "unpaid",
        is_active=active,
        membership_starts_on=today - timedelta(days=30),
        membership_ends_on=today + timedelta(days=60) if active else today - timedelta(days=1),
    )
    return member.user


def _team(name="Rocket Team", **overrides):
    slug = overrides.pop("slug", None)
    fields = dict(
        name=name, description=None, admission_mode="approval", applications_open=True,
        application_prompt=None, max_members=None, forum_group=None,
    )
    fields.update(overrides)
    team = teams.create_team(None, slug=slug, **fields)
    db.session.commit()
    return team


def _in_team(user, team):
    db.session.add(TeamMembership(team=team, user=user, status=teams.ACTIVE))
    db.session.commit()


class TestTheSwitch:
    def test_off_until_switched_on(self, app):
        assert teams.teams_enabled() is False

        teams.save_team_settings(None, enabled=True, label_singular="", label_plural="")
        db.session.commit()

        assert teams.teams_enabled() is True

    def test_called_teams_unless_named_otherwise(self, app):
        assert teams.team_labels() == ("Team", "Teams")

        teams.save_team_settings(None, enabled=False, label_singular="Section", label_plural="Sections")
        db.session.commit()

        assert teams.team_labels() == ("Section", "Sections")

    def test_saving_the_same_thing_records_nothing(self, app):
        assert teams.save_team_settings(None, enabled=False, label_singular="Team", label_plural="Teams") is False
        assert db.session.query(AuditLog).filter_by(category="teams").count() == 0


class TestCreatingTeams:
    def test_the_short_name_comes_from_the_name(self, app):
        assert _team("Rocket Team").slug == "rocket-team"

    def test_a_typed_short_name_is_taken_in_lowercase(self, app):
        assert _team("Rocket", slug="Rocket").slug == "rocket"

    @pytest.mark.parametrize("slug", ["rocket team", "-rocket", "rock--et", "ü", "a" * 61])
    def test_a_short_name_is_letters_digits_and_hyphens(self, app, slug):
        with pytest.raises(ValidationError):
            _team(slug=slug)

    def test_two_teams_cannot_share_a_short_name(self, app):
        _team("Rocket")
        with pytest.raises(ConflictError):
            _team("Rocket")

    @pytest.mark.parametrize("overrides", [
        {"name": "  "},
        {"admission_mode": "lottery"},
        {"max_members": "0"},
        {"max_members": "ten"},
    ])
    def test_what_does_not_make_sense_is_refused(self, app, overrides):
        with pytest.raises(ValidationError):
            _team(**overrides)

    def test_the_short_name_cannot_be_changed(self, app):
        team = _team("Rocket")

        teams.update_team(
            None, team, name="Rocket Science", description="We build rockets.",
            admission_mode="open", applications_open=False, application_prompt=None,
            max_members="12", forum_group=None,
        )
        db.session.commit()

        assert (team.slug, team.name, team.max_members, team.applications_open) == ("rocket", "Rocket Science", 12, False)

    def test_archiving_keeps_the_team(self, app):
        team = _team("Rocket")

        teams.set_team_archived(None, team, True, confirmed_name=team.name)
        db.session.commit()

        assert team.status == teams.STATUS_ARCHIVED and team.archived_at is not None
        teams.set_team_archived(None, team, False)
        assert team.status == teams.STATUS_ACTIVE and team.archived_at is None


class TestWhatALeadMayDo:
    def test_a_lead_in_the_team_may_do_everything_there(self, app):
        team = _team()
        lead = _association_member("lead@example.com")
        _in_team(lead, team)
        teams.grant_team_role(None, team, lead, teams.ROLE_LEAD)

        assert teams.team_permissions(lead, team) == TeamPermission.ALL

    def test_a_lead_not_yet_in_the_team_may_do_nothing_yet(self, app):
        team = _team()
        lead = _association_member("lead@example.com")
        teams.grant_team_role(None, team, lead, teams.ROLE_LEAD)

        assert teams.team_permissions(lead, team) == frozenset()

    def test_a_lapsed_association_membership_pauses_the_role(self, app):
        team = _team()
        lead = _association_member("lead@example.com", active=False)
        _in_team(lead, team)
        teams.grant_team_role(None, team, lead, teams.ROLE_LEAD)

        assert teams.team_permissions(lead, team) == frozenset()
        assert db.session.query(TeamRole).count() == 1, "paused, not taken away"

    def test_leading_one_team_gives_nothing_in_another(self, app):
        rocket, glider = _team("Rocket"), _team("Glider")
        lead = _association_member("lead@example.com")
        _in_team(lead, rocket)
        _in_team(lead, glider)
        teams.grant_team_role(None, rocket, lead, teams.ROLE_LEAD)

        assert teams.team_permissions(lead, glider) == frozenset()
        assert teams.teams_led_by(lead) == [rocket]

    def test_an_ordinary_team_member_may_do_nothing_there(self, app):
        team = _team()
        member = _association_member("member@example.com")
        _in_team(member, team)

        assert teams.team_permissions(member, team) == frozenset()

    def test_an_archived_team_is_closed_to_its_leads_but_not_to_admins(self, app):
        team = _team()
        lead = _association_member("lead@example.com")
        _in_team(lead, team)
        teams.grant_team_role(None, team, lead, teams.ROLE_LEAD)
        admin = _staff("admin@example.com", "admin")
        teams.set_team_archived(None, team, True, confirmed_name=team.name)

        assert teams.team_permissions(lead, team) == frozenset()
        assert teams.team_permissions(admin, team) == TeamPermission.ALL

    def test_site_admins_may_do_everything_in_every_team(self, app):
        assert teams.team_permissions(_staff("admin@example.com", "admin"), _team()) == TeamPermission.ALL

    def test_a_forum_moderator_is_not_a_site_admin_here(self, app):
        assert teams.team_permissions(_staff("mod@example.com", "forum_moderator"), _team()) == frozenset()


class TestRoles:
    def test_giving_a_role_twice_gives_it_once(self, app):
        team = _team()
        lead = _association_member("lead@example.com")

        teams.grant_team_role(None, team, lead, teams.ROLE_LEAD)
        teams.grant_team_role(None, team, lead, teams.ROLE_LEAD)

        assert db.session.query(TeamRole).count() == 1

    def test_only_roles_that_exist(self, app):
        with pytest.raises(ValidationError):
            teams.grant_team_role(None, _team(), _association_member("a@example.com"), "emperor")

    def test_the_last_lead_goes_only_once_confirmed(self, app):
        team = _team()
        lead = _association_member("lead@example.com")
        teams.grant_team_role(None, team, lead, teams.ROLE_LEAD)
        db.session.commit()

        with pytest.raises(ConflictError) as refused:
            teams.revoke_team_role(None, team, lead, teams.ROLE_LEAD)
        assert refused.value.code == "team_last_lead"

        assert teams.revoke_team_role(None, team, lead, teams.ROLE_LEAD, confirmed=True) is True
        assert db.session.query(TeamRole).count() == 0

    def test_one_of_two_leads_goes_without_asking(self, app):
        team = _team()
        first, second = _association_member("one@example.com"), _association_member("two@example.com")
        teams.grant_team_role(None, team, first, teams.ROLE_LEAD)
        teams.grant_team_role(None, team, second, teams.ROLE_LEAD)

        assert teams.revoke_team_role(None, team, first, teams.ROLE_LEAD) is True

    def test_whether_a_team_has_a_lead_in_force(self, app):
        team = _team()
        lead = _association_member("lead@example.com")
        teams.grant_team_role(None, team, lead, teams.ROLE_LEAD)
        assert teams.has_lead_in_force(team) is False

        _in_team(lead, team)
        assert teams.has_lead_in_force(team) is True


class TestTheAdminPages:
    def test_closed_to_members(self, app, client):
        member = _association_member("member@example.com")
        _login(client, member.id)

        response = client.get("/admin/teams")

        assert response.status_code == 302

    def test_an_admin_switches_teams_on_and_names_them(self, app, client):
        admin = _staff("admin@example.com", "admin")
        _login(client, admin.id)

        client.post("/admin/teams", data={"teams_enabled": "on", "label_singular": "Section", "label_plural": "Sections"})

        assert teams.teams_enabled() is True
        body = client.get("/admin/teams").get_data(as_text=True)
        assert "Sections" in body and "New Section" in body

    def test_an_admin_creates_a_team_and_appoints_a_lead(self, app, client):
        admin = _staff("admin@example.com", "admin")
        lead = _association_member("lead@example.com")
        _login(client, admin.id)

        response = client.post("/admin/teams/new", data={
            "name": "Rocket Team", "slug": "", "admission_mode": "approval", "applications_open": "on",
        })
        assert response.headers["Location"].endswith("/admin/teams/rocket-team")

        client.post("/admin/teams/rocket-team/roles", data={"email": "LEAD@example.com", "role": "lead"})
        body = client.get("/admin/teams/rocket-team").get_data(as_text=True)

        assert "lead@example.com" in body
        assert "Not yet a team member" in body
        assert db.session.query(TeamRole).filter_by(user_id=lead.id, role="lead").count() == 1

    def test_removing_the_last_lead_on_the_page_carries_the_confirmation(self, app, client):
        admin = _staff("admin@example.com", "admin")
        team = _team("Rocket")
        lead = _association_member("lead@example.com")
        teams.grant_team_role(None, team, lead, teams.ROLE_LEAD)
        db.session.commit()
        _login(client, admin.id)

        body = client.get("/admin/teams/rocket").get_data(as_text=True)
        assert "This is the last lead. Remove anyway?" in body

        client.post("/admin/teams/rocket/roles/revoke", data={"user_id": lead.id, "role": "lead", "confirmed": "1"})
        assert db.session.query(TeamRole).count() == 0

    def test_an_unknown_address_is_said_so(self, app, client):
        admin = _staff("admin@example.com", "admin")
        _team("Rocket")
        _login(client, admin.id)

        response = client.post("/admin/teams/rocket/roles", data={"email": "nobody@example.com", "role": "lead"},
                               follow_redirects=True)

        assert "No account uses that address." in response.get_data(as_text=True)

    def test_the_nav_shows_teams_to_admins(self, app, client):
        admin = _staff("admin@example.com", "admin")
        _login(client, admin.id)

        assert 'href="/admin/teams"' in client.get("/admin").get_data(as_text=True)

    def test_every_change_is_in_the_audit_log(self, app, client):
        admin = _staff("admin@example.com", "admin")
        _login(client, admin.id)
        client.post("/admin/teams/new", data={"name": "Rocket", "admission_mode": "open"})
        client.post("/admin/teams/rocket/archive", data={"archived": "1", "confirm_name": "Rocket"})

        events = {entry.event_type for entry in db.session.query(AuditLog).filter_by(category="teams")}
        assert {"team_created", "team_archived"} <= events
