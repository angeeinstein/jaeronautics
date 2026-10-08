"""Two people deciding the same thing at the same moment.

Every decision checked the state first, but two requests arriving together
both read it before either saved, and both went ahead: a photo approved and
rejected at once with the member emailed both, a change request whose new
name went into the profile while the request ended up rejected, two
superadmins each taking the other's rights. The rows are now locked while
a decision is made; the second request waits, reads what the first decided,
and stops.

SQLite has no row locks, so what can be tested here is the other half: that
the locked read sees what was committed meanwhile, and that each of these
actions takes the lock.
"""
import pytest
from sqlalchemy.dialects import mysql

from api_helpers import send
from conftest import db, make_member
from aeronautics_members.services import account as account_module
from aeronautics_members.db_models import ForumAvatarSubmission, MemberProfileChangeRequest, NotificationEvent
from aeronautics_members.services import account_admin, locking
from test_admin_reviews import _login, _name_change, _picture, _staff, quiet_forum  # noqa: F401


def _committed_elsewhere(model, row_id, **values):
    """What another admin's request committed while this one was waiting."""
    with db.engine.begin() as connection:
        connection.execute(db.update(model).where(model.id == row_id).values(**values))


def _emails(event_type):
    return db.session.query(NotificationEvent).filter_by(event_type=event_type).count()


def test_the_lock_really_is_a_lock_on_the_real_database():
    statement = locking.locked(db.select(ForumAvatarSubmission).where(ForumAvatarSubmission.id == 1))

    assert "FOR UPDATE" in str(statement.compile(dialect=mysql.dialect()))


@pytest.mark.usefixtures("quiet_forum")
def test_a_picture_rejected_meanwhile_is_not_approved_as_well(app, client):
    member = make_member(email="photo@example.com")
    picture = _picture(member)
    assert picture.status == "pending"  # this request's view, loaded before the other decided
    _committed_elsewhere(ForumAvatarSubmission, picture.id, status="rejected")
    _login(client, _staff("second@example.org", "admin").id)

    response = send(client, "POST", f"/api/v1/admin/reviews/pictures/{picture.id}/approve", {})

    assert response.status_code == 409
    assert "no longer waiting for review" in response.get_json()["error"]["message"]
    assert db.session.get(ForumAvatarSubmission, picture.id).status == "rejected"
    assert _emails("forum_avatar_approved") == 0, "the member is not told both"


@pytest.mark.usefixtures("quiet_forum")
def test_a_picture_approved_meanwhile_is_not_rejected_as_well(app, client):
    member = make_member(email="photo2@example.com")
    picture = _picture(member)
    assert picture.status == "pending"
    _committed_elsewhere(ForumAvatarSubmission, picture.id, status="approved")
    _login(client, _staff("second2@example.org", "admin").id)

    send(client, "POST", f"/api/v1/admin/reviews/pictures/{picture.id}/reject", {})

    assert db.session.get(ForumAvatarSubmission, picture.id).status == "approved"
    assert _emails("forum_avatar_rejected") == 0


@pytest.mark.usefixtures("quiet_forum")
def test_a_change_request_rejected_meanwhile_does_not_change_the_profile(app, client):
    member = make_member(email="rename@example.com", last_name="Before")
    change = _name_change(member, last_name="After")
    assert change.status == "pending"
    _committed_elsewhere(MemberProfileChangeRequest, change.id, status="rejected")
    _login(client, _staff("second3@example.org", "admin").id)

    send(client, "POST", f"/api/v1/admin/reviews/name-changes/{change.id}/approve", {})

    db.session.expire_all()
    assert member.last_name == "Before"
    assert db.session.get(MemberProfileChangeRequest, change.id).status == "rejected"


def test_a_member_cannot_cancel_what_was_decided_meanwhile(app, client):
    member = make_member(email="cancel@example.com")
    change = _name_change(member)
    assert change.status == "pending"
    _committed_elsewhere(MemberProfileChangeRequest, change.id, status="approved")
    _login(client, member.user.id)

    response = client.delete(f"/api/v1/account/change-request/{change.id}")

    assert response.status_code == 409

    assert db.session.get(MemberProfileChangeRequest, change.id).status == "approved"


def test_changes_to_who_administers_the_site_take_the_lock(app, client, monkeypatch):
    """Roles, switching an account off, erasure: all behind the one lock, so
    two of them cannot both find another admin left and leave none."""
    taken = []
    monkeypatch.setattr(account_admin, "lock_administration", lambda: taken.append("admin"))
    monkeypatch.setattr(account_module, "lock_administration", lambda: taken.append("account"))
    boss = _staff("boss2@example.org", "superadmin")
    target = make_member(email="target@example.com")
    _login(client, boss.id)

    url = f"/api/v1/admin/accounts/{target.user.id}"
    send(client, "PUT", f"{url}/roles", {"roles": ["admin"]})
    send(client, "PUT", f"{url}/disabled", {"disabled": True, "reason": "x"})
    send(client, "POST", f"{url}/erase", {"confirm_email": "wrong"})

    assert taken == ["admin", "admin", "admin"]
