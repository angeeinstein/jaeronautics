"""The health report must notice real problems, not just render.

Its whole purpose is that someone who does not use a terminal can tell whether
the site is working. A report that always says "healthy" would be worse than no
report at all, so these tests check that each condition actually surfaces.
"""
from datetime import date, timedelta

from conftest import app_module, clock, db, make_member, outbox, periods
from aeronautics_members.db_models import (
    EmailDeliveryJob,
    ExternalWorkItem,
    MembershipPeriod,
    ProcessedStripeEvent,
    User,
)
from aeronautics_members.services import diagnostics

CUR = date.today().year


def _login_admin(client, app):
    """A super admin: the health report lives on the Maintenance tab."""
    user = User(email="healthadmin@example.com")
    user.set_password("x")
    db.session.add(user)
    user.grant_role(app_module.get_role("admin"))
    user.grant_role(app_module.get_role("superadmin"))
    db.session.commit()
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
    return user


def test_clean_installation_reports_healthy(app):
    health = diagnostics.collect_system_health()
    assert health["healthy"] is True
    assert health["problems"] == []


def test_failed_forum_task_is_reported(app):
    member = make_member(email="ff@example.com")
    item = outbox.enqueue_forum_sync(member, reason="test")
    db.session.commit()
    item.status = ExternalWorkItem.STATUS_FAILED
    db.session.commit()

    health = diagnostics.collect_system_health()

    assert health["healthy"] is False
    assert any("forum" in p.lower() for p in health["problems"])


def test_failed_stripe_event_is_reported(app):
    db.session.add(ProcessedStripeEvent(
        event_id="evt_bad", event_type="invoice.paid",
        status=ProcessedStripeEvent.STATUS_FAILED, attempts=5,
    ))
    db.session.commit()

    health = diagnostics.collect_system_health()

    assert health["healthy"] is False
    assert any("stripe" in p.lower() for p in health["problems"])


def test_undeliverable_email_is_reported(app):
    db.session.add(EmailDeliveryJob(email_type="welcome_email", status="exhausted"))
    db.session.commit()
    assert any("email" in p.lower() for p in diagnostics.collect_system_health()["problems"])


def _queued_email(next_attempt_at):
    db.session.add(
        EmailDeliveryJob(
            email_type="welcome_email", status="pending", next_attempt_at=next_attempt_at
        )
    )
    db.session.commit()


def test_an_email_still_backing_off_is_not_a_problem(app):
    """A retry scheduled for tomorrow is the queue working, not failing."""
    _queued_email(clock.get_now_utc() + timedelta(hours=20))

    health = diagnostics.collect_system_health()

    assert health["queues"]["emails_overdue"] == 0
    assert health["healthy"] is True


def test_an_email_long_past_its_retry_time_is_reported(app):
    """Nothing is delivering it. The count alone could not have said so.

    Three emails queued for a day look identical whether the timer is running
    or stopped, which is exactly how a dead notification timer went unnoticed.
    """
    _queued_email(clock.get_now_utc() - timedelta(hours=6))

    health = diagnostics.collect_system_health()

    assert health["queues"]["emails_overdue"] == 1
    assert health["healthy"] is False
    assert any("timer" in p.lower() for p in health["problems"])


def test_an_email_only_just_due_is_given_grace(app):
    """The timer runs every 15 minutes; being a few minutes late is normal."""
    _queued_email(clock.get_now_utc() - timedelta(minutes=20))

    assert diagnostics.collect_system_health()["queues"]["emails_overdue"] == 0


def test_an_email_never_scheduled_is_not_counted_as_overdue(app):
    """A NULL next attempt means due immediately, not overdue since epoch."""
    _queued_email(None)

    assert diagnostics.collect_system_health()["queues"]["emails_overdue"] == 0


def test_access_without_evidence_is_reported(app):
    """A covered member with no coverage record means the ledger disagrees."""
    make_member(
        email="noevidence@example.com", payment_status="paid", is_active=True,
        membership_starts_on=date(CUR, 1, 1), membership_ends_on=date(CUR, 12, 31),
    )
    db.session.commit()

    health = diagnostics.collect_system_health()

    assert health["membership"]["covered_without_evidence"] == 1
    assert any("no coverage record" in p for p in health["problems"])


def test_member_with_evidence_is_not_reported(app):
    member = make_member(
        email="withevidence@example.com", payment_status="paid", is_active=True,
        membership_starts_on=date(CUR, 1, 1), membership_ends_on=date(CUR, 12, 31),
    )
    periods.grant_calendar_year(member, CUR, MembershipPeriod.REASON_PAID)
    db.session.commit()

    health = diagnostics.collect_system_health()

    assert health["membership"]["covered_without_evidence"] == 0
    assert health["healthy"] is True


def test_revoked_evidence_does_not_count_as_evidence(app):
    member = make_member(
        email="revoked@example.com", payment_status="paid", is_active=True,
        membership_starts_on=date(CUR, 1, 1), membership_ends_on=date(CUR, 12, 31),
    )
    period = periods.grant_calendar_year(member, CUR, MembershipPeriod.REASON_PAID)
    periods.revoke_period(period, "chargeback")
    db.session.commit()

    assert diagnostics.collect_system_health()["membership"]["covered_without_evidence"] == 1


def test_large_backlog_is_warned_about(app, monkeypatch):
    # A stalled forum worker shows up as a growing queue rather than an error.
    # Built from the real summary rather than a literal, so adding a counter
    # does not silently turn this into a test of a stale hand-written dict.
    baseline = diagnostics.get_queue_summary()
    monkeypatch.setattr(
        diagnostics, "get_queue_summary", lambda: {**baseline, "external_work_pending": 50}
    )
    health = diagnostics.collect_system_health()
    assert health["healthy"] is True          # nothing has failed outright
    assert any("worker may not be running" in w for w in health["warnings"])


def test_schema_revision_is_reported(app):
    schema = diagnostics.get_schema_revision()
    # create_all() builds the schema in tests without stamping Alembic, so the
    # applied revision is absent here; the field must still be present.
    assert set(schema) == {"applied", "expected", "up_to_date"}


def test_cli_prints_the_report(app):
    result = app.test_cli_runner().invoke(args=["system-check"])
    assert "Schema:" in result.output
    assert "Background work:" in result.output


def test_cli_exits_nonzero_when_unhealthy(app):
    db.session.add(EmailDeliveryJob(email_type="welcome_email", status="exhausted"))
    db.session.commit()

    result = app.test_cli_runner().invoke(args=["system-check"])

    assert result.exit_code == 1, "an unhealthy install must fail the command"


def test_health_panel_renders_on_the_settings_page(client, app):
    _login_admin(client, app)

    response = client.get("/admin/settings")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "System Health" in body
    assert "Background work" in body


def test_expired_member_is_not_counted_as_covered(app):
    make_member(
        email="expired@example.com", payment_status="expired", is_active=False,
        membership_ends_on=clock.get_membership_today() - timedelta(days=1),
    )
    db.session.commit()

    membership = diagnostics.collect_system_health()["membership"]

    assert membership["currently_covered"] == 0
    assert membership["covered_without_evidence"] == 0
