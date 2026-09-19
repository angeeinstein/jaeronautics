#!/usr/bin/env python3
"""Turn a MyBB database dump into the JSON the forum importer reads.

Run this against a ``mysqldump`` file on your own machine. Nothing leaves it:
the dump holds six hundred real addresses, and the only thing that needs to
travel is the JSON, which you can inspect first.

    python scripts/mybb_export.py backup.sql --out people.json
    python scripts/mybb_export.py backup.sql.gz --out people.json --jahrgang-field fid3

If ``--jahrgang-field`` is not given, the script finds it: ``mybb_profilefields``
maps a field id to a name, and the column in ``mybb_userfields`` is ``fid`` plus
that id. The id differs per installation, so it is looked up rather than
assumed.

Why parse the dump rather than query the database? Because a dump is what you
already have, and it means no credentials and no live connection. If you would
rather query directly, phpMyAdmin can run the SELECT in docs/forum-import.md
and export the result as JSON, and you can skip this entirely.
"""

import argparse
import gzip
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# MyBB stores registration and post times as UTC timestamps, but the board
# displayed them in local time -- so a student who signed up at 23:00 Vienna
# time shows as the 23rd on their profile and as the 22nd in UTC. Converting in
# UTC would quietly move a chunk of the intake dates a day earlier than the
# forum has said for a decade. This is the same rule services/clock.py applies
# to membership dates; the script repeats it because it runs standalone.
DEFAULT_TIMEZONE = "Europe/Vienna"


def read_dump(path):
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"No such file: {path}")
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except gzip.BadGzipFile:
        raise SystemExit(f"{path} is named .gz but is not gzipped.") from None


# Tables MyBB itself always has. A plugin can add its own `..._users` table --
# Tapatalk does -- so the prefix is the one where these exist together, not
# whichever table happens to end in "users" first.
CORE_TABLES = ("users", "userfields", "profilefields", "posts", "threads", "settings")


def all_tables(dump):
    return set(re.findall(r"CREATE TABLE `([^`]+)`", dump))


def find_prefix(dump):
    """The MyBB table prefix, chosen by which candidate has the core tables.

    Returns (prefix, rejected) so the caller can say what it passed over --
    silently picking a plugin's table produced an empty export and a confusing
    zero rather than an error.
    """
    tables = all_tables(dump)
    scored = []
    for table in tables:
        if not table.endswith("users"):
            continue
        prefix = table[: -len("users")]
        score = sum(1 for core in CORE_TABLES if f"{prefix}{core}" in tables)
        scored.append((score, -len(prefix), prefix))
    if not scored:
        return None, []
    scored.sort(reverse=True)
    best = scored[0][2]
    return best, [prefix for _score, _length, prefix in scored[1:]]


def find_table(dump, suffix, prefix=None):
    """The real table name for a MyBB table, or None when it is absent."""
    if prefix is not None:
        name = f"{prefix}{suffix}"
        return name if name in all_tables(dump) else None
    match = re.search(rf"CREATE TABLE `([^`]*{re.escape(suffix)})`", dump)
    return match.group(1) if match else None


def columns_of(dump, table):
    """Column order from CREATE TABLE, needed because mysqldump omits names."""
    match = re.search(
        rf"CREATE TABLE `{re.escape(table)}` \((.*?)\n\) ENGINE", dump, re.S
    )
    if not match:
        return []
    return re.findall(r"^\s*`([^`]+)`\s", match.group(1), re.M)


def _split_values(text):
    """Split one VALUES tuple on top-level commas, respecting quotes."""
    values, current, in_string, escaped = [], [], False, False
    for character in text:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\" and in_string:
            current.append(character)
            escaped = True
        elif character == "'":
            in_string = not in_string
            current.append(character)
        elif character == "," and not in_string:
            values.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    values.append("".join(current).strip())
    return values


def _decode(raw):
    if raw.upper() == "NULL":
        return None
    if raw.startswith("'") and raw.endswith("'"):
        body = raw[1:-1]
        for escaped, plain in (
            ("\\'", "'"), ('\\"', '"'), ("\\\\", "\\"),
            ("\\n", "\n"), ("\\r", "\r"), ("\\t", "\t"), ("\\0", ""),
        ):
            body = body.replace(escaped, plain)
        return body
    return raw


def rows_of(dump, table):
    """Every row of a table, as dicts keyed by column name."""
    names = columns_of(dump, table)
    if not names:
        return []

    rows = []
    for statement in re.finditer(
        rf"INSERT INTO `{re.escape(table)}`(?: \(([^)]*)\))? VALUES\s*(.*?);\s*$",
        dump,
        re.S | re.M,
    ):
        explicit = statement.group(1)
        keys = re.findall(r"`([^`]+)`", explicit) if explicit else names
        for tuple_body in re.finditer(r"\((.*?)\)(?=\s*,\s*\(|\s*$)", statement.group(2), re.S):
            values = _split_values(tuple_body.group(1))
            if len(values) != len(keys):
                continue
            rows.append({key: _decode(value) for key, value in zip(keys, values)})
    return rows


def find_jahrgang_field(dump, prefix_table):
    """Which fidN column holds the year group, from the field definitions."""
    fields = rows_of(dump, prefix_table)
    for row in fields:
        name = (row.get("name") or "").strip().lower()
        if "jahrgang" in name or "year" in name:
            return f"fid{row.get('fid')}", row.get("name")
    return None, None


def _timestamp_to_date(value, zone):
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        # 0 means "never posted", not midnight on 1 January 1970.
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(zone).date().isoformat()


def _avatar_parts(value):
    """`./uploads/avatars/avatar_142.jpg?dateline=169...` -> dir, filename.

    The directory is reported rather than used: it is the one thing somebody
    fetching the files off the hosting needs to know, and guessing it wrong
    means downloading twelve years of attachments by mistake.
    """
    if not value:
        return None, None
    if value.startswith("http://") or value.startswith("https://"):
        return None, None  # hosted elsewhere, nothing local to copy
    path = value.split("?", 1)[0].strip().lstrip("./")
    if not path:
        return None, None
    directory, _, filename = path.rpartition("/")
    return (directory or None), (filename or None)


def build_people(dump, jahrgang_field=None, timezone_name=DEFAULT_TIMEZONE, prefix=None):
    zone = ZoneInfo(timezone_name)
    rejected = []
    if prefix is None:
        prefix, rejected = find_prefix(dump)
    if prefix is None:
        raise SystemExit("No MyBB users table found -- is this a MyBB dump?")

    users_table = find_table(dump, "users", prefix)
    if not users_table:
        raise SystemExit(f"No table named {prefix}users in this dump.")
    fields_table = find_table(dump, "userfields", prefix)
    profilefields_table = find_table(dump, "profilefields", prefix)

    field_label = None
    if jahrgang_field is None and profilefields_table:
        jahrgang_field, field_label = find_jahrgang_field(dump, profilefields_table)

    year_groups = {}
    if fields_table and jahrgang_field:
        for row in rows_of(dump, fields_table):
            value = (row.get(jahrgang_field) or "").strip()
            if value:
                year_groups[row.get("ufid")] = value

    people, remote_avatars = [], 0
    avatar_directories = {}
    for row in rows_of(dump, users_table):
        uid = row.get("uid")
        if not uid:
            continue
        avatar = row.get("avatar") or ""
        if avatar.startswith("http"):
            remote_avatars += 1
        avatar_directory, avatar_file = _avatar_parts(avatar)
        if avatar_directory:
            avatar_directories[avatar_directory] = avatar_directories.get(avatar_directory, 0) + 1
        people.append({
            "source_user_id": str(uid),
            "source_username": row.get("username"),
            "source_email": row.get("email") or None,
            "display_name": None,
            "year_group": year_groups.get(uid),
            "post_count": int(row.get("postnum") or 0),
            "joined_on": _timestamp_to_date(row.get("regdate"), zone),
            "last_posted_on": _timestamp_to_date(row.get("lastpost"), zone),
            "avatar_file": avatar_file,
        })

    return people, {
        "prefix": prefix,
        "rejected_prefixes": rejected,
        "users_table": users_table,
        "jahrgang_field": jahrgang_field,
        "jahrgang_label": field_label,
        "with_year_group": sum(1 for person in people if person["year_group"]),
        "with_avatar": sum(1 for person in people if person["avatar_file"]),
        "remote_avatars": remote_avatars,
        # Where the files actually live on the old host, counted, so the right
        # directory gets downloaded rather than the whole uploads tree.
        "avatar_directories": sorted(avatar_directories.items(), key=lambda item: -item[1]),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dump", help="mysqldump file (.sql or .sql.gz)")
    parser.add_argument("--out", default="people.json", help="where to write the JSON")
    parser.add_argument("--jahrgang-field", help="e.g. fid3, if the lookup gets it wrong")
    parser.add_argument("--prefix", help="table prefix, e.g. mybb_ (detected by default)")
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE,
                        help=f"the board's timezone for reading timestamps (default {DEFAULT_TIMEZONE})")
    args = parser.parse_args(argv)

    people, summary = build_people(
        read_dump(args.dump), args.jahrgang_field, args.timezone, args.prefix
    )
    Path(args.out).write_text(json.dumps(people, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"table prefix       : {summary['prefix']}")
    if summary["rejected_prefixes"]:
        print(f"  ignored          : {', '.join(summary['rejected_prefixes'])} (plugin tables)")
    print(f"users table        : {summary['users_table']}")
    print(f"year group field   : {summary['jahrgang_field']} ({summary['jahrgang_label'] or 'not found'})")
    print(f"people             : {len(people)}")
    print(f"  with year group  : {summary['with_year_group']}")
    print(f"  with avatar file : {summary['with_avatar']}")
    if summary["remote_avatars"]:
        print(f"  remote avatars   : {summary['remote_avatars']} (hosted elsewhere, no local file)")
    for directory, count in summary["avatar_directories"]:
        print(f"  files live in    : {directory}/  ({count})")
    print(f"written            : {args.out}")
    if not people:
        print("\nNo people were found. If the prefix above looks wrong, pass --prefix.",
              file=sys.stderr)
        return 1
    if not summary["jahrgang_field"]:
        print("\nNo Jahrgang field was found. Pass --jahrgang-field fidN; the ids are in "
              "the profilefields table.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
