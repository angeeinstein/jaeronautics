"""The outbox: external work recorded with the change that requires it.

Calling another system from inside the handler that changed our own data couples
two failure domains. If Discourse is slow, our webhook is slow -- and Stripe gives
up on a webhook that takes too long. If Discourse fails after our commit, the
two systems disagree and nothing remembers that they should not.

Enqueueing instead makes the remote call a durable intention: the row is written
in the same transaction as the local change, so it cannot be lost by a crash in
between. A worker claims it under a lease, performs the call, and records
success or a retry with backoff.

This module deliberately knows nothing about *how* any particular kind of work
is done. Handlers register themselves with :func:`register_handler`, which keeps
the queue mechanics independent of the forum, the mailer, or whatever comes
next -- and lets a test drive the whole loop with a handler of its own.
"""

from datetime import timedelta

from flask import current_app

from ..db_models import ExternalWorkItem, db
from .clock import get_now_utc

# How long a claimed item stays reserved before another worker may take it over.
# Generous relative to a remote call's own timeout, so a slow handler is not
# raced, but short enough that a killed worker does not strand the item.
WORK_ITEM_LEASE = timedelta(minutes=10)

# Backoff between attempts. An item that keeps failing is retried more slowly;
# after the last delay it is left failed for an administrator to look at.
RETRY_DELAYS = (
    timedelta(minutes=1),
    timedelta(minutes=15),
    timedelta(hours=1),
    timedelta(hours=6),
)

_HANDLERS = {}


def register_handler(kind, handler):
    """Register the callable that performs one kind of work.

    The handler receives the :class:`ExternalWorkItem` and should raise to
    signal failure; returning normally marks the item completed.
    """
    _HANDLERS[kind] = handler
    return handler


def registered_kinds():
    return sorted(_HANDLERS)


def enqueue(kind, *, member=None, user=None, payload=None, dedupe_key=None, reason=None):
    """Record external work to be performed after the current transaction.

    Adds to the session without committing, so the item lands atomically with
    whatever change the caller is making. Returns the existing item when one is
    already outstanding for ``dedupe_key``, since repeated requests for the same
    outcome only need doing once.
    """
    if dedupe_key:
        existing = db.session.execute(
            db.select(ExternalWorkItem).filter_by(dedupe_key=dedupe_key)
        ).scalar_one_or_none()
        if existing is not None:
            # Already queued (or in flight): fold this request into it, and make
            # sure a backoff delay does not outlive the new reason to run.
            existing.not_before = None
            if reason:
                existing.reason = reason[:255]
            return existing

    item = ExternalWorkItem(
        kind=kind,
        status=ExternalWorkItem.STATUS_PENDING,
        member=member,
        user=user,
        payload=payload,
        dedupe_key=dedupe_key,
        reason=(reason or "")[:255] or None,
    )
    db.session.add(item)
    return item


def enqueue_forum_sync(member, reason=None):
    """Queue a Discourse state sync for one member.

    Keyed by member, so several membership changes in quick succession collapse
    into the single sync that actually matters: the last one.
    """
    if member is None or member.user is None:
        return None
    return enqueue(
        ExternalWorkItem.KIND_FORUM_SYNC,
        member=member,
        user=member.user,
        dedupe_key=f"{ExternalWorkItem.KIND_FORUM_SYNC}:member:{member.id}",
        reason=reason,
    )


def enqueue_forum_anonymise(user, reason=None):
    """Queue a Discourse anonymisation for one account.

    Keyed by user rather than member, because by the time this runs the local
    profile is already erased and the forum account is reached through the user.
    """
    if user is None:
        return None
    return enqueue(
        ExternalWorkItem.KIND_FORUM_ANONYMISE,
        member=user.member,
        user=user,
        dedupe_key=f"{ExternalWorkItem.KIND_FORUM_ANONYMISE}:user:{user.id}",
        reason=reason,
    )


def enqueue_forum_discard_replaced(user, remote_user_id, reason=None):
    """Queue removal of the forum account a returning student has just left.

    The remote id goes in the payload rather than being looked up later,
    because the local row that knew it is deleted by the time this runs -- the
    claim keeps the archived account and discards the one signup made.

    Keyed on the remote id so re-running a claim cannot queue the same deletion
    twice.
    """
    if not remote_user_id:
        return None
    return enqueue(
        ExternalWorkItem.KIND_FORUM_DISCARD_REPLACED,
        user=user,
        payload={"remote_user_id": remote_user_id},
        dedupe_key=(
            f"{ExternalWorkItem.KIND_FORUM_DISCARD_REPLACED}:remote:{remote_user_id}"
        ),
        reason=reason,
    )


def claim_next(kinds=None, now=None):
    """Claim one due item, or return None.

    The claim is a conditional UPDATE whose row count decides the winner, so two
    workers cannot take the same item. That works the same on MySQL and on the
    SQLite used by the tests, unlike a SELECT ... FOR UPDATE SKIP LOCKED.
    """
    now = now or get_now_utc()
    lease_cutoff = now - WORK_ITEM_LEASE

    query = db.select(ExternalWorkItem).where(
        db.or_(
            ExternalWorkItem.status == ExternalWorkItem.STATUS_PENDING,
            # Abandoned by a worker that died holding the claim.
            db.and_(
                ExternalWorkItem.status == ExternalWorkItem.STATUS_PROCESSING,
                ExternalWorkItem.claimed_at < lease_cutoff,
            ),
        ),
        db.or_(ExternalWorkItem.not_before.is_(None), ExternalWorkItem.not_before <= now),
    )
    if kinds:
        query = query.where(ExternalWorkItem.kind.in_(list(kinds)))
    query = query.order_by(ExternalWorkItem.id).limit(10)

    for candidate in db.session.execute(query).scalars().all():
        updated = db.session.execute(
            db.update(ExternalWorkItem)
            .where(
                ExternalWorkItem.id == candidate.id,
                ExternalWorkItem.status == candidate.status,
                # Guard against another worker having just claimed it.
                ExternalWorkItem.claimed_at.is_(None)
                if candidate.claimed_at is None
                else ExternalWorkItem.claimed_at == candidate.claimed_at,
            )
            .values(
                status=ExternalWorkItem.STATUS_PROCESSING,
                claimed_at=now,
                attempts=ExternalWorkItem.attempts + 1,
            )
        )
        db.session.commit()
        if updated.rowcount == 1:
            db.session.refresh(candidate)
            return candidate
    return None


def complete(item):
    item.status = ExternalWorkItem.STATUS_COMPLETED
    item.completed_at = get_now_utc()
    item.claimed_at = None
    item.last_error = None
    # Free the key so a later change can queue fresh work for the same member.
    item.dedupe_key = None
    db.session.commit()


def fail(item, error):
    """Record a failed attempt and schedule a retry, or give up."""
    db.session.rollback()
    item = db.session.get(ExternalWorkItem, item.id)
    if item is None:
        return
    item.last_error = str(error)[:2000]
    item.claimed_at = None

    attempt_index = max((item.attempts or 1) - 1, 0)
    if attempt_index < len(RETRY_DELAYS):
        item.status = ExternalWorkItem.STATUS_PENDING
        item.not_before = get_now_utc() + RETRY_DELAYS[attempt_index]
    else:
        # Out of retries. Leave it failed and keyed, so it stays visible and a
        # fresh enqueue for the same member revives it rather than duplicating.
        item.status = ExternalWorkItem.STATUS_FAILED
        item.not_before = None
    db.session.commit()


def process_pending(limit=25, kinds=None, now=None):
    """Run due work items. Returns (completed, failed).

    Safe to run concurrently with itself: each item is claimed before it runs.
    """
    completed = failed = 0
    for _ in range(limit):
        item = claim_next(kinds=kinds, now=now)
        if item is None:
            break
        handler = _HANDLERS.get(item.kind)
        if handler is None:
            fail(item, f"No handler registered for work kind {item.kind!r}")
            failed += 1
            continue
        try:
            handler(item)
        except Exception as exc:
            current_app.logger.warning(
                "External work item %s (%s) failed on attempt %s: %s",
                item.id, item.kind, item.attempts, exc,
            )
            fail(item, exc)
            failed += 1
        else:
            complete(item)
            completed += 1
    return completed, failed


def pending_count(kinds=None):
    query = db.select(db.func.count(ExternalWorkItem.id)).where(
        ExternalWorkItem.status.in_(
            [ExternalWorkItem.STATUS_PENDING, ExternalWorkItem.STATUS_PROCESSING]
        )
    )
    if kinds:
        query = query.where(ExternalWorkItem.kind.in_(list(kinds)))
    return db.session.execute(query).scalar_one()


def failed_items(limit=50):
    """Items that exhausted their retries, for an administrator to review."""
    return db.session.execute(
        db.select(ExternalWorkItem)
        .where(ExternalWorkItem.status == ExternalWorkItem.STATUS_FAILED)
        .order_by(ExternalWorkItem.id.desc())
        .limit(limit)
    ).scalars().all()
