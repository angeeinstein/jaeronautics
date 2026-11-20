"""Permissions per person, roles as templates (docs/permissions-plan.md).

A person holds roles, plus exceptions -- permissions added or taken away. A
super admin holds everything and takes none. Whoever grants gives or takes
only what they hold themselves, and an account holding more than them is out
of their reach: its address, its switch, its erasure.
"""
import pytest

from api_helpers import send
from conftest import app_module, db, make_member
from aeronautics_members.db_models import UserPermission
from aeronautics_members.permissions import (
    ALL_PERMISSIONS, NEEDS, PERMISSION_ROW_NEEDS, PERMISSION_ROWS, ROLE_PERMISSIONS, Permission,
    effective_permissions)
from aeronautics_members.services import access, messages
from aeronautics_members.services.clock import get_now_utc


def _user(email, *roles):
    member = make_member(email=email)
    for slug in roles:
        member.user.grant_role(app_module.get_role(slug))
    db.session.commit()
    return member.user


def _login(client, user):
    from flask import g

    g.pop("_login_user", None)  # the tests share one application context
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)


def _set(client, user, roles, permissions=None):
    body = {"roles": list(roles)}
    if permissions is not None:
        body["permissions"] = sorted(permissions)
    return send(client, "PUT", f"/api/v1/admin/accounts/{user.id}/roles", body)


class TestTheCatalogue:
    def test_every_permission_is_on_one_row_but_the_workspace(self):
        listed = [permission for row in PERMISSION_ROWS for permission in row.permissions]

        assert sorted(listed) == sorted(ALL_PERMISSIONS - {Permission.ADMIN_ACCESS})
        assert len(listed) == len(set(listed)) and len({row.key for row in PERMISSION_ROWS}) == len(PERMISSION_ROWS)

    def test_a_row_and_its_levels(self):
        money = next(row for row in PERMISSION_ROWS if row.key == "team_money")

        assert [step.label for step in money.levels] == ["View", "Manage"] and money.levels[0].sees_only
        assert money.at(2) == {Permission.TEAMS_MONEY_VIEW, Permission.TEAMS_MONEY}
        assert money.level_of({Permission.TEAMS_MONEY_VIEW}) == 1 and money.level_of(set()) == 0
        # Re-syncing billing is done on the account's page, so needs seeing the accounts.
        assert PERMISSION_ROW_NEEDS["billing"] == [{"accounts": 1}]

    def test_the_workspace_comes_with_what_is_done_in_it(self):
        assert effective_permissions(["auditor"], taken=[Permission.TEAMS_MONEY_VIEW, Permission.CREDIT_VIEW]) == set()
        assert effective_permissions([], added=[Permission.FORUM_MODERATOR]) == {Permission.FORUM_MODERATOR}

    def test_a_forum_administrator_is_a_moderator_there_too(self):
        assert effective_permissions([], added=[Permission.FORUM_ADMIN]) == {
            Permission.FORUM_ADMIN, Permission.FORUM_MODERATOR}

    def test_changing_needs_seeing(self):
        # "every writing permission also requires the reading permission" (2026-11-19)
        assert Permission.TEAMS_MONEY_VIEW in NEEDS[Permission.TEAMS_MONEY]
        assert Permission.CREDIT_VIEW in NEEDS[Permission.CREDIT_MANAGE]
        assert Permission.LEGAL_TEXTS in NEEDS[Permission.LEGAL_PUBLISH]
        assert Permission.MESSAGES_ANSWER in NEEDS[Permission.MESSAGES_RECEIVE]

    def test_a_permission_brings_what_it_needs_and_goes_with_it(self):
        assert effective_permissions([], added=[Permission.LEGAL_PUBLISH]) == {
            Permission.ADMIN_ACCESS, Permission.LEGAL_TEXTS, Permission.LEGAL_PUBLISH}
        treasurer = effective_permissions(["treasurer"], taken=[Permission.TEAMS_MONEY_VIEW])
        assert Permission.TEAMS_MONEY not in treasurer and Permission.CREDIT_MANAGE in treasurer

    def test_a_super_admin_holds_everything_and_takes_no_exceptions(self):
        assert ROLE_PERMISSIONS["superadmin"] == ALL_PERMISSIONS
        assert effective_permissions(["superadmin"], taken=[Permission.CARDS_MANAGE]) == ALL_PERMISSIONS

    def test_the_auditor_sees_the_money_and_changes_nothing(self):
        auditor = effective_permissions(["auditor"])

        assert {Permission.TEAMS_MONEY_VIEW, Permission.CREDIT_VIEW} <= auditor
        assert not auditor & {Permission.TEAMS_MONEY, Permission.CREDIT_MANAGE, Permission.ACCOUNTS_VIEW}


class TestExceptions:
    def test_a_member_given_one_permission(self, client):
        boss = _user("boss@example.org", "superadmin")
        helper = _user("helper@example.org")
        _login(client, boss)

        saved = _set(client, helper, [], {Permission.MESSAGES_ANSWER})

        assert saved.status_code == 200
        assert helper.permissions == {Permission.ADMIN_ACCESS, Permission.MESSAGES_ANSWER}
        assert [(row.permission, row.granted) for row in helper.permission_exceptions] == [
            (Permission.ADMIN_ACCESS, True), (Permission.MESSAGES_ANSWER, True)]

    def test_an_admin_without_cards_and_the_role_still_live(self, client, monkeypatch):
        boss = _user("boss@example.org", "superadmin")
        admin = _user("admin@example.org", "admin")
        _login(client, boss)

        _set(client, admin, ["admin"], ROLE_PERMISSIONS["admin"] - {Permission.CARDS_MANAGE})

        assert not admin.can(Permission.CARDS_MANAGE) and admin.can(Permission.ACCOUNTS_VIEW)
        # A permission added to the role later reaches everyone holding it.
        monkeypatch.setitem(ROLE_PERMISSIONS, "admin", ROLE_PERMISSIONS["admin"] | {Permission.SYSTEM_BACKUP})
        assert admin.can(Permission.SYSTEM_BACKUP)

    def test_the_roles_alone_keep_the_exceptions(self, client):
        boss = _user("boss@example.org", "superadmin")
        helper = _user("helper@example.org")
        _login(client, boss)
        _set(client, helper, [], {Permission.CARDS_MANAGE})

        _set(client, helper, ["auditor"])  # an older page: roles only

        assert helper.can(Permission.CARDS_MANAGE) and helper.can(Permission.CREDIT_VIEW)

    def test_made_a_super_admin_the_exceptions_go(self, client):
        boss = _user("boss@example.org", "superadmin")
        admin = _user("admin@example.org", "admin")
        _login(client, boss)
        _set(client, admin, ["admin"], ROLE_PERMISSIONS["admin"] - {Permission.CARDS_MANAGE})

        _set(client, admin, ["superadmin"], ROLE_PERMISSIONS["admin"])

        assert admin.permission_exceptions == [] and admin.permissions == set(ALL_PERMISSIONS)

    def test_the_change_is_logged_with_its_exceptions(self, client):
        from aeronautics_members.db_models import AuditLog

        boss = _user("boss@example.org", "superadmin")
        helper = _user("helper@example.org")
        _login(client, boss)

        _set(client, helper, [], {Permission.CARDS_MANAGE})

        entry = db.session.query(AuditLog).filter_by(event_type="account_roles_changed").one()
        assert entry.event_metadata["permissions_added"] == [Permission.ADMIN_ACCESS, Permission.CARDS_MANAGE]
        assert "+cards.manage" in entry.after_state["permission_exceptions"]


class TestOnlyWhatOneHolds:
    def test_an_admin_cannot_give_what_they_do_not_hold(self, client):
        admin = _user("admin@example.org", "admin")
        helper = _user("helper@example.org")
        _login(client, admin)

        refused = _set(client, helper, [], {Permission.SETTINGS_CREDENTIALS})
        given = _set(client, helper, [], {Permission.CARDS_MANAGE})

        assert refused.status_code == 409 and given.status_code == 200
        assert helper.can(Permission.CARDS_MANAGE) and not helper.can(Permission.SETTINGS_CREDENTIALS)

    def test_an_admin_cannot_unmake_a_super_admin(self, client):
        admin = _user("admin@example.org", "admin")
        boss = _user("boss@example.org", "superadmin")
        _login(client, admin)

        refused = _set(client, boss, ["admin"])

        assert refused.status_code == 409 and boss.can(Permission.SYSTEM_UPDATE)

    def test_an_account_holding_more_is_out_of_reach(self, client):
        boss = _user("boss@example.org", "superadmin")
        admin = _user("admin@example.org", "admin")
        backup = _user("backup@example.org", "admin")
        _login(client, boss)
        _set(client, backup, ["admin"], ROLE_PERMISSIONS["admin"] | {Permission.SYSTEM_BACKUP})
        _login(client, admin)

        page = client.get(f"/api/v1/admin/accounts/{backup.id}").get_json()["access"]
        refused = _set(client, backup, [])

        assert refused.status_code == 409 and backup.can(Permission.ACCOUNTS_VIEW)
        assert "holds permissions you do not" in page["roles_locked"]
        assert not any(row["may_reach"] for row in page["rows"])
        assert not any(option["may_change"] for option in page["options"])

    def test_a_super_admin_out_of_an_admins_reach(self, client):
        # Otherwise an admin could change the super admin's address, reset the password and hold it.
        admin = _user("admin@example.org", "admin")
        boss = _user("boss@example.org", "superadmin")
        _login(client, admin)

        address = send(client, "PUT", f"/api/v1/admin/accounts/{boss.id}/email", {"email": "mine@example.org"})
        switch = send(client, "PUT", f"/api/v1/admin/accounts/{boss.id}/disabled", {"disabled": True})
        erasure = client.get(f"/api/v1/admin/accounts/{boss.id}/erasure").get_json()

        assert address.status_code == 403 and boss.email == "boss@example.org"
        assert switch.status_code == 409 and boss.disabled_at is None
        assert any("holds permissions you do not" in blocker for blocker in erasure["blockers"])

    def test_an_admin_still_corrects_a_plain_members_address(self, client):
        admin = _user("admin@example.org", "admin")
        member = _user("member@example.org")
        _login(client, admin)

        changed = send(client, "PUT", f"/api/v1/admin/accounts/{member.id}/email", {"email": "fixed@example.org"})

        assert changed.status_code == 200


class TestWhoHoldsIt:
    def test_a_permission_added_counts_and_one_taken_away_does_not(self, client):
        boss = _user("boss@example.org", "superadmin")
        helper = _user("helper@example.org")
        admin = _user("admin@example.org", "admin")
        _login(client, boss)
        _set(client, helper, [], {Permission.MESSAGES_RECEIVE})
        _set(client, admin, ["admin"], ROLE_PERMISSIONS["admin"] - {Permission.MESSAGES_RECEIVE})
        for user in (boss, helper, admin):
            user.email_verified_at = get_now_utc()
        db.session.commit()

        assert messages.admin_recipients() == ["boss@example.org", "helper@example.org"]
        assert {user.email for user in access.holders(Permission.MESSAGES_ANSWER)} == {
            "boss@example.org", "helper@example.org", "admin@example.org"}

    def test_a_row_survives_only_while_it_differs(self, client):
        boss = _user("boss@example.org", "superadmin")
        helper = _user("helper@example.org")
        _login(client, boss)
        _set(client, helper, [], {Permission.CARDS_MANAGE})

        _set(client, helper, [], set())

        assert db.session.query(UserPermission).count() == 0


class TestTheSplit:
    @pytest.fixture
    def auditor(self, client):
        user = _user("auditor@example.org", "auditor")
        _login(client, user)
        return user

    def test_an_auditor_reads_the_money_and_credit(self, client, auditor):
        assert client.get("/api/v1/admin/money").status_code == 200
        assert client.get("/api/v1/admin/credit").status_code == 200

    def test_an_auditor_changes_nothing(self, client, auditor):
        settings = send(client, "PUT", "/api/v1/admin/money/settings", {})
        booking = send(client, "POST", f"/api/v1/admin/credit/{auditor.id}/cash", {"amount": 500})

        assert settings.status_code == 403 and booking.status_code == 403

    def test_answering_messages_without_the_emails(self, client):
        boss = _user("boss@example.org", "superadmin")
        helper = _user("helper@example.org")
        _login(client, boss)
        _set(client, helper, [], {Permission.MESSAGES_ANSWER})
        helper.email_verified_at = get_now_utc()
        db.session.commit()
        _login(client, helper)

        assert client.get("/api/v1/admin/messages").status_code == 200
        assert "helper@example.org" not in messages.admin_recipients()


class TestEmailsAndCountsFollowThePerson:
    def test_admin_notifications_follow_exceptions(self, client):
        from aeronautics_members.notification_service import NotificationService

        boss = _user("boss@example.org", "superadmin")
        helper = _user("helper@example.org")
        admin = _user("admin@example.org", "admin")
        _login(client, boss)
        _set(client, helper, [], {Permission.NOTIFICATIONS_RECEIVE})
        _set(client, admin, ["admin"], ROLE_PERMISSIONS["admin"] - {Permission.NOTIFICATIONS_RECEIVE})
        for user in (boss, helper, admin):
            user.email_verified_at = get_now_utc()
        db.session.commit()

        assert NotificationService().get_admin_recipient_emails() == ["boss@example.org", "helper@example.org"]

    def test_who_can_install_an_update_counts_an_added_permission(self, client):
        boss = _user("boss@example.org", "superadmin")
        admin = _user("admin@example.org", "admin")
        _login(client, boss)
        _set(client, admin, ["admin"], ROLE_PERMISSIONS["admin"] | {Permission.SYSTEM_UPDATE})

        assert app_module.count_users_with_permission(Permission.SYSTEM_UPDATE) == 2


class TestThePageAsShown:
    @pytest.mark.parametrize("role", sorted(ROLE_PERMISSIONS))
    def test_saving_it_unchanged_changes_nothing(self, client, role):
        # What the Access tab sends back: the roles held, and the permissions of each row's level.
        boss = _user("boss@example.org", "superadmin")
        holder = _user("holder@example.org", role)
        _login(client, boss)
        access = client.get(f"/api/v1/admin/accounts/{holder.id}").get_json()["access"]
        roles = [option["slug"] for option in access["options"] if option["held"]]
        permissions = {permission for row in access["rows"] if row["level"]
                       for permission in row["levels"][row["level"] - 1]["permissions"]}

        saved = _set(client, holder, roles, permissions)

        assert saved.status_code == 200 and saved.get_json()["changed"] is False
        assert holder.permission_exceptions == []
