"""A team's join page: what it does, a picture, its rules, and applying there.

Every signed-in visitor sees it, at /teams/<team>/about; the team's own page,
with who is in it, is for its members, and sends anybody else here.
A team with rules needs them ticked to apply or join, and the membership
keeps when and which version (the day the rules last changed). Leads and site
admins edit the page; every change to the rules is logged.
"""
from io import BytesIO

import pytest

from conftest import app_module, db
from aeronautics_members.db_models import AuditLog, User
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
    teams.update_team_page(None, team, about=team.about, terms_text=text)
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
        teams.update_team_page(None, team, about="We build rockets.\n\nEvery Tuesday.", terms_text=None)
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
        teams.update_team_page(None, team, about="We build rockets.", terms_text=RULES)
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

    def test_a_change_is_a_new_version_and_logged(self, app):
        team, lead = _led()
        _with_rules(team)
        first = team.terms_updated_at

        teams.update_team_page(lead, team, about=None, terms_text=RULES + "\n3. Have fun.")
        db.session.commit()

        assert team.terms_updated_at > first
        logs = db.session.query(AuditLog).filter_by(event_type="team_terms_changed").order_by(AuditLog.id).all()
        assert logs[-1].before_state == {"terms_text": RULES} and logs[-1].actor_user_id == lead.id

    def test_unchanged_is_no_new_version(self, app):
        team, _lead = _led()
        _with_rules(team)
        version = team.terms_updated_at

        teams.update_team_page(None, team, about=None, terms_text=RULES)

        assert team.terms_updated_at == version
        assert db.session.query(AuditLog).filter_by(event_type="team_terms_changed").count() == 1

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

        client.post("/teams/rocket/manage/settings", data={
            "description": "Rockets.", "about": "We build rockets.", "terms_text": RULES,
        })

        assert (team.about, team.terms_text) == ("We build rockets.", RULES)

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

        client.post("/admin/teams/rocket", data={
            "name": "Rocket", "admission_mode": "approval", "about": "From the admins.", "terms_text": RULES,
        })

        assert (team.about, team.terms_text) == ("From the admins.", RULES)
        assert "Rules to accept" in client.get("/admin/teams/rocket").get_data(as_text=True)
