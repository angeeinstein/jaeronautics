"""Tests for the cleanup-logs retention command."""
from datetime import datetime, timedelta, timezone

from conftest import db
from aeronautics_members.db_models import AuditLog, NotificationBatch, NotificationEvent


def _seed_old_and_recent(app):
    now = datetime.now(timezone.utc)
    old, recent = now - timedelta(days=400), now - timedelta(days=10)
    db.session.add_all([
        AuditLog(category="x", event_type="old", created_at=old),
        AuditLog(category="x", event_type="recent", created_at=recent),
        NotificationEvent(channel="c", audience="admin", event_type="old", summary="s", queued_at=old),
        NotificationEvent(channel="c", audience="admin", event_type="recent", summary="s", queued_at=recent),
        NotificationBatch(channel="c", recipient_scope="all", created_at=old),
        NotificationBatch(channel="c", recipient_scope="all", created_at=recent),
    ])
    db.session.commit()


def test_cleanup_logs_removes_only_old_records(app):
    _seed_old_and_recent(app)
    result = app.test_cli_runner().invoke(args=["cleanup-logs", "--audit-days", "365", "--notification-days", "365"])
    assert result.exit_code == 0

    audit = [a.event_type for a in db.session.execute(db.select(AuditLog)).scalars()]
    events = [e.event_type for e in db.session.execute(db.select(NotificationEvent)).scalars()]
    batches = db.session.execute(db.select(db.func.count()).select_from(NotificationBatch)).scalar()
    assert audit == ["recent"]
    assert events == ["recent"]
    assert batches == 1


def test_cleanup_logs_zero_keeps_everything(app):
    _seed_old_and_recent(app)
    app.test_cli_runner().invoke(args=["cleanup-logs", "--audit-days", "0", "--notification-days", "0"])
    assert db.session.execute(db.select(db.func.count()).select_from(AuditLog)).scalar() == 2
    assert db.session.execute(db.select(db.func.count()).select_from(NotificationEvent)).scalar() == 2
