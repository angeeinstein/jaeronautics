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
from collections import Counter
from pathlib import Path

from flask import current_app

from ..forum_service import ForumProviderError
from .forum_content import AT_LEAST, Requirement, migrate_thread

#: How deep Discourse nests categories. Three is possible, but only where the
#: site reports max_category_nesting and lets it be set to three -- on a forum
#: that does not, the third level is refused with "You can't nest a
#: subcategory under another", one category at a time, after every setting has
#: already been changed. Two is what every Discourse allows, so two is the
#: default and three is asked for only when the site says it is available.
MAX_CATEGORY_NESTING = 2
DEEPEST_CATEGORY_NESTING = 3

#: Discourse refuses a category name longer than this.
CATEGORY_NAME_LIMIT = 50


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
    """The old board's forums, each with its ancestors, deepest last."""
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


def _fit(name, limit=CATEGORY_NAME_LIMIT):
    """A category name Discourse will accept. It stops at fifty characters."""
    name = " ".join((name or "").split())
    if len(name) <= limit:
        return name
    return name[: limit - 1].rstrip() + "\u2026"


def _join_to_fit(names, limit=CATEGORY_NAME_LIMIT):
    """Several levels as one name, dropping from the outside in to make it fit.

    "Studium / Bachelor Luftfahrt / Aviation / 01 Semester" is 53 characters
    and Discourse takes 50, so something has to go. Cutting the end would take
    the semester number -- the one part anybody navigates by -- and leave the
    board heading, which nobody does. So the outermost level goes first.
    """
    kept = list(names)
    while len(kept) > 1 and len(" / ".join(kept)) > limit:
        kept.pop(0)
    return _fit(" / ".join(kept), limit)


def _compress(ancestors, max_depth):
    """The ancestors of a forum, in as many levels as the forum allows.

    The leaf keeps its own level -- it is the lecture, the thing anybody is
    actually looking for -- and the levels above it are joined from the top
    down until they fit. "Studium / Bachelor / 3. Semester" is a worse name
    than three categories would be, and a much better one than losing the
    distinction between a Bachelor and a Master semester of the same number.
    """
    keep = max_depth - 1
    if keep <= 0 or not ancestors:
        return []
    if len(ancestors) <= keep:
        return list(ancestors)
    fold = len(ancestors) - keep + 1
    return [_join_to_fit(ancestors[:fold])] + list(ancestors[fold:])


def category_plan(forums, threads, max_depth=MAX_CATEGORY_NESTING):
    """The categories to make, shallowest first, and which forum goes in which.

    Keyed by the name path rather than by a MyBB forum id, because once the
    tree is compressed several forums can share one category and some
    categories belong to no forum at all.

    A forum nobody ever posted in gets no category of its own. Recreating
    those would be recreating the filing rather than the archive -- though its
    name still shows up in its descendants' parent, which is where it was
    doing any work.
    """
    used = {row.get("fid") for row in threads}
    tree = forum_tree(forums)
    counts = Counter(row.get("fid") for row in threads)

    rows = {}
    taken = {}

    def place(names, fid=None, threads_here=0):
        """One category, and every category above it. Returns its key."""
        parent_key = place(names[:-1]) if len(names) > 1 else None
        wanted = _fit(names[-1])

        # Two lectures whose names differ past the fiftieth character would
        # otherwise become one category, and one of them would lose its
        # threads into the other.
        clash = taken.get((parent_key, wanted))
        if clash is not None and clash != tuple(names):
            suffix = f" ({fid})" if fid else f" ({len(taken)})"
            wanted = _fit(names[-1], CATEGORY_NAME_LIMIT - len(suffix)) + suffix

        key = "path:" + "/".join(names)
        row = rows.get(key)
        if row is None:
            row = rows[key] = {
                "key": key, "name": wanted, "path": list(names),
                "depth": len(names), "parent_key": parent_key,
                "fids": [], "threads": 0,
            }
            taken[(parent_key, wanted)] = tuple(names)
        if fid is not None and fid not in row["fids"]:
            row["fids"].append(fid)
        row["threads"] += threads_here
        return key

    for fid in used:
        path = tree.get(fid, [])
        if not path:
            continue
        names = [(forum.get("name") or f"forum {forum.get('fid')}").strip()
                 for forum in path]
        place(_compress(names[:-1], max_depth) + [names[-1]],
              fid=fid, threads_here=counts[fid])

    # Shallowest first, so a parent exists before anything asks to go under it.
    return sorted(rows.values(), key=lambda row: (row["depth"], row["name"]))


def categories_by_forum(plan, made):
    """Which Discourse category each old forum's threads belong in."""
    return {
        fid: made[row["key"]]
        for row in plan if row["key"] in made
        for fid in row["fids"]
    }


def category_nesting_requirement(plan):
    """How deep the forum has to let categories go for this board to fit."""
    deepest = max((row["depth"] for row in plan), default=1)
    return Requirement(
        "max_category_nesting", deepest, AT_LEAST,
        f"the categories this makes are {deepest} levels deep",
    )


def audit_uploads(attachments, uploads_dir):
    """Which of the old board's files are on this machine, and which are not.

    Nine gigabytes fetched a directory at a time, over several sittings, is not
    something anybody can hold in their head, and the import reports a missing
    file per post rather than as a list -- which is the right shape for one
    thread and the wrong one for 2,347.

    Size is checked as well as presence, because the usual way a file goes
    wrong here is not absence. A web server asked for something it does not
    have answers with a page saying so, and a fetch that does not check will
    write that page to disk under the name of the file it wanted: present,
    readable, and 200 bytes of HTML where a PDF should be.
    """
    uploads_dir = Path(uploads_dir)
    report = {"expected": 0, "present": 0, "missing": [], "wrong_size": [],
              "bytes_missing": 0, "bytes_present": 0}

    for row in attachments:
        name = row.get("attachname") or ""
        if not name:
            continue
        report["expected"] += 1
        recorded = int(row.get("filesize") or 0)
        path = uploads_dir / name

        try:
            on_disk = path.stat().st_size
        except OSError:
            report["missing"].append(name)
            report["bytes_missing"] += recorded
            continue

        report["present"] += 1
        report["bytes_present"] += on_disk
        if recorded and on_disk != recorded:
            report["wrong_size"].append({
                "name": name, "recorded": recorded, "on_disk": on_disk,
            })
    return report


def ensure_categories(poster, plan, ledger, *, dry_run=False, problems=None):
    """Make the categories the old board had. Returns {fid: category id}.

    Already-made ones are found rather than remade, by name under the same
    parent, so a second run after an interruption does not end up with two of
    everything.

    One category the forum will not make is one forum's worth of threads with
    nowhere to go, reported and skipped. It is not a reason to abandon the
    other 274 -- and the commonest cause, a forum too deeply nested for this
    Discourse to accept, would otherwise take the whole run down at the point
    where it has already changed every setting.
    """
    existing = {}
    if not dry_run:
        for category in poster.categories():
            name = (category.get("name") or "").strip()
            existing[(category.get("parent_category_id"), name)] = category.get("id")

    made = {}
    for row in plan:
        known = ledger.category_for(row["key"])
        if known:
            made[row["key"]] = known
            continue

        parent_id = made.get(row["parent_key"]) if row["parent_key"] else None
        if row["parent_key"] and parent_id is None:
            # Its parent could not be made either. Saying so once per level is
            # noise; the parent's failure is already reported.
            continue

        if dry_run:
            made[row["key"]] = f"would-create:{'/'.join(row['path'])}"
            continue

        category_id = existing.get((parent_id, row["name"]))
        if category_id is None:
            try:
                category_id = poster.create_category(row["name"], parent_id=parent_id)
            except ForumProviderError as exc:
                if problems is not None:
                    problems.append(f"category {' / '.join(row['path'])}: {exc}")
                current_app.logger.warning(
                    "Could not make category %s: %s", row["name"], exc
                )
                continue
            # Remembered, so a name that appears twice in the plan -- a forum
            # that is both a lecture and the parent of one -- resolves to the
            # category just made rather than being made again.
            existing[(parent_id, row["name"])] = category_id
        made[row["key"]] = category_id
        ledger.record_category(row["key"], category_id)
    return made


# ---------------------------------------------------------------------------
# The run itself
# ---------------------------------------------------------------------------

def migrate_board(poster, tables, uploads_dir, ledger, *, dry_run=False,
                  limit=0, fallback_username=None, on_thread=None,
                  require_attachments=True, max_depth=MAX_CATEGORY_NESTING):
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

    plan = category_plan(tables["forums"], tables["threads"], max_depth)
    summary = {
        "categories": len(plan), "threads": 0, "posted": 0,
        "already_there": 0, "waiting": 0, "failed": 0, "problems": [],
    }
    categories = categories_by_forum(plan, ensure_categories(
        poster, plan, ledger, dry_run=dry_run, problems=summary["problems"]
    ))

    threads = sorted(
        tables["threads"], key=lambda row: int(row.get("dateline") or 0)
    )

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
                require_attachments=require_attachments,
            )
        except (ForumProviderError, ValueError) as exc:
            # One thread that cannot be started is not a reason to abandon the
            # other 719. The ledger means it can be picked up later.
            summary["failed"] += len(posts)
            summary["problems"].append(f"thread {thread.get('tid')}: {exc}")
            continue

        for record in report["posts"]:
            if record["result"] in ("posted", "would post"):
                summary["posted"] += 1
            elif record["result"] == "already there":
                summary["already_there"] += 1
            elif record["result"].startswith(("waiting", "not attempted")):
                # Not a failure. Its files are not here yet, and the same
                # command run again once they are will pick it up.
                summary["waiting"] += 1
            else:
                summary["failed"] += 1
        summary["problems"].extend(report["problems"])
        if on_thread is not None:
            on_thread(thread, report, summary)

    return summary
