"""A team's money: what its members paid, what was passed on, and to where.

A team is owed exactly what its members paid -- Stripe's fees are the
association's -- less refunds and lost chargebacks, less what was transferred.
Its leads and treasurers see that and keep the team's bank details; the
association's treasurer sees it for every team, records the transfers, and
pays by scanning a GiroCode (the API's side: tests/test_api_admin_money.py).
Nobody with only money rights sees the people.
"""
from datetime import timedelta

import pytest

from api_helpers import send
from conftest import app_module, db
from aeronautics_members.db_models import AuditLog, NotificationEvent, Payment, TeamPayout, User
from aeronautics_members.services import ValidationError, team_money, teams
from aeronautics_members.services.clock import get_membership_today
from test_emails import outbox  # noqa: F401
from test_teams_flow import _led, _login, _person, switched_on  # noqa: F401
from test_teams_foundation import _in_team

IBAN = "AT611904300234573201"


def _paid(team, user, cents=2500, *, refunded=0, status="paid", until=None, pi=None):
    payment = Payment(
        purpose="team", team_id=team.id, user_id=user.id, amount_cents=cents, currency="eur",
        refunded_cents=refunded, status=status, covers_until=until or get_membership_today() + timedelta(days=90),
        stripe_payment_intent_id=pi,
    )
    db.session.add(payment)
    db.session.commit()
    return payment


def _with_bank(team):
    team.bank_account_holder, team.bank_iban, team.bank_bic = "Rocket Team", IBAN, "BKAUATWW"
    db.session.commit()
    return team


def _account_with_role(slug, email):
    user = User(email=email)
    user.set_password("x")
    db.session.add(user)
    user.grant_role(app_module.get_role(slug))
    db.session.commit()
    return user


def _treasurer(email="treasurer@example.com"):
    return _account_with_role("treasurer", email)


def _team_treasurer(team, email="tim@example.com"):
    tim = _person(email, "Tim", "Treasurer")
    _in_team(tim, team)
    teams.grant_team_role(None, team, tim, teams.ROLE_TREASURER)
    db.session.commit()
    return tim


class TestTheBalance:
    def test_refunds_and_chargebacks_come_off_and_transfers_leave_what_is_open(self, app):
        team, lead = _led()
        anna = _person()
        until = get_membership_today() + timedelta(days=60)
        _paid(team, anna, until=until, pi="pi_a")
        _paid(team, lead, until=until, refunded=1000, pi="pi_b")
        _paid(team, anna, status="disputed", pi="pi_c")
        team_money.record_payout(None, team, amount="15,00")
        db.session.commit()

        money = team_money.summary(team)

        assert (money["earned"], money["paid_out"], money["open"]) == (4000, 1500, 2500)
        assert money["by_period"][-1] == (until, 4000)

    def test_only_the_teams_own_payments(self, app):
        team, lead = _led()
        other, _ = _led("Glider")
        _paid(other, lead, pi="pi_other")

        assert team_money.summary(team)["earned"] == 0

    def test_euros(self):
        assert [team_money.euros(c) for c in (0, 1250, 123456, -500)] == ["€0.00", "€12.50", "€1,234.56", "-€5.00"]


class TestRecordingATransfer:
    def test_kept_with_the_account_it_went_to(self, app):
        team, lead = _led()
        _with_bank(team)
        _paid(team, lead)
        treasurer = _treasurer()

        payout = team_money.record_payout(treasurer, team, amount="25.00", reference="Semester")
        team.bank_iban = "DE89370400440532013000"
        db.session.commit()

        assert (payout.amount_cents, payout.iban, payout.reference) == (2500, IBAN, "Semester")
        log = db.session.query(AuditLog).filter_by(event_type="team_payout_recorded").one()
        assert log.actor_user_id == treasurer.id

    def test_never_more_than_is_open(self, app):
        team, lead = _led()
        _paid(team, lead)

        with pytest.raises(ValidationError, match="more than is open"):
            team_money.record_payout(None, team, amount="25.01")

    @pytest.mark.parametrize("amount", ["", "abc", "0", "-5", "1.234"])
    def test_a_readable_amount(self, app, amount):
        team, lead = _led()
        _paid(team, lead)

        with pytest.raises(ValidationError):
            team_money.record_payout(None, team, amount=amount)

    def test_not_in_the_future(self, app):
        team, lead = _led()
        _paid(team, lead)

        with pytest.raises(ValidationError, match="future"):
            team_money.record_payout(None, team, amount="5",
                                     paid_on=(get_membership_today() + timedelta(days=1)).isoformat())

    def test_a_reference_by_default(self, app):
        team, lead = _led()
        _paid(team, lead)

        payout = team_money.record_payout(None, team, amount="5")

        assert payout.reference.startswith("Teambeiträge Rocket bis ")


class TestTheBankDetails:
    def test_cleaned_up_and_logged_with_before_and_after(self, app):
        team, lead = _led()

        changed = team_money.update_bank_details(lead, team, account_holder="  Rocket   Team ",
                                                 iban="at61 1904 3002 3457 3201", bic="bkauatww")
        db.session.commit()

        assert changed
        assert (team.bank_account_holder, team.bank_iban, team.bank_bic) == ("Rocket Team", IBAN, "BKAUATWW")
        log = db.session.query(AuditLog).filter_by(event_type="team_bank_details_changed").one()
        assert log.actor_user_id == lead.id and log.after_state["iban"] == IBAN and log.before_state["iban"] is None

    def test_unchanged_is_no_change(self, app):
        team, lead = _led()
        _with_bank(team)

        assert team_money.update_bank_details(lead, team, account_holder="Rocket Team", iban=IBAN,
                                              bic="BKAUATWW") is False

    @pytest.mark.parametrize("iban, message", [
        ("AT611904300234573202", "check digits"),
        ("hello", "does not look like an IBAN"),
    ])
    def test_a_mistyped_iban_is_refused(self, app, iban, message):
        team, lead = _led()

        with pytest.raises(ValidationError, match=message):
            team_money.update_bank_details(lead, team, account_holder="Rocket Team", iban=iban, bic="")

    def test_an_iban_needs_its_holder(self, app):
        team, lead = _led()

        with pytest.raises(ValidationError, match="account holder"):
            team_money.update_bank_details(lead, team, account_holder="", iban=IBAN, bic="")

    def test_a_bad_bic_is_refused(self, app):
        team, lead = _led()

        with pytest.raises(ValidationError, match="BIC"):
            team_money.update_bank_details(lead, team, account_holder="Rocket Team", iban=IBAN, bic="XYZ")

    def test_the_association_treasurer_is_told_of_a_change_by_somebody_else(self, app):
        team, lead = _led()
        treasurer = _treasurer()

        team_money.update_bank_details(lead, team, account_holder="Rocket Team", iban=IBAN, bic="")
        team_money.update_bank_details(treasurer, team, account_holder="Rocket e.V.", iban=IBAN, bic="")
        db.session.commit()

        told = db.session.query(NotificationEvent).filter_by(event_type="team_bank_details_changed").all()
        assert [event.recipient_email for event in told] == [treasurer.email]
        assert told[0].payload["iban"] == "AT61 … 3201"


class TestTheGiroCode:
    def test_the_epc_payload(self, app):
        team, _lead = _led()
        _with_bank(team)

        assert team_money.epc_payload(team, 4250, "Teambeiträge").split("\n") == [
            "BCD", "002", "1", "SCT", "BKAUATWW", "Rocket Team", IBAN, "EUR42.50", "", "", "Teambeiträge",
        ]

    def test_a_scalable_svg_only_with_somewhere_to_pay(self, app):
        team, _lead = _led()

        assert team_money.payout_qr_svg(team, 100, "x") is None
        _with_bank(team)
        assert team_money.payout_qr_svg(team, 0, "x") is None
        svg = team_money.payout_qr_svg(team, 100, "x")
        assert svg.startswith("<svg") and "viewBox" in svg


@pytest.mark.usefixtures("switched_on")
class TestWhoSeesWhat:
    def test_a_lead_sees_the_money_and_keeps_the_bank_details_but_records_no_transfer(self, app, client):
        team, lead = _led()
        _paid(team, lead)
        _login(client, lead.id)

        page = client.get("/teams/rocket/money").get_data(as_text=True)
        assert "€25.00" in page and 'name="iban"' in page and "Mark as transferred" not in page
        assert "team.view_money" in client.get("/api/v1/teams/rocket/manage").get_json()["permissions"]
        assert send(client, "POST", "/api/v1/admin/money/rocket/transfers", {"amount": "5"}).status_code == 403
        assert db.session.query(TeamPayout).count() == 0
        client.post("/teams/rocket/money/bank", data={"account_holder": "Rocket Team", "iban": IBAN})
        assert team.bank_iban == IBAN

    def test_a_team_treasurer_sees_the_money_not_the_management(self, app, client):
        team, _lead = _led()
        tim = _team_treasurer(team)
        _login(client, tim.id)

        assert client.get("/teams/rocket/money").status_code == 200
        assert client.get("/teams/rocket/manage").status_code == 403
        [card] = client.get("/api/v1/teams").get_json()["mine"]
        assert "money" in card["membership"]["actions"] and "manage" not in card["membership"]["actions"]
        client.post("/teams/rocket/money/bank", data={"account_holder": "Rocket Team", "iban": IBAN})
        assert team.bank_iban == IBAN

    def test_a_member_sees_none_of_it(self, app, client):
        team, _lead = _led()
        anna = _person()
        _in_team(anna, team)
        _login(client, anna.id)

        assert client.get("/teams/rocket/money").status_code == 403
        assert client.post("/teams/rocket/money/bank", data={"iban": IBAN}).status_code == 403
        assert client.get("/admin/money").status_code in (302, 403)
        assert team.bank_iban is None

    def test_the_association_treasurer_pays_out_but_sees_no_people(self, app, client):
        team, lead = _led()
        _with_bank(team)
        _paid(team, lead)
        treasurer = _treasurer()
        _login(client, treasurer.id)

        [row] = client.get("/api/v1/admin/money").get_json()["teams"]
        assert row["team"]["name"] == "Rocket" and row["open"] == 2500
        code = client.get("/admin/money/rocket/transfer-code.svg?amount=2500")
        assert code.mimetype == "image/svg+xml" and b"<svg" in code.data
        assert "Mark as transferred" not in client.get("/teams/rocket/money").get_data(as_text=True)
        assert client.get("/teams/rocket/manage").status_code == 403
        assert client.get("/api/v1/teams/rocket").get_json()["sees_team_page"] is False
        assert client.get("/admin/teams").status_code in (302, 403)
        assert client.get("/admin/accounts").status_code in (302, 403)

        send(client, "POST", "/api/v1/admin/money/rocket/transfers",
             {"amount": "25,00", "paid_on": get_membership_today().isoformat(), "reference": "WS"})
        assert db.session.query(TeamPayout).one().amount_cents == 2500
        assert team_money.summary(team)["open"] == 0

    def test_the_association_treasurer_still_sees_an_archived_team_with_teams_off(self, app, client):
        team, lead = _led()
        _paid(team, lead)
        team.status = teams.STATUS_ARCHIVED
        teams.save_team_settings(None, enabled=False, label_singular="", label_plural="")
        db.session.commit()
        _login(client, _treasurer().id)

        assert client.get("/teams/rocket/money").status_code == 200
        assert client.get("/api/v1/admin/money/rocket").status_code == 200
        _login(client, lead.id)
        assert client.get("/teams/rocket/money").status_code == 404

    def test_the_export(self, app, client):
        team, lead = _led()
        _paid(team, lead, refunded=500)
        _login(client, lead.id)

        response = client.get("/teams/rocket/money.csv")

        body = response.get_data(as_text=True)
        assert response.mimetype == "text/csv" and "Lena Lead" in body and "25.00,5.00,20.00" in body


@pytest.mark.usefixtures("outbox")
def test_the_email_about_changed_bank_details(app):
    from test_emails import FakeSMTP, _user_status_event

    from aeronautics_members.services.notifications import get_notification_service

    service = get_notification_service()
    event = _user_status_event("team_bank_details_changed", team_name="Rocket", team_slug="rocket",
                               changed_by="lead@example.com", account_holder="Rocket Team", iban="AT61 … 3201")
    with app.test_request_context():
        subject, template_vars = service._build_user_status_message(event)
        ok, _error = service._send_user_status_mail(event, subject, template_vars)

    assert ok and subject == "Bank details of Rocket changed"
    assert template_vars["action_url"].endswith("/admin/money/rocket"), "the association's side of it"
    assert FakeSMTP.sent


def test_the_treasurer_role_carries_no_other_admin_rights(app):
    from aeronautics_members.permissions import ROLE_PERMISSIONS, Permission

    assert ROLE_PERMISSIONS["treasurer"] == {Permission.ADMIN_ACCESS, Permission.TEAMS_MONEY}
