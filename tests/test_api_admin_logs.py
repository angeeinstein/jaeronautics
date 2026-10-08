"""The log (api/admin_logs.py; the page is frontend/src/pages/admin/Logs.tsx)."""
from api_helpers import signed_in
from conftest import db, make_member
from aeronautics_members.db_models import AuditLog
from aeronautics_members.services import audit
from test_admin_reviews import _staff

API = "/api/v1/admin/logs"


def _entry(category="account", event_type="email_changed", **fields):
    entry = AuditLog(category=category, event_type=event_type, **fields)
    db.session.add(entry)
    db.session.commit()
    return entry


def test_signed_out(client):
    assert client.get(API).status_code == 401


def test_only_with_the_log_permission(client):
    signed_in(client, _staff("money@example.org", "treasurer"))

    assert client.get(API).status_code == 403
    assert client.get("/admin/logs").status_code in (302, 403)


def test_newest_first_with_who_and_to_whom(client):
    admin = _staff("boss@example.org", "admin")
    member = make_member(email="anna@example.com")
    _entry(category="settings", event_type="saved")
    _entry(actor_user=admin, target_user=member.user, after_state={"email": "anna@example.com"})
    signed_in(client, admin)

    body = client.get(API).get_json()

    newest, older = body["items"]
    assert (newest["category"], newest["event_type"]) == ("account", "email_changed")
    assert newest["actor"] == {"user_id": admin.id, "email": "boss@example.org"}
    assert newest["target"] == {"user_id": member.user.id, "email": "anna@example.com"}
    assert newest["after"] == {"email": "anna@example.com"} and newest["before"] is None
    assert older["actor"] is None and older["target"] is None, "the system did it"
    assert body["categories"] == ["account", "settings"] and body["total"] == 2
    assert body["about"] is None and body["accounts_linked"] is True


def test_a_membership_without_an_account_is_named_by_its_address(client):
    member = make_member(email="nobody@example.com")
    member.user = None
    db.session.commit()
    _entry(target_member=member)
    signed_in(client, _staff("boss@example.org", "admin"))

    [entry] = client.get(API).get_json()["items"]

    assert entry["target"] == {"user_id": None, "email": "nobody@example.com"}


def test_secrets_stay_out(client):
    _entry(category="settings", event_type="saved", after_state={"stripe_secret_key": "sk_live_x", "name": "x"})
    signed_in(client, _staff("boss@example.org", "admin"))

    [entry] = client.get(API).get_json()["items"]

    assert entry["after"] == {"stripe_secret_key": "<configured>", "name": "x"}


def test_search_and_category(client):
    admin = _staff("boss@example.org", "admin")
    member = make_member(email="anna@example.com")
    _entry(target_user=member.user)
    _entry(category="forum", event_type="synced")
    signed_in(client, admin)

    assert [e["event_type"] for e in client.get(f"{API}?q=ANNA@").get_json()["items"]] == ["email_changed"]
    assert [e["event_type"] for e in client.get(f"{API}?q=synced").get_json()["items"]] == ["synced"]
    assert [e["category"] for e in client.get(f"{API}?category=forum").get_json()["items"]] == ["forum"]


def test_everything_about_one_person(client):
    admin = _staff("boss@example.org", "admin")
    anna, bernd = make_member(email="anna@example.com"), make_member(email="bernd@example.com")
    _entry(event_type="to_her", target_user=anna.user)
    _entry(event_type="by_her", actor_user=anna.user)
    _entry(event_type="to_her_membership", target_member=anna)
    _entry(event_type="someone_else", target_user=bernd.user)
    signed_in(client, admin)

    body = client.get(f"{API}?user={anna.user.id}").get_json()

    assert {e["event_type"] for e in body["items"]} == {"to_her", "by_her", "to_her_membership"}
    assert body["about"] == {"user_id": anna.user.id, "email": "anna@example.com"}
    assert client.get(f"{API}?user=999999").status_code == 404


def test_pages(client, monkeypatch):
    monkeypatch.setattr(audit.log_page, "__kwdefaults__", {**audit.log_page.__kwdefaults__, "per_page": 2})
    signed_in(client, _staff("boss@example.org", "admin"))
    for index in range(5):
        _entry(event_type=f"e{index}")
    total = db.session.query(AuditLog).count()

    first = client.get(API).get_json()
    last = client.get(f"{API}?page={first['pages']}").get_json()

    assert first["total"] == total and first["pages"] == (total + 1) // 2
    assert [e["event_type"] for e in first["items"]] == ["e4", "e3"]
    assert len(last["items"]) == total - 2 * (first["pages"] - 1)
    assert client.get(f"{API}?page=0").status_code == 400


def test_the_page_is_the_apps(client):
    signed_in(client, _staff("boss@example.org", "admin"))

    assert client.get("/admin/logs").status_code == 200
