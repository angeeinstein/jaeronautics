"""Moving the whole board, and carrying on after it stops.

A run of 1,529 posts across nine gigabytes will be interrupted. The only two
outcomes that matter are that it can be run again and continues, or that it
cannot and somebody is left reconciling half an archive by hand. Everything
here is about the first one.
"""
import json

import pytest

from aeronautics_members.forum_service import ForumProviderError
from aeronautics_members.services.forum_board import (
    Ledger,
    audit_uploads,
    category_nesting_requirement,
    category_plan,
    ensure_categories,
    forum_tree,
    migrate_board,
)
from aeronautics_members.services.forum_content import migrate_thread

from test_forum_content_migration import FakePoster


class BoardPoster(FakePoster):
    """A forum that also has categories."""

    def __init__(self, categories=(), **kwargs):
        super().__init__(**kwargs)
        self._categories = list(categories)
        self.created_categories = []
        self._next_category = 500

    def categories(self):
        return list(self._categories)

    def create_category(self, name, parent_id=None):
        self._next_category += 1
        self.created_categories.append((name, parent_id))
        self._categories.append({
            "id": self._next_category, "name": name,
            "parent_category_id": parent_id,
        })
        return self._next_category


def a_board():
    """The shape the real one has: a heading, a degree, a semester, a lecture."""
    forums = [
        {"fid": "1", "pid": "0", "name": "Studium"},
        {"fid": "2", "pid": "1", "name": "Bachelor"},
        {"fid": "3", "pid": "2", "name": "3. Semester"},
        {"fid": "4", "pid": "3", "name": "Technisches Programmieren"},
        {"fid": "9", "pid": "2", "name": "Nie benutzt"},
    ]
    threads = [
        {"tid": "10", "fid": "4", "subject": "Klausuren Aerodynamik",
         "firstpost": "100", "dateline": "1490000000"},
    ]
    posts = [
        {"pid": "100", "tid": "10", "uid": "7", "dateline": "1490000000",
         "message": "Hier die Angabe von 2017."},
        {"pid": "101", "tid": "10", "uid": "8", "dateline": "1490000100",
         "message": "Danke, das hilft weiter."},
    ]
    return {
        "forums": forums, "threads": threads, "posts": posts,
        "attachments": [], "users": [{"uid": "7", "username": "HoferT_M13"},
                                     {"uid": "8", "username": "LutzB_L21"}],
    }


class TestTheOldBoardsShape:
    def test_a_forum_knows_its_ancestors(self):
        tree = forum_tree(a_board()["forums"])

        assert [row["name"] for row in tree["4"]] == [
            "Studium", "Bachelor", "3. Semester", "Technisches Programmieren"
        ]

    def test_a_loop_in_the_parents_does_not_hang(self):
        """Ten years of an admin panel produces stranger data than this."""
        forums = [{"fid": "1", "pid": "2", "name": "A"},
                  {"fid": "2", "pid": "1", "name": "B"}]

        assert len(forum_tree(forums)["1"]) == 2

    def test_a_forum_nobody_posted_in_is_not_recreated(self):
        plan = {row["fid"]: row for row in category_plan(*[
            a_board()["forums"], a_board()["threads"]
        ])}

        assert "9" not in plan, "nobody ever posted in it"
        assert "4" in plan

    def test_the_parents_of_a_used_forum_are_kept(self):
        """A subcategory needs something to be under."""
        plan = {row["fid"]: row for row in category_plan(
            a_board()["forums"], a_board()["threads"]
        )}

        assert set(plan) == {"1", "2", "3", "4"}

    def test_parents_come_before_their_children(self):
        plan = category_plan(a_board()["forums"], a_board()["threads"])
        made = []
        for row in plan:
            if row["parent_fid"]:
                assert row["parent_fid"] in made, f"{row['name']} has no parent yet"
            made.append(row["fid"])

    def test_a_board_deeper_than_discourse_allows_is_folded_not_dropped(self):
        """Four levels of MyBB into three of Discourse, keeping every name."""
        plan = {row["fid"]: row for row in category_plan(
            a_board()["forums"], a_board()["threads"]
        )}

        assert plan["4"]["depth"] == 3
        assert plan["4"]["path"] == [
            "Studium", "Bachelor", "3. Semester / Technisches Programmieren"
        ]

    def test_the_nesting_setting_is_asked_for(self):
        requirement = category_nesting_requirement(
            category_plan(a_board()["forums"], a_board()["threads"])
        )

        assert requirement.setting == "max_category_nesting"
        assert requirement.needed == 3


class TestTheLedger:
    def test_what_it_wrote_it_can_read_back(self, tmp_path):
        path = tmp_path / "ledger.jsonl"
        ledger = Ledger(path)
        ledger.record_topic("10", 99)
        ledger.record_post("100", 4001)
        ledger.record_category("4", 7)
        ledger.close()

        again = Ledger(path)
        assert again.topic_for("10") == 99
        assert again.post_for("100") == 4001
        assert again.category_for("4") == 7

    def test_an_unfinished_last_line_costs_one_post_not_the_file(self, app, tmp_path):
        """What an interrupted write leaves behind."""
        path = tmp_path / "ledger.jsonl"
        ledger = Ledger(path)
        ledger.record_post("100", 4001)
        ledger.close()
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"kind": "post", "key": "101", "i')

        with app.app_context():
            again = Ledger(path)

        assert again.post_for("100") == 4001
        assert again.post_for("101") is None

    def test_nothing_recorded_is_an_empty_ledger_not_an_error(self, tmp_path):
        ledger = Ledger(tmp_path / "never-written.jsonl")

        assert ledger.post_for("100") is None


class TestCarryingOnFromWhereItStopped:
    def test_a_post_already_there_is_not_posted_again(self, app, tmp_path):
        poster = FakePoster()
        ledger = Ledger(tmp_path / "ledger.jsonl")
        ledger.record_topic("10", 99)
        ledger.record_post("100", 4001)
        board = a_board()

        with app.app_context():
            report = migrate_thread(
                poster, board["threads"][0], board["posts"], {},
                {"7": "HoferT_M13", "8": "LutzB_L21"}, "/nowhere", 4,
                ledger=ledger,
            )

        assert report["posts"][0]["result"] == "already there"
        assert len(poster.calls) == 1, "only the one that was missing"
        assert poster.calls[0]["topic_id"] == 99

    def test_the_topic_is_not_created_again_either(self, app, tmp_path):
        poster = FakePoster()
        ledger = Ledger(tmp_path / "ledger.jsonl")
        ledger.record_topic("10", 99)
        board = a_board()

        with app.app_context():
            migrate_thread(
                poster, board["threads"][0], board["posts"], {},
                {"7": "HoferT_M13", "8": "LutzB_L21"}, "/nowhere", 4,
                ledger=ledger,
            )

        assert [call["title"] for call in poster.calls] == [None, None]

    def test_it_is_written_down_as_it_goes_not_at_the_end(self, app, tmp_path):
        """A run that dies on post three has to have recorded posts one and two."""
        path = tmp_path / "ledger.jsonl"
        poster = FakePoster()
        board = a_board()
        seen = []

        def create_post(**kwargs):
            seen.append(kwargs)
            if len(seen) == 2:
                raise ForumProviderError("POST /posts.json failed (500)")
            return FakePoster.create_post(poster, **kwargs)

        poster.create_post = create_post
        ledger = Ledger(path)

        with app.app_context():
            migrate_thread(
                poster, board["threads"][0], board["posts"], {},
                {"7": "HoferT_M13", "8": "LutzB_L21"}, "/nowhere", 4,
                ledger=ledger,
            )

        written = [json.loads(line) for line in
                   path.read_text(encoding="utf-8").splitlines()]
        assert {row["kind"] for row in written} == {"topic", "post"}
        assert any(row["key"] == "100" for row in written if row["kind"] == "post")
        assert not any(row["key"] == "101" for row in written if row["kind"] == "post")

    def test_without_a_ledger_it_behaves_as_it_always_did(self, app):
        poster = FakePoster()
        board = a_board()

        with app.app_context():
            report = migrate_thread(
                poster, board["threads"][0], board["posts"], {},
                {"7": "HoferT_M13", "8": "LutzB_L21"}, "/nowhere", 4,
            )

        assert [record["result"] for record in report["posts"]] == ["posted", "posted"]


class TestMakingTheCategories:
    def test_it_makes_them_parents_first(self, app, tmp_path):
        poster = BoardPoster()
        plan = category_plan(a_board()["forums"], a_board()["threads"])

        with app.app_context():
            made = ensure_categories(poster, plan, Ledger(tmp_path / "l.jsonl"))

        names = [name for name, _parent in poster.created_categories]
        assert names[0] == "Studium"
        assert len(made) == 4

    def test_a_category_already_there_is_not_made_twice(self, app, tmp_path):
        """A second run after an interruption must not double the tree."""
        poster = BoardPoster(categories=[
            {"id": 300, "name": "Studium", "parent_category_id": None},
        ])
        plan = category_plan(a_board()["forums"], a_board()["threads"])

        with app.app_context():
            made = ensure_categories(poster, plan, Ledger(tmp_path / "l.jsonl"))

        assert made["1"] == 300
        assert "Studium" not in [name for name, _ in poster.created_categories]

    def test_the_ledger_is_believed_before_the_forum_is_asked(self, app, tmp_path):
        poster = BoardPoster()
        ledger = Ledger(tmp_path / "l.jsonl")
        ledger.record_category("1", 42)
        plan = category_plan(a_board()["forums"], a_board()["threads"])

        with app.app_context():
            made = ensure_categories(poster, plan, ledger)

        assert made["1"] == 42

    def test_a_dry_run_makes_nothing(self, app, tmp_path):
        poster = BoardPoster()
        plan = category_plan(a_board()["forums"], a_board()["threads"])

        with app.app_context():
            ensure_categories(poster, plan, Ledger(tmp_path / "l.jsonl"), dry_run=True)

        assert poster.created_categories == []


class TestTheWholeBoard:
    def test_every_thread_goes_into_its_own_old_category(self, app, tmp_path):
        poster = BoardPoster()
        board = a_board()

        with app.app_context():
            summary = migrate_board(
                poster, board, "/nowhere", Ledger(tmp_path / "l.jsonl"),
            )

        assert summary["posted"] == 2
        assert summary["failed"] == 0
        opening = [call for call in poster.calls if call["title"]]
        assert opening[0]["category"] == poster._categories[-1]["id"]

    def test_a_thread_that_cannot_start_does_not_stop_the_others(self, app, tmp_path):
        """719 threads are not forfeit because one has no usable author."""
        board = a_board()
        board["threads"].append({
            "tid": "11", "fid": "4", "subject": "Ein zweiter Thread",
            "firstpost": "200", "dateline": "1490000000",
        })
        board["posts"].append({
            "pid": "200", "tid": "11", "uid": "999", "dateline": "1490000200",
            "message": "Von jemandem ohne Konto.",
        })
        poster = BoardPoster()

        with app.app_context():
            summary = migrate_board(
                poster, board, "/nowhere", Ledger(tmp_path / "l.jsonl"),
                fallback_username=None,
            )

        assert summary["posted"] == 2
        assert summary["failed"] == 1
        assert any("thread 11" in problem for problem in summary["problems"])

    def test_running_it_again_posts_nothing(self, app, tmp_path):
        """The property the whole ledger exists for."""
        path = tmp_path / "l.jsonl"
        board = a_board()

        with app.app_context():
            first = BoardPoster()
            migrate_board(first, board, "/nowhere", Ledger(path))

            second = BoardPoster(categories=first.categories())
            summary = migrate_board(second, board, "/nowhere", Ledger(path))

        assert second.calls == []
        assert summary["already_there"] == 2
        assert summary["posted"] == 0

    def test_the_oldest_thread_goes_first(self, app, tmp_path):
        """A run stopped halfway should end somewhere, not have holes."""
        board = a_board()
        board["threads"].insert(0, {
            "tid": "12", "fid": "4", "subject": "Der aeltere Thread",
            "firstpost": "300", "dateline": "1400000000",
        })
        board["posts"].append({
            "pid": "300", "tid": "12", "uid": "7", "dateline": "1400000000",
            "message": "Aus dem Jahr 2014.",
        })
        poster = BoardPoster()

        with app.app_context():
            migrate_board(poster, board, "/nowhere", Ledger(tmp_path / "l.jsonl"),
                          limit=1)

        assert [call["title"] for call in poster.calls] == ["Der aeltere Thread"]

    @pytest.mark.parametrize("limit,expected", [(1, 1), (2, 2), (0, 2)])
    def test_the_limit_counts_threads(self, app, tmp_path, limit, expected):
        board = a_board()
        board["threads"].append({
            "tid": "11", "fid": "4", "subject": "Noch ein Thread",
            "firstpost": "200", "dateline": "1490000500",
        })
        board["posts"].append({
            "pid": "200", "tid": "11", "uid": "7", "dateline": "1490000500",
            "message": "Noch etwas dazu.",
        })
        poster = BoardPoster()

        with app.app_context():
            summary = migrate_board(
                poster, board, "/nowhere", Ledger(tmp_path / "l.jsonl"), limit=limit,
            )

        assert summary["threads"] == expected


class TestACategoryTheForumWillNotMake:
    """275 categories, and the whole run already changed every setting."""

    def a_refusing_forum(self, refuse):
        poster = BoardPoster()
        original = poster.create_category

        def create_category(name, parent_id=None):
            if name in refuse:
                raise ForumProviderError(
                    'POST /categories.json failed (422): '
                    '{"errors":["Categories cannot be nested that deeply"]}'
                )
            return original(name, parent_id=parent_id)

        poster.create_category = create_category
        return poster

    def test_one_refusal_does_not_take_the_run_down(self, app, tmp_path):
        poster = self.a_refusing_forum({"3. Semester / Technisches Programmieren"})

        with app.app_context():
            summary = migrate_board(
                poster, a_board(), "/nowhere", Ledger(tmp_path / "l.jsonl"),
            )

        assert any("Technisches Programmieren" in problem
                   for problem in summary["problems"])
        assert summary["posted"] == 0, "its threads have nowhere to go"

    def test_the_threads_of_other_categories_still_land(self, app, tmp_path):
        board = a_board()
        board["forums"].append({"fid": "5", "pid": "2", "name": "Andere"})
        board["threads"].append({
            "tid": "11", "fid": "5", "subject": "Ein anderer Thread",
            "firstpost": "200", "dateline": "1490000000",
        })
        board["posts"].append({
            "pid": "200", "tid": "11", "uid": "7", "dateline": "1490000000",
            "message": "In einer Kategorie, die geht.",
        })
        poster = self.a_refusing_forum({"3. Semester / Technisches Programmieren"})

        with app.app_context():
            summary = migrate_board(
                poster, board, "/nowhere", Ledger(tmp_path / "l.jsonl"),
            )

        assert summary["posted"] == 1

    def test_a_child_of_a_refused_parent_is_not_tried_on_its_own(self, app, tmp_path):
        """It would land under the wrong parent, or at the top of the forum."""
        poster = self.a_refusing_forum({"Bachelor"})

        with app.app_context():
            summary = migrate_board(
                poster, a_board(), "/nowhere", Ledger(tmp_path / "l.jsonl"),
            )

        made = [name for name, _parent in poster.created_categories]
        assert made == ["Studium"]
        # One complaint about the category, not one per level below it.
        assert len([p for p in summary["problems"]
                    if p.startswith("category ")]) == 1
        # And the threads that have nowhere to go say so themselves.
        assert any("no category" in p for p in summary["problems"])


class TestKnowingWhichFilesAreHere:
    """Nine gigabytes fetched by hand over several sittings."""

    def attachments(self):
        return [
            {"pid": "1", "attachname": "201703/a.attach", "filesize": "100"},
            {"pid": "1", "attachname": "201705/b.attach", "filesize": "200"},
            {"pid": "2", "attachname": "201906/c.attach", "filesize": "300"},
        ]

    def a_folder(self, tmp_path, files):
        for name, size in files.items():
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * size)
        return tmp_path

    def test_it_says_what_is_still_to_come(self, tmp_path):
        folder = self.a_folder(tmp_path, {"201703/a.attach": 100})

        report = audit_uploads(self.attachments(), folder)

        assert report["present"] == 1
        assert report["missing"] == ["201705/b.attach", "201906/c.attach"]
        assert report["bytes_missing"] == 500

    def test_a_file_of_the_wrong_size_is_not_counted_as_here(self, tmp_path):
        """An error page written under the name of the file somebody wanted."""
        folder = self.a_folder(tmp_path, {
            "201703/a.attach": 100, "201705/b.attach": 17, "201906/c.attach": 300,
        })

        report = audit_uploads(self.attachments(), folder)

        assert report["missing"] == []
        assert [row["name"] for row in report["wrong_size"]] == ["201705/b.attach"]
        assert report["wrong_size"][0]["on_disk"] == 17

    def test_a_complete_folder_says_so(self, tmp_path):
        folder = self.a_folder(tmp_path, {
            "201703/a.attach": 100, "201705/b.attach": 200, "201906/c.attach": 300,
        })

        report = audit_uploads(self.attachments(), folder)

        assert not report["missing"] and not report["wrong_size"]
        assert report["bytes_present"] == 600

    def test_a_folder_that_is_not_there_is_not_an_error(self, tmp_path):
        """It is the first run, and nothing has been copied yet."""
        report = audit_uploads(self.attachments(), tmp_path / "nothing-here")

        assert report["present"] == 0
        assert len(report["missing"]) == 3

    def test_a_board_that_records_no_size_is_only_checked_for_presence(self, tmp_path):
        folder = self.a_folder(tmp_path, {"201703/a.attach": 999})

        report = audit_uploads(
            [{"pid": "1", "attachname": "201703/a.attach", "filesize": "0"}], folder
        )

        assert report["present"] == 1
        assert report["wrong_size"] == []


class TestAPostWhoseFilesAreNotHereYet:
    """Nine gigabytes will not be on the machine all at once."""

    def a_board_with_attachments(self):
        board = a_board()
        board["attachments"] = [
            {"pid": "100", "attachname": "201703/a.attach",
             "filename": "Angabe.pdf", "filesize": "10"},
        ]
        return board

    def test_it_waits_rather_than_posting_without_them(self, app, tmp_path):
        """Otherwise the PDF is lost: the post is done and never revisited."""
        poster = BoardPoster()

        with app.app_context():
            summary = migrate_board(
                poster, self.a_board_with_attachments(), tmp_path / "empty",
                Ledger(tmp_path / "l.jsonl"),
            )

        assert poster.calls == [], "not one post was sent"
        assert summary["waiting"] == 2
        assert summary["failed"] == 0, "waiting is not failing"

    def test_the_whole_thread_waits_if_its_first_post_does(self, app, tmp_path):
        """A reply must not become the topic under its own name and date."""
        poster = BoardPoster()

        with app.app_context():
            report_problems = migrate_board(
                poster, self.a_board_with_attachments(), tmp_path / "empty",
                Ledger(tmp_path / "l.jsonl"),
            )["problems"]

        assert any("left for a later run" in problem for problem in report_problems)

    def test_nothing_is_written_down_so_a_later_run_picks_it_up(self, app, tmp_path):
        path = tmp_path / "l.jsonl"
        board = self.a_board_with_attachments()

        with app.app_context():
            migrate_board(BoardPoster(), board, tmp_path / "empty", Ledger(path))

            # The file arrives.
            uploads = tmp_path / "uploads" / "201703"
            uploads.mkdir(parents=True)
            (uploads / "a.attach").write_bytes(b"x" * 10)

            poster = BoardPoster()
            summary = migrate_board(
                poster, board, tmp_path / "uploads", Ledger(path),
            )

        assert summary["posted"] == 2
        assert summary["waiting"] == 0
        assert poster.uploads == [("Angabe.pdf", None)]

    def test_a_thread_whose_files_are_here_still_goes(self, app, tmp_path):
        """One thread waiting must not hold up the ones that can go."""
        board = self.a_board_with_attachments()
        board["threads"].append({
            "tid": "11", "fid": "4", "subject": "Ohne Anhang",
            "firstpost": "200", "dateline": "1490000000",
        })
        board["posts"].append({
            "pid": "200", "tid": "11", "uid": "7", "dateline": "1490000000",
            "message": "Braucht keine Datei.",
        })
        poster = BoardPoster()

        with app.app_context():
            summary = migrate_board(
                poster, board, tmp_path / "empty", Ledger(tmp_path / "l.jsonl"),
            )

        assert summary["posted"] == 1
        assert summary["waiting"] == 2

    def test_it_can_be_told_to_post_anyway(self, app, tmp_path):
        """For files that are gone for good and words worth having."""
        poster = BoardPoster()

        with app.app_context():
            summary = migrate_board(
                poster, self.a_board_with_attachments(), tmp_path / "empty",
                Ledger(tmp_path / "l.jsonl"), require_attachments=False,
            )

        assert summary["posted"] == 2
        assert summary["waiting"] == 0
