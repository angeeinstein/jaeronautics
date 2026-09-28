"""Data export and erasure.

The interesting property of erasure is not that it removes data -- it is what
survives. Too little and the association loses records it is legally required
to keep; too much and the erasure did not erase anything. Most of these tests
are about that line.
"""
import json
from datetime import date

import pytest

from conftest import app_module, db, make_member, privacy
from aeronautics_members.db_models import (
    AuditLog,
    EmailDeliveryJob,
    NotificationEvent,
    ExternalWorkItem,
    ForumAccount,
    ForumAvatarSubmission,
    MemberProfileChangeRequest,
    MembershipPeriod,
    User,
)
from aeronautics_members.services import ConflictError, ExternalServiceError
from aeronautics_members.services.membership import member_has_active_access


@pytest.fixture(autouse=True)
def no_remote_calls(monkeypatch):
    """Erasure talks to Stripe and Discourse; neither exists in a test run.

    Defaulting both to "nothing to do" keeps each test about the behaviour it
    names, and the tests that care about a remote failure override this.
    """
    monkeypatch.setattr(privacy, "cancel_member_subscription", lambda member, reason=None: False)
    monkeypatch.setattr(privacy, "anonymise_forum_account", lambda user: (False, False))


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)


def _make_admin(email="privacyadmin@example.com"):
    user = User(email=email)
    user.set_password("x")
    user.grant_role(app_module.get_role("admin"))
    db.session.add(user)
    db.session.commit()
    return user


def _paid_member(email="paid@example.com", year=2026):
    member = make_member(email=email, payment_status="paid", is_active=True)
    member.membership_starts_on = date(year, 1, 1)
    member.membership_ends_on = date(year, 12, 31)
    member.stripe_customer_id = f"cus_{member.id}"
    member.stripe_subscription_id = f"sub_{member.id}"
    db.session.add(
        MembershipPeriod(
            member=member,
            starts_on=date(year, 1, 1),
            ends_on=date(year, 12, 31),
            reason=MembershipPeriod.REASON_PAID,
            stripe_invoice_id=f"in_{member.id}",
        )
    )
    db.session.commit()
    return member


class TestExport:
    def test_export_contains_the_profile_the_member_gave_us(self, app):
        member = make_member(email="export@example.com", first_name="Ada", last_name="Lovelace")

        payload = privacy.export_account_data(member.user)

        assert payload["member_profile"]["first_name"] == "Ada"
        assert payload["member_profile"]["last_name"] == "Lovelace"
        assert payload["account"]["email"] == "export@example.com"

    def test_export_is_json_serialisable(self, app):
        """Dates and decimals must survive, or the download is a 500."""
        member = _paid_member(email="serialise@example.com")

        payload = privacy.export_account_data(member.user)

        json.dumps(payload)  # would raise on a stray date/Decimal
        assert payload["membership_periods"][0]["starts_on"] == "2026-01-01"

    def test_export_includes_the_payment_history(self, app):
        member = _paid_member(email="history@example.com")

        payload = privacy.export_account_data(member.user)

        assert len(payload["membership_periods"]) == 1
        assert payload["membership_periods"][0]["reason"] == "paid"
        assert payload["membership_periods"][0]["stripe_invoice_id"] == f"in_{member.id}"

    def test_export_names_stripe_so_the_member_can_ask_them_too(self, app):
        """Stripe is a separate controller holding its own copy of the data."""
        member = _paid_member(email="stripeexport@example.com")

        payload = privacy.export_account_data(member.user)

        assert payload["member_profile"]["stripe_customer_id"] == f"cus_{member.id}"

    def test_export_excludes_actions_this_person_took_on_others(self, app):
        """An admin's export must not become a dump of other members' data."""
        admin = _make_admin(email="actor@example.com")
        other = make_member(email="subject@example.com")
        app_module.log_audit_event(
            category="member",
            event_type="profile_approved",
            actor_user=admin,
            target_user=other.user,
            target_member=other,
            before={"last_name": "Secret"},
        )
        db.session.commit()

        payload = privacy.export_account_data(admin)

        assert payload["account_history"] == []

    def test_an_account_without_a_membership_gets_only_its_own_history(self, app):
        """Regression: a NULL member id must not match every member-less entry.

        An admin account has no membership profile. Comparing its member id
        against the column renders as IS NULL, which matches settings changes,
        system events and anything else with no member attached -- turning this
        export into a dump of unrelated records.
        """
        admin = _make_admin(email="nomember@example.com")
        app_module.log_audit_event(
            category="settings",
            event_type="settings_updated",
            actor_user=_make_admin(email="someoneelse@example.com"),
            before={"stripe_price_id": "price_secret"},
        )
        db.session.commit()

        payload = privacy.export_account_data(admin)

        assert all(entry["event"] != "settings_updated" for entry in payload["account_history"])

    def test_export_endpoint_downloads_a_file(self, client):
        member = make_member(email="download@example.com")
        _login(client, member.user_id)

        response = client.get("/account/data-export")

        assert response.status_code == 200
        assert response.mimetype == "application/json"
        assert "attachment" in response.headers["Content-Disposition"]
        # Personal data must not sit in a shared cache on the way.
        assert "no-store" in response.headers["Cache-Control"]
        assert json.loads(response.get_data(as_text=True))["account"]["id"] == member.user_id

    def test_export_requires_a_login(self, client):
        assert client.get("/account/data-export").status_code in (302, 401)

    def test_a_member_cannot_export_someone_elses_data(self, client):
        member = make_member(email="nosy@example.com")
        victim = make_member(email="victim@example.com")
        _login(client, member.user_id)

        response = client.get(f"/admin/accounts/{victim.user_id}/data-export")

        assert response.status_code in (302, 403)


class TestErasureRemovesThePerson:
    def test_profile_fields_are_overwritten(self, app):
        member = make_member(email="erase@example.com", first_name="Ada", last_name="Lovelace")

        privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        assert member.first_name == privacy.ERASED_TEXT
        assert member.last_name == privacy.ERASED_TEXT
        assert member.street == privacy.ERASED_TEXT
        assert member.phone_private == privacy.ERASED_TEXT
        assert "Lovelace" not in str(member.__dict__)

    def test_the_login_stops_working(self, app):
        member = make_member(email="login@example.com")
        user = member.user

        privacy.erase_account(user, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        assert user.password_hash is None
        assert not user.check_password("initial-password")
        assert user.email != "login@example.com"

    def test_outstanding_signed_links_stop_working(self, app):
        """Nonces are what make reset and verification links valid."""
        member = make_member(email="nonces@example.com")
        user = member.user
        user.password_reset_nonce = "abc"
        user.email_verification_nonce = "def"
        db.session.commit()

        privacy.erase_account(user, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        assert user.password_reset_nonce is None
        assert user.email_verification_nonce is None

    def test_roles_are_removed(self, app):
        admin = _make_admin(email="exadmin@example.com")
        _make_admin(email="remaining@example.com")  # so this is not the last one

        privacy.erase_account(admin, initiated_by=privacy.INITIATED_BY_ADMIN)
        db.session.commit()

        assert admin.roles == []

    def test_two_erased_accounts_do_not_collide(self, app):
        """The replaced addresses land in unique columns."""
        first = make_member(email="one@example.com")
        second = make_member(email="two@example.com")

        privacy.erase_account(first.user, initiated_by=privacy.INITIATED_BY_MEMBER)
        privacy.erase_account(second.user, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        assert first.user.email != second.user.email
        assert first.email_private != second.email_private

    def test_avatar_photographs_are_deleted(self, app, tmp_path):
        member = make_member(email="avatar@example.com")
        avatar = tmp_path / "face.png"
        avatar.write_bytes(b"not really a png")
        db.session.add(
            ForumAvatarSubmission(
                user=member.user, member=member, status="approved", storage_path=str(avatar)
            )
        )
        db.session.commit()

        summary = privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        assert summary["avatar_files_deleted"] == 1
        assert not avatar.exists()
        assert db.session.execute(db.select(ForumAvatarSubmission)).scalars().all() == []

    def test_pending_name_changes_are_deleted(self, app):
        """A change request holds a name and a free-text note."""
        member = make_member(email="change@example.com")
        db.session.add(
            MemberProfileChangeRequest(
                member=member,
                requested_by_user_id=member.user_id,
                requested_salutation="Ms",
                requested_first_name="Grace",
                requested_last_name="Hopper",
                requested_year_group="LAV26",
                member_note="please fix my name",
            )
        )
        db.session.commit()

        privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        assert db.session.execute(db.select(MemberProfileChangeRequest)).scalars().all() == []

    def test_queued_email_payloads_are_cleared(self, app):
        member = make_member(email="queued@example.com")
        db.session.add(
            EmailDeliveryJob(
                email_type="welcome",
                recipient_email="queued@example.com",
                target_user_id=member.user_id,
                target_member_id=member.id,
                payload={"first_name": "Ada"},
            )
        )
        db.session.commit()

        privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        job = db.session.execute(db.select(EmailDeliveryJob)).scalar_one()
        assert job.payload is None
        assert job.recipient_email is None

    def test_a_notification_summary_stops_naming_the_member(self, app):
        """The summary is prose, and several call sites put the address in it.

        Clearing the structured payload alone would leave "a sync failed for
        ada@example.com" sitting in the admin notification list.
        """
        member = make_member(email="named@example.com")
        db.session.add(
            NotificationEvent(
                channel="admin_errors",
                audience="admin",
                event_type="forum_sync_failed",
                summary="A forum synchronization attempt failed for named@example.com.",
                payload={"member_email": "named@example.com"},
                target_user_id=member.user_id,
                target_member_id=member.id,
            )
        )
        db.session.commit()

        privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        event = db.session.execute(db.select(NotificationEvent)).scalar_one()
        assert "named@example.com" not in event.summary
        assert event.payload is None
        # The row itself stays, so the admin history still shows something failed.
        assert event.event_type == "forum_sync_failed"

    def test_rows_linked_only_to_the_user_are_scrubbed_too(self, app):
        """Password resets and verification emails carry no member id.

        Matching on the member alone would walk straight past them, and for an
        account with no membership profile it would find nothing at all.
        """
        admin = _make_admin(email="onlyuser@example.com")
        _make_admin(email="anotheradmin@example.com")
        db.session.add(
            EmailDeliveryJob(
                email_type="password_reset",
                recipient_email="onlyuser@example.com",
                target_user_id=admin.id,
                payload={"email": "onlyuser@example.com"},
            )
        )
        db.session.commit()

        privacy.erase_account(admin, initiated_by=privacy.INITIATED_BY_ADMIN)
        db.session.commit()

        job = db.session.execute(db.select(EmailDeliveryJob)).scalar_one()
        assert job.recipient_email is None
        assert job.payload is None

    def test_the_audit_trail_stops_holding_the_old_profile(self, app):
        """Otherwise the data survives in the log and nothing was erased.

        Profile approvals store a full before/after snapshot, which is a second
        copy of exactly the fields the erasure is meant to remove.
        """
        member = make_member(email="audited@example.com", last_name="Lovelace")
        app_module.log_audit_event(
            category="member",
            event_type="profile_updated",
            target_user=member.user,
            target_member=member,
            before={"last_name": "Lovelace", "city": "Graz"},
            after={"last_name": "Byron", "city": "Graz"},
        )
        db.session.commit()

        privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        entry = db.session.execute(
            db.select(AuditLog).filter_by(event_type="profile_updated")
        ).scalar_one()
        assert entry.before_state is None
        assert entry.after_state is None
        # The skeleton stays: something happened, on this date, to this account.
        assert entry.event_type == "profile_updated"
        assert entry.created_at is not None

    def test_entries_about_other_people_are_left_alone(self, app):
        """This person acting on somebody else is the other person's record."""
        admin = _make_admin(email="admin2@example.com")
        _make_admin(email="spare@example.com")
        other = make_member(email="other@example.com")
        app_module.log_audit_event(
            category="member",
            event_type="profile_approved",
            actor_user=admin,
            target_user=other.user,
            target_member=other,
            before={"last_name": "Hopper"},
        )
        db.session.commit()

        privacy.erase_account(admin, initiated_by=privacy.INITIATED_BY_ADMIN)
        db.session.commit()

        entry = db.session.execute(
            db.select(AuditLog).filter_by(event_type="profile_approved")
        ).scalar_one()
        assert entry.before_state == {"last_name": "Hopper"}

    def test_the_erasure_entry_is_not_a_copy_of_what_was_erased(self, app):
        member = make_member(email="meta@example.com", last_name="Lovelace")

        privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        entry = db.session.execute(
            db.select(AuditLog).filter_by(event_type="account_erased")
        ).scalar_one()
        assert entry.before_state is None
        assert entry.after_state is None
        assert "Lovelace" not in json.dumps(entry.event_metadata)


class TestErasureKeepsTheRecord:
    def test_membership_periods_survive(self, app):
        """They are the accounting record the association must keep."""
        member = _paid_member(email="keep@example.com")

        summary = privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_ADMIN)
        db.session.commit()

        periods = db.session.execute(db.select(MembershipPeriod)).scalars().all()
        assert len(periods) == 1
        assert periods[0].reason == "paid"
        assert periods[0].stripe_invoice_id == f"in_{member.id}"
        assert periods[0].revoked_at is None
        assert summary["periods_retained"] == 1

    def test_the_rows_themselves_survive(self, app):
        member = _paid_member(email="rows@example.com")
        member_id, user_id = member.id, member.user_id

        privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_ADMIN)
        db.session.commit()

        assert db.session.get(User, user_id) is not None
        assert db.session.get(app_module.Member, member_id) is not None

    def test_the_stripe_reference_stays_for_the_treasurer(self, app):
        member = _paid_member(email="treasurer@example.com")
        customer_id = member.stripe_customer_id

        privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_ADMIN)
        db.session.commit()

        assert member.stripe_customer_id == customer_id


class TestErasureEndsAccess:
    def test_an_erased_member_has_no_access_despite_paid_coverage(self, app):
        """The ledger still says covered, because the payment record is kept."""
        member = _paid_member(email="access@example.com")
        on_date = date(2026, 6, 1)
        assert member_has_active_access(member, on_date=on_date) is True

        privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_ADMIN)
        db.session.commit()

        assert member_has_active_access(member, on_date=on_date) is False

    def test_erased_members_are_not_counted_as_members(self, app):
        from aeronautics_members.services import diagnostics

        _paid_member(email="counted@example.com")
        erased = _paid_member(email="uncounted@example.com")
        privacy.erase_account(erased.user, initiated_by=privacy.INITIATED_BY_ADMIN)
        db.session.commit()

        summary = diagnostics.get_membership_summary()

        assert summary["members"] == 1
        assert summary["erased_members"] == 1
        # And the erased member must not look like a ledger inconsistency.
        assert summary["covered_without_evidence"] == 0


class TestBillingIsStoppedFirst:
    def test_an_active_subscription_is_cancelled(self, app, monkeypatch):
        cancelled = []
        monkeypatch.setattr(
            privacy,
            "cancel_member_subscription",
            lambda member, reason=None: cancelled.append((member.id, reason)) or True,
        )
        member = _paid_member(email="cancelme@example.com")

        summary = privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_ADMIN)
        db.session.commit()

        assert summary["subscription_cancelled"] is True
        assert cancelled and cancelled[0][0] == member.id

    def test_nothing_is_erased_when_the_subscription_cannot_be_cancelled(self, app, monkeypatch):
        """Otherwise Stripe keeps billing a customer nobody can identify."""
        def explode(member, reason=None):
            raise RuntimeError("stripe is down")

        monkeypatch.setattr(privacy, "cancel_member_subscription", explode)
        member = _paid_member(email="stillbilled@example.com")

        with pytest.raises(ExternalServiceError):
            privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_ADMIN)
        db.session.rollback()

        assert member.first_name == "Test"
        assert member.deleted_at is None

    def test_a_member_without_a_subscription_is_erased_normally(self, app):
        member = make_member(email="nosub@example.com")

        summary = privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        assert summary["subscription_cancelled"] is False
        assert member.deleted_at is not None


class TestStripeIsLeftAlone:
    """Cancelling stops the billing; the customer record stays, on purpose.

    Stripe is the association's accounting record under the same seven-year
    retention, its finalised invoices keep the name and address regardless, and
    the customer id retained on the erased row is the one path left from an
    anonymous membership period back to who paid. So the erasure must not touch
    it -- and must say so before the member confirms.
    """

    def test_the_customer_reference_survives_the_erasure(self, app):
        member = _paid_member(email="keepstripe@example.com")
        customer_id = member.stripe_customer_id

        privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        assert member.stripe_customer_id == customer_id

    def test_the_impact_reports_that_stripe_holds_a_copy(self, app):
        member = _paid_member(email="impactstripe@example.com")

        impact = privacy.describe_deletion_impact(member.user)

        assert impact["has_stripe_customer"] is True

    def test_an_account_stripe_never_saw_does_not_claim_otherwise(self, app):
        member = make_member(email="nostripeimpact@example.com")

        impact = privacy.describe_deletion_impact(member.user)

        assert impact["has_stripe_customer"] is False

    def test_the_confirmation_page_says_stripe_keeps_its_own_copy(self, client):
        """The member should learn this before deleting, not afterwards."""
        member = _paid_member(email="tellthem@example.com")
        _login(client, member.user_id)
        token = privacy.build_account_deletion_token(member.user)

        response = client.get(f"/account/delete/{token}")

        assert response.status_code == 200
        assert "Stripe" in response.get_data(as_text=True)


class TestTheForumIsNotAllowedToBlockErasure:
    def test_a_forum_outage_defers_rather_than_aborts(self, app, monkeypatch):
        monkeypatch.setattr(privacy, "anonymise_forum_account", lambda user: (False, True))
        member = make_member(email="forumdown@example.com")
        db.session.add(
            ForumAccount(user=member.user, member=member, provider="discourse", external_id="9")
        )
        db.session.commit()

        summary = privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        assert summary["forum_deferred"] is True
        assert member.deleted_at is not None

    def test_the_retry_is_queued_on_the_outbox(self, app, monkeypatch):
        # Real service call this time, with the remote side failing.
        from aeronautics_members.services import forum as forum_service_module

        monkeypatch.setattr(privacy, "anonymise_forum_account", forum_service_module.anonymise_forum_account)
        monkeypatch.setattr(
            forum_service_module,
            "get_forum_service",
            lambda: type("S", (), {"anonymize_user": staticmethod(lambda user: (False, "discourse unreachable"))})(),
        )
        member = make_member(email="retry@example.com")
        db.session.add(
            ForumAccount(user=member.user, member=member, provider="discourse", external_id="7")
        )
        db.session.commit()

        privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        item = db.session.execute(
            db.select(ExternalWorkItem).filter_by(kind=ExternalWorkItem.KIND_FORUM_ANONYMISE)
        ).scalar_one()
        assert item.user_id == member.user_id
        assert item.status == ExternalWorkItem.STATUS_PENDING

    def test_a_queued_forum_sync_does_not_restore_the_erased_profile(self, app, monkeypatch):
        """The sync would push the placeholder profile back to Discourse."""
        from aeronautics_members.services import workflows

        member = make_member(email="resync@example.com")
        item = ExternalWorkItem(
            kind=ExternalWorkItem.KIND_FORUM_SYNC,
            status=ExternalWorkItem.STATUS_PROCESSING,
            member=member,
            user=member.user,
        )
        db.session.add(item)
        privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        called = []
        monkeypatch.setattr(
            workflows, "sync_member_forum_state", lambda m: called.append(m) or (None, None)
        )
        workflows._handle_forum_sync_work(item)

        assert called == []


class TestGuards:
    def test_erasing_twice_is_refused(self, app):
        member = make_member(email="twice@example.com")
        privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        with pytest.raises(ConflictError):
            privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_MEMBER)

    def test_the_last_admin_cannot_be_erased(self, app):
        """Nobody could administer the site afterwards."""
        admin = _make_admin(email="onlyadmin@example.com")

        with pytest.raises(ConflictError) as excinfo:
            privacy.erase_account(admin, initiated_by=privacy.INITIATED_BY_MEMBER)
        assert excinfo.value.code == "last_admin"

    def test_a_second_admin_makes_it_possible(self, app):
        admin = _make_admin(email="first@example.com")
        _make_admin(email="second@example.com")

        privacy.erase_account(admin, initiated_by=privacy.INITIATED_BY_ADMIN)
        db.session.commit()

        assert admin.deleted_at is not None

    def test_an_admin_cannot_erase_themselves_from_the_admin_page(self, app):
        admin = _make_admin(email="self@example.com")
        _make_admin(email="spare2@example.com")

        with pytest.raises(ConflictError) as excinfo:
            privacy.erase_account(admin, actor_user=admin, initiated_by=privacy.INITIATED_BY_ADMIN)
        assert excinfo.value.code == "self_deletion_via_admin_page"

    def test_an_unknown_initiator_is_rejected(self, app):
        member = make_member(email="unknown@example.com")
        from aeronautics_members.services import ValidationError

        with pytest.raises(ValidationError):
            privacy.erase_account(member.user, initiated_by="somebody_else")


class TestAdminRoutes:
    def test_only_an_admin_may_delete_an_account(self, client):
        member = make_member(email="notadmin2@example.com")
        victim = make_member(email="victim2@example.com")
        _login(client, member.user_id)

        response = client.post(
            f"/admin/accounts/{victim.user_id}/delete",
            data={"confirm_email": "victim2@example.com"},
        )

        assert response.status_code in (302, 403)
        assert victim.deleted_at is None

    def test_the_typed_address_must_match(self, client):
        admin = _make_admin()
        victim = make_member(email="typo@example.com")
        _login(client, admin.id)

        client.post(
            f"/admin/accounts/{victim.user_id}/delete",
            data={"confirm_email": "wrong@example.com"},
        )

        assert victim.deleted_at is None
        assert victim.first_name == "Test"

    def test_an_admin_can_delete_a_member(self, client):
        admin = _make_admin()
        victim = make_member(email="goodbye@example.com")
        _login(client, admin.id)

        client.post(
            f"/admin/accounts/{victim.user_id}/delete",
            data={"confirm_email": "goodbye@example.com", "reason": "expelled"},
        )

        assert victim.deleted_at is not None
        entry = db.session.execute(
            db.select(AuditLog).filter_by(event_type="account_erased")
        ).scalar_one()
        assert entry.actor_user_id == admin.id
        assert entry.event_metadata["initiated_by"] == "admin"
        assert entry.event_metadata["note"] == "expelled"

    def test_get_does_not_delete(self, client):
        admin = _make_admin()
        victim = make_member(email="navigated@example.com")
        _login(client, admin.id)

        assert client.get(f"/admin/accounts/{victim.user_id}/delete").status_code == 405
        assert victim.deleted_at is None

    def test_the_detail_page_warns_about_an_active_subscription(self, client):
        admin = _make_admin()
        victim = _paid_member(email="warned@example.com")
        _login(client, admin.id)

        body = client.get(f"/admin/accounts/{victim.user_id}").get_data(as_text=True)

        assert "active subscription" in body
        assert "No refund is issued" in body


class TestMemberInitiatedDeletion:
    def test_the_button_only_sends_an_email(self, client, monkeypatch):
        from aeronautics_members.blueprints import account as account_bp_module

        sent = []
        monkeypatch.setattr(
            account_bp_module, "send_account_deletion_email",
            lambda app, user: sent.append(user.id) or True,
        )
        member = make_member(email="askfirst@example.com")
        _login(client, member.user_id)

        client.post("/account/delete")

        assert sent == [member.user_id]
        assert member.deleted_at is None

    def test_opening_the_link_does_not_delete_anything(self, client):
        """Mail scanners follow links; an unrecoverable GET would be a trap."""
        member = make_member(email="scanner@example.com")
        _login(client, member.user_id)
        token = privacy.build_account_deletion_token(member.user)

        response = client.get(f"/account/delete/{token}")

        assert response.status_code == 200
        assert member.deleted_at is None
        assert "Delete my account permanently" in response.get_data(as_text=True)

    def test_the_confirmation_page_offers_cancelling_instead(self, client):
        """Leaving and deleting are separate steps, done in that order.

        Deleting while a paid year is still running throws away time the member
        has already paid for, so the page that could cost them that says so
        before the button, not after.
        """
        member = _paid_member(email="twostep@example.com")
        _login(client, member.user_id)
        token = privacy.build_account_deletion_token(member.user)

        body = client.get(f"/account/delete/{token}").get_data(as_text=True)

        assert "cancel your membership instead" in body
        assert "no refund" in body

    def test_confirming_erases_the_account(self, client):
        member = make_member(email="confirmed@example.com")
        _login(client, member.user_id)
        token = privacy.build_account_deletion_token(member.user)

        client.post(f"/account/delete/{token}")

        assert member.deleted_at is not None
        assert member.first_name == privacy.ERASED_TEXT

    def test_confirming_signs_the_person_out(self, client):
        member = make_member(email="signedout@example.com")
        _login(client, member.user_id)
        token = privacy.build_account_deletion_token(member.user)

        client.post(f"/account/delete/{token}")

        with client.session_transaction() as session:
            assert "_user_id" not in session

    def test_a_token_for_another_account_is_refused(self, client):
        member = make_member(email="mine@example.com")
        other = make_member(email="theirs@example.com")
        _login(client, member.user_id)
        token = privacy.build_account_deletion_token(other.user)

        client.post(f"/account/delete/{token}")

        assert other.deleted_at is None
        assert member.deleted_at is None

    def test_a_token_stops_working_when_the_address_changes(self, app):
        """A link mailed to an old address must not erase the new account."""
        member = make_member(email="old@example.com")
        token_data = {"user_id": member.user_id, "email": "old@example.com"}
        assert privacy.account_deletion_claims_match(token_data, member.user) is True

        member.user.email = "new@example.com"
        db.session.commit()

        assert privacy.account_deletion_claims_match(token_data, member.user) is False

    def test_an_expired_token_is_refused(self, client, monkeypatch):
        member = make_member(email="expired@example.com")
        _login(client, member.user_id)
        token = privacy.build_account_deletion_token(member.user)
        monkeypatch.setattr(privacy, "TOKEN_MAX_AGE_ACCOUNT_DELETION", -1)
        from aeronautics_members.blueprints import account as account_bp_module

        monkeypatch.setattr(account_bp_module, "TOKEN_MAX_AGE_ACCOUNT_DELETION", -1)

        client.post(f"/account/delete/{token}")

        assert member.deleted_at is None

    def test_a_garbage_token_is_refused(self, client):
        member = make_member(email="garbage@example.com")
        _login(client, member.user_id)

        response = client.post("/account/delete/not-a-real-token")

        assert response.status_code == 302
        assert member.deleted_at is None

    def test_deletion_needs_a_login(self, client):
        assert client.post("/account/delete").status_code in (302, 401)


class TestErasedAccountsStayErased:
    """Sessions issued before the erasure must stop working too.

    Clearing the password hash blocks the *next* login and says nothing about
    the session already sitting in someone's browser -- and an expelled member
    signed in on their laptop is exactly when that gap matters.

    Each of these makes at most one authenticated request, on purpose: the
    ``app`` fixture keeps a single application context open for the whole test,
    and Flask-Login caches the loaded user on ``g``, which is bound to that
    context. A second request in the same test would reuse the first request's
    cached user instead of consulting the loader again. A real deployment gets a
    fresh context per request, so the check runs every time.
    """

    def test_a_session_from_before_the_erasure_no_longer_authenticates(self, client, app):
        member = make_member(email="stillbrowsing@example.com")
        _login(client, member.user_id)  # the cookie the member already holds
        privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_ADMIN)
        db.session.commit()

        response = client.get("/account", follow_redirects=False)

        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_the_same_session_works_when_the_account_is_not_erased(self, client, app):
        """Control for the test above: the cookie itself is good."""
        member = make_member(email="stillamember@example.com")
        _login(client, member.user_id)

        response = client.get("/account", follow_redirects=False)

        assert "/login" not in (response.headers.get("Location") or "")

    def test_an_erased_admin_loses_the_admin_pages(self, client, app):
        admin = _make_admin(email="wasadmin@example.com")
        _make_admin(email="stilladmin@example.com")
        _login(client, admin.id)
        privacy.erase_account(admin, initiated_by=privacy.INITIATED_BY_ADMIN)
        db.session.commit()

        assert client.get("/admin").status_code in (302, 403)

    def test_an_erased_account_cannot_sign_in(self, client, app):
        member = make_member(email="ghost@example.com")
        privacy.erase_account(member.user, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        response = client.post(
            "/login",
            data={"email": "ghost@example.com", "password": "initial-password"},
            follow_redirects=True,
        )

        with client.session_transaction() as session:
            assert "_user_id" not in session
        assert response.status_code == 200
