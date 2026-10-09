"""Who carries Stripe's fees: the association (as before), or the teams -- each
payment and each credit sale keeping how it was when it was made
(services/team_money.py, Admin › Money).
"""
import pytest
import stripe

from api_helpers import send, signed_in
from conftest import db
from aeronautics_members.services import ValidationError, credit, credit_sales, team_money
from test_credit import credit_on, fake_stripe  # noqa: F401
from test_team_money import _paid, _treasurer
from test_teams_flow import _led, _person, switched_on  # noqa: F401


@pytest.fixture
def rocket(app, switched_on):  # noqa: F811
    return _led()


def _teams_pay(share_bps=0):
    team_money.save_money_settings(None, fee_payer="team", credit_share_bps=share_bps)
    db.session.commit()


class TestTeamFees:
    def test_the_association_pays_unless_set_otherwise(self, rocket):
        team, lead = rocket
        _paid(team, lead, cents=2500)

        assert not team_money.teams_bear_fees()
        assert team_money.summary(team)["earned"] == 2500

    def test_a_team_carries_the_fee_of_what_is_paid_after_the_switch(self, rocket, monkeypatch):
        team, lead = rocket
        _paid(team, lead, cents=2500, pi="pi_before")
        _teams_pay()
        after = _paid(team, lead, cents=2500, pi="pi_after")
        after.team_bears_fee = True  # as the webhook records it now
        db.session.commit()

        waiting = team_money.summary(team)
        assert (waiting["earned"], waiting["waiting_for_fee"]) == (2500, 2500)

        monkeypatch.setattr(stripe.PaymentIntent, "retrieve", staticmethod(lambda pid, **k: {
            "id": pid, "latest_charge": {"balance_transaction": {"fee": 63}}}))
        assert team_money.fill_in_fees() == 1
        db.session.commit()

        money = team_money.summary(team)
        assert (money["earned"], money["waiting_for_fee"]) == (2500 + 2500 - 63, 0)

    def test_a_fee_not_known_yet_waits(self, rocket, monkeypatch):
        team, lead = rocket
        payment = _paid(team, lead, cents=2500, pi="pi_sepa")
        payment.team_bears_fee = True
        db.session.commit()
        monkeypatch.setattr(stripe.PaymentIntent, "retrieve", staticmethod(lambda pid, **k: {
            "id": pid, "latest_charge": {"balance_transaction": None}}))

        assert team_money.fill_in_fees() == 0
        assert team_money.summary(team)["waiting_for_fee"] == 2500

    def test_the_webhook_records_who_carries_it(self, rocket):
        from aeronautics_members.services import team_payments

        _teams_pay()
        assert team_payments.teams_bear_fees() is True

    def test_settings_refused(self, app):
        with pytest.raises(ValidationError):
            team_money.save_money_settings(None, fee_payer="nobody", credit_share_bps=0)
        with pytest.raises(ValidationError):
            team_money.save_money_settings(None, fee_payer="team", credit_share_bps=2500)


class TestCreditSales:
    def test_the_association_keeps_its_share_of_a_teams_sale(self, credit_on, rocket):  # noqa: F811
        team, lead = rocket
        anna = _person()
        credit.book_cash(None, anna, 1000)
        beer = credit_sales.add_item(lead, team=team, name="Beer", price_cents=200)
        credit_sales.sell(anna, beer)
        _teams_pay(share_bps=300)
        sold = credit_sales.sell(anna, beer)
        db.session.commit()

        assert sold.kept_cents == 6
        assert team_money.summary(team)["sales"]["earned"] == 200 + 194

        credit_sales.take_back(lead, sold)
        db.session.commit()
        assert team_money.summary(team)["sales"]["earned"] == 200


class TestThroughTheApi:
    def test_the_treasurer_sets_them(self, client, app):
        signed_in(client, _treasurer())

        saved = send(client, "PUT", "/api/v1/admin/money/settings", {"fee_payer": "team", "credit_share_bps": 250})

        assert saved.status_code == 200 and saved.get_json()["changed"]
        assert client.get("/api/v1/admin/money/settings").get_json() == {"fee_payer": "team",
                                                                          "credit_share_bps": 250}

    def test_not_for_a_member(self, client, app):
        signed_in(client, _person())
        assert client.get("/api/v1/admin/money/settings").status_code == 403
