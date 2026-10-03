"""Teams: an optional logo, and the payment step every approval goes through.

A logo is optional -- plenty of teams have none. When there is one it shows on
the team's cards and pages, and in its emails below the association's own
header, so they still clearly come from the association.

Every approval, and joining an open team, passes through a payment step, even
for a team that charges nothing: the flow is then the same for every team, and
payment can be added later without changing it. A free team settles the step
on the spot and nobody is emailed about a payment that did not happen.
"""
from io import BytesIO

import pytest

from conftest import db
from aeronautics_members.db_models import AuditLog, NotificationEvent
from aeronautics_members.services import ValidationError, teams
from test_admin_reviews import _staff
from test_emails import FakeSMTP, _user_status_event, outbox  # noqa: F401
from test_teams_flow import _led, _login, _person, switched_on  # noqa: F401


def _png(size=(900, 300), colour=(0, 200, 230, 255)):
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGBA", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def logo_dir(app, tmp_path):
    app.config["TEAM_LOGO_DIR"] = str(tmp_path / "team_logos")
    return tmp_path / "team_logos"


@pytest.mark.usefixtures("switched_on")
class TestThePaymentStep:
    def test_approval_passes_through_it_and_a_free_team_settles_it_at_once(self, app):
        team, lead = _led()
        anna = _person()
        membership = teams.join_or_apply(anna, team)

        teams.approve(lead, team, membership.id)

        assert membership.status == teams.ACTIVE
        assert membership.payment_mode == teams.PAYMENT_NONE
        assert membership.payment_settled_at is not None
        assert membership.approved_at is not None and membership.started_at is not None
        events = {entry.event_type for entry in db.session.query(AuditLog).filter_by(category="teams")}
        assert {"team_approved", "team_payment_settled"} <= events

    def test_only_the_welcome_is_emailed(self, app):
        team, lead = _led()
        anna = _person()
        membership = teams.join_or_apply(anna, team)

        teams.approve(lead, team, membership.id)

        sent_to_anna = {event.event_type for event in db.session.query(NotificationEvent).filter_by(recipient_email=anna.email)}
        assert sent_to_anna == {"team_approved"}

    def test_joining_an_open_team_passes_through_it_too(self, app):
        team, _lead = _led("Glider", admission_mode="open")

        membership = teams.join_or_apply(_person(), team)

        assert membership.status == teams.ACTIVE
        assert membership.approved_at is not None and membership.payment_settled_at is not None

    def test_a_team_that_charges_waits_at_the_step(self, app):
        """Nothing can be set to charge yet; this is what will happen once it can."""
        team, lead = _led()
        team.payment_mode = "stripe"
        anna = _person()
        membership = teams.join_or_apply(anna, team)

        teams.approve(lead, team, membership.id)

        assert membership.status == teams.APPROVED
        assert membership.payment_settled_at is None
        assert teams.active_team_membership(anna, team) is None
        assert not db.session.query(NotificationEvent).filter_by(event_type="team_approved").count()


@pytest.mark.usefixtures("logo_dir")
class TestTheLogo:
    def test_optional(self, app):
        team, _lead = _led()

        assert team.logo_token is None and teams.logo_file(team) is None

    def test_stored_as_a_png_this_code_made_and_shrunk(self, app, logo_dir):
        from PIL import Image

        team, _lead = _led()
        teams.set_team_logo(None, team, _png(size=(2000, 500)))

        path = teams.logo_file(team)
        assert path.parent == logo_dir
        with Image.open(path) as stored:
            assert stored.format == "PNG" and max(stored.size) == teams.LOGO_MAX_SIDE

    @pytest.mark.parametrize("raw", [b"", b"not a picture", b"<svg xmlns='http://www.w3.org/2000/svg'/>"])
    def test_anything_else_is_refused(self, app, raw):
        team, _lead = _led()

        with pytest.raises(ValidationError):
            teams.set_team_logo(None, team, raw)

    def test_replacing_or_removing_deletes_the_old_file(self, app):
        team, _lead = _led()
        teams.set_team_logo(None, team, _png())
        first = teams.logo_file(team)

        teams.set_team_logo(None, team, _png(colour=(255, 0, 0, 255)))
        assert not first.exists() and teams.logo_file(team) is not None

        second = teams.logo_file(team)
        teams.remove_team_logo(None, team)
        assert not second.exists() and team.logo_token is None

    def test_served_under_its_token(self, app, client):
        team, _lead = _led()
        teams.set_team_logo(None, team, _png())
        db.session.commit()

        response = client.get(f"/teams/logo/{team.logo_token}")

        assert response.status_code == 200 and response.mimetype == "image/png"
        assert client.get("/teams/logo/0123abcd").status_code == 404

    def test_an_admin_uploads_and_removes_it(self, app, client):
        team, _lead = _led()
        _login(client, _staff("admin@example.com", "admin").id)
        fields = {"name": team.name, "admission_mode": team.admission_mode}

        client.post("/admin/teams/rocket", data={**fields, "logo": (BytesIO(_png()), "logo.png")},
                    content_type="multipart/form-data")
        db.session.refresh(team)
        assert team.logo_token is not None

        client.post("/admin/teams/rocket", data={**fields, "remove_logo": "on"}, content_type="multipart/form-data")
        db.session.refresh(team)
        assert team.logo_token is None

    @pytest.mark.usefixtures("switched_on")
    def test_a_lead_uploads_it_too(self, app, client):
        team, lead = _led()
        _login(client, lead.id)

        client.post("/teams/rocket/manage/settings", data={"logo": (BytesIO(_png()), "logo.png")},
                    content_type="multipart/form-data")

        db.session.refresh(team)
        assert team.logo_token is not None

    @pytest.mark.usefixtures("switched_on")
    def test_shown_to_somebody_choosing_a_team(self, app, client):
        team, _lead = _led()
        teams.set_team_logo(None, team, _png())
        db.session.commit()
        _login(client, _person().id)

        assert f"/teams/logo/{team.logo_token}" in client.get("/teams").get_data(as_text=True)

    @pytest.mark.usefixtures("outbox")
    def test_in_its_emails_below_the_associations_header(self, app):
        from aeronautics_members.services.notifications import get_notification_service

        team, _lead = _led()
        teams.set_team_logo(None, team, _png())
        db.session.commit()
        service = get_notification_service()
        event = _user_status_event("team_approved", team_name="Rocket", team_slug="rocket",
                                   team_logo_token=team.logo_token)
        with app.test_request_context():
            subject, template_vars = service._build_user_status_message(event)
            ok, _error = service._send_user_status_mail(event, subject, template_vars)

        assert ok
        message = FakeSMTP.sent[-1]
        content_ids = {part.get("Content-ID") for part in message.walk() if part.get("Content-ID")}
        assert content_ids == {"<logo>", "<teamlogo>"}, "the association's header stays, the team's logo joins it"
        html = next(part for part in message.walk() if part.get_content_type() == "text/html")
        assert 'src="cid:teamlogo"' in html.get_payload(decode=True).decode()

    @pytest.mark.usefixtures("outbox")
    def test_without_a_logo_the_email_names_the_team(self, app):
        from aeronautics_members.services.notifications import get_notification_service

        service = get_notification_service()
        event = _user_status_event("team_approved", team_name="Rocket", team_slug="rocket")
        with app.test_request_context():
            subject, template_vars = service._build_user_status_message(event)
            service._send_user_status_mail(event, subject, template_vars)

        message = FakeSMTP.sent[-1]
        assert {part.get("Content-ID") for part in message.walk() if part.get("Content-ID")} == {"<logo>"}
