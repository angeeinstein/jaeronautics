"""Capabilities: what each role may do, who may hold one, and how the first starts.

Access is decided by capability, never by role name, so these tests ask what an
account *can do* rather than what it is called. Splitting a privilege out is
easy to get subtly wrong in two directions: too strict and the installation
locks itself out of the update button -- which is also how the fix would have
been delivered -- and too loose makes the split decorative, because the tab is
hidden while the URL behind it still works.
"""
import pytest

from conftest import app_module, db, make_member
from aeronautics_members.db_models import ROLE_ADMIN, ROLE_SUPERADMIN, User
from aeronautics_members.permissions import Permission, ROLE_PERMISSIONS, roles_with
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


class TestCapabilities:
    """Roles are bundles of capabilities; nothing else asks about role names."""

    def test_a_superadmin_can_do_everything_an_admin_can(self, app):
        admin_can = ROLE_PERMISSIONS[ROLE_ADMIN]
        superadmin_can = ROLE_PERMISSIONS[ROLE_SUPERADMIN]

        assert admin_can < superadmin_can

    def test_the_superadmin_role_alone_opens_the_admin_workspace(self, app):
        """There is no implication: the bundle simply contains the capability."""
        user = _user("solo@example.com", ROLE_SUPERADMIN)

        assert user.can(Permission.ADMIN_ACCESS) is True
        assert user.is_admin is True
        assert user.has_role(ROLE_ADMIN) is False  # the row itself is not granted

    def test_an_admin_cannot_update_or_manage_credentials_or_roles(self, app):
        user = _user("plain@example.com", ROLE_ADMIN)

        assert user.can(Permission.SYSTEM_UPDATE) is False
        assert user.can(Permission.SETTINGS_CREDENTIALS) is False
        assert user.can(Permission.ROLES_MANAGE) is False
        # But everything an administrator is for still works.
        assert user.can(Permission.ACCOUNTS_VIEW) is True
        assert user.can(Permission.APPROVALS_REVIEW) is True
        assert user.can(Permission.SETTINGS_GENERAL) is True

    def test_an_account_with_no_roles_can_do_nothing(self, app):
        user = _user("nobody@example.com")

        assert user.permissions == set()
        assert user.can(Permission.ADMIN_ACCESS) is False

    def test_an_erased_account_can_do_nothing(self, app, monkeypatch):
        """Its rows survive as the record; its access must not."""
        monkeypatch.setattr(privacy, "cancel_member_subscription", lambda m, reason=None: False)
        monkeypatch.setattr(privacy, "anonymise_forum_account", lambda u: (False, False))
        user = _user("erased@example.com", ROLE_ADMIN)
        _user("spare@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        privacy.erase_account(user, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        assert user.can(Permission.ADMIN_ACCESS) is False

    def test_the_displayed_role_names_the_most_capable(self, app):
        assert _user("d1@example.com", ROLE_ADMIN, ROLE_SUPERADMIN).role == ROLE_SUPERADMIN
        assert _user("d2@example.com", ROLE_ADMIN).role == ROLE_ADMIN
        assert _user("d3@example.com").role == "user"

    def test_counting_asks_about_the_capability_not_the_role(self, app):
        """A lone super admin still counts as somebody who can administer."""
        _user("onlysuper@example.com", ROLE_SUPERADMIN)

        assert app_module.count_users_with_permission(Permission.ADMIN_ACCESS) == 1
        assert app_module.count_users_with_permission(Permission.SYSTEM_UPDATE) == 1

    def test_every_capability_is_carried_by_some_role(self, app):
        """A capability no role has is a route nobody can reach."""
        declared = {
            value for name, value in vars(Permission).items()
            if not name.startswith("_") and isinstance(value, str)
        }
        for permission in declared:
            assert roles_with(permission), f"no role grants {permission}"


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

        assert admin.can(Permission.SYSTEM_UPDATE) is False

    def test_a_superadmin_can_grant_and_revoke(self, client):
        boss = _user("boss@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        target = make_member(email="promoteme@example.com")
        _login(client, boss.id)

        client.post(f"/admin/accounts/{target.user_id}/grant-superadmin")
        assert target.user.can(Permission.SYSTEM_UPDATE) is True

        client.post(f"/admin/accounts/{target.user_id}/revoke-superadmin")
        assert target.user.can(Permission.SYSTEM_UPDATE) is False
        # Stepped down to ordinary administrator, not thrown out entirely.
        assert target.user.has_role(ROLE_ADMIN) is True

    def test_revoking_admin_from_a_superadmin_removes_both(self, client):
        """Otherwise implication would leave the access and the button look broken."""
        boss = _user("boss2@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        other = _user("other@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        _login(client, boss.id)

        client.post(f"/admin/accounts/{other.id}/revoke-admin")

        assert other.has_role(ROLE_ADMIN) is False
        assert other.can(Permission.SYSTEM_UPDATE) is False

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

        assert boss.can(Permission.SYSTEM_UPDATE) is True

    def test_one_of_two_superadmins_may_be_stepped_down(self, client):
        boss = _user("stays@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        second = _user("stepsdown@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        _login(client, boss.id)

        client.post(f"/admin/accounts/{second.id}/revoke-superadmin")

        assert second.can(Permission.SYSTEM_UPDATE) is False
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

        assert gone.user.can(Permission.SYSTEM_UPDATE) is False


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
        assert user.can(Permission.SYSTEM_UPDATE) is True

    def test_a_later_admin_does_not(self, app):
        runner = app.test_cli_runner()
        runner.invoke(args=["create-admin", "one@example.com", "--password", "pw"])
        runner.invoke(args=["create-admin", "two@example.com", "--password", "pw"])

        second = db.session.execute(db.select(User).filter_by(email="two@example.com")).scalar_one()
        assert second.has_role(ROLE_ADMIN) is True
        assert second.can(Permission.SYSTEM_UPDATE) is False

    def test_the_flag_forces_it_either_way(self, app):
        runner = app.test_cli_runner()
        runner.invoke(args=["create-admin", "a@example.com", "--password", "pw"])
        runner.invoke(args=["create-admin", "b@example.com", "--password", "pw", "--superadmin"])

        second = db.session.execute(db.select(User).filter_by(email="b@example.com")).scalar_one()
        assert second.can(Permission.SYSTEM_UPDATE) is True

    def test_grant_superadmin_is_the_recovery_path(self, app):
        existing = _user("recover@example.com", ROLE_ADMIN)

        result = app.test_cli_runner().invoke(args=["grant-superadmin", "recover@example.com"])

        db.session.refresh(existing)
        assert result.exit_code == 0
        assert existing.can(Permission.SYSTEM_UPDATE) is True

    def test_granting_to_an_unknown_address_fails_loudly(self, app):
        result = app.test_cli_runner().invoke(args=["grant-superadmin", "nobody@example.com"])

        assert result.exit_code == 1


class TestAddingARoleNeedsNoOtherChange:
    """The point of the whole arrangement, exercised rather than asserted.

    A future moderator is defined here exactly as it would be in
    permissions.py -- one entry, nothing else -- and then has to work: reach the
    forum queue, be refused the settings it was not given, and count towards the
    lockout guards for the capabilities it does carry.
    """

    @pytest.fixture
    def moderator_role(self, app, monkeypatch):
        bundle = frozenset({Permission.ADMIN_ACCESS, Permission.FORUM_MODERATE})
        monkeypatch.setitem(ROLE_PERMISSIONS, "moderator", bundle)
        app_module.seed_default_roles()
        db.session.commit()
        return bundle

    def test_the_role_row_appears_from_the_table_alone(self, app, moderator_role):
        from aeronautics_members.db_models import Role

        row = db.session.execute(db.select(Role).filter_by(slug="moderator")).scalar_one_or_none()
        assert row is not None

    def test_its_holder_reaches_what_the_entry_lists(self, client, moderator_role):
        mod = _user("mod@example.com", "moderator")
        _login(client, mod.id)

        assert client.get("/admin").status_code == 200
        assert client.get("/admin/forum").status_code == 200

    def test_and_is_refused_what_it_does_not(self, client, moderator_role):
        mod = _user("mod2@example.com", "moderator")
        _login(client, mod.id)

        assert client.get("/admin/settings").status_code == 302
        assert client.get("/admin/logs").status_code == 302
        assert client.post("/admin/system-update").status_code == 302

    def test_it_counts_towards_the_capabilities_it_carries(self, app, moderator_role):
        """So the last-admin guard sees a moderator as somebody still in charge."""
        _user("mod3@example.com", "moderator")

        assert app_module.count_users_with_permission(Permission.ADMIN_ACCESS) == 1
        assert app_module.count_users_with_permission(Permission.SYSTEM_UPDATE) == 0

    def test_no_route_or_template_mentions_it(self, app, moderator_role):
        """If adding a role needed edits elsewhere, they would be here."""
        import pathlib

        root = pathlib.Path(app_module.__file__).parent
        for path in list(root.rglob("*.py")) + list(root.rglob("*.html")):
            if path.name == "permissions.py":
                continue
            assert "moderator" not in path.read_text(), f"{path} names the role"

    def test_the_navigation_offers_only_what_it_can_reach(self, client, moderator_role):
        """A link that bounces you is worse than no link."""
        mod = _user("mod4@example.com", "moderator")
        _login(client, mod.id)

        body = client.get("/admin").get_data(as_text=True)

        assert "/admin/forum" in body
        assert "/admin/settings" not in body
        assert "/admin/logs" not in body

    def test_the_account_filter_understands_a_role_it_never_heard_of(self, client, moderator_role):
        """The filter asks about the capability, not about Role.slug == "admin".

        Hard-coding the slug filed a moderator under "member only" and meant the
        dropdown had to be edited for every new role -- the exact coupling this
        arrangement is meant to remove.
        """
        boss = _user("filterboss@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        _user("filtermod@example.com", "moderator")
        _login(client, boss.id)

        staff = client.get("/admin/accounts?role=staff").get_data(as_text=True)
        assert "filtermod@example.com" in staff

        # And it must not be mistaken for an ordinary member.
        members_only = client.get("/admin/accounts?role=member").get_data(as_text=True)
        assert "filtermod@example.com" not in members_only

        by_role = client.get("/admin/accounts?role=role:moderator").get_data(as_text=True)
        assert "filtermod@example.com" in by_role
        assert "filterboss@example.com" not in by_role

    def test_the_filter_dropdown_lists_it(self, client, moderator_role):
        boss = _user("dropdownboss@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        _login(client, boss.id)

        body = client.get("/admin/accounts").get_data(as_text=True)

        assert 'value="role:moderator"' in body


class TestTheAccountListOffersOnlyUsableActions:
    def test_an_admin_is_not_shown_role_buttons_that_would_bounce_them(self, client):
        admin = _user("listadmin@example.com", ROLE_ADMIN)
        make_member(email="listed@example.com")
        _login(client, admin.id)

        body = client.get("/admin/accounts").get_data(as_text=True)

        assert "grant-admin" not in body
        assert "revoke-admin" not in body
        assert "/admin/accounts/" in body  # the View link is still there

    def test_a_superadmin_keeps_them(self, client):
        boss = _user("listboss@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        make_member(email="listed2@example.com")
        _login(client, boss.id)

        body = client.get("/admin/accounts").get_data(as_text=True)

        assert "grant-admin" in body

    def test_an_erased_account_is_offered_no_role_change(self, client, monkeypatch):
        monkeypatch.setattr(privacy, "cancel_member_subscription", lambda m, reason=None: False)
        monkeypatch.setattr(privacy, "anonymise_forum_account", lambda u: (False, False))
        boss = _user("listboss2@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        gone = make_member(email="erasedlisted@example.com")
        privacy.erase_account(gone.user, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()
        _login(client, boss.id)

        body = client.get("/admin/accounts").get_data(as_text=True)

        assert f"/admin/accounts/{gone.user_id}/grant-admin" not in body
