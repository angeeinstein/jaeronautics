"""One account for an administrator (api/admin_account.py, services/account_admin.py;
the page is frontend/src/pages/admin/Account.tsx).

What each action does in detail is tested where it always was -- roles in
test_roles.py, switching off in test_account_disabled.py, erasure in
test_privacy.py, the private address in test_wrong_private_address.py,
reconnecting in test_admin_reconnect.py. Here: who may look, what the answer
says, what the page is told it may offer, and the failures the actions turn
into answers.
"""
from datetime import date

import pytest
import stripe

from api_helpers import send, signed_in
from conftest import db, make_member
from aeronautics_members.db_models import AuditLog, NotificationEvent
from test_admin_reviews import _staff

THIS_YEAR_END = date(date.today().year, 12, 31)


def url(user_id, rest=""):
    return f"/api/v1/admin/accounts/{user_id}{rest}"


@pytest.fixture
def member(app):
    return make_member(email="anna@example.com", first_name="Anna", last_name="Berger", title="Ing.",
                       payment_status="paid", is_active=True, membership_ends_on=THIS_YEAR_END,
                       renewal_due_on=date(date.today().year + 1, 1, 1))


def _get(client, user_id):
    response = client.get(url(user_id))
    assert response.status_code == 200, response.get_json()
    return response.get_json()


class TestWhoMayLook:
    def test_signed_out(self, client, member):
        assert client.get(url(member.user_id)).status_code == 401

    def test_admin_access_alone_is_not_enough(self, client, member):
        signed_in(client, _staff("money@example.org", "treasurer"))

        assert client.get(url(member.user_id)).status_code == 403

    def test_an_account_that_does_not_exist(self, client):
        signed_in(client, _staff("boss@example.org", "admin"))

        response = client.get(url(999999))

        assert response.status_code == 404
        assert response.get_json()["error"]["code"] == "not_found"

    def test_the_page_itself_is_the_app(self, client, member):
        signed_in(client, _staff("boss@example.org", "admin"))

        assert '<div id="root">' in client.get(f"/admin/accounts/{member.user_id}").get_data(as_text=True)


class TestWhatItSays:
    def test_the_account_and_its_membership(self, client, member):
        signed_in(client, _staff("boss@example.org", "admin"))

        body = _get(client, member.user_id)

        assert body["name"] == "Anna Berger" and body["email"] == "anna@example.com"
        assert body["account_state"] == "active" and body["erased_at"] is None
        assert body["membership"] == {
            "title": "Ing.", "first_name": "Anna", "last_name": "Berger", "email_private": "anna@example.com",
            "category": "student", "category_label": "Student", "year_group": "2020",
            "company_name": None,
            "state": "active", "payment_status": "paid", "payment_status_label": "Paid", "is_active": True,
            "ends_on": THIS_YEAR_END.isoformat(), "renews_on": date(date.today().year + 1, 1, 1).isoformat(),
            "has_billing": False, "picture_replacement_allowed_since": None,
        }
        assert body["old_forum"] is None
        assert body["forum"]["status"] in {"disabled", "needs_avatar"}

    def test_a_cancelled_membership_does_not_renew(self, client, member):
        member.cancel_at_period_end = True
        member.payment_status = "cancel_scheduled"
        db.session.commit()
        signed_in(client, _staff("boss@example.org", "admin"))

        membership = _get(client, member.user_id)["membership"]

        assert membership["renews_on"] is None and membership["state"] == "ending"

    def test_an_account_without_a_membership(self, client):
        boss = _staff("boss@example.org", "admin")
        signed_in(client, boss)

        body = _get(client, boss.id)

        assert body["membership"] is None and body["name"] is None
        assert body["forum"]["status"] == "no_membership"
        assert body["roles"] == [{"slug": "admin", "label": "Admin"}]

    def test_recent_activity_without_secrets(self, client, member):
        boss = _staff("boss@example.org", "admin")
        db.session.add(AuditLog(category="settings", event_type="changed", actor_user=boss, target_user=member.user,
                                before_state={"stripe_secret_key": "sk_live_x", "city": "Graz"},
                                after_state={"stripe_secret_key": "sk_live_y", "city": "Wien"}))
        db.session.commit()
        signed_in(client, boss)

        [entry] = _get(client, member.user_id)["recent_activity"]

        assert entry["actor"] == "boss@example.org" and entry["at"].endswith("Z")
        assert entry["before"] == {"stripe_secret_key": "<configured>", "city": "Graz"}
        assert "sk_live" not in str(entry)


class TestWhatThePageMayOffer:
    """Each button only for whoever the action would not refuse."""

    def test_an_admin(self, client, member):
        signed_in(client, _staff("boss@example.org", "admin"))

        body = _get(client, member.user_id)

        assert body["actions"] == {
            "sync_billing": False,  # nothing at Stripe to ask about
            "resync_forum": True, "picture_replacement": False, "correct_email": True, "reconnect": True,
            "manage_access": False, "export_data": True, "erase": True,
        }
        assert body["access"] is None, "roles are for whoever manages access"
        assert body["teams"] is not None

    def test_billing_sync_once_there_is_something_at_stripe(self, client, member):
        member.stripe_customer_id = "cus_1"
        db.session.commit()
        signed_in(client, _staff("boss@example.org", "admin"))

        assert _get(client, member.user_id)["actions"]["sync_billing"] is True

    def test_a_superadmin_manages_access(self, client, member):
        signed_in(client, _staff("root@example.org", "superadmin"))

        body = _get(client, member.user_id)

        assert body["actions"]["manage_access"] is True
        assert {option["slug"] for option in body["access"]["options"]} >= {"admin", "superadmin"}
        assert body["access"]["roles_locked"] is None

    def test_nobody_changes_their_own_roles(self, client):
        root = _staff("root@example.org", "superadmin")
        signed_in(client, root)

        assert "cannot change your own roles" in _get(client, root.id)["access"]["roles_locked"]

    def test_an_erased_account_offers_no_access_changes(self, client, member):
        from datetime import datetime

        member.user.deleted_at = datetime.utcnow()
        db.session.commit()
        signed_in(client, _staff("root@example.org", "superadmin"))

        body = _get(client, member.user_id)

        assert body["account_state"] == "erased"
        assert body["actions"]["manage_access"] is False and body["actions"]["correct_email"] is False


class TestBillingSync:
    @pytest.fixture
    def boss(self, client):
        boss = _staff("boss@example.org", "admin")
        signed_in(client, boss)
        return boss

    def test_nothing_to_ask_stripe_about(self, client, boss, member):
        response = send(client, "POST", url(member.user_id, "/billing-sync"))

        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "no_billing_reference"

    def test_it_reports_what_changed(self, client, boss, member, monkeypatch):
        member.stripe_customer_id = "cus_1"
        db.session.commit()
        monkeypatch.setattr("aeronautics_members.services.workflows.refresh_member_billing_state",
                            lambda m, **kwargs: (True, {"status": "active"}, None))

        response = send(client, "POST", url(member.user_id, "/billing-sync"))

        assert response.get_json() == {"changed": True, "forum_error": None}
        assert db.session.query(AuditLog).filter_by(event_type="manual_billing_sync").count() == 1

    def test_stripe_down_is_a_bad_gateway_and_the_admins_hear_of_it(self, client, boss, member, monkeypatch):
        member.stripe_customer_id = "cus_1"
        db.session.commit()

        def down(m, **kwargs):
            raise stripe.APIConnectionError("no route")

        monkeypatch.setattr("aeronautics_members.services.workflows.refresh_member_billing_state", down)

        response = send(client, "POST", url(member.user_id, "/billing-sync"))

        assert response.status_code == 502
        assert response.get_json()["error"]["code"] == "external_service_unavailable"
        assert db.session.query(NotificationEvent).filter_by(event_type="manual_billing_sync_failed").count() == 1

    def test_only_with_the_billing_permission(self, client, member):
        signed_in(client, _staff("money@example.org", "treasurer"))

        assert send(client, "POST", url(member.user_id, "/billing-sync")).status_code == 403


class TestTheRest:
    def test_no_picture_permission_without_a_membership(self, client):
        boss = _staff("boss@example.org", "admin")
        signed_in(client, boss)

        response = send(client, "PUT", url(boss.id, "/picture-replacement"), {"allow": True})

        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "no_membership"

    def test_the_erasure_check_refuses_ones_own_account(self, client):
        root = _staff("root@example.org", "superadmin")
        signed_in(client, root)

        erasure = client.get(url(root.id, "/erasure")).get_json()

        assert any("your own account" in reason for reason in erasure["blockers"])
        assert erasure["confirm_email"] == "root@example.org"

    def test_the_erasure_check_needs_the_privacy_permission(self, client, member):
        signed_in(client, _staff("money@example.org", "treasurer"))

        assert client.get(url(member.user_id, "/erasure")).status_code == 403

    def test_unknown_fields_are_refused(self, client, member):
        signed_in(client, _staff("root@example.org", "superadmin"))

        response = send(client, "PUT", url(member.user_id, "/disabled"), {"disabled": True, "why": "x"})

        assert response.status_code == 400 and "why" in response.get_json()["error"]["fields"]
