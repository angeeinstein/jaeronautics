"""A team's join page: what it does, a picture, its rules, and applying there.

Every signed-in visitor sees it, at /teams/<team>/about; the team's own page,
with who is in it, is for its members, and sends anybody else here.
A team with rules needs them ticked to apply or join, and the membership
keeps when and which version. Leads and site admins edit the page, but no
longer the rules: those are the association's files now (test_team_rules.py).
Rules typed into the portal before still apply, as here, until there is one.
"""
from io import BytesIO

import pytest

from conftest import app_module, db
from aeronautics_members.db_models import AuditLog, User
from aeronautics_members.services.clock import get_now_utc
from aeronautics_members.services import ValidationError, privacy, teams
from test_teams_flow import _led, _login, _person, switched_on  # noqa: F401
from test_teams_foundation import _in_team

RULES = "1. Safety briefing before every flight.\n2. Tools go back where they came from."


def _png(size=(2400, 1200)):
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGBA", size, (0, 200, 230, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def picture_dir(app, tmp_path):
    app.config["TEAM_PICTURE_DIR"] = str(tmp_path / "team_pictures")
    return tmp_path / "team_pictures"


def _with_rules(team, text=RULES):
    """Rules as a lead typed them into the portal, before they became files."""
    team.terms_text, team.terms_updated_at = text, get_now_utc()
    db.session.commit()
    return team


def _admin():
    user = User(email="admin@example.com")
    user.set_password("x")
    db.session.add(user)
    user.grant_role(app_module.get_role("admin"))
    db.session.commit()
    return user


@pytest.mark.usefixtures("switched_on")
class TestThePage:
    def test_shows_what_the_team_does_and_how_to_apply(self, app, client, picture_dir):
        team, _lead = _led()
        teams.update_team_page(None, team, about="We build rockets.\n\nEvery Tuesday.")
        teams.set_team_picture(None, team, _png())
        db.session.commit()
        _login(client, _person().id)

        body = client.get("/teams/rocket/about").get_data(as_text=True)

        assert "We build rockets." in body and f"/teams/picture/{team.picture_token}" in body
        assert 'action="/teams/rocket/join"' in body and 'name="accept_terms"' not in body
        picture = client.get(f"/teams/picture/{team.picture_token}")
        assert picture.status_code == 200 and picture.mimetype == "image/jpeg"

    def test_the_overview_leads_there(self, app, client):
        _led()
        _login(client, _person().id)

        body = client.get("/teams").get_data(as_text=True)

        assert 'href="/teams/rocket/about"' in body and 'action="/teams/rocket/join"' not in body

    def test_shows_the_rules_with_a_box_to_tick(self, app, client):
        team, _lead = _led()
        _with_rules(team)
        _login(client, _person().id)

        body = client.get("/teams/rocket/about").get_data(as_text=True)

        assert "Safety briefing before every flight." in body and 'name="accept_terms"' in body


@pytest.mark.usefixtures("switched_on")
class TestTheTeamsOwnPage:
    def test_sends_anybody_not_in_the_team_to_the_join_page(self, app, client):
        _led()
        _login(client, _person().id)

        assert client.get("/teams/rocket").headers["Location"].endswith("/teams/rocket/about")

    def test_shows_members_their_team_without_the_join_page(self, app, client, picture_dir):
        team, _lead = _led()
        teams.update_team_page(None, team, about="We build rockets.")
        _with_rules(team)
        teams.set_team_picture(None, team, _png())
        anna = _person()
        _in_team(anna, team)
        db.session.commit()
        _login(client, anna.id)

        body = client.get("/teams/rocket").get_data(as_text=True)

        assert "Lena Lead" in body and "Anna Berger" in body
        assert "We build rockets." not in body and team.picture_token not in body
        assert 'action="/teams/rocket/join"' not in body and 'href="/teams/rocket/about"' in body


class TestThePicture:
    def test_made_anew_as_a_jpeg_no_larger_than_needed(self, app, picture_dir):
        from PIL import Image

        team, _lead = _led()

        teams.set_team_picture(None, team, _png())

        with Image.open(teams.picture_file(team)) as stored:
            assert stored.format == "JPEG" and max(stored.size) == teams.PICTURE_MAX_SIDE

    def test_removed_with_its_file(self, app, picture_dir):
        team, _lead = _led()
        teams.set_team_picture(None, team, _png())
        path = teams.picture_file(team)

        teams.remove_team_picture(None, team)

        assert team.picture_token is None and not path.exists()

    def test_not_anything_but_a_picture(self, app, picture_dir):
        team, _lead = _led()

        with pytest.raises(ValidationError):
            teams.set_team_picture(None, team, b"%PDF-1.4 not a picture")


@pytest.mark.usefixtures("switched_on")
class TestTheRules:
    def test_needed_to_apply_and_kept_with_their_version(self, app, client):
        team, _lead = _led()
        _with_rules(team)
        anna = _person()
        _login(client, anna.id)

        refused = client.post("/teams/rocket/join", data={})
        assert refused.headers["Location"].endswith("/teams/rocket/about#join")
        assert teams.ongoing_membership(anna, team) is None

        client.post("/teams/rocket/join", data={"accept_terms": "on"})
        membership = teams.ongoing_membership(anna, team)
        assert membership.terms_accepted_at is not None
        assert membership.terms_version == team.terms_updated_at

    def test_also_to_join_an_open_team(self, app):
        team, _lead = _led(admission_mode="open")
        _with_rules(team)

        with pytest.raises(ValidationError, match="accept the rules"):
            teams.join_or_apply(_person(), team)

    def test_no_longer_changed_in_the_portal(self, app, client):
        team, lead = _led()
        _with_rules(team)
        version = team.terms_updated_at
        _login(client, lead.id)

        client.post("/teams/rocket/manage/settings", data={"section": "applying", "terms_text": "Anything goes."})
        body = client.get("/teams/rocket/manage").get_data(as_text=True)

        assert (team.terms_text, team.terms_updated_at) == (RULES, version)
        assert 'name="terms_text"' not in body and "send the new text to the association" in body

    def test_without_rules_nothing_is_ticked_or_kept(self, app):
        team, _lead = _led()

        membership = teams.join_or_apply(_person(), team)

        assert membership.terms_accepted_at is None and membership.terms_version is None

    def test_in_the_members_data(self, app):
        team, _lead = _led()
        _with_rules(team)
        anna = _person()
        teams.join_or_apply(anna, team, accepted_terms=True)
        db.session.commit()

        [membership] = privacy.export_account_data(anna)["teams"]["memberships"]

        assert membership["team_rules_accepted_at"] and membership["team_rules_version"]


@pytest.mark.usefixtures("switched_on")
class TestEditingThePage:
    def test_by_a_lead(self, app, client):
        team, lead = _led()
        _login(client, lead.id)

        client.post("/teams/rocket/manage/settings", data={"description": "Rockets.", "about": "We build rockets."})

        assert team.about == "We build rockets."
        assert db.session.query(AuditLog).filter_by(event_type="team_about_changed", actor_user_id=lead.id).count() == 1

    def test_not_by_an_ordinary_member(self, app, client):
        team, _lead = _led()
        anna = _person()
        _in_team(anna, team)
        _login(client, anna.id)

        response = client.post("/teams/rocket/manage/settings", data={"about": "Hijacked."})

        assert response.status_code == 403 and team.about is None

    def test_by_a_site_admin(self, app, client):
        team, _lead = _led()
        _login(client, _admin().id)

        # On the team's management page, like the leads; the admin form is for the rest.
        client.post("/teams/rocket/manage/settings", data={"section": "page", "about": "From the admins."})

        assert team.about == "From the admins."
        admin_form = client.get("/admin/teams/rocket").get_data(as_text=True)
        assert 'href="/teams/rocket/manage"' in admin_form and "Rules to accept" not in admin_form

    def test_the_admin_form_leaves_the_leads_part_be(self, app, client):
        team, _lead = _led(application_prompt="Why?")
        teams.update_team_by_lead(None, team, description="Rockets.", applications_open=False)
        _with_rules(team)
        _login(client, _admin().id)

        client.post("/admin/teams/rocket", data={
            "name": "Rocket Team", "admission_mode": "approval", "access_list_enabled": "on",
        })

        assert (team.name, team.description, team.application_prompt, team.applications_open, team.terms_text) == (
            "Rocket Team", "Rockets.", "Why?", False, RULES)
        assert team.access_list_enabled is True


@pytest.mark.usefixtures("switched_on")
class TestTheManagementPage:
    def test_one_section_saved_leaves_the_others_be(self, app, client):
        team, lead = _led(application_prompt="Why?")
        _with_rules(team)
        teams.update_team_page(None, team, about="We build rockets.")
        db.session.commit()
        _login(client, lead.id)

        client.post("/teams/rocket/manage/settings", data={"section": "page", "description": "Rockets.", "about": "New."})

        assert (team.description, team.about, team.terms_text, team.application_prompt) == (
            "Rockets.", "New.", RULES, "Why?")
        assert team.applications_open is True

    def test_back_to_the_section_saved(self, app, client):
        _team, lead = _led()
        _login(client, lead.id)

        response = client.post("/teams/rocket/manage/settings", data={"section": "applying", "applications_open": "on"})

        assert response.headers["Location"].endswith("/teams/rocket/manage#manage-applying")


@pytest.mark.usefixtures("switched_on")
class TestTheTreasurer:
    def test_a_lead_appoints_a_member_and_takes_it_back(self, app, client):
        team, lead = _led()
        anna = _person()
        _in_team(anna, team)
        _login(client, lead.id)

        client.post("/teams/rocket/manage/treasurer", data={"user_id": anna.id})
        assert teams.can_in_team(anna, team, teams.TeamPermission.VIEW_MONEY)
        assert db.session.query(AuditLog).filter_by(event_type="team_role_granted", target_user_id=anna.id).count() == 1

        client.post("/teams/rocket/manage/treasurer/remove", data={"user_id": anna.id})
        assert not teams.can_in_team(anna, team, teams.TeamPermission.VIEW_MONEY)

    def test_only_one_of_the_teams_members(self, app, client):
        team, lead = _led()
        outsider = _person("out@example.com", "Otto", "Outside")
        _login(client, lead.id)

        client.post("/teams/rocket/manage/treasurer", data={"user_id": outsider.id})

        assert not teams.role_holders(team, teams.ROLE_TREASURER)

    def test_not_by_the_treasurer_or_an_ordinary_member(self, app, client):
        team, _lead = _led()
        anna = _person()
        _in_team(anna, team)
        tim = _person("tim@example.com", "Tim", "Treasurer")
        _in_team(tim, team)
        teams.grant_team_role(None, team, tim, teams.ROLE_TREASURER)
        db.session.commit()

        for who in (anna, tim):
            _login(client, who.id)
            assert client.post("/teams/rocket/manage/treasurer", data={"user_id": anna.id}).status_code == 403


@pytest.mark.usefixtures("switched_on")
class TestTheOverview:
    def test_my_teams_first_the_others_to_read_about(self, app, client):
        rocket, _lead = _led()
        _led("Glider")
        anna = _person()
        _in_team(anna, rocket)
        _login(client, anna.id)

        body = client.get("/teams").get_data(as_text=True)

        mine, others = body.split('id="other-teams-heading"')
        assert "My teams" in mine and "Rocket" in mine and 'href="/teams/rocket"' in mine
        assert "Glider" in others and 'href="/teams/glider/about"' in others and "About & apply" in others

    def test_nothing_of_mine_just_the_teams(self, app, client):
        _led()
        _login(client, _person().id)

        body = client.get("/teams").get_data(as_text=True)

        assert "My teams" not in body and 'href="/teams/rocket/about"' in body
