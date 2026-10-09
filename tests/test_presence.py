"""Who is using the portal right now (services/presence.py, api/presence.py).

A tab in use says so; whoever installs updates sees counts and pages, never
who. Rows go a quarter of an hour after their tab went quiet.
"""
from datetime import timedelta

from conftest import db, make_member
from aeronautics_members.db_models import Presence
from aeronautics_members.services import presence
from aeronautics_members.services.clock import get_now_utc
from api_helpers import signed_in
from test_admin_reviews import _staff

URL = "/api/v1/presence"
ADMIN_URL = "/api/v1/admin/presence"
TAB_A = "tab-aaaa-0001"
TAB_B = "tab-bbbb-0002"


def _say(client, tab=TAB_A, page="/join", typed=False):
    response = client.post(URL, json={"tab": tab, "page": page, "typed": typed})
    assert response.status_code == 204, response.get_json()


class TestService:
    def test_counts_tabs_signed_in_or_not_and_typing(self, app):
        now = get_now_utc()
        presence.seen(TAB_A, "/join", typed=True, signed_in=False, now=now)
        presence.seen(TAB_B, "/account", typed=False, signed_in=True, now=now)
        found = presence.report(at=now)
        assert (found["active"], found["signed_in"], found["visitors"], found["typing"]) == (2, 1, 1, 1)
        assert found["pages"][0] == {"page": "/join", "people": 1, "typing": 1}

    def test_quiet_tabs_stop_counting_then_go(self, app):
        start = get_now_utc()
        presence.seen(TAB_A, "/join", typed=True, signed_in=False, now=start)
        later = start + presence.ACTIVE + timedelta(seconds=1)
        found = presence.report(at=later)
        assert found["active"] == 0 and found["last_seen_at"] is not None
        presence.seen(TAB_B, "/", typed=False, signed_in=False, now=start + presence.KEPT + timedelta(seconds=1))
        assert db.session.get(Presence, TAB_A) is None

    def test_typing_wears_off(self, app):
        start = get_now_utc()
        presence.seen(TAB_A, "/join", typed=True, signed_in=False, now=start)
        presence.seen(TAB_A, "/join", typed=False, signed_in=False, now=start + presence.TYPING + timedelta(seconds=1))
        found = presence.report(at=start + presence.TYPING + timedelta(seconds=2))
        assert (found["active"], found["typing"]) == (1, 0)

    def test_the_asking_tab_is_left_out(self, app):
        presence.seen(TAB_A, "/admin/settings/updates", typed=False, signed_in=True)
        assert presence.report(besides=TAB_A)["active"] == 0

    def test_only_address_patterns_are_kept(self, app):
        assert presence.page_name("/teams/:slug") == "/teams/:slug"
        assert presence.page_name("/") == "/"
        assert presence.page_name("/reset-password/Abc123Token_xyz") == "other"
        assert presence.page_name("/admin/accounts/42") == "other"
        assert presence.page_name("https://example.com/") == "other"
        assert presence.page_name("/x" * 50) == "other"

    def test_odd_tab_ids_are_refused(self, app):
        assert not presence.seen("short", "/", typed=False, signed_in=False)
        assert not presence.seen("a" * 37, "/", typed=False, signed_in=False)
        assert not presence.seen("<script>x</script>", "/", typed=False, signed_in=False)

    def test_the_table_stays_small(self, app, monkeypatch):
        monkeypatch.setattr(presence, "MOST_TABS", 1)
        assert presence.seen(TAB_A, "/", typed=False, signed_in=False)
        assert not presence.seen(TAB_B, "/", typed=False, signed_in=False)
        assert presence.seen(TAB_A, "/join", typed=False, signed_in=False)  # a known tab still counts


class TestApi:
    def test_anybody_may_say_so(self, client):
        _say(client, typed=True)
        row = db.session.get(Presence, TAB_A)
        assert (row.page, row.signed_in, row.typed_at is not None) == ("/join", False, True)

    def test_signed_in_is_taken_from_the_session(self, client, app):
        signed_in(client, make_member(email="m@example.com"))
        _say(client, page="/account")
        assert db.session.get(Presence, TAB_A).signed_in is True

    def test_a_bad_body_is_refused(self, client):
        assert client.post(URL, json={"tab": "x", "page": "/"}).status_code == 400

    def test_the_counts_for_whoever_installs_updates(self, client, app):
        presence.seen(TAB_B, "/join", typed=True, signed_in=False)
        signed_in(client, _staff("root@example.org", "superadmin"))
        _say(client, page="/admin/settings/updates")
        response = client.get(f"{ADMIN_URL}?tab={TAB_A}")
        assert response.status_code == 200
        body = response.get_json()
        assert (body["active"], body["visitors"], body["typing"]) == (1, 1, 1)
        assert body["pages"] == [{"page": "/join", "people": 1, "typing": 1}]
        assert "tab" not in str(body)

    def test_not_for_members(self, client, app):
        signed_in(client, make_member(email="m@example.com"))
        assert client.get(ADMIN_URL).status_code == 403
