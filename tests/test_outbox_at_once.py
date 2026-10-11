"""Queued work starts at once (services/outbox.py): the moment a request's change is
committed, a thread of its own works through the queue -- stopping at the first
failure, so a forum asking us to slow down is left to the worker's retries.
"""
import pytest

from conftest import db, outbox
from aeronautics_members.db_models import ExternalWorkItem


@pytest.fixture
def started(app, monkeypatch):
    """Switched on, with the thread replaced by a note of what would have started."""
    app.config["OUTBOX_START_AT_ONCE"] = True
    calls = []
    monkeypatch.setattr(outbox, "start_now", lambda application: calls.append(application) or True)
    return calls


def test_a_request_that_queues_work_starts_it_after_its_commit(app, started):
    with app.test_request_context("/"):
        outbox.enqueue("test_kind", dedupe_key="one")
        assert started == [], "not before the commit"
        db.session.commit()

    assert started == [app]


def test_nothing_starts_when_the_change_is_rolled_back(app, started):
    with app.test_request_context("/"):
        outbox.enqueue("test_kind", dedupe_key="one")
        db.session.rollback()
        db.session.commit()

    assert started == []


def test_outside_a_request_the_worker_does_it(app, started):
    outbox.enqueue("test_kind", dedupe_key="one")
    db.session.commit()

    assert started == []


def test_switched_off_nothing_starts(app, started):
    app.config["OUTBOX_START_AT_ONCE"] = False
    with app.test_request_context("/"):
        outbox.enqueue("test_kind", dedupe_key="one")
        db.session.commit()

    assert started == []


def test_one_pass_stops_at_the_first_failure(app, monkeypatch):
    calls = []

    def handler(item):
        calls.append(item.payload["n"])
        if item.payload["n"] == 2:
            raise RuntimeError("429 Too Many Requests")

    monkeypatch.setitem(outbox._HANDLERS, "test_kind", handler)
    for n in (1, 2, 3):
        outbox.enqueue("test_kind", payload={"n": n}, dedupe_key=f"n{n}")
    db.session.commit()

    outbox.run_at_once(app)

    assert calls == [1, 2], "the third waits for the worker"
    statuses = [item.status for item in db.session.query(ExternalWorkItem).order_by(ExternalWorkItem.id)]
    assert statuses == ["completed", "pending", "pending"]


def test_not_while_background_jobs_are_paused(app, monkeypatch):
    from aeronautics_members.services import background_jobs

    monkeypatch.setitem(outbox._HANDLERS, "test_kind", lambda item: pytest.fail("ran while paused"))
    outbox.enqueue("test_kind", dedupe_key="one")
    db.session.commit()
    monkeypatch.setattr(background_jobs, "is_paused", lambda: True)

    outbox.run_at_once(app)

    assert db.session.query(ExternalWorkItem).one().status == "pending"


def test_one_thread_at_a_time_going_round_again_for_new_work(app, monkeypatch):
    passes = []
    threads = []

    class InlineThread:
        def __init__(self, target, args, **kwargs):
            self.target, self.args = target, args
            threads.append(self)

        def start(self):
            pass  # run below, after a second start was asked for while it "runs"

    monkeypatch.setattr(outbox.threading, "Thread", InlineThread)
    monkeypatch.setattr(outbox, "run_at_once", lambda application: passes.append(application))

    assert outbox.start_now(app) is True
    assert outbox.start_now(app) is False, "folded into the one running"
    threads[0].target(*threads[0].args)

    assert len(threads) == 1 and passes == [app, app]
    assert outbox.start_now(app) is True, "free again afterwards"
    threads[1].target(*threads[1].args)
