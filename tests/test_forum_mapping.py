"""Filing the old board by somebody's decisions rather than by its own tree.

The other importer recreates the categories MyBB had, which is the right shape
for a test and the wrong one to keep: most of a decade-old board is lectures
that no longer run. This one reads the worksheet's JSON -- 246 old forums,
sorted by hand into 93 lectures that still run and an archive -- and files
every thread where it was told to.

What matters here is that nothing is lost in the sorting: a forum missing from
the mapping, a target that names no semester, a decision never made. Each of
those is a way for threads to go somewhere nobody meant, and each is archived
and reported rather than dropped or guessed at.
"""
import pytest

from aeronautics_members.services.forum_board import Ledger, migrate_board
from aeronautics_members.services.forum_mapping import (
    ARCHIVE_ROOT,
    degree_of,
    mapping_plan,
    read_mapping,
    split_target,
    titles_for,
)


def a_mapping(*rows):
    return {"mapping": [
        {"old_fid": fid, "old_path": old_path, "target": target, "decided": decided}
        for fid, old_path, target, decided in rows
    ]}


FORUMS = [
    {"fid": "1", "pid": "0", "name": "Bachelor"},
    {"fid": "2", "pid": "1", "name": "Viertes Semester (NEW since SS18)"},
    {"fid": "10", "pid": "2", "name": "04-09 Mensch-Maschine-Interaktion"},
    {"fid": "11", "pid": "2", "name": "04-02 Angewandte Thermodynamik"},
    {"fid": "12", "pid": "2", "name": "04-02 Thermodynamik 2"},
]
THREADS = [
    {"tid": "1", "fid": "10", "subject": "Klausuren", "firstpost": "1",
     "dateline": "1490000000"},
    {"tid": "2", "fid": "11", "subject": "Klausuren", "firstpost": "2",
     "dateline": "1590000000"},
    {"tid": "3", "fid": "12", "subject": "Klausuren", "firstpost": "3",
     "dateline": "1690000000"},
]


class TestReadingWhatSomebodyDecided:
    def test_a_lecture_becomes_semester_and_lecture(self):
        payload = a_mapping(
            ("10", "Bachelor / Viertes Semester", "Bachelor 4. Semester / Mechanik 2", True),
        )

        placement = read_mapping(payload)

        assert placement["paths"]["10"] == ["Bachelor 4. Semester", "Mechanik 2"]
        assert placement["archived"] == set()

    def test_the_archive_is_split_by_degree(self):
        """A master's student looking for old material has no use for six
        semesters of bachelor's lectures."""
        payload = a_mapping(
            ("10", "Bachelor / Erstes Semester / Physik", "ARCHIVE", True),
            ("20", "Master / Second Semester / CFD", "ARCHIVE", True),
            ("30", "Sonstiges / Kaffeemaschine", "ARCHIVE", True),
        )

        paths = read_mapping(payload)["paths"]

        assert paths["10"] == [ARCHIVE_ROOT, "Bachelor"]
        assert paths["20"] == [ARCHIVE_ROOT, "Master"]
        assert paths["30"] == [ARCHIVE_ROOT, "Allgemein"]

    def test_a_lecture_name_with_a_slash_survives(self):
        """"CNS/ATM Systems" is a lecture. The semester is the first field."""
        assert split_target("Master 1. Semester / CNS/ATM Systems") == [
            "Master 1. Semester", "CNS/ATM Systems",
        ]
        assert split_target(
            "Master 3. Semester / Professional Internship (Seminar / Advising)"
        ) == ["Master 3. Semester", "Professional Internship (Seminar / Advising)"]

    def test_something_never_decided_is_archived_and_counted(self):
        """The worksheet's own default, and not having decided means the same.

        Counted because a half-finished file must not look finished.
        """
        payload = a_mapping(("10", "Bachelor / Physik", "", False))

        placement = read_mapping(payload)

        assert placement["paths"]["10"] == [ARCHIVE_ROOT, "Bachelor"]
        assert any("never decided" in problem for problem in placement["problems"])

    def test_a_target_with_no_semester_is_archived_and_reported(self):
        """Otherwise a lecture becomes a top-level category of its own."""
        payload = a_mapping(("10", "Bachelor / Physik", "Mechanik 2", True))

        placement = read_mapping(payload)

        assert placement["paths"]["10"] == [ARCHIVE_ROOT, "Bachelor"]
        assert any("no semester" in problem for problem in placement["problems"])

    def test_a_forum_the_mapping_never_mentions_is_archived_not_dropped(self):
        """The dump is the authority on what exists; the mapping may lag it.

        Dropping it would lose its threads without a word, which is the one
        outcome worth ruling out.
        """
        payload = a_mapping(
            ("10", "Bachelor / Viertes", "Bachelor 4. Semester / Mechanik 2", True),
        )

        placement = read_mapping(payload, FORUMS, THREADS)

        assert placement["paths"]["11"] == [ARCHIVE_ROOT, "Bachelor"]
        assert placement["paths"]["12"] == [ARCHIVE_ROOT, "Bachelor"]
        assert any("not in the mapping" in problem for problem in placement["problems"])

    def test_a_mapping_for_another_board_is_reported_not_used(self):
        payload = a_mapping(
            ("999", "Somewhere else", "Bachelor 1. Semester / Physik", True),
        )

        placement = read_mapping(payload, FORUMS, THREADS)

        assert "999" not in placement["paths"]
        assert any("not in this dump" in problem for problem in placement["problems"])

    def test_a_file_that_is_not_a_worksheet_export_says_so(self):
        with pytest.raises(ValueError, match="Export JSON"):
            read_mapping({"something": "else"})

    @pytest.mark.parametrize("old_path,expected", [
        ("Bachelor / Erstes Semester", "Bachelor"),
        ("Master / Second Semester", "Master"),
        ("Bachelor", "Bachelor"),
        ("", "Allgemein"),
        (None, "Allgemein"),
    ])
    def test_the_degree_comes_from_the_old_path(self, old_path, expected):
        assert degree_of(old_path) == expected


class TestTheCategoriesItMakes:
    def test_several_years_of_one_course_share_one_category(self):
        """Which is the whole point: the new forum has one Thermodynamik."""
        paths = {
            "11": ["Bachelor 4. Semester", "Angewandte Thermodynamik"],
            "12": ["Bachelor 4. Semester", "Angewandte Thermodynamik"],
        }

        plan = mapping_plan(paths, THREADS)
        lecture = [row for row in plan if row["depth"] == 2][0]

        assert sorted(lecture["fids"]) == ["11", "12"]
        assert lecture["threads"] == 2

    def test_it_stays_within_two_levels(self):
        """Three is refused one category at a time, hours into a run."""
        paths = {"10": ["Bachelor 4. Semester", "Mechanik 2"],
                 "11": [ARCHIVE_ROOT, "Bachelor"]}

        plan = mapping_plan(paths, THREADS)

        assert max(row["depth"] for row in plan) == 2

    def test_a_lecture_nobody_ever_posted_in_gets_no_category(self):
        """The new board would open with a hundred empty rooms otherwise."""
        paths = {"10": ["Bachelor 4. Semester", "Mechanik 2"],
                 "99": ["Bachelor 6. Semester", "Nobody posted here"]}

        plan = mapping_plan(paths, THREADS)

        assert not any(row["name"] == "Nobody posted here" for row in plan)

    def test_a_name_too_long_for_discourse_is_cut_to_fit(self):
        paths = {"10": ["Bachelor 4. Semester", "M" * 80]}

        plan = mapping_plan(paths, THREADS)

        assert all(len(row["name"]) <= 50 for row in plan)

    def test_two_lectures_alike_past_the_cut_stay_apart(self):
        """Or one of them loses its threads into the other."""
        paths = {
            "10": ["Bachelor 4. Semester", "Angewandte " + "M" * 60 + " eins"],
            "11": ["Bachelor 4. Semester", "Angewandte " + "M" * 60 + " zwei"],
        }

        plan = mapping_plan(paths, THREADS)
        names = [row["name"] for row in plan if row["depth"] == 2]

        assert len(set(names)) == 2


class TestTheTitlesItGives:
    def test_an_archived_thread_carries_its_lecture(self):
        """One category holds every retired lecture of a degree, and this
        board has ninety-one threads called "Klausuren"."""
        titles = titles_for(FORUMS, THREADS, archived={"10"})

        assert titles["1"].startswith("Klausuren (04-09 Mensch-Maschine")

    def test_a_live_thread_keeps_its_subject_where_it_can(self):
        """Inside the lecture's own category the subject is unambiguous, and
        the category already says what the suffix would."""
        threads = [THREADS[0]]

        titles = titles_for(FORUMS, threads)

        assert titles["1"] == "Klausuren"

    def test_two_live_threads_that_collide_are_still_told_apart(self):
        titles = titles_for(FORUMS, THREADS)

        assert len(set(titles.values())) == 3

    def test_an_archived_and_a_live_thread_do_not_collide_either(self):
        titles = titles_for(FORUMS, THREADS, archived={"10", "11"})

        assert len(set(titles.values())) == 3
        assert all(title for title in titles.values())


class TestPostingIntoThem:
    def a_board(self):
        return {
            "forums": FORUMS,
            "threads": THREADS,
            "posts": [
                {"pid": "1", "tid": "1", "uid": "7", "dateline": "1490000000",
                 "message": "Angabe."},
                {"pid": "2", "tid": "2", "uid": "7", "dateline": "1590000000",
                 "message": "Angabe."},
                {"pid": "3", "tid": "3", "uid": "7", "dateline": "1690000000",
                 "message": "Angabe."},
            ],
            "attachments": [],
            "users": [{"uid": "7", "username": "HoferT_M13"}],
        }

    def a_placement(self):
        return {
            "10": ["Bachelor 4. Semester", "Mensch-Maschine-Interaktion"],
            "11": ["Bachelor 4. Semester", "Angewandte Thermodynamik"],
            "12": ["Bachelor 4. Semester", "Angewandte Thermodynamik"],
        }

    def test_threads_land_in_the_category_they_were_assigned(self, app, tmp_path):
        from test_forum_board_import import BoardPoster

        board = self.a_board()
        paths = self.a_placement()
        poster = BoardPoster()

        with app.app_context():
            summary = migrate_board(
                poster, board, "/nowhere", Ledger(tmp_path / "l.jsonl"),
                fallback_username="system",
                plan=mapping_plan(paths, board["threads"]),
                titles=titles_for(board["forums"], board["threads"]),
            )

        assert summary["posted"] == 3
        assert summary["failed"] == 0
        # the two Thermodynamik forums share one category, the third has its own
        used = {call["category"] for call in poster.calls if call.get("title")}
        assert len(used) == 2
        made = dict(poster.created_categories)
        assert set(made) == {"Bachelor 4. Semester", "Angewandte Thermodynamik",
                             "Mensch-Maschine-Interaktion"}
        assert made["Bachelor 4. Semester"] is None, "the semester is the parent"
        assert made["Angewandte Thermodynamik"] is not None, "the lecture is under it"

    def test_categories_only_makes_them_and_posts_nothing(self, app, tmp_path):
        """For trying a structure against a forum that already holds content,
        where every title is taken and posting would be refusals that teach
        nothing."""
        from test_forum_board_import import BoardPoster

        board = self.a_board()
        poster = BoardPoster()

        with app.app_context():
            summary = migrate_board(
                poster, board, "/nowhere", Ledger(tmp_path / "l.jsonl"),
                fallback_username="system",
                plan=mapping_plan(self.a_placement(), board["threads"]),
                categories_only=True,
            )

        assert summary["posted"] == 0
        assert poster.calls == [], "nothing was posted"
        assert poster.created_categories, "and the categories were made"
        assert dict(poster.created_categories)["Angewandte Thermodynamik"] is not None
        assert summary["categories_made"] >= 3
