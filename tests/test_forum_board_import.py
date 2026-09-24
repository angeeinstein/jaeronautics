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
    categories_by_forum,
    category_nesting_requirement,
    category_plan,
    ensure_categories,
    forum_tree,
    migrate_board,
    settings_inventory,
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

    def plan(self, max_depth=2, board=None):
        board = board or a_board()
        return category_plan(board["forums"], board["threads"], max_depth)

    def test_two_levels_is_what_every_discourse_allows(self):
        """The third is refused by name, one category at a time, far too late."""
        plan = self.plan()

        assert max(row["depth"] for row in plan) == 2

    def test_the_lecture_keeps_its_own_level(self):
        """It is the thing anybody is looking for."""
        plan = {row["name"]: row for row in self.plan()}

        assert plan["Technisches Programmieren"]["depth"] == 2
        assert plan["Technisches Programmieren"]["path"] == [
            "Studium / Bachelor / 3. Semester", "Technisches Programmieren"
        ]

    def test_the_levels_above_it_are_joined_rather_than_dropped(self):
        """Losing "Bachelor" would merge a Bachelor and a Master semester."""
        names = {row["name"] for row in self.plan()}

        assert "Studium / Bachelor / 3. Semester" in names

    def test_three_levels_are_used_where_they_are_available(self):
        plan = {row["name"]: row for row in self.plan(max_depth=3)}

        assert plan["Technisches Programmieren"]["path"] == [
            "Studium / Bachelor", "3. Semester", "Technisches Programmieren"
        ]

    def test_a_forum_nobody_posted_in_gets_no_category(self):
        names = {row["name"] for row in self.plan()}

        assert "Nie benutzt" not in names

    def test_parents_come_before_their_children(self):
        made = []
        for row in self.plan():
            if row["parent_key"]:
                assert row["parent_key"] in made, f"{row['name']} has no parent yet"
            made.append(row["key"])

    def test_a_name_too_long_for_discourse_is_cut_to_fit(self):
        """Fifty characters, and the board has lecture names past seventy."""
        board = a_board()
        board["forums"][3]["name"] = (
            "01-02 Einfuehrung in die Luftfahrt und internationale "
            "Luftfahrtorganisationen"
        )
        names = [row["name"] for row in self.plan(board=board)]

        assert all(len(name) <= 50 for name in names)
        assert any(name.startswith("01-02 Einfuehrung") for name in names)

    def test_two_lectures_alike_past_the_limit_stay_apart(self):
        """Otherwise one of them loses its threads into the other."""
        board = a_board()
        stem = "05-05 Thermische Turbomaschinen und Strahlantriebe"
        board["forums"] += [
            {"fid": "20", "pid": "3", "name": stem + " (Vorlesung)"},
            {"fid": "21", "pid": "3", "name": stem + " (Labor)"},
        ]
        board["threads"] += [
            {"tid": "20", "fid": "20", "subject": "A", "firstpost": "900",
             "dateline": "1"},
            {"tid": "21", "fid": "21", "subject": "B", "firstpost": "901",
             "dateline": "1"},
        ]
        plan = self.plan(board=board)

        under = [row["name"] for row in plan if row["depth"] == 2]
        assert len(set(under)) == len(under), "no two share a name"

    def test_every_used_forum_can_be_found_again(self):
        plan = self.plan()
        made = {row["key"]: 100 + n for n, row in enumerate(plan)}

        assert categories_by_forum(plan, made)["4"] is not None

    def test_the_nesting_setting_asks_for_what_the_plan_needs(self):
        requirement = category_nesting_requirement(self.plan(max_depth=3))

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
        assert names[0] == "Studium / Bachelor / 3. Semester"
        assert len(made) == 2

    def test_a_category_already_there_is_not_made_twice(self, app, tmp_path):
        """A second run after an interruption must not double the tree."""
        top = "Studium / Bachelor / 3. Semester"
        poster = BoardPoster(categories=[
            {"id": 300, "name": top, "parent_category_id": None},
        ])
        plan = category_plan(a_board()["forums"], a_board()["threads"])

        with app.app_context():
            made = ensure_categories(poster, plan, Ledger(tmp_path / "l.jsonl"))

        assert made["path:" + top] == 300
        assert top not in [name for name, _ in poster.created_categories]

    def test_the_ledger_is_believed_before_the_forum_is_asked(self, app, tmp_path):
        poster = BoardPoster()
        ledger = Ledger(tmp_path / "l.jsonl")
        key = "path:Studium / Bachelor / 3. Semester"
        ledger.record_category(key, 42)
        plan = category_plan(a_board()["forums"], a_board()["threads"])

        with app.app_context():
            made = ensure_categories(poster, plan, ledger)

        assert made[key] == 42

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
        poster = self.a_refusing_forum({"Technisches Programmieren"})

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
        poster = self.a_refusing_forum({"Technisches Programmieren"})

        with app.app_context():
            summary = migrate_board(
                poster, board, "/nowhere", Ledger(tmp_path / "l.jsonl"),
            )

        assert summary["posted"] == 1

    def test_a_child_of_a_refused_parent_is_not_tried_on_its_own(self, app, tmp_path):
        """It would land under the wrong parent, or at the top of the forum."""
        poster = self.a_refusing_forum({"Studium / Bachelor / 3. Semester"})

        with app.app_context():
            summary = migrate_board(
                poster, a_board(), "/nowhere", Ledger(tmp_path / "l.jsonl"),
            )

        made = [name for name, _parent in poster.created_categories]
        assert made == []
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


class TestNamesThatDoNotFit:
    """Discourse takes fifty characters and the board has names past seventy."""

    def a_deep_board(self, top="Studium", degree="Bachelor Luftfahrt / Aviation"):
        forums = [
            {"fid": "1", "pid": "0", "name": top},
            {"fid": "2", "pid": "1", "name": degree},
            {"fid": "3", "pid": "2", "name": "01 Semester"},
            {"fid": "4", "pid": "2", "name": "02 Semester"},
            {"fid": "5", "pid": "3", "name": "01-02 Luftfahrtrecht"},
            {"fid": "6", "pid": "4", "name": "02-05 Festigkeitslehre"},
        ]
        threads = [
            {"tid": "1", "fid": "5", "subject": "A", "firstpost": "1", "dateline": "1"},
            {"tid": "2", "fid": "6", "subject": "B", "firstpost": "2", "dateline": "1"},
        ]
        return forums, threads

    def test_the_outermost_level_goes_before_the_innermost_does(self):
        """Cutting the end would take the semester number and keep "Studium"."""
        plan = category_plan(*self.a_deep_board(), 2)
        tops = sorted(row["name"] for row in plan if row["depth"] == 1)

        assert tops == [
            "Bachelor Luftfahrt / Aviation / 01 Semester",
            "Bachelor Luftfahrt / Aviation / 02 Semester",
        ]

    def test_it_keeps_what_fits_whole(self):
        plan = category_plan(*self.a_deep_board(degree="Master"), 2)
        tops = sorted(row["name"] for row in plan if row["depth"] == 1)

        assert tops == ["Studium / Master / 01 Semester",
                        "Studium / Master / 02 Semester"]

    def test_semesters_of_the_same_number_stay_apart(self):
        """The reason the degree is worth keeping in the name at all."""
        forums, threads = self.a_deep_board()
        forums += [
            {"fid": "7", "pid": "1", "name": "Master"},
            {"fid": "8", "pid": "7", "name": "01 Semester"},
            {"fid": "9", "pid": "8", "name": "01-02 Luftfahrtrecht"},
        ]
        threads.append(
            {"tid": "3", "fid": "9", "subject": "C", "firstpost": "3", "dateline": "1"}
        )
        plan = category_plan(forums, threads, 2)

        tops = {row["name"] for row in plan if row["depth"] == 1}
        assert len(tops) == 3
        # Both degrees keep a Luftfahrtrecht, under their own semester.
        rechte = [row for row in plan if row["name"] == "01-02 Luftfahrtrecht"]
        assert len(rechte) == 2
        assert rechte[0]["parent_key"] != rechte[1]["parent_key"]

    def test_a_name_with_nothing_above_it_is_left_alone(self):
        forums = [{"fid": "1", "pid": "0", "name": "Allgemeines"}]
        threads = [{"tid": "1", "fid": "1", "subject": "A",
                    "firstpost": "1", "dateline": "1"}]
        plan = category_plan(forums, threads, 2)

        assert [row["name"] for row in plan] == ["Allgemeines"]
        assert plan[0]["parent_key"] is None


class TestReadingWhatTheForumSaysAboutItself:
    """Two runs were spent on limits that were in this list the whole time."""

    def rows(self):
        return [
            {"setting": "max_category_nesting", "value": "2", "default": "2",
             "category": "categories", "description": "How deep categories go."},
            {"setting": "title", "value": "LAVboard", "default": "Discourse",
             "category": "required", "description": "The name of this site."},
            {"setting": "min_post_length", "value": "20", "default": "20",
             "category": "posting", "description": "Shortest allowed post."},
            {"setting": "s3_secret_access_key", "value": "hunter2",
             "default": "", "secret": True, "category": "files",
             "description": "For the object store."},
            {"setting": "discourse_connect_secret", "value": "abcdef",
             "default": "", "category": "login", "description": "Shared secret."},
        ]

    def test_it_keeps_everything_the_forum_reported(self):
        everything, _ = settings_inventory(self.rows())

        assert [entry["setting"] for entry in everything] == [
            "discourse_connect_secret", "max_category_nesting",
            "min_post_length", "s3_secret_access_key", "title",
        ]

    def test_it_marks_the_ones_that_constrain_an_import(self):
        _, interesting = settings_inventory(self.rows())

        assert [entry["setting"] for entry in interesting] == [
            "max_category_nesting", "min_post_length",
        ]
        assert "title" not in [entry["setting"] for entry in interesting]

    def test_a_secret_discourse_marks_is_left_out(self):
        everything = {e["setting"]: e for e in settings_inventory(self.rows())[0]}

        assert everything["s3_secret_access_key"]["value"] == "(hidden)"

    def test_and_one_it_does_not_mark_but_is_named_like_one(self):
        """The file is meant to be shareable, so the name is checked too."""
        everything = {e["setting"]: e for e in settings_inventory(self.rows())[0]}

        assert everything["discourse_connect_secret"]["value"] == "(hidden)"

    def test_an_ordinary_setting_keeps_its_value(self):
        everything = {e["setting"]: e for e in settings_inventory(self.rows())[0]}

        assert everything["title"]["value"] == "LAVboard"
        assert everything["title"]["description"] == "The name of this site."

    def test_the_description_is_kept_because_that_is_the_point(self):
        _, interesting = settings_inventory(self.rows())

        assert interesting[0]["description"] == "How deep categories go."
