#!/usr/bin/env python3
"""Check the MyBB uploads folder against what the database says should be in it.

Run this before migrating any content. The old board is mostly a document
archive -- nine tenths of its posts carry a file -- so a post whose attachment
is missing migrates as a sentence pointing at nothing. Better to know which
ones those are now than to find out afterwards.

    python scripts/mybb_attachments_check.py backup.sql.gz --uploads ./uploads
    python scripts/mybb_attachments_check.py backup.sql.gz --uploads ./uploads --limit-mb 100

Exits non-zero if any attachment is missing or the wrong size, so it can gate
the import.

Nothing leaves your machine and no file is read: this compares names and sizes
from the directory listing only. Original filenames are not printed either --
they are coursework, and some carry a student's name. Missing files are
reported by their stored path, which is a hash.
"""

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mybb_export import find_table, read_dump, rows_of  # noqa: E402

# What Discourse accepts by default. Both are raised in site settings rather
# than being a reason to drop a file, but you want the number before you start.
DEFAULT_LIMIT_MB = 4


def index_uploads(root):
    """Every file under the uploads folder, by relative path and by basename.

    Both, because a folder that has been zipped, copied off a server and
    unpacked does not always keep its shape. The database stores
    ``201402/post_19_....attach``; matching on the basename as a fallback
    means a flattened or differently-rooted copy still resolves.
    """
    by_path, by_name = {}, defaultdict(list)
    for item in root.rglob("*"):
        if not item.is_file():
            continue
        by_path[item.relative_to(root).as_posix()] = item
        by_name[item.name].append(item)
    return by_path, by_name


def locate(attachname, by_path, by_name):
    """The file for a stored attachment name, or None. Returns (file, exact)."""
    if not attachname:
        return None, False
    cleaned = attachname.strip().lstrip("./")
    if cleaned in by_path:
        return by_path[cleaned], True
    candidates = by_name.get(Path(cleaned).name, [])
    # Only when it is unambiguous. Two files with one name is not a match, it
    # is a question, and answering it by picking the first would be a guess.
    if len(candidates) == 1:
        return candidates[0], False
    return None, False


def check(dump, uploads_root, limit_mb=DEFAULT_LIMIT_MB):
    table = find_table(dump, "attachments")
    if not table:
        raise SystemExit("no attachments table in this dump")

    posts = {row.get("pid") for row in rows_of(dump, find_table(dump, "posts") or "")}
    by_path, by_name = index_uploads(uploads_root)

    found, loose, missing, wrong_size = [], [], [], []
    referenced = set()
    for row in rows_of(dump, table):
        attachname = row.get("attachname") or ""
        declared = int(row["filesize"]) if (row.get("filesize") or "").isdigit() else None
        path, exact = locate(attachname, by_path, by_name)
        if path is None:
            missing.append(row)
            continue
        referenced.add(path)
        actual = path.stat().st_size
        if declared is not None and actual != declared:
            wrong_size.append((row, declared, actual))
        elif exact:
            found.append((row, actual))
        else:
            loose.append((row, actual))

    return {
        "found": found, "loose": loose, "missing": missing, "wrong_size": wrong_size,
        "orphan_rows": [r for r in rows_of(dump, table) if r.get("pid") not in posts],
        "unreferenced_files": [p for p in by_path.values() if p not in referenced],
        "limit_mb": limit_mb,
    }


def report(result):
    found, loose = result["found"], result["loose"]
    missing, wrong = result["missing"], result["wrong_size"]
    total = len(found) + len(loose) + len(missing) + len(wrong)
    usable = found + loose
    bytes_present = sum(size for _row, size in usable)

    print(f"attachments in the database : {total}")
    print(f"  matched exactly           : {len(found)}")
    if loose:
        print(f"  matched by filename only  : {len(loose)}  (folder shape differs; still fine)")
    print(f"  wrong size on disk        : {len(wrong)}")
    print(f"  MISSING                   : {len(missing)}")
    print(f"\nbytes present               : {bytes_present / 1e9:.2f} GB")

    if usable:
        sizes = sorted(size for _row, size in usable)
        over = sum(1 for s in sizes if s > result["limit_mb"] * 1e6)
        print(f"  median {sizes[len(sizes) // 2] / 1e6:.2f} MB"
              f"   largest {sizes[-1] / 1e6:.1f} MB")
        print(f"  over {result['limit_mb']} MB (Discourse default): {over}"
              f"  -- raise max_attachment_size_kb to at least {int(sizes[-1] / 1024) + 1}")
        kinds = Counter((r.get("filetype") or "?") for r, _ in usable)
        print("  filetypes: " + ", ".join(f"{k} x{n}" for k, n in kinds.most_common(6)))
        extensions = {Path(r.get("filename") or "").suffix.lower().lstrip(".")
                      for r, _ in usable}
        extensions.discard("")
        print("  authorized_extensions needs: " + " ".join(sorted(extensions)))

    if result["orphan_rows"]:
        print(f"\n{len(result['orphan_rows'])} attachments belong to a post that is not in "
              "the dump -- nothing to attach them to, so they are skipped")
    if result["unreferenced_files"]:
        extra = sum(p.stat().st_size for p in result["unreferenced_files"])
        print(f"{len(result['unreferenced_files'])} files on disk are referenced by nothing "
              f"({extra / 1e9:.2f} GB) -- usually thumbnails and deleted posts")

    for row, declared, actual in wrong[:10]:
        print(f"  size mismatch: {row.get('attachname')} "
              f"db={declared} disk={actual}")
    if missing:
        print(f"\nmissing files ({min(len(missing), 20)} of {len(missing)} shown):")
        for row in missing[:20]:
            print(f"  aid={row.get('aid')} pid={row.get('pid')} {row.get('attachname')}")

    ok = not missing and not wrong
    print("\n" + ("every attachment is accounted for." if ok
                  else "NOT complete -- see above before importing."))
    return ok


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dump", help="mysqldump file (.sql or .sql.gz)")
    parser.add_argument("--uploads", required=True,
                        help="the uploads folder downloaded from the old server")
    parser.add_argument("--limit-mb", type=float, default=DEFAULT_LIMIT_MB,
                        help=f"size to count against (default {DEFAULT_LIMIT_MB}, "
                             "Discourse's max_attachment_size_kb default)")
    args = parser.parse_args(argv)

    root = Path(args.uploads).expanduser()
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")

    return 0 if report(check(read_dump(args.dump), root, args.limit_mb)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
