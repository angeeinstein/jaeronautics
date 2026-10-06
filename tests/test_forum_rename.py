"""An approved new forum username has to reach the forum, and stay.

Found on the Azure forum: an administrator approved a name change and ticked
"also change the forum username", and nothing changed -- not on the forum, and
not even in the portal. The sync that carries every other change leaves the
forum's username alone on purpose (returning students keep the name their old
posts are under), and the portal then followed the forum's answer, which was
the old name, straight back.

A rename is now asked for as one, through Discourse's own rename. Posts stay
with the account, which is what they belong to, and Discourse rewrites
@mentions and quotes of the old name itself.
"""
from datetime import datetime

import pytest

from api_helpers import send
from conftest import db, make_member
from aeronautics_members.services import forum as forum_module
from aeronautics_members.db_models import (
    ExternalWorkItem,
    ForumAccount,
    ImportedForumProfile,
    NotificationEvent,
)
from aeronautics_members.forum_service import (
    ForumProviderError,
    ForumService,
    _record_the_name_the_forum_gave,
)
from aeronautics_members.services import outbox, workflows
from test_admin_reviews import _login, _name_change, _staff

OLD = "HuberA_L25"
NEW = "MaierA_L25"


class FakeDiscourse:
    """Answers the two calls a rename makes, as Discourse would."""

    def __init__(self, name=OLD):
        self.name = name
        self.renames = []
        self.down = False
        self.refuse = False
        self.external_id = None  # whose account this is; set by the fixture

    def get_remote_user_by_external_id(self, external_id):
        if self.down:
            raise ForumProviderError("GET /u/by-external failed: connection refused")
        if external_id != self.external_id:
            raise ForumProviderError(f"GET /u/by-external/{external_id}.json failed (404): not found")
        return {"id": 7, "username": self.name}

    def change_username(self, current, new):
        if self.down:
            raise ForumProviderError("PUT username failed: connection refused")
        if self.refuse:
            raise ForumProviderError("PUT username failed (422): Username must be unique")
        self.renames.append((current, new))
        self.name = new
        return new


@pytest.fixture
def forum(app, monkeypatch):
    discourse = FakeDiscourse()
    service = ForumService({"forum_integration_enabled": "True"})
    service.provider = discourse
    monkeypatch.setattr(service, "is_ready", lambda: True)
    monkeypatch.setattr(workflows, "get_forum_service", lambda: service)

    def the_sync_that_undid_it(member):
        # What the approval's sync did: ask the forum, and follow its answer.
        _record_the_name_the_forum_gave(member.user, {"id": 7, "username": discourse.name})
        return None, None

    monkeypatch.setattr(forum_module, "sync_member_forum_state", the_sync_that_undid_it)
    return discourse


@pytest.fixture
def member(app, forum):
    member = make_member(email="huber@example.com", first_name="Anna", last_name="Huber",
                         year_group="LAV25")
    member.user.forum_username = OLD
    db.session.add(ForumAccount(user=member.user, member=member, external_id=str(member.user.id),
                                state="active"))
    db.session.commit()
    forum.external_id = str(member.user.id)
    return member


def _approve(client, member, *, rename=True, wanted=NEW):
    admin = _staff("boss@example.org", "admin")
    change = _name_change(member, last_name="Maier")
    _login(client, admin.id)
    body = {"forum_username": wanted} if rename else {}
    return send(client, "POST", f"/api/v1/admin/reviews/name-changes/{change.id}/approve", body)


def _rename_items():
    return db.session.query(ExternalWorkItem).filter_by(kind=ExternalWorkItem.KIND_FORUM_RENAME).all()


def test_the_new_name_reaches_the_forum_and_the_portal_keeps_it(client, forum, member):
    _approve(client, member)

    assert forum.renames == [(OLD, NEW)]
    assert db.session.get(type(member.user), member.user.id).forum_username == NEW


def test_a_forum_that_is_down_is_tried_again_and_the_name_is_not_taken_back(client, forum, member):
    forum.down = True

    response = _approve(client, member)

    assert response.get_json()["rename_pending"] is True, "the page says it is tried again automatically"
    user = db.session.get(type(member.user), member.user.id)
    assert user.forum_username == NEW, "not followed back to the old one meanwhile"
    item = _rename_items()[0]
    assert item.status == ExternalWorkItem.STATUS_PENDING

    forum.down = False
    item.not_before = None
    db.session.commit()
    outbox.process_pending(kinds=[ExternalWorkItem.KIND_FORUM_RENAME])

    assert forum.renames == [(OLD, NEW)]


def test_a_name_the_forum_refuses_goes_back_to_the_old_one_and_is_reported(client, forum, member):
    """Taken on the forum by an account the portal does not know -- an archived one."""
    forum.refuse = True

    _approve(client, member)

    user = db.session.get(type(member.user), member.user.id)
    assert user.forum_username == OLD, "the portal must not show a name the forum does not have"
    assert _rename_items()[0].status == ExternalWorkItem.STATUS_COMPLETED, "not retried for ever"
    assert db.session.query(NotificationEvent).filter_by(event_type="forum_rename_refused").count() == 1


def test_somebody_not_on_the_forum_yet_simply_arrives_under_the_new_name(client, forum, member):
    db.session.delete(member.user.forum_account)
    db.session.commit()

    _approve(client, member)

    assert forum.renames == []
    assert db.session.get(type(member.user), member.user.id).forum_username == NEW


def test_a_returning_student_on_their_reclaimed_account_is_renamed_there(client, forum, member):
    """Found on the Azure forum: the reclaim worked, the later rename did not.

    After a reclaim the membership sits on the archived row, and its forum
    account is the old one with the history -- which is the one renamed.
    """
    db.session.add(ImportedForumProfile(
        user=member.user, source_user_id="412", source_username=OLD, display_name="Anna Huber",
        year_group="LAV15", claimed_at=datetime(2026, 9, 20),
    ))
    db.session.commit()

    _approve(client, member)

    assert forum.renames == [(OLD, NEW)]
    assert db.session.get(type(member.user), member.user.id).forum_username == NEW


def test_without_the_tick_nothing_is_renamed(client, forum, member):
    _approve(client, member, rename=False)

    assert forum.renames == []
    assert _rename_items() == []
    assert db.session.get(type(member.user), member.user.id).forum_username == OLD
