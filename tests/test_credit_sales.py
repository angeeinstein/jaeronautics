"""Selling for credit: price lists, sales, sales taken back, and what each seller
earned -- a team's sales counting towards what it is owed (services/credit_sales.py,
services/team_money.py).
"""
import pytest

from api_helpers import send, signed_in
from conftest import db
from aeronautics_members.services import ConflictError, ValidationError, credit, credit_sales, team_money
from test_credit import credit_on, fake_stripe  # noqa: F401
from test_team_money import _paid, _treasurer
from test_teams_flow import _led, _login, _person, switched_on  # noqa: F401


@pytest.fixture
def anna(credit_on):  # noqa: F811
    person = _person()
    credit.book_cash(None, person, 1500)
    db.session.commit()
    return person


@pytest.fixture
def rocket(app, switched_on):  # noqa: F811
    team, lead = _led()
    return team, lead


class TestPriceLists:
    def test_the_association_and_each_team_have_their_own(self, credit_on, rocket):  # noqa: F811
        team, lead = rocket
        coffee = credit_sales.add_item(None, name="Coffee", price_cents=120)
        beer = credit_sales.add_item(lead, team=team, name="  Beer ", price_cents=200)
        credit_sales.add_item(lead, team=team, name="Mate", price_cents=250)

        assert [item.name for item in credit_sales.items()] == ["Coffee"]
        assert [item.name for item in credit_sales.items(team)] == ["Beer", "Mate"]
        assert beer.team_id == team.id and coffee.team_id is None

    def test_switched_off_and_moved(self, credit_on, rocket):  # noqa: F811
        team, lead = rocket
        beer = credit_sales.add_item(lead, team=team, name="Beer", price_cents=200)
        mate = credit_sales.add_item(lead, team=team, name="Mate", price_cents=250)

        credit_sales.move_item(lead, mate, -1)
        credit_sales.update_item(lead, beer, active=False)

        assert [item.name for item in credit_sales.items(team)] == ["Mate"]
        assert [item.name for item in credit_sales.items(team, include_inactive=True)] == ["Mate", "Beer"]

    @pytest.mark.parametrize("name, price, field", [("", 100, "name"), ("Beer", 0, "price_cents"),
                                                     ("Beer", 10**7, "price_cents")])
    def test_what_is_refused(self, credit_on, name, price, field):  # noqa: F811
        with pytest.raises(ValidationError) as refused:
            credit_sales.add_item(None, name=name, price_cents=price)
        assert field in refused.value.details["fields"]

    def test_an_item_of_another_list_is_not_found(self, credit_on, rocket):  # noqa: F811
        team, lead = rocket
        beer = credit_sales.add_item(lead, team=team, name="Beer", price_cents=200)
        from aeronautics_members.services import NotFoundError

        with pytest.raises(NotFoundError):
            credit_sales.item_of(beer.id, None)


class TestSelling:
    def test_a_sale_names_the_item_and_the_seller(self, anna, rocket):
        team, lead = rocket
        beer = credit_sales.add_item(lead, team=team, name="Beer", price_cents=200)

        entry = credit_sales.sell(anna, beer, by=_treasurer())

        assert (entry.kind, entry.amount_cents, entry.team_id, entry.item_id, entry.channel) == (
            "purchase", -200, team.id, beer.id, "booked")
        assert credit.balance_of(anna) == 1300

    def test_at_the_price_of_the_day(self, anna):
        coffee = credit_sales.add_item(None, name="Coffee", price_cents=120)
        sold = credit_sales.sell(anna, coffee)
        credit_sales.update_item(None, coffee, price_cents=150)

        assert sold.amount_cents == -120
        assert credit_sales.sell(anna, coffee).amount_cents == -150

    def test_refused_without_enough_credit_or_when_off(self, anna):
        champagne = credit_sales.add_item(None, name="Champagne", price_cents=5000)
        with pytest.raises(ConflictError):
            credit_sales.sell(anna, champagne)
        coffee = credit_sales.add_item(None, name="Coffee", price_cents=120)
        credit_sales.update_item(None, coffee, active=False)
        with pytest.raises(ConflictError):
            credit_sales.sell(anna, coffee)

    def test_taken_back_once(self, anna, rocket):
        team, lead = rocket
        beer = credit_sales.add_item(lead, team=team, name="Beer", price_cents=200)
        sale = credit_sales.sell(anna, beer)
        db.session.flush()

        undone = credit_sales.take_back(lead, sale, note="Wrong fridge")

        assert (undone.kind, undone.amount_cents, undone.reverses_id) == ("taken_back", 200, sale.id)
        assert credit.balance_of(anna) == 1500
        assert credit_sales.taken_back_ids([sale]) == {sale.id}
        with pytest.raises(ConflictError):
            credit_sales.take_back(lead, sale)

    def test_only_a_sale_is_taken_back(self, anna):
        with pytest.raises(ConflictError):
            credit_sales.take_back(None, credit.history(anna)[0])


class TestWhatSellersEarned:
    def test_a_teams_sales_count_towards_what_it_is_owed(self, anna, rocket):
        team, lead = rocket
        _paid(team, lead, cents=2500)
        beer = credit_sales.add_item(lead, team=team, name="Beer", price_cents=200)
        mate = credit_sales.add_item(lead, team=team, name="Mate", price_cents=250)
        credit_sales.sell(anna, beer)
        credit_sales.sell(anna, beer)
        wrong = credit_sales.sell(anna, mate)
        db.session.flush()
        credit_sales.take_back(lead, wrong)
        db.session.commit()

        money = team_money.summary(team)

        assert (money["fees"], money["sales"]["earned"], money["earned"], money["open"]) == (2500, 400, 2900, 2900)
        assert money["sales"]["count"] == 2
        assert money["sales"]["by_item"] == [{"name": "Beer", "count": 2, "earned": 400}]

    def test_the_associations_sales_are_not_a_teams(self, anna, rocket):
        team, _lead = rocket
        coffee = credit_sales.add_item(None, name="Coffee", price_cents=120)
        credit_sales.sell(anna, coffee)
        db.session.commit()

        assert team_money.summary(team)["sales"]["earned"] == 0
        assert credit_sales.sales_summary()["earned"] == 120

    def test_a_payout_takes_sales_too(self, anna, rocket):
        team, lead = rocket
        beer = credit_sales.add_item(lead, team=team, name="Beer", price_cents=200)
        credit_sales.sell(anna, beer)
        db.session.commit()

        team_money.record_payout(_treasurer(), team, amount="2.00")

        assert team_money.summary(team)["open"] == 0


class TestThroughTheApi:
    def test_the_association_keeps_its_prices(self, client, credit_on):  # noqa: F811
        signed_in(client, _treasurer())

        added = send(client, "POST", "/api/v1/admin/credit/items", {"name": "Coffee", "price_cents": 120})
        assert added.status_code == 201, added.get_json()
        item_id = added.get_json()["items"][0]["id"]
        changed = send(client, "PUT", f"/api/v1/admin/credit/items/{item_id}", {"price_cents": 150})

        assert changed.get_json()["items"][0]["price_cents"] == 150

    def test_a_lead_keeps_the_teams_prices_a_member_does_not(self, client, credit_on, rocket):  # noqa: F811
        team, lead = rocket
        signed_in(client, lead)
        path = f"/api/v1/teams/{team.slug}/manage/prices"

        assert send(client, "POST", path, {"name": "Beer", "price_cents": 200}).status_code == 201
        assert [item["name"] for item in client.get(path).get_json()["items"]] == ["Beer"]

        _login(client, _person("someone@example.com", "Some", "One").id)
        assert client.get(path).status_code in (403, 404)

    def test_booking_a_sale_and_taking_it_back(self, client, anna, rocket):
        team, lead = rocket
        beer = credit_sales.add_item(lead, team=team, name="Beer", price_cents=200)
        db.session.commit()
        signed_in(client, _treasurer())

        sold = send(client, "POST", f"/api/v1/admin/credit/{anna.id}/sale", {"item_id": beer.id})
        assert sold.status_code == 200, sold.get_json()
        body = sold.get_json()
        assert body["balance_cents"] == 1300
        sale = body["entries"][0]
        assert sale["kind"] == "purchase" and sale["seller"] == "Rocket" and sale["may_take_back"] is True

        back = send(client, "POST", f"/api/v1/admin/credit/{anna.id}/entries/{sale['id']}/take-back", {})
        assert back.status_code == 200 and back.get_json()["balance_cents"] == 1500
        assert back.get_json()["entries"][1]["may_take_back"] is False

    def test_the_teams_money_shows_its_sales(self, client, anna, rocket):
        team, lead = rocket
        beer = credit_sales.add_item(lead, team=team, name="Beer", price_cents=200)
        credit_sales.sell(anna, beer)
        db.session.commit()
        signed_in(client, lead)

        funds = client.get(f"/api/v1/teams/{team.slug}/money").get_json()

        assert funds["earned"] == 200 and funds["sales"]["earned"] == 200
        assert funds["sales"]["by_item"] == [{"name": "Beer", "count": 1, "earned": 200}]
