#!/usr/bin/env python3
"""Everything the forum import needs, in one file, checked at both ends.

The import takes four things that do not live in the repository and are not
generated on the server: the old board's dump, the gigabytes of attachments,
the avatars, and the two files a person made -- ``people.json`` and the
worksheet's category mapping. Between them they are the whole of the input, and
every reset of the portal means putting all of them back.

Carrying them as four separate copies is how a run gets halfway through and
discovers an avatar folder that never arrived. So they travel as one archive
with a manifest, and the manifest is checked on the machine that will use it --
not on the machine that made it, where everything is always fine.

    python3 scripts/forum_package.py pack \\
        --dump backup.sql.gz --uploads ./uploads --avatars ./avatars \\
        --people people.json --mapping categories.json \\
        --out forum-import.zip

    python3 scripts/forum_package.py check forum-import.zip

Standalone on purpose: it is run on the laptop that has the files, which has no
virtualenv, no Flask and no database. Nothing here imports the application.

The archive is stored rather than compressed. Its contents are PDFs, JPEGs and
a gzip -- already compressed, every one of them -- so deflating nine gigabytes
would spend an hour to save nothing.
"""

import argparse
import json
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

MANIFEST = "manifest.json"

#: Where each part lands inside the archive. The import reads them by these
#: names, so they are part of the format rather than a convenience. The dump
#: is the exception: it keeps whichever suffix says the truth about its bytes,
#: because everything that opens it decides by the suffix.
LAYOUT = {
    "dump": "dump.sql.gz",
    "people": "people.json",
    "mapping": "categories.json",
    "uploads": "uploads",
    "avatars": "avatars",
}

GZIP_MAGIC = b"\x1f\x8b"


def _say(message):
    print(message, flush=True)


def _size(count):
    for unit, step in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("kB", 1024)):
        if count >= step:
            return f"{count / step:.1f} {unit}"
    return f"{count} B"


def _files_under(root):
    return sorted(path for path in Path(root).rglob("*") if path.is_file())


def _dump_name(path):
    """``dump.sql.gz`` or ``dump.sql``, by what the file is rather than its name.

    ``read_dump`` picks gzip or plain by the suffix alone, so a plain dump
    stored under a ``.gz`` name fails on the server with a message about
    gzipping -- a long way from the mistake, which was here.
    """
    with open(path, "rb") as handle:
        gzipped = handle.read(2) == GZIP_MAGIC
    return "dump.sql.gz" if gzipped else "dump.sql"


def _read_json(path_or_bytes, what):
    try:
        if isinstance(path_or_bytes, bytes):
            return json.loads(path_or_bytes.decode("utf-8"))
        return json.loads(Path(path_or_bytes).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"{what} cannot be read as JSON: {exc}")


def _avatars_wanted(people):
    """The avatar filenames ``people.json`` asks for, in the order it asks."""
    names = []
    for row in people:
        if not isinstance(row, dict):
            continue
        name = (row.get("avatar_file") or "").strip()
        if name:
            names.append(name)
    return names


def _describe_people(payload):
    """What is in people.json, for the manifest.

    Read rather than assumed: a package whose manifest says 740 people and
    whose people.json is last week's 120 is worse than no manifest at all.
    """
    people = payload if isinstance(payload, list) else payload.get("people", [])
    return people, {
        "people": len(people),
        "with_avatar": len(_avatars_wanted(people)),
    }


def _describe_mapping(payload):
    """What the worksheet decided, for the manifest.

    The number worth reading back is how many forums end up somewhere live:
    an export where that is 4 is one somebody exported before they had
    finished, and it looks exactly like a finished one from the outside.
    """
    rows = payload.get("mapping") or payload.get("rows") or []
    live = [
        row for row in rows
        if row.get("decided")
        and (row.get("target") or "").strip() not in ("", "ARCHIVE")
    ]
    return {
        "forums": len(rows),
        "decided": sum(1 for row in rows if row.get("decided")),
        "to_lectures": len(live),
        "lectures": len({(row.get("target") or "").strip() for row in live}),
        "archived": len(rows) - len(live),
    }


def pack(args):
    parts = {
        "dump": Path(args.dump),
        "people": Path(args.people),
        "mapping": Path(args.mapping),
        "uploads": Path(args.uploads),
        "avatars": Path(args.avatars) if args.avatars else None,
    }
    for name, path in parts.items():
        if path is not None and not path.exists():
            raise SystemExit(f"No such {name}: {path}")

    layout = dict(LAYOUT, dump=_dump_name(parts["dump"]))
    manifest = {
        "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "layout": layout,
        "parts": {},
    }

    members = []
    for name in ("dump", "people", "mapping"):
        path = parts[name]
        members.append((path, layout[name]))
        manifest["parts"][name] = {"name": layout[name], "bytes": path.stat().st_size}

    people, described = _describe_people(_read_json(parts["people"], parts["people"]))
    manifest["parts"]["people"].update(described)
    manifest["parts"]["mapping"].update(
        _describe_mapping(_read_json(parts["mapping"], parts["mapping"]))
    )

    for name in ("uploads", "avatars"):
        root = parts[name]
        if root is None:
            continue
        found = _files_under(root)
        for path in found:
            members.append((path, f"{layout[name]}/{path.relative_to(root).as_posix()}"))
        manifest["parts"][name] = {
            "name": layout[name],
            "files": len(found),
            "bytes": sum(path.stat().st_size for path in found),
        }

    # The one cross-check worth making here, because it is the failure this
    # whole file exists to prevent: people.json names the avatar files, and a
    # folder that is last month's copy has most of them and not all.
    wanted = set(_avatars_wanted(people))
    have = set()
    if parts["avatars"] is not None:
        have = {
            path.relative_to(parts["avatars"]).as_posix()
            for path in _files_under(parts["avatars"])
        }
        have |= {Path(name).name for name in have}
    missing = sorted(wanted - have)
    manifest["avatars_named"] = sorted(wanted)
    manifest["avatars_missing"] = missing

    total = sum(path.stat().st_size for path, _ in members)
    out = Path(args.out)
    where = out.parent if str(out.parent) and out.parent.exists() else Path(".")
    free = shutil.disk_usage(where).free
    _say(f"{len(members)} files, {_size(total)}.")
    if free < total * 1.02:
        raise SystemExit(
            f"Only {_size(free)} free where {out} would go, and the archive "
            f"needs {_size(total)}. Write it somewhere else "
            f"with --out, or make room: this is a copy, not a move."
        )

    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
        archive.writestr(MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False))
        for index, (path, inside) in enumerate(members, 1):
            archive.write(path, inside)
            if index % 250 == 0 or index == len(members):
                _say(f"  {index}/{len(members)}")

    _say(f"\nWritten: {out}  ({_size(out.stat().st_size)})")
    _say(_summary(manifest))
    if missing:
        _say(
            f"\n  ! {len(missing)} avatars are named in people.json and are not "
            f"in the avatar folder, so those people import without a picture. "
            f"The first few: {', '.join(missing[:5])}"
        )
    _say("\nCheck it on the machine that will use it:")
    _say(f"  python3 scripts/forum_package.py check {out.name}")
    return 0


def _summary(manifest):
    lines = []
    for name, part in manifest["parts"].items():
        extra = ", ".join(
            f"{key} {value}" for key, value in part.items()
            if key not in ("name", "bytes")
        )
        lines.append(f"  {name:<8} {_size(part['bytes']):>10}  {extra}".rstrip())
    return "\n".join(lines)


def check(args):
    """Is this archive whole, and is it the one it says it is?

    Two different questions, and both are asked. The zip's own checksums say
    whether the bytes survived the copy; the manifest says whether the thing
    that was packed is the thing that is needed -- 2,347 attachments and not
    2,300.
    """
    path = Path(args.package)
    if not path.exists():
        raise SystemExit(f"No such package: {path}")

    problems = []
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise SystemExit(f"{path} is not a readable zip file: {exc}")

    with archive:
        names = set(archive.namelist())
        if MANIFEST not in names:
            raise SystemExit(
                f"{path} has no {MANIFEST} in it, so it was not written by this "
                f"script and nothing about it can be checked."
            )
        manifest = _read_json(archive.read(MANIFEST), MANIFEST)

        _say(f"Packed {manifest.get('written_at', 'at some point')}.")
        for name, part in manifest.get("parts", {}).items():
            inside = part["name"]
            if "files" in part:
                here = sum(1 for entry in names if entry.startswith(inside + "/"))
                state = "ok" if here == part["files"] else \
                    f"! {here} here, {part['files']} packed"
                if here != part["files"]:
                    problems.append(f"{name}: {here} files here, {part['files']} packed")
                _say(f"  {name:<8} {part['files']:>6} files   {state}")
                continue
            if inside not in names:
                problems.append(f"{name}: {inside} is not in the archive")
                _say(f"  {name:<8} missing")
                continue
            extra = ", ".join(
                f"{key} {value}" for key, value in part.items()
                if key not in ("name", "bytes")
            )
            _say(f"  {name:<8} {_size(part['bytes']):>10}  {extra}".rstrip())

        # Asked again here, against the archive rather than the folder, because
        # this is the copy that will be used and the two can differ: a package
        # built from a good folder still arrives with files missing.
        people_name = manifest.get("layout", {}).get("people", LAYOUT["people"])
        avatars_at = manifest.get("layout", {}).get("avatars", LAYOUT["avatars"])
        if people_name in names:
            people, _ = _describe_people(_read_json(archive.read(people_name), people_name))
            inside_names = {
                entry[len(avatars_at) + 1:] for entry in names
                if entry.startswith(avatars_at + "/")
            }
            inside_names |= {Path(entry).name for entry in inside_names}
            absent = sorted(set(_avatars_wanted(people)) - inside_names)
            if absent:
                _say(
                    f"\n  {len(absent)} of the avatars people.json names are not in "
                    f"the archive; those people import without a picture."
                )
                if not manifest.get("avatars_missing"):
                    # They were there when it was packed, so they were lost on
                    # the way here -- which is a broken package, not a choice
                    # somebody made.
                    problems.append(
                        f"{len(absent)} avatars named in people.json were packed "
                        f"and are no longer in the archive"
                    )

        # The slow one, and the point of the exercise: every byte read back and
        # checked against the checksum the archive carries for it. Nine
        # gigabytes copied over a network is where a package goes wrong.
        if not args.quick:
            _say("\nReading every file back to check it survived the copy...")
            try:
                broken = archive.testzip()
            except zipfile.BadZipFile as exc:
                broken = str(exc)
            if broken is not None:
                problems.append(f"{broken} is corrupt")

    if problems:
        _say("\n" + "\n".join(f"  ! {problem}" for problem in problems))
        _say("\nThis package is not usable as it is. Build it again and copy it again.")
        return 1
    _say("\nWhole, and complete. Unpack it with:")
    _say(f"  unzip -q {path.name} -d /var/tmp/forum-migration")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    make = commands.add_parser("pack", help="Build the archive.")
    make.add_argument("--dump", required=True, help="The MyBB dump (.sql or .sql.gz).")
    make.add_argument("--uploads", required=True, help="The old board's uploads folder.")
    make.add_argument("--avatars", help="The folder of avatar files.")
    make.add_argument("--people", required=True, help="people.json.")
    make.add_argument("--mapping", required=True, help="The worksheet's JSON export.")
    make.add_argument("--out", default="forum-import.zip")
    make.set_defaults(run=pack)

    verify = commands.add_parser("check", help="Verify an archive.")
    verify.add_argument("package")
    verify.add_argument("--quick", action="store_true",
                        help="Count the files and skip reading them back.")
    verify.set_defaults(run=check)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
