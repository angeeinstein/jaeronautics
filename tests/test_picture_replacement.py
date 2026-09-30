"""Changing an approved profile picture: only when an admin allows it.

A picture, once approved, stays; members are not meant to change it on a
whim. Somebody who wants to asks an admin, who allows one new picture. The
old one -- and the forum access it gives -- stays until the new one is
approved, which uses the permission up and tells the member it is live.
"""
from datetime import datetime, timezone
from io import BytesIO

import pytest

from conftest import db
from aeronautics_members.db_models import ForumAccount, ForumAvatarSubmission, Member
from aeronautics_members.forum_service import ForumService
from test_admin_reviews import _login, _staff
from test_member_journey import _paid_member, forum  # noqa: F401


def _png():
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (64, 64), (10, 120, 200)).save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


@pytest.mark.usefixtures("forum")
class TestTheMembersSide:
    def test_without_permission_there_is_no_upload(self, app, client):
        _paid_member(client, verified=True)

        body = client.get("/account", follow_redirects=True).get_data(as_text=True)

        assert "data-avatar-upload-root" not in body

    def test_a_direct_upload_without_permission_is_refused(self, app, client, monkeypatch):
        member = _paid_member(client, verified=True)

        response = client.post("/forum/avatar", data={"avatar": (_png(), "new.png")},
                               content_type="multipart/form-data", follow_redirects=True)

        assert "please ask an admin" in response.get_data(as_text=True)
        assert db.session.query(ForumAvatarSubmission).filter_by(member_id=member.id).count() == 0

    def test_once_allowed_the_upload_is_offered(self, app, client):
        member = _paid_member(client, verified=True)
        member.avatar_replacement_allowed_at = datetime(2026, 10, 1)
        db.session.commit()

        body = client.get("/account", follow_redirects=True).get_data(as_text=True)

        assert "data-avatar-upload-root" in body
        assert "Upload your new picture." in body
        assert "Open Forum" in body, "their access is untouched meanwhile"


class TestTheAdminsSide:
    def test_an_admin_allows_and_withdraws_it(self, app, client):
        member = _paid_member(client, verified=True)
        admin = _staff("mod@example.org", "admin")
        _login(client, admin.id)

        client.post(f"/admin/accounts/{member.user_id}/picture-replacement", data={"allow": "1"})
        assert db.session.get(Member, member.id).avatar_replacement_allowed_at is not None

        client.post(f"/admin/accounts/{member.user_id}/picture-replacement", data={"allow": "0"})
        assert db.session.get(Member, member.id).avatar_replacement_allowed_at is None


class RecordingProvider:
    """The forum, answering the calls an approval makes and noting the states."""

    def __init__(self):
        self.states = []

    def sync_user(self, forum_account, user, member, desired_state, **kwargs):
        self.states.append(desired_state)
        return {"id": 7}

    def set_avatar(self, forum_account, user, submission):
        return True

    def build_avatar_url(self, submission):
        return None

    def __getattr__(self, name):
        return lambda *a, **k: None


def _with_pictures(tmp_path, client):
    member = _paid_member(client, verified=True)
    old = tmp_path / "old.png"
    new = tmp_path / "new.png"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    db.session.add(ForumAccount(user=member.user, member=member, external_id=str(member.user.id), state="active"))
    db.session.add(ForumAvatarSubmission(user_id=member.user.id, member_id=member.id, status="approved",
                                         storage_path=str(old), content_type="image/png",
                                         uploaded_at=datetime(2026, 9, 1, tzinfo=timezone.utc)))
    pending = ForumAvatarSubmission(user_id=member.user.id, member_id=member.id, status="pending",
                                    storage_path=str(new), content_type="image/png",
                                    uploaded_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    db.session.add(pending)
    member.avatar_replacement_allowed_at = datetime(2026, 9, 30)
    db.session.commit()
    return member, pending


def test_a_replacement_never_takes_them_out_of_the_members_group(app, client, tmp_path, monkeypatch):
    """The approval used to sync everybody as a newcomer before uploading --
    which, for somebody replacing a picture, meant leaving the members' group,
    and staying out if the upload then failed."""
    member, pending = _with_pictures(tmp_path, client)
    service = ForumService({"forum_integration_enabled": "True"})
    provider = RecordingProvider()
    service.provider = provider
    monkeypatch.setattr(service, "is_ready", lambda: True)

    service.approve_avatar_submission(pending)

    assert provider.states and all(state == "active" for state in provider.states)


def test_approving_the_new_picture_uses_the_permission_up_and_says_so(app, client, tmp_path, monkeypatch):
    from aeronautics_members.blueprints import admin as admin_module

    member, pending = _with_pictures(tmp_path, client)
    service = ForumService({"forum_integration_enabled": "True"})
    service.provider = RecordingProvider()
    monkeypatch.setattr(service, "is_ready", lambda: True)
    monkeypatch.setattr(admin_module, "get_forum_service", lambda: service)
    told = []
    monkeypatch.setattr(admin_module, "queue_user_status_notification",
                        lambda event_type, *a, **k: told.append(event_type))
    _login(client, _staff("mod2@example.org", "admin").id)

    client.post(f"/admin/reviews/pictures/{pending.id}/approve")

    assert db.session.get(Member, member.id).avatar_replacement_allowed_at is None
    assert told == ["forum_avatar_replaced"]
    assert db.session.get(ForumAvatarSubmission, pending.id).status == "approved"


def test_a_rejected_new_picture_keeps_the_permission(app, client, tmp_path, monkeypatch):
    """The old picture stays, and they may try again."""
    from aeronautics_members.blueprints import admin as admin_module

    member, pending = _with_pictures(tmp_path, client)
    service = ForumService({"forum_integration_enabled": "True"})
    service.provider = RecordingProvider()
    monkeypatch.setattr(service, "is_ready", lambda: True)
    monkeypatch.setattr(admin_module, "get_forum_service", lambda: service)
    _login(client, _staff("mod3@example.org", "admin").id)

    client.post(f"/admin/reviews/pictures/{pending.id}/reject", data={"review_note": "Blurry"})

    assert db.session.get(Member, member.id).avatar_replacement_allowed_at is not None
