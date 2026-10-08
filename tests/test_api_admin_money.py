"""Money, the association's side (api/admin_money.py; the pages are frontend/src/pages/admin/money/).

What a team is owed, transfers and bank details: tests/test_team_money.py.
The money on Stripe: tests/test_money_overview.py. Here: what the answers
say, and the refusals.
"""
from datetime import timedelta

import pytest

from api_helpers import send, signed_in
from conftest import db
from aeronautics_members.db_models import TeamPayout
from aeronautics_members.services.clock import get_membership_today
from test_team_money import IBAN, _paid, _treasurer, _with_bank
from test_teams_flow import _led, _person, switched_on  # noqa: F401

API = "/api/v1/admin/money"


@pytest.fixture
def rocket(app):
    team, lead = _led()
    _paid(team, lead, cents=2500)
    _paid(team, lead, cents=1000, refunded=400)
    return team, lead


def test_signed_out(client):
    assert client.get(API).status_code == 401


def test_only_with_the_money_permission(client, rocket):
    _team, lead = rocket
    signed_in(client, lead)

    assert client.get(API).status_code == 403
    assert client.get(f"{API}/rocket").status_code == 403
    assert send(client, "PUT", f"{API}/rocket/bank", {"iban": IBAN}).status_code == 403
    assert client.get("/admin/money/rocket/transfer-code.svg?amount=100").status_code in (302, 403)


def test_every_team_with_what_is_open(client, rocket):
    team, _lead = rocket
    _with_bank(team)
    signed_in(client, _treasurer())

    body = client.get(API).get_json()

    [row] = body["teams"]
    assert (row["earned"], row["paid_out"], row["open"]) == (3100, 0, 3100)
    assert row["account"] == "AT61 … 3201" and row["last_transfer_on"] is None
    assert body["total_open"] == 3100


def test_one_team(client, rocket):
    signed_in(client, _treasurer())

    body = client.get(f"{API}/rocket").get_json()

    assert body["team"]["name"] == "Rocket" and body["open"] == 3100
    assert body["bank"] == {"account_holder": None, "iban": None, "bic": None}
    assert body["reference"].startswith("Teambeiträge Rocket bis ")
    assert body["export_url"] == "/teams/rocket/money.csv"
    first, second = body["payments"]
    assert {first["counts"], second["counts"]} == {2500, 600}
    assert {payment["name"] for payment in body["payments"]} == {"Lena Lead"}
    assert [period["earned"] for period in body["by_period"]] == [3100]


def test_an_unknown_team(client):
    signed_in(client, _treasurer())

    assert client.get(f"{API}/nothing").status_code == 404


def test_recording_a_transfer(client, rocket):
    team, _lead = rocket
    _with_bank(team)
    signed_in(client, _treasurer())

    response = send(client, "POST", f"{API}/rocket/transfers", {"amount": "31,00", "reference": "WS 2026"})

    assert response.status_code == 201
    body = response.get_json()
    assert body["open"] == 0
    [transfer] = body["transfers"]
    assert (transfer["amount"], transfer["reference"], transfer["to"]) == (3100, "WS 2026", "AT61 … 3201")
    assert transfer["paid_on"] == get_membership_today().isoformat()


@pytest.mark.parametrize("payload, code", [
    ({"amount": "31.01"}, "team_payout_more_than_open"),
    ({"amount": "lots"}, "team_payout_amount_invalid"),
    ({"amount": "5", "paid_on": (get_membership_today() + timedelta(days=1)).isoformat()},
     "team_payout_date_invalid"),
])
def test_a_transfer_is_refused_with_the_reason(client, rocket, payload, code):
    signed_in(client, _treasurer())

    response = send(client, "POST", f"{API}/rocket/transfers", payload)

    assert response.status_code == 400 and response.get_json()["error"]["code"] == code
    assert db.session.query(TeamPayout).count() == 0


def test_the_bank_details(client, rocket):
    signed_in(client, _treasurer())

    body = send(client, "PUT", f"{API}/rocket/bank",
                {"account_holder": " Rocket  Team ", "iban": "at61 1904 3002 3457 3201", "bic": None}).get_json()

    assert body["bank"] == {"account_holder": "Rocket Team", "iban": IBAN, "bic": None}


def test_a_mistyped_iban_is_refused(client, rocket):
    signed_in(client, _treasurer())

    response = send(client, "PUT", f"{API}/rocket/bank", {"account_holder": "Rocket Team",
                                                          "iban": "AT611904300234573202"})

    assert response.status_code == 400 and response.get_json()["error"]["code"] == "team_iban_invalid"


def test_the_transfer_code_for_the_amount_typed(client, rocket):
    team, _lead = rocket
    signed_in(client, _treasurer())
    assert client.get("/admin/money/rocket/transfer-code.svg?amount=1000").status_code == 404, "nowhere to pay"
    _with_bank(team)

    response = client.get("/admin/money/rocket/transfer-code.svg?amount=1000&reference=WS")

    assert response.status_code == 200 and response.mimetype == "image/svg+xml"
    assert response.get_data(as_text=True).startswith('<svg xmlns="http://www.w3.org/2000/svg"')
    assert client.get("/admin/money/rocket/transfer-code.svg?amount=0").status_code == 404


def test_the_pages_are_the_apps(client, rocket):
    signed_in(client, _treasurer())

    for path in ("/admin/money", "/admin/money/rocket"):
        assert client.get(path).status_code == 200


def test_the_short_name_overview_is_taken_by_the_api(client):
    from test_admin_reviews import _staff

    signed_in(client, _staff("boss@example.org", "admin"))

    response = send(client, "POST", "/api/v1/admin/teams", {"name": "Overview"})

    assert response.status_code == 400 and response.get_json()["error"]["code"] == "team_slug_reserved"
