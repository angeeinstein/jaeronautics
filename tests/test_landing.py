"""The front door: a landing page for visitors, the form at /join, and neither
for somebody already signed in."""
from conftest import app_module, db, make_member
from aeronautics_members.db_models import User


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)


def test_a_visitor_gets_the_landing_page_not_the_form(client):
    body = client.get("/").get_data(as_text=True)

    assert "Your membership, in one place" in body
    assert 'href="/join"' in body
    assert 'href="/login"' in body
    assert 'name="first_name"' not in body


def test_the_form_is_at_join(client):
    body = client.get("/join").get_data(as_text=True)

    assert 'action="/process-membership"' in body
    assert 'name="first_name"' in body


def test_a_signed_in_member_is_sent_to_their_account(client, app):
    member = make_member(email="home@example.com")
    _login(client, member.user.id)

    assert client.get("/").headers["Location"].startswith("/account")
    assert client.get("/join").headers["Location"].startswith("/account")


def test_staff_land_where_logging_in_would_take_them(client, app):
    staff = User(email="staff@example.org")
    staff.set_password("x")
    db.session.add(staff)
    staff.grant_role(app_module.get_role("admin"))
    db.session.commit()
    _login(client, staff.id)

    assert client.get("/").headers["Location"] == "/admin"


def test_the_login_page_is_the_apps(client):
    """It points new people at the form (frontend/src/pages/public/SignIn.tsx)."""
    assert '<div id="root">' in client.get("/login").get_data(as_text=True)
