"""Smoke tests that key pages actually render (no 500 / BuildError / NameError).

These guard the blueprint structure: a missing url_for rename, an un-migrated
helper, or a bad import surfaces here as a 500 even when imports succeed. Static
checks can't catch template BuildErrors or handler-body NameErrors on code paths
they don't execute; rendering the page does.
"""

from datetime import date, datetime

import pytest

from conftest import app_module, db, make_member
from aeronautics_members.db_models import User


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)


@pytest.fixture
def admin_user(app):
    user = User(email="admin@example.com")
    user.set_password("x")
    user.grant_role(app_module.get_role("admin"))
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def active_member(app):
    member = make_member(email="active@example.com", payment_status="paid", is_active=True)
    member.membership_ends_on = date(date.today().year, 12, 31)
    member.email_verified_at = datetime.utcnow()
    member.user.email_verified_at = member.email_verified_at
    db.session.commit()
    return member


PUBLIC_PAGES = ["/", "/__health", "/login", "/legal", "/forgot-password", "/thank-you", "/cancel"]
ADMIN_PAGES = ["/admin", "/admin/accounts", "/admin/settings", "/admin/approvals", "/admin/logs", "/admin/forum"]


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_public_pages_render(client, path):
    resp = client.get(path)
    assert resp.status_code < 500, f"{path} returned {resp.status_code}"


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_admin_pages_render(client, admin_user, path):
    _login(client, admin_user.id)
    resp = client.get(path)
    assert resp.status_code < 500, f"{path} returned {resp.status_code}"


def test_admin_account_detail_renders(client, admin_user, active_member):
    _login(client, admin_user.id)
    resp = client.get(f"/admin/accounts/{active_member.user_id}")
    assert resp.status_code < 500


def test_account_and_forum_render(client, active_member):
    _login(client, active_member.user_id)
    for path in ["/account?rt=1", "/forum"]:
        resp = client.get(path)
        assert resp.status_code < 500, f"{path} returned {resp.status_code}"
