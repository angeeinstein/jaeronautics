"""A team's About page as a presentation: the formatted text, the gallery,
and the facts at the top (api/teams.py, api/team_manage.py, services/teams.py).

The leads, or whoever may edit the team's settings, add photos -- re-encoded,
like the cover -- caption and order them, and remove them; everybody signed in
sees them on the About page.
"""
from io import BytesIO

import pytest

from conftest import db
from aeronautics_members.db_models import AuditLog, TeamPhoto
from aeronautics_members.services import ConflictError, ValidationError, backup, teams
from test_team_page import _png, picture_dir  # noqa: F401 -- fixture
from test_teams_flow import _led, _login, _person, switched_on  # noqa: F401
from test_teams_foundation import _in_team

API = "/api/v1/teams/rocket"
MANAGE = f"{API}/manage/page"


def _upload(client, caption=None, image=None):
    url = f"{MANAGE}/photos" + (f"?caption={caption}" if caption else "")
    return client.post(url, data={"image": (BytesIO(image or _png((3000, 2000))), "photo.png")},
                       content_type="multipart/form-data")


@pytest.fixture
def lead(app, client, picture_dir):  # noqa: F811
    team, person = _led()
    _login(client, person.id)
    return team


@pytest.mark.usefixtures("switched_on")
class TestTheGallery:
    def test_a_photo_is_made_anew_and_shown_in_order(self, app, client, lead):
        from PIL import Image

        first = _upload(client, caption="On the launch pad")
        second = _upload(client)

        assert first.status_code == 201 and second.status_code == 201
        photos = second.get_json()["photos"]
        assert [photo["caption"] for photo in photos] == ["On the launch pad", None]
        stored = db.session.get(TeamPhoto, photos[0]["id"])
        with Image.open(stored.path) as image:
            assert image.format == "JPEG" and max(image.size) == teams.PHOTO_MAX_SIDE
        assert (photos[0]["width"], photos[0]["height"]) == (2000, 1333)
        served = client.get(photos[0]["url"])
        assert served.status_code == 200 and served.mimetype == "image/jpeg"

    def test_everybody_signed_in_sees_it_on_the_about_page(self, app, client, lead):
        _upload(client, caption="Workshop")
        _login(client, _person().id)

        body = client.get(API).get_json()

        assert [photo["caption"] for photo in body["photos"]] == ["Workshop"]

    def test_ordered_and_captioned_together(self, app, client, lead):
        _upload(client)
        photos = _upload(client).get_json()["photos"]
        first, second = (photo["id"] for photo in photos)

        answer = client.put(f"{MANAGE}/photos", json={"photos": [
            {"id": second, "caption": "  Launch   day "}, {"id": first, "caption": ""}]})

        assert answer.status_code == 200
        assert [(photo["id"], photo["caption"]) for photo in answer.get_json()["photos"]] == [
            (second, "Launch day"), (first, None)]
        assert db.session.query(AuditLog).filter_by(event_type="team_photos_arranged").count() == 1

    def test_an_arrangement_from_before_a_change_is_refused(self, app, client, lead):
        photos = _upload(client).get_json()["photos"]
        _upload(client)

        answer = client.put(f"{MANAGE}/photos", json={"photos": [{"id": photos[0]["id"], "caption": None}]})

        assert answer.status_code == 409 and answer.get_json()["error"]["code"] == "team_photos_changed"

    def test_removed_with_its_file(self, app, client, lead):
        from pathlib import Path

        photos = [_upload(client), _upload(client)][-1].get_json()["photos"]
        path = Path(db.session.get(TeamPhoto, photos[0]["id"]).path)

        answer = client.delete(f"{MANAGE}/photos/{photos[0]['id']}")

        assert [photo["id"] for photo in answer.get_json()["photos"]] == [photos[1]["id"]]
        assert not path.exists()
        assert client.get(photos[0]["url"]).status_code == 404
        assert db.session.get(TeamPhoto, photos[1]["id"]).position == 0

    def test_holds_so_many_photos(self, app, client, lead):
        small = _png((40, 30))
        for _ in range(teams.PHOTOS_MAX):
            assert _upload(client, image=small).status_code == 201

        answer = _upload(client, image=small)

        assert answer.status_code == 400 and answer.get_json()["error"]["code"] == "team_photos_full"

    def test_not_a_picture_is_refused(self, app, client, lead):
        answer = _upload(client, image=b"not a picture")

        assert answer.status_code == 400

    def test_only_who_edits_the_team_page(self, app, client, lead):
        anna = _person()
        _in_team(anna, lead)
        db.session.commit()
        _login(client, anna.id)

        assert _upload(client).status_code == 403
        assert client.put(f"{MANAGE}/photos", json={"photos": []}).status_code == 403

    def test_another_teams_photo_cannot_be_removed(self, app, client, lead):
        other, _other_lead = _led("Glider")
        photo = teams.add_team_photo(None, other, _png((40, 30)))
        db.session.commit()

        assert client.delete(f"{MANAGE}/photos/{photo.id}").status_code == 404
        assert db.session.get(TeamPhoto, photo.id) is not None


class TestTheService:
    def test_a_caption_too_long(self, app, picture_dir):  # noqa: F811
        team, _lead = _led()

        with pytest.raises(ValidationError):
            teams.add_team_photo(None, team, _png((40, 30)), caption="x" * (teams.CAPTION_MAX_LENGTH + 1))

    def test_every_photo_once(self, app, picture_dir):  # noqa: F811
        team, _lead = _led()
        photo = teams.add_team_photo(None, team, _png((40, 30)))
        db.session.commit()

        with pytest.raises(ConflictError):
            teams.arrange_team_photos(None, team, [(photo.id, None), (photo.id, None)])


class TestTheText:
    def test_light_formatting(self):
        html = teams.render_about("# What we do\n\nWe **build** rockets:\n\n- structures\n- avionics")

        assert "<h2>What we do</h2>" in html  # one below the page's own heading
        assert "<strong>build</strong>" in html and "<li>avionics</li>" in html

    def test_a_line_break_stays_one(self):
        """So a text typed before it was formatted looks as it did."""
        assert teams.render_about("Every Tuesday\nat 18:00") == "<p>Every Tuesday<br />\nat 18:00</p>\n"

    def test_nothing_that_could_run(self):
        html = teams.render_about('<script>alert(1)</script> [x](javascript:alert(1)) <img src=x onerror=alert(1)>')

        assert "<script" not in html and "<img" not in html and 'href="javascript' not in html

    def test_no_images_from_elsewhere(self):
        assert "<img" not in teams.render_about("![rocket](https://elsewhere.example/r.png)")

    def test_links_out_open_on_their_own(self):
        html = teams.render_about("[Our website](https://rocket.example)")

        assert 'target="_blank"' in html and 'rel="noopener noreferrer"' in html

    def test_nothing_without_a_text(self):
        assert teams.render_about(None) is None and teams.render_about("") is None


@pytest.mark.usefixtures("switched_on")
class TestTheAboutPage:
    def test_says_how_many_are_in_it_and_what_it_costs(self, app, client):
        team, _lead = _led()
        team.payment_mode, team.stripe_price_id, team.fee_display = "subscription", "price_1", "€10.00 every 6 months"
        teams.update_team_page(None, team, about="We **build** rockets.")
        db.session.commit()
        _login(client, _person().id)

        body = client.get(API).get_json()

        assert body["member_count"] == 1
        assert body["fee"] == "€10.00 every 6 months"
        assert body["about_html"] == "<p>We <strong>build</strong> rockets.</p>\n"

    def test_a_free_team_says_no_fee(self, app, client):
        _led()
        _login(client, _person().id)

        assert client.get(API).get_json()["fee"] is None

    def test_only_who_edits_it_is_offered_to(self, app, client, lead):
        assert client.get(API).get_json()["can_edit_page"] is True
        anna = _person()
        _in_team(anna, lead)
        db.session.commit()
        _login(client, anna.id)

        assert client.get(API).get_json()["can_edit_page"] is False

    def test_the_text_previewed_before_it_is_saved(self, app, client, lead):
        answer = client.post(f"{MANAGE}/preview", json={"about": "## Join us"})

        assert answer.get_json() == {"html": "<h3>Join us</h3>\n"}


def test_a_restore_elsewhere_moves_the_photos_and_the_cover_along():
    assert "picture_path" in backup.STORAGE_PATH_COLUMNS["teams"]
    assert backup.STORAGE_PATH_COLUMNS["team_photos"] == ("path",)
