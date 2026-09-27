"""What a member meets on the way from signup to the forum.

Each of these was found by walking the path as a member would, and each left
somebody stuck with nothing on screen saying why.
"""
import types
from datetime import date

import pytest

from conftest import db, make_member
from aeronautics_members import app as app_module
from aeronautics_members.blueprints import forum as forum_blueprint
from aeronautics_members.forum_service import ForumProviderError
from aeronautics_members.services import forum as forum_services

FORUM_SSO = "https://forum.test/session/sso?return_path=%2F"


class FakeForum:
    """A forum that is set up, and where this member's access is ready."""

    settings = {"forum_avatar_max_bytes": 5_000_000, "forum_onboarding_path": "/",
                "forum_avatar_allowed_types": ["jpg", "png"]}
    handoff_error = None

    def is_enabled(self): return True
    def is_ready(self): return True
    def get_pending_submission(self, member): return None
    def get_current_approved_submission(self, member): return object()
    def get_reclaimed_avatar(self, member): return None
    def get_latest_submission(self, member): return None
    def get_upload_request_limit(self): return 10_000_000

    def sync_member(self, member):
        return types.SimpleNamespace(changed=False, error=None, desired_state="active", forum_account=None)

    def build_forum_redirect(self, destination_path=None):
        return FORUM_SSO

    def handle_provider_request(self, *args, **kwargs):
        if self.handoff_error:
            raise ForumProviderError(self.handoff_error)
        return "https://forum.test/session/sso_login?sso=ok"


@pytest.fixture
def forum(monkeypatch):
    fake = FakeForum()
    for module in (app_module, forum_services, forum_blueprint):
        if hasattr(module, "get_forum_service"):
            monkeypatch.setattr(module, "get_forum_service", lambda: fake)
    return fake


@pytest.fixture
def links_sent(monkeypatch):
    sent = []
    monkeypatch.setattr(
        forum_blueprint, "send_email_verification_email",
        lambda app, user: sent.append(user.email) or True,
    )
    return sent


def _paid_member(client, verified):
    member = make_member(
        email="journey@example.com", payment_status="paid", is_active=True,
        membership_starts_on=date(2026, 1, 1), membership_ends_on=date(2099, 12, 31),
    )
    if verified:
        from aeronautics_members.services.clock import get_now_utc
        member.user.email_verified_at = get_now_utc()
        db.session.commit()
    with client.session_transaction() as session:
        session["_user_id"] = str(member.user.id)
    return member


def _flashes(client):
    with client.session_transaction() as session:
        return [text for _category, text in session.get("_flashes", [])]


class TestAnUnconfirmedAddressDoesNotLoop:
    """Paid, photo approved, first confirmation mail never opened: the forum
    and the portal sent them back and forth until the browser gave up."""

    def test_the_forum_page_does_not_send_them_to_the_forum(self, app, client, forum, links_sent):
        _paid_member(client, verified=False)

        response = client.get("/forum")

        assert response.headers["Location"].endswith("/account")
        assert links_sent == ["journey@example.com"]
        assert any("just sent a new link" in text for text in _flashes(client))

    def test_the_sign_in_from_the_forum_ends_on_the_account_page(self, app, client, forum, links_sent):
        _paid_member(client, verified=False)

        response = client.get("/forum/discourse/connect?sso=x&sig=y")

        assert response.headers["Location"].endswith("/account")
        assert "forum.test" not in response.headers["Location"]

    def test_a_confirmed_member_still_goes_straight_in(self, app, client, forum, links_sent):
        _paid_member(client, verified=True)

        assert client.get("/forum").headers["Location"] == FORUM_SSO
        assert links_sent == []


def test_a_failed_forum_sign_in_is_reported_not_looped(app, client, forum, links_sent):
    forum.handoff_error = "Invalid DiscourseConnect signature."
    _paid_member(client, verified=True)

    response = client.get("/forum/discourse/connect?sso=x&sig=y")

    assert response.headers["Location"].endswith("/account")
    assert any("could not be completed" in text for text in _flashes(client))


class TestJustAfterPaying:
    """Stripe's confirmation arrives seconds after the payment page closes. In
    between, the account page offered to resume a payment just made."""

    def _pending(self, client):
        member = make_member(email="justpaid@example.com", payment_status="pending_checkout",
                             stripe_checkout_session_id="cs_done")
        with client.session_transaction() as session:
            session["_user_id"] = str(member.user.id)
        return member

    def _stripe_says(self, monkeypatch, status):
        from conftest import billing

        monkeypatch.setattr(billing, "apply_runtime_stripe_config", lambda: {})
        monkeypatch.setattr(
            billing.stripe.checkout.Session, "retrieve",
            staticmethod(lambda session_id, **kwargs: {"id": session_id, "status": status}),
        )

    def test_a_finished_checkout_is_not_offered_again(self, app, client, monkeypatch):
        self._pending(client)
        self._stripe_says(monkeypatch, "complete")

        body = client.get("/account", follow_redirects=True).get_data(as_text=True)

        assert "Payment received" in body
        assert "Resume Payment" not in body

    def test_an_abandoned_one_still_is(self, app, client, monkeypatch):
        self._pending(client)
        self._stripe_says(monkeypatch, "open")

        body = client.get("/account", follow_redirects=True).get_data(as_text=True)

        assert "Resume Payment" in body
        assert "Payment received" not in body
