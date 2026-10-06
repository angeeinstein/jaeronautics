"""Who moderates the forum is decided here, and taking the role away takes it.

Discourse has two global staff flags, admin and moderator, and Discourse
Connect sets both from the payload. With forum_manage_staff_flags on, every
sync sends both -- "true" or "false", never only the true one -- from two
portal permissions: Super Admin carries FORUM_ADMIN, the Forum moderator role
carries FORUM_MODERATOR, and so do Admin and Super Admin: somebody trusted to
administer the members here is trusted to keep order on the forum (decided
2026-09-26).
"""
import pytest

from api_helpers import send
from conftest import db, make_member

from aeronautics_members import app as app_module
from aeronautics_members.forum_service import DiscourseConnectProvider
from aeronautics_members.permissions import (
    ROLE_LABELS,
    ROLE_PERMISSIONS,
    Permission,
)


def _payload(*roles, manage=True):
    member = make_member(email=f"{'-'.join(roles) or 'plain'}@example.com")
    for slug in roles:
        member.user.grant_role(app_module.get_role(slug))
    db.session.commit()
    provider = DiscourseConnectProvider(settings={
        "forum_member_group": "members",
        "forum_onboarding_group": "members-onboarding",
        "forum_inactive_group": "membership-inactive",
        "forum_staff_group": "committee",
        "forum_manage_staff_flags": "True" if manage else "False",
        "discourse_connect_secret": "s",
    })
    return provider.build_sso_payload(
        member.user, member, desired_state="active", nonce="n1"
    )


class TestTheFlagsFollowTheRoles:
    def test_a_super_admin_is_an_admin_on_the_forum(self, app):
        payload = _payload("superadmin")
        assert payload["admin"] == "true"

    def test_the_forum_moderator_role_is_a_moderator_there(self, app):
        payload = _payload("forum_moderator")
        assert payload["moderator"] == "true"
        assert payload["admin"] == "false"

    def test_a_portal_admin_is_a_forum_moderator(self, app):
        """Trusted with the members here, trusted with order there."""
        payload = _payload("admin")
        assert payload["moderator"] == "true"
        assert payload["admin"] == "false", "not the forum's settings and keys"

    def test_a_super_admin_is_both(self, app):
        payload = _payload("superadmin")
        assert payload["moderator"] == "true"

    def test_an_ordinary_member_is_told_false_for_both(self, app):
        """Only ever granting is how a flag outlives the role behind it."""
        payload = _payload()
        assert payload["admin"] == "false"
        assert payload["moderator"] == "false"


    def test_with_the_setting_off_nothing_is_said_about_either(self, app):
        """Off leaves whatever the forum has alone -- the default, on purpose."""
        payload = _payload("superadmin", manage=False)
        assert "admin" not in payload
        assert "moderator" not in payload


class TestTheForumModeratorRole:
    def test_it_exists_and_has_a_name_people_can_read(self, app):
        assert "forum_moderator" in ROLE_PERMISSIONS
        assert ROLE_LABELS["forum_moderator"][0] == "Forum moderator"

    def test_it_carries_nothing_in_this_portal(self, app):
        """Keeping a lecture category tidy needs no view of who has paid."""
        assert ROLE_PERMISSIONS["forum_moderator"] == frozenset(
            {Permission.FORUM_MODERATOR}
        )

    def test_its_holder_is_kept_out_of_the_admin_workspace(self, app, client):
        member = make_member(email="mod.only@example.com")
        member.user.grant_role(app_module.get_role("forum_moderator"))
        db.session.commit()
        with client.session_transaction() as session:
            session["_user_id"] = str(member.user.id)
            session["_fresh"] = True

        assert client.get("/admin").status_code == 302

    def test_admin_already_includes_it(self, app):
        """Ticking both is one account, so only Admin is stored."""
        from aeronautics_members.permissions import minimal_roles

        assert minimal_roles({"admin", "forum_moderator"}) == {"admin"}
        assert minimal_roles({"superadmin", "forum_moderator"}) == {"superadmin"}


class TestSavingRolesReachesTheForum:
    """Found while planning this: a role change waited for an unrelated sync.

    The nightly drift check compares membership state, not roles, so a role
    taken away here would have stayed on the forum indefinitely.
    """

    def test_saving_roles_syncs_the_account(self, app, client, monkeypatch):
        boss = make_member(email="boss@example.com")
        boss.user.grant_role(app_module.get_role("superadmin"))
        target = make_member(email="becomes.mod@example.com")
        db.session.commit()
        synced = []
        monkeypatch.setattr(
            "aeronautics_members.services.account_admin.sync_member_forum_state",
            lambda member, **kwargs: synced.append(member.id) or (None, None),
        )
        with client.session_transaction() as session:
            session["_user_id"] = str(boss.user.id)
            session["_fresh"] = True

        send(client, "PUT", f"/api/v1/admin/accounts/{target.user.id}/roles", {"roles": ["forum_moderator"]})

        assert synced == [target.id]

    def test_a_forum_that_cannot_be_reached_does_not_undo_the_roles(
            self, app, client, monkeypatch):
        boss = make_member(email="boss2@example.com")
        boss.user.grant_role(app_module.get_role("superadmin"))
        target = make_member(email="still.saved@example.com")
        db.session.commit()

        def unreachable(member, **kwargs):
            raise RuntimeError("forum down")

        monkeypatch.setattr(
            "aeronautics_members.services.account_admin.sync_member_forum_state",
            unreachable,
        )
        with client.session_transaction() as session:
            session["_user_id"] = str(boss.user.id)
            session["_fresh"] = True

        send(client, "PUT", f"/api/v1/admin/accounts/{target.user.id}/roles", {"roles": ["forum_moderator"]})

        db.session.expire_all()
        assert target.user.can(Permission.FORUM_MODERATOR)


class TestTheSwitch:
    @pytest.fixture
    def admin_client(self, app, client):
        from aeronautics_members.db_models import User

        admin = User(email="admin-staff-flags@example.com")
        admin.set_password("x")
        admin.grant_role(app_module.get_role("superadmin"))
        db.session.add(admin)
        db.session.commit()
        with client.session_transaction() as session:
            session["_user_id"] = str(admin.id)
        return client

    def _save(self, client, **extra):
        form = {
            "save_settings": "1",
            "settings_section": "forum",
            "forum_integration_enabled": "y",
            "forum_provider": "discourse",
            "forum_auth_strategy": "discourse_connect",
            "forum_base_url": "https://forum.example.org",
            "discourse_api_username": "system",
            "forum_member_group": "members",
            "forum_onboarding_group": "members-onboarding",
            "forum_inactive_group": "membership-inactive",
            "forum_lecture_groups": "students",
            "forum_onboarding_path": "/",
            "forum_avatar_max_bytes": "5242880",
            "forum_avatar_allowed_types": "jpg,png",
            **extra,
        }
        response = client.post("/admin/settings", data=form, follow_redirects=True)
        assert response.status_code == 200

    def _stored(self):
        from aeronautics_members.services.forum import get_forum_settings_map

        return get_forum_settings_map().get("forum_manage_staff_flags")

    def test_it_is_off_until_somebody_turns_it_on(self, app):
        from aeronautics_members.services.forum import get_forum_service

        assert get_forum_service().settings["forum_manage_staff_flags"] is False

    def test_ticking_it_is_kept(self, app, admin_client):
        self._save(admin_client, forum_manage_staff_flags="on")
        assert self._stored() == "True"

    def test_unticking_it_is_kept_too(self, app, admin_client):
        self._save(admin_client, forum_manage_staff_flags="on")
        self._save(admin_client)
        assert self._stored() == "False"

    def test_it_travels_with_the_settings_file(self, app):
        """A rebuild must not quietly turn it off, or on."""
        from aeronautics_members.services.portal_settings import SECTIONS

        assert "forum_manage_staff_flags" in SECTIONS["forum"]
