"""A member changes their private email address: what the forum sees, start to end.

The forum is replaced by a small stand-in that behaves the way Discourse's own
code does (app/models/discourse_connect.rb, read for this): an address change
from the portal replaces the address and, when the portal has not confirmed
it, deactivates the account; an account is activated again by the first sync
that vouches for its address, and welcomed each time that happens unless told
not to; two accounts cannot share an address.
"""
import base64
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qsl, unquote, urlsplit

import pytest

from conftest import db, make_member
from aeronautics_members.db_models import Setting
from aeronautics_members.forum_service import DiscourseConnectProvider, ForumProviderError
from aeronautics_members.services.forum import sync_member_forum_state
from aeronautics_members.services.identity import build_email_verification_claims, generate_token


class FakeDiscourse:
    def __init__(self):
        self.users = {}  # id -> dict(external_id, email, active, welcomed)
        self.next_id = 100
        self.lookups = 0
        self.lookup_fails = False

    def add_local_user(self, email):
        """Somebody with a forum account the portal does not manage."""
        user_id = self.next_id
        self.next_id += 1
        self.users[user_id] = {"external_id": None, "email": email, "active": True, "welcomed": 0}
        return user_id

    def by_external(self, external_id):
        return next((uid for uid, u in self.users.items() if u["external_id"] == external_id), None)

    def sync_sso(self, payload):
        external_id = payload["external_id"]
        email = payload["email"].lower()
        require_activation = payload.get("require_activation") == "true"
        uid = self.by_external(external_id)
        clash = any(u["email"] == email for other, u in self.users.items() if other != uid)
        if uid is None:
            if clash:
                raise ForumProviderError("POST /admin/users/sync_sso failed (403): Primary email has already been taken")
            uid = self.next_id
            self.next_id += 1
            self.users[uid] = {"external_id": external_id, "email": email, "active": False, "welcomed": 0}
        user = self.users[uid]
        if user["email"] != email:
            if clash:
                raise ForumProviderError("POST /admin/users/sync_sso failed (403): Primary email has already been taken")
            user["email"] = email
            if require_activation:
                user["active"] = False
        if not user["active"] and not require_activation:
            user["active"] = True
            if payload.get("suppress_welcome_message") != "true":
                user["welcomed"] += 1
        return {"id": uid}

    def request(self, provider, method, path, data=None, json_body=None, rate_limit_retries=0):
        if method == "POST" and path == "/admin/users/sync_sso":
            raw = base64.b64decode(data["sso"]).decode("utf-8")
            return self.sync_sso(dict(parse_qsl(raw, keep_blank_values=True)))
        if method == "GET" and path.startswith("/u/by-external/"):
            uid = self.by_external(unquote(path[len("/u/by-external/"):-len(".json")]))
            if uid is None:
                raise ForumProviderError(f"GET {path} failed (404)")
            return {"user": {"id": uid}}
        if method == "GET" and path.startswith("/admin/users/list/all.json"):
            self.lookups += 1
            if self.lookup_fails:
                raise ForumProviderError(f"GET {path} failed (502)")
            email = dict(parse_qsl(urlsplit(path).query))["email"].lower()
            return [{"id": uid, "username": f"u{uid}"} for uid, u in self.users.items() if u["email"] == email]
        # Groups, user fields, site settings: nothing this is about.
        raise ForumProviderError(f"{method} {path} failed (404)")


@pytest.fixture
def forum(app, monkeypatch):
    fake = FakeDiscourse()
    monkeypatch.setattr(
        DiscourseConnectProvider, "_request",
        lambda self, method, path, **kwargs: fake.request(self, method, path, **kwargs),
    )
    for key, value in (
        ("forum_integration_enabled", "True"),
        ("forum_base_url", "https://forum.example"),
        ("discourse_api_key", "key"),
        ("discourse_api_username", "system"),
        ("discourse_connect_secret", "x" * 32),
    ):
        db.session.merge(Setting(key=key, value=value))
    db.session.commit()
    return fake


@pytest.fixture
def member_on_forum(app, forum):
    """A paid member, confirmed, who has been on the forum."""
    member = make_member(
        email="anna@example.com", is_active=True, payment_status="paid",
        membership_starts_on=date.today() - timedelta(days=30),
        membership_ends_on=date.today() + timedelta(days=300),
        email_work="anna.student@edu.fh-joanneum.at",
    )
    member.user.email_verified_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()
    sync_member_forum_state(member)
    db.session.commit()
    return member


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)


def _change_email(client, member, new_email):
    """What the page sends (frontend/src/pages/account/Contact.tsx); the answer
    carries the messages for the page, or the field that was refused."""
    return client.put("/api/v1/account/contact", json={
        "street": member.street,
        "house_number": member.house_number,
        "postal_code": member.postal_code,
        "city": member.city,
        "country": member.country,
        "phone_private": member.phone_private,
        "email_private": new_email,
        "phone_work": "",
        "email_work": member.email_work or "",
    })


def _forum_user(forum, member):
    return forum.users[forum.by_external(str(member.user.id))]


class TestTheWholeWay:
    def test_first_activation_welcomes_once(self, member_on_forum, forum):
        remote = _forum_user(forum, member_on_forum)

        assert remote["active"] is True
        assert remote["welcomed"] == 1
        assert member_on_forum.user.forum_account.activated_at is not None

    def test_the_new_address_reaches_the_forum_and_pauses_it(self, client, member_on_forum, forum):
        _login(client, member_on_forum.user.id)

        answer = _change_email(client, member_on_forum, "anna.new@example.com").get_json()

        remote = _forum_user(forum, member_on_forum)
        assert remote["email"] == "anna.new@example.com"
        assert remote["active"] is False
        assert {"tone": "info", "text": "Your forum access is paused until you confirm the new address."} \
            in answer["messages"]

    def test_confirming_reactivates_it_without_a_second_welcome(self, client, member_on_forum, forum):
        _login(client, member_on_forum.user.id)
        _change_email(client, member_on_forum, "anna.new@example.com")
        db.session.refresh(member_on_forum.user)
        token = generate_token("verify-email", **build_email_verification_claims(member_on_forum.user))

        client.get(f"/verify-email/{token}", follow_redirects=True)

        remote = _forum_user(forum, member_on_forum)
        assert remote["active"] is True
        assert remote["email"] == "anna.new@example.com"
        assert remote["welcomed"] == 1

    def test_a_brand_new_forum_account_is_still_welcomed(self, app, forum):
        member = make_member(email="new@example.com", is_active=True, payment_status="paid",
                             membership_ends_on=date.today() + timedelta(days=300))
        member.user.email_verified_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.commit()

        sync_member_forum_state(member)
        db.session.commit()

        assert _forum_user(forum, member)["welcomed"] == 1


class TestAnAddressTheForumAlreadyHas:
    def test_is_refused_before_anything_changes(self, client, member_on_forum, forum):
        forum.add_local_user("taken@example.com")
        _login(client, member_on_forum.user.id)

        response = _change_email(client, member_on_forum, "taken@example.com")

        db.session.refresh(member_on_forum)
        assert response.status_code == 400
        assert "already belongs to another account on the forum" in \
            response.get_json()["error"]["fields"]["email_private"]
        assert member_on_forum.email_private == "anna@example.com"
        assert member_on_forum.user.email == "anna@example.com"
        assert member_on_forum.user.email_is_verified
        assert _forum_user(forum, member_on_forum)["email"] == "anna@example.com"

    def test_their_own_forum_account_does_not_count(self, app, member_on_forum, forum):
        from aeronautics_members.services.forum import get_forum_service

        taken = get_forum_service().address_taken_by_another_forum_account(
            member_on_forum.user, "anna@example.com",
        )

        assert taken is False

    def test_a_forum_that_cannot_be_asked_does_not_block_the_change(self, client, member_on_forum, forum):
        forum.lookup_fails = True
        _login(client, member_on_forum.user.id)

        _change_email(client, member_on_forum, "anna.other@example.com")

        db.session.refresh(member_on_forum)
        assert member_on_forum.email_private == "anna.other@example.com"

    def test_nobody_without_a_forum_account_is_asked_about(self, client, app, forum):
        member = make_member(email="offline@example.com", email_work="offline@edu.fh-joanneum.at")
        _login(client, member.user.id)

        _change_email(client, member, "offline.new@example.com")

        assert forum.lookups == 0
