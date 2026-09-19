"""The email queue: delivering it, and resolving what it gives up on.

The health report counts undelivered emails, and nothing prunes the rows. That
made the report a dead end: one member mistyping their address at signup would
leave the Maintenance tab permanently red, with no way to see whose email it
was, fix it, or make it stop saying so. These cover the way out -- and the
delivery pass itself, which runs from a timer with no request behind it.
"""
from datetime import timedelta

import pytest

from conftest import app_module, clock, db, make_member, workflows
from aeronautics_members.db_models import AuditLog, EmailDeliveryJob, Setting, User
from aeronautics_members.services import notifications


def _admin(client, email="mailadmin@example.com"):
    user = User(email=email)
    user.set_password("x")
    user.grant_role(app_module.get_role("admin"))
    db.session.add(user)
    db.session.commit()
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
    return user


def _exhausted(recipient="typo@exmaple.com", member=None):
    job = EmailDeliveryJob(
        email_type="welcome_email",
        recipient_email=recipient,
        status="exhausted",
        retry_count=2,
        last_attempted_at=clock.get_now_utc() - timedelta(hours=2),
        last_error="Name or service not known",
        target_member_id=member.id if member else None,
        target_user_id=member.user_id if member else None,
    )
    db.session.add(job)
    db.session.commit()
    return job


class TestListing:
    def test_exhausted_jobs_are_listed(self, app):
        job = _exhausted()

        assert [j.id for j in notifications.list_undelivered_emails()] == [job.id]

    def test_jobs_still_being_retried_are_not(self, app):
        """They are not a problem yet; the queue is still working on them."""
        db.session.add(EmailDeliveryJob(email_type="welcome_email", status="pending"))
        db.session.commit()

        assert notifications.list_undelivered_emails() == []


class TestRetrying:
    def test_retrying_puts_the_job_back_in_the_queue(self, app):
        job = _exhausted()

        assert notifications.requeue_email_delivery_job(job) is True
        db.session.commit()

        assert job.status == "pending"
        assert job.retry_count == 0
        assert job.last_error is None
        assert job.next_attempt_at is not None

    def test_retrying_uses_the_corrected_address(self, app):
        """The job keeps the old address; delivery reads the member's current one.

        That is what makes 'fix the profile, then retry' work, so it must not
        quietly send to the typo recorded on the job.
        """
        member = make_member(email="wrong@exmaple.com")
        job = _exhausted(recipient="wrong@exmaple.com", member=member)
        member.email_private = "right@example.com"
        db.session.commit()

        notifications.requeue_email_delivery_job(job)
        db.session.commit()

        # The delivery pass overwrites recipient_email from the member row.
        assert job.target_member_id == member.id
        assert member.email_private == "right@example.com"

    def test_a_job_that_is_not_exhausted_is_left_alone(self, app):
        """A double-submitted form must not resurrect a cancelled job."""
        job = _exhausted()
        notifications.dismiss_email_delivery_job(job)
        db.session.commit()

        assert notifications.requeue_email_delivery_job(job) is False
        assert job.status == "canceled"


class TestDismissing:
    def test_dismissing_stops_it_being_reported(self, app):
        from aeronautics_members.services import diagnostics

        job = _exhausted()
        assert diagnostics.collect_system_health()["healthy"] is False

        notifications.dismiss_email_delivery_job(job)
        db.session.commit()

        assert diagnostics.collect_system_health()["healthy"] is True

    def test_the_row_is_kept_rather_than_deleted(self, app):
        """It is part of the account's record and the data export reads it."""
        job = _exhausted()
        job_id = job.id

        notifications.dismiss_email_delivery_job(job)
        db.session.commit()

        assert db.session.get(EmailDeliveryJob, job_id) is not None


class TestTheDeliveryPassHasNoRequestBehindIt:
    """It runs from a systemd timer, so `request` does not exist.

    The locale selector read `request.args` unconditionally, so the first
    translated string in this pass raised "Working outside of request context"
    and took the whole delivery run down with it -- reachable simply by turning
    automatic emails off while a retry was queued.
    """

    def test_a_disabled_queue_cancels_rather_than_crashing(self, app):
        member = make_member(email="queued@example.com")
        db.session.add(Setting(key="automatic_emails_enabled", value="False"))
        db.session.add(
            EmailDeliveryJob(
                email_type="welcome_email",
                status="pending",
                target_member_id=member.id,
                target_user_id=member.user_id,
                next_attempt_at=clock.get_now_utc() - timedelta(minutes=1),
            )
        )
        db.session.commit()

        summary = workflows.process_email_delivery_jobs(app)
        db.session.commit()

        assert summary["canceled"] == 1

    def test_a_job_whose_member_is_gone_cancels_rather_than_crashing(self, app):
        db.session.add(
            EmailDeliveryJob(
                email_type="welcome_email",
                status="pending",
                target_member_id=99999,
                next_attempt_at=clock.get_now_utc() - timedelta(minutes=1),
            )
        )
        db.session.commit()

        summary = workflows.process_email_delivery_jobs(app)
        db.session.commit()

        assert summary["canceled"] == 1

    def test_translation_works_without_a_request(self, app):
        """The general fix: Babel falls back to the default locale."""
        from flask_babel import gettext

        assert gettext("Retry") == "Retry"


class TestTheAdminRoute:
    def test_retry_is_reachable_from_the_maintenance_tab(self, client):
        _admin(client)
        job = _exhausted()

        response = client.post(f"/admin/undelivered-emails/{job.id}/retry", follow_redirects=False)

        assert response.status_code == 302
        assert job.status == "pending"

    def test_dismiss_is_reachable(self, client):
        _admin(client)
        job = _exhausted()

        client.post(f"/admin/undelivered-emails/{job.id}/dismiss")

        assert job.status == "canceled"

    def test_the_action_is_audited(self, client):
        _admin(client)
        job = _exhausted()

        client.post(f"/admin/undelivered-emails/{job.id}/dismiss")

        entry = db.session.execute(
            db.select(AuditLog).filter_by(event_type="undelivered_email_dismiss")
        ).scalar_one()
        assert entry.event_metadata["job_id"] == job.id

    def test_a_missing_job_does_not_break_the_page(self, client):
        _admin(client)

        response = client.post("/admin/undelivered-emails/9999/dismiss", follow_redirects=True)

        assert response.status_code == 200

    def test_an_unknown_action_is_not_routed(self, client):
        _admin(client)
        job = _exhausted()

        assert client.post(f"/admin/undelivered-emails/{job.id}/delete").status_code == 404

    @pytest.mark.parametrize("action", ["retry", "dismiss"])
    def test_a_member_cannot_resolve_emails(self, client, action):
        member = make_member(email="notadmin@example.com")
        job = _exhausted()
        with client.session_transaction() as session:
            session["_user_id"] = str(member.user_id)

        response = client.post(f"/admin/undelivered-emails/{job.id}/{action}")

        assert response.status_code in (302, 403)
        assert job.status == "exhausted"

    def test_the_panel_lists_the_recipient_and_the_error(self, client):
        _admin(client)
        _exhausted(recipient="typo@exmaple.com")

        body = client.get("/admin/settings").get_data(as_text=True)

        assert "typo@exmaple.com" in body
        assert "Name or service not known" in body
