"""Who carries Stripe's fees: the association, or the teams -- for every payment and
credit sale, past and future (services/team_money.py, services/credit_fees.py,
Admin › Money; docs/money-ledger-plan.md). And every figure shown with the lines
it adds up from.
"""
from datetime import datetime, timezone

import pytest

from api_helpers import send, signed_in
from conftest import db
from aeronautics_members.db_models import Payment, StripeBooking
from aeronautics_members.services import ValidationError, credit, credit_fees, credit_sales, team_money
from test_credit import credit_on, fake_stripe  # noqa: F401
from test_team_money import _paid, _treasurer
from test_teams_flow import _led, _person, switched_on  # noqa: F401


@pytest.fixture
def rocket(app, switched_on):  # noqa: F811
    return _led()


def _teams_pay(share_bps=0):
    team_money.save_money_settings(None, fee_payer="team", credit_share_bps=share_bps)
    db.session.commit()


_booked = iter(range(1, 10_000))


def _in_the_book(payment, fee, kind="charge", amount=None):
    """The payment's booking in the copy of Stripe's book, as a fetch writes it."""
    number = next(_booked)
    amount = payment.amount_cents if amount is None else amount
    db.session.add(StripeBooking(stripe_id=f"txn_{number}", type=kind, amount_cents=amount, fee_cents=fee,
                                 net_cents=amount - fee, currency="eur", booked_at=datetime.now(timezone.utc),
                                 charge_id=f"ch_{payment.id}", product=payment.purpose, payment_id=payment.id))
    if kind == "charge":
        payment.fee_cents = fee
    db.session.commit()


def _top_up(user, cents, fee=None):
    """A top-up paid through Stripe, with the fee Stripe kept (None: not known yet)."""
    account = credit._account(user.id)
    payment = Payment(purpose="credit", user_id=user.id, amount_cents=cents, currency="eur",
                      stripe_payment_intent_id=f"pi_top_{next(_booked)}", fee_cents=fee)
    db.session.add(payment)
    db.session.flush()
    entry = credit._book(account, credit.TOP_UP, cents, "Top-up", payment=payment)
    db.session.commit()
    return entry


class TestTeamFees:
    def test_the_association_pays_unless_set_otherwise(self, rocket):
        team, lead = rocket
        payment = _paid(team, lead, cents=2500)
        _in_the_book(payment, fee=63)

        assert not team_money.teams_bear_fees()
        assert team_money.summary(team)["earned"] == 2500

    def test_switching_counts_for_every_payment_at_once(self, rocket):
        team, lead = rocket
        before = _paid(team, lead, cents=2500, pi="pi_before")
        _in_the_book(before, fee=63)
        _teams_pay()
        after = _paid(team, lead, cents=2500, pi="pi_after")
        _in_the_book(after, fee=63)

        assert team_money.summary(team)["earned"] == 2 * (2500 - 63)
        team_money.save_money_settings(None, fee_payer="association", credit_share_bps=0)
        assert team_money.summary(team)["earned"] == 5000

    def test_a_fee_not_known_yet_waits(self, rocket):
        team, lead = rocket
        _teams_pay()
        _paid(team, lead, cents=2500, pi="pi_sepa")

        money = team_money.summary(team)
        assert (money["earned"], money["waiting_for_fee"], money["open"]) == (0, 2500, 0)

    def test_stripe_keeps_its_fee_on_a_refund_and_on_a_chargeback(self, rocket):
        team, lead = rocket
        _teams_pay()
        refunded = _paid(team, lead, cents=2500, refunded=2500, pi="pi_refunded")
        _in_the_book(refunded, fee=63)
        lost = _paid(team, lead, cents=2500, status="disputed", pi="pi_lost")
        _in_the_book(lost, fee=63)
        _in_the_book(lost, fee=1500, kind="adjustment", amount=-2500)  # the chargeback and its fee

        found = team_money.statement(team)

        assert found["earned"] == -63 - 63 - 1500
        kinds = [line.kind for line in found["lines"] if line.group == f"payment:{lost.id}"]
        assert kinds == ["fee_paid", "taken_back", "stripe_fee", "stripe_later_fee"]

    def test_every_figure_is_the_sum_of_its_lines(self, rocket):
        team, lead = rocket
        _teams_pay()
        payment = _paid(team, lead, cents=2500, refunded=500)
        _in_the_book(payment, fee=63)
        team_money.record_payout(None, team, amount="10.00")
        db.session.commit()

        found = team_money.statement(team)

        assert [(kind, amount) for kind, _label, amount in found["rows"]] == [
            ("fee_paid", 2500), ("refunded", -500), ("stripe_fee", -63)]
        assert found["earned"] == 1937 and found["transferred"] == 1000 and found["open"] == 937
        assert sum(line.amount for line in found["lines"]) == found["open"]

    def test_settings_refused(self, app):
        with pytest.raises(ValidationError):
            team_money.save_money_settings(None, fee_payer="nobody", credit_share_bps=0)
        with pytest.raises(ValidationError):
            team_money.save_money_settings(None, fee_payer="team", credit_share_bps=2500)


class TestCreditFees:
    """Oldest top-up first; each part carries its share of that top-up's fee."""

    def test_a_sale_carries_its_share_of_the_top_ups_fee(self, credit_on, rocket):  # noqa: F811
        team, lead = rocket
        anna = _person()
        _top_up(anna, 2000, fee=55)
        beer = credit_sales.add_item(lead, team=team, name="Beer", price_cents=200)
        sold = credit_sales.sell(anna, beer)
        db.session.commit()

        parts = credit_fees.parts_for_people([anna.id])[sold.id]

        assert [(part.amount, part.fee) for part in parts] == [(200, 6)]  # 55 × 200 / 2000 = 5.5, rounded up
        _teams_pay()
        assert team_money.summary(team)["sales"]["earned"] == 194
        line = [line for line in team_money.statement(team)["lines"] if line.kind == "top_up_fee"][0]
        assert "€20.00, fee €0.55" in line.text and line.amount == -6

    def test_the_shares_add_up_to_the_fee(self, credit_on, rocket):  # noqa: F811
        team, lead = rocket
        anna = _person()
        _top_up(anna, 1000, fee=35)
        beer = credit_sales.add_item(lead, team=team, name="Beer", price_cents=300)
        sales = [credit_sales.sell(anna, beer) for _ in range(3)]
        cola = credit_sales.add_item(lead, team=team, name="Cola", price_cents=100)
        sales.append(credit_sales.sell(anna, cola))
        db.session.commit()

        parts = credit_fees.parts_for_people([anna.id])

        assert sum(part.fee for sale in sales for part in parts[sale.id]) == 35

    def test_oldest_first_and_cash_carries_none(self, credit_on, rocket):  # noqa: F811
        team, lead = rocket
        anna = _person()
        credit.book_cash(None, anna, 500)
        _top_up(anna, 1000, fee=35)
        beer = credit_sales.add_item(lead, team=team, name="Beer", price_cents=800)
        sold = credit_sales.sell(anna, beer)
        db.session.commit()

        parts = credit_fees.parts_for_people([anna.id])[sold.id]

        assert [(part.payment_id is not None, part.amount, part.fee) for part in parts] == [
            (False, 500, 0), (True, 300, 11)]  # 35 × 300 / 1000 = 10.5 → 11

    def test_a_sale_taken_back_puts_back_exactly_what_it_took(self, credit_on, rocket):  # noqa: F811
        team, lead = rocket
        anna = _person()
        _top_up(anna, 2000, fee=55)
        beer = credit_sales.add_item(lead, team=team, name="Beer", price_cents=200)
        sold = credit_sales.sell(anna, beer)
        undone = credit_sales.take_back(lead, sold)
        again = credit_sales.sell(anna, beer)
        db.session.commit()

        parts = credit_fees.parts_for_people([anna.id])

        assert [(part.amount, part.fee) for part in parts[undone.id]] == [(-200, -6)]
        assert [(part.amount, part.fee) for part in parts[again.id]] == [(200, 6)]
        _teams_pay()
        assert team_money.summary(team)["sales"]["earned"] == 194

    def test_a_refund_takes_from_its_own_top_up_first(self, credit_on):  # noqa: F811
        anna = _person()
        first = _top_up(anna, 1000, fee=35)
        second = _top_up(anna, 1000, fee=35)
        coffee = credit_sales.add_item(None, name="Coffee", price_cents=500)
        credit_sales.sell(anna, coffee)  # from the first, oldest
        account = credit._account(anna.id)
        refund = credit._book(account, credit.REFUND, -1000, "Refunded", payment=second.payment)
        db.session.commit()

        parts = credit_fees.parts_for_people([anna.id])[refund.id]

        assert [(part.payment_id, part.amount, part.fee) for part in parts] == [(second.payment_id, 1000, 35)]
        assert credit_fees.held_on() == {"top_ups": 500, "cash": 0}
        assert first.payment_id != second.payment_id

    def test_a_fee_not_known_yet_holds_the_sale_back(self, credit_on, rocket):  # noqa: F811
        team, lead = rocket
        anna = _person()
        _top_up(anna, 2000, fee=None)
        beer = credit_sales.add_item(lead, team=team, name="Beer", price_cents=200)
        credit_sales.sell(anna, beer)
        db.session.commit()
        _teams_pay()

        money = team_money.summary(team)

        assert (money["earned"], money["waiting_for_fee"]) == (0, 200)

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

        saved = send(client, "PUT", "/api/v1/admin/money/settings",
                     {"fee_payer": "team", "credit_share_bps": 250, "difference_emails": False})

        assert saved.status_code == 200 and saved.get_json()["changed"]
        assert client.get("/api/v1/admin/money/settings").get_json() == {
            "fee_payer": "team", "credit_share_bps": 250, "difference_emails": False}

    def test_the_team_money_page_shows_the_statement(self, client, rocket):
        team, lead = rocket
        _teams_pay()
        payment = _paid(team, lead, cents=2500)
        _in_the_book(payment, fee=63)
        signed_in(client, _treasurer())

        body = client.get(f"/api/v1/admin/money/{team.slug}").get_json()

        statement = body["statement"]
        assert statement["teams_carry_fees"] is True and statement["earned"] == body["earned"] == 2437
        assert [line["kind"] for line in statement["lines"]] == ["fee_paid", "stripe_fee"]
        assert body["payments"][0]["fee"] == 63 and body["payments"][0]["counts"] == 2437

    def test_not_for_a_member(self, client, app):
        signed_in(client, _person())
        assert client.get("/api/v1/admin/money/settings").status_code == 403
