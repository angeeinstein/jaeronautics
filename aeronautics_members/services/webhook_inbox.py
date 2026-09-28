"""Durable inbox for incoming Stripe webhook events.

Stripe redelivers events, can deliver them out of order, and expects a fast
acknowledgement. Recording an event as seen *before* the handler runs is what
makes two concurrent deliveries of the same event safe -- but on its own it also
means a process killed mid-handler leaves an event that looks handled and never
was, and every later redelivery is waved through as a duplicate.

So a claim carries a lease and a status. Only an event processed through to
completion suppresses reprocessing; a claim abandoned by a dead process is taken
over by the next delivery, and a failure is recorded rather than deleted so it
stays visible.
"""

from datetime import timedelta, timezone

from flask import current_app
from sqlalchemy.exc import IntegrityError

from ..db_models import ProcessedStripeEvent, db
from .clock import get_now_utc

# How long a claimed-but-unfinished event stays reserved before a redelivery may
# take it over. Long enough that a slow handler is not raced, short enough that a
# killed process does not suppress the event until Stripe stops retrying.
STRIPE_EVENT_LEASE = timedelta(minutes=15)




def stripe_event_already_processed(event_id):
    """Return True when a Stripe webhook event was handled through to completion.

    A merely claimed (in-flight or abandoned) event is deliberately not
    "processed": its work may never have finished.
    """
    if not event_id:
        return False
    return (
        db.session.execute(
            db.select(ProcessedStripeEvent.id).filter_by(
                event_id=event_id, status=ProcessedStripeEvent.STATUS_COMPLETED
            )
        ).first()
        is not None
    )


def claim_stripe_event(event_id, event_type=None):
    """Claim a Stripe event for processing, returning False to skip it.

    Recording "seen" before the work runs is what makes concurrent duplicate
    deliveries safe, but on its own it means a process killed mid-handler leaves
    an event that looks handled and never was. So the claim carries a lease: only
    a *completed* event suppresses reprocessing, while a stale in-flight claim is
    taken over by the next delivery. Events without an id cannot be deduplicated
    and are always allowed through.
    """
    if not event_id:
        return True

    now = get_now_utc()
    db.session.add(
        ProcessedStripeEvent(
            event_id=event_id,
            event_type=event_type,
            status=ProcessedStripeEvent.STATUS_PROCESSING,
            attempts=1,
            claimed_at=now,
        )
    )
    try:
        db.session.commit()
        return True
    except IntegrityError:
        db.session.rollback()

    existing = db.session.execute(
        db.select(ProcessedStripeEvent).filter_by(event_id=event_id)
    ).scalar_one_or_none()
    if existing is None:
        # The competing row vanished between the conflict and this read; let the
        # delivery through rather than dropping the event.
        return True

    if existing.status == ProcessedStripeEvent.STATUS_COMPLETED:
        return False

    claimed_at = existing.claimed_at
    if claimed_at is not None and claimed_at.tzinfo is None:
        claimed_at = claimed_at.replace(tzinfo=timezone.utc)
    lease_active = (
        existing.status == ProcessedStripeEvent.STATUS_PROCESSING
        and claimed_at is not None
        and (now - claimed_at) < STRIPE_EVENT_LEASE
    )
    if lease_active:
        # Another delivery is genuinely still working on it; Stripe will retry.
        return False

    # Failed, or abandoned by a process that died holding the claim: take it over.
    existing.status = ProcessedStripeEvent.STATUS_PROCESSING
    existing.attempts = (existing.attempts or 0) + 1
    existing.claimed_at = now
    if existing.event_type is None:
        existing.event_type = event_type
    db.session.commit()
    current_app.logger.info(
        "Retrying Stripe webhook event %s (%s), attempt %s.",
        event_id,
        existing.event_type,
        existing.attempts,
    )
    return True


def complete_stripe_event(event_id):
    """Mark a claimed event as finished so redeliveries are ignored."""
    if not event_id:
        return
    record = db.session.execute(
        db.select(ProcessedStripeEvent).filter_by(event_id=event_id)
    ).scalar_one_or_none()
    if record is None:
        return
    record.status = ProcessedStripeEvent.STATUS_COMPLETED
    record.processed_at = get_now_utc()
    record.last_error = None
    db.session.commit()


def release_stripe_event(event_id, error=None):
    """Release a claimed event so Stripe's retry can reprocess it.

    The row is kept (rather than deleted) so failures stay visible and the
    attempt count survives, but its status no longer suppresses redelivery.
    """
    if not event_id:
        return
    # Processing may have failed mid-transaction; start from a clean session so
    # the release itself can commit.
    db.session.rollback()
    record = db.session.execute(
        db.select(ProcessedStripeEvent).filter_by(event_id=event_id)
    ).scalar_one_or_none()
    if record is None:
        return
    record.status = ProcessedStripeEvent.STATUS_FAILED
    record.claimed_at = None
    if error is not None:
        record.last_error = str(error)[:2000]
    db.session.commit()
