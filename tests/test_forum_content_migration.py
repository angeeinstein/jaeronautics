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
import contextlib
import io
import json
from urllib.error import HTTPError

import pytest

from aeronautics_members.forum_service import ForumProviderError
from aeronautics_members.services.forum_content import (
    EMPTY_POST,
    LOOSEN,
    SMILEY_ONLY_POST,
    PROCEED,
    REFUSE,
    ContentPoster,
    check_site_settings,
    journal_is_spent,
    loosen_site_settings,
    mark_journal_restored,
    migrate_thread,
    plan_site_settings,
    read_settings_journal,
    rehearsal_threads,
    restore_site_settings,
    what_discourse_counts,
    what_to_do_about_settings,
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


class TestAPostWithNothingInIt:
    """Discourse measures a stripped body; this used to measure the raw one.

    A post whose converted body is two newlines is two characters here and
    none there, so the run set min_post_length to 2, sent it, and was told
    "Body is too short (minimum is 2 characters)" -- a refusal that reads like
    a contradiction.
    """

    def test_the_length_that_counts_is_the_stripped_one(self):
        posts = [{"pid": "1", "tid": "1", "uid": "7", "message": "  \n\n  "}]
        threads = [{"tid": "1", "subject": "Klausuren", "firstpost": "1"}]

        requirements = {r.setting: r for r in plan_site_settings(threads, posts)}

        assert requirements["min_post_length"].why.endswith("0 characters after conversion")

    def test_but_it_never_asks_for_a_minimum_of_nothing(self):
        """Nothing is sent empty, so nothing needs a minimum of zero."""
        posts = [{"pid": "1", "tid": "1", "uid": "7", "message": "  "}]
        threads = [{"tid": "1", "subject": "Klausuren", "firstpost": "1"}]

        requirements = {r.setting: r for r in plan_site_settings(threads, posts)}

        assert requirements["min_post_length"].needed == 1
        assert requirements["min_first_post_length"].needed == 1

    def test_it_is_posted_as_a_marked_empty_post(self, app):
        """Dropping it would take its date and its place in the thread too."""
        poster = FakePoster()
        thread = {"tid": "1", "subject": "Klausuren", "firstpost": "1"}
        posts = [
            {"pid": "1", "tid": "1", "uid": "7", "dateline": "1490000000",
             "message": "Hier die Angabe."},
            {"pid": "2", "tid": "1", "uid": "8", "dateline": "1490000100",
             "message": "   "},
        ]

        with app.app_context():
            report = migrate_thread(
                poster, thread, posts, {}, {"7": "A_L23", "8": "B_L19"},
                "/nowhere", 12,
            )

        assert report["posts"][1]["result"] == "posted: empty on the old board"
        assert poster.calls[1]["raw"] == EMPTY_POST
        assert [post["result"] for post in report["posts"]] == [
            "posted", "posted: empty on the old board",
        ]


class TestAPostDiscourseCountsAsEmptyThoughItIsNot:
    """``:f16:`` -- the old board's F-16 smiley, and a real refused post.

    Discourse removes emoji shortcodes before it measures a post, so five
    characters here are none there, and no value of min_post_length makes it
    acceptable: the refusal quotes a minimum the post appears to meet. Two
    earlier explanations of this failure were wrong, which is what the
    inspect-forum-post command now exists to prevent.
    """

    def test_what_discourse_counts_leaves_out_the_smiley(self):
        assert what_discourse_counts(":f16:") == ""
        assert what_discourse_counts("Danke! :f16:") == "Danke!"

    def test_a_time_of_day_is_not_mistaken_for_a_smiley(self):
        """Over-stripping only matters when it empties a post that is not."""
        assert what_discourse_counts("Treffpunkt 12:30:45 Uhr")

    def test_the_smiley_is_kept_and_the_post_is_accepted(self, app):
        poster = FakePoster()
        thread = {"tid": "1", "subject": "New LAVBoard Design", "firstpost": "1"}
        posts = [
            {"pid": "1", "tid": "1", "uid": "7", "dateline": "1490000000",
             "message": "Was haltet ihr davon?"},
            {"pid": "2", "tid": "1", "uid": "8", "dateline": "1490000100",
             "message": ":f16:"},
        ]

        with app.app_context():
            report = migrate_thread(
                poster, thread, posts, {}, {"7": "A_L23", "8": "B_L19"},
                "/nowhere", 12,
            )

        assert ":f16:" in poster.calls[1]["raw"], "the smiley is not thrown away"
        assert SMILEY_ONLY_POST in poster.calls[1]["raw"]
        assert report["posts"][1]["result"] == "posted: only a smiley on the old board"

    def test_the_planned_minimum_is_what_discourse_will_count(self, app):
        posts = [{"pid": "1", "tid": "1", "uid": "7", "message": ":f16:"}]
        threads = [{"tid": "1", "subject": "Klausuren", "firstpost": "1"}]

        requirements = {r.setting: r for r in plan_site_settings(threads, posts)}

        assert requirements["min_post_length"].needed == 1, "and never zero"
        assert requirements["min_post_length"].why.endswith("0 characters after conversion")


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
            # Somebody picks a third value: not what the import set (2) and not
            # what it was before (15), so putting 15 back would discard a real
            # decision somebody made.
            poster._settings["min_topic_title_length"] = "7"
            results = {row["setting"]: row for row in restore_site_settings(poster, changes)}

        assert results["min_topic_title_length"]["outcome"].startswith("left alone")
        assert results["title_min_entropy"]["outcome"] == "restored"

    def test_settings_that_are_already_back_say_so(self, app, board, journal):
        """The commonest case of all, and it used to accuse a passer-by.

        Ctrl-C puts the settings back on its way out -- and then tells you to
        run the restore, which is what somebody sensible does. Finding them
        already back was reported as "somebody else has changed it", which
        reads like an intruder and is only the run's own tidying up.
        """
        poster = self.a_forum()

        with app.app_context():
            changes = loosen_site_settings(poster, plan_site_settings(*board), journal)
            restore_site_settings(poster, changes)
            again = {row["setting"]: row for row in restore_site_settings(poster, changes)}

        assert {row["outcome"] for row in again.values()} <= {
            "already back", "left as it is, on purpose",
        }
        assert not any("somebody else" in row["outcome"] for row in again.values())

    def test_force_overrides_that(self, app, board, journal):
        poster = self.a_forum()

        with app.app_context():
            changes = loosen_site_settings(poster, plan_site_settings(*board), journal)
            poster._settings["min_topic_title_length"] = "7"
            results = {row["setting"]: row
                       for row in restore_site_settings(poster, changes, force=True)}

        assert results["min_topic_title_length"]["outcome"] == "restored"

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

        # Every post they made in it, not every one after their first:
        # Discourse refused the thirteenth saying "limited to 12 replies".
        assert requirements["newuser_max_replies_per_topic"].needed == 13

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


class TestASettingDiscourseHasRenamed:
    """Unknown and renamed look the same, and one of them is not a problem."""

    def a_post_with_images(self):
        return [a_post("1", message="[img]a.png[/img][img]b.png[/img][img]c.png[/img]")]

    def test_the_name_this_forum_uses_is_the_one_checked(self):
        poster = FakePoster(settings={"newuser_max_embedded_media": "1"})

        rows = {row["setting"]: row for row in check_site_settings(
            poster, plan_site_settings([], self.a_post_with_images())
        )}

        assert "newuser_max_embedded_media" in rows
        assert rows["newuser_max_embedded_media"]["ok"] is False
        assert rows["newuser_max_embedded_media"]["now"] == "1"

    def test_the_old_name_still_works_where_it_is_the_one_there(self):
        poster = FakePoster(settings={"newuser_max_images": "1"})

        rows = {row["setting"]: row for row in check_site_settings(
            poster, plan_site_settings([], self.a_post_with_images())
        )}

        assert rows["newuser_max_images"]["ok"] is False

    def test_it_is_written_back_under_the_name_that_exists(self, app, tmp_path):
        """Restoring under a name the site does not have is a 404 later on."""
        poster = FakePoster(settings={"newuser_max_embedded_media": "1"})

        with app.app_context():
            changes = loosen_site_settings(
                poster, plan_site_settings([], self.a_post_with_images()),
                tmp_path / "settings.json",
            )
            restore_site_settings(poster, changes)

        assert [name for name, _ in poster.settings_written] == [
            "newuser_max_embedded_media", "newuser_max_embedded_media"
        ]
        assert poster.site_settings()["newuser_max_embedded_media"] == "1"

    def test_a_setting_under_no_known_name_is_still_reported_unknown(self):
        poster = FakePoster(settings={})

        rows = {row["setting"]: row for row in check_site_settings(
            poster, plan_site_settings([], self.a_post_with_images())
        )}

        assert rows["newuser_max_images"]["ok"] is None


class TestWhetherARunCanGoAhead:
    """A dry run sends nothing, so nothing the forum allows can stop it."""

    def decide(self, **kwargs):
        defaults = {"ready": False, "anything": True,
                    "adjust_settings": False, "dry_run": False}
        return what_to_do_about_settings(**{**defaults, **kwargs})

    def test_a_dry_run_is_never_refused(self):
        """It is how you find out what is missing before changing anything."""
        assert self.decide(dry_run=True) == PROCEED

    def test_a_dry_run_changes_nothing_either(self):
        assert self.decide(dry_run=True, adjust_settings=True) == PROCEED

    def test_a_real_run_that_would_be_refused_is_refused(self):
        assert self.decide() == REFUSE

    def test_unless_it_was_told_to_change_them(self):
        assert self.decide(adjust_settings=True) == LOOSEN

    def test_a_forum_already_wide_enough_just_runs(self):
        assert self.decide(ready=True, anything=False) == PROCEED

    def test_something_worth_changing_that_blocks_nothing_still_gets_changed(self):
        """disable_emails does not cause a refusal, but nobody wants the mail."""
        assert self.decide(ready=True, anything=True, adjust_settings=True) == LOOSEN

    def test_and_does_not_stop_a_run_that_was_not_told_to(self):
        assert self.decide(ready=True, anything=True) == PROCEED


class TestWhatAPostCarriesOnceItsAttachmentsAreOnIt:
    """The body that gets posted is not the body somebody typed in 2017.

    Each attachment is appended as markdown: a picture inline, anything else
    as a link. Counting only the old text said a post carried nothing, and
    Discourse refused it for carrying four.
    """

    def test_attachments_count_as_links(self):
        posts = [a_post("1", message="Hier die Angaben.")]
        attachments = [
            {"pid": "1", "filename": f"Angabe{n}.pdf", "filesize": "10"}
            for n in range(3)
        ]
        requirements = {r.setting: r for r in plan_site_settings([], posts, attachments)}

        assert requirements["newuser_max_links"].needed == 3

    def test_pictures_count_as_embedded_media_instead(self):
        posts = [a_post("1", message="Screenshots vom Beispiel.")]
        attachments = [
            {"pid": "1", "filename": f"bild{n}.png", "filesize": "10"}
            for n in range(4)
        ]
        requirements = {r.setting: r for r in plan_site_settings([], posts, attachments)}

        assert requirements["newuser_max_images"].needed == 4

    def test_links_in_the_text_are_added_to_the_attachments(self):
        posts = [a_post("1", message="Siehe https://example.at und https://fh.at")]
        attachments = [{"pid": "1", "filename": "Angabe.pdf", "filesize": "10"}]
        requirements = {r.setting: r for r in plan_site_settings([], posts, attachments)}

        assert requirements["newuser_max_links"].needed == 3

    def test_another_post_s_attachments_are_not_counted_against_this_one(self):
        posts = [a_post("1", message="eins"), a_post("2", message="zwei")]
        attachments = [
            {"pid": "1", "filename": "a.pdf", "filesize": "10"},
            {"pid": "2", "filename": "b.pdf", "filesize": "10"},
        ]
        requirements = {r.setting: r for r in plan_site_settings([], posts, attachments)}

        assert requirements["newuser_max_links"].needed == 1

    def test_the_waiting_between_posts_is_asked_for_too(self):
        """Hit on the second post anybody makes, every time."""
        requirements = {r.setting: r for r in plan_site_settings([], [a_post("1")])}

        for setting in ("rate_limit_create_post", "rate_limit_new_user_create_post",
                        "rate_limit_create_topic", "rate_limit_new_user_create_topic"):
            assert requirements[setting].needed == 0
            assert requirements[setting].compare == "at_most"


class TestBeingToldToWait:
    """A dropped post is lost. The settings go back at the end of the run."""

    def too_quickly(self, wait_seconds=29):
        detail = json.dumps({
            "errors": ["You're replying a bit too quickly."],
            "error_type": "rate_limit",
            "extras": {"wait_seconds": wait_seconds},
        }).encode()
        return HTTPError("https://forum.example.at/posts.json", 429, "Too Many",
                         {}, io.BytesIO(detail))

    def test_it_waits_as_long_as_it_was_asked_to_and_tries_again(self, app, monkeypatch):
        slept = []
        monkeypatch.setattr(
            "aeronautics_members.services.forum_content.time.sleep", slept.append
        )
        answers = [self.too_quickly(29), None]

        def urlopen(request, timeout=None):
            answer = answers.pop(0)
            if answer is not None:
                raise answer
            return contextlib.closing(io.BytesIO(b'{"id": 7, "topic_id": 3}'))

        monkeypatch.setattr(
            "aeronautics_members.services.forum_content.urlopen", urlopen
        )
        poster = ContentPoster({
            "forum_base_url": "https://forum.example.at",
            "discourse_api_key": "c" * 64,
            "discourse_api_username": "system",
        })

        with app.app_context():
            result = poster.create_post(
                raw="Danke!", as_username="LutzB_L21",
                created_at="2022-02-04T10:00:00+00:00", topic_id=3,
            )

        assert result["id"] == 7
        assert slept == [29]

    def test_it_gives_up_rather_than_waiting_for_ever(self, app, monkeypatch):
        slept = []
        monkeypatch.setattr(
            "aeronautics_members.services.forum_content.time.sleep", slept.append
        )
        monkeypatch.setattr(
            "aeronautics_members.services.forum_content.urlopen",
            lambda request, timeout=None: (_ for _ in ()).throw(self.too_quickly(5)),
        )
        poster = ContentPoster({
            "forum_base_url": "https://forum.example.at",
            "discourse_api_key": "c" * 64,
            "discourse_api_username": "system",
        })

        with app.app_context():
            with pytest.raises(ForumProviderError):
                poster.create_post(
                    raw="Danke!", as_username="LutzB_L21",
                    created_at="2022-02-04T10:00:00+00:00", topic_id=3,
                )

        assert len(slept) == 5, "five tries, then the problem is somebody else's"

    def test_an_hour_is_not_a_wait_it_honours(self, app):
        assert ContentPoster._wait_seconds(
            json.dumps({"extras": {"wait_seconds": 3600}})
        ) == 300

    def test_a_refusal_with_no_number_in_it_still_waits(self, app):
        assert ContentPoster._wait_seconds("not json at all") == 30


class TestAnAuthorTheForumCallsSomethingElse:
    """Discourse caps a username at twenty characters and shortens the rest.

    So NiedergrottenthalerR_L12 is not a user on the forum, and posting as them
    comes back 403 invalid_access -- the same answer a key bound to one user
    gives, which is what it was read as on the real run. Four files and a post
    were lost to it.
    """

    def _forbidden(self):
        return HTTPError(
            "https://forum.example.at/posts.json", 403, "Forbidden", {},
            io.BytesIO(b'{"errors":["invalid_access"],"error_type":"invalid_access"}'),
        )

    def _poster(self, monkeypatch, answers):
        def urlopen(request, timeout=None):
            answer = answers.pop(0)
            answer_as = request.get_header("Api-username")
            if isinstance(answer, Exception):
                raise answer
            return contextlib.closing(io.BytesIO(
                json.dumps({**answer, "acted_as": answer_as}).encode()
            ))

        monkeypatch.setattr(
            "aeronautics_members.services.forum_content.urlopen", urlopen
        )
        return ContentPoster({
            "forum_base_url": "https://forum.example.at",
            "discourse_api_key": "c" * 64,
            "discourse_api_username": "system",
        })

    def test_the_post_goes_again_under_the_name_the_forum_has(self, app, monkeypatch):
        poster = self._poster(monkeypatch, [
            self._forbidden(), {"id": 7, "topic_id": 3},
        ])
        poster.find_author = lambda name: "NiedergrottenthalerR_L"

        with app.app_context():
            result = poster.create_post(
                raw="Danke!", as_username="NiedergrottenthalerR_L12",
                created_at="2022-02-04T10:00:00+00:00", topic_id=3,
            )

        assert result["id"] == 7
        assert result["acted_as"] == "NiedergrottenthalerR_L"

    def test_the_forum_is_asked_once_however_many_posts_they_wrote(self, app, monkeypatch):
        poster = self._poster(monkeypatch, [
            self._forbidden(), {"id": 7}, self._forbidden(), {"id": 8},
        ])
        asked = []

        def find(name):
            asked.append(name)
            return "NiedergrottenthalerR_L"

        poster.find_author = find
        with app.app_context():
            for _ in range(2):
                poster.create_post(
                    raw="Danke!", as_username="NiedergrottenthalerR_L12",
                    created_at="2022-02-04T10:00:00+00:00", topic_id=3,
                )

        assert asked == ["NiedergrottenthalerR_L12"]

    def test_a_key_bound_to_one_user_is_still_reported(self, app, monkeypatch):
        """The other cause of the same 403, and the message must name both."""
        poster = self._poster(monkeypatch, [self._forbidden()])
        poster.find_author = lambda name: None

        with app.app_context():
            with pytest.raises(ForumProviderError) as raised:
                poster.create_post(
                    raw="Danke!", as_username="LutzB_L21",
                    created_at="2022-02-04T10:00:00+00:00", topic_id=3,
                )

        assert "All Users" in str(raised.value)
        assert "twenty-character" in str(raised.value)

    def test_a_lookup_that_answers_the_same_name_does_not_loop(self, app, monkeypatch):
        poster = self._poster(monkeypatch, [self._forbidden()])
        poster.find_author = lambda name: name

        with app.app_context():
            with pytest.raises(ForumProviderError):
                poster.create_post(
                    raw="Danke!", as_username="LutzB_L21",
                    created_at="2022-02-04T10:00:00+00:00", topic_id=3,
                )

    def test_a_lookup_that_breaks_does_not_end_the_run(self, app, monkeypatch):
        """It is a diagnosis, not the job. Its failure must read as the 403."""
        poster = self._poster(monkeypatch, [self._forbidden()])

        def explode(name):
            raise RuntimeError("the database is asleep")

        poster.find_author = explode
        with app.app_context():
            with pytest.raises(ForumProviderError):
                poster.create_post(
                    raw="Danke!", as_username="LutzB_L21",
                    created_at="2022-02-04T10:00:00+00:00", topic_id=3,
                )


class TestAJournalThatHasDoneItsJob:
    """A successful run must not block the next one."""

    def a_forum(self):
        return FakePoster(settings={
            "disable_emails": "no", "min_topic_title_length": "15",
            "title_prettify": "true", "title_min_entropy": "10",
        })

    def threads(self):
        return [{"tid": "1", "subject": "Klausuren", "firstpost": "1"}]

    def test_a_journal_still_live_stops_a_second_run(self, app, tmp_path):
        journal = tmp_path / "settings.json"
        poster = self.a_forum()

        with app.app_context():
            loosen_site_settings(poster, plan_site_settings(self.threads(), []), journal)
            with pytest.raises(FileExistsError):
                loosen_site_settings(
                    poster, plan_site_settings(self.threads(), []), journal
                )

    def test_a_journal_whose_settings_went_back_does_not(self, app, tmp_path):
        journal = tmp_path / "settings.json"
        poster = self.a_forum()

        with app.app_context():
            changes = loosen_site_settings(
                poster, plan_site_settings(self.threads(), []), journal
            )
            mark_journal_restored(journal, restore_site_settings(poster, changes))
            again = loosen_site_settings(
                poster, plan_site_settings(self.threads(), []), journal
            )

        assert again, "the second run got to change the settings"
        assert read_settings_journal(journal)["changes"] == again

    def test_a_restore_that_left_something_alone_keeps_it_live(self, app, tmp_path):
        """The forum is not as it was, so the way back still matters."""
        journal = tmp_path / "settings.json"
        poster = self.a_forum()

        with app.app_context():
            changes = loosen_site_settings(
                poster, plan_site_settings(self.threads(), []), journal
            )
            poster._settings["title_prettify"] = "somebody else's value"
            results = restore_site_settings(poster, changes)

            assert mark_journal_restored(journal, results) is False
            with pytest.raises(FileExistsError):
                loosen_site_settings(
                    poster, plan_site_settings(self.threads(), []), journal
                )

    def test_an_unreadable_journal_is_not_assumed_dealt_with(self, tmp_path):
        journal = tmp_path / "settings.json"
        journal.write_text("this is not json", encoding="utf-8")

        assert journal_is_spent(journal) is False

    def test_the_refusal_says_how_to_undo_it(self, app, tmp_path):
        journal = tmp_path / "settings.json"
        poster = self.a_forum()

        with app.app_context():
            loosen_site_settings(poster, plan_site_settings(self.threads(), []), journal)
            with pytest.raises(FileExistsError) as raised:
                loosen_site_settings(
                    poster, plan_site_settings(self.threads(), []), journal
                )

        assert "restore-forum-settings" in str(raised.value)


class TestPicturesAreSizedByADifferentRule:
    """Discourse measures an image against max_image_size_kb and nothing else."""

    def attachments(self):
        return [
            {"pid": "1", "filename": "Angabe.pdf", "filesize": str(9 * 1024 * 1024)},
            {"pid": "1", "filename": "IMG_2020.jpg", "filesize": str(4400 * 1024)},
        ]

    def test_the_picture_limit_comes_from_the_biggest_picture(self):
        requirements = {
            r.setting: r for r in
            plan_site_settings([], [a_post("1")], self.attachments())
        }

        assert requirements["max_image_size_kb"].needed == 4400
        assert requirements["max_image_size_kb"].compare == "at_least"

    def test_it_is_not_the_biggest_attachment(self):
        """Raising only the attachment limit lets the photograph through
        everything except the check that applies to it."""
        requirements = {
            r.setting: r for r in
            plan_site_settings([], [a_post("1")], self.attachments())
        }

        assert requirements["max_attachment_size_kb"].needed == 9 * 1024
        assert requirements["max_image_size_kb"].needed < 9 * 1024

    def test_a_board_with_no_pictures_is_not_asked_about_them(self):
        requirements = {
            r.setting: r for r in plan_site_settings(
                [], [a_post("1")],
                [{"pid": "1", "filename": "Angabe.pdf", "filesize": "1024"}],
            )
        }

        assert "max_image_size_kb" not in requirements


class TestWhenTheForumIsSimplyNotRunning:
    """A 502 is not a permissions problem, and should not read like one."""

    def a_poster(self, code, key="d" * 64):
        class Poster(ContentPoster):
            def _call(self, method, path, **kwargs):
                raise ForumProviderError(f"{method} {path} failed ({code}): down")

        return Poster({
            "forum_base_url": "https://forum.example.at",
            "discourse_api_key": key,
            "discourse_api_username": "system",
        })

    def test_it_blames_the_forum_rather_than_the_api_user(self):
        with pytest.raises(ForumProviderError) as raised:
            self.a_poster(502).site_settings()

        said = str(raised.value)
        assert "not running" in said
        assert "not an administrator" not in said

    def test_it_mentions_the_rebuild_that_asks_to_be_run_twice(self):
        """Which is what a Discourse upgrade leaves behind, mid-flight."""
        with pytest.raises(ForumProviderError) as raised:
            self.a_poster(503).site_settings()

        assert "second time" in str(raised.value)

    def test_a_truncated_key_is_still_blamed_on_a_404(self):
        with pytest.raises(ForumProviderError) as raised:
            self.a_poster(404, key="short").site_settings()

        assert "truncated" in str(raised.value)

    def test_and_a_good_key_on_a_404_still_points_at_admin_rights(self):
        with pytest.raises(ForumProviderError) as raised:
            self.a_poster(404).site_settings()

        assert "not an administrator" in str(raised.value)


class TestLimitsThatWereInTheListAllAlong:
    """Read off the forum's own settings rather than thought of."""

    def a_thread(self, uids):
        return [
            {"pid": str(n), "tid": "5", "uid": uid, "dateline": str(1000 + n),
             "message": "Angabe"}
            for n, uid in enumerate(uids)
        ]

    def test_replies_in_a_row_are_counted_apart_from_replies_in_total(self):
        """Four exam papers posted one after another is how this board was used."""
        posts = self.a_thread(["7", "7", "7", "7", "8", "7"])
        requirements = {r.setting: r for r in plan_site_settings([], posts)}

        assert requirements["max_consecutive_replies"].needed == 4
        # Five posts in the thread altogether, four of them in a row.
        assert requirements["newuser_max_replies_per_topic"].needed == 5

    def test_a_run_does_not_carry_across_threads(self):
        posts = self.a_thread(["7", "7"]) + [
            {"pid": "9", "tid": "6", "uid": "7", "dateline": "1", "message": "x"}
        ]
        requirements = {r.setting: r for r in plan_site_settings([], posts)}

        assert requirements["max_consecutive_replies"].needed == 2

    def test_a_conversation_that_alternates_needs_nothing_raised(self):
        posts = self.a_thread(["7", "8", "7", "8"])
        requirements = {r.setting: r for r in plan_site_settings([], posts)}

        assert "max_consecutive_replies" not in requirements

    def test_the_longest_post_has_to_fit(self):
        posts = [a_post("1", message="x" * 40000)]
        requirements = {r.setting: r for r in plan_site_settings([], posts)}

        assert requirements["max_post_length"].needed == 40000
        assert requirements["max_post_length"].compare == "at_least"

    def test_a_long_german_word_in_a_title_has_its_own_limit(self):
        """Discourse caps one word in a title, and German runs words together."""
        threads = [{"tid": "1", "firstpost": "1",
                    "subject": "Ueberflieger Weisswurstfruehstueck"}]
        requirements = {r.setting: r for r in plan_site_settings(threads, [])}

        assert requirements["title_max_word_length"].needed == len(
            "Weisswurstfruehstueck"
        )

    def test_the_longest_subject_has_to_fit_too(self):
        threads = [{"tid": "1", "firstpost": "1", "subject": "K" * 300}]
        requirements = {r.setting: r for r in plan_site_settings(threads, [])}

        assert requirements["max_topic_title_length"].needed == 300
