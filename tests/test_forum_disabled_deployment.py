"""The membership portal has to stand up on its own, with no forum behind it.

This is a deployment shape, not a corner case: the plan is to launch the portal
and the new forum together, with the fallback of going live on the portal alone
and leaving the old forum running until the switch is ready. That fallback is
only real if somebody has run it.

So these drive the member and administrator journeys against the **real**
ForumService with ``forum_integration_enabled`` off -- the smoke suite uses a
stub whose ``is_enabled()`` returns False, which proves the callers cope with a
disabled service but not that the actual service behaves that way.
"""
from datetime import date, datetime, timezone

import pytest

from conftest import app_module, db, make_member
from aeronautics_members.db_models import Setting, User
from aeronautics_members.services.forum import get_forum_service

CUR = date.today().year


@pytest.fixture(autouse=True)
def forum_is_off(app):
    """The default, stated explicitly so the test says what it is testing."""
    db.session.merge(Setting(key="forum_integration_enabled", value="False"))
    db.session.commit()
    assert get_forum_service().is_enabled() is False


def _member(email="offline@example.com"):
    member = make_member(
        email=email, payment_status="paid", is_active=True,
        membership_starts_on=date(CUR, 1, 1), membership_ends_on=date(CUR, 12, 31),
    )
    member.user.email_verified_at = datetime.now(timezone.utc)
    member.user.set_password("Str0ngPassw0rd!x")
    db.session.commit()
    return member


def _admin(client, email="offlineadmin@example.com"):
    user = User(email=email)
    user.set_password("x")
    db.session.add(user)
    user.grant_role(app_module.get_role("admin"))
    user.grant_role(app_module.get_role("superadmin"))
    db.session.commit()
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
    return user


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)


class TestTheMemberJourney:
    def test_signing_in_works(self, client):
        member = _member()

        response = client.post(
            "/login",
            data={"email": member.email_private, "password": "Str0ngPassw0rd!x"},
            follow_redirects=True,
        )

        assert response.status_code == 200

    def test_the_account_page_renders(self, client):
        member = _member("account@example.com")
        _login(client, member.user_id)

        # The page self-redirects once with a cache-busting parameter.
        response = client.get("/account", follow_redirects=True)

        assert response.status_code == 200
        assert "My Account" in response.get_data(as_text=True)

    def test_the_profile_can_be_saved(self, client):
        """Saving a profile triggers a forum sync when the forum is on."""
        member = _member("profile@example.com")
        _login(client, member.user_id)

        response = client.post(
            "/account/profile",
            data={
                f"profile-{key}": value
                for key, value in {
                    "street": "Alte Poststrasse", "house_number": "149",
                    "postal_code": "8020", "city": "Vienna", "country": "Austria",
                    "phone_private": "+43660000000", "email_private": member.email_private,
                    # A student's profile now carries a university address.
                    "email_work": "profile@edu.fh-joanneum.at",
                }.items()
            },
        )

        assert response.status_code == 302
        assert member.city == "Vienna"

    def test_the_data_export_still_works(self, client):
        member = _member("export@example.com")
        _login(client, member.user_id)

        response = client.get("/account/data-export")

        assert response.status_code == 200
        assert response.json["forum_account"] is None


class TestTheForumRoutesRefuseCleanly:
    """Reachable by typing the URL even when nothing links to them."""

    @pytest.mark.parametrize("path", ["/forum", "/account/forum"])
    def test_no_server_error(self, client, path):
        member = _member("forumroutes@example.com")
        _login(client, member.user_id)

        response = client.get(path, follow_redirects=True)

        assert response.status_code < 500


class TestTheAdminSideRenders:
    def test_every_admin_page_renders(self, client):
        _admin(client)
        member = _member("adminsees@example.com")

        for path in (
            "/admin",
            "/admin/accounts",
            f"/admin/accounts/{member.user_id}",
            "/admin/approvals",
            "/admin/logs",
            "/admin/settings",
            "/admin/forum",
        ):
            assert client.get(path).status_code == 200, path

    def test_the_health_report_is_not_unhealthy_merely_because_the_forum_is_off(self, app):
        from aeronautics_members.services import diagnostics

        health = diagnostics.collect_system_health()

        assert health["healthy"] is True


class TestBackgroundWorkDoesNotDependOnIt:
    def test_a_billing_refresh_skips_the_forum(self, app):
        """It syncs the forum when enabled; with it off there is nothing to do.

        No Stripe stub needed: force_stripe_sync is off, so this exercises the
        local half and the forum branch only.
        """
        from aeronautics_members.services import workflows

        member = _member("billing@example.com")

        _changed, _subscription, forum_result = workflows.refresh_member_billing_state(
            member, force_stripe_sync=False
        )

        assert forum_result is None

    def test_the_outbox_does_not_fill_with_forum_work(self, app):
        """Queued forum syncs would pile up forever with nothing to send them to."""
        from aeronautics_members.db_models import ExternalWorkItem

        member = _member("outbox@example.com")
        member.city = "Graz"
        db.session.commit()

        pending = db.session.scalar(
            db.select(db.func.count()).select_from(ExternalWorkItem)
            .where(ExternalWorkItem.status == ExternalWorkItem.STATUS_PENDING)
        )
        assert pending == 0
