"""Switching an account off, separately from the membership.

Two states that answer different questions and are allowed to disagree. A
member paid up for the year can be barred from signing in; an account in good
standing can sit here with no membership yet, or a lapsed one. Before this the
only way to stop somebody was to erase them, which is irreversible and
destroys the record you would want to keep if you were suspending rather than
deleting.

So the property these tests really protect is the independence. Anything that
made disabling touch the membership would mean suspending a person by
cancelling what they had paid for.
"""
from datetime import date, datetime

import pytest

from conftest import app_module, db, make_member
from aeronautics_members.db_models import User
from aeronautics_members.services import ConflictError
from aeronautics_members.services.access import (
    describe_account_disable,
    set_account_disabled,
)


@pytest.fixture
def admin(app):
    user = User(email="admin@example.com")
    user.set_password("x")
    user.grant_role(app_module.get_role("admin"))
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def second_admin(app):
    user = User(email="admin2@example.com")
    user.set_password("x")
    user.grant_role(app_module.get_role("superadmin"))
    db.session.add(user)
    db.session.commit()
    return user


def _paid_member(email="paid@example.com"):
    member = make_member(email=email, payment_status="paid", is_active=True)
    member.membership_ends_on = date(date.today().year, 12, 31)
    member.user.email_verified_at = datetime.utcnow()
    db.session.commit()
    return member


class TestTheTwoStatesAreIndependent:
    def test_disabling_leaves_a_paid_membership_alone(self, app, admin):
        """Barring somebody is not a refund."""
        member = _paid_member()

        set_account_disabled(member.user, disable=True, actor_user=admin, reason="Conduct")
        db.session.commit()

        assert member.user.is_disabled is True
        assert member.is_active is True
        assert member.payment_status == "paid"
        assert member.membership_ends_on is not None

    def test_an_active_account_can_have_no_membership_at_all(self, app):
        user = User(email="nomember@example.com")
        user.set_password("x")
        db.session.add(user)
        db.session.commit()

        assert user.account_status == "active"
        assert user.member is None

    def test_a_lapsed_membership_does_not_disable_the_account(self, app):
        member = make_member(email="lapsed@example.com", payment_status="canceled",
                             is_active=False)

        assert member.is_active is False
        assert member.user.is_disabled is False
        assert member.user.account_status == "active"

    def test_reactivating_restores_access_without_touching_billing(self, app, admin):
        member = _paid_member()
        set_account_disabled(member.user, disable=True, actor_user=admin)
        db.session.commit()

        set_account_disabled(member.user, disable=False, actor_user=admin)
        db.session.commit()

        assert member.user.is_disabled is False
        assert member.user.disabled_reason is None
        assert member.is_active is True


class TestWhatGetsRecorded:
    def test_the_reason_and_who_did_it_are_kept(self, app, admin):
        """"Disabled, and nobody remembers why" is the thing to avoid."""
        member = _paid_member()

        set_account_disabled(member.user, disable=True, actor_user=admin,
                             reason="Repeated abuse in the forum")
        db.session.commit()

        assert member.user.disabled_reason == "Repeated abuse in the forum"
        assert member.user.disabled_by_user_id == admin.id
        assert member.user.disabled_at is not None

    def test_an_empty_reason_is_stored_as_none(self, app, admin):
        member = _paid_member()

        set_account_disabled(member.user, disable=True, actor_user=admin, reason="   ")
        db.session.commit()

        assert member.user.disabled_reason is None

    def test_reactivating_clears_it_rather_than_leaving_a_stale_reason(self, app, admin):
        member = _paid_member()
        set_account_disabled(member.user, disable=True, actor_user=admin, reason="Conduct")
        db.session.commit()

        set_account_disabled(member.user, disable=False, actor_user=admin)
        db.session.commit()

        assert member.user.disabled_reason is None
        assert member.user.disabled_by_user_id is None


class TestTheGuards:
    def test_you_cannot_switch_off_your_own_account(self, app, admin, second_admin):
        """The confirmation dialog is not the place to discover this."""
        with pytest.raises(ConflictError):
            set_account_disabled(admin, disable=True, actor_user=admin)

    def test_the_last_account_that_can_administer_is_protected(self, app, admin):
        """Disabling removes their access as surely as taking the role away."""
        other = User(email="other@example.com")
        other.set_password("x")
        db.session.add(other)
        db.session.commit()

        with pytest.raises(ConflictError):
            set_account_disabled(admin, disable=True, actor_user=other)

    def test_another_admin_means_one_can_be_switched_off(self, app, admin, second_admin):
        set_account_disabled(admin, disable=True, actor_user=second_admin)
        db.session.commit()

        assert admin.is_disabled is True

    def test_a_disabled_admin_does_not_count_as_cover(self, app, admin, second_admin):
        """Otherwise the last working one could be switched off after them."""
        set_account_disabled(admin, disable=True, actor_user=second_admin)
        db.session.commit()

        with pytest.raises(ConflictError):
            set_account_disabled(second_admin, disable=True, actor_user=admin)

    def test_an_erased_account_cannot_be_switched_off(self, app, admin):
        member = _paid_member()
        member.user.deleted_at = datetime.utcnow()
        db.session.commit()

        outcome = describe_account_disable(member.user, actor_user=admin, disable=True)

        assert outcome["blockers"]
        assert outcome["changed"] is False

    def test_disabling_twice_is_a_no_op(self, app, admin):
        member = _paid_member()
        set_account_disabled(member.user, disable=True, actor_user=admin)
        db.session.commit()
        first = member.user.disabled_at

        outcome = set_account_disabled(member.user, disable=True, actor_user=admin)

        assert outcome["changed"] is False
        assert member.user.disabled_at == first, "the original decision keeps its date"


class TestWhatADisabledAccountCanDo:
    def test_the_password_still_matches_but_the_login_is_refused(self, app, client, admin):
        """Told apart from a wrong password, which sends people to a reset."""
        member = _paid_member()
        member.user.set_password("the-password")
        db.session.commit()
        set_account_disabled(member.user, disable=True, actor_user=admin)
        db.session.commit()

        assert member.user.check_password("the-password") is True

        response = client.post("/login", data={
            "email": member.email_private, "password": "the-password",
        }, follow_redirects=True)
        body = response.get_data(as_text=True)

        assert "deactivated" in body.lower()
        assert "Invalid email or password" not in body

    def test_an_open_session_ends_at_the_next_request(self, app, client, admin):
        """An account is disabled because somebody should stop using it now."""
        member = _paid_member()
        with client.session_transaction() as session:
            session["_user_id"] = str(member.user_id)

        assert client.get("/account").status_code < 400

        set_account_disabled(member.user, disable=True, actor_user=admin)
        db.session.commit()

        assert client.get("/account").status_code in (302, 401)

    def test_the_forum_syncs_them_out_even_though_they_paid(self, app, admin):
        """Discourse holds its own groups, so barring here means nothing there."""
        from aeronautics_members.forum_service import ForumService, FORUM_STATE_INACTIVE
        from aeronautics_members.services.forum import get_forum_settings_map

        member = _paid_member()
        service = ForumService(get_forum_settings_map())
        assert service.get_desired_state(member) != FORUM_STATE_INACTIVE

        set_account_disabled(member.user, disable=True, actor_user=admin)
        db.session.commit()

        assert service.get_desired_state(member) == FORUM_STATE_INACTIVE

    def test_the_forum_page_says_the_account_not_the_membership(self, app, admin):
        """"Your membership is not active" would be untrue; they have paid."""
        from aeronautics_members.app import build_forum_context

        from aeronautics_members.db_models import Setting

        # With the integration off, "the forum is not set up" is the honest
        # answer and comes first. Turn it on so the account gate is what shows.
        for key, value in (
            ("forum_integration_enabled", "True"),
            ("forum_base_url", "https://forum.example"),
            ("discourse_connect_secret", "x" * 16),
        ):
            db.session.merge(Setting(key=key, value=value))
        member = _paid_member()
        set_account_disabled(member.user, disable=True, actor_user=admin)
        db.session.commit()

        # A request context: the builder makes URLs for the forum links.
        with app.test_request_context("/account"):
            context = build_forum_context(member)

        assert context["status_key"] == "account_disabled"
        assert context["can_enter_forum"] is False


class TestTheAccountStatusLabel:
    def test_an_ordinary_account(self, app):
        member = _paid_member()

        assert member.user.account_status == "active"
        assert member.user.can_sign_in is True

    def test_a_disabled_one(self, app, admin):
        member = _paid_member()
        set_account_disabled(member.user, disable=True, actor_user=admin)

        assert member.user.account_status == "disabled"
        assert member.user.can_sign_in is False

    def test_an_erased_one_outranks_disabled(self, app, admin):
        """Erasure is final; showing "deactivated" would suggest it is undoable."""
        member = _paid_member()
        set_account_disabled(member.user, disable=True, actor_user=admin)
        member.user.deleted_at = datetime.utcnow()

        assert member.user.account_status == "erased"

    def test_an_imported_person_reads_as_archived_not_disabled(self, app):
        """Never activated is not the same as switched off."""
        from aeronautics_members.db_models import ImportedForumProfile
        from aeronautics_members.services.forum_import import import_forum_people

        import_forum_people([{"source_user_id": "1", "source_username": "OneA_L20"}])
        db.session.commit()
        profile = db.session.execute(db.select(ImportedForumProfile)).scalar_one()

        assert profile.user.account_status == "archived"
        assert profile.user.is_disabled is False
        assert profile.user.can_sign_in is False


class TestTheAdminScreens:
    @pytest.fixture
    def admin_client(self, app, client, admin, second_admin):
        with client.session_transaction() as session:
            session["_user_id"] = str(second_admin.id)
        return client

    def _rows(self, response):
        return response.get_data(as_text=True).count('class="btn btn-secondary btn-sm"')

    def test_the_account_page_shows_both_states(self, app, admin_client):
        member = _paid_member()

        body = admin_client.get(f"/admin/accounts/{member.user_id}").get_data(as_text=True)

        assert "Account Status" in body
        assert "Deactivate Account" in body

    def test_deactivating_from_the_page_works(self, app, admin_client):
        member = _paid_member()

        response = admin_client.post(
            f"/admin/accounts/{member.user_id}/disabled",
            data={"disable": "1", "reason": "Conduct"},
            follow_redirects=True,
        )

        assert response.status_code < 400
        db.session.expire_all()
        user = db.session.get(User, member.user_id)
        assert user.is_disabled is True
        assert user.disabled_reason == "Conduct"
        assert user.member.is_active is True, "the membership is untouched"

    def test_the_page_says_why_the_switch_is_missing(self, app, client, admin):
        """"You cannot do this to yourself" leads somewhere different from
        "nobody else could install an update"."""
        with client.session_transaction() as session:
            session["_user_id"] = str(admin.id)

        body = client.get(f"/admin/accounts/{admin.id}").get_data(as_text=True)

        assert "Deactivate Account" not in body
        assert "your own account" in body

    def test_the_list_filters_by_account_state(self, app, admin_client):
        member = _paid_member()
        set_account_disabled(member.user, disable=True, actor_user=None)
        db.session.commit()

        assert self._rows(admin_client.get("/admin/accounts?account=disabled")) == 1
        assert self._rows(admin_client.get("/admin/accounts?account=active")) == 2

    def test_the_list_shows_the_two_states_in_separate_columns(self, app, admin_client):
        member = _paid_member()
        set_account_disabled(member.user, disable=True, actor_user=None)
        db.session.commit()

        body = admin_client.get("/admin/accounts?account=disabled").get_data(as_text=True)

        assert "Deactivated" in body
        assert "Membership Active" in body
