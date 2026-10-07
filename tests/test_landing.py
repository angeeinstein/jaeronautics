"""The front door: a landing page for visitors, the form at /join, and neither
for somebody already signed in. Both are the app's pages
(frontend/src/pages/public/Landing.tsx, Join.tsx)."""
import pytest

from conftest import app_module, db, make_member
from aeronautics_members.db_models import User


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)


@pytest.mark.parametrize("path", ["/", "/join"])
def test_a_visitor_gets_the_app(client, path):
    response = client.get(path)

    assert response.status_code == 200
    assert '<div id="root">' in response.get_data(as_text=True)


def test_the_form_has_what_it_needs_without_an_account(client):
    """The choices and the texts to accept are there for somebody not signed in."""
    options = client.get("/api/v1/forms/options")
    texts = client.get("/api/v1/legal")

    assert options.status_code == 200 and options.get_json()["member_categories"]
    assert texts.status_code == 200


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
