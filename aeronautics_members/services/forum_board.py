"""Moving the whole old board, not one thread of it.

The spike in ``forum_content`` answered whether this can be done at all. This
is the part that does it 720 times, and the difference between the two is
entirely about what happens when a run stops halfway.

1,529 posts, 2,347 attachments and nine gigabytes will not go across in one
uninterrupted go. Something will time out, run out of disk, or be rate limited
past its patience, and the only two outcomes that matter are: it can be run
again and carries on, or it cannot and the forum is left with half an archive
that nobody can reconcile. So every post that lands is written down before the
next one is attempted, and a second run skips what is already there.

The ledger is a file rather than a table on purpose. This is a one-off job run
from a terminal, the record has to survive the application being restarted or
reinstalled under it, and somebody reading it in six months should be able to
open it in an editor rather than needing the application to still exist.
"""

import json
from pathlib import Path

from flask import current_app

from ..forum_service import ForumProviderError
from .forum_content import AT_LEAST, Requirement, migrate_thread

#: Discourse will not nest categories deeper than this even when asked.
MAX_CATEGORY_NESTING = 3


class Ledger:
    """What has already been posted, written down as it happens.

    Append-only, one JSON object per line. A file rewritten in place can be
    caught half-written by the thing that interrupted the run; a file only ever
    appended to loses at most its last line, and a last line that will not
    parse is one post to redo rather than a record to throw away.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.topics = {}
        self.posts = {}
        self.categories = {}
        self._handle = None
        if self.path.exists():
            self._read()

    def _read(self):
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                # The run was interrupted mid-write. One unfinished line.
                current_app.logger.warning("Ledger line could not be read: %r", line[:120])
                continue
            self._remember(entry)

    def _remember(self, entry):
        kind, key, value = entry.get("kind"), entry.get("key"), entry.get("id")
        if kind == "topic":
            self.topics[key] = value
        elif kind == "post":
            self.posts[key] = value
        elif kind == "category":
            self.categories[key] = value

    def _write(self, kind, key, value):
        self._remember({"kind": kind, "key": key, "id": value})
        if self._handle is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a", encoding="utf-8")
        self._handle.write(json.dumps({"kind": kind, "key": key, "id": value}) + "\n")
        # Flushed every time. A buffered record of what has been posted is not
        # a record of what has been posted.
        self._handle.flush()

    def close(self):
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def topic_for(self, tid):
        return self.topics.get(str(tid))

    def post_for(self, pid):
        return self.posts.get(str(pid))

    def category_for(self, fid):
        return self.categories.get(str(fid))

    def record_topic(self, tid, topic_id):
        self._write("topic", str(tid), topic_id)

    def record_post(self, pid, post_id):
        self._write("post", str(pid), post_id)

    def record_category(self, fid, category_id):
        self._write("category", str(fid), category_id)


# ---------------------------------------------------------------------------
# The old board's own shape
# ---------------------------------------------------------------------------

def forum_tree(forums):
    """The old board's forums, each with its ancestors, deepest last.

    MyBB nests without limit: the board is degree, then semester, then lecture,
    and a category above all of it. Discourse stops at three levels, so a path
    longer than that has its tail folded into one name rather than being
    dropped -- "Bachelor / 3. Semester / Technisches Programmieren" survives as
    a name even where it cannot survive as a shape.
    """
    by_id = {row.get("fid"): row for row in forums}
    tree = {}
    for fid, row in by_id.items():
        path, seen, cursor = [], set(), row
        while cursor is not None and cursor.get("fid") not in seen:
            seen.add(cursor.get("fid"))
            path.append(cursor)
            parent = cursor.get("pid")
            cursor = by_id.get(parent) if parent and parent != "0" else None
        tree[fid] = list(reversed(path))
    return tree


def category_plan(forums, threads):
    """One row per forum that actually holds threads, in the order to make them.

    A board that has been running for a decade has forums nobody ever posted
    in, and recreating those would be recreating the filing rather than the
    archive. Parents are kept even when empty, because a child needs one.
    """
    used = {row.get("fid") for row in threads}
    tree = forum_tree(forums)

    needed = set()
    for fid in used:
        for forum in tree.get(fid, []):
            needed.add(forum.get("fid"))

    rows = []
    for fid in needed:
        path = tree.get(fid, [])
        if not path:
            continue
        names = [(forum.get("name") or f"forum {forum.get('fid')}").strip()
                 for forum in path]
        if len(names) > MAX_CATEGORY_NESTING:
            # Everything past the limit becomes part of the last name.
            kept = names[: MAX_CATEGORY_NESTING - 1]
            names = kept + [" / ".join(names[MAX_CATEGORY_NESTING - 1:])]
        rows.append({
            "fid": fid,
            "name": names[-1],
            "path": names,
            "depth": len(names),
            "parent_fid": path[-2].get("fid") if len(path) > 1 else None,
            "threads": sum(1 for row in threads if row.get("fid") == fid),
        })
    # Shallowest first, so a parent exists before anything asks to go under it.
    rows.sort(key=lambda row: (row["depth"], row["name"]))
    return rows


def category_nesting_requirement(plan):
    """How deep the forum has to let categories go for this board to fit."""
    deepest = max((row["depth"] for row in plan), default=1)
    return Requirement(
        "max_category_nesting", min(deepest, MAX_CATEGORY_NESTING), AT_LEAST,
        f"the old board is {deepest} levels deep where it is deepest",
    )


def ensure_categories(poster, plan, ledger, *, dry_run=False):
    """Make the categories the old board had. Returns {fid: category id}.

    Already-made ones are found rather than remade, by name under the same
    parent, so a second run after an interruption does not end up with two of
    everything.
    """
    existing = {}
    if not dry_run:
        for category in poster.categories():
            key = (category.get("parent_category_id"), (category.get("name") or "").strip())
            existing[key] = category.get("id")

    made = {}
    for row in plan:
        known = ledger.category_for(row["fid"])
        if known:
            made[row["fid"]] = known
            continue

        parent_id = made.get(row["parent_fid"]) if row["parent_fid"] else None
        if dry_run:
            made[row["fid"]] = f"would-create:{'/'.join(row['path'])}"
            continue

        category_id = existing.get((parent_id, row["name"]))
        if category_id is None:
            category_id = poster.create_category(row["name"], parent_id=parent_id)
        made[row["fid"]] = category_id
        ledger.record_category(row["fid"], category_id)
    return made


# ---------------------------------------------------------------------------
# The run itself
# ---------------------------------------------------------------------------

def migrate_board(poster, tables, uploads_dir, ledger, *, dry_run=False,
                  limit=0, fallback_username=None, on_thread=None):
    """Move every thread. Returns a summary; the detail goes to on_thread.

    Ordered oldest first, so that a run stopped halfway leaves a forum whose
    archive ends somewhere sensible rather than one with holes through it.
    """
    posts_by_thread = {}
    for post in tables["posts"]:
        posts_by_thread.setdefault(post.get("tid"), []).append(post)

    attachments_by_post = {}
    for row in tables["attachments"]:
        attachments_by_post.setdefault(row.get("pid"), []).append(row)

    usernames_by_uid = {
        row.get("uid"): row.get("username") for row in tables["users"]
    }

    plan = category_plan(tables["forums"], tables["threads"])
    categories = ensure_categories(poster, plan, ledger, dry_run=dry_run)

    threads = sorted(
        tables["threads"], key=lambda row: int(row.get("dateline") or 0)
    )
    summary = {
        "categories": len(plan), "threads": 0, "posted": 0,
        "already_there": 0, "failed": 0, "problems": [],
    }

    for thread in threads:
        if limit and summary["threads"] >= limit:
            break
        posts = sorted(
            posts_by_thread.get(thread.get("tid"), []),
            key=lambda row: int(row.get("dateline") or 0),
        )
        if not posts:
            continue

        category_id = categories.get(thread.get("fid"))
        if category_id is None:
            summary["problems"].append(
                f"thread {thread.get('tid')}: no category for forum "
                f"{thread.get('fid')}"
            )
            continue

        summary["threads"] += 1
        try:
            report = migrate_thread(
                poster, thread, posts, attachments_by_post, usernames_by_uid,
                uploads_dir, category_id, dry_run=dry_run,
                fallback_username=fallback_username, ledger=ledger,
            )
        except (ForumProviderError, ValueError) as exc:
            # One thread that cannot be started is not a reason to abandon the
            # other 719. The ledger means it can be picked up later.
            summary["failed"] += len(posts)
            summary["problems"].append(f"thread {thread.get('tid')}: {exc}")
            continue

        for record in report["posts"]:
            if record["result"] == "posted" or record["result"] == "would post":
                summary["posted"] += 1
            elif record["result"] == "already there":
                summary["already_there"] += 1
            else:
                summary["failed"] += 1
        summary["problems"].extend(report["problems"])
        if on_thread is not None:
            on_thread(thread, report, summary)

    return summary
