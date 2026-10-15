"""A sales point switching its outputs after a sale -- a fridge's lock, a
coffee machine's relay (services/sales_points.py, "actions";
docs/devices-plan.md, "Actions after a sale")."""
import pytest

from conftest import db
from aeronautics_members.db_models import DeviceEvent
from aeronautics_members.services import ValidationError, cards, credit_sales, devices
from test_cards import CARD, _buy, _card, anna, beer, reader  # noqa: F401
from test_credit import credit_on, fake_stripe  # noqa: F401


def _actions(*lines, on=True):
    return {"on": on, "lines": [dict(zip(("item", "output", "seconds"), line)) for line in lines]}


@pytest.fixture
def tea(credit_on):  # noqa: F811
    item = credit_sales.add_item(None, name="Tea", price_cents=100, icon="tea")
    db.session.commit()
    return item


@pytest.fixture
def linked(anna):  # noqa: F811
    cards.link(anna, CARD, name="Key fob", by=anna, how=cards.BY_PHONE)
    db.session.commit()
    return anna


def _sell(app, key, item):
    menu = _card(app, key)
    return _buy(app, key, menu["ticket"], item.id)


def test_the_device_says_how_many_outputs_it_has(app, reader):  # noqa: F811
    device, key = reader

    response = app.test_client().get("/api/v1/device", headers={"Authorization": f"Bearer {key}",
                                                                "X-Device-Outputs": "4"})

    assert response.status_code == 200 and device.outputs == 4
    assert db.session.query(DeviceEvent).filter_by(device_id=device.id, kind="outputs").one().detail == "Outputs: 4"
    # Not a number from 0 to 16: nothing changes.
    for wrong in ("17", "four", "-1"):
        app.test_client().get("/api/v1/device", headers={"Authorization": f"Bearer {key}", "X-Device-Outputs": wrong})
        assert device.outputs == 4


def test_a_sale_switches_the_outputs_for_any_sale_and_for_its_item(app, linked, beer, tea, reader):  # noqa: F811
    device, key = reader
    device.outputs = 4
    devices.change(None, device, settings={"items": [beer.id, tea.id],
                                           "actions": _actions((None, 1, 5), (tea.id, 2, 0.5))})
    db.session.commit()

    assert _sell(app, key, beer)["actions"] == [{"output": 1, "seconds": 5.0}]
    assert _sell(app, key, tea)["actions"] == [{"output": 1, "seconds": 5.0}, {"output": 2, "seconds": 0.5}]

    # Off: kept, but nothing switches.
    devices.change(None, device, settings={"actions": {**device.settings["actions"], "on": False}})
    db.session.commit()
    assert _sell(app, key, tea)["actions"] == []
    assert len(device.settings["actions"]["lines"]) == 2


def test_an_output_the_device_does_not_have_is_left_out(app, linked, beer, reader):  # noqa: F811
    device, key = reader
    devices.change(None, device, settings={"actions": _actions((None, 1, 3), (None, 6, 3))})
    device.outputs = 4
    db.session.commit()

    assert _sell(app, key, beer)["actions"] == [{"output": 1, "seconds": 3.0}]


@pytest.mark.parametrize("lines, message", [
    ([(999, 1, 5)], "Choose any sale or an item of the menu."),
    ([(None, 0, 5)], "An output from 1 to 16."),
    ([(None, 17, 5)], "An output from 1 to 16."),
    ([(None, 1, 0)], "Between 0.1 and 600 seconds."),
    ([(None, 1, 601)], "Between 0.1 and 600 seconds."),
    ([(None, 1, True)], "Between 0.1 and 600 seconds."),
    ([(None, 1, 5)] * 17, "At most 16 actions."),
])
def test_what_cannot_be_an_action_is_refused(app, beer, reader, lines, message):  # noqa: F811
    device, _key = reader

    with pytest.raises(ValidationError) as refused:
        devices.change(None, device, settings={"actions": _actions(*lines)})

    assert refused.value.details["fields"] == {"settings.actions": message}


@pytest.fixture
def rocket(app):
    from test_teams_flow import _led, switched_on  # noqa: F401

    from aeronautics_members.services import teams

    teams.save_team_settings(None, enabled=True, label_singular="", label_plural="")
    team, lead = _led()
    db.session.commit()
    return team, lead


def test_another_owners_item_is_refused(app, beer, reader, rocket):  # noqa: F811
    device, _key = reader
    theirs = credit_sales.add_item(None, team=rocket[0], name="Mate", price_cents=200)
    db.session.commit()

    with pytest.raises(ValidationError):
        devices.change(None, device, settings={"actions": _actions((theirs.id, 1, 5))})


def test_an_items_actions_go_when_it_leaves_the_menu_or_the_list(app, beer, tea, reader):  # noqa: F811
    device, _key = reader
    devices.change(None, device, settings={"items": [beer.id, tea.id],
                                           "actions": _actions((None, 1, 5), (tea.id, 2, 1), (beer.id, 3, 1))})
    db.session.commit()

    devices.change(None, device, settings={"items": [beer.id]})
    assert device.settings["actions"]["lines"] == [{"item": None, "output": 1, "seconds": 5.0},
                                                   {"item": beer.id, "output": 3, "seconds": 1.0}]

    credit_sales.delete_item(None, beer)
    db.session.commit()
    assert device.settings["items"] == []
    assert device.settings["actions"]["lines"] == [{"item": None, "output": 1, "seconds": 5.0}]


def test_the_owner_sees_the_outputs_and_sets_the_actions_on_the_sales_page(app, client, credit_on, rocket):  # noqa: F811
    from api_helpers import send, signed_in

    team, lead = rocket
    mate = credit_sales.add_item(None, team=team, name="Mate", price_cents=250)
    device, _key = devices.add(None, type="sales_point", owner=team, settings={"items": [mate.id]})
    device.outputs = 2
    db.session.commit()
    signed_in(client, lead)
    url = f"/api/v1/teams/{team.slug}/manage/devices"

    [owned] = client.get(url).get_json()["items"]
    assert owned["outputs"] == 2
    field = next(field for field in owned["fields"] if field["name"] == "actions")
    assert (field["kind"], field["with_prices"]) == ("actions", True)

    response = send(client, "PATCH", f"{url}/{device.id}", {"settings": {"actions": _actions((mate.id, 1, 2.5))}})

    assert response.status_code == 200, response.get_json()
    assert device.settings["actions"] == {"on": True, "lines": [{"item": mate.id, "output": 1, "seconds": 2.5}]}
