"""Reviews (api/admin_reviews.py; the decisions in services/reviews.py; the page
is frontend/src/pages/admin/Reviews.tsx).

Who decides what, one queue, each item decided on its own: tests/test_admin_reviews.py.
Renaming on the forum: tests/test_forum_rename.py. Here: what the answers say.
"""
from conftest import db, make_member
from aeronautics_members.forum_service import ForumProviderError
from api_helpers import send, signed_in
from test_admin_reviews import _name_change, _picture, _staff, quiet_forum  # noqa: F401

API = "/api/v1/admin/reviews"


def test_signed_out(client):
    assert client.get(API).status_code == 401


def test_a_picture_in_the_queue(client):
    member = make_member(email="photo@example.com", first_name="Petra", last_name="Pending")
    member.user.forum_username = "PendingP_L25"
    db.session.commit()
    picture = _picture(member, token="tok-1")
    signed_in(client, _staff("boss@example.org", "admin"))

    [item] = client.get(API).get_json()["queue"]

    assert item["kind"] == "picture" and item["id"] == picture.id and item["at"].endswith("Z")
    assert item["person"] == {"user_id": member.user_id, "name": "Petra Pending", "email": "photo@example.com"}
    assert item["picture"] == {"image_url": "/forum/avatar/public/tok-1", "forum_username": "PendingP_L25"}
    assert item["change"] is None


def test_a_name_change_offers_the_new_forum_username(client):
    member = make_member(email="anna@example.com", first_name="Anna", last_name="Huber", year_group="LAV25")
    member.user.forum_username = "HuberA_L25"
    db.session.commit()
    _name_change(member, last_name="Maier")
    signed_in(client, _staff("boss@example.org", "admin"))

    [item] = client.get(API).get_json()["queue"]

    assert item["change"]["requested_full_name"] == "Anna Maier"
    assert item["change"]["forum_username"] == {"current": "HuberA_L25", "suggested": "MaierA_L25"}


def test_no_forum_username_question_without_a_forum_name(client):
    member = make_member(email="anna@example.com")
    _name_change(member, last_name="Maier")
    signed_in(client, _staff("boss@example.org", "admin"))

    assert client.get(API).get_json()["queue"][0]["change"]["forum_username"] is None


def test_a_decided_item_is_a_conflict_not_a_second_decision(client, quiet_forum):  # noqa: F811
    member = make_member(email="twice@example.com")
    change = _name_change(member)
    signed_in(client, _staff("boss@example.org", "admin"))
    assert send(client, "POST", f"{API}/name-changes/{change.id}/reject", {}).status_code == 204

    response = send(client, "POST", f"{API}/name-changes/{change.id}/approve", {})

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "already_decided"


def test_one_that_never_existed_is_the_same_answer(client):
    signed_in(client, _staff("boss@example.org", "admin"))

    assert send(client, "POST", f"{API}/pictures/99999/approve", {}).status_code == 409


def test_a_forum_that_refuses_the_picture_is_a_bad_gateway(client, monkeypatch):
    from aeronautics_members.services import forum as forum_module

    class Refusing:
        def get_current_approved_submission(self, member):
            return None

        def get_reclaimed_avatar(self, member):
            return None

        def approve_avatar_submission(self, submission, **kwargs):
            raise ForumProviderError("The forum said no.")

    monkeypatch.setattr(forum_module, "get_forum_service", lambda: Refusing())
    member = make_member(email="refused@example.com")
    picture = _picture(member)
    signed_in(client, _staff("boss@example.org", "admin"))

    response = send(client, "POST", f"{API}/pictures/{picture.id}/approve", {})

    assert response.status_code == 502
    assert response.get_json()["error"]["message"] == "The forum said no."
    db.session.refresh(picture)
    assert picture.status == "pending"


def test_a_note_too_long_or_a_field_unknown_is_refused(client):
    member = make_member(email="note@example.com")
    change = _name_change(member)
    signed_in(client, _staff("boss@example.org", "admin"))

    assert send(client, "POST", f"{API}/name-changes/{change.id}/reject", {"note": "x" * 2001}).status_code == 400
    assert send(client, "POST", f"{API}/name-changes/{change.id}/reject", {"admin_note": "x"}).status_code == 400


def test_history_is_paged(client, quiet_forum):  # noqa: F811
    boss = _staff("boss@example.org", "admin")
    signed_in(client, boss)
    for index in range(3):
        change = _name_change(make_member(email=f"h{index}@example.com"), last_name=f"New{index}")
        send(client, "POST", f"{API}/name-changes/{change.id}/reject", {})

    history = client.get(f"{API}/history").get_json()

    assert history["total"] == 3 and history["page"] == 1 and history["pages"] == 1
    assert all(item["decision"] == "rejected" and item["by"] == "boss@example.org" for item in history["items"])
    assert client.get(f"{API}/history?page=0").status_code == 400
