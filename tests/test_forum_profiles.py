"""Publishing the old forum's people so they can still be found.

The old board is the association's register of everyone who was ever a member,
and people use it to look somebody up from an earlier cohort. Recreating that
means every imported person gets a profile -- not only the 242 who posted --
with their name, their year group and the face they had.

What matters here is mostly what must NOT happen: these are not accounts, they
must not be named after a placeholder address, and they must not cause 740
activation emails to a domain that cannot resolve.
"""
import pytest

from conftest import db
from aeronautics_members.db_models import ImportedForumProfile, User
from aeronautics_members.services.forum_import import import_forum_people
from aeronautics_members.services.forum_profiles import (
    ARCHIVE_GROUP,
    build_profile_payload,
    group_name_for_year_group,
    publish_imported_profiles,
)


def _imported(uid="645", username="PopovicA_L23", year_group="LAV23", **extra):
    import_forum_people([{
        "source_user_id": uid,
        "source_username": username,
        "source_email": "a.popovic@edu.fh-joanneum.at",
        "year_group": year_group,
        "post_count": 7,
        **extra,
    }])
    db.session.commit()
    return db.session.execute(
        db.select(ImportedForumProfile).filter_by(source_user_id=uid)
    ).scalar_one()


class FakeProvider:
    """Records what would have gone to Discourse."""

    def __init__(self, fail_on=()):
        self.sent = []
        self.fail_on = set(fail_on)

    def sync_imported_profile(self, payload):
        from aeronautics_members.forum_service import ForumProviderError
        if payload["username"] in self.fail_on:
            raise ForumProviderError("Discourse said no")
        self.sent.append(payload)
        return {"ok": True}


class TestWhatTheForumIsTold:
    def test_they_are_named_by_their_display_name(self, app):
        """Not by their email, which is a placeholder that cannot resolve.

        The member payload falls back to the address when there is no
        membership, which would call every one of these profiles
        forum-mybb-645@imported.invalid.
        """
        profile = _imported()

        payload = build_profile_payload(profile)

        assert payload["name"] == profile.display_name
        assert "imported.invalid" not in payload["name"]

    def test_no_activation_email_is_asked_for(self, app):
        """740 of them would go to a domain reserved for never resolving."""
        profile = _imported()

        assert build_profile_payload(profile)["require_activation"] == "false"

    def test_they_are_keyed_on_the_portal_account(self, app):
        """The same external_id their posts will be attached to."""
        profile = _imported()

        assert build_profile_payload(profile)["external_id"] == str(profile.user_id)

    def test_they_land_in_their_cohort_and_the_archive(self, app):
        profile = _imported(year_group="LAV23")

        groups = build_profile_payload(profile)["add_groups"].split(",")

        assert ARCHIVE_GROUP in groups
        assert "lav23" in groups

    def test_somebody_with_no_year_group_still_gets_published(self, app):
        """Five people have none. They belong in the register regardless."""
        profile = _imported(year_group=None, username="dpilz")

        groups = build_profile_payload(profile)["add_groups"].split(",")

        assert groups == [ARCHIVE_GROUP]

    def test_the_year_group_is_only_sent_when_there_is_a_field_for_it(self, app):
        profile = _imported()

        without = build_profile_payload(profile)
        with_field = build_profile_payload(profile, year_group_field="user_field_3")

        assert not any(key.startswith("custom.") for key in without)
        assert with_field["custom.user_field_3"] == "LAV23"


class TestGroupNames:
    @pytest.mark.parametrize("year_group,expected", [
        ("LAV23", "lav23"),
        ("MAV17", "mav17"),
        ("ATM17", "atm17"),
        ("  LAV23  ", "lav23"),
        ("LAV 23", "lav_23"),
        ("", None),
        (None, None),
    ])
    def test_it_makes_a_usable_group_name(self, year_group, expected):
        assert group_name_for_year_group(year_group) == expected


class TestPublishing:
    def test_everyone_is_published_not_only_the_posters(self, app):
        """498 of the 740 never wrote a word and still belong in the register."""
        _imported(uid="1", username="A_L23")
        _imported(uid="2", username="B_L19", post_count=0)
        provider = FakeProvider()

        report = publish_imported_profiles(provider)
        db.session.commit()

        assert report["published"] == 2
        assert {payload["username"] for payload in provider.sent} == {"A_L23", "B_L19"}

    def test_a_rehearsal_sends_nothing(self, app):
        _imported()
        provider = FakeProvider()

        report = publish_imported_profiles(provider, dry_run=True)

        assert provider.sent == []
        assert report["published"] == 1, "but it still reports what it would do"

    def test_a_rehearsal_leaves_no_token_behind(self, app, tmp_path):
        """The rollback undoes the database; a written token would outlive it."""
        profile = _imported()
        profile.avatar_path = str(tmp_path / "face.jpg")
        db.session.commit()

        publish_imported_profiles(FakeProvider(), dry_run=True)

        assert profile.avatar_public_token is None

    def test_one_refusal_does_not_stop_the_rest(self, app):
        """740 calls; one of them will fail and the other 739 still matter."""
        _imported(uid="1", username="A_L23")
        _imported(uid="2", username="B_L19")
        provider = FakeProvider(fail_on=["A_L23"])

        report = publish_imported_profiles(provider)
        db.session.commit()

        assert report["failed"] == 1
        assert report["published"] == 1
        assert any("A_L23" in problem for problem in report["problems"])

    def test_a_second_run_can_skip_what_already_went(self, app):
        """One API call per person, so an interrupted run must be resumable."""
        _imported(uid="1", username="A_L23")
        _imported(uid="2", username="B_L19")
        publish_imported_profiles(FakeProvider(fail_on=["B_L19"]))
        db.session.commit()

        resumed = FakeProvider()
        publish_imported_profiles(resumed, only_unsynced=True)
        db.session.commit()

        assert [payload["username"] for payload in resumed.sent] == ["B_L19"]

    def test_a_failure_is_not_recorded_as_published(self, app):
        _imported(uid="1", username="A_L23")

        publish_imported_profiles(FakeProvider(fail_on=["A_L23"]))
        db.session.commit()

        profile = db.session.execute(db.select(ImportedForumProfile)).scalar_one()
        assert profile.forum_synced_at is None, "or the retry would skip it"


class TestTheAvatarTheForumFetches:
    def test_the_url_is_public_and_token_guarded(self, app, tmp_path):
        """Discourse fetches it itself, unauthenticated, from its own server.

        And the staging directory also holds avatars awaiting review, so the
        route must not take a filename.
        """
        profile = _imported()
        profile.avatar_path = str(tmp_path / "face.jpg")
        db.session.commit()
        provider = FakeProvider()

        publish_imported_profiles(provider)
        db.session.commit()

        avatar_url = provider.sent[0]["avatar_url"]
        assert profile.avatar_public_token
        assert profile.avatar_public_token in avatar_url
        assert avatar_url.startswith("http"), "Discourse fetches it, so it must be absolute"
        assert len(profile.avatar_public_token) >= 20, "short enough to guess is no guard"

    def test_each_profile_gets_its_own_token(self, app, tmp_path):
        """A token shared or derived from the row id would guard nothing."""
        first = _imported(uid="1", username="A_L23")
        second = _imported(uid="2", username="B_L19")
        for profile in (first, second):
            profile.avatar_path = str(tmp_path / "face.jpg")
        db.session.commit()

        publish_imported_profiles(FakeProvider())
        db.session.commit()

        assert first.avatar_public_token != second.avatar_public_token

    def test_the_route_refuses_an_unknown_token(self, app, client):
        _imported()

        assert client.get("/forum/avatar/imported/not-a-real-token").status_code == 404

    def test_the_route_serves_the_file_for_a_real_token(self, app, client, tmp_path):
        from PIL import Image

        profile = _imported()
        face = tmp_path / "face.jpg"
        Image.new("RGB", (32, 32), (10, 20, 30)).save(face)
        profile.avatar_path = str(face)
        db.session.commit()
        publish_imported_profiles(FakeProvider())
        db.session.commit()

        response = client.get(f"/forum/avatar/imported/{profile.avatar_public_token}")

        assert response.status_code == 200
        assert response.data[:2] == b"\xff\xd8", "a JPEG"

    def test_it_is_served_without_signing_in(self, app, client, tmp_path):
        """The admin-only route cannot serve Discourse; that is why this exists."""
        from PIL import Image

        profile = _imported()
        face = tmp_path / "face.jpg"
        Image.new("RGB", (8, 8), (1, 2, 3)).save(face)
        profile.avatar_path = str(face)
        db.session.commit()
        publish_imported_profiles(FakeProvider())
        db.session.commit()

        # No session at all on this client.
        assert client.get(
            f"/forum/avatar/imported/{profile.avatar_public_token}"
        ).status_code == 200


class TestMakingTheGroups:
    """Discourse answers 404 for a group that does not exist yet.

    Which is the ordinary case on a first run, for all thirty-four of them.
    Treating it as a failure meant the very first publish created no groups at
    all and reported thirty-four errors, while claiming success.
    """

    def _provider(self, monkeypatch, *, lookup_fails=True):
        from aeronautics_members.forum_service import (
            DiscourseConnectProvider,
            ForumProviderError,
        )

        provider = DiscourseConnectProvider({
            "forum_base_url": "http://forum.test",
            "discourse_api_key": "k",
            "discourse_api_username": "system",
            "discourse_connect_secret": "s",
        })
        calls = []

        def fake_request(method, path, data=None, json_body=None):
            calls.append((method, path))
            if method == "GET" and path.startswith("/groups/"):
                if lookup_fails:
                    raise ForumProviderError("Discourse API request failed (404)")
                return {"group": {"id": 7, "name": "lav23"}}
            return {"basic_group": {"id": 9, "name": "lav23"}}

        monkeypatch.setattr(provider, "_request", fake_request)
        return provider, calls

    def test_a_missing_group_is_created_not_reported_as_broken(self, app, monkeypatch):
        provider, calls = self._provider(monkeypatch, lookup_fails=True)

        group, created = provider.ensure_group("lav23")

        assert created is True
        assert group["name"] == "lav23"
        assert ("POST", "/admin/groups.json") in calls

    def test_an_existing_group_is_left_alone(self, app, monkeypatch):
        """Thirty-four groups on the first run, none of them again after."""
        provider, calls = self._provider(monkeypatch, lookup_fails=False)

        group, created = provider.ensure_group("lav23")

        assert created is False
        assert group["id"] == 7
        assert ("POST", "/admin/groups.json") not in calls


class TestFindingTheUserFieldEndpoint:
    """Discourse has moved this admin route between versions.

    And an admin route answers 404 rather than 403 when the API user is not
    staff, so one guessed path cannot tell a wrong URL from wrong credentials.
    Guessing one and dying on it meant a 404 aborted the whole publish before a
    single profile went across -- which is what happened on the first real run.
    """

    def _provider(self, monkeypatch, working_path=None):
        from aeronautics_members.forum_service import (
            DiscourseConnectProvider,
            ForumProviderError,
        )

        provider = DiscourseConnectProvider({
            "forum_base_url": "http://forum.test",
            "discourse_api_key": "k",
            "discourse_api_username": "system",
            "discourse_connect_secret": "s",
        })
        tried = []

        def fake_request(method, path, data=None, json_body=None):
            tried.append((method, path))
            if method == "GET":
                if path == working_path:
                    return {"user_fields": [{"id": 4, "name": "Year group"}]}
                raise ForumProviderError("Discourse API request failed (404)")
            return {"user_field": {"id": 9, "name": "Year group"}}

        monkeypatch.setattr(provider, "_request", fake_request)
        return provider, tried

    @pytest.mark.parametrize("working_path", [
        "/admin/customize/user_fields.json",
        "/admin/config/user_fields.json",
        "/admin/user_fields.json",
    ])
    def test_it_finds_whichever_path_this_version_answers_on(
        self, app, monkeypatch, working_path
    ):
        provider, _tried = self._provider(monkeypatch, working_path)

        assert provider.find_user_field("Year group") == "user_field_4"

    def test_it_creates_on_the_path_that_answered(self, app, monkeypatch):
        """Not on the first candidate, which may be the one that 404s."""
        provider, tried = self._provider(
            monkeypatch, "/admin/config/user_fields.json"
        )

        field, created = provider.ensure_user_field("Something else")

        assert created is True
        assert ("POST", "/admin/config/user_fields.json") in tried

    def test_when_no_path_works_it_says_what_to_check(self, app, monkeypatch):
        """404 on every admin route usually means the API user is not staff."""
        from aeronautics_members.forum_service import ForumProviderError

        provider, _tried = self._provider(monkeypatch, working_path=None)

        with pytest.raises(ForumProviderError) as raised:
            provider.find_user_field("Year group")

        assert "discourse_api_username" in str(raised.value)


class TestTheTokenItself:
    def test_it_survives_being_pasted_into_a_shell(self, app, tmp_path):
        """token_urlsafe can begin with "-", which curl then reads as a flag.

        Which is exactly how the first real run was misdiagnosed: the avatar
        route was fine and the manual check 404'd because the paste had lost
        the leading hyphen.
        """
        profile = _imported()
        profile.avatar_path = str(tmp_path / "face.jpg")
        db.session.commit()

        publish_imported_profiles(FakeProvider())
        db.session.commit()

        token = profile.avatar_public_token
        assert not token.startswith("-")
        assert token.isalnum(), "no characters that a shell or a URL parser argues about"


class TestTheAvatarNeedsASecondCall:
    """Discourse ignores avatar_url on the call that creates the account.

    Found on the real forum: twenty profiles published in one pass all showed
    letter avatars, and the one re-sent afterwards came back with its
    photograph. The account has to exist before the picture will stick.
    """

    def test_somebody_with_an_avatar_is_sent_twice(self, app, tmp_path):
        profile = _imported()
        profile.avatar_path = str(tmp_path / "face.jpg")
        db.session.commit()
        provider = FakeProvider()

        publish_imported_profiles(provider)
        db.session.commit()

        assert len(provider.sent) == 2, "create, then update to carry the avatar"
        assert provider.sent[0]["username"] == provider.sent[1]["username"]
        assert all(payload.get("avatar_url") for payload in provider.sent)

    def test_somebody_without_one_is_sent_once(self, app):
        """Sixty-three of them have no picture; a second call would buy nothing."""
        _imported()
        provider = FakeProvider()

        publish_imported_profiles(provider)
        db.session.commit()

        assert len(provider.sent) == 1

    def test_the_second_call_carries_a_fresh_nonce(self, app, tmp_path):
        """A nonce is meant to be used once, even where it is not checked."""
        profile = _imported()
        profile.avatar_path = str(tmp_path / "face.jpg")
        db.session.commit()
        provider = FakeProvider()

        publish_imported_profiles(provider)
        db.session.commit()

        assert provider.sent[0]["nonce"] != provider.sent[1]["nonce"]

    def test_a_rehearsal_still_sends_neither(self, app, tmp_path):
        profile = _imported()
        profile.avatar_path = str(tmp_path / "face.jpg")
        db.session.commit()
        provider = FakeProvider()

        publish_imported_profiles(provider, dry_run=True)

        assert provider.sent == []
