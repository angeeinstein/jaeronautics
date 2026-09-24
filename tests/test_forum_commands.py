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

    def test_inspecting_attachments_prints_the_recorded_name(self, app, dump):
        with app.app_context():
            result = run(app, "inspect-forum-attachments", [str(dump)])

        assert result.exit_code == 0, result.output
        assert "Klausur_LAV16.pdf" in result.output
        assert "201703/a.attach" in result.output

    @pytest.mark.parametrize("name", [
        "forum-category-worksheet", "check-forum-uploads", "check-forum-settings",
        "inspect-forum-attachments", "import-forum-content", "migrate-forum-thread",
        "restore-forum-settings", "dump-forum-settings",
    ])
    def test_every_one_of_them_has_help(self, app, name):
        """Which is enough to catch a decorator that does not match its function."""
        result = run(app, name, ["--help"])

        assert result.exit_code == 0
        assert result.output.strip()
