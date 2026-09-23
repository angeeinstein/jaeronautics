"""Whether a thread can be moved, and whether the forum will take it.

Two things are pinned here, both of which cost a real run to discover.

The first is that a thread whose opening post is refused stops. Discourse
would not take a topic called "Klausuren" -- nine characters against a minimum
of fifteen -- and every one of the eleven replies then tried to open a topic of
its own, with no title and no category, so one real failure was reported as
twelve invented ones.

The second is that the forum is asked what it allows before anything is
posted. The defaults are written for somebody typing into a box today; the
archive is a decade of "Klausuren", "Exams" and "Danke!", and finding that out
one refused post at a time, hours into an import that cannot be re-run, is the
expensive way to learn it.
"""
import json

import pytest

from aeronautics_members.forum_service import ForumProviderError
from aeronautics_members.services.forum_content import (
    ContentPoster,
    check_site_settings,
    loosen_site_settings,
    migrate_thread,
    plan_site_settings,
    read_settings_journal,
    rehearsal_threads,
    restore_site_settings,
)


class FakePoster:
    """A Discourse that records what it was asked and can be told to refuse."""

    def __init__(self, refuse_titles_shorter_than=0, settings=None):
        self.refuse_titles_shorter_than = refuse_titles_shorter_than
        self.calls = []
        self.uploads = []
        self._settings = dict(settings or {})
        self._next_topic = 100
        self.base_url = "https://forum.example.at"
        self.settings_written = []
        self.refuse_to_write = set()

    def create_post(self, *, raw, as_username, created_at, category=None,
                    title=None, topic_id=None):
        self.calls.append({
            "raw": raw, "as_username": as_username, "created_at": created_at,
            "category": category, "title": title, "topic_id": topic_id,
        })
        if topic_id is None:
            if title is None or len(title) < self.refuse_titles_shorter_than:
                raise ForumProviderError(
                    'POST /posts.json failed (422): {"errors":["Title is too short"]}'
                )
            self._next_topic += 1
            return {"id": self._next_topic * 10, "topic_id": self._next_topic,
                    "created_at": created_at}
        return {"id": self._next_topic * 10 + len(self.calls),
                "topic_id": topic_id, "created_at": created_at}

    def upload(self, path, as_username, filename=None, content_type=None):
        self.uploads.append((filename, content_type))
        return {"short_url": "upload://abc", "extension": "pdf"}

    def site_settings(self):
        return dict(self._settings)

    def set_site_setting(self, setting, value):
        if setting in self.refuse_to_write:
            raise ForumProviderError(f"PUT /admin/site_settings/{setting}.json failed (403)")
        self.settings_written.append((setting, value))
        self._settings[setting] = value


def a_post(pid, uid="7", dateline="1394119460", message="Hier die Angabe von 2014."):
    return {"pid": pid, "uid": uid, "dateline": dateline, "message": message}


class TestARefusedOpeningPostStopsTheThread:
    """One failure should be reported as one failure."""

    def test_the_replies_are_not_attempted(self, app):
        poster = FakePoster(refuse_titles_shorter_than=15)
        posts = [a_post(str(pid)) for pid in range(1, 13)]

        with app.app_context():
            report = migrate_thread(
                poster, {"tid": "5", "subject": "Klausuren"}, posts,
                {}, {"7": "HoferT_M13"}, "/nowhere", 4,
            )

        assert len(poster.calls) == 1, "only the opening post should have been sent"
        assert report["topic_id"] is None
        assert report["posts"][0]["result"].startswith("failed:")
        assert all(
            person["result"] == "not attempted: the thread has no topic to go in"
            for person in report["posts"][1:]
        )

    def test_it_says_why_the_rest_was_left(self, app):
        poster = FakePoster(refuse_titles_shorter_than=15)

        with app.app_context():
            report = migrate_thread(
                poster, {"tid": "5", "subject": "Exams"}, [a_post("1"), a_post("2")],
                {}, {"7": "HoferT_M13"}, "/nowhere", 4,
            )

        assert any("no topic" in problem for problem in report["problems"])
        assert any("run the thread again" in problem for problem in report["problems"])

    def test_a_failed_reply_does_not_stop_the_thread(self, app):
        """Only the opening post is fatal. A later one is just a gap."""
        poster = FakePoster()
        calls = []

        def create_post(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise ForumProviderError("POST /posts.json failed (422): rate limited")
            return FakePoster.create_post(poster, **kwargs)

        poster.create_post = create_post
        posts = [a_post("1"), a_post("2"), a_post("3")]

        with app.app_context():
            report = migrate_thread(
                poster, {"tid": "5", "subject": "Eine ordentlich lange Frage"}, posts,
                {}, {"7": "HoferT_M13"}, "/nowhere", 4,
            )

        assert len(calls) == 3
        assert report["posts"][1]["result"].startswith("failed:")
        assert report["posts"][2]["result"] == "posted"


class TestWhoeverOpensTheTopic:
    def test_it_is_the_first_post_that_can_be_sent(self, app):
        """Not the first in the list, who may have no account at all."""
        poster = FakePoster()
        posts = [a_post("1", uid="999"), a_post("2", uid="7")]

        with app.app_context():
            report = migrate_thread(
                poster, {"tid": "5", "subject": "Wie lief die Klausur?"}, posts,
                {}, {"7": "HoferT_M13"}, "/nowhere", 4, fallback_username=None,
            )

        assert report["posts"][0]["result"].startswith("skipped")
        assert len(poster.calls) == 1
        assert poster.calls[0]["title"] == "Wie lief die Klausur?"
        assert poster.calls[0]["as_username"] == "HoferT_M13"
        assert report["posts"][1]["result"] == "posted"

    def test_the_replies_go_into_that_topic(self, app):
        poster = FakePoster()
        posts = [a_post("1"), a_post("2"), a_post("3")]

        with app.app_context():
            report = migrate_thread(
                poster, {"tid": "5", "subject": "Zusammenfassung Aerodynamik"}, posts,
                {}, {"7": "HoferT_M13"}, "/nowhere", 4,
            )

        assert [call["topic_id"] for call in poster.calls] == [
            None, report["topic_id"], report["topic_id"]
        ]
        assert report["topic_id"] is not None


class TestWhatTheArchiveNeedsTheForumToAllow:
    """Measured from the archive, not assumed from Discourse's defaults."""

    def test_the_shortest_subject_sets_the_title_minimum(self):
        threads = [
            {"tid": "1", "subject": "Klausuren", "firstpost": "1"},
            {"tid": "2", "subject": "AM", "firstpost": "2"},
        ]
        requirements = {r.setting: r for r in plan_site_settings(threads, [a_post("1")])}

        assert requirements["min_topic_title_length"].needed == 2
        assert requirements["min_topic_title_length"].compare == "at_most"

    def test_repeated_subjects_ask_for_duplicates_to_be_allowed(self):
        """Ninety-one threads on the real board are called "Klausuren"."""
        threads = [
            {"tid": str(n), "subject": "Klausuren", "firstpost": str(n)}
            for n in range(1, 92)
        ]
        requirements = {r.setting: r for r in plan_site_settings(threads, [])}

        assert requirements["allow_duplicate_topic_titles"].needed == "true"
        assert "91 times" in requirements["allow_duplicate_topic_titles"].why

    def test_unique_subjects_do_not_ask_for_it(self):
        threads = [
            {"tid": "1", "subject": "Klausuren Aerodynamik", "firstpost": "1"},
            {"tid": "2", "subject": "Klausuren Flugmechanik", "firstpost": "2"},
        ]
        requirements = {r.setting: r for r in plan_site_settings(threads, [])}

        assert "allow_duplicate_topic_titles" not in requirements

    def test_the_shortest_post_sets_the_post_minimum(self):
        posts = [a_post("1", message="Hier die Angabe."), a_post("2", message="Danke")]
        threads = [{"tid": "1", "subject": "Angabe Klausur", "firstpost": "1"}]
        requirements = {r.setting: r for r in plan_site_settings(threads, posts)}

        assert requirements["min_post_length"].needed == len("Danke")
        # The opening post is measured separately: Discourse has its own
        # minimum for it, and it is not the same setting.
        assert requirements["min_first_post_length"].needed == len("Hier die Angabe.")

    def test_it_measures_the_converted_text(self):
        """[size=4]Danke[/size] is five characters by the time it is posted."""
        posts = [a_post("1", message="[size=4][color=red]Danke[/color][/size]")]
        requirements = {r.setting: r for r in plan_site_settings([], posts)}

        assert requirements["min_post_length"].needed == 5

    def test_an_author_repeating_themselves_asks_for_the_window_to_close(self):
        posts = [a_post("1", uid="7", message="Danke!"), a_post("2", uid="7", message="Danke!")]
        requirements = {r.setting: r for r in plan_site_settings([], posts)}

        assert requirements["unique_posts_mins"].needed == 0

    def test_two_people_saying_the_same_thing_is_not_a_repeat(self):
        posts = [a_post("1", uid="7", message="Danke!"), a_post("2", uid="8", message="Danke!")]
        requirements = {r.setting: r for r in plan_site_settings([], posts)}

        assert "unique_posts_mins" not in requirements

    def test_attachments_set_the_extensions_and_the_size(self):
        attachments = [
            {"pid": "1", "filename": "Angabe.pdf", "filesize": "2048"},
            {"pid": "1", "filename": "Lösung.PDF", "filesize": "1048576"},
            {"pid": "2", "filename": "Formeln.docx", "filesize": "512"},
        ]
        requirements = {
            r.setting: r for r in plan_site_settings([], [a_post("1")], attachments)
        }

        assert requirements["authorized_extensions"].needed == ["docx", "pdf"]
        assert requirements["max_attachment_size_kb"].needed == 1024
        assert requirements["newuser_max_attachments"].needed == 2

    def test_titles_are_kept_as_they_were_written(self):
        threads = [{"tid": "1", "subject": "klausuren!!!", "firstpost": "1"}]
        requirements = {r.setting: r for r in plan_site_settings(threads, [])}

        assert requirements["title_prettify"].needed == "false"


class TestAskingTheForumWhatItAllows:
    def test_a_minimum_that_is_too_high_is_reported(self):
        threads = [{"tid": "1", "subject": "Klausuren", "firstpost": "1"}]
        poster = FakePoster(settings={"min_topic_title_length": "15"})

        rows = {row["setting"]: row for row in check_site_settings(
            poster, plan_site_settings(threads, [])
        )}

        assert rows["min_topic_title_length"]["ok"] is False
        assert rows["min_topic_title_length"]["now"] == "15"

    def test_a_minimum_that_is_already_low_enough_passes(self):
        threads = [{"tid": "1", "subject": "Klausuren", "firstpost": "1"}]
        poster = FakePoster(settings={"min_topic_title_length": "3"})

        rows = {row["setting"]: row for row in check_site_settings(
            poster, plan_site_settings(threads, [])
        )}

        assert rows["min_topic_title_length"]["ok"] is True

    def test_the_current_value_is_reported_so_it_can_be_restored(self):
        """The whole point: these are loosened for the import and put back."""
        threads = [{"tid": "1", "subject": "Klausuren", "firstpost": "1"}]
        poster = FakePoster(settings={
            "min_topic_title_length": "15", "title_prettify": "true",
        })

        rows = {row["setting"]: row for row in check_site_settings(
            poster, plan_site_settings(threads, [])
        )}

        assert rows["min_topic_title_length"]["now"] == "15"
        assert rows["title_prettify"]["now"] == "true"

    @pytest.mark.parametrize("allowed,ok", [
        ("jpg|png|pdf|docx", True),
        ("jpg|png", False),
        ("*", True),
    ])
    def test_the_extensions_have_to_be_listed(self, allowed, ok):
        attachments = [
            {"pid": "1", "filename": "Angabe.pdf", "filesize": "10"},
            {"pid": "1", "filename": "Notizen.docx", "filesize": "10"},
        ]
        poster = FakePoster(settings={"authorized_extensions": allowed})

        rows = {row["setting"]: row for row in check_site_settings(
            poster, plan_site_settings([], [a_post("1")], attachments)
        )}

        assert rows["authorized_extensions"]["ok"] is ok

    def test_it_names_only_the_missing_extensions(self):
        attachments = [
            {"pid": "1", "filename": "Angabe.pdf", "filesize": "10"},
            {"pid": "1", "filename": "Bild.png", "filesize": "10"},
        ]
        poster = FakePoster(settings={"authorized_extensions": "jpg|png"})

        rows = {row["setting"]: row for row in check_site_settings(
            poster, plan_site_settings([], [a_post("1")], attachments)
        )}

        assert rows["authorized_extensions"]["needed"] == "pdf"

    def test_a_ceiling_that_is_too_low_is_reported(self):
        attachments = [
            {"pid": "1", "filename": f"f{n}.pdf", "filesize": "10"} for n in range(5)
        ]
        poster = FakePoster(settings={"newuser_max_attachments": "2"})

        rows = {row["setting"]: row for row in check_site_settings(
            poster, plan_site_settings([], [a_post("1")], attachments)
        )}

        assert rows["newuser_max_attachments"]["ok"] is False

    def test_a_setting_the_site_does_not_report_is_not_called_wrong(self):
        """Discourse renames settings between versions. Unknown is unknown."""
        threads = [{"tid": "1", "subject": "Klausuren", "firstpost": "1"}]
        poster = FakePoster(settings={})

        rows = {row["setting"]: row for row in check_site_settings(
            poster, plan_site_settings(threads, [])
        )}

        assert rows["min_topic_title_length"]["ok"] is None


class TestLooseningAndPuttingBack:
    """The import changes the settings itself, and undoes it."""

    @pytest.fixture
    def journal(self, tmp_path):
        return tmp_path / "forum-settings.json"

    @pytest.fixture
    def board(self):
        return (
            [{"tid": "1", "subject": "Klausuren", "firstpost": "1"},
             {"tid": "2", "subject": "Klausuren", "firstpost": "2"}],
            [a_post("1", message="Danke"), a_post("2", message="Bitte")],
        )

    def a_forum(self):
        return FakePoster(settings={
            "disable_emails": "no",
            "min_topic_title_length": "15",
            "allow_duplicate_topic_titles": "false",
            "title_prettify": "true",
            "title_min_entropy": "10",
            "min_post_length": "20",
            "min_first_post_length": "20",
            "body_min_entropy": "7",
            "max_topics_in_first_day": "5",
            "max_topics_per_day": "20",
        })

    def test_it_changes_what_needs_changing(self, app, board, journal):
        poster = self.a_forum()

        with app.app_context():
            changes = loosen_site_settings(poster, plan_site_settings(*board), journal)

        written = dict(poster.settings_written)
        assert written["min_topic_title_length"] == "9"
        assert written["allow_duplicate_topic_titles"] == "true"
        assert written["disable_emails"] == "non-staff"
        assert {change["setting"] for change in changes} == set(written)

    def test_it_leaves_alone_what_is_already_wide_enough(self, app, board, journal):
        poster = self.a_forum()
        poster._settings["max_topics_per_day"] = "1000"

        with app.app_context():
            loosen_site_settings(poster, plan_site_settings(*board), journal)

        assert "max_topics_per_day" not in dict(poster.settings_written)

    def test_the_record_is_on_disk_before_anything_is_touched(self, app, board, journal):
        """A run that dies halfway has to be undoable by somebody else."""
        poster = self.a_forum()
        poster.refuse_to_write = {"title_prettify"}

        with app.app_context():
            with pytest.raises(ForumProviderError):
                loosen_site_settings(poster, plan_site_settings(*board), journal)

        payload = read_settings_journal(journal)
        assert payload["forum"] == poster.base_url
        recorded = {row["setting"]: row["was"] for row in payload["changes"]}
        assert recorded["min_topic_title_length"] == "15"
        assert "title_prettify" in recorded, "the one that failed is recorded too"

    def test_restoring_puts_every_value_back(self, app, board, journal):
        poster = self.a_forum()
        before = poster.site_settings()

        with app.app_context():
            changes = loosen_site_settings(poster, plan_site_settings(*board), journal)
            restore_site_settings(poster, changes)

        assert poster.site_settings() == before

    def test_a_setting_marked_to_keep_is_not_put_back(self, app, journal):
        """The forum has to go on accepting PDFs after the import."""
        poster = FakePoster(settings={"authorized_extensions": "jpg|png"})
        attachments = [{"pid": "1", "filename": "Angabe.pdf", "filesize": "10"}]

        with app.app_context():
            changes = loosen_site_settings(
                poster, plan_site_settings([], [a_post("1")], attachments), journal
            )
            results = {row["setting"]: row for row in restore_site_settings(poster, changes)}

        assert "pdf" in poster.site_settings()["authorized_extensions"]
        assert results["authorized_extensions"]["outcome"] == "left as it is, on purpose"

    def test_widening_a_list_keeps_what_was_there(self, app, journal):
        poster = FakePoster(settings={"authorized_extensions": "jpg|png"})
        attachments = [{"pid": "1", "filename": "Angabe.pdf", "filesize": "10"}]

        with app.app_context():
            loosen_site_settings(
                poster, plan_site_settings([], [a_post("1")], attachments), journal
            )

        allowed = poster.site_settings()["authorized_extensions"].split("|")
        assert set(allowed) == {"jpg", "png", "pdf"}

    def test_a_setting_somebody_else_changed_is_left_alone(self, app, board, journal):
        poster = self.a_forum()

        with app.app_context():
            changes = loosen_site_settings(poster, plan_site_settings(*board), journal)
            # Somebody decides mid-import that duplicate titles are a bad idea.
            poster._settings["allow_duplicate_topic_titles"] = "false"
            results = {row["setting"]: row for row in restore_site_settings(poster, changes)}

        assert results["allow_duplicate_topic_titles"]["outcome"].startswith("left alone")
        assert results["min_topic_title_length"]["outcome"] == "restored"

    def test_force_overrides_that(self, app, board, journal):
        poster = self.a_forum()

        with app.app_context():
            changes = loosen_site_settings(poster, plan_site_settings(*board), journal)
            poster._settings["allow_duplicate_topic_titles"] = "false"
            results = {row["setting"]: row
                       for row in restore_site_settings(poster, changes, force=True)}

        assert results["allow_duplicate_topic_titles"]["outcome"] == "restored"

    def test_a_forum_that_needs_nothing_writes_no_record(self, app, journal):
        poster = FakePoster(settings={
            "disable_emails": "non-staff", "min_topic_title_length": "1",
            "title_prettify": "false", "title_min_entropy": "0",
        })
        threads = [{"tid": "1", "subject": "Klausuren", "firstpost": "1"}]

        with app.app_context():
            changes = loosen_site_settings(poster, plan_site_settings(threads, []), journal)

        assert changes == []
        assert not journal.exists()

    def test_an_empty_record_is_refused(self, journal):
        journal.write_text(json.dumps({"changes": []}), encoding="utf-8")

        with pytest.raises(ValueError):
            read_settings_journal(journal)


class TestTheGuardsAimedAtBrandNewAccounts:
    """Every author is an account created minutes ago at trust level 0."""

    def test_a_long_exchange_needs_the_per_topic_cap_raised(self):
        posts = [a_post(str(n), uid="7") for n in range(1, 14)]
        for post in posts:
            post["tid"] = "5"
        requirements = {r.setting: r for r in plan_site_settings([], posts)}

        assert requirements["newuser_max_replies_per_topic"].needed == 12

    def test_somebody_prolific_needs_the_daily_caps_raised(self):
        threads = [{"tid": str(n), "subject": f"Klausur {n}", "firstpost": str(n)}
                   for n in range(1, 8)]
        posts = [a_post(str(n), uid="7") for n in range(1, 8)]
        requirements = {r.setting: r for r in plan_site_settings(threads, posts)}

        assert requirements["max_topics_in_first_day"].needed == 7
        assert requirements["max_topics_per_day"].needed == 7

    def test_links_and_images_are_counted(self):
        posts = [a_post("1", message="Siehe https://a.at und https://b.at und https://c.at")]
        requirements = {r.setting: r for r in plan_site_settings([], posts)}

        assert requirements["newuser_max_links"].needed == 3

    def test_the_plainest_title_sets_the_entropy_floor(self):
        """"AM" is two distinct characters against a default of ten."""
        threads = [{"tid": "1", "subject": "AM", "firstpost": "1"}]
        requirements = {r.setting: r for r in plan_site_settings(threads, [])}

        assert requirements["title_min_entropy"].needed == 2

    def test_mail_is_turned_off_but_is_not_a_reason_to_stop(self):
        requirements = {r.setting: r for r in plan_site_settings([], [])}

        assert requirements["disable_emails"].needed == "non-staff"
        assert requirements["disable_emails"].blocks is False

    def test_a_title_too_short_is_a_reason_to_stop(self):
        threads = [{"tid": "1", "subject": "AM", "firstpost": "1"}]
        requirements = {r.setting: r for r in plan_site_settings(threads, [])}

        assert requirements["min_topic_title_length"].blocks is True


class TestTheShapesDiscourseActuallyReturns:
    """The API answers with JSON types, not with the strings it was sent."""

    def test_a_switch_that_comes_back_as_a_json_boolean_still_restores(self, app, tmp_path):
        """Set "true", read back True. Comparing those literally strands it."""
        class BooleanForum(FakePoster):
            def set_site_setting(self, setting, value):
                self.settings_written.append((setting, value))
                if str(value).lower() in {"true", "false"}:
                    value = str(value).lower() == "true"
                self._settings[setting] = value

        poster = BooleanForum(settings={
            "allow_duplicate_topic_titles": False, "title_prettify": True,
        })
        threads = [{"tid": "1", "subject": "Klausuren Aerodynamik", "firstpost": "1"},
                   {"tid": "2", "subject": "Klausuren Aerodynamik", "firstpost": "2"}]
        journal = tmp_path / "settings.json"

        with app.app_context():
            changes = loosen_site_settings(poster, plan_site_settings(threads, []), journal)
            results = {row["setting"]: row for row in restore_site_settings(poster, changes)}

        assert results["allow_duplicate_topic_titles"]["outcome"] == "restored"
        assert poster.site_settings()["allow_duplicate_topic_titles"] is False

    def test_a_boolean_already_correct_is_not_changed(self, app, tmp_path):
        poster = FakePoster(settings={"title_prettify": False, "disable_emails": "non-staff"})

        with app.app_context():
            changes = loosen_site_settings(
                poster,
                plan_site_settings([{"tid": "1", "subject": "Klausuren", "firstpost": "1"}], []),
                tmp_path / "settings.json",
            )

        assert "title_prettify" not in {change["setting"] for change in changes}


class TestPickingAThreadToRehearseWith:
    """720 threads, and no way to pick a useful one by eye."""

    def a_board(self):
        threads = [
            {"tid": "1", "subject": "Einer allein", "firstpost": "1"},
            {"tid": "2", "subject": "Zwei Leute", "firstpost": "3"},
            {"tid": "3", "subject": "Zwei Leute mit Anhang", "firstpost": "5"},
        ]
        posts = [
            {"pid": "1", "tid": "1", "uid": "7", "dateline": "1", "message": "allein"},
            {"pid": "2", "tid": "1", "uid": "7", "dateline": "2", "message": "immer noch"},
            {"pid": "3", "tid": "2", "uid": "7", "dateline": "3", "message": "frage"},
            {"pid": "4", "tid": "2", "uid": "8", "dateline": "4", "message": "antwort"},
            {"pid": "5", "tid": "3", "uid": "7", "dateline": "5", "message": "angabe"},
            {"pid": "6", "tid": "3", "uid": "8", "dateline": "6", "message": "danke"},
        ]
        attachments = [{"pid": "5", "filename": "Angabe.pdf", "filesize": "10"}]
        return threads, posts, attachments

    def test_a_thread_with_attachments_comes_first(self):
        picked = rehearsal_threads(*self.a_board())

        assert picked[0]["tid"] == "3"
        assert picked[0]["attachments"] == 1

    def test_one_person_talking_to_themselves_is_no_rehearsal(self):
        """Impersonation is the thing being tested. One author tests nothing."""
        picked = rehearsal_threads(*self.a_board())

        assert "1" not in {row["tid"] for row in picked}

    def test_a_single_post_is_no_rehearsal_either(self):
        """Replies landing in the topic the first post opened is half the job."""
        threads = [{"tid": "9", "subject": "Nur einer", "firstpost": "20"}]
        posts = [{"pid": "20", "tid": "9", "uid": "7", "dateline": "1", "message": "hallo"}]

        assert rehearsal_threads(threads, posts) == []

    def test_it_counts_what_it_shows(self):
        picked = {row["tid"]: row for row in rehearsal_threads(*self.a_board())}

        assert picked["2"]["posts"] == 2
        assert picked["2"]["authors"] == 2
        assert picked["2"]["attachments"] == 0


class TestTheRecordIsNotOverwritten:
    def test_a_second_run_will_not_write_over_the_first(self, app, tmp_path):
        """Otherwise the loosened values get recorded as the originals."""
        journal = tmp_path / "settings.json"
        threads = [{"tid": "1", "subject": "Klausuren", "firstpost": "1"}]

        with app.app_context():
            first = FakePoster(settings={"min_topic_title_length": "15"})
            loosen_site_settings(first, plan_site_settings(threads, []), journal)
            recorded = read_settings_journal(journal)

            second = FakePoster(settings={"min_topic_title_length": "9"})
            with pytest.raises(FileExistsError):
                loosen_site_settings(second, plan_site_settings(threads, []), journal)

        assert read_settings_journal(journal) == recorded
        assert second.settings_written == [], "and nothing was changed either"


class TestWhenDiscourseSaysTheAdminRouteIsNotThere:
    """404 on an admin route means "not staff", not "not implemented"."""

    class Poster(ContentPoster):
        def __init__(self, key, answers):
            super().__init__({
                "forum_base_url": "https://forum.example.at",
                "discourse_api_key": key,
                "discourse_api_username": "system",
            })
            self.answers = answers
            self.asked = []

        def _call(self, method, path, **kwargs):
            self.asked.append((method, path))
            answer = self.answers.get(path)
            if answer is None:
                raise ForumProviderError(f"{method} {path} failed (404)")
            return answer

    A_REAL_KEY = "a" * 64

    def test_it_tries_the_other_paths_it_has_been_known_to_live_at(self):
        poster = self.Poster(self.A_REAL_KEY, {
            "/admin/config/site_settings.json": {
                "site_settings": [{"setting": "min_post_length", "value": "20"}]
            },
        })

        assert poster.site_settings() == {"min_post_length": "20"}
        assert ("GET", "/admin/site_settings.json") in poster.asked

    def test_writing_goes_back_to_the_path_that_answered(self):
        poster = self.Poster(self.A_REAL_KEY, {
            "/admin/config/site_settings.json": {"site_settings": []},
            "/admin/config/site_settings/min_post_length.json": {},
        })

        poster.set_site_setting("min_post_length", 2)

        assert ("PUT", "/admin/config/site_settings/min_post_length.json") in poster.asked

    def test_a_truncated_key_is_blamed_before_the_forum_is(self):
        """The usual cause, and it looks like a missing page."""
        poster = self.Poster("faa7e2993d99a10bd006ba18e6b716b7c8729594e5", {})

        with pytest.raises(ForumProviderError) as raised:
            poster.site_settings()

        assert "42 characters" in str(raised.value)
        assert "truncated" in str(raised.value)

    def test_with_a_good_key_the_forum_is_blamed_instead(self):
        poster = self.Poster(self.A_REAL_KEY, {})

        with pytest.raises(ForumProviderError) as raised:
            poster.site_settings()

        assert "not an administrator" in str(raised.value)
        assert "/admin/site_settings.json" in str(raised.value)

    def test_an_empty_key_says_so(self):
        poster = self.Poster("", {})

        with pytest.raises(ForumProviderError) as raised:
            poster.site_settings()

        assert "no API key" in str(raised.value)


class TestAKeyReadOutOfAFile:
    def test_a_trailing_newline_is_not_part_of_the_key(self):
        """cat and every editor add one. A header with it in is broken."""
        poster = ContentPoster({
            "forum_base_url": "https://forum.example.at/",
            "discourse_api_key": "b" * 64 + "\n",
            "discourse_api_username": " system \n",
        })

        assert poster.api_key == "b" * 64
        assert poster.admin_username == "system"
        assert poster._key_complaint() is None
