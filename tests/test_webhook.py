"""Integration tests for the Stripe webhook handler.

These drive the real ``/stripe-webhook`` route against a SQLite database with
Stripe's signature verification and the side-effecting helpers
(``send_member_welcome_email``, ``sync_member_forum_state``) stubbed out, so we
observe how membership state transitions and how duplicate events are handled.
"""

import json
from datetime import date, datetime, timezone

import pytest

from conftest import Member, ProcessedStripeEvent, app_module, db, make_member
from aeronautics_members.blueprints import webhook as webhook_module
from aeronautics_members.db_models import Setting

TODAY = app_module.get_membership_today()
YEAR_END = date(TODAY.year, 12, 31)
NEXT_YEAR_START = date(TODAY.year + 1, 1, 1)


@pytest.fixture
def stub_side_effects(monkeypatch):
    """Neutralize email + forum side effects and record welcome-email sends."""
    sends = []
    # Patch where the names are looked up: inside the webhook blueprint module.
    monkeypatch.setattr(
        webhook_module,
        "send_member_welcome_email",
        lambda app, member, *a, **k: sends.append(member.id),
    )
    monkeypatch.setattr(
        webhook_module,
        "sync_member_forum_state",
        lambda member, *a, **k: (None, None),
    )
    return sends


def _ensure_webhook_secret():
    """The handler fails closed without a configured signing secret; set one."""
    if db.session.get(Setting, "stripe_webhook_secret") is None:
        db.session.add(Setting(key="stripe_webhook_secret", value="whsec_test"))
        db.session.commit()


def post_event(client, monkeypatch, event):
    """Post a webhook whose signature verification yields ``event``."""
    _ensure_webhook_secret()
    monkeypatch.setattr(
        webhook_module.stripe.Webhook,
        "construct_event",
        staticmethod(lambda payload, sig_header, secret: event),
    )
    return client.post(
        "/stripe-webhook",
        data=b"{}",
        headers={"stripe-signature": "t=1,v1=test"},
    )


def test_webhook_rejected_when_secret_not_configured(client, monkeypatch):
    """Audit H3: with no signing secret configured, reject (fail closed)."""
    # Do NOT configure a secret. construct_event should never be reached.
    def _boom(*a, **k):
        raise AssertionError("construct_event must not be called without a secret")

    monkeypatch.setattr(webhook_module.stripe.Webhook, "construct_event", staticmethod(_boom))
    resp = client.post("/stripe-webhook", data=b"{}", headers={"stripe-signature": "t=1,v1=x"})
    assert resp.status_code == 500


def checkout_event(member, user, activation_mode="free_period", payment_status="no_payment_required", event_id="evt_checkout_1"):
    profile = {
        "salutation": member.salutation,
        "first_name": member.first_name,
        "last_name": member.last_name,
        "street": member.street,
        "house_number": member.house_number,
        "postal_code": member.postal_code,
        "city": member.city,
        "country": member.country,
        "phone_private": member.phone_private,
        "email_private": member.email_private,
        "year_group": member.year_group,
        "terms_accepted": True,
    }
    metadata = {
        "member_data": json.dumps(profile),
        "member_id": str(member.id),
        "user_id": str(user.id),
        "membership_starts_on": TODAY.isoformat(),
        "membership_ends_on": YEAR_END.isoformat(),
        "renewal_due_on": NEXT_YEAR_START.isoformat(),
        "activation_mode": activation_mode,
    }
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_1",
                "customer": "cus_test_1",
                "subscription": "sub_test_1",
                "payment_status": payment_status,
                "metadata": metadata,
            }
        },
    }


class TestCheckoutCompleted:
    def test_free_period_activates_member_and_sends_welcome(self, client, monkeypatch, stub_side_effects):
        member = make_member()
        event = checkout_event(member, member.user, activation_mode="free_period")

        resp = post_event(client, monkeypatch, event)

        assert resp.status_code == 200
        refreshed = db.session.get(Member, member.id)
        assert refreshed.payment_status == "free_period"
        assert refreshed.is_active is True
        assert refreshed.membership_ends_on == YEAR_END
        assert stub_side_effects == [member.id]

    def test_paid_checkout_marks_member_paid(self, client, monkeypatch, stub_side_effects):
        member = make_member()
        event = checkout_event(
            member, member.user, activation_mode="paid_now", payment_status="paid"
        )

        resp = post_event(client, monkeypatch, event)

        assert resp.status_code == 200
        refreshed = db.session.get(Member, member.id)
        assert refreshed.payment_status == "paid"
        assert refreshed.is_active is True

    def test_unpaid_processing_checkout_is_not_active(self, client, monkeypatch, stub_side_effects):
        member = make_member()
        event = checkout_event(
            member, member.user, activation_mode="paid_now", payment_status="unpaid"
        )

        resp = post_event(client, monkeypatch, event)

        assert resp.status_code == 200
        refreshed = db.session.get(Member, member.id)
        assert refreshed.payment_status == "processing"
        assert refreshed.is_active is False
        assert stub_side_effects == []  # no welcome email until active


class TestIdempotency:
    def test_duplicate_event_is_ignored(self, client, monkeypatch, stub_side_effects):
        member = make_member()
        event = checkout_event(member, member.user, activation_mode="free_period")

        first = post_event(client, monkeypatch, event)
        second = post_event(client, monkeypatch, event)

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.data == b"Already processed"

        # Welcome email fired exactly once, despite two deliveries.
        assert stub_side_effects == [member.id]

        # Exactly one processed-event row recorded.
        rows = db.session.execute(
            db.select(ProcessedStripeEvent).filter_by(event_id="evt_checkout_1")
        ).scalars().all()
        assert len(rows) == 1

    def test_records_event_id_after_success(self, client, monkeypatch, stub_side_effects):
        member = make_member()
        event = checkout_event(member, member.user)

        post_event(client, monkeypatch, event)

        assert app_module.stripe_event_already_processed("evt_checkout_1") is True

    def test_claim_released_when_processing_raises(self, client, monkeypatch, stub_side_effects):
        # If the handler raises after claiming the event, the marker must be
        # released so Stripe's retry can reprocess it (no lost event).
        # The member must exist so the handler reaches update_member_paid_coverage.
        make_member(email="raise@example.com", stripe_customer_id="cus_r",
                    stripe_subscription_id="sub_r", payment_status="unpaid")

        def boom(*a, **k):
            raise RuntimeError("processing failed")

        monkeypatch.setattr(webhook_module, "update_member_paid_coverage", boom)
        client.application.config["PROPAGATE_EXCEPTIONS"] = False

        now_ts = int(datetime.now(timezone.utc).timestamp())
        event = {
            "id": "evt_boom", "type": "invoice.paid",
            "data": {"object": {"id": "in_b", "customer": "cus_r", "subscription": "sub_r",
                                "status_transitions": {"paid_at": now_ts}, "created": now_ts}},
        }
        resp = post_event(client, monkeypatch, event)
        assert resp.status_code == 500
        assert app_module.stripe_event_already_processed("evt_boom") is False

    def test_failed_event_is_not_recorded(self, client, monkeypatch, stub_side_effects):
        # A checkout event missing member_data returns 400 and must NOT be
        # marked processed, so Stripe's retry can still be handled later.
        event = {
            "id": "evt_missing_meta",
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_x", "metadata": {}}},
        }

        resp = post_event(client, monkeypatch, event)

        assert resp.status_code == 400
        assert app_module.stripe_event_already_processed("evt_missing_meta") is False


class TestInvoicePaid:
    def test_invoice_paid_updates_coverage_and_activates(self, client, monkeypatch, stub_side_effects):
        member = make_member(
            stripe_customer_id="cus_inv_1",
            stripe_subscription_id="sub_inv_1",
            payment_status="unpaid",
        )
        now_ts = int(datetime.now(timezone.utc).timestamp())
        event = {
            "id": "evt_invoice_1",
            "type": "invoice.paid",
            "data": {
                "object": {
                    "id": "in_1",
                    "customer": "cus_inv_1",
                    "subscription": "sub_inv_1",
                    "status_transitions": {"paid_at": now_ts},
                    "created": now_ts,
                }
            },
        }

        resp = post_event(client, monkeypatch, event)

        assert resp.status_code == 200
        refreshed = db.session.get(Member, member.id)
        assert refreshed.payment_status == "paid"
        assert refreshed.is_active is True
        assert refreshed.membership_ends_on == YEAR_END
        assert stub_side_effects == [member.id]


class TestRenewalCoverage:
    def test_renewal_advances_by_billing_period_not_timestamp(self, client, monkeypatch, stub_side_effects):
        # M2/M3: a renewal invoice paid at a timestamp that maps to Dec 31 must
        # still advance coverage to next year, driven by the billing period.
        make_member(email="renew@example.com", stripe_customer_id="cus_ren",
                    stripe_subscription_id="sub_ren", payment_status="free_period",
                    is_active=True, membership_ends_on=YEAR_END)
        dec31_ts = app_module.start_of_day_unix(date(TODAY.year, 12, 31))
        next_year_start_ts = app_module.start_of_day_unix(date(TODAY.year + 1, 1, 1))
        event = {
            "id": "evt_renew", "type": "invoice.paid",
            "data": {"object": {
                "id": "in_ren", "customer": "cus_ren", "subscription": "sub_ren",
                "status_transitions": {"paid_at": dec31_ts}, "created": dec31_ts,
                "lines": {"data": [{"period": {"start": next_year_start_ts}}]},
            }},
        }
        resp = post_event(client, monkeypatch, event)
        assert resp.status_code == 200
        refreshed = db.session.get(Member, member_id_for("renew@example.com"))
        assert refreshed.membership_ends_on == date(TODAY.year + 1, 12, 31)
        assert refreshed.payment_status == "paid"


def member_id_for(email):
    return db.session.execute(db.select(Member.id).filter_by(email_private=email)).scalar_one()


class TestSubscriptionDeleted:
    def test_voluntary_cancellation_keeps_coverage_until_year_end(self, client, monkeypatch, stub_side_effects):
        member = make_member(
            stripe_customer_id="cus_can_1",
            stripe_subscription_id="sub_can_1",
            payment_status="paid",
            is_active=True,
            membership_starts_on=date(TODAY.year, 1, 1),
            membership_ends_on=YEAR_END,
            renewal_due_on=NEXT_YEAR_START,
        )
        now_ts = int(datetime.now(timezone.utc).timestamp())
        event = {
            "id": "evt_sub_del_1",
            "type": "customer.subscription.deleted",
            "created": now_ts,
            "data": {
                "object": {
                    "id": "sub_can_1",
                    "customer": "cus_can_1",
                    "status": "canceled",
                    "cancellation_details": {"reason": "cancellation_requested"},
                    "metadata": {},
                }
            },
        }

        resp = post_event(client, monkeypatch, event)

        assert resp.status_code == 200
        refreshed = db.session.get(Member, member.id)
        assert refreshed.payment_status == "canceled"
        # Coverage paid through year end, so access remains until then.
        assert refreshed.is_active is True
        assert refreshed.membership_ends_on == YEAR_END

    def test_cancellation_for_failed_payment_deactivates(self, client, monkeypatch, stub_side_effects):
        member = make_member(
            stripe_customer_id="cus_fail_1",
            stripe_subscription_id="sub_fail_1",
            payment_status="paid",
            is_active=True,
            membership_ends_on=YEAR_END,
        )
        now_ts = int(datetime.now(timezone.utc).timestamp())
        event = {
            "id": "evt_sub_del_2",
            "type": "customer.subscription.deleted",
            "created": now_ts,
            "data": {
                "object": {
                    "id": "sub_fail_1",
                    "customer": "cus_fail_1",
                    "status": "canceled",
                    "cancellation_details": {"reason": "payment_failed"},
                    "metadata": {},
                }
            },
        }

        resp = post_event(client, monkeypatch, event)

        assert resp.status_code == 200
        refreshed = db.session.get(Member, member.id)
        assert refreshed.payment_status == "failed"
        assert refreshed.is_active is False
