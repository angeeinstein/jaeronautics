"""The MyBB dump converter.

It runs on somebody's laptop against a file full of six hundred real email
addresses, so the failure to avoid is the quiet one: producing plausible JSON
with the year groups silently missing, or half the rows dropped because one
biography contained an apostrophe.

The fixture below is a real ``mysqldump`` in miniature -- backticks, no column
names on the INSERT, multiple tuples per statement, NULLs, escaped quotes, a
non-default table prefix and a zero ``lastpost`` for somebody who never posted.
"""
import gzip
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import mybb_export  # noqa: E402


DUMP = """
-- MySQL dump 10.13
-- Tapatalk adds its own users table. Picking the first CREATE TABLE ending in
-- "users" found this one on the real forum and exported nobody.
CREATE TABLE `mybb_tapatalk_users` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `push_type` varchar(20) NOT NULL DEFAULT '',
  PRIMARY KEY (`id`)
) ENGINE=MyISAM;

INSERT INTO `mybb_tapatalk_users` VALUES (1,'apns');

CREATE TABLE `lav_profilefields` (
  `fid` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(200) NOT NULL DEFAULT '',
  `type` text NOT NULL,
  PRIMARY KEY (`fid`)
) ENGINE=MyISAM;

INSERT INTO `lav_profilefields` VALUES (1,'Sex','select'),(3,'Jahrgang','text');

CREATE TABLE `lav_posts` (
  `pid` int(10) unsigned NOT NULL AUTO_INCREMENT,
  PRIMARY KEY (`pid`)
) ENGINE=MyISAM;

CREATE TABLE `lav_threads` (
  `tid` int(10) unsigned NOT NULL AUTO_INCREMENT,
  PRIMARY KEY (`tid`)
) ENGINE=MyISAM;

CREATE TABLE `lav_users` (
  `uid` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `username` varchar(120) NOT NULL DEFAULT '',
  `email` varchar(220) NOT NULL DEFAULT '',
  `usergroup` int(10) NOT NULL DEFAULT '2',
  `postnum` int(10) NOT NULL DEFAULT '0',
  `regdate` bigint(30) NOT NULL DEFAULT '0',
  `lastpost` bigint(30) NOT NULL DEFAULT '0',
  `avatar` varchar(200) NOT NULL DEFAULT '',
  `usernotes` text NOT NULL,
  PRIMARY KEY (`uid`)
) ENGINE=MyISAM;

INSERT INTO `lav_users` VALUES (142,'PopovicA_L23','a.popovic@edu.fh-joanneum.at',2,2,1700694000,1717286400,'./uploads/avatars/avatar_142.jpg?dateline=1700694000','fine'),(7,'OBrienC_M20','c.obrien@edu.fh-joanneum.at',7,41,1580000000,0,'','note with an \\'apostrophe\\' in it');
INSERT INTO `lav_users` VALUES (9,'RemoteR_L21','r.remote@edu.fh-joanneum.at',7,3,1600000000,1600000001,'https://example.com/avatar.png',NULL);

CREATE TABLE `lav_userfields` (
  `ufid` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `fid1` text NOT NULL,
  `fid3` text NOT NULL,
  PRIMARY KEY (`ufid`)
) ENGINE=MyISAM;

INSERT INTO `lav_userfields` VALUES (142,'Undisclosed','LAV23'),(7,'','MAV20'),(9,'','');

CREATE TABLE `lav_usergroups` (
  `gid` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `title` varchar(120) NOT NULL DEFAULT '',
  PRIMARY KEY (`gid`)
) ENGINE=MyISAM;

INSERT INTO `lav_usergroups` VALUES (2,'Registered'),(4,'Administrators'),(7,'Banned');

CREATE TABLE `lav_banned` (
  `uid` int(10) unsigned NOT NULL,
  `reason` varchar(255) NOT NULL DEFAULT '',
  `lifted` bigint(30) NOT NULL DEFAULT '0',
  PRIMARY KEY (`uid`)
) ENGINE=MyISAM;

INSERT INTO `lav_banned` VALUES (7,'non active student',0),(9,'',0);
"""


@pytest.fixture
def people():
    return mybb_export.build_people(DUMP)


def test_it_finds_the_table_prefix(people):
    _rows, summary = people
    assert summary["prefix"] == "lav_"
    assert summary["users_table"] == "lav_users"


def test_a_plugins_users_table_does_not_win(people):
    """Tapatalk's mybb_tapatalk_users has no uid and exported nobody.

    The prefix is chosen by which candidate also has userfields, profilefields,
    posts and threads beside it -- a plugin table has none of them.
    """
    _rows, summary = people

    assert summary["users_table"] != "mybb_tapatalk_users"
    assert "mybb_tapatalk_" in summary["rejected_prefixes"]


def test_an_explicit_prefix_overrides_detection():
    _rows, summary = mybb_export.build_people(DUMP, prefix="lav_")
    assert summary["users_table"] == "lav_users"


def test_finding_nobody_exits_nonzero(tmp_path):
    """Zero people is an error, not a successful export of nothing."""
    dump = tmp_path / "empty.sql"
    dump.write_text("CREATE TABLE `x_users` (\n  `uid` int\n) ENGINE=MyISAM;\n")
    assert mybb_export.main([str(dump), "--out", str(tmp_path / "out.json")]) == 1


def test_it_finds_the_jahrgang_field_without_being_told(people):
    """The column number differs per installation, so it must be looked up."""
    _rows, summary = people
    assert summary["jahrgang_field"] == "fid3"
    assert summary["jahrgang_label"] == "Jahrgang"


def test_every_user_survives_the_parse(people):
    """Including the one whose note contains an escaped apostrophe."""
    rows, _summary = people
    assert {person["source_username"] for person in rows} == {
        "PopovicA_L23", "OBrienC_M20", "RemoteR_L21"
    }


def test_timestamps_are_read_in_the_board_s_timezone(people):
    """1700694000 is 22 Nov 23:00 UTC, which is the 23rd in Vienna.

    The old forum has shown that profile as "Thursday, 23. November 2023" for
    years. Converting in UTC would move every late-evening registration a day
    earlier than the record everybody remembers.
    """
    rows, _summary = people
    anna = next(p for p in rows if p["source_user_id"] == "142")

    assert anna["joined_on"] == "2023-11-23"

    utc_rows, _ = mybb_export.build_people(DUMP, timezone_name="UTC")
    assert next(p for p in utc_rows if p["source_user_id"] == "142")["joined_on"] == "2023-11-22"


def test_the_fields_line_up(people):
    rows, _summary = people
    anna = next(p for p in rows if p["source_user_id"] == "142")

    assert anna["source_email"] == "a.popovic@edu.fh-joanneum.at"
    assert anna["year_group"] == "LAV23"
    assert anna["post_count"] == 2
    assert anna["joined_on"] == "2023-11-23"
    assert anna["last_posted_on"] == "2024-06-02"
    assert anna["avatar_file"] == "avatar_142.jpg"


def test_never_posted_is_empty_rather_than_1970(people):
    """lastpost is 0 for somebody who never posted, not a date in 1970."""
    rows, _summary = people
    conor = next(p for p in rows if p["source_user_id"] == "7")

    assert conor["last_posted_on"] is None
    assert conor["joined_on"] == "2020-01-26"
    assert conor["year_group"] == "MAV20"


def test_an_empty_avatar_yields_no_file(people):
    rows, _summary = people
    conor = next(p for p in rows if p["source_user_id"] == "7")

    assert conor["avatar_file"] is None


def test_a_remote_avatar_is_counted_but_not_named_as_a_file(people):
    """Older MyBB allowed hosting the image elsewhere; there is nothing to copy."""
    rows, summary = people
    remote = next(p for p in rows if p["source_user_id"] == "9")

    assert remote["avatar_file"] is None
    assert summary["remote_avatars"] == 1


def test_an_empty_year_group_is_none_not_an_empty_string(people):
    rows, _summary = people
    remote = next(p for p in rows if p["source_user_id"] == "9")

    assert remote["year_group"] is None


def test_it_says_where_the_avatar_files_live(people):
    """Guessing the directory means downloading years of attachments instead."""
    _rows, summary = people

    assert summary["avatar_directories"] == [("uploads/avatars", 1)]


def test_the_summary_counts_what_was_found(people):
    _rows, summary = people
    assert summary["with_year_group"] == 2
    assert summary["with_avatar"] == 1


def test_an_explicit_field_overrides_the_lookup():
    _rows, summary = mybb_export.build_people(DUMP, jahrgang_field="fid1")
    assert summary["jahrgang_field"] == "fid1"


def test_the_output_feeds_the_importer(app, tmp_path):
    """The two halves have to agree, or this is a file nothing reads."""
    from aeronautics_members.services.forum_import import import_forum_people
    from conftest import db

    rows, _summary = mybb_export.build_people(DUMP)
    report = import_forum_people(rows)
    db.session.commit()

    assert report["created"] == 3
    assert report["skipped"] == 0
    # The one with no exported year group falls back to its username suffix.
    from aeronautics_members.db_models import ImportedForumProfile
    remote = db.session.execute(
        db.select(ImportedForumProfile).filter_by(source_user_id="9")
    ).scalar_one()
    assert remote.year_group == "LAV21"


class TestTheCommandLine:
    def test_it_writes_json(self, tmp_path, capsys):
        dump = tmp_path / "backup.sql"
        dump.write_text(DUMP)
        out = tmp_path / "people.json"

        assert mybb_export.main([str(dump), "--out", str(out)]) == 0

        written = json.loads(out.read_text())
        assert len(written) == 3
        assert "fid3" in capsys.readouterr().out

    def test_it_reads_a_gzipped_dump(self, tmp_path):
        dump = tmp_path / "backup.sql.gz"
        with gzip.open(dump, "wt", encoding="utf-8") as handle:
            handle.write(DUMP)
        out = tmp_path / "people.json"

        mybb_export.main([str(dump), "--out", str(out)])

        assert len(json.loads(out.read_text())) == 3


class TestWhoWasStillActive:
    """On this forum "Banned" is how a graduating member was deactivated.

    The ban reasons in the real dump say so outright -- "non active student",
    "Not active student/exchange semester", "Is now a Lecturer" -- and 220 of
    the 230 banned accounts follow the university naming convention, 188 have
    avatars and 89 have posts. That is not spam. It is the only record of who
    left and why, it exists nowhere but this dump, and switching the old forum
    off destroys it. So carry it, whatever is eventually done with it.
    """

    def test_the_group_comes_across_by_name(self, people):
        rows, _summary = people
        by_uid = {person["source_user_id"]: person for person in rows}

        assert by_uid["142"]["source_group"] == "Registered"
        assert by_uid["7"]["source_group"] == "Banned"

    def test_the_reason_an_account_was_closed_is_kept(self, people):
        """"non active student" is the whole point; the group alone loses it."""
        rows, _summary = people
        conor = next(p for p in rows if p["source_user_id"] == "7")

        assert conor["source_group_reason"] == "non active student"

    def test_an_empty_reason_is_none_rather_than_an_empty_string(self, people):
        rows, _summary = people
        remote = next(p for p in rows if p["source_user_id"] == "9")

        assert remote["source_group"] == "Banned"
        assert remote["source_group_reason"] is None

    def test_somebody_never_banned_has_no_reason(self, people):
        rows, _summary = people
        anna = next(p for p in rows if p["source_user_id"] == "142")

        assert anna["source_group_reason"] is None

    def test_the_summary_counts_the_groups(self, people):
        """So the split is visible before importing, not after."""
        _rows, summary = people

        assert dict(summary["group_counts"]) == {"Banned": 2, "Registered": 1}

    def test_a_dump_without_the_groups_table_still_exports(self):
        """Nothing here is worth failing an import over."""
        without = DUMP.replace("INSERT INTO `lav_usergroups`", "INSERT INTO `other_x`")

        rows, _summary = mybb_export.build_people(without)

        assert len(rows) == 3
        # Falls back to the raw gid rather than inventing a name.
        assert next(p for p in rows if p["source_user_id"] == "7")["source_group"] == "7"

    def test_the_importer_ignores_what_it_has_not_been_taught(self, app):
        """These two fields ride along unused until somebody decides on them."""
        from aeronautics_members.services.forum_import import import_forum_people
        from conftest import db

        rows, _summary = mybb_export.build_people(DUMP)
        report = import_forum_people(rows)
        db.session.commit()

        assert report["created"] == 3
        assert report["problems"] == []


class TestTheDialectMyBBActuallyWrites:
    """MyBB's own backup tool does not shell out to mysqldump.

    It builds the SQL itself, and the result is not what the fixture above
    looks like: the table name arrives bare while the column names are
    backticked. The first converter required backticks on the table, matched
    zero statements against the real 1 MB dump, and reported "people: 0"
    with no error at all.
    """

    REAL = (
        "CREATE TABLE `mybb_users` (\n"
        "  `uid` int(10) unsigned NOT NULL AUTO_INCREMENT,\n"
        "  `username` varchar(120) NOT NULL DEFAULT '',\n"
        "  `email` varchar(220) NOT NULL DEFAULT '',\n"
        "  `postnum` int(10) NOT NULL DEFAULT '0',\n"
        "  `regdate` bigint(30) NOT NULL DEFAULT '0',\n"
        "  `lastpost` bigint(30) NOT NULL DEFAULT '0',\n"
        "  `avatar` varchar(200) NOT NULL DEFAULT '',\n"
        "  PRIMARY KEY (`uid`)\n"
        ") ENGINE=MyISAM;\n"
        "INSERT INTO mybb_users (`uid`,`username`,`email`,`postnum`,`regdate`,"
        "`lastpost`,`avatar`) VALUES (5,'HuberT_L19','t.huber@edu.fh-joanneum.at',"
        "8,1560000000,1590000000,'./uploads/avatars/avatar_5.jpg?dateline=1560000000');\n"
    )

    def test_a_bare_table_name_with_backticked_columns_is_read(self):
        rows = mybb_export.rows_of(self.REAL, "mybb_users")

        assert [row["username"] for row in rows] == ["HuberT_L19"]

    def test_the_columns_come_from_the_statement_not_the_create(self):
        """The INSERT lists its own columns; they are what the values line up with."""
        rows = mybb_export.rows_of(self.REAL, "mybb_users")

        assert rows[0]["uid"] == "5"
        assert rows[0]["avatar"].endswith("avatar_5.jpg?dateline=1560000000")


class TestRowsThatCannotBeRead:
    """Dropping rows silently is the failure this converter must not have."""

    def test_the_summary_says_how_many_rows_went_unread(self):
        """A statement with the wrong number of values is skipped, so it is counted."""
        broken = DUMP + (
            "INSERT INTO `lav_users` (`uid`,`username`) VALUES (99,'ShortRow_L25',"
            "'extra','values','that','do','not','fit');\n"
        )

        _rows, summary = mybb_export.build_people(broken)

        assert summary["rows_offered"] == 4
        assert summary["rows_unread"] == 1

    def test_nothing_unread_is_reported_as_nothing(self, people):
        _rows, summary = people

        assert summary["rows_offered"] == 3
        assert summary["rows_unread"] == 0

    def test_unread_rows_are_shouted_about_on_stderr(self, tmp_path, capsys):
        """Printed to stderr, because this is read by somebody skimming."""
        dump = tmp_path / "backup.sql"
        dump.write_text(DUMP + (
            "INSERT INTO `lav_users` (`uid`,`username`) VALUES (99,'ShortRow_L25',"
            "'extra','values','that','do','not','fit');\n"
        ))

        mybb_export.main([str(dump), "--out", str(tmp_path / "people.json")])

        assert "NOT READ" in capsys.readouterr().err


def test_a_missing_dump_says_so_without_a_traceback(tmp_path):
    """This is run by hand, on a server, by somebody not reading Python."""
    with pytest.raises(SystemExit) as excinfo:
        mybb_export.read_dump(tmp_path / "not-here.sql.gz")

    assert "No such file" in str(excinfo.value)


def test_a_file_only_named_gz_says_so(tmp_path):
    path = tmp_path / "plain.sql.gz"
    path.write_text(DUMP)

    with pytest.raises(SystemExit) as excinfo:
        mybb_export.read_dump(path)

    assert "not gzipped" in str(excinfo.value)


class TestReadingTheDumpInTheEncodingItIsActuallyIn:
    """A German board read as the wrong thing loses every umlaut, silently.

    The reader used errors="replace", so "Prüfungen" arrived as
    "Pr�fungen" -- in every post, in every title -- and every count still
    added up. On the real board that was 7,867 characters, and nothing in a
    clean-looking run would ever have said so.
    """

    def test_it_believes_what_the_dump_declares(self):
        raw = "/*!40101 SET NAMES latin1 */;\n'Übungsbeispiele'".encode("cp1252")

        text, how = mybb_export.decode_dump(raw)

        assert "Übungsbeispiele" in text
        assert "latin1" in how

    def test_utf8_is_read_as_utf8(self):
        raw = "/*!40101 SET NAMES utf8mb4 */;\n'Prüfungen'".encode("utf-8")

        text, how = mybb_export.decode_dump(raw)

        assert "Prüfungen" in text
        assert "utf8mb4" in how

    def test_a_dump_that_declares_nothing_is_still_read(self):
        """MySQL's latin1 is cp1252: the curly quotes matter on a board."""
        raw = "Prüfungen „Zitat“".encode("cp1252")

        text, _how = mybb_export.decode_dump(raw)

        assert text == "Prüfungen „Zitat“"

    def test_a_mixed_dump_keeps_what_is_already_right(self):
        """The real case: 7,867 characters of this board are not UTF-8.

        Reading the whole file as cp1252 because of them would turn every
        correct umlaut into "Ã¼" -- far more damage than the problem
        it fixes. Only the bytes that are not UTF-8 are read the other way.
        """
        raw = ("SET NAMES utf8mb4; Prüfung ".encode("utf-8")
               + "Übung".encode("cp1252") + b" Ende")

        text, how = mybb_export.decode_dump(raw)

        assert "Prüfung" in text, "the UTF-8 part is untouched"
        assert "Übung" in text, "and the latin1 byte is recovered"
        assert "�" not in text
        assert "mixed" in how

    def test_a_mixed_dump_says_how_many_there_were(self):
        raw = "Prüfung ".encode("utf-8") + "Übung".encode("cp1252")

        _text, how = mybb_export.decode_dump(raw)

        assert "1 characters" in how

    def test_something_that_is_not_a_dump_at_all_says_so(self):
        """cp1252 would read UTF-16 as mojibake and call it a success."""
        raw = "SET NAMES utf8; 'Prüfungen'".encode("utf-16")

        _text, how = mybb_export.decode_dump(raw)

        assert "worth checking" in how

    def test_how_it_was_read_is_reported_to_the_caller(self, tmp_path):
        dump = tmp_path / "backup.sql"
        dump.write_bytes("/*!40101 SET NAMES utf8mb4 */;\n".encode("utf-8"))
        said = []

        mybb_export.read_dump(dump, on_note=said.append)

        assert said and "utf8mb4" in said[0]
