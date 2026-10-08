"""Teams, the admins' part (api/admin_teams.py; the page is frontend/src/pages/admin/teams/).

Creating, roles, archiving and the audit trail: tests/test_teams_foundation.py.
Fees: tests/test_team_payments.py and tests/test_fee_change.py. Here: what
the answers say, and the refusals.
"""
from api_helpers import send, signed_in
from conftest import db
from aeronautics_members.services import teams
from test_admin_reviews import _staff
from test_team_payments import PRICE_ID, _charging, fake_stripe  # noqa: F401
from test_teams_foundation import _association_member, _in_team, _team

API = "/api/v1/admin/teams"


def test_signed_out(client):
    assert client.get(API).status_code == 401


def test_the_list(client):
    team = _team("Rocket")
    _charging(team)
    lead = _association_member("lead@example.com")
    teams.grant_team_role(None, team, lead, teams.ROLE_LEAD)
    _in_team(lead, team)
    db.session.commit()
    signed_in(client, _staff("boss@example.org", "admin"))

    [row] = client.get(API).get_json()["teams"]

    assert row["slug"] == "rocket" and row["status"] == "active" and row["members"] == 1
    assert row["fee"] == "€10.00 every 6 months"
    assert row["leads"] == ["Test Member"] and row["has_lead_in_force"] is True


def test_a_free_team_has_no_fee_and_no_lead_in_force(client):
    _team("Glider")
    signed_in(client, _staff("boss@example.org", "admin"))

    [row] = client.get(API).get_json()["teams"]

    assert row["fee"] is None and row["leads"] == [] and row["has_lead_in_force"] is False


def test_one_team(client):
    team = _team("Rocket", max_members=12)
    signed_in(client, _staff("boss@example.org", "admin"))

    body = client.get(f"{API}/rocket").get_json()

    assert body["name"] == "Rocket" and body["max_members"] == 12
    assert body["fee"] == {"payment_mode": "none", "stripe_price_id": None, "period_starts": None, "display": None}
    assert body["manage_url"] == "/teams/rocket/manage" and team.slug == "rocket"


def test_an_unknown_team(client):
    signed_in(client, _staff("boss@example.org", "admin"))

    assert client.get(f"{API}/nothing").status_code == 404


def test_changing_the_details(client):
    _team("Rocket")
    signed_in(client, _staff("boss@example.org", "admin"))

    body = send(client, "PUT", f"{API}/rocket", {"name": "Rocket Team", "forum_group": "rocket"}).get_json()

    assert (body["name"], body["forum_group"]) == ("Rocket Team", "rocket")
    assert body["slug"] == "rocket", "the short name stays"
    # How people join and how many: the leads' (test_team_settings_for_leads.py).
    assert (body["admission_mode"], body["max_members"]) == ("approval", None)


def test_how_people_join_is_not_the_admins_form_any_more(client):
    _team("Rocket")
    signed_in(client, _staff("boss@example.org", "admin"))

    response = send(client, "PUT", f"{API}/rocket", {"name": "Rocket", "admission_mode": "open"})

    assert response.status_code == 400


def test_a_bad_detail_is_refused_with_the_reason(client):
    _team("Rocket")
    signed_in(client, _staff("boss@example.org", "admin"))

    response = send(client, "PUT", f"{API}/rocket", {"name": "  "})

    assert response.status_code == 400 and response.get_json()["error"]["message"] == "A team needs a name."


def test_the_short_name_new_is_taken_by_the_pages(client):
    """/admin/teams/new is the form; a team called that could never be opened."""
    signed_in(client, _staff("boss@example.org", "admin"))

    response = send(client, "POST", API, {"name": "New"})

    assert response.status_code == 400 and response.get_json()["error"]["code"] == "team_slug_reserved"


def test_creating_with_a_fee(client, fake_stripe):  # noqa: F811
    signed_in(client, _staff("boss@example.org", "admin"))

    response = send(client, "POST", API, {
        "name": "Rocket",
        "fee": {"payment_mode": "subscription", "stripe_price_id": PRICE_ID, "period_starts": "01.10, 01.04"},
    })

    assert response.status_code == 201
    assert response.get_json()["fee"]["payment_mode"] == "subscription"


def test_a_fee_stripe_does_not_know_is_refused(client, monkeypatch):
    import stripe

    def unknown(price_id, **kwargs):
        raise stripe.InvalidRequestError("No such price", "id")

    monkeypatch.setattr(stripe.Price, "retrieve", staticmethod(unknown))
    _team("Rocket")
    signed_in(client, _staff("boss@example.org", "admin"))

    response = send(client, "PUT", f"{API}/rocket/fee", {
        "payment_mode": "subscription", "stripe_price_id": "price_nope", "period_starts": "01.10, 01.04"})

    assert response.status_code == 400 and response.get_json()["error"]["code"] == "team_price_unknown"


def test_restoring_an_archived_team(client):
    team = _team("Rocket")
    teams.set_team_archived(None, team, True, confirmed_name="Rocket")
    db.session.commit()
    signed_in(client, _staff("boss@example.org", "admin"))

    body = send(client, "PUT", f"{API}/rocket/archived", {"archived": False}).get_json()

    assert body["status"] == "active" and body["archived_at"] is None


def test_only_with_the_teams_permission(client):
    _team("Rocket")
    signed_in(client, _staff("money@example.org", "treasurer"))

    assert client.get(API).status_code == 403
    assert send(client, "PUT", f"{API}/rocket/archived", {"archived": True, "confirm_name": "Rocket"}).status_code == 403
