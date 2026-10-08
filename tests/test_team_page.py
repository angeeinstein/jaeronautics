"""A team's join page: what it does, a picture, its rules, and applying there.

Every signed-in visitor sees it, at /teams/<team>/about; the team's own page,
with who is in it, is for its members, and sends anybody else here.
A team with rules needs them ticked to apply or join, and the membership
keeps when and which version. Leads and site admins edit the page, but no
longer the rules: those are the association's files (test_team_rules.py).
"""
from datetime import datetime, time
from io import BytesIO

import pytest

from api_helpers import send
from conftest import app_module, db
from aeronautics_members.db_models import AuditLog, User
from aeronautics_members.services.clock import get_membership_today
from aeronautics_members.services import ValidationError, privacy, teams
from test_legal_texts import legal_dir  # noqa: F401 -- fixture
from test_teams_flow import _led, _login, _person, switched_on  # noqa: F401
from test_teams_foundation import _in_team

API = "/api/v1/teams"
RULES = "1. Safety briefing before every flight.\n2. Tools go back where they came from."
RULES_DAY = datetime.combine(get_membership_today(), time())


def _png(size=(2400, 1200)):
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGBA", size, (0, 200, 230, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def picture_dir(app, tmp_path):
    app.config["TEAM_PICTURE_DIR"] = str(tmp_path / "team_pictures")
    return tmp_path / "team_pictures"


@pytest.fixture
def with_rules(legal_dir):  # noqa: F811
    """``put(team)``: the team's rules as the association keeps them, in force from today."""

    def put(team, text=RULES):
        day = get_membership_today().isoformat()
        folder = legal_dir / "teams" / team.slug / "team-rules" / "de"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{day}.md").write_text(
            f'---\ntitle: "Teamordnung"\ndocument: "team-rules"\nlanguage: "de"\nteam: "{team.slug}"\n'
            f'version: "{day}"\neffective_from: "{day}"\nstatus: "published"\n---\n\n{text}\n', encoding="utf-8")
        return team

    return put


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

        body = client.get(f"{API}/rocket").get_json()

        assert body["about"].startswith("We build rockets.")
        assert body["picture_url"] == f"/teams/picture/{team.picture_token}"
        assert body["joining"]["submit_label"] == "Apply" and body["rules"] is None
        assert body["members"] is None, "only its members see who is in it"
        picture = client.get(f"/teams/picture/{team.picture_token}")
        assert picture.status_code == 200 and picture.mimetype == "image/jpeg"

    def test_the_overview_leads_there(self, app, client):
        _led()
        _login(client, _person().id)

        [card] = client.get(API).get_json()["others"]

        assert card["opens"] == "about" and card["about_label"] == "About & apply"

    def test_shows_the_rules_with_a_box_to_tick(self, app, client, with_rules):
        team, _lead = _led()
        with_rules(team)
        _login(client, _person().id)

        body = client.get(f"{API}/rocket").get_json()

        assert body["rules"]["version"] == get_membership_today().isoformat()
        assert body["rules"]["accepted"] is None


@pytest.mark.usefixtures("switched_on")
class TestTheTeamsOwnPage:
    def test_sends_anybody_not_in_the_team_to_the_join_page(self, app, client):
        _led()
        _login(client, _person().id)

        # The page itself is the app's; it goes to /about when the answer says so.
        assert client.get("/teams/rocket").status_code == 200
        assert client.get(f"{API}/rocket").get_json()["sees_team_page"] is False

    def test_shows_members_their_team_without_the_join_page(self, app, client, picture_dir, with_rules):
        team, _lead = _led()
        teams.update_team_page(None, team, about="We build rockets.")
        with_rules(team)
        teams.set_team_picture(None, team, _png())
        anna = _person()
        _in_team(anna, team)
        db.session.commit()
        _login(client, anna.id)

        body = client.get(f"{API}/rocket").get_json()

        assert body["sees_team_page"] is True and body["joining"] is None
        assert {person["name"] for person in body["members"]} >= {"Lena Lead", "Anna Berger"}
        assert body["membership"]["status"] == "active" and "leave" in body["membership"]["actions"]


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
    def test_needed_to_apply_and_kept_with_their_version(self, app, client, with_rules):
        team, _lead = _led()
        with_rules(team)
        anna = _person()
        _login(client, anna.id)

        refused = send(client, "POST", f"{API}/rocket/join", {})
        assert refused.status_code == 400
        assert teams.ongoing_membership(anna, team) is None

        send(client, "POST", f"{API}/rocket/join", {"accept_rules": True})
        membership = teams.ongoing_membership(anna, team)
        assert membership.terms_accepted_at is not None
        assert membership.terms_version == RULES_DAY

    def test_also_to_join_an_open_team(self, app, with_rules):
        team, _lead = _led(admission_mode="open")
        with_rules(team)

        with pytest.raises(ValidationError, match="accept the rules"):
            teams.join_or_apply(_person(), team)

    def test_not_changed_in_the_portal(self, app, client, with_rules):
        team, lead = _led()
        with_rules(team)
        _login(client, lead.id)

        applying = client.get(f"{API}/rocket/manage/applying").get_json()

        assert applying["rules"]["page_url"] == "/teams/rocket/rules"
        # The rules are not a field of the leads' settings: a request with them is refused.
        assert send(client, "PUT", f"{API}/rocket/manage/applying",
                    {"applications_open": True, "terms_text": "Changed."}).status_code == 400
        assert "Changed." not in teams.team_rules(team).version.path.read_text()

    def test_without_rules_nothing_is_ticked_or_kept(self, app):
        team, _lead = _led()

        membership = teams.join_or_apply(_person(), team)

        assert membership.terms_accepted_at is None and membership.terms_version is None

    def test_in_the_members_data(self, app, with_rules):
        team, _lead = _led()
        with_rules(team)
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

        send(client, "PUT", f"{API}/rocket/manage/page", {"description": "Rockets.", "about": "We build rockets."})

        assert team.about == "We build rockets."
        assert db.session.query(AuditLog).filter_by(event_type="team_about_changed", actor_user_id=lead.id).count() == 1

    def test_not_by_an_ordinary_member(self, app, client):
        team, _lead = _led()
        anna = _person()
        _in_team(anna, team)
        _login(client, anna.id)

        response = send(client, "PUT", f"{API}/rocket/manage/page", {"about": "Hijacked."})

        assert response.status_code == 403 and team.about is None

    def test_by_a_site_admin(self, app, client):
        team, _lead = _led()
        _login(client, _admin().id)

        # On the team's management page, like the leads; the admin form is for the rest.
        send(client, "PUT", f"{API}/rocket/manage/page", {"description": None, "about": "From the admins."})

        assert team.about == "From the admins."
        admin_view = client.get("/api/v1/admin/teams/rocket").get_json()
        assert admin_view["manage_url"] == "/teams/rocket/manage"
        assert "about" not in admin_view and "rules" not in admin_view, "the leads' part is theirs"

    def test_the_admin_form_leaves_the_leads_part_be(self, app, client):
        team, _lead = _led(application_prompt="Why?")
        teams.update_team_by_lead(None, team, description="Rockets.", applications_open=False)
        _login(client, _admin().id)

        send(client, "PUT", "/api/v1/admin/teams/rocket", {
            "name": "Rocket Team", "admission_mode": "approval", "access_list_enabled": True,
        })

        assert (team.name, team.description, team.application_prompt, team.applications_open) == (
            "Rocket Team", "Rockets.", "Why?", False)
        assert team.access_list_enabled is True


@pytest.mark.usefixtures("switched_on")
class TestTheManagementPage:
    def test_one_section_saved_leaves_the_others_be(self, app, client):
        team, lead = _led(application_prompt="Why?")
        teams.update_team_page(None, team, about="We build rockets.")
        db.session.commit()
        _login(client, lead.id)

        send(client, "PUT", f"{API}/rocket/manage/page", {"description": "Rockets.", "about": "New."})

        assert (team.description, team.about, team.application_prompt) == ("Rockets.", "New.", "Why?")
        assert team.applications_open is True

    def test_applying_saved_on_its_own(self, app, client):
        team, lead = _led(application_prompt="Why?")
        teams.update_team_page(None, team, about="We build rockets.")
        db.session.commit()
        _login(client, lead.id)

        body = send(client, "PUT", f"{API}/rocket/manage/applying",
                    {"applications_open": False, "application_prompt": "Why rockets?"}).get_json()

        assert body["applications_open"] is False and body["application_prompt"] == "Why rockets?"
        assert team.about == "We build rockets."

    def test_the_short_description_is_one_line(self, app, client):
        _team, lead = _led()
        _login(client, lead.id)

        too_long = send(client, "PUT", f"{API}/rocket/manage/page", {"description": "x" * 161, "about": None})
        assert too_long.status_code == 400
        assert send(client, "PUT", f"{API}/rocket/manage/page", {"description": "x" * 160, "about": None}).status_code == 200

    def test_an_old_link_to_a_section_opens_the_app(self, app, client):
        _team, lead = _led()
        _login(client, lead.id)

        for path in ("/teams/rocket/manage", "/teams/rocket/manage/applying", "/teams/rocket/manage/roles"):
            assert client.get(path).status_code == 200, path


@pytest.mark.usefixtures("switched_on")
class TestTheTreasurer:
    def test_a_lead_appoints_a_member_and_takes_it_back(self, app, client):
        team, lead = _led()
        anna = _person()
        _in_team(anna, team)
        _login(client, lead.id)

        roles = send(client, "POST", f"{API}/rocket/manage/treasurer", {"user_id": anna.id}).get_json()
        assert [holder["name"] for holder in roles["treasurers"]] == ["Anna Berger"]
        assert teams.can_in_team(anna, team, teams.TeamPermission.VIEW_MONEY)
        assert db.session.query(AuditLog).filter_by(event_type="team_role_granted", target_user_id=anna.id).count() == 1

        send(client, "DELETE", f"{API}/rocket/manage/treasurer/{anna.id}")
        assert not teams.can_in_team(anna, team, teams.TeamPermission.VIEW_MONEY)

    def test_only_one_of_the_teams_members(self, app, client):
        team, lead = _led()
        outsider = _person("out@example.com", "Otto", "Outside")
        _login(client, lead.id)

        response = send(client, "POST", f"{API}/rocket/manage/treasurer", {"user_id": outsider.id})

        assert response.status_code == 400 and not teams.role_holders(team, teams.ROLE_TREASURER)

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
            assert send(client, "POST", f"{API}/rocket/manage/treasurer", {"user_id": anna.id}).status_code == 403


@pytest.mark.usefixtures("switched_on")
class TestTheOverview:
    def test_my_teams_first_the_others_to_read_about(self, app, client):
        rocket, _lead = _led()
        _led("Glider")
        anna = _person()
        _in_team(anna, rocket)
        _login(client, anna.id)

        body = client.get(API).get_json()

        assert [(card["slug"], card["opens"]) for card in body["mine"]] == [("rocket", "team")]
        assert [(card["slug"], card["opens"], card["about_label"]) for card in body["others"]] == [
            ("glider", "about", "About & apply")]

    def test_nothing_of_mine_just_the_teams(self, app, client):
        _led()
        _login(client, _person().id)

        body = client.get(API).get_json()

        assert body["mine"] == [] and [card["slug"] for card in body["others"]] == ["rocket"]
