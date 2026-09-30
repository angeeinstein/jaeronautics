"""Erasing somebody from the old forum erases what the old forum knew too.

Found by the pre-deployment audit: erasure scrubbed the account and the
membership but left the imported profile alone -- the old address, the name,
the year group -- and the avatar stayed reachable at its public address. A
later run of the import would also have written it all back from the export.
"""
import os
from datetime import datetime

import pytest

from conftest import db, make_member, privacy
from aeronautics_members.db_models import ImportedForumProfile
from aeronautics_members.services import forum_profiles
from aeronautics_members.services.forum_import import import_forum_people
from test_forum_account_claim import OLD_EMAIL, _archived


@pytest.fixture(autouse=True)
def no_remote_calls(monkeypatch):
    anonymised = []

    def record(user):
        anonymised.append(user.forum_account.external_id if user.forum_account else None)
        return True, False

    monkeypatch.setattr(privacy, "anonymise_forum_account", record)
    monkeypatch.setattr(privacy, "cancel_member_subscription", lambda member, reason=None: False)
    return anonymised


@pytest.fixture
def archived_with_avatar(app, tmp_path):
    profile = _archived()
    avatar = tmp_path / "imported-avatar.png"
    avatar.write_bytes(b"\x89PNG not really")
    profile.avatar_path = str(avatar)
    profile.avatar_public_token = "a" * 32
    profile.display_name = "Ana Popovic"
    db.session.commit()
    return profile


def _profile(profile_id):
    return db.session.get(ImportedForumProfile, profile_id)


def test_the_imported_profile_is_blanked_and_the_avatar_deleted(archived_with_avatar):
    profile = archived_with_avatar
    avatar_path = profile.avatar_path

    summary = privacy.erase_account(profile.user)
    db.session.commit()

    erased = _profile(profile.id)
    assert erased.source_email is None
    assert erased.display_name == privacy.ERASED_TEXT
    assert erased.source_username == privacy.ERASED_TEXT
    assert erased.year_group is None
    assert erased.avatar_path is None and erased.avatar_public_token is None
    assert not os.path.exists(avatar_path)
    assert summary["avatar_files_deleted"] == 1
    # Kept, and nothing personal: the old forum's own key.
    assert erased.source_user_id == "645"


def test_the_public_avatar_address_stops_answering(archived_with_avatar, client):
    profile = archived_with_avatar
    url = f"/forum/avatar/imported/{profile.avatar_public_token}"
    assert client.get(url).status_code == 200

    privacy.erase_account(profile.user)
    db.session.commit()

    assert client.get(url).status_code == 404


def test_running_the_import_again_does_not_bring_them_back(archived_with_avatar):
    profile = archived_with_avatar
    privacy.erase_account(profile.user)
    db.session.commit()

    import_forum_people([{
        "source_user_id": "645", "source_username": "PopovicA_L23",
        "source_email": OLD_EMAIL, "year_group": "LAV23",
    }])
    db.session.commit()

    again = _profile(profile.id)
    assert again.source_email is None
    assert again.display_name == privacy.ERASED_TEXT
    assert db.session.query(ImportedForumProfile).count() == 1


def test_they_are_not_published_to_the_forum_again(archived_with_avatar):
    profile = archived_with_avatar
    other = _archived(email="other@edu.fh-joanneum.at", username="OtherO_L20", uid="700")

    privacy.erase_account(profile.user)
    db.session.commit()

    assert [p.id for p in forum_profiles.profiles_to_publish()] == [other.id]


def test_a_published_archive_is_anonymised_on_the_forum(archived_with_avatar, no_remote_calls):
    """Nobody signs in as an archived person, so no forum account row existed
    for erasure to go by -- their name and face would have stayed there."""
    profile = archived_with_avatar
    profile.forum_synced_at = datetime(2026, 9, 1)
    db.session.commit()

    privacy.erase_account(profile.user)
    db.session.commit()

    assert no_remote_calls == [str(profile.user_id)]


def test_an_unpublished_archive_asks_nothing_of_the_forum(archived_with_avatar, no_remote_calls):
    privacy.erase_account(archived_with_avatar.user)
    db.session.commit()

    assert no_remote_calls == []


def test_a_returning_student_who_reclaimed_the_account_is_erased_whole(app):
    member = make_member(email="returning@example.com", first_name="Ana", last_name="Popovic")
    profile = ImportedForumProfile(
        user=member.user, source_user_id="812", source_username="PopovicA_L15",
        source_email="a.popovic@edu.fh-joanneum.at", display_name="Ana Popovic",
        year_group="LAV15", claimed_at=datetime(2026, 9, 20),
    )
    db.session.add(profile)
    db.session.commit()

    privacy.erase_account(member.user)
    db.session.commit()

    erased = _profile(profile.id)
    assert erased.source_email is None
    assert erased.display_name == privacy.ERASED_TEXT
