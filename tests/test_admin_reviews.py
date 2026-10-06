"""The Reviews page, the dashboard's attention list, and the self-applying filters.

Change requests and profile pictures used to live on two pages; they are one
queue now. What must not be lost in the merge is that each item is decided on
its own: a member who changed their name and uploaded a picture the same
evening can have one approved and the other turned down.
"""
import re
import types
from datetime import datetime, timedelta, timezone

import pytest

from conftest import app_module, db, make_member
from aeronautics_members.db_models import (
    ForumAccount,
    ForumAvatarSubmission,
    MemberProfileChangeRequest,
    User,
)
from aeronautics_members.member_categories import MemberCategory
from aeronautics_members.permissions import Permission, ROLE_PERMISSIONS
from aeronautics_members.services import reviews
from api_helpers import send

API = "/api/v1/admin/reviews"


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)


def _staff(email, role):
    user = User(email=email)
    user.set_password("x")
    db.session.add(user)
    user.grant_role(app_module.get_role(role))
    db.session.commit()
    return user


def _name_change(member, last_name="Photograph", **fields):
    record = MemberProfileChangeRequest(
        member=member,
        requested_by=member.user,
        requested_salutation=fields.get("salutation", member.salutation),
        requested_first_name=fields.get("first_name", member.first_name),
        requested_last_name=last_name,
        requested_member_category=fields.get("member_category", member.member_category),
        requested_year_group=fields.get("year_group", member.year_group),
        member_note=fields.get("member_note"),
        status="pending",
    )
    db.session.add(record)
    db.session.commit()
    return record


def _picture(member, token=None, uploaded_at=None):
    submission = ForumAvatarSubmission(
        user_id=member.user.id,
        member_id=member.id,
        status="pending",
        public_token=token or f"token-{member.id}",
        uploaded_at=uploaded_at or datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.session.add(submission)
    db.session.commit()
    return submission


@pytest.fixture
def admin(app):
    return _staff("boss@example.org", "admin")


@pytest.fixture
def quiet_forum(monkeypatch):
    """Approving a picture talks to the forum; this answers for it."""
    from aeronautics_members.services import forum as forum_module

    class FakeForum:
        def get_current_approved_submission(self, member):
            return None

        def get_reclaimed_avatar(self, member):
            return None

        def approve_avatar_submission(self, submission, reviewer=None, review_note=None):
            submission.status = "approved"
            submission.reviewed_by = reviewer
            submission.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            return types.SimpleNamespace(error=None, desired_state="onboarding", changed=True, forum_account=None)

        def reject_avatar_submission(self, submission, reviewer=None, review_note=None):
            submission.status = "rejected"
            submission.reviewed_by = reviewer
            submission.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            return types.SimpleNamespace(error=None)

    monkeypatch.setattr(forum_module, "get_forum_service", lambda: FakeForum())
    monkeypatch.setattr(forum_module, "sync_member_forum_state", lambda member: (None, None))


def _queue(client):
    response = client.get(API)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _ids(body, kind):
    return [item["id"] for item in body["queue"] if item["kind"] == kind]


class TestOneQueueSeparateDecisions:
    def test_both_kinds_are_listed_each_on_its_own(self, client, admin):
        member = make_member(email="both@example.com")
        change = _name_change(member)
        picture = _picture(member)
        _login(client, admin.id)

        body = _queue(client)

        assert _ids(body, "name_change") == [change.id]
        assert _ids(body, "picture") == [picture.id]
        assert body["waiting"] == {"name_changes": 1, "pictures": 1, "sync_problems": 0}

    def test_the_picture_can_be_approved_and_the_name_change_rejected(self, client, admin, quiet_forum):
        member = make_member(email="split@example.com")
        change = _name_change(member)
        picture = _picture(member)
        _login(client, admin.id)

        assert send(client, "POST", f"{API}/pictures/{picture.id}/approve", {"note": ""}).status_code == 200
        assert send(client, "POST", f"{API}/name-changes/{change.id}/reject",
                    {"note": "Please use your legal name."}).status_code == 204

        db.session.refresh(picture)
        db.session.refresh(change)
        db.session.refresh(member)
        assert picture.status == "approved"
        assert change.status == "rejected"
        assert member.last_name == "Member"

    def test_deciding_one_leaves_the_other_waiting(self, client, admin, quiet_forum):
        member = make_member(email="one@example.com")
        change = _name_change(member)
        picture = _picture(member)
        _login(client, admin.id)

        send(client, "POST", f"{API}/pictures/{picture.id}/reject", {"note": "Blurry"})

        body = _queue(client)
        assert _ids(body, "name_change") == [change.id]
        assert _ids(body, "picture") == []

    def test_oldest_comes_first_whatever_its_kind(self, app, admin):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        early = make_member(email="early@example.com")
        late = make_member(email="late@example.com")
        picture = _picture(early, uploaded_at=now - timedelta(days=2))
        change = _name_change(late)
        change.created_at = now - timedelta(days=1)
        db.session.commit()

        queue = reviews.review_queue(admin)

        assert [item.record for item in queue] == [picture, change]

    def test_only_what_changed_is_shown(self, client, admin):
        member = make_member(email="typo@example.com")
        _name_change(member, last_name="Membr", member_note="Typo")
        _login(client, admin.id)

        [item] = _queue(client)["queue"]

        assert [change["label"] for change in item["change"]["changes"]] == ["Last name"]
        assert item["change"]["is_name_change"] is True
        assert item["change"]["member_note"] == "Typo"

    def test_a_change_without_a_new_name_is_called_a_details_change(self, app):
        member = make_member(email="alum@example.com", member_category=MemberCategory.STUDENT)
        record = _name_change(member, last_name=member.last_name, member_category=MemberCategory.ALUMNI)

        changes = reviews.describe_changes(record)

        assert [field for field, *_ in changes] == ["member_category"]
        assert reviews.is_name_change(changes) is False


class TestEmptySectionsStayAway:
    def test_nothing_waiting(self, client, admin):
        _login(client, admin.id)

        body = _queue(client)

        assert body["queue"] == [] and body["sync_problems"] == []
        assert client.get(f"{API}/history").get_json()["total"] == 0

    def test_sync_problems_appear_when_there_are_some(self, client, admin):
        member = make_member(email="stuck@example.com")
        db.session.add(ForumAccount(user=member.user, member=member, provider="discourse",
                                    external_id="1", state="sync_error", last_error="The forum could not be reached."))
        db.session.commit()
        _login(client, admin.id)

        [problem] = _queue(client)["sync_problems"]

        assert problem["error"] == "The forum could not be reached."
        assert problem["user_id"] == member.user_id

    def test_history_holds_both_kinds(self, client, admin, quiet_forum):
        member = make_member(email="past@example.com")
        change = _name_change(member)
        picture = _picture(member)
        _login(client, admin.id)
        send(client, "POST", f"{API}/name-changes/{change.id}/approve", {})
        send(client, "POST", f"{API}/pictures/{picture.id}/reject", {})

        history = client.get(f"{API}/history").get_json()

        assert history["total"] == 2
        assert {(item["kind"], item["decision"]) for item in history["items"]} == {
            ("name_change", "approved"), ("picture", "rejected")}
        assert {item["by"] for item in history["items"]} == {"boss@example.org"}

    def test_history_pages_do_not_overlap(self, app, admin):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for index in range(5):
            member = make_member(email=f"h{index}@example.com")
            change = _name_change(member)
            change.status = "approved"
            change.reviewed_at = now - timedelta(hours=index * 2)
            picture = _picture(member)
            picture.status = "rejected"
            picture.reviewed_at = now - timedelta(hours=index * 2 + 1)
        db.session.commit()

        pages = [reviews.review_history(admin, page=n, per_page=4) for n in (1, 2, 3)]
        seen = [item.record for page in pages for item in page.items]

        assert pages[0].pages == 3
        assert len(seen) == 10
        assert len({id(record) for record in seen}) == 10
        whens = [item.when for page in pages for item in page.items]
        assert whens == sorted(whens, reverse=True)


class TestWhoSeesWhat:
    @pytest.fixture
    def picture_only_role(self, app, monkeypatch):
        monkeypatch.setitem(
            ROLE_PERMISSIONS, "picture_checker",
            frozenset({Permission.ADMIN_ACCESS, Permission.FORUM_MODERATE}),
        )
        app_module.seed_default_roles()
        db.session.commit()

    def test_a_role_without_approvals_sees_only_pictures(self, client, picture_only_role):
        checker = _staff("pics@example.org", "picture_checker")
        member = make_member(email="mixed@example.com")
        change = _name_change(member)
        picture = _picture(member)
        _login(client, checker.id)

        body = _queue(client)

        assert _ids(body, "picture") == [picture.id]
        assert _ids(body, "name_change") == [] and change.id
        assert body["waiting"]["name_changes"] is None, "not a kind they decide"
        assert send(client, "POST", f"{API}/name-changes/{change.id}/approve", {}).status_code == 403

    def test_somebody_who_reviews_nothing_is_sent_back(self, client, monkeypatch, app):
        monkeypatch.setitem(ROLE_PERMISSIONS, "viewer", frozenset({Permission.ADMIN_ACCESS}))
        app_module.seed_default_roles()
        db.session.commit()
        viewer = _staff("viewer@example.org", "viewer")
        _login(client, viewer.id)

        response = client.get("/admin/reviews")

        assert response.status_code == 302
        assert client.get(API).status_code == 403


class TestTheCountOnTheTab:
    """On the pages still drawn by Flask -- by now only Settings -> Maintenance,
    which needs system.update; the new front end's sidebar has its own count."""

    def test_it_shows_what_is_waiting(self, client):
        member = make_member(email="count@example.com")
        _name_change(member)
        _picture(member)
        _login(client, _staff("boss@example.org", "superadmin").id)

        body = client.get("/admin/settings").get_data(as_text=True)

        assert re.search(r'Reviews<span class="nav-count"[^>]*>2</span>', body)

    def test_it_is_absent_when_nothing_waits(self, client):
        _login(client, _staff("boss@example.org", "superadmin").id)

        response = client.get("/admin/settings")

        assert response.status_code == 200
        assert 'class="nav-count"' not in response.get_data(as_text=True)
