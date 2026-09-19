"""Integration tests for the Stripe webhook handler.

These drive the real ``/stripe-webhook`` route against a SQLite database with
Stripe's signature verification and the side-effecting helpers
(``send_member_welcome_email``) stubbed out, so we observe how membership state
transitions and how duplicate events are handled.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import Member, ProcessedStripeEvent, clock, db, make_member, outbox, periods, webhook_inbox
from aeronautics_members.blueprints import webhook as webhook_module
from aeronautics_members.db_models import ExternalWorkItem, Setting

TODAY = clock.get_membership_today()
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
    # Forum sync is no longer called from the webhook at all -- it is queued as
    # external work -- so there is nothing to stub for it here.
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
    # Identifiers only. The profile is deliberately not carried through Stripe;
    # the handler reads it from the database. See TestCheckoutMetadata.
    metadata = {
        "member_email": member.email_private,
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

        assert webhook_inbox.stripe_event_already_processed("evt_checkout_1") is True

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
        assert webhook_inbox.stripe_event_already_processed("evt_boom") is False

    def test_event_abandoned_by_a_crash_is_reprocessed(self, client, monkeypatch, stub_side_effects):
        # Simulate a process killed mid-handler: the event was claimed but never
        # completed. A redelivery after the lease expires must actually run the
        # work instead of being acknowledged as a duplicate.
        member = make_member(email="crash@example.com")
        event = checkout_event(member, member.user, activation_mode="free_period",
                               event_id="evt_crash")

        assert webhook_inbox.claim_stripe_event("evt_crash", "checkout.session.completed") is True
        row = db.session.execute(
            db.select(ProcessedStripeEvent).filter_by(event_id="evt_crash")
        ).scalar_one()
        row.claimed_at = clock.get_now_utc() - webhook_inbox.STRIPE_EVENT_LEASE - timedelta(minutes=1)
        db.session.commit()

        resp = post_event(client, monkeypatch, event)

        assert resp.status_code == 200
        assert resp.data != b"Already processed"
        refreshed = db.session.get(Member, member.id)
        assert refreshed.is_active is True
        assert stub_side_effects == [member.id]

    def test_failed_event_is_not_recorded(self, client, monkeypatch, stub_side_effects):
        # A checkout that cannot be matched to a member returns 400 and must NOT
        # be marked processed, so Stripe's retry can still be handled later.
        event = {
            "id": "evt_missing_meta",
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_x", "metadata": {}}},
        }

        resp = post_event(client, monkeypatch, event)

        assert resp.status_code == 400
        assert webhook_inbox.stripe_event_already_processed("evt_missing_meta") is False


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
        dec31_ts = clock.start_of_day_unix(date(TODAY.year, 12, 31))
        next_year_start_ts = clock.start_of_day_unix(date(TODAY.year + 1, 1, 1))
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


class TestDisputeLost:
    def test_unreachable_stripe_fails_instead_of_acknowledging(self, client, monkeypatch, stub_side_effects):
        # Swallowing the error and returning 200 would drop the event for good,
        # leaving a member active on a charge that was lost.
        def boom(*a, **k):
            raise RuntimeError("stripe unreachable")

        monkeypatch.setattr(webhook_module.stripe.Charge, "retrieve", staticmethod(boom))
        event = {
            "id": "evt_dispute_err", "type": "charge.dispute.closed",
            "data": {"object": {"status": "lost", "charge": "ch_1"}},
        }

        resp = post_event(client, monkeypatch, event)

        assert resp.status_code == 500
        # Released, so Stripe's retry is handled rather than ignored.
        assert webhook_inbox.stripe_event_already_processed("evt_dispute_err") is False

    def test_lost_dispute_deactivates_member(self, client, monkeypatch, stub_side_effects):
        member = make_member(email="disputed@example.com", stripe_customer_id="cus_d",
                             payment_status="paid", is_active=True,
                             membership_ends_on=YEAR_END)
        monkeypatch.setattr(
            webhook_module.stripe.Charge, "retrieve",
            staticmethod(lambda *a, **k: {"customer": "cus_d"}),
        )
        event = {
            "id": "evt_dispute_ok", "type": "charge.dispute.closed",
            "data": {"object": {"status": "lost", "charge": "ch_2"}},
        }

        resp = post_event(client, monkeypatch, event)

        assert resp.status_code == 200
        refreshed = db.session.get(Member, member.id)
        assert refreshed.is_active is False
        assert refreshed.payment_status == "dispute_lost"


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

    def test_immediate_cancellation_does_not_take_back_the_paid_year(self, client, monkeypatch, stub_side_effects):
        """The two-step flow must survive the portal cancelling straight away.

        Stripe's customer portal can be configured to cancel either at the end
        of the billing period or immediately. In the second case this event
        arrives in the middle of a year the member has already paid for, and
        treating "subscription gone" as "access gone" would take back what they
        bought -- the exact outcome cancelling-before-deleting exists to avoid.

        Coverage comes from the ledger, so what matters is that nothing here
        revokes the period. Only a lost chargeback does that, because only then
        was the money actually taken back.
        """
        member = make_member(
            email="immediate_cancel@example.com",
            stripe_customer_id="cus_now_1",
            stripe_subscription_id="sub_now_1",
            payment_status="paid",
            is_active=True,
            membership_starts_on=date(TODAY.year, 1, 1),
            membership_ends_on=YEAR_END,
            renewal_due_on=NEXT_YEAR_START,
        )
        periods.grant_period(
            member,
            starts_on=date(TODAY.year, 1, 1),
            ends_on=YEAR_END,
            reason="paid",
            stripe_invoice_id="in_now_1",
        )
        db.session.commit()

        event = {
            "id": "evt_sub_del_now",
            "type": "customer.subscription.deleted",
            "created": int(datetime.now(timezone.utc).timestamp()),
            "data": {
                "object": {
                    "id": "sub_now_1",
                    "customer": "cus_now_1",
                    "status": "canceled",
                    "cancellation_details": {"reason": "cancellation_requested"},
                    "metadata": {},
                }
            },
        }

        assert post_event(client, monkeypatch, event).status_code == 200

        refreshed = db.session.get(Member, member.id)
        assert [p.revoked_at for p in refreshed.membership_periods] == [None]
        assert refreshed.membership_ends_on == YEAR_END
        assert refreshed.is_active is True

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


class TestCoverageLedger:
    """Every grant of access through the webhook must leave evidence behind."""

    def test_paid_invoice_records_a_period_with_the_invoice_id(self, client, monkeypatch, stub_side_effects):
        member = make_member(email="ledger_inv@example.com", stripe_customer_id="cus_l",
                             stripe_subscription_id="sub_l", payment_status="unpaid")
        now_ts = int(datetime.now(timezone.utc).timestamp())
        event = {
            "id": "evt_ledger_1", "type": "invoice.paid",
            "data": {"object": {"id": "in_ledger_1", "customer": "cus_l", "subscription": "sub_l",
                                "status_transitions": {"paid_at": now_ts}, "created": now_ts}},
        }

        assert post_event(client, monkeypatch, event).status_code == 200

        refreshed = db.session.get(Member, member.id)
        granted = [p for p in refreshed.membership_periods if not p.is_revoked]
        assert len(granted) == 1
        assert granted[0].reason == "paid"
        assert granted[0].stripe_invoice_id == "in_ledger_1"

    def test_redelivered_invoice_does_not_grant_twice(self, client, monkeypatch, stub_side_effects):
        # The inbox already dedupes by event id; this guards the ledger itself,
        # since Stripe can send the same invoice under a different event.
        member = make_member(email="ledger_dup@example.com", stripe_customer_id="cus_d2",
                             stripe_subscription_id="sub_d2", payment_status="unpaid")
        now_ts = int(datetime.now(timezone.utc).timestamp())

        def invoice_event(event_id):
            return {
                "id": event_id, "type": "invoice.paid",
                "data": {"object": {"id": "in_same", "customer": "cus_d2", "subscription": "sub_d2",
                                    "status_transitions": {"paid_at": now_ts}, "created": now_ts}},
            }

        post_event(client, monkeypatch, invoice_event("evt_dup_a"))
        post_event(client, monkeypatch, invoice_event("evt_dup_b"))

        refreshed = db.session.get(Member, member.id)
        assert len(refreshed.membership_periods) == 1

    def test_free_period_checkout_records_why_it_was_free(self, client, monkeypatch, stub_side_effects):
        member = make_member(email="ledger_free@example.com")
        event = checkout_event(member, member.user, activation_mode="free_period",
                               event_id="evt_ledger_free")

        assert post_event(client, monkeypatch, event).status_code == 200

        refreshed = db.session.get(Member, member.id)
        assert [p.reason for p in refreshed.membership_periods] == ["free_period"]

    def test_lost_dispute_revokes_the_coverage_it_bought(self, client, monkeypatch, stub_side_effects):
        member = make_member(email="ledger_disp@example.com", stripe_customer_id="cus_dl",
                             stripe_subscription_id="sub_dl", payment_status="paid",
                             is_active=True, membership_ends_on=YEAR_END)
        periods.grant_calendar_year(member, TODAY.year, "paid",
                                    stripe_invoice_id="in_dl", stripe_subscription_id="sub_dl")
        db.session.commit()
        monkeypatch.setattr(webhook_module.stripe.Charge, "retrieve",
                            staticmethod(lambda *a, **k: {"customer": "cus_dl"}))
        event = {
            "id": "evt_dl", "type": "charge.dispute.closed",
            "data": {"object": {"status": "lost", "charge": "ch_dl"}},
        }

        assert post_event(client, monkeypatch, event).status_code == 200

        refreshed = db.session.get(Member, member.id)
        assert all(p.is_revoked for p in refreshed.membership_periods)
        assert periods.has_coverage(refreshed) is False


class TestExternalWorkIsQueued:
    """The webhook must not make remote calls in its own handler.

    Discourse sync used to run inline here, with a 20-second network timeout
    inside the handler Stripe is waiting on.
    """

    def test_paid_invoice_queues_the_forum_sync(self, client, monkeypatch, stub_side_effects):
        member = make_member(email="queued@example.com", stripe_customer_id="cus_q",
                             stripe_subscription_id="sub_q", payment_status="unpaid")
        now_ts = int(datetime.now(timezone.utc).timestamp())
        event = {
            "id": "evt_queue", "type": "invoice.paid",
            "data": {"object": {"id": "in_q", "customer": "cus_q", "subscription": "sub_q",
                                "status_transitions": {"paid_at": now_ts}, "created": now_ts}},
        }

        assert post_event(client, monkeypatch, event).status_code == 200

        queued = db.session.execute(db.select(ExternalWorkItem)).scalars().all()
        assert [i.kind for i in queued] == [ExternalWorkItem.KIND_FORUM_SYNC]
        assert queued[0].member_id == member.id
        assert queued[0].status == ExternalWorkItem.STATUS_PENDING

    def test_queued_work_survives_a_forum_outage(self, client, monkeypatch, stub_side_effects):
        """The local change commits; the sync is retried rather than lost."""
        member = make_member(email="outage@example.com", stripe_customer_id="cus_o",
                             stripe_subscription_id="sub_o", payment_status="unpaid")
        now_ts = int(datetime.now(timezone.utc).timestamp())
        event = {
            "id": "evt_outage", "type": "invoice.paid",
            "data": {"object": {"id": "in_o", "customer": "cus_o", "subscription": "sub_o",
                                "status_transitions": {"paid_at": now_ts}, "created": now_ts}},
        }
        post_event(client, monkeypatch, event)

        # Discourse is down when the worker runs.
        monkeypatch.setitem(
            outbox._HANDLERS, ExternalWorkItem.KIND_FORUM_SYNC,
            lambda item: (_ for _ in ()).throw(RuntimeError("discourse down")),
        )
        completed, failed = outbox.process_pending()
        assert (completed, failed) == (0, 1)

        # The payment still landed, and the sync is still owed.
        assert db.session.get(Member, member.id).payment_status == "paid"
        item = db.session.execute(db.select(ExternalWorkItem)).scalars().one()
        assert item.status == ExternalWorkItem.STATUS_PENDING

        # When the forum comes back, the same item is picked up and finishes.
        ran = []
        monkeypatch.setitem(outbox._HANDLERS, ExternalWorkItem.KIND_FORUM_SYNC, lambda i: ran.append(i.id))
        item.not_before = None
        db.session.commit()
        assert outbox.process_pending() == (1, 0)
        assert ran == [item.id]
