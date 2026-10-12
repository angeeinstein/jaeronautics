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

import threading
from datetime import timedelta

from flask import current_app, has_request_context

from ..db_models import ExternalWorkItem, db
from .clock import get_now_utc
from .locking import locked

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

#: How many items one start at once works through; the rest wait for the
#: worker's next run. Small, so a burst never hits the forum's rate limits.
KICK_LIMIT = 25


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
        # Locked: a run finishing in the same moment then either sees the
        # request below (and runs again) or has already freed the key (and
        # this finds nothing and queues anew).
        existing = db.session.execute(
            locked(db.select(ExternalWorkItem).filter_by(dedupe_key=dedupe_key))
        ).scalar_one_or_none()
        if existing is not None:
            # Already queued (or in flight): fold this request into it, and make
            # sure a backoff delay does not outlive the new reason to run.
            existing.not_before = None
            if reason:
                existing.reason = reason[:255]
            if existing.status == ExternalWorkItem.STATUS_PROCESSING:
                # Running now, perhaps on what was true before this change:
                # once it finishes, it runs again (see complete()).
                existing.asked_again = True
            if existing.status == ExternalWorkItem.STATUS_FAILED:
                # It gave up earlier -- the forum was down, most likely -- but
                # there is a new reason to do it now. Left failed, the new
                # change would be folded into an item nothing ever runs again,
                # and this member's forum would stop hearing about them.
                revive(existing)
            _start_after_commit()
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
    _start_after_commit()
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


def enqueue_forum_rename(user, reason=None):
    """Queue giving somebody's forum account the portal's name for it.

    Keyed by user: whatever the name is when it runs is the one it asks for,
    so two renames in a row end with the second.
    """
    if user is None:
        return None
    return enqueue(
        ExternalWorkItem.KIND_FORUM_RENAME,
        member=user.member,
        user=user,
        dedupe_key=f"{ExternalWorkItem.KIND_FORUM_RENAME}:user:{user.id}",
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


def enqueue_forum_discard_replaced(user, remote_user_id, reason=None, external_id=None):
    """Queue removal of the forum account a returning student has just left.

    The remote id goes in the payload rather than being looked up later,
    because the local row that knew it is deleted by the time this runs -- the
    claim keeps the archived account and discards the one signup made.

    Keyed on the remote id so re-running a claim cannot queue the same deletion
    twice.
    """
    if not remote_user_id and not external_id:
        return None
    return enqueue(
        ExternalWorkItem.KIND_FORUM_DISCARD_REPLACED,
        user=user,
        payload={"remote_user_id": remote_user_id, "external_id": external_id},
        dedupe_key=(
            f"{ExternalWorkItem.KIND_FORUM_DISCARD_REPLACED}:remote:{remote_user_id}"
            if remote_user_id else
            f"{ExternalWorkItem.KIND_FORUM_DISCARD_REPLACED}:external:{external_id}"
        ),
        reason=reason,
    )


def revive(item):
    """Put an item that ran out of retries back in the queue, with fresh retries."""
    item.status = ExternalWorkItem.STATUS_PENDING
    item.attempts = 0
    item.not_before = None
    item.claimed_at = None
    return item


def retry_failed(kinds=None):
    """Revive every item that ran out of retries. Returns how many.

    For an administrator once the cause -- usually the forum being down for
    hours -- has been dealt with, rather than waiting for each member to change
    again.
    """
    query = db.select(ExternalWorkItem).where(ExternalWorkItem.status == ExternalWorkItem.STATUS_FAILED)
    if kinds:
        query = query.where(ExternalWorkItem.kind.in_(list(kinds)))
    items = db.session.execute(query).scalars().all()
    for item in items:
        revive(item)
    return len(items)


def claim_next(kinds=None, now=None, user_id=None):
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
    if user_id is not None:
        query = query.where(ExternalWorkItem.user_id == user_id)
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
                # This run reads what is true from now on.
                asked_again=False,
            )
        )
        db.session.commit()
        if updated.rowcount == 1:
            db.session.refresh(candidate)
            return candidate
    return None


def complete(item):
    """Mark it done -- unless it was asked for again while it ran, when it goes
    back in the queue to run once more on what is true now.

    One conditional UPDATE decides, so a request committed in the same moment
    is either seen here or finds the item done and queues a new one.
    """
    db.session.flush()  # whatever the handler changed on the item
    done = db.session.execute(
        db.update(ExternalWorkItem)
        .where(ExternalWorkItem.id == item.id, ExternalWorkItem.asked_again.is_(False))
        .values(
            status=ExternalWorkItem.STATUS_COMPLETED,
            completed_at=get_now_utc(),
            claimed_at=None,
            last_error=None,
            # Free the key so a later change can queue fresh work for the same member.
            dedupe_key=None,
        )
    ).rowcount
    if not done:
        db.session.execute(
            db.update(ExternalWorkItem)
            .where(ExternalWorkItem.id == item.id)
            .values(status=ExternalWorkItem.STATUS_PENDING, claimed_at=None, last_error=None,
                    not_before=None, attempts=0, asked_again=False)
        )
    db.session.commit()
    db.session.refresh(item)


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


def process_pending(limit=25, kinds=None, now=None, user_id=None, stop_on_failure=False):
    """Run due work items. Returns (completed, failed).

    Safe to run concurrently with itself: each item is claimed before it runs.
    ``stop_on_failure``: the first failure ends the run -- the remote end is
    down or asks us to slow down, and the worker's retries take it from there.
    """
    completed = failed = 0
    for _ in range(limit):
        item = claim_next(kinds=kinds, now=now, user_id=user_id)
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
            if stop_on_failure:
                break
        else:
            complete(item)
            completed += 1
    return completed, failed


# --- Started at once --------------------------------------------------------------
#
# Work queued by a request -- a payment's webhook, a lead's click -- starts
# the moment that request's change is committed, in a thread of its own, so
# nobody waits for the worker's next run and no page waits for the forum. The
# worker (``flask process-external-work``, every 2 minutes) stays: it retries,
# and picks up whatever a start at once left. One thread per process at a
# time; work queued while it runs makes it go round once more.

_kick_lock = threading.Lock()
_kick_running = False
_kick_again = False


def _start_after_commit():
    """Mark the session: once it commits, start the queue (outside requests: the worker does)."""
    if has_request_context() and current_app.config.get("OUTBOX_START_AT_ONCE", True):
        db.session.info["outbox_start"] = current_app._get_current_object()


def after_commit(session):
    """SQLAlchemy's after-commit hook (registered in app.py)."""
    app = session.info.pop("outbox_start", None)
    if app is not None:
        start_now(app)


def after_rollback(session):
    session.info.pop("outbox_start", None)


def start_now(app):
    global _kick_running, _kick_again
    with _kick_lock:
        if _kick_running:
            _kick_again = True
            return False
        _kick_running = True
    threading.Thread(target=_work_at_once, args=(app,), name="outbox-at-once", daemon=True).start()
    return True


def _work_at_once(app):
    global _kick_running, _kick_again
    try:
        while True:
            run_at_once(app)
            with _kick_lock:
                if not _kick_again:
                    _kick_running = False
                    return
                _kick_again = False
    except BaseException:
        with _kick_lock:
            _kick_running = False
        raise


def run_at_once(app):
    """One pass over the due work, stopping at the first failure; not while jobs are paused."""
    from .background_jobs import is_paused

    with app.app_context():
        try:
            if not is_paused():
                process_pending(limit=KICK_LIMIT, stop_on_failure=True)
        except Exception:  # noqa: BLE001 -- the worker retries on its next run
            db.session.rollback()
            current_app.logger.exception("Queued work started at once failed; the worker takes it from here.")
        finally:
            db.session.remove()


def try_now_for(user_id, kinds):
    """Run somebody's waiting work at once, because they are waiting on it.

    The queue backs off after a failure -- a minute, then a quarter of an
    hour -- which is right for the worker and wrong for a person clicking
    "try again": they were told a minute, and after the second failure
    nothing would have happened for fifteen. So their own click runs their
    own items, whatever the schedule says.

    Without touching it. A failure here neither counts as an attempt nor
    moves the next retry: the queue gives up after a handful of attempts,
    and an impatient person must not be able to use those up by clicking.
    How often this can run at all is the rate limit on the route that
    calls it.
    """
    now = get_now_utc()
    query = db.select(ExternalWorkItem).where(
        ExternalWorkItem.user_id == user_id,
        ExternalWorkItem.kind.in_(list(kinds)),
        ExternalWorkItem.status == ExternalWorkItem.STATUS_PENDING,
    )
    for candidate in db.session.execute(query).scalars().all():
        claimed = db.session.execute(
            db.update(ExternalWorkItem)
            .where(
                ExternalWorkItem.id == candidate.id,
                ExternalWorkItem.status == ExternalWorkItem.STATUS_PENDING,
            )
            .values(status=ExternalWorkItem.STATUS_PROCESSING, claimed_at=now)
        ).rowcount
        db.session.commit()
        if claimed != 1:
            continue  # the worker got there first
        db.session.refresh(candidate)
        handler = _HANDLERS.get(candidate.kind)
        try:
            if handler is None:
                raise RuntimeError(f"No handler registered for work kind {candidate.kind!r}")
            handler(candidate)
        except Exception as exc:
            db.session.rollback()
            item = db.session.get(ExternalWorkItem, candidate.id)
            item.status = ExternalWorkItem.STATUS_PENDING
            item.claimed_at = None
            item.last_error = str(exc)[:2000]
            db.session.commit()
        else:
            complete(candidate)


def pending_count(kinds=None, user_id=None):
    query = db.select(db.func.count(ExternalWorkItem.id)).where(
        ExternalWorkItem.status.in_(
            [ExternalWorkItem.STATUS_PENDING, ExternalWorkItem.STATUS_PROCESSING]
        )
    )
    if kinds:
        query = query.where(ExternalWorkItem.kind.in_(list(kinds)))
    if user_id is not None:
        query = query.where(ExternalWorkItem.user_id == user_id)
    return db.session.execute(query).scalar_one()


def failed_items(limit=50):
    """Items that exhausted their retries, for an administrator to review."""
    return db.session.execute(
        db.select(ExternalWorkItem)
        .where(ExternalWorkItem.status == ExternalWorkItem.STATUS_FAILED)
        .order_by(ExternalWorkItem.id.desc())
        .limit(limit)
    ).scalars().all()


def _listen():
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    if not event.contains(Session, "after_commit", after_commit):
        event.listen(Session, "after_commit", after_commit)
        event.listen(Session, "after_rollback", after_rollback)


_listen()
