"""Cards linked to people, and paying with one at a sales point
(services/cards.py, services/sales_points.py, docs/cards-plan.md).

A reader is driven here the way its ESP32 will drive it: plain HTTP with its
key as a bearer token, reporting a tap with the card's ID.
"""
from datetime import timedelta

import pytest

from conftest import db
from aeronautics_members.db_models import AuditLog, Card, CreditEntry, DeviceEvent
from aeronautics_members.services import ConflictError, ValidationError, cards, credit, credit_sales, devices
from aeronautics_members.services.clock import get_now_utc
from test_credit import credit_on, fake_stripe  # noqa: F401
from test_teams_flow import _person

CARD = "04A1B2C3D4E5F6"
OTHER_CARD = "04FFEEDDCCBB11"


@pytest.fixture
def anna(credit_on):  # noqa: F811
    person = _person("anna@example.com")
    credit.book_cash(None, person, 500)
    db.session.commit()
    return person


@pytest.fixture
def beer(credit_on):  # noqa: F811
    item = credit_sales.add_item(None, name="Beer", price_cents=150)
    db.session.commit()
    return item


@pytest.fixture
def reader(app, beer):
    device, key = devices.add(None, name="Fridge reader", type="sales_point", settings={"item": beer.id})
    db.session.commit()
    return device, key


def _tap(app, key, card=CARD, event_id=None):
    """A tap as the reader sends it; its answer."""
    event_id = event_id or f"tap-{get_now_utc().timestamp()}"
    response = app.test_client().post("/api/v1/device/events", headers={"Authorization": f"Bearer {key}"},
                                      json={"event_id": event_id, "kind": "tap", "data": {"card": card}})
    assert response.status_code == 200, response.get_json()
    return response.get_json()["answer"]


def _sales_of(person):
    return db.session.query(CreditEntry).filter_by(user_id=person.id, kind=credit.PURCHASE).all()


class TestCards:
    def test_a_card_is_kept_as_a_keyed_hash_and_its_end(self, anna):
        card = cards.link(anna, "04:a1:b2:c3:d4:e5:f6", name="Student card", by=anna, how=cards.BY_PHONE)
        db.session.commit()

        assert card.id_tail == "E5F6" and cards.tail(card) == "…E5F6"
        assert CARD not in (card.id_hash, card.linked_hash) and card.id_hash == cards.id_hash(CARD)
        # Not a plain hash: without the server's secret, the ID cannot be tried for.
        import hashlib
        assert card.id_hash != hashlib.sha256(CARD.encode()).hexdigest()
        # However it was read, it is the same card.
        assert cards.find("04a1b2c3d4e5f6") is card and cards.find("04-A1-B2-C3-D4-E5-F6") is card
        assert db.session.query(AuditLog).filter_by(event_type="card_linked").count() == 1

    @pytest.mark.parametrize("card_id", ["", "04A1", "XYZXYZXY", "04A1B2C3D4E5F6A"])
    def test_what_is_not_a_card_id(self, anna, card_id):
        with pytest.raises(ValidationError):
            cards.link(anna, card_id, by=anna, how=cards.BY_PHONE)

    def test_one_card_belongs_to_one_person(self, anna):
        cards.link(anna, CARD, by=anna, how=cards.BY_PHONE)
        other = _person("ben@example.com")
        db.session.commit()

        with pytest.raises(ConflictError) as again:
            cards.link(anna, CARD, by=anna, how=cards.BY_PHONE)
        assert again.value.code == "card_linked_already"
        with pytest.raises(ConflictError) as taken:
            cards.link(other, CARD, by=other, how=cards.BY_PHONE)
        assert taken.value.code == "card_linked_elsewhere"

    def test_removed_its_id_is_free_and_its_row_stays(self, anna):
        card = cards.link(anna, CARD, name="Key fob", by=anna, how=cards.BY_PHONE)
        cards.remove(anna, card)
        db.session.commit()

        assert cards.find(CARD) is None and cards.cards_of(anna) == []
        assert db.session.get(Card, card.id).name == "Key fob"
        other = _person("ben@example.com")
        assert cards.link(other, CARD, by=other, how=cards.BY_PHONE).user_id == other.id

    def test_disabled_by_its_owner_blocked_by_an_admin(self, anna):
        card = cards.link(anna, CARD, by=anna, how=cards.BY_PHONE)
        cards.disable(anna, card)
        assert cards.state(card) == cards.DISABLED
        cards.block(None, card)
        cards.enable(anna, card)
        # Switched on by its owner, still blocked: only an admin lifts that.
        assert cards.state(card) == cards.BLOCKED
        cards.unblock(None, card)
        assert cards.state(card) == cards.ACTIVE

    def test_at_most_ten_each(self, anna):
        for number in range(cards.MOST_CARDS):
            cards.link(anna, f"04{number:012X}", by=anna, how=cards.BY_PHONE)
        with pytest.raises(ConflictError):
            cards.link(anna, CARD, by=anna, how=cards.BY_PHONE)

    def test_erasure_forgets_the_ids(self, anna):
        card = cards.link(anna, CARD, by=anna, how=cards.BY_PHONE)
        cards.forget_for_erasure(anna)

        assert card.id_hash is None and card.linked_hash is None and card.removed_at is not None

    def test_a_changed_secret_key_is_noticed(self, app, anna):
        cards.link(anna, CARD, by=anna, how=cards.BY_PHONE)
        db.session.commit()
        assert not cards.key_changed()

        app.config["SECRET_KEY"] = "another-secret"
        try:
            assert cards.key_changed() and cards.find(CARD) is None
        finally:
            app.config["SECRET_KEY"] = "test-secret-key"


class TestSalesPoint:
    def test_a_tap_sells_the_item_for_its_seller(self, app, anna, beer, reader):
        device, key = reader
        card = cards.link(anna, CARD, name="Key fob", by=anna, how=cards.BY_PHONE)
        db.session.commit()

        answer = _tap(app, key)

        assert answer == {"result": "sold", "item": "Beer", "price_cents": 150, "balance_cents": 350}
        [sale] = _sales_of(anna)
        assert (sale.item_id, sale.card_id, sale.device_id, sale.channel) == (beer.id, card.id, device.id, "reader")
        assert sale.team_id is None
        assert card.last_used_device_id == device.id and card.last_used_at is not None

    def test_the_cards_id_is_not_kept_with_the_event(self, app, anna, reader):
        _device, key = reader
        cards.link(anna, CARD, by=anna, how=cards.BY_PHONE)
        db.session.commit()

        _tap(app, key, event_id="tap-1")

        event = db.session.query(DeviceEvent).filter_by(event_id="tap-1").one()
        assert event.data == {"card": "…E5F6"}
        # Said in words on the device's page.
        assert event.detail == "…E5F6 bought Beer, €1.50; €3.50 left"
        _tap(app, key, event_id="tap-2")
        refused = db.session.query(DeviceEvent).filter_by(event_id="tap-2").one()
        assert refused.detail == "…E5F6 refused: Sold to this card a moment ago."

    def test_a_double_tap_is_one_sale(self, app, anna, reader):
        _device, key = reader
        cards.link(anna, CARD, by=anna, how=cards.BY_PHONE)
        db.session.commit()

        assert _tap(app, key)["result"] == "sold"
        assert _tap(app, key) == {"result": "refused", "reason": "too_soon",
                                  "message": "Sold to this card a moment ago."}
        assert len(_sales_of(anna)) == 1

    def test_the_same_report_again_is_answered_from_the_first(self, app, anna, reader):
        _device, key = reader
        cards.link(anna, CARD, by=anna, how=cards.BY_PHONE)
        db.session.commit()

        first = _tap(app, key, event_id="tap-7")
        assert _tap(app, key, event_id="tap-7") == first
        assert len(_sales_of(anna)) == 1

    def test_after_a_few_seconds_it_sells_again(self, app, anna, reader):
        _device, key = reader
        cards.link(anna, CARD, by=anna, how=cards.BY_PHONE)
        db.session.commit()
        _tap(app, key)
        sale = _sales_of(anna)[0]
        sale.created_at = sale.created_at - timedelta(seconds=10)
        db.session.commit()

        assert _tap(app, key)["result"] == "sold"
        assert len(_sales_of(anna)) == 2

    @pytest.mark.parametrize("change, reason", [
        (lambda card, person: None, None),
        (lambda card, person: cards.disable(person, card), "card_disabled"),
        (lambda card, person: cards.block(None, card), "card_blocked"),
        (lambda card, person: setattr(person, "disabled_at", get_now_utc()), "account_off"),
    ])
    def test_a_card_that_may_not_buy(self, app, anna, reader, change, reason):
        _device, key = reader
        card = cards.link(anna, CARD, by=anna, how=cards.BY_PHONE)
        change(card, anna)
        db.session.commit()

        answer = _tap(app, key)

        if reason is None:
            assert answer["result"] == "sold"
        else:
            assert (answer["result"], answer["reason"]) == ("refused", reason) and not _sales_of(anna)

    def test_an_unknown_card(self, app, anna, reader):
        _device, key = reader
        assert _tap(app, key)["reason"] == "card_unknown"

    def test_not_enough_credit_says_what_is_left(self, app, reader):
        _device, key = reader
        poor = _person("ben@example.com")
        credit.book_cash(None, poor, 100)
        cards.link(poor, CARD, by=poor, how=cards.BY_PHONE)
        db.session.commit()

        answer = _tap(app, key)
        assert (answer["reason"], answer["balance_cents"]) == ("not_enough", 100)

    def test_an_item_switched_off_and_a_reader_without_one(self, app, anna, beer, reader):
        device, key = reader
        cards.link(anna, CARD, by=anna, how=cards.BY_PHONE)
        credit_sales.update_item(None, beer, active=False)
        db.session.commit()
        assert _tap(app, key)["reason"] == "item_off"

        devices.change(None, device, settings={"item": None})
        db.session.commit()
        assert _tap(app, key)["reason"] == "not_set_up"

    def test_credit_switched_off(self, app, anna, reader):
        _device, key = reader
        cards.link(anna, CARD, by=anna, how=cards.BY_PHONE)
        settings = credit.settings()
        credit.save_settings(None, enabled=False, product_id=settings.product_id,
                             min_top_up_cents=settings.min_top_up_cents, max_balance_cents=settings.max_balance_cents,
                             suggested_cents=settings.suggested_cents, eps=settings.eps)
        db.session.commit()
        assert _tap(app, key)["reason"] == "credit_off"

    def test_the_item_is_chosen_from_its_owners_price_list(self, app, beer, reader):
        device, _key = reader
        from aeronautics_members.services import device_types

        setting = device_types.get("sales_point").settings[0]
        assert [(choice.value, choice.label) for choice in setting.choices(None)] == [(beer.id, "Beer, €1.50")]
        with pytest.raises(ValidationError):
            devices.change(None, device, settings={"item": 9999})
        # Kept as it was once switched off, though it cannot be chosen any more.
        credit_sales.update_item(None, beer, active=False)
        assert devices.change(None, device, settings={"item": beer.id}).settings == {"item": beer.id}

    def test_a_team_reader_sells_only_the_teams_items(self, app, anna, beer, reader):
        from test_teams_flow import _team

        device, key = reader
        rocket = _team("Rocket")
        mate = credit_sales.add_item(None, team=rocket, name="Mate", price_cents=250)
        cards.link(anna, CARD, by=anna, how=cards.BY_PHONE)
        db.session.commit()

        # Given to the team: the association's beer no longer fits, and is cleared.
        devices.change(None, device, owner=rocket)
        assert device.settings == {"item": None}
        with pytest.raises(ValidationError):
            devices.change(None, device, settings={"item": beer.id})
        devices.change(None, device, settings={"item": mate.id})
        db.session.commit()

        assert _tap(app, key)["item"] == "Mate"
        assert _sales_of(anna)[0].team_id == rocket.id


class TestOwners:
    def test_a_device_has_a_random_code_and_a_name_only_if_given(self, app):
        device, _key = devices.add(None, type="test")
        other, _key = devices.add(None, type="test", name="Fridge")

        assert len(device.code) == 9 and device.code[4] == "-" and device.code != other.code
        assert device.name is None and devices.label(device) == device.code
        assert devices.label(other) == "Fridge"

    def test_the_board_it_speaks_from(self, app):
        device, key = devices.add(None, type="test")
        db.session.commit()
        client = app.test_client()

        def hello(mac):
            return client.get("/api/v1/device", headers={"Authorization": f"Bearer {key}",
                                                         "X-Device-Hardware": mac}).get_json()

        assert hello("24-6f-28-aa-bb-cc")["code"] == device.code
        assert device.hardware_id == "24:6F:28:AA:BB:CC"
        hello("24:6F:28:00:00:01")
        moved = db.session.query(DeviceEvent).filter_by(device_id=device.id, kind="hardware").all()
        assert moved[-1].detail == "Board 24:6F:28:00:00:01, was 24:6F:28:AA:BB:CC: the key is on another board"

    def test_an_owner_changes_only_what_is_theirs(self, app, beer, reader):
        device, _key = reader
        devices.change(None, device, name="Workshop fridge", place="Workshop", description="By the door",
                       enabled=False, settings={"item": beer.id}, by_owner=True)
        assert (device.name, device.place, device.description, device.enabled) == (
            "Workshop fridge", "Workshop", "By the door", False)
        # Who owns it is an admin's to change, not the owner's.
        from test_teams_flow import _team

        devices.change(None, device, owner=_team("Rocket"), by_owner=True)
        assert device.team_id is None


    def test_its_item_for_a_label(self, app, reader):
        _device, key = reader
        answer = app.test_client().get("/api/v1/device/resources/item",
                                       headers={"Authorization": f"Bearer {key}"}).get_json()
        assert answer["data"] == {"item": "Beer", "price_cents": 150, "seller": "Joanneum Aeronautics",
                                  "on_sale": True}

    def test_removing_the_reader_keeps_its_sales(self, app, anna, reader):
        device, key = reader
        card = cards.link(anna, CARD, by=anna, how=cards.BY_PHONE)
        db.session.commit()
        _tap(app, key)

        devices.remove(None, device)
        db.session.commit()

        [sale] = _sales_of(anna)
        assert sale.device_id is None and sale.card_id == card.id and card.last_used_device_id is None


class TestLinkingAtAReader:
    def test_the_next_unknown_card_is_linked_while_an_admin_waits(self, app, anna, reader):
        device, key = reader
        cards.wait_at_reader(None, anna, device)
        db.session.commit()

        assert _tap(app, key) == {"result": "linked", "card": "Card"}
        [card] = cards.cards_of(anna)
        assert card.linked_how == cards.BY_READER and cards.waiting_for(anna) == []
        # The wait is over: the next tap buys.
        assert _tap(app, key, event_id="next")["result"] in ("sold", "refused")

    def test_a_known_card_buys_while_waiting(self, app, anna, reader):
        device, key = reader
        cards.link(anna, CARD, by=anna, how=cards.BY_PHONE)
        other = _person("ben@example.com")
        cards.wait_at_reader(None, other, device)
        db.session.commit()

        assert _tap(app, key)["result"] == "sold"
        assert cards.waiting_for(other) != []

    def test_the_wait_ends_after_a_minute(self, app, anna, reader):
        device, key = reader
        cards.wait_at_reader(None, anna, device, now=get_now_utc() - timedelta(minutes=2))
        db.session.commit()

        assert _tap(app, key)["reason"] == "card_unknown" and cards.cards_of(anna) == []


class TestPages:
    @pytest.fixture
    def admin(self, app):
        from test_admin_reviews import _staff

        return _staff("admin@example.org", "admin")

    def test_my_cards_scanned_renamed_switched_off_and_removed(self, client, anna, reader):
        from api_helpers import send, signed_in

        signed_in(client, anna)
        assert client.get("/api/v1/me").get_json()["cards_area"] is True

        linked = send(client, "POST", "/api/v1/account/cards", {"card_id": "04:a1:b2:c3:d4:e5:f6", "name": "Fob"})
        assert linked.status_code == 201
        [card] = linked.get_json()["items"]
        assert (card["name"], card["tail"], card["state"]) == ("Fob", "…E5F6", "active")
        assert "04A1" not in str(linked.get_json())

        changed = send(client, "PATCH", f"/api/v1/account/cards/{card['id']}", {"name": "Key fob", "enabled": False})
        assert changed.get_json()["items"][0]["state"] == "disabled"
        removed = send(client, "DELETE", f"/api/v1/account/cards/{card['id']}")
        assert removed.get_json()["items"] == []

    def test_somebody_elses_card_is_not_found(self, client, anna):
        from api_helpers import send, signed_in

        card = cards.link(anna, CARD, by=anna, how=cards.BY_PHONE)
        db.session.commit()
        signed_in(client, _person("ben@example.com"))

        assert send(client, "DELETE", f"/api/v1/account/cards/{card.id}").status_code == 404
        assert send(client, "POST", "/api/v1/account/cards", {"card_id": CARD}).get_json()["error"]["code"] == \
            "card_linked_elsewhere"

    def test_no_cards_page_before_a_reader_exists(self, client, anna):
        from api_helpers import signed_in

        signed_in(client, anna)
        assert client.get("/api/v1/me").get_json()["cards_area"] is False

    def test_admins_block_link_and_wait_at_a_reader(self, client, admin, anna, reader):
        from api_helpers import send, signed_in

        device, _key = reader
        card = cards.link(anna, CARD, by=anna, how=cards.BY_PHONE)
        db.session.commit()
        signed_in(client, admin)

        listed = client.get("/api/v1/admin/cards").get_json()["items"]
        assert [(item["tail"], item["owner"]["email"]) for item in listed] == [("…E5F6", "anna@example.com")]
        blocked = send(client, "PATCH", f"/api/v1/admin/cards/{card.id}", {"blocked": True}).get_json()
        assert blocked["state"] == "blocked"

        url = f"/api/v1/admin/accounts/{anna.id}/cards"
        person = send(client, "POST", url, {"card_id": OTHER_CARD, "name": "Spare"}).get_json()
        assert [item["name"] for item in person["items"]] == ["Card", "Spare"]
        assert person["readers"] == [{"id": device.id, "name": "Fridge reader", "place": None}]
        waiting = send(client, "POST", f"{url}/wait", {"device_id": device.id}).get_json()
        assert waiting["waiting"]["reader"]["name"] == "Fridge reader"
        assert send(client, "DELETE", f"{url}/wait").get_json()["waiting"] is None
        assert send(client, "DELETE", f"/api/v1/admin/cards/{card.id}").get_json()["state"] == "removed"

    def test_members_may_not_manage_others_cards(self, client, anna):
        from api_helpers import signed_in

        signed_in(client, anna)
        assert client.get("/api/v1/admin/cards").status_code == 403

    def test_a_sale_at_a_reader_names_the_card_and_the_reader(self, app, client, anna, reader):
        from api_helpers import signed_in

        _device, key = reader
        cards.link(anna, CARD, name="Key fob", by=anna, how=cards.BY_PHONE)
        db.session.commit()
        _tap(app, key)
        signed_in(client, anna)

        entry = client.get("/api/v1/account/credit").get_json()["entries"][0]
        assert (entry["description"], entry["card"], entry["reader"]) == ("Beer", "Key fob", "Fridge reader")

    def test_the_data_export_has_the_cards_without_their_ids(self, anna):
        from aeronautics_members.services.privacy import export_account_data

        cards.link(anna, CARD, name="Key fob", by=anna, how=cards.BY_PHONE)
        exported = export_account_data(anna)["cards"]
        assert exported[0]["name"] == "Key fob" and exported[0]["id_ends_with"] == "…E5F6"
        assert CARD not in str(exported)


class TestOwnersPages:
    @pytest.fixture
    def rocket(self, app):
        from test_teams_flow import _led, switched_on  # noqa: F401

        from aeronautics_members.services import teams

        teams.save_team_settings(None, enabled=True, label_singular="", label_plural="")
        team, lead = _led()
        db.session.commit()
        return team, lead

    def test_a_lead_runs_the_teams_reader_and_sees_its_taps(self, app, client, anna, rocket):
        from api_helpers import send, signed_in

        team, lead = rocket
        mate = credit_sales.add_item(None, team=team, name="Mate", price_cents=250)
        device, key = devices.add(None, type="sales_point", owner=team)
        cards.link(anna, CARD, by=anna, how=cards.BY_PHONE)
        db.session.commit()
        signed_in(client, lead)
        assert client.get(f"/api/v1/teams/{team.slug}").get_json()["has_devices"] is True

        url = f"/api/v1/teams/{team.slug}/manage/devices"
        [owned] = client.get(url).get_json()["items"]
        assert owned["label"] == device.code and owned["name"] is None
        assert [c["label"] for c in owned["fields"][0]["choices"]] == ["Mate, €2.50"]

        changed = send(client, "PATCH", f"{url}/{device.id}",
                       {"name": "Rocket fridge", "place": "Hangar", "settings": {"item": mate.id}})
        assert changed.status_code == 200, changed.get_json()
        _tap(app, key)
        [owned] = client.get(url).get_json()["items"]
        assert owned["label"] == "Rocket fridge"
        assert owned["recent"][0]["detail"] == "…E5F6 bought Mate, €2.50; €2.50 left"

    def test_a_lead_cannot_reach_another_owners_device(self, app, client, rocket, reader):
        from api_helpers import send, signed_in

        team, lead = rocket
        association_device, _key = reader
        signed_in(client, lead)
        answer = send(client, "PATCH", f"/api/v1/teams/{team.slug}/manage/devices/{association_device.id}",
                      {"enabled": False})
        assert answer.status_code == 404
        assert send(client, "PATCH", f"/api/v1/teams/{team.slug}/manage/devices/{association_device.id}",
                    {"settings": {"item": 1}}).status_code == 404

    def test_an_owner_may_not_change_what_is_technical(self, app, client, rocket):
        from api_helpers import send, signed_in

        team, lead = rocket
        device, _key = devices.add(None, type="test", owner=team)
        db.session.commit()
        signed_in(client, lead)
        refused = send(client, "PATCH", f"/api/v1/teams/{team.slug}/manage/devices/{device.id}",
                       {"settings": {"greeting": "Hi"}})
        assert refused.status_code == 400 and refused.get_json()["error"]["code"] == "device_setting_admin"

    def test_a_member_without_a_role_sees_no_devices(self, app, client, anna, rocket):
        from api_helpers import signed_in

        team, _lead = rocket
        devices.add(None, type="sales_point", owner=team)
        db.session.commit()
        signed_in(client, anna)
        assert client.get(f"/api/v1/teams/{team.slug}/manage/devices").status_code == 403

    def test_the_association_s_devices_for_the_treasurer(self, app, client, reader):
        from api_helpers import signed_in
        from test_admin_reviews import _staff

        signed_in(client, _staff("treasurer@example.org", "treasurer"))
        items = client.get("/api/v1/admin/credit/devices").get_json()["items"]
        assert [item["code"] for item in items] == [reader[0].code]

    def test_admins_give_a_device_to_a_team(self, app, client, rocket):
        from api_helpers import send, signed_in
        from test_admin_reviews import _staff

        team, _lead = rocket
        signed_in(client, _staff("admin@example.org", "admin"))
        added = send(client, "POST", "/api/v1/admin/devices", {"type": "sales_point", "owner": team.slug})
        assert added.status_code == 201, added.get_json()
        device = added.get_json()["device"]
        assert (device["owner"], device["owner_label"], device["name"]) == (team.slug, team.name, None)
        listed = client.get("/api/v1/admin/devices").get_json()
        assert [o["value"] for o in listed["owners"]] == ["association", team.slug]
        moved = send(client, "PATCH", f"/api/v1/admin/devices/{device['id']}", {"owner": "association"})
        assert moved.get_json()["owner"] == "association"


def test_the_key_comes_with_a_firmware_starter(app, client):
    from api_helpers import send, signed_in
    from test_admin_reviews import _staff

    signed_in(client, _staff("admin@example.org", "admin"))
    added = send(client, "POST", "/api/v1/admin/devices", {"type": "sales_point"}).get_json()
    starter, key, code = added["starter"], added["key"], added["device"]["code"]

    assert added["starter_name"] == f"device-{code}-starter.md"
    assert f'const char* DEVICE_KEY = "{key}";' in starter
    assert 'const char* SERVER = "https://members.test/api/v1/device";' in starter
    assert "X-Device-Hardware" in starter and '"kind": "tap"' in starter and "too_soon" in starter
    assert "setInsecure" not in starter.split("```cpp")[1]
