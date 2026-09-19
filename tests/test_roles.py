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
from aeronautics_members.services.access import describe_role_change


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


class TestSettingRoles:
    """One endpoint takes the whole set, so the guards are about the result."""

    def _set(self, client, user_id, *slugs):
        return client.post(f"/admin/accounts/{user_id}/roles", data={"roles": list(slugs)})

    def test_an_admin_cannot_change_anyones_roles(self, client):
        admin = _user("nogrant@example.com", ROLE_ADMIN)
        target = make_member(email="target@example.com")
        _login(client, admin.id)

        self._set(client, target.user_id, ROLE_ADMIN)

        assert target.user.has_role(ROLE_ADMIN) is False

    def test_an_admin_cannot_promote_themselves(self, client):
        admin = _user("selfpromote@example.com", ROLE_ADMIN)
        _login(client, admin.id)

        self._set(client, admin.id, ROLE_ADMIN, ROLE_SUPERADMIN)

        assert admin.can(Permission.SYSTEM_UPDATE) is False

    def test_a_superadmin_sets_the_whole_set_at_once(self, client):
        boss = _user("boss@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        target = make_member(email="promoteme@example.com")
        _login(client, boss.id)

        self._set(client, target.user_id, ROLE_ADMIN, ROLE_SUPERADMIN)
        assert target.user.can(Permission.SYSTEM_UPDATE) is True

        self._set(client, target.user_id, ROLE_ADMIN)
        assert target.user.can(Permission.SYSTEM_UPDATE) is False
        assert target.user.has_role(ROLE_ADMIN) is True

    def test_clearing_every_box_removes_all_access(self, client):
        boss = _user("boss2@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        other = _user("other@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        _login(client, boss.id)

        self._set(client, other.id)

        assert other.roles == []
        assert other.can(Permission.ADMIN_ACCESS) is False

    def test_nobody_may_edit_their_own_roles(self, client):
        """The escalation and the self-lockout are the same check."""
        boss = _user("lonely@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        _login(client, boss.id)

        self._set(client, boss.id, ROLE_ADMIN)

        assert boss.can(Permission.SYSTEM_UPDATE) is True

    def test_the_last_holder_of_a_protected_capability_is_refused(self, app):
        """Checked at the service, because HTTP cannot reach this case.

        Taking the last SYSTEM_UPDATE away means editing the only account that
        has it, and only that account can manage roles -- so the self-edit
        refusal catches it first. The guard underneath still has to be right:
        the erasure route reaches the same situation, and a future role holding
        ROLES_MANAGE would reach this one.
        """
        boss = _user("onlyupdater@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)

        change = describe_role_change(boss, [ROLE_ADMIN])

        # One per protected capability that would lose its last holder: this
        # account is the only one that can install an update *and* the only one
        # that can grant access.
        assert {code for code, _message in change["blockers"]} == {"last_holder"}
        assert len(change["blockers"]) == 2

    def test_the_refusal_says_what_would_be_lost(self, app):
        boss = _user("explain@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)

        change = describe_role_change(boss, [ROLE_ADMIN])

        messages = " ".join(message for _code, message in change["blockers"])
        assert "install an update" in messages
        assert "grant access to anyone else" in messages

    def test_a_second_holder_makes_it_allowed(self, app):
        boss = _user("one@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        _user("two@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)

        assert describe_role_change(boss, [ROLE_ADMIN])["blockers"] == []

    def test_an_erased_holder_does_not_count_as_cover(self, app, monkeypatch):
        """They cannot sign in, so they cannot install anything."""
        monkeypatch.setattr(privacy, "cancel_member_subscription", lambda m, reason=None: False)
        monkeypatch.setattr(privacy, "anonymise_forum_account", lambda u: (False, False))
        boss = _user("survivor@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        leaving = _user("leaving@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        privacy.erase_account(leaving, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()

        change = describe_role_change(boss, [ROLE_ADMIN])

        assert {code for code, _message in change["blockers"]} == {"last_holder"}

    def test_an_unknown_role_is_refused(self, client):
        boss = _user("boss4@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        target = make_member(email="unknownrole@example.com")
        _login(client, boss.id)

        self._set(client, target.user_id, "wizard")

        assert target.user.roles == []

    def test_an_erased_account_cannot_be_given_a_role(self, client, monkeypatch):
        monkeypatch.setattr(privacy, "cancel_member_subscription", lambda m, reason=None: False)
        monkeypatch.setattr(privacy, "anonymise_forum_account", lambda u: (False, False))
        boss = _user("boss3@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        gone = make_member(email="gone@example.com")
        privacy.erase_account(gone.user, initiated_by=privacy.INITIATED_BY_MEMBER)
        db.session.commit()
        _login(client, boss.id)

        self._set(client, gone.user_id, ROLE_ADMIN, ROLE_SUPERADMIN)

        assert gone.user.can(Permission.SYSTEM_UPDATE) is False

    def test_the_change_is_audited_as_a_diff(self, client):
        from aeronautics_members.db_models import AuditLog

        boss = _user("auditboss@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        target = make_member(email="audited@example.com")
        _login(client, boss.id)

        self._set(client, target.user_id, ROLE_ADMIN)

        entry = db.session.execute(
            db.select(AuditLog).filter_by(event_type="account_roles_changed")
        ).scalar_one()
        assert entry.event_metadata["granted_roles"] == [ROLE_ADMIN]
        assert entry.event_metadata["revoked_roles"] == []
        assert Permission.ADMIN_ACCESS in entry.event_metadata["permissions_gained"]


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

    def test_it_receives_the_admin_digests_if_its_entry_says_so(self, app, monkeypatch):
        """The non-route surfaces have to honour the table too.

        Admin recipients were selected with Role.slug == "admin", so an account
        holding any other privileged role silently stopped being told about
        errors and review tasks -- a failure that announces itself by nothing
        arriving.
        """
        from datetime import datetime, timezone
        from aeronautics_members.notification_service import NotificationService

        monkeypatch.setitem(
            ROLE_PERMISSIONS, "reporter",
            frozenset({Permission.ADMIN_ACCESS, Permission.NOTIFICATIONS_RECEIVE}),
        )
        monkeypatch.setitem(
            ROLE_PERMISSIONS, "quiet",
            frozenset({Permission.ADMIN_ACCESS, Permission.FORUM_MODERATE}),
        )
        app_module.seed_default_roles()
        db.session.commit()
        for email, role in (("gets@example.com", "reporter"), ("silent@example.com", "quiet")):
            user = _user(email, role)
            user.email_verified_at = datetime.now(timezone.utc)
        db.session.commit()

        recipients = NotificationService(app).get_admin_recipient_emails()

        assert "gets@example.com" in recipients
        assert "silent@example.com" not in recipients

    def test_its_holder_lands_on_the_admin_dashboard_after_signing_in(self, app, moderator_role):
        """Another role-name check that a moderator would have failed."""
        mod = _user("landing@example.com", "moderator")

        assert app_module.get_member_portal_target(mod) == "admin.admin_dashboard"

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


class TestTheAccountListIsReadOnly:
    """Role changes belong on the account, where the consequences are visible.

    Deciding from a list means deciding without knowing what else the account
    holds, or whether anybody else could still do the job.
    """

    def test_no_role_controls_appear_for_anyone(self, client):
        boss = _user("listboss@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        make_member(email="listed@example.com")
        _login(client, boss.id)

        body = client.get("/admin/accounts").get_data(as_text=True)

        assert "/roles" not in body
        assert "Grant Admin" not in body
        assert "Revoke Admin" not in body

    def test_the_view_link_is_still_there(self, client):
        boss = _user("listboss2@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        target = make_member(email="listed2@example.com")
        _login(client, boss.id)

        body = client.get("/admin/accounts").get_data(as_text=True)

        assert f"/admin/accounts/{target.user_id}" in body


class TestRedundantRolesAreNotStoredTwice:
    """"Admin + Super Admin" and "Super Admin" describe the same account.

    Offering both as distinct states asks a question with no answer: whichever
    you pick, the account can do exactly the same things. So only one spelling
    is stored, and the form says which role covers which.
    """

    def _set(self, client, user_id, *slugs):
        return client.post(
            f"/admin/accounts/{user_id}/roles", data={"roles": list(slugs)},
            follow_redirects=True,
        )

    def test_ticking_both_stores_only_the_covering_role(self, client):
        boss = _user("redboss@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        target = make_member(email="bothticked@example.com")
        _login(client, boss.id)

        self._set(client, target.user_id, ROLE_ADMIN, ROLE_SUPERADMIN)

        assert [r.slug for r in target.user.roles] == [ROLE_SUPERADMIN]

    def test_and_loses_nothing(self, client):
        """The covering role grants everything the dropped one did."""
        boss = _user("redboss2@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        target = make_member(email="nothinglost@example.com")
        _login(client, boss.id)

        self._set(client, target.user_id, ROLE_ADMIN, ROLE_SUPERADMIN)

        assert target.user.permissions == ROLE_PERMISSIONS[ROLE_SUPERADMIN]
        assert target.user.can(Permission.ADMIN_ACCESS) is True

    def test_the_drop_is_reported_rather_than_silent(self, client):
        boss = _user("redboss3@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        target = make_member(email="told@example.com")
        _login(client, boss.id)

        body = self._set(client, target.user_id, ROLE_ADMIN, ROLE_SUPERADMIN).get_data(as_text=True)

        assert "not stored separately" in body

    def test_the_page_says_which_role_covers_which(self, client):
        boss = _user("redboss4@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        target = _user("covered@example.com", ROLE_SUPERADMIN)
        _login(client, boss.id)

        body = client.get(f"/admin/accounts/{target.id}").get_data(as_text=True)

        assert "included in Super Admin" in body

    def test_the_page_lists_what_the_account_can_actually_do(self, client):
        """So an unticked Admin box on a Super Admin is not alarming."""
        boss = _user("redboss5@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        target = _user("effective@example.com", ROLE_SUPERADMIN)
        _login(client, boss.id)

        body = client.get(f"/admin/accounts/{target.id}").get_data(as_text=True)

        assert "Open the admin workspace" in body
        assert "Install a new version" in body

    def test_but_folded_away_until_asked(self, client):
        """A dozen capability lines above the controls buries the controls."""
        boss = _user("folded@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        target = _user("foldedtarget@example.com", ROLE_SUPERADMIN)
        _login(client, boss.id)

        body = client.get(f"/admin/accounts/{target.id}").get_data(as_text=True)

        assert "Show what this account can currently do" in body
        # No <details open>: everything on this page starts closed.
        assert "<details open" not in body

    def test_dropping_the_covering_role_leaves_the_other_tickable(self, client):
        """The covered box must still post, or unticking one would clear both."""
        boss = _user("redboss6@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        target = _user("stepdown@example.com", ROLE_SUPERADMIN)
        _login(client, boss.id)

        self._set(client, target.id, ROLE_ADMIN)

        assert [r.slug for r in target.roles] == [ROLE_ADMIN]

    def test_two_orthogonal_roles_both_survive(self, app, monkeypatch):
        """Only a role that is genuinely covered is dropped."""
        from aeronautics_members.permissions import minimal_roles

        monkeypatch.setitem(
            ROLE_PERMISSIONS, "moderator",
            frozenset({Permission.ADMIN_ACCESS, Permission.FORUM_MODERATE}),
        )
        monkeypatch.setitem(
            ROLE_PERMISSIONS, "treasurer",
            frozenset({Permission.ADMIN_ACCESS, Permission.ACCOUNTS_BILLING}),
        )

        assert minimal_roles({"moderator", "treasurer"}) == {"moderator", "treasurer"}
        # ...and a role inside another still is.
        assert minimal_roles({ROLE_ADMIN, ROLE_SUPERADMIN}) == {ROLE_SUPERADMIN}

    def test_each_role_can_be_inspected_before_it_is_granted(self, client):
        """Deciding what to hand someone should not need reading the source."""
        boss = _user("inspect@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        target = make_member(email="inspectme@example.com")
        _login(client, boss.id)

        body = client.get(f"/admin/accounts/{target.user_id}").get_data(as_text=True)

        assert "Show what this role can do" in body
        # Super Admin's list names what only it carries...
        assert "Install a new version and roll one back" in body
        # ...and Admin's names what an ordinary administrator gets.
        assert "Approve profile change requests" in body

    def test_the_union_rule_is_stated_rather_than_implied(self, client):
        boss = _user("union@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        target = make_member(email="unionme@example.com")
        _login(client, boss.id)

        body = client.get(f"/admin/accounts/{target.user_id}").get_data(as_text=True)

        assert "combined permissions of every role selected" in body

    def test_a_new_role_is_inspectable_with_no_template_change(self, app, monkeypatch, client):
        monkeypatch.setitem(
            ROLE_PERMISSIONS, "moderator",
            frozenset({Permission.ADMIN_ACCESS, Permission.FORUM_MODERATE}),
        )
        app_module.seed_default_roles()
        db.session.commit()
        boss = _user("newrole@example.com", ROLE_ADMIN, ROLE_SUPERADMIN)
        target = make_member(email="newrolefor@example.com")
        _login(client, boss.id)

        body = client.get(f"/admin/accounts/{target.user_id}").get_data(as_text=True)

        assert 'value="moderator"' in body
        assert "Moderate the forum and avatars" in body
