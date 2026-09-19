"""Starting, resuming and completing a Stripe Checkout attempt.

Three faults live here, and all three are invisible to a test that only uses
short names:

* The whole member profile used to be serialised into one Stripe metadata
  value. Stripe caps a value at 500 characters, and an ordinary Austrian
  profile -- a double-barrelled surname, a title, two university addresses --
  goes past it, so Session.create fails and the member cannot pay at all.
* The webhook then reapplied that snapshot, silently overwriting any profile
  edit the member made while checkout was open.
* Resuming an abandoned signup opened a second session. Two open sessions can
  both be completed, which buys the association two subscriptions.
"""
import json

import pytest

from conftest import billing, clock, db, make_member
from aeronautics_members.db_models import Member

# Stripe's documented limits for a metadata entry.
STRIPE_METADATA_VALUE_LIMIT = 500
STRIPE_METADATA_KEY_LIMIT = 40
STRIPE_METADATA_MAX_KEYS = 50

# Long, but entirely plausible for this association: a hyphenated surname, an
# Austrian degree title, and the university's own long address domain.
LONG_PROFILE = dict(
    salutation="Mr",
    title="Dipl.-Ing. (FH)",
    first_name="Maximilian Alexander",
    last_name="Hofstetter-Wallensteiner",
    street="Alte Poststrasse",
    house_number="149/3/12",
    postal_code="8020",
    city="Graz",
    country="Austria",
    phone_private="+43 664 1234567",
    phone_work="+43 316 5453 8712",
    email_work="m.hofstetter-wallensteiner@joanneum-aeronautics.at",
    year_group="LAV26",
)


class FakeSession(dict):
    """Stand-in for a Stripe Checkout session object."""

    @property
    def url(self):
        return self["url"]


@pytest.fixture(autouse=True)
def request_context(app):
    """Checkout builds localised line-item text, which needs a request.

    In production these calls always happen inside one; the service is exercised
    directly here, so provide the context the locale selector expects.
    """
    with app.test_request_context():
        yield


@pytest.fixture
def stripe_calls(monkeypatch):
    """Capture what would be sent to Stripe, without calling it."""
    calls = {"create": [], "retrieve": []}

    def fake_create(**kwargs):
        calls["create"].append(kwargs)
        return FakeSession(id="cs_created_1", url="https://checkout.stripe.test/cs_created_1", status="open")

    def fake_retrieve(session_id, **kwargs):
        calls["retrieve"].append(session_id)
        stored = calls.get("stored")
        if stored is None:
            raise billing.stripe.InvalidRequestError("No such session", param="id")
        return FakeSession(stored)

    monkeypatch.setattr(billing.stripe.checkout.Session, "create", staticmethod(fake_create))
    monkeypatch.setattr(billing.stripe.checkout.Session, "retrieve", staticmethod(fake_retrieve))
    monkeypatch.setattr(
        billing,
        "get_stripe_membership_price",
        lambda: {"id": "price_test", "currency": "eur", "unit_amount": 3000,
                 "interval": "year", "interval_count": 1},
    )
    monkeypatch.setattr(billing, "apply_runtime_stripe_config", lambda: {"stripe_price_id": "price_test"})
    return calls


class TestMetadataStaysWithinStripesLimits:
    def test_a_long_profile_does_not_blow_the_value_limit(self, app, stripe_calls):
        """The regression: this profile produced a 507-character value.

        Stripe rejects the whole request, the signup route catches StripeError
        and tells the member their account exists but payment could not be
        started -- so the members with the longest names are exactly the ones
        who cannot join.
        """
        member = make_member(email="maximilian.hofstetter-wallensteiner@edu.fh-joanneum.at", **LONG_PROFILE)

        billing.create_checkout_session_for_member(member)

        metadata = stripe_calls["create"][0]["metadata"]
        oversized = {
            key: len(str(value))
            for key, value in metadata.items()
            if len(str(value)) > STRIPE_METADATA_VALUE_LIMIT
        }
        assert not oversized, f"metadata values exceed Stripe's {STRIPE_METADATA_VALUE_LIMIT}-char limit: {oversized}"

    def test_keys_and_count_stay_within_limits_too(self, app, stripe_calls):
        member = make_member(email="limits@example.com", **LONG_PROFILE)

        billing.create_checkout_session_for_member(member)

        for scope in ("metadata", "subscription_data"):
            payload = stripe_calls["create"][0][scope]
            metadata = payload["metadata"] if scope == "subscription_data" else payload
            assert len(metadata) <= STRIPE_METADATA_MAX_KEYS
            assert all(len(key) <= STRIPE_METADATA_KEY_LIMIT for key in metadata)

    def test_the_profile_is_not_sent_to_stripe_at_all(self, app, stripe_calls):
        """Identifiers are enough; the profile is read back from our database.

        Sending it is what created the limit problem, and it also handed a
        payment processor a home address it has no need for.
        """
        member = make_member(email="notsent@example.com", **LONG_PROFILE)

        billing.create_checkout_session_for_member(member)

        serialised = json.dumps(stripe_calls["create"][0]["metadata"])
        assert "Alte Poststrasse" not in serialised
        assert "Hofstetter-Wallensteiner" not in serialised
        assert "member_data" not in stripe_calls["create"][0]["metadata"]

    def test_the_identifiers_needed_to_find_the_member_are_sent(self, app, stripe_calls):
        member = make_member(email="ids@example.com")

        billing.create_checkout_session_for_member(member)

        metadata = stripe_calls["create"][0]["metadata"]
        assert metadata["member_id"] == str(member.id)
        assert metadata["user_id"] == str(member.user_id)


class TestOneAttemptAtATime:
    def test_the_session_id_is_remembered(self, app, stripe_calls):
        member = make_member(email="remember@example.com")

        billing.create_checkout_session_for_member(member)

        assert member.stripe_checkout_session_id == "cs_created_1"

    def test_an_open_session_is_reused_instead_of_opening_a_second(self, app, stripe_calls):
        """Two open sessions can both be completed -- two subscriptions, one member."""
        member = make_member(email="reuse@example.com")
        billing.create_checkout_session_for_member(member)
        stripe_calls["stored"] = {
            "id": "cs_created_1", "status": "open",
            "url": "https://checkout.stripe.test/cs_created_1",
        }

        session, _cycle = billing.create_checkout_session_for_member(member)

        assert session["id"] == "cs_created_1"
        assert len(stripe_calls["create"]) == 1, "a second Checkout session was created"

    def test_an_expired_session_starts_a_fresh_one(self, app, stripe_calls):
        member = make_member(email="expired@example.com")
        billing.create_checkout_session_for_member(member)
        stripe_calls["stored"] = {"id": "cs_created_1", "status": "expired", "url": None}

        billing.create_checkout_session_for_member(member)

        assert len(stripe_calls["create"]) == 2

    def test_a_stripe_lookup_failure_does_not_block_a_new_attempt(self, app, stripe_calls):
        """A member must never be stuck because a stale id cannot be read."""
        member = make_member(email="lookupfail@example.com")
        member.stripe_checkout_session_id = "cs_gone"
        db.session.commit()

        session, _cycle = billing.create_checkout_session_for_member(member)

        assert session["id"] == "cs_created_1"

    def test_creation_is_idempotent_per_member_and_year(self, app, stripe_calls):
        """A double-submitted form must not open two sessions."""
        member = make_member(email="idem@example.com")

        billing.create_checkout_session_for_member(member)

        key = stripe_calls["create"][0]["idempotency_key"]
        assert str(member.id) in key
        assert str(clock.get_membership_today().year) in key


class TestTheWebhookDoesNotOverwriteTheProfile:
    """A profile edited while checkout was open must survive completion."""

    def _complete_checkout(self, client, monkeypatch, member, edited_last_name):
        from tests.test_webhook import checkout_event, post_event

        event = checkout_event(member, member.user, activation_mode="free_period")
        monkeypatch.setattr(
            "aeronautics_members.blueprints.webhook.send_member_welcome_email",
            lambda app, m, *a, **k: None,
        )
        return post_event(client, monkeypatch, event)

    def test_an_edit_made_during_checkout_is_kept(self, client, monkeypatch):
        member = make_member(email="edited@example.com", last_name="Before")
        # The member corrects their name in another tab while Checkout is open.
        member.last_name = "After"
        db.session.commit()

        response = self._complete_checkout(client, monkeypatch, member, "After")

        assert response.status_code == 200
        assert db.session.get(Member, member.id).last_name == "After"

    def test_completion_clears_the_stored_session(self, client, monkeypatch):
        member = make_member(email="cleared@example.com")
        member.stripe_checkout_session_id = "cs_test_1"
        db.session.commit()

        self._complete_checkout(client, monkeypatch, member, None)

        assert db.session.get(Member, member.id).stripe_checkout_session_id is None


def test_an_unmatched_checkout_is_reported_not_invented(client, monkeypatch):
    """Inventing a blank member would leave a paid subscription nobody owns."""
    from tests.test_webhook import post_event

    event = {
        "id": "evt_no_member",
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_orphan", "customer": "cus_orphan",
                            "subscription": "sub_orphan", "metadata": {}}},
    }

    response = post_event(client, monkeypatch, event)

    assert response.status_code == 400
    assert db.session.execute(db.select(Member)).scalars().all() == []
