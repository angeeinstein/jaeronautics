"""The forum migration commands, run as commands.

Every one of these is exercised elsewhere at the level of its functions, and
one still shipped broken: a careless text edit took an argument off a call
site, every unit test went on passing because they call the function directly,
and the failure arrived on somebody else's server.

So these are deliberately shallow. They do not check what the commands decide
-- that is tested properly in the other files -- only that each one can be
invoked at all, which is the part nothing else covers.
"""
import gzip

import pytest
from click.testing import CliRunner


@pytest.fixture
def dump(tmp_path):
    """A MyBB dump small enough to read and shaped the way mysqldump writes.

    The shape matters: columns are read out of CREATE TABLE because mysqldump
    leaves them off the INSERT, and it only looks for them between the opening
    bracket and ") ENGINE". A tidier fixture parses as nothing at all, which
    would make these tests pass against an empty board.
    """
    def table(name, columns, rows):
        lines = ",\n".join(f"  `{column}` {kind}" for column, kind in columns)
        return (
            f"DROP TABLE IF EXISTS `{name}`;\n"
            f"CREATE TABLE `{name}` (\n{lines}\n) ENGINE=InnoDB "
            f"DEFAULT CHARSET=utf8mb4;\n"
            f"INSERT INTO `{name}` VALUES {rows};\n"
        )

    sql = "".join([
        table("mybb_forums", [("fid", "int"), ("pid", "int"), ("name", "varchar(120)")],
              "(1,0,'Studium'),(2,1,'01 Semester'),"
              "(3,2,'01-06 Technisches Programmieren')"),
        table("mybb_threads",
              [("tid", "int"), ("fid", "int"), ("subject", "varchar(120)"),
               ("firstpost", "int"), ("dateline", "int")],
              "(1,3,'Klausuren',1,1490000000)"),
        table("mybb_posts",
              [("pid", "int"), ("tid", "int"), ("uid", "int"),
               ("dateline", "int"), ("message", "text")],
              "(1,1,7,1490000000,'Hier die Angabe.'),(2,1,8,1490000100,'Danke!')"),
        table("mybb_users", [("uid", "int"), ("username", "varchar(80)")],
              "(7,'HoferT_M13'),(8,'LutzB_L21')"),
        table("mybb_attachments",
              [("aid", "int"), ("pid", "int"), ("attachname", "varchar(120)"),
               ("filename", "varchar(120)"), ("filetype", "varchar(80)"),
               ("filesize", "int")],
              "(1,1,'201703/a.attach','Klausur_LAV16.pdf','application/pdf',2048)"),
    ])
    path = tmp_path / "dump.sql.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(sql)
    return path


def run(app, name, args):
    return CliRunner().invoke(app.cli.commands[name], args, catch_exceptions=False)


class FakeDiscourse:
    """A forum that answers the way the real one does, wrong parts included.

    Specifically: the SSO endpoint only puts somebody in a group that already
    exists, and says nothing about the names it dropped. A fake that took the
    payload at its word would have gone on passing while the real forum ended
    up with thirty-four empty groups.
    """

    def __init__(self):
        self.published = []
        self.groups = {}
        self.members = {}

    def sync_imported_profile(self, payload):
        self.published.append(payload)
        for name in filter(None, payload.get("add_groups", "").split(",")):
            if name in self.groups:
                self.members.setdefault(name, set()).add(payload["username"])
        return {}

    def ensure_group(self, name):
        if name in self.groups:
            return {"id": self.groups[name], "name": name}, False
        self.groups[name] = len(self.groups) + 1
        self.members.setdefault(name, set())
        return {"id": self.groups[name], "name": name}, True

    def add_group_members(self, group_id, usernames):
        name = next(key for key, value in self.groups.items() if value == group_id)
        self.members.setdefault(name, set()).update(usernames)
        return len(usernames)

    def ensure_user_field(self, name, description):
        return "user_field_1", True

    def find_user_field(self, name):
        return "user_field_1"


class FakeForumService:
    def __init__(self, provider, settings=None):
        self.provider = provider
        self.config_errors = []
        self.settings = settings or {
            "forum_base_url": "https://forum.example.at",
            "discourse_api_key": "c" * 64,
            "discourse_api_username": "system",
        }

    def is_enabled(self):
        return True


class TestTheCommandsCanBeRun:
    def test_the_worksheet_writes_a_page(self, app, dump, tmp_path):
        out = tmp_path / "categories.html"

        with app.app_context():
            result = run(app, "forum-category-worksheet", [str(dump), "--out", str(out)])

        assert result.exit_code == 0, result.output
        page = out.read_text(encoding="utf-8")
        assert "01-06 Technisches Programmieren" in page
        assert "Klausur_LAV16.pdf" in page, "what is in the forum travels with it"

    def test_the_worksheet_can_still_write_a_csv(self, app, dump, tmp_path):
        out = tmp_path / "categories.csv"

        with app.app_context():
            result = run(app, "forum-category-worksheet",
                         [str(dump), "--out", str(out), "--csv"])

        assert result.exit_code == 0, result.output
        lines = out.read_text(encoding="utf-8-sig").splitlines()
        assert lines[0].startswith("old_fid,old_path,lecture")
        assert "01-06 Technisches Programmieren" in lines[1]

    def test_the_uploads_check_reports_what_is_missing(self, app, dump, tmp_path):
        missing = tmp_path / "still-needed.txt"

        with app.app_context():
            result = CliRunner().invoke(
                app.cli.commands["check-forum-uploads"],
                [str(dump), "--uploads", str(tmp_path / "nothing"),
                 "--missing-to", str(missing)],
            )

        assert result.exit_code == 1, "it exits non-zero while anything is out"
        assert missing.read_text(encoding="utf-8").strip() == "201703/a.attach"

    def test_the_uploads_check_is_happy_when_everything_is_there(self, app, dump, tmp_path):
        here = tmp_path / "uploads" / "201703"
        here.mkdir(parents=True)
        (here / "a.attach").write_bytes(b"x" * 2048)

        with app.app_context():
            result = CliRunner().invoke(
                app.cli.commands["check-forum-uploads"],
                [str(dump), "--uploads", str(tmp_path / "uploads")],
            )

        assert result.exit_code == 0, result.output
        assert "Everything is here" in result.output

    def test_inspecting_a_post_shows_both_versions_of_it(self, app, dump):
        """Because "Body is too short" against a post that is not is a guess."""
        with app.app_context():
            result = run(app, "inspect-forum-post", [str(dump), "--pid", "1"])

        assert result.exit_code == 0, result.output
        assert "Hier die Angabe." in result.output
        assert "HoferT_M13" in result.output
        assert "opening post" in result.output

    def test_inspecting_a_post_that_is_not_there_says_so(self, app, dump):
        with app.app_context():
            result = CliRunner().invoke(
                app.cli.commands["inspect-forum-post"], [str(dump), "--pid", "9999"]
            )

        assert result.exit_code != 0

    def test_inspecting_attachments_prints_the_recorded_name(self, app, dump):
        with app.app_context():
            result = run(app, "inspect-forum-attachments", [str(dump)])

        assert result.exit_code == 0, result.output
        assert "Klausur_LAV16.pdf" in result.output
        assert "201703/a.attach" in result.output

    def test_publishing_profiles_leaves_people_in_their_groups(self, app, monkeypatch):
        """The one thing the register is for, and the one thing it did not do.

        ``FakeDiscourse`` ignores a group it has never heard of, which is what
        the real endpoint does. Against a fake that simply believed the payload,
        the broken order -- publish, then make the groups -- passed.
        """
        from conftest import db
        from aeronautics_members.services.forum_import import import_forum_people

        provider = FakeDiscourse()
        monkeypatch.setattr("aeronautics_members.app.get_forum_service",
                            lambda: FakeForumService(provider))
        with app.app_context():
            import_forum_people([{
                "source_user_id": "7", "source_username": "HoferT_M13",
                "source_email": "t.hofer@edu.fh-joanneum.at",
                "year_group": "MAV13", "post_count": 3,
            }])
            db.session.commit()

            result = run(app, "publish-forum-profiles", [])

        assert result.exit_code == 0, result.output
        assert provider.members["old_forum"] == {"HoferT_M13"}
        assert provider.members["mav13"] == {"HoferT_M13"}

    def test_a_forum_that_refuses_every_signature_stops_the_run(
            self, app, monkeypatch):
        """Found for real: 740 refusals, then 34 more about the groups.

        A Connect secret that differs between the two sides fails every person
        identically, and Discourse only says "Login Error". So the run stops at
        the fifth, names the secret, and does not go on to fill groups with
        people the forum has never heard of.
        """
        from conftest import db
        from aeronautics_members.forum_service import ForumProviderError
        from aeronautics_members.services.forum_import import import_forum_people

        class WrongSecret(FakeDiscourse):
            def sync_imported_profile(self, payload):
                raise ForumProviderError(
                    'Discourse API request failed (422): '
                    '{"failed":"FAILED","message":"Login Error"}'
                )

            def add_group_members(self, group_id, usernames):
                raise AssertionError("nobody got through; nobody to add")

        provider = WrongSecret()
        monkeypatch.setattr("aeronautics_members.app.get_forum_service",
                            lambda: FakeForumService(provider))
        with app.app_context():
            import_forum_people([{
                "source_user_id": str(uid), "source_username": f"P{uid}_L21",
                "source_email": f"p{uid}@edu.fh-joanneum.at",
                "year_group": "LAV21", "post_count": 0,
            } for uid in range(1, 12)])
            db.session.commit()

            result = CliRunner().invoke(
                app.cli.commands["publish-forum-profiles"], [])

        assert result.exit_code != 0
        assert "DiscourseConnect secret" in result.output
        assert "10/11" not in result.output, "it stopped, it did not carry on"

    def test_the_groups_can_be_repaired_without_publishing_again(self, app, monkeypatch):
        """739 profiles is half an hour; their group membership is one minute."""
        from conftest import db
        from aeronautics_members.services.forum_import import import_forum_people

        provider = FakeDiscourse()
        monkeypatch.setattr("aeronautics_members.app.get_forum_service",
                            lambda: FakeForumService(provider))
        with app.app_context():
            import_forum_people([{
                "source_user_id": "8", "source_username": "LutzB_L21",
                "source_email": "b.lutz@edu.fh-joanneum.at",
                "year_group": "LAV21", "post_count": 0,
            }])
            db.session.commit()

            result = run(app, "publish-forum-profiles", ["--groups-only"])

        assert result.exit_code == 0, result.output
        assert provider.published == [], "nobody is published again"
        assert provider.members["lav21"] == {"LutzB_L21"}

    def test_the_worksheet_server_explains_both_ways_to_reach_it(self, app):
        """One machine serving, another looking at it, and no password on it."""
        result = run(app, "serve-forum-worksheet", ["--help"])

        assert result.exit_code == 0
        assert "ssh -N -L" in result.output
        assert "0.0.0.0" in result.output
        assert "no password" in result.output

    def test_the_mapping_is_read_and_its_categories_made(self, app, dump, tmp_path,
                                                        monkeypatch):
        """The whole command, on the path the production run will take.

        Every piece of this is tested on its own; what is not, anywhere else,
        is that the command passes the mapping to the importer at all. A call
        site that did not was how this project last shipped something broken.
        """
        import json as _json
        from test_forum_board_import import BoardPoster

        mapping = tmp_path / "categories.json"
        mapping.write_text(_json.dumps({"mapping": [{
            "old_fid": "3",
            "old_path": "Studium / 01 Semester / 01-06 Technisches Programmieren",
            "target": "Bachelor 1. Semester / Technisches Programmieren 1",
            "decided": True,
        }]}), encoding="utf-8")

        poster = BoardPoster()
        # The command asks the poster about its key before doing anything, and
        # a fake that cannot answer is a fake, not a finding.
        poster._key_complaint = lambda: None
        poster.find_author = None
        monkeypatch.setattr("aeronautics_members.app.ContentPoster",
                            lambda settings: poster)
        monkeypatch.setattr("aeronautics_members.app.get_forum_service",
                            lambda: FakeForumService(poster))

        with app.app_context():
            result = run(app, "import-forum-content", [
                str(dump), "--uploads", str(tmp_path),
                "--ledger", str(tmp_path / "l.jsonl"),
                "--mapping", str(mapping), "--categories-only",
            ])

        assert result.exit_code == 0, result.output
        assert "Mapping read: 1 lectures" in result.output
        assert "2 categories on the forum, 2 of them made by this run" in result.output
        assert poster.settings_written == [], (
            "a run that posts nothing has no reason to change a setting"
        )
        made = dict(poster.created_categories)
        assert set(made) == {"Bachelor 1. Semester", "Technisches Programmieren 1"}
        assert made["Bachelor 1. Semester"] is None, "the semester is the parent"
        assert poster.calls == [], "and nothing was posted"

    def _a_stale_ledger(self, tmp_path):
        path = tmp_path / "l.jsonl"
        path.write_text(
            "\n".join('{"kind": "post", "key": "%d", "id": %d}' % (n, 900 + n)
                      for n in range(10)) + "\n",
            encoding="utf-8",
        )
        return path

    def _a_forum_that_knows_nothing(self, monkeypatch):
        from aeronautics_members.forum_service import ForumProviderError
        from test_forum_board_import import BoardPoster

        poster = BoardPoster()
        poster._key_complaint = lambda: None
        poster.find_author = None

        def gone(post_id):
            raise ForumProviderError("GET /posts.json failed (404): {}")

        poster.read_post = gone
        monkeypatch.setattr("aeronautics_members.app.ContentPoster",
                            lambda settings: poster)
        monkeypatch.setattr("aeronautics_members.app.get_forum_service",
                            lambda: FakeForumService(poster))
        return poster

    def test_a_ledger_from_a_forum_that_was_reset_stops_the_run(
            self, app, dump, tmp_path, monkeypatch):
        """Otherwise it skips all 1,517 posts and reports a clean run.

        The forum is reset several times before the real import; the ledger
        outlives it, and nothing about the resulting empty archive looks like
        a failure.
        """
        self._a_forum_that_knows_nothing(monkeypatch)
        ledger = self._a_stale_ledger(tmp_path)

        with app.app_context():
            result = CliRunner().invoke(app.cli.commands["import-forum-content"], [
                str(dump), "--uploads", str(tmp_path),
                "--ledger", str(ledger), "--categories-only",
            ])

        assert result.exit_code != 0
        assert "does not describe this forum" in result.output
        assert "--reset-ledger" in result.output
        assert ledger.exists(), "and it is not thrown away"

    def test_reset_ledger_moves_it_aside_and_carries_on(
            self, app, dump, tmp_path, monkeypatch):
        """Renamed, not deleted: it is the only record of a run that happened."""
        self._a_forum_that_knows_nothing(monkeypatch)
        ledger = self._a_stale_ledger(tmp_path)

        with app.app_context():
            result = run(app, "import-forum-content", [
                str(dump), "--uploads", str(tmp_path),
                "--ledger", str(ledger), "--categories-only", "--reset-ledger",
            ])

        assert result.exit_code == 0, result.output
        assert "starting a fresh record" in result.output
        kept = list(tmp_path.glob("l.jsonl.stale-*"))
        assert len(kept) == 1, "the old one is kept under another name"

    @pytest.mark.parametrize("name", [
        "forum-category-worksheet", "check-forum-uploads", "check-forum-settings",
        "inspect-forum-attachments", "import-forum-content", "migrate-forum-thread",
        "restore-forum-settings", "dump-forum-settings", "publish-forum-profiles",
        "import-forum-people", "inspect-forum-post", "serve-forum-worksheet",
    ])
    def test_every_one_of_them_has_help(self, app, name):
        """Which is enough to catch a decorator that does not match its function."""
        result = run(app, name, ["--help"])

        assert result.exit_code == 0
        assert result.output.strip()
