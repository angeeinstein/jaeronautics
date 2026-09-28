"""My Account for everybody signed in, and the account shortcuts gone from admin.

A staff account without a membership had no My Account link, and My Account
itself forwarded to the membership form that ends at the payment page. The
only way such an account could change its password was a card in the admin
settings -- which is why that card and a "Create Membership Profile" button on
every admin page existed. Both are gone; My Account shows the part of an
account there is.
"""
from conftest import app_module, db, make_member
from aeronautics_members.db_models import EmailDeliveryJob, User


def _staff(email, role):
    user = User(email=email)
    user.set_password("old-password-1")
    db.session.add(user)
    user.grant_role(app_module.get_role(role))
    db.session.commit()
    return user


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)


class TestMyAccountWithoutAMembership:
    def test_the_link_is_in_the_top_bar(self, client, app):
        staff = _staff("staff@example.org", "admin")
        _login(client, staff.id)

        body = client.get("/admin").get_data(as_text=True)

        assert ">My Account</a>" in body

    def test_it_shows_the_account_instead_of_the_payment_form(self, client, app):
        staff = _staff("staff2@example.org", "admin")
        _login(client, staff.id)

        response = client.get("/account", follow_redirects=True)
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "staff2@example.org" in body
        assert "This account has no membership." in body
        assert "/change-password" in body
        assert "/account/create-membership" in body
        # Not the membership form itself.
        assert 'name="first_name"' not in body

    def test_a_password_change_comes_back_to_my_account(self, client, app):
        staff = _staff("staff3@example.org", "admin")
        _login(client, staff.id)

        response = client.post("/change-password", data={
            "current_password": "old-password-1",
            "new_password": "new-password-22",
            "confirm_new_password": "new-password-22",
        })

        assert response.status_code == 302
        assert response.headers["Location"].startswith("/account")

    def test_members_still_get_their_full_page(self, client, app):
        member = make_member(email="member@example.org")
        _login(client, member.user.id)

        body = client.get("/account", follow_redirects=True).get_data(as_text=True)

        assert "This account has no membership." not in body


class TestTheAdminPagesNoLongerCarryAccountShortcuts:
    def test_no_create_membership_button_on_admin_pages(self, client, app):
        staff = _staff("boss@example.org", "superadmin")
        _login(client, staff.id)

        for path in ("/admin", "/admin/accounts", "/admin/reviews", "/admin/logs", "/admin/settings", f"/admin/accounts/{staff.id}"):
            body = client.get(path).get_data(as_text=True)
            assert "Create Membership Profile" not in body, path
            assert "/account/create-membership" not in body, path

    def test_no_account_tools_in_settings(self, client, app):
        staff = _staff("boss2@example.org", "superadmin")
        _login(client, staff.id)

        body = client.get("/admin/settings").get_data(as_text=True)

        assert "Account Tools" not in body


class TestHealthOnTheDashboard:
    def test_a_problem_is_one_line_pointing_at_the_health_report(self, client, app):
        boss = _staff("health@example.org", "superadmin")
        db.session.add(EmailDeliveryJob(email_type="welcome_email", status="exhausted"))
        db.session.commit()
        _login(client, boss.id)

        body = client.get("/admin").get_data(as_text=True)

        assert "System health problem" in body
        assert "1 email(s) could not be delivered." in body
        assert "/admin/settings#settings-maintenance" in body
        # Not the list with Retry buttons itself.
        assert "/admin/undelivered-emails/" not in body

    def test_not_shown_to_somebody_who_cannot_open_maintenance(self, client, app):
        admin = _staff("plainadmin@example.org", "admin")
        db.session.add(EmailDeliveryJob(email_type="welcome_email", status="exhausted"))
        db.session.commit()
        _login(client, admin.id)

        body = client.get("/admin").get_data(as_text=True)

        assert "System health problem" not in body

    def test_a_healthy_system_adds_nothing(self, client, app):
        boss = _staff("fine@example.org", "superadmin")
        _login(client, boss.id)

        body = client.get("/admin").get_data(as_text=True)

        assert "System health problem" not in body
        assert "Nothing needs your attention" in body
