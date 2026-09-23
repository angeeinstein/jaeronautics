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
import pytest

from aeronautics_members.forum_service import ForumProviderError
from aeronautics_members.services.forum_content import (
    check_site_settings,
    migrate_thread,
    plan_site_settings,
)


class FakePoster:
    """A Discourse that records what it was asked and can be told to refuse."""

    def __init__(self, refuse_titles_shorter_than=0, settings=None):
        self.refuse_titles_shorter_than = refuse_titles_shorter_than
        self.calls = []
        self.uploads = []
        self._settings = settings or {}
        self._next_topic = 100

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
        return self._settings


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
