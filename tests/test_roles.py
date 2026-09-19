"""The super admin role: what it carries, who may hold it, and how it starts.

Splitting a privilege out of ``admin`` is easy to get subtly wrong in two
directions. Too strict and the installation locks itself out of the update
button -- which is also how the fix would have been delivered. Too loose and the
split is decorative, because the tab is hidden while the URL behind it still
works.
"""
import pytest

from conftest import app_module, db, make_member
from aeronautics_members.db_models import ROLE_ADMIN, ROLE_SUPERADMIN, User
from aeronautics_members.services import ConflictError, privacy


def _user(email, *roles):
    user = User(email=email)
    user.set_password("x")
    db.session.add(user)
    for slug in roles:
        user.grant_role(app_module.get_role(slug))
    db.session.commit()
    return user


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)


class TestImplication:
    """A super admin is an administrator with more, not a parallel account."""

    def test_a_superadmin_passes_an_admin_check(self, app):
        user = _user("implied@example.com", ROLE_SUPERADMIN)

        assert user.is_admin is True
        assert user.has_role(ROLE_ADMIN) is True

    def test_but_does_not_hold_the_admin_row(self, app):
        """The distinction revoking and counting depend on."""
        user = _user("implied2@example.com", ROLE_SUPERADMIN)

        assert user.has_role_directly(ROLE_ADMIN) is False
        assert user.has_role_directly(ROLE_SUPERADMIN) is True

    def test_an_admin_is_not_a_superadmin(self, app):
        user = _user("plain@example.com", ROLE_ADMIN)

        assert user.is_superadmin is False

    def test_the_displayed_role_names_the_strongest(self, app):
        assert _user("d1@example.com", ROLE_ADMIN, ROLE_SUPERADMIN).role == ROLE_SUPERADMIN
        assert _user("d2@example.com", ROLE_ADMIN).role == ROLE_ADMIN
        assert _user("d3@example.com").role == "user"

    def test_counting_admins_includes_implied_ones(self, app):
        """Otherwise the last-admin guard would not see a lone super admin."""
        _user("onlysuper@example.com", ROLE_SUPERADMIN)

        assert app_module.count_users_with_role(ROLE_ADMIN) == 1


class TestTheUpdateSurfaceIsRestricted:
    """Hiding a tab is not access control; these are the checks that decide."""

    @pytest.mark.parametrize(
        "path,method",
        [
            ("/admin/system-update", "POST"),
            ("/admin/system-update/status", "GET"),
        ],
    )
    def test_an_admin_is_refused(self, client, path, method):
        admin = _user("noupdate@example.com", ROLE_ADMIN)
        _login(client, admin.id)

        response = client.open(path, method=method)

        assert response.status_code == 302

    def test_a_superadmin_is_allowed_to_read_the_status(self, client):
        boss = _user("canupdate@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        _login(client, boss.id)

        assert client.get("/admin/system-update/status").status_code == 200


class TestCredentialsAreRestricted:
    def test_an_admin_cannot_post_the_billing_settings(self, client):
        """The tab is not rendered, but the form is trivial to reconstruct."""
        admin = _user("nokeys@example.com", ROLE_ADMIN)
        _login(client, admin.id)

        response = client.post(
            "/admin/settings",
            data={
                "save_settings": "1",
                "settings_section": "billing",
                "stripe_secret_key": "sk_live_attacker",
            },
        )

        assert response.status_code == 302
        assert db.session.get(app_module.Setting, "stripe_secret_key") is None

    def test_an_admin_cannot_post_the_forum_settings(self, client):
        admin = _user("nodiscourse@example.com", ROLE_ADMIN)
        _login(client, admin.id)

        client.post(
            "/admin/settings",
            data={"save_settings": "1", "settings_section": "forum", "discourse_api_key": "leak"},
        )

        assert db.session.get(app_module.Setting, "discourse_api_key") is None

    def test_an_admin_may_still_save_the_general_settings(self, client):
        """The split must not take away ordinary administration."""
        admin = _user("general@example.com", ROLE_ADMIN)
        _login(client, admin.id)

        response = client.post(
            "/admin/settings",
            data={"save_settings": "1", "settings_section": "general", "automatic_emails_enabled": "on"},
        )

        assert response.status_code == 302
        assert db.session.get(app_module.Setting, "automatic_emails_enabled").value == "True"

    def test_an_admin_cannot_export_the_smtp_passwords(self, client):
        admin = _user("nosmtp@example.com", ROLE_ADMIN)
        _login(client, admin.id)

        response = client.post("/admin/settings/mail-accounts/export", data={"export_password": "x"})

        assert response.status_code == 302
        assert "application/json" not in response.headers.get("Content-Type", "")


class TestRoleManagementIsRestricted:
    def test_an_admin_cannot_grant_admin(self, client):
        admin = _user("nogrant@example.com", ROLE_ADMIN)
        target = make_member(email="target@example.com")
        _login(client, admin.id)

        client.post(f"/admin/accounts/{target.user_id}/grant-admin")

        assert target.user.has_role(ROLE_ADMIN) is False

    def test_an_admin_cannot_promote_themselves(self, client):
        admin = _user("selfpromote@example.com", ROLE_ADMIN)
        _login(client, admin.id)

        client.post(f"/admin/accounts/{admin.id}/grant-superadmin")

        assert admin.is_superadmin is False

    def test_a_superadmin_can_grant_and_revoke(self, client):
        boss = _user("boss@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        target = make_member(email="promoteme@example.com")
        _login(client, boss.id)

        client.post(f"/admin/accounts/{target.user_id}/grant-superadmin")
        assert target.user.is_superadmin is True

        client.post(f"/admin/accounts/{target.user_id}/revoke-superadmin")
        assert target.user.is_superadmin is False
        # Stepped down to ordinary administrator, not thrown out entirely.
        assert target.user.has_role(ROLE_ADMIN) is True

    def test_revoking_admin_from_a_superadmin_removes_both(self, client):
        """Otherwise implication would leave the access and the button look broken."""
        boss = _user("boss2@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        other = _user("other@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        _login(client, boss.id)

        client.post(f"/admin/accounts/{other.id}/revoke-admin")

        assert other.has_role(ROLE_ADMIN) is False
        assert other.is_superadmin is False

    def test_a_superadmin_cannot_strip_their_own_role(self, client):
        """The reachable half of "do not leave nobody in charge".

        The count guard beneath it is defence in depth and cannot be reached
        through the interface: stripping the only super admin means stripping
        yourself, which this catches first. The count is what stops the erasure
        route, which is covered separately.
        """
        boss = _user("lonely@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        _login(client, boss.id)

        client.post(f"/admin/accounts/{boss.id}/revoke-superadmin")

        assert boss.is_superadmin is True

    def test_one_of_two_superadmins_may_be_stepped_down(self, client):
        boss = _user("stays@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        second = _user("stepsdown@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        _login(client, boss.id)

        client.post(f"/admin/accounts/{second.id}/revoke-superadmin")

        assert second.is_superadmin is False
        assert second.has_role(ROLE_ADMIN) is True

    def test_an_erased_account_cannot_be_promoted(self, client, monkeypatch):
        monkeypatch.setattr(privacy, "cancel_member_subscription", lambda m, reason=None: False)
        monkeypatch.setattr(privacy, "anonymise_forum_account", lambda u: (False, False))
        boss = _user("boss3@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        gone = make_member(email="gone@example.com")
        privacy.erase_account(gone.user, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()
        _login(client, boss.id)

        client.post(f"/admin/accounts/{gone.user_id}/grant-superadmin")

        assert gone.user.is_superadmin is False


class TestTheUiHidesWhatItDoesNotOffer:
    def test_an_admin_sees_no_maintenance_or_credential_tabs(self, client):
        admin = _user("hidden@example.com", ROLE_ADMIN)
        _login(client, admin.id)

        body = client.get("/admin/settings").get_data(as_text=True)

        assert "settings-maintenance-tab" not in body
        assert "settings-billing-tab" not in body
        assert "settings-mail-tab" not in body
        # Ordinary administration is untouched.
        assert "settings-general-tab" in body
        assert "settings-notifications-tab" in body

    def test_a_superadmin_sees_them(self, client):
        boss = _user("visible@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        _login(client, boss.id)

        body = client.get("/admin/settings").get_data(as_text=True)

        assert "settings-maintenance-tab" in body
        assert "settings-billing-tab" in body

    def test_the_stored_secret_never_reaches_an_admins_browser(self, client):
        """The point of hiding rather than disabling."""
        app_module.set_setting_value("stripe_secret_key", "sk_test_supersecret")
        db.session.commit()
        admin = _user("nosecret@example.com", ROLE_ADMIN)
        _login(client, admin.id)

        body = client.get("/admin/settings").get_data(as_text=True)

        assert "sk_test_supersecret" not in body

    def test_an_admin_sees_no_role_buttons(self, client):
        admin = _user("norolebuttons@example.com", ROLE_ADMIN)
        target = make_member(email="someone@example.com")
        _login(client, admin.id)

        body = client.get(f"/admin/accounts/{target.user_id}").get_data(as_text=True)

        assert "grant-admin" not in body
        assert "grant-superadmin" not in body


class TestErasureKeepsSomebodyInCharge:
    @pytest.fixture(autouse=True)
    def no_remote_calls(self, monkeypatch):
        monkeypatch.setattr(privacy, "cancel_member_subscription", lambda m, reason=None: False)
        monkeypatch.setattr(privacy, "anonymise_forum_account", lambda u: (False, False))

    def test_the_last_superadmin_cannot_erase_themselves(self, app):
        boss = _user("onlyboss@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        _user("spare@example.com", ROLE_ADMIN)  # an admin exists, so not the last admin

        with pytest.raises(ConflictError) as excinfo:
            privacy.erase_account(boss, initiated_by=privacy.INITIATED_BY_MEMBER)

        assert excinfo.value.code == "last_superadmin"

    def test_a_second_superadmin_makes_it_possible(self, app):
        boss = _user("leaving@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        _user("staying@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)

        privacy.erase_account(boss, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        assert boss.deleted_at is not None

    def test_the_impact_names_the_blocker_before_anything_happens(self, app):
        boss = _user("warnme@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        _user("spare2@example.com", ROLE_ADMIN)

        impact = privacy.describe_deletion_impact(boss)

        assert impact["is_last_superadmin"] is True
        assert "last_superadmin" in impact["blockers"]


class TestBootstrap:
    """Who holds the role the first time, when by definition nobody does."""

    def test_the_first_admin_created_becomes_a_superadmin(self, app):
        app.test_cli_runner().invoke(
            args=["create-admin", "first@example.com", "--password", "pw"]
        )

        user = db.session.execute(db.select(User).filter_by(email="first@example.com")).scalar_one()
        assert user.is_superadmin is True

    def test_a_later_admin_does_not(self, app):
        runner = app.test_cli_runner()
        runner.invoke(args=["create-admin", "one@example.com", "--password", "pw"])
        runner.invoke(args=["create-admin", "two@example.com", "--password", "pw"])

        second = db.session.execute(db.select(User).filter_by(email="two@example.com")).scalar_one()
        assert second.has_role(ROLE_ADMIN) is True
        assert second.is_superadmin is False

    def test_the_flag_forces_it_either_way(self, app):
        runner = app.test_cli_runner()
        runner.invoke(args=["create-admin", "a@example.com", "--password", "pw"])
        runner.invoke(args=["create-admin", "b@example.com", "--password", "pw", "--superadmin"])

        second = db.session.execute(db.select(User).filter_by(email="b@example.com")).scalar_one()
        assert second.is_superadmin is True

    def test_grant_superadmin_is_the_recovery_path(self, app):
        existing = _user("recover@example.com", ROLE_ADMIN)

        result = app.test_cli_runner().invoke(args=["grant-superadmin", "recover@example.com"])

        db.session.refresh(existing)
        assert result.exit_code == 0
        assert existing.is_superadmin is True

    def test_granting_to_an_unknown_address_fails_loudly(self, app):
        result = app.test_cli_runner().invoke(args=["grant-superadmin", "nobody@example.com"])

        assert result.exit_code == 1
