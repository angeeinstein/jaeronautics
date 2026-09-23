"""External work must survive the things that used to lose it.

Calls to Discourse used to run inline, inside the request or webhook that caused
them. A failure after the local commit left the two systems disagreeing with
nothing recording that they did, and a slow forum made Stripe's webhook time
out. These tests cover the queue that replaced that: the item is recorded with
the change, claimed under a lease, retried with backoff, and never run twice at
once.
"""
from datetime import timedelta

import pytest

from conftest import clock, db, make_member, outbox
from aeronautics_members.db_models import ExternalWorkItem


@pytest.fixture
def handler_calls(monkeypatch):
    """Register a recording handler for a test-only work kind."""
    calls = []

    def handler(item):
        calls.append(item.id)

    monkeypatch.setitem(outbox._HANDLERS, "test_kind", handler)
    return calls


def _item(item_id):
    return db.session.get(ExternalWorkItem, item_id)


class TestEnqueueing:
    def test_enqueue_does_not_commit_on_its_own(self, app):
        """The item must land in the caller's transaction, not a separate one.

        That is the whole point: either the local change and the intention to
        sync both commit, or neither does.
        """
        member = make_member(email="tx@example.com")
        outbox.enqueue_forum_sync(member, reason="test")
        db.session.rollback()

        assert db.session.execute(db.select(ExternalWorkItem)).scalars().all() == []

    def test_repeated_requests_collapse_into_one_item(self, app):
        # Five membership changes in a minute need one forum sync, not five.
        member = make_member(email="dedupe@example.com")
        for _ in range(5):
            outbox.enqueue_forum_sync(member, reason="repeated")
        db.session.commit()

        assert len(db.session.execute(db.select(ExternalWorkItem)).scalars().all()) == 1

    def test_enqueueing_clears_a_pending_backoff(self, app):
        # A fresh reason to sync should not wait out an earlier failure's delay.
        member = make_member(email="backoff@example.com")
        item = outbox.enqueue_forum_sync(member, reason="first")
        db.session.commit()
        item.not_before = clock.get_now_utc() + timedelta(hours=6)
        db.session.commit()

        outbox.enqueue_forum_sync(member, reason="something changed again")
        db.session.commit()

        assert _item(item.id).not_before is None

    def test_member_without_a_user_is_not_queued(self, app):
        assert outbox.enqueue_forum_sync(None) is None


class TestClaiming:
    def test_claim_marks_the_item_and_counts_the_attempt(self, app, handler_calls):
        member = make_member(email="claim@example.com")
        item = outbox.enqueue("test_kind", member=member)
        db.session.commit()

        claimed = outbox.claim_next()
        assert claimed.id == item.id
        assert claimed.status == ExternalWorkItem.STATUS_PROCESSING
        assert claimed.attempts == 1

    def test_a_claimed_item_is_not_claimed_again(self, app, handler_calls):
        member = make_member(email="claim2@example.com")
        outbox.enqueue("test_kind", member=member)
        db.session.commit()

        assert outbox.claim_next() is not None
        assert outbox.claim_next() is None

    def test_item_waiting_on_backoff_is_not_claimed(self, app, handler_calls):
        member = make_member(email="wait@example.com")
        item = outbox.enqueue("test_kind", member=member)
        db.session.commit()
        item.not_before = clock.get_now_utc() + timedelta(hours=1)
        db.session.commit()

        assert outbox.claim_next() is None

    def test_abandoned_claim_is_taken_over_after_the_lease(self, app, handler_calls):
        """A worker killed mid-call must not strand the work forever."""
        member = make_member(email="abandoned@example.com")
        outbox.enqueue("test_kind", member=member)
        db.session.commit()
        claimed = outbox.claim_next()
        claimed.claimed_at = clock.get_now_utc() - outbox.WORK_ITEM_LEASE - timedelta(minutes=1)
        db.session.commit()

        retaken = outbox.claim_next()
        assert retaken is not None and retaken.id == claimed.id
        assert retaken.attempts == 2


class TestProcessing:
    def test_successful_item_completes_and_frees_its_key(self, app, handler_calls):
        member = make_member(email="ok@example.com")
        item = outbox.enqueue("test_kind", member=member, dedupe_key="k1")
        db.session.commit()

        completed, failed = outbox.process_pending()

        assert (completed, failed) == (1, 0)
        assert handler_calls == [item.id]
        row = _item(item.id)
        assert row.status == ExternalWorkItem.STATUS_COMPLETED
        assert row.completed_at is not None
        # Key released, so a later change can queue fresh work for this member.
        assert row.dedupe_key is None

    def test_failure_is_retried_with_backoff(self, app, monkeypatch):
        def boom(item):
            raise RuntimeError("discourse unreachable")

        monkeypatch.setitem(outbox._HANDLERS, "test_kind", boom)
        member = make_member(email="fail@example.com")
        item = outbox.enqueue("test_kind", member=member)
        db.session.commit()

        completed, failed = outbox.process_pending()

        assert (completed, failed) == (0, 1)
        row = _item(item.id)
        assert row.status == ExternalWorkItem.STATUS_PENDING
        assert "discourse unreachable" in row.last_error
        assert row.not_before is not None  # will be retried later, not immediately

    def test_item_gives_up_after_the_last_retry(self, app, monkeypatch):
        monkeypatch.setitem(outbox._HANDLERS, "test_kind",
                            lambda item: (_ for _ in ()).throw(RuntimeError("still down")))
        member = make_member(email="giveup@example.com")
        item = outbox.enqueue("test_kind", member=member)
        db.session.commit()

        for _ in range(len(outbox.RETRY_DELAYS) + 1):
            row = _item(item.id)
            row.not_before = None
            db.session.commit()
            outbox.process_pending()

        row = _item(item.id)
        assert row.status == ExternalWorkItem.STATUS_FAILED
        assert row in outbox.failed_items()

    def test_unknown_kind_fails_rather_than_disappearing(self, app):
        member = make_member(email="unknown@example.com")
        item = outbox.enqueue("no_such_kind", member=member)
        db.session.commit()

        completed, failed = outbox.process_pending()

        assert (completed, failed) == (0, 1)
        assert "No handler registered" in _item(item.id).last_error

    def test_pending_count_tracks_outstanding_work(self, app, handler_calls):
        member = make_member(email="count@example.com")
        outbox.enqueue("test_kind", member=member)
        db.session.commit()
        assert outbox.pending_count() == 1

        outbox.process_pending()
        assert outbox.pending_count() == 0


def test_forum_sync_is_a_registered_kind(app):
    """The handler must actually be wired up, not just written."""
    assert ExternalWorkItem.KIND_FORUM_SYNC in outbox.registered_kinds()


def test_worker_cli_reports_what_it_did(app, handler_calls):
    member = make_member(email="cli@example.com")
    outbox.enqueue("test_kind", member=member)
    db.session.commit()

    result = app.test_cli_runner().invoke(args=["process-external-work"])

    assert result.exit_code == 0, result.output
    assert "1 completed" in result.output


class TestRemovingTheAccountAReconnectionLeftBehind:
    """The worker side of the orphan a returning student leaves on the forum."""

    def _item(self, remote_user_id=8801):
        from aeronautics_members.db_models import ExternalWorkItem, db as _db

        item = ExternalWorkItem(
            kind=ExternalWorkItem.KIND_FORUM_DISCARD_REPLACED,
            payload={"remote_user_id": remote_user_id},
            status=ExternalWorkItem.STATUS_PENDING,
        )
        _db.session.add(item)
        _db.session.flush()
        return item

    def _handler(self):
        from aeronautics_members.services.workflows import (
            _handle_forum_discard_replaced_work,
        )
        return _handle_forum_discard_replaced_work

    def test_it_asks_the_forum_to_delete_that_account(self, app, monkeypatch):
        deleted = []

        class FakeProvider:
            def delete_remote_user(self, remote_user_id):
                deleted.append(remote_user_id)
                return True

        class FakeService:
            provider = FakeProvider()

            def is_ready(self):
                return True

        monkeypatch.setattr(
            "aeronautics_members.services.workflows.get_forum_service",
            lambda: FakeService(),
        )

        self._handler()(self._item(8801))

        assert deleted == [8801]

    def test_an_item_without_a_remote_id_is_simply_done(self, app):
        """Nothing to delete, nothing to retry for ever."""
        self._handler()(self._item(remote_user_id=None))

    def test_it_retries_while_the_forum_is_unreachable(self, app, monkeypatch):
        """Raising is what puts it back in the queue with backoff.

        Marking it done would lose the orphan silently, and the orphan is
        holding somebody's email address.
        """
        from aeronautics_members.services import ExternalServiceError
        from aeronautics_members.forum_service import ForumProviderError

        class FakeProvider:
            def delete_remote_user(self, remote_user_id):
                raise ForumProviderError("Could not reach Discourse")

        class FakeService:
            provider = FakeProvider()

            def is_ready(self):
                return True

        monkeypatch.setattr(
            "aeronautics_members.services.workflows.get_forum_service",
            lambda: FakeService(),
        )

        with pytest.raises(ExternalServiceError):
            self._handler()(self._item())

    def test_it_waits_rather_than_forgetting_when_the_forum_is_off(self, app, monkeypatch):
        class FakeService:
            provider = None

            def is_ready(self):
                return False

        monkeypatch.setattr(
            "aeronautics_members.services.workflows.get_forum_service",
            lambda: FakeService(),
        )

        from aeronautics_members.services import ExternalServiceError
        with pytest.raises(ExternalServiceError):
            self._handler()(self._item())
