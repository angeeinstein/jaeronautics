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
    photo_approved = True

    def is_enabled(self): return True
    def is_ready(self): return True
    def get_pending_submission(self, member): return None
    def get_current_approved_submission(self, member): return object() if self.photo_approved else None
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


class TestWhileASepaDebitClears:
    """Paid by SEPA, told "set up successfully" -- and then days of "not
    active" with nothing saying the money simply takes a while."""

    def test_the_account_page_says_it_is_on_its_way(self, app, client, forum, monkeypatch):
        member = make_member(email="sepa-first@example.com", payment_status="processing",
                             is_active=False, stripe_customer_id="cus_p")
        with client.session_transaction() as session:
            session["_user_id"] = str(member.user.id)
        # The page asks Stripe for the latest on every load; there is no Stripe here.
        monkeypatch.setattr(app_module, "refresh_member_billing_state", lambda *a, **k: (False, None, None))

        body = client.get("/account", follow_redirects=True).get_data(as_text=True)

        assert "usually takes a few business days" in body
        assert "starts as soon as your payment has cleared" in body
        assert "not active" not in body


class TestAReturningStudentBeforeConfirming:
    """Their old account -- username, posts, usually a picture -- comes back
    when they confirm the university address. Until then the page asked for a
    photo and showed a new username they would never use."""

    def test_they_are_told_to_confirm_not_to_upload(self, app, client, forum, monkeypatch):
        from aeronautics_members.services.forum_import import import_forum_people

        forum.photo_approved = False
        import_forum_people([{"source_user_id": "9", "source_username": "BackB_L21",
                              "source_email": "back@edu.fh-joanneum.at", "year_group": "LAV21"}])
        member = _paid_member(client, verified=True)
        member.email_work = "back@edu.fh-joanneum.at"
        member.user.forum_username = "BackB_L21-2"
        db.session.commit()
        monkeypatch.setattr(app_module, "refresh_member_billing_state", lambda *a, **k: (False, None, None))

        body = client.get("/account", follow_redirects=True).get_data(as_text=True)

        assert "Confirm your university email address to get your old account back" in body
        assert "Upload Profile Picture" not in body
        assert "BackB_L21-2" not in body   # the username they are about to lose
        assert "BackB_L21" not in body     # nor the one that is not proven theirs yet


class TestAFailedRenewal:
    """A bounced renewal leaves the subscription running while Stripe retries.
    The page offered Rejoin, which was then refused."""

    def _failed(self, client, monkeypatch, subscription_status):
        member = make_member(email="bounced@example.com", payment_status="failed", is_active=False,
                             stripe_customer_id="cus_b", stripe_subscription_id="sub_b",
                             membership_ends_on=date(2020, 12, 31))
        with client.session_transaction() as session:
            session["_user_id"] = str(member.user.id)
        monkeypatch.setattr(
            app_module, "refresh_member_billing_state",
            lambda *a, **k: (False, {"id": "sub_b", "status": subscription_status}, None),
        )
        return client.get("/account", follow_redirects=True).get_data(as_text=True)

    def test_while_stripe_retries_they_are_asked_to_update_payment(self, app, client, monkeypatch):
        body = self._failed(client, monkeypatch, "past_due")

        assert "update your payment method under Manage Billing" in body
        assert "/account/rejoin" not in body

    def test_once_the_subscription_has_ended_they_can_rejoin(self, app, client, monkeypatch):
        body = self._failed(client, monkeypatch, "canceled")

        assert "/account/rejoin" in body
        assert "update your payment method" not in body


class TestErrorsSayWhatIsWrong:
    def test_the_signup_form_names_fields_as_the_form_does(self, app, client):
        body = client.post("/process-membership", data={
            "email_private": "someone@example.com", "member_category": "student",
            "email_work": "someone@gmail.com",
        }).get_data(as_text=True)

        assert "University or Company Email:" in body
        assert "Email Work" not in body

    def test_a_rejected_profile_change_says_why(self, app, client, monkeypatch):
        """The page said "please correct the profile form" and showed nothing."""
        member = make_member(email="typo@example.com")
        with client.session_transaction() as session:
            session["_user_id"] = str(member.user.id)
        monkeypatch.setattr(app_module, "refresh_member_billing_state", lambda *a, **k: (False, None, None))

        body = client.post("/account/profile", data={
            "profile-street": "Main", "profile-house_number": "1", "profile-postal_code": "8010",
            "profile-city": "Graz", "profile-country": "Austria",
            "profile-phone_private": "call me maybe", "profile-email_private": "typo@example.com",
        }).get_data(as_text=True)

        assert "Private Phone:" in body
        assert "Invalid phone number format" in body

    def test_a_forum_error_is_not_shown_raw(self, app, client, forum, monkeypatch):
        from aeronautics_members.db_models import ForumAccount

        member = _paid_member(client, verified=True)
        db.session.add(ForumAccount(user=member.user, member=member, provider="discourse",
                                    external_id=str(member.user.id), state="active",
                                    last_error="Discourse API request failed (422): Primary email has already been taken"))
        db.session.commit()
        monkeypatch.setattr(app_module, "refresh_member_billing_state", lambda *a, **k: (False, None, None))

        body = client.get("/account", follow_redirects=True).get_data(as_text=True)

        assert "Discourse API request failed" not in body
        assert "could not be updated just now" in body


class TestThePhotoUpload:
    def _page(self, client, forum, monkeypatch, **forum_state):
        forum.photo_approved = False
        for key, value in forum_state.items():
            setattr(forum, key, value)
        _paid_member(client, verified=True)
        monkeypatch.setattr(app_module, "refresh_member_billing_state", lambda *a, **k: (False, None, None))
        return client.get("/account", follow_redirects=True).get_data(as_text=True)

    def test_says_what_photo_is_wanted(self, app, client, forum, monkeypatch):
        body = self._page(client, forum, monkeypatch)

        assert "real photo of yourself" in body
        assert "optimized down to the avatar limit" not in body

    def test_a_rejection_gives_its_reason_as_a_reason(self, app, client, forum, monkeypatch):
        rejected = types.SimpleNamespace(status="rejected", review_note="That is a giraffe.")
        forum.get_latest_submission = lambda member: rejected

        body = self._page(client, forum, monkeypatch)

        assert "Reason: That is a giraffe." in body


def test_the_forum_card_says_its_status_once(app, client, forum, monkeypatch):
    forum.photo_approved = False
    _paid_member(client, verified=True)
    monkeypatch.setattr(app_module, "refresh_member_billing_state", lambda *a, **k: (False, None, None))

    body = client.get("/account", follow_redirects=True).get_data(as_text=True)

    assert body.count("Upload a profile picture to complete your forum access.") == 1
    assert "Please upload a profile picture before your forum access can be completed." not in body


def test_the_portal_is_english_whatever_the_browser_asks(app, client):
    """The German translation lags the portal; a German browser picked it up
    on its own and showed a half-translated site."""
    body = client.get("/?lang=de", headers={"Accept-Language": "de-AT,de;q=0.9"}).get_data(as_text=True)

    assert '<html lang="en">' in body
    assert "language-selector" not in body
