"""The webhook inbox must not let unfinished work look finished.

Claiming an event before running the handler is what makes concurrent duplicate
deliveries safe, but on its own it means a process killed mid-handler leaves a
marker that suppresses every later redelivery -- the event is lost for good.
The claim therefore carries a lease, and only completion suppresses retries.
"""
from datetime import timedelta

from conftest import app_module, db
from aeronautics_members.db_models import ProcessedStripeEvent

STATUS_PROCESSING = ProcessedStripeEvent.STATUS_PROCESSING
STATUS_COMPLETED = ProcessedStripeEvent.STATUS_COMPLETED
STATUS_FAILED = ProcessedStripeEvent.STATUS_FAILED


def _row(event_id):
    return db.session.execute(
        db.select(ProcessedStripeEvent).filter_by(event_id=event_id)
    ).scalar_one_or_none()


def test_first_delivery_is_claimed(app):
    assert app_module.claim_stripe_event("evt_1", "invoice.paid") is True
    row = _row("evt_1")
    assert row.status == STATUS_PROCESSING
    assert row.attempts == 1
    assert row.processed_at is None


def test_completed_event_is_not_reprocessed(app):
    app_module.claim_stripe_event("evt_2", "invoice.paid")
    app_module.complete_stripe_event("evt_2")

    assert app_module.claim_stripe_event("evt_2", "invoice.paid") is False
    row = _row("evt_2")
    assert row.status == STATUS_COMPLETED
    assert row.processed_at is not None


def test_concurrent_delivery_during_active_lease_is_skipped(app):
    # A second delivery arriving while the first is genuinely still working must
    # back off; Stripe retries later.
    app_module.claim_stripe_event("evt_3", "invoice.paid")

    assert app_module.claim_stripe_event("evt_3", "invoice.paid") is False
    assert _row("evt_3").attempts == 1


def test_abandoned_claim_is_taken_over_after_lease_expires(app):
    """The crash case: claimed, never completed, process died.

    Under a plain "seen it" marker this event would be acknowledged as already
    processed forever, silently dropping the work it described.
    """
    app_module.claim_stripe_event("evt_4", "invoice.paid")
    row = _row("evt_4")
    row.claimed_at = app_module.get_now_utc() - app_module.STRIPE_EVENT_LEASE - timedelta(minutes=1)
    db.session.commit()

    assert app_module.claim_stripe_event("evt_4", "invoice.paid") is True
    refreshed = _row("evt_4")
    assert refreshed.status == STATUS_PROCESSING
    assert refreshed.attempts == 2


def test_released_event_can_be_retried_immediately(app):
    app_module.claim_stripe_event("evt_5", "invoice.paid")
    app_module.release_stripe_event("evt_5", error=RuntimeError("boom"))

    row = _row("evt_5")
    assert row.status == STATUS_FAILED
    assert "boom" in row.last_error
    # A failed event is not "processed", so a redelivery must be handled.
    assert app_module.stripe_event_already_processed("evt_5") is False
    assert app_module.claim_stripe_event("evt_5", "invoice.paid") is True
    assert _row("evt_5").attempts == 2


def test_claimed_but_unfinished_event_is_not_reported_processed(app):
    app_module.claim_stripe_event("evt_6", "invoice.paid")
    assert app_module.stripe_event_already_processed("evt_6") is False

    app_module.complete_stripe_event("evt_6")
    assert app_module.stripe_event_already_processed("evt_6") is True


def test_events_without_an_id_are_always_allowed(app):
    assert app_module.claim_stripe_event(None) is True
    assert app_module.claim_stripe_event("") is True


def test_long_error_is_truncated(app):
    app_module.claim_stripe_event("evt_7", "invoice.paid")
    app_module.release_stripe_event("evt_7", error="x" * 5000)
    assert len(_row("evt_7").last_error) == 2000
