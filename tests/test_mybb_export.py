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
  `postnum` int(10) NOT NULL DEFAULT '0',
  `regdate` bigint(30) NOT NULL DEFAULT '0',
  `lastpost` bigint(30) NOT NULL DEFAULT '0',
  `avatar` varchar(200) NOT NULL DEFAULT '',
  `usernotes` text NOT NULL,
  PRIMARY KEY (`uid`)
) ENGINE=MyISAM;

INSERT INTO `lav_users` VALUES (142,'PopovicA_L23','a.popovic@edu.fh-joanneum.at',2,1700694000,1717286400,'./uploads/avatars/avatar_142.jpg?dateline=1700694000','fine'),(7,'OBrienC_M20','c.obrien@edu.fh-joanneum.at',41,1580000000,0,'','note with an \\'apostrophe\\' in it');
INSERT INTO `lav_users` VALUES (9,'RemoteR_L21','r.remote@edu.fh-joanneum.at',3,1600000000,1600000001,'https://example.com/avatar.png',NULL);

CREATE TABLE `lav_userfields` (
  `ufid` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `fid1` text NOT NULL,
  `fid3` text NOT NULL,
  PRIMARY KEY (`ufid`)
) ENGINE=MyISAM;

INSERT INTO `lav_userfields` VALUES (142,'Undisclosed','LAV23'),(7,'','MAV20'),(9,'','');
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
