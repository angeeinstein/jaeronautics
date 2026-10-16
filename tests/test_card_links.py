"""Linking a card at a reader with a QR code (services/card_links.py,
docs/cards-plan.md, docs/device-protocol.md section 7.4).

The reader is driven as its firmware drives it -- its key as a bearer token --
and the phone as the page does: opening the link, then linking it signed in.
"""
# The fixtures of test_cards are imported, and so named again as arguments.
# ruff: noqa: F811
from datetime import timedelta

from flask import g

from api_helpers import send
from api_helpers import signed_in as _signed_in
from conftest import db
from aeronautics_members.db_models import CardLink, DeviceRequest
from aeronautics_members.services import card_links, cards, devices
from aeronautics_members.services.clock import get_now_utc
from test_cards import CARD, _card, anna, beer, reader  # noqa: F401
from test_credit import credit_on, fake_stripe  # noqa: F401
from test_teams_flow import _person


def signed_in(client, user):
    # The test's app context remembers who was signed in for an earlier request.
    g.pop("_login_user", None)
    return _signed_in(client, user)


def _check(app, key, link_id):
    """The reader asking about its link."""
    return app.test_client().get(f"/api/v1/device/card-links/{link_id}", headers={"Authorization": f"Bearer {key}"})


def _state(app, key, link_id):
    response = _check(app, key, link_id)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _token(link):
    return link["url"].rsplit("/", 1)[1]


def _open(client, token, claim=None):
    return send(client, "POST", f"/api/v1/card-links/{token}/open", {"claim": claim})


def _use(client, token, claim, name=None):
    return send(client, "POST", f"/api/v1/card-links/{token}", {"claim": claim, "name": name})


def _offered(app, key, card=CARD):
    answer = _card(app, key, card)
    assert answer["result"] == "refused" and answer["reason"] == "card_unknown"
    return answer["link"]


class TestTheReader:
    def test_an_unknown_card_is_answered_with_a_link_for_a_qr_code(self, app, reader):
        _device, key = reader
        link = _offered(app, key)

        assert link["url"].startswith("https://members.test/link/")
        assert link["show_for"] == 30 and link["check_every"] == 2
        # Nothing of the card is in the address.
        assert CARD not in link["url"].upper() and CARD[-4:] not in link["url"].upper()
        assert _state(app, key, link["id"]) == {"state": "waiting", "first_name": None}
        stored = db.session.get(CardLink, link["id"])
        assert stored.card_hash == cards.id_hash(CARD) and stored.card_tail == "E5F6"

    def test_asking_is_not_listed_on_the_devices_page(self, app, reader):
        device, key = reader
        link = _offered(app, key)
        before = db.session.query(DeviceRequest).filter_by(device_id=device.id).count()
        for _ in range(3):
            _state(app, key, link["id"])
        assert db.session.query(DeviceRequest).filter_by(device_id=device.id).count() == before

    def test_another_devices_link_is_unknown_to_it(self, app, reader):
        device, key = reader
        link = _offered(app, key)
        _other, other_key = devices.add(None, name="Other reader", type="sales_point", settings={"items": []})
        db.session.commit()

        refused = _check(app, other_key, link["id"])
        assert refused.status_code == 404 and refused.get_json()["error"]["code"] == "card_link_unknown"


class TestThePhone:
    def test_opened_the_reader_moves_on_and_the_card_is_linked_signed_in(self, app, client, reader, anna):
        _device, key = reader
        link = _offered(app, key)
        token = _token(link)

        opened = _open(client, token)
        assert opened.status_code == 200, opened.get_json()
        body = opened.get_json()
        assert body["card"] == "…E5F6" and body["reader"] == "Fridge reader" and body["signed_in"] is False
        assert _state(app, key, link["id"])["state"] == "opened"

        # Signing in comes after; the same phone goes on with its claim.
        signed_in(client, anna)
        again = _open(client, token, body["claim"])
        assert again.status_code == 200 and again.get_json()["signed_in"] is True
        linked = _use(client, token, body["claim"], name="Student card")
        assert linked.status_code == 201, linked.get_json()
        assert linked.get_json()["first_name"] == "Anna"
        card = linked.get_json()["card"]
        assert (card["name"], card["tail"], card["linked_how"]) == (
            "Student card", "…E5F6", "Tapped on a reader, linked by its owner")
        assert [mine.id_tail for mine in cards.cards_of(anna)] == ["E5F6"]
        assert _state(app, key, link["id"]) == {"state": "linked", "first_name": "Anna"}

        # The next tap is an ordinary one: the menu.
        assert _card(app, key)["result"] == "menu"
        used = _use(client, token, body["claim"])
        assert used.status_code == 409 and used.get_json()["error"]["code"] == "card_link_used"

    def test_the_first_phone_keeps_it(self, app, client, reader, anna):
        _device, key = reader
        token = _token(_offered(app, key))
        claim = _open(client, token).get_json()["claim"]

        other_phone = app.test_client()
        for refused in (_open(other_phone, token), _open(other_phone, token, "made-up")):
            assert refused.status_code == 409 and refused.get_json()["error"]["code"] == "card_link_taken"
        signed_in(other_phone, _person("someone@example.com"))
        refused = _use(other_phone, token, "made-up")
        assert refused.status_code == 409 and refused.get_json()["error"]["code"] == "card_link_taken"
        assert _open(client, token, claim).status_code == 200

    def test_tapping_again_starts_over_even_after_it_was_opened(self, app, client, reader, anna):
        _device, key = reader
        first = _offered(app, key)
        claim = _open(client, _token(first)).get_json()["claim"]

        second = _offered(app, key)
        assert second["id"] != first["id"]
        assert _state(app, key, first["id"])["state"] == "expired"
        gone = _open(client, _token(first), claim)
        assert gone.status_code == 404 and gone.get_json()["error"]["code"] == "card_link_expired"
        signed_in(client, anna)
        new_claim = _open(client, _token(second)).get_json()["claim"]
        assert _use(client, _token(second), new_claim).status_code == 201

    def test_after_ten_minutes_it_no_longer_works(self, app, client, reader):
        _device, key = reader
        link = _offered(app, key)
        stored = db.session.get(CardLink, link["id"])
        assert stored.expires_at - stored.created_at == timedelta(minutes=10)
        stored.expires_at = get_now_utc() - timedelta(seconds=1)
        db.session.commit()

        assert _state(app, key, link["id"])["state"] == "expired"
        gone = _open(client, _token(link))
        assert gone.status_code == 404 and gone.get_json()["error"]["code"] == "card_link_expired"

    def test_a_card_linked_another_way_meanwhile(self, app, client, reader, anna):
        _device, key = reader
        token = _token(_offered(app, key))
        claim = _open(client, token).get_json()["claim"]
        cards.link(anna, CARD, by=anna, how=cards.BY_PHONE)
        db.session.commit()

        refused = _open(client, token, claim)
        assert refused.status_code == 409 and refused.get_json()["error"]["code"] == "card_link_card_linked"
        signed_in(client, _person("someone@example.com"))
        refused = _use(client, token, claim)
        assert refused.status_code == 409 and refused.get_json()["error"]["code"] == "card_linked_elsewhere"

    def test_linking_needs_somebody_signed_in(self, app, client, reader):
        _device, key = reader
        token = _token(_offered(app, key))
        claim = _open(client, token).get_json()["claim"]
        assert _use(client, token, claim).status_code == 401

    def test_an_unknown_address(self, app, client):
        gone = _open(client, "no-such-link")
        assert gone.status_code == 404 and gone.get_json()["error"]["code"] == "card_link_expired"

    def test_the_page_opens_without_signing_in(self, app, client):
        assert client.get("/link/some-token").status_code == 200


class TestCleaningUp:
    def test_links_are_deleted_the_next_day(self, app, reader):
        _device, key = reader
        _offered(app, key)
        assert card_links.forget_old(get_now_utc() + timedelta(hours=23)) == 0
        assert devices.forget_old(get_now_utc() + timedelta(days=1, minutes=1)) >= 0
        assert db.session.query(CardLink).count() == 0

    def test_a_removed_reader_takes_its_links(self, app, reader):
        device, key = reader
        _offered(app, key)
        devices.remove(None, device)
        db.session.commit()
        assert db.session.query(CardLink).count() == 0

    def test_erasure_takes_the_link_that_linked_a_card(self, app, client, reader, anna):
        _device, key = reader
        token = _token(_offered(app, key))
        claim = _open(client, token).get_json()["claim"]
        signed_in(client, anna)
        assert _use(client, token, claim).status_code == 201

        cards.forget_for_erasure(anna)
        db.session.commit()
        assert db.session.query(CardLink).count() == 0
