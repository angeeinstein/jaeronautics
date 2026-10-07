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
    def test_the_top_bar_has_what_it_needs(self, client, app):
        """The top bar (frontend/src/frame/TopBar.tsx) always offers My Account;
        for a staff account without a membership it has no name to show, only the email."""
        staff = _staff("staff@example.org", "admin")
        _login(client, staff.id)

        me = client.get("/api/v1/me").get_json()

        assert (me["first_name"], me["email"], me["admin_area"]) == (None, "staff@example.org", True)

    def test_it_shows_the_account_instead_of_the_payment_form(self, client, app):
        staff = _staff("staff2@example.org", "admin")
        _login(client, staff.id)

        account = client.get("/api/v1/account").get_json()

        # The page says it has no membership, with the way to start one
        # (frontend/src/pages/account/Account.tsx) -- not the membership form itself.
        assert account["email"]["address"] == "staff2@example.org"
        assert account["member"] is None

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

        account = client.get("/api/v1/account").get_json()

        assert account["member"]["contact"]["email_private"] == "member@example.org"


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

        attention = client.get("/api/v1/admin/dashboard").get_json()["attention"]

        assert attention["health_problems"] == ["1 email(s) could not be delivered."]

    def test_not_shown_to_somebody_who_cannot_open_maintenance(self, client, app):
        admin = _staff("plainadmin@example.org", "admin")
        db.session.add(EmailDeliveryJob(email_type="welcome_email", status="exhausted"))
        db.session.commit()
        _login(client, admin.id)

        assert client.get("/api/v1/admin/dashboard").get_json()["attention"]["health_problems"] is None

    def test_a_healthy_system_adds_nothing(self, client, app):
        boss = _staff("fine@example.org", "superadmin")
        _login(client, boss.id)

        assert client.get("/api/v1/admin/dashboard").get_json()["attention"]["health_problems"] == []
