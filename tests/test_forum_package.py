"""The import package: one archive, checked on the machine that will use it.

Every reset of the portal means putting four things back -- the dump, the
uploads, the avatars, and the two files a person spent days making. The failure
this guards against is not a dramatic one. It is a run that starts, works for
twenty minutes, and then imports 739 people without pictures because the avatar
folder that was copied was last month's.

So the tests here are mostly about lying: a manifest that says one thing while
the archive holds another, a plain dump wearing a ``.gz`` name, an export that
was taken before the worksheet was finished.
"""
import gzip
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import forum_package  # noqa: E402


PEOPLE = [
    {"old_user_id": "7", "username": "AnnaB", "avatar_file": "avatar_7.png"},
    {"old_user_id": "8", "username": "BertaC", "avatar_file": "avatar_8.jpg"},
    {"old_user_id": "9", "username": "CarlD", "avatar_file": ""},
]

MAPPING = {
    "mapping": [
        {"old_fid": "12", "old_path": "Bachelor / 4. Semester",
         "target": "Bachelor 4. Semester / Mechanik 2", "decided": True},
        {"old_fid": "13", "old_path": "Bachelor / 2. Semester",
         "target": "Bachelor 4. Semester / Mechanik 2", "decided": True},
        {"old_fid": "14", "old_path": "Master / 1. Semester",
         "target": "ARCHIVE", "decided": True},
        {"old_fid": "15", "old_path": "Bachelor / 6. Semester",
         "target": "", "decided": False},
    ]
}


@pytest.fixture
def inputs(tmp_path):
    """The four things the import takes, in miniature."""
    home = tmp_path / "laptop"
    home.mkdir()

    dump = home / "backup.sql.gz"
    dump.write_bytes(gzip.compress(b"CREATE TABLE `lav_users` (`uid` int);\n"))

    people = home / "people.json"
    people.write_text(json.dumps(PEOPLE), encoding="utf-8")

    mapping = home / "categories.json"
    mapping.write_text(json.dumps(MAPPING), encoding="utf-8")

    uploads = home / "uploads"
    (uploads / "201402").mkdir(parents=True)
    (uploads / "201402" / "post_19_1390000000_abc.attach").write_bytes(b"pdf bytes")
    (uploads / "201801").mkdir()
    (uploads / "201801" / "post_31_1520000000_def.attach").write_bytes(b"more bytes")

    avatars = home / "avatars"
    avatars.mkdir()
    (avatars / "avatar_7.png").write_bytes(b"png bytes")
    (avatars / "avatar_8.jpg").write_bytes(b"jpg bytes")

    return {
        "home": home, "dump": dump, "people": people, "mapping": mapping,
        "uploads": uploads, "avatars": avatars, "out": tmp_path / "forum-import.zip",
    }


def pack(inputs, **overrides):
    argv = [
        "pack",
        "--dump", str(overrides.get("dump", inputs["dump"])),
        "--uploads", str(overrides.get("uploads", inputs["uploads"])),
        "--people", str(overrides.get("people", inputs["people"])),
        "--mapping", str(overrides.get("mapping", inputs["mapping"])),
        "--out", str(overrides.get("out", inputs["out"])),
    ]
    avatars = overrides.get("avatars", inputs["avatars"])
    if avatars is not None:
        argv += ["--avatars", str(avatars)]
    return forum_package.main(argv)


def manifest_of(archive_path):
    with zipfile.ZipFile(archive_path) as archive:
        return json.loads(archive.read(forum_package.MANIFEST))


class TestPacking:
    def test_every_part_is_in_it_under_the_name_the_import_expects(self, inputs):
        pack(inputs)
        with zipfile.ZipFile(inputs["out"]) as archive:
            names = set(archive.namelist())
        assert {"dump.sql.gz", "people.json", "categories.json"} <= names
        assert "uploads/201402/post_19_1390000000_abc.attach" in names
        assert "avatars/avatar_7.png" in names

    def test_the_dump_survives_the_round_trip_readable(self, inputs, tmp_path):
        pack(inputs)
        with zipfile.ZipFile(inputs["out"]) as archive:
            raw = archive.read("dump.sql.gz")
        assert b"lav_users" in gzip.decompress(raw)

    def test_the_manifest_says_what_is_in_the_two_json_files(self, inputs):
        pack(inputs)
        parts = manifest_of(inputs["out"])["parts"]
        assert parts["people"]["people"] == 3
        assert parts["people"]["with_avatar"] == 2
        assert parts["uploads"]["files"] == 2
        assert parts["avatars"]["files"] == 2

    def test_it_counts_what_the_worksheet_decided(self, inputs):
        """Two old forums merging into one lecture is the point, not a mistake."""
        pack(inputs)
        mapping = manifest_of(inputs["out"])["parts"]["mapping"]
        assert mapping["forums"] == 4
        assert mapping["to_lectures"] == 2
        assert mapping["lectures"] == 1
        assert mapping["archived"] == 2

    def test_a_missing_input_stops_before_anything_is_written(self, inputs):
        with pytest.raises(SystemExit) as stopped:
            pack(inputs, avatars=inputs["home"] / "avatars-from-the-other-laptop")
        assert "No such avatars" in str(stopped.value)
        assert not inputs["out"].exists()

    def test_a_plain_dump_is_not_stored_under_a_gz_name(self, inputs):
        """The server picks gzip or plain by the suffix, so the suffix must be true."""
        plain = inputs["home"] / "backup.sql.gz"
        plain.write_bytes(b"CREATE TABLE `lav_users` (`uid` int);\n")
        pack(inputs, dump=plain)
        with zipfile.ZipFile(inputs["out"]) as archive:
            names = archive.namelist()
        assert "dump.sql" in names
        assert "dump.sql.gz" not in names

    def test_json_that_is_not_json_says_so_without_a_traceback(self, inputs):
        inputs["mapping"].write_text("{ this was pasted out of a chat window", "utf-8")
        with pytest.raises(SystemExit) as stopped:
            pack(inputs)
        assert "cannot be read as JSON" in str(stopped.value)


class TestAvatarsThatPeopleJsonAsksForAndTheFolderDoesNotHave:
    """The quiet one. 739 people import, nobody has a picture, nothing failed."""

    def test_packing_says_which_are_missing(self, inputs, capsys):
        (inputs["avatars"] / "avatar_8.jpg").unlink()
        pack(inputs)
        said = capsys.readouterr().out
        assert "1 avatars are named in people.json" in said
        assert "avatar_8.jpg" in said

    def test_it_is_recorded_rather_than_only_printed(self, inputs):
        (inputs["avatars"] / "avatar_8.jpg").unlink()
        pack(inputs)
        assert manifest_of(inputs["out"])["avatars_missing"] == ["avatar_8.jpg"]

    def test_no_avatar_folder_at_all_is_reported_the_same_way(self, inputs, capsys):
        pack(inputs, avatars=None)
        assert "2 avatars are named in people.json" in capsys.readouterr().out

    def test_all_present_says_nothing_about_them(self, inputs, capsys):
        pack(inputs)
        assert "named in people.json" not in capsys.readouterr().out


class TestChecking:
    def test_a_package_as_packed_passes(self, inputs, capsys):
        pack(inputs)
        assert forum_package.main(["check", str(inputs["out"])]) == 0
        assert "Whole, and complete" in capsys.readouterr().out

    def test_it_reports_the_counts_so_they_can_be_recognised(self, inputs, capsys):
        pack(inputs)
        forum_package.main(["check", str(inputs["out"])])
        said = capsys.readouterr().out
        assert "people 3" in said
        assert "2 files" in said

    def test_a_missing_package_says_so_without_a_traceback(self, tmp_path):
        with pytest.raises(SystemExit) as stopped:
            forum_package.main(["check", str(tmp_path / "nothing.zip")])
        assert "No such package" in str(stopped.value)

    def test_something_that_is_not_a_zip_says_so(self, tmp_path):
        not_a_zip = tmp_path / "forum-import.zip"
        not_a_zip.write_bytes(b"<html>404 Not Found</html>")
        with pytest.raises(SystemExit) as stopped:
            forum_package.main(["check", str(not_a_zip)])
        assert "not a readable zip" in str(stopped.value)

    def test_a_zip_somebody_else_made_is_refused_rather_than_guessed_at(self, tmp_path):
        theirs = tmp_path / "forum-import.zip"
        with zipfile.ZipFile(theirs, "w") as archive:
            archive.writestr("people.json", "[]")
        with pytest.raises(SystemExit) as stopped:
            forum_package.main(["check", str(theirs)])
        assert "no manifest.json" in str(stopped.value)

    def test_files_lost_between_the_two_machines_are_counted_and_fail(
        self, inputs, tmp_path, capsys
    ):
        """The manifest is the whole point: the archive alone cannot miss them."""
        pack(inputs)
        thinner = tmp_path / "arrived.zip"
        with zipfile.ZipFile(inputs["out"]) as whole:
            with zipfile.ZipFile(thinner, "w") as part:
                for entry in whole.namelist():
                    if entry == "uploads/201801/post_31_1520000000_def.attach":
                        continue
                    part.writestr(entry, whole.read(entry))
        assert forum_package.main(["check", str(thinner)]) == 1
        said = capsys.readouterr().out
        assert "1 files here, 2 packed" in said
        assert "not usable" in said

    def test_a_file_corrupted_in_transit_is_found_by_reading_it_back(
        self, inputs, tmp_path, capsys
    ):
        pack(inputs)
        rotten = bytearray(inputs["out"].read_bytes())
        at = rotten.find(b"pdf bytes")
        assert at > 0
        rotten[at:at + 9] = b"PDF BYTES"
        broken = tmp_path / "arrived.zip"
        broken.write_bytes(bytes(rotten))
        assert forum_package.main(["check", str(broken)]) == 1
        assert "is corrupt" in capsys.readouterr().out

    def test_quick_skips_reading_every_byte(self, inputs, tmp_path, capsys):
        """Nine gigabytes off a USB stick is minutes; sometimes the count is enough."""
        pack(inputs)
        rotten = bytearray(inputs["out"].read_bytes())
        at = rotten.find(b"pdf bytes")
        rotten[at:at + 9] = b"PDF BYTES"
        broken = tmp_path / "arrived.zip"
        broken.write_bytes(bytes(rotten))
        assert forum_package.main(["check", str(broken), "--quick"]) == 0
        assert "Reading every file back" not in capsys.readouterr().out

    def test_avatars_lost_on_the_way_are_a_failure_not_a_remark(
        self, inputs, tmp_path, capsys
    ):
        pack(inputs)
        thinner = tmp_path / "arrived.zip"
        with zipfile.ZipFile(inputs["out"]) as whole:
            with zipfile.ZipFile(thinner, "w") as part:
                for entry in whole.namelist():
                    if entry == "avatars/avatar_8.jpg":
                        continue
                    part.writestr(entry, whole.read(entry))
        assert forum_package.main(["check", str(thinner)]) == 1
        assert "no longer in the archive" in capsys.readouterr().out

    def test_avatars_that_were_never_there_are_said_but_do_not_fail_the_package(
        self, inputs, capsys
    ):
        """Somebody who never had a picture is not a broken package."""
        (inputs["avatars"] / "avatar_8.jpg").unlink()
        pack(inputs)
        capsys.readouterr()
        assert forum_package.main(["check", str(inputs["out"])]) == 0
        said = capsys.readouterr().out
        assert "import without a picture" in said
        assert "Whole, and complete" in said
