"""Reconnecting a returning student to their old forum account by hand.

The ordinary way is confirming the university address the old forum had.
That fails when the address changed with a married name, no longer works,
or the old forum never had one: the student ends up with an empty new forum
account beside their old one. An admin who recognises them picks the old
account instead, and the reconnection itself is the same.
"""
from api_helpers import send
from conftest import db, make_member
from aeronautics_members.db_models import AuditLog, ExternalWorkItem, ImportedForumProfile, User
from test_admin_reviews import _login, _staff
from test_forum_account_claim import _archived


def _married_returner():
    """Came back for the master's, under a new name and a new address."""
    member = make_member(email="anna.berger@example.com", first_name="Anna", last_name="Berger",
                         year_group="MAV24")
    member.email_work = "anna.berger@edu.fh-joanneum.at"
    db.session.commit()
    return member


def test_the_search_finds_the_old_account_by_its_username(app, client):
    _archived(email="a.huber@edu.fh-joanneum.at", username="HuberA_L21", uid="501")
    member = _married_returner()
    _login(client, _staff("boss@example.org", "admin").id)

    body = client.get(f"/api/v1/admin/accounts/{member.user_id}/old-forum-candidates?q=hubera").get_json()

    assert [item["username"] for item in body["items"]] == ["HuberA_L21"]
    assert body["likely"] is False
    assert client.get(f"/api/v1/admin/accounts/{member.user_id}").get_json()["actions"]["reconnect"] is True


def test_reconnecting_by_hand_moves_the_member_onto_the_old_account(app, client):
    profile = _archived(email="a.huber@edu.fh-joanneum.at", username="HuberA_L21", uid="502")
    archived_id = profile.user_id
    member = _married_returner()
    new_user_id = member.user_id
    _login(client, _staff("boss2@example.org", "admin").id)

    response = send(client, "POST", f"/api/v1/admin/accounts/{new_user_id}/reconnect", {"profile_id": profile.id})

    assert response.status_code == 200, response.get_json()
    assert response.get_json()["account_id"] == archived_id, "the page goes on to the account's new home"
    assert response.get_json()["old_username"] == "HuberA_L21"
    db.session.expire_all()
    archived = db.session.get(User, archived_id)
    assert archived.email == "anna.berger@example.com", "they sign in as before"
    assert archived.member.last_name == "Berger", "their details are theirs, not the old forum's"
    assert archived.forum_username == "HuberA_L21", "the name their posts are under"
    assert db.session.get(User, new_user_id) is None
    assert db.session.get(ImportedForumProfile, profile.id).claimed_at is not None
    assert db.session.query(ExternalWorkItem).filter_by(
        kind=ExternalWorkItem.KIND_FORUM_SYNC, member_id=archived.member.id).count() == 1
    assert db.session.query(AuditLog).filter_by(event_type="forum_account_reconnected_by_admin").count() == 1


def test_an_old_account_taken_meanwhile_is_not_taken_twice(app, client):
    profile = _archived(email="a.huber@edu.fh-joanneum.at", username="HuberA_L21", uid="503")
    first = _married_returner()
    second = make_member(email="someone.else@example.com")
    _login(client, _staff("boss3@example.org", "admin").id)
    send(client, "POST", f"/api/v1/admin/accounts/{first.user_id}/reconnect", {"profile_id": profile.id})

    response = send(client, "POST", f"/api/v1/admin/accounts/{second.user_id}/reconnect", {"profile_id": profile.id})

    assert response.status_code == 409
    assert "taken already" in response.get_json()["error"]["message"]
    assert db.session.get(User, second.user_id) is not None


def test_a_reconnected_account_is_not_offered_in_the_search(app, client):
    profile = _archived(email="a.huber@edu.fh-joanneum.at", username="HuberA_L21", uid="504")
    first = _married_returner()
    other = make_member(email="other@example.com")
    _login(client, _staff("boss4@example.org", "admin").id)
    send(client, "POST", f"/api/v1/admin/accounts/{first.user_id}/reconnect", {"profile_id": profile.id})

    body = client.get(f"/api/v1/admin/accounts/{other.user_id}/old-forum-candidates?q=hubera").get_json()

    assert body["items"] == []
