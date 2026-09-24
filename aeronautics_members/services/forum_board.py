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
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
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

#: And a topic title longer than this.
TITLE_LENGTH_LIMIT = 255


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


#: Settings whose value is nobody's business, even on a test box. Discourse
#: marks most of them itself; the name check is for the ones it does not, and
#: for plugins that invent their own.
SECRET_SETTING_WORDS = ("secret", "password", "token", "api_key", "private_key",
                        "client_id", "credential")


def is_secret_setting(row):
    """Should this setting's value be left out of a file somebody will share?"""
    if row.get("secret"):
        return True
    name = (row.get("setting") or "").lower()
    return any(word in name for word in SECRET_SETTING_WORDS)


#: What a setting's name looks like when it constrains what can be posted.
#: This is the list that should have been read rather than guessed at: the
#: third level of categories and the fifty-character name were both in it.
LIMIT_WORDS = ("max_", "min_", "_max", "_min", "rate_limit", "limit",
               "length", "nesting", "entropy", "allow_", "unique_")


def settings_inventory(rows):
    """Every setting, with the ones that constrain an import marked.

    Returns (all, interesting). Values of secret settings are left out.
    """
    everything, interesting = [], []
    for row in rows:
        name = row.get("setting")
        if not name:
            continue
        entry = {
            "setting": name,
            "value": "(hidden)" if is_secret_setting(row) else row.get("value"),
            "default": "(hidden)" if is_secret_setting(row) else row.get("default"),
            "category": row.get("category"),
            "description": (row.get("description") or "").strip(),
        }
        everything.append(entry)
        if any(word in name.lower() for word in LIMIT_WORDS):
            interesting.append(entry)
    everything.sort(key=lambda entry: entry["setting"])
    interesting.sort(key=lambda entry: entry["setting"])
    return everything, interesting


def _sortable(name):
    """A lecture name with the parts that vary between years taken off.

    The board writes the same course as "01-06 Technisches Programmieren" one
    year and "02-07 Technisches Programmieren 1" the next: the code at the
    front is the semester slot, which moves, and the name is the course, which
    mostly does not. Sorting on the name alone puts every version of a course
    on adjacent lines, which is all that is needed -- the question of whether
    they really are the same course is a curriculum question, and nobody here
    should be answering it by string comparison.
    """
    name = re.sub(r"^\s*\d+\s*[-.]\s*\d+\s*", "", name or "")
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()


def category_worksheet(forums, threads, posts, attachments=(), samples=8):
    """One row per old forum that holds threads, for deciding where it goes.

    The new forum is not the old one rearranged: it is a place students look
    things up, and most of a decade-old board is lectures that no longer run.
    So the question for each old forum is which current lecture it belongs to,
    or whether it is archive -- and that is a curriculum question, answerable
    only by somebody who knows the curriculum.

    What can be supplied is the evidence: how much is in there, and when it
    stopped. A forum whose last post is from 2017 is not a live lecture.
    """
    tree = forum_tree(forums)
    threads_by_forum = {}
    for row in threads:
        threads_by_forum.setdefault(row.get("fid"), []).append(row)

    # Which thread each attachment hangs off, so a forum can show what is
    # actually in it. A filename like "Klausur_LAV16_Musterloesung_27.01.2017"
    # says more about whether two lectures are the same course than any amount
    # of comparing their titles does.
    thread_of_post = {}
    for post in posts:
        thread_of_post[post.get("pid")] = post.get("tid")
    files_by_thread = {}
    for row in attachments:
        tid = thread_of_post.get(row.get("pid"))
        if tid is not None:
            name = (row.get("filename") or "").strip()
            if name:
                files_by_thread.setdefault(tid, []).append(name)

    dates_by_thread = {}
    counts_by_thread = Counter()
    for post in posts:
        tid = post.get("tid")
        counts_by_thread[tid] += 1
        when = int(post.get("dateline") or 0)
        first, last = dates_by_thread.get(tid, (when, when))
        dates_by_thread[tid] = (min(first, when), max(last, when))

    rows = []
    for fid, in_forum in threads_by_forum.items():
        path = tree.get(fid, [])
        if not path:
            continue
        names = [(forum.get("name") or f"forum {forum.get('fid')}").strip()
                 for forum in path]
        spans = [dates_by_thread[row.get("tid")] for row in in_forum
                 if row.get("tid") in dates_by_thread]
        newest = sorted(
            in_forum,
            key=lambda row: dates_by_thread.get(row.get("tid"), (0, 0))[1],
            reverse=True,
        )
        files = [name for row in newest
                 for name in files_by_thread.get(row.get("tid"), [])]
        rows.append({
            "old_fid": fid,
            "old_path": " / ".join(names),
            "lecture": names[-1],
            "threads": len(in_forum),
            "posts": sum(counts_by_thread[row.get("tid")] for row in in_forum),
            "first_post": _as_day(min(span[0] for span in spans)) if spans else "",
            "last_post": _as_day(max(span[1] for span in spans)) if spans else "",
            "subjects": [(row.get("subject") or "").strip()
                         for row in newest[:samples]],
            "files": files[:samples],
            "target": "",
            "access": "",
        })

    # By course name, so every year's version of the same course is adjacent,
    # and newest first within a course so the live one is the line on top.
    rows.sort(key=lambda row: (_sortable(row["lecture"]), row["last_post"]),
              reverse=False)
    return rows


def _as_day(dateline):
    return datetime.fromtimestamp(int(dateline), tz=timezone.utc).strftime("%Y-%m-%d")


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


def unique_titles(forums, threads, limit=TITLE_LENGTH_LIMIT):
    """A title per thread that no other thread on the board shares.

    Discourse refuses a second topic with a title it already has -- "This
    title has already been used by another topic" -- and 323 of these 720
    threads share a subject with another. Ninety-one are called "Klausuren".
    The setting that used to turn this off is not in the site's settings any
    more, so the titles have to do the work instead.

    What gets added is the lecture the thread was filed under, which is the
    thing that told the old board's readers which "Klausuren" they were
    looking at. Where that is still not enough, the year; and after that the
    thread's own id, which is ugly and unique and only reached by threads that
    were already indistinguishable.
    """
    tree = forum_tree(forums)
    subjects = {}
    for thread in threads:
        subject = (thread.get("subject") or "").strip() or "(no subject)"
        subjects.setdefault(subject, []).append(thread)

    def fit(text):
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"

    def lecture_of(thread):
        path = tree.get(thread.get("fid"), [])
        return (path[-1].get("name") or "").strip() if path else ""

    def year_of(thread):
        return datetime.fromtimestamp(
            int(thread.get("dateline") or 0), tz=timezone.utc
        ).year

    # Tried in turn, and the first that tells every thread in the group apart
    # is used for all of them. Picking per thread instead would leave one
    # "Klausuren (Luftfahrtrecht)" beside a "Klausuren (Luftfahrtrecht 2019)",
    # where the first is from 2017 and does not say so.
    schemes = (
        lambda subject, thread: subject,
        lambda subject, thread: (
            f"{subject} ({lecture_of(thread)})" if lecture_of(thread) else None
        ),
        lambda subject, thread: (
            f"{subject} ({lecture_of(thread)} {year_of(thread)})"
            if lecture_of(thread) else f"{subject} ({year_of(thread)})"
        ),
        lambda subject, thread: f"{subject} #{thread.get('tid')}",
    )

    titles, taken = {}, set()
    for subject, sharing in subjects.items():
        for scheme in schemes:
            names = [scheme(subject, thread) for thread in sharing]
            if any(name is None for name in names):
                continue
            names = [fit(name) for name in names]
            if len(set(names)) == len(names) and not (set(names) & taken):
                break
        for thread, name in zip(sharing, names):
            titles[thread.get("tid")] = name
            taken.add(name)
    return titles


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
                  require_attachments=True, max_depth=MAX_CATEGORY_NESTING,
                  keep_duplicate_titles=False):
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
    titles = (
        {} if keep_duplicate_titles
        else unique_titles(tables["forums"], tables["threads"])
    )
    renamed = sum(
        1 for thread in tables["threads"]
        if titles.get(thread.get("tid"), "") != (thread.get("subject") or "").strip()
        and thread.get("tid") in titles
    )
    summary = {
        "categories": len(plan), "threads": 0, "posted": 0, "renamed": renamed,
        "already_there": 0, "waiting": 0, "not_attempted": 0, "failed": 0,
        "problems": [],
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
                title=titles.get(thread.get("tid")),
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
            elif record["result"].startswith("waiting"):
                # Not a failure. Its files are not here yet, or the forum
                # would not take one of them, and the same command run again
                # once that is fixed will pick it up.
                summary["waiting"] += 1
            elif record["result"].startswith("not attempted"):
                # Its thread never got a topic. The reason is reported against
                # the post that failed, not against this one.
                summary["not_attempted"] += 1
            else:
                summary["failed"] += 1
        summary["problems"].extend(report["problems"])
        if on_thread is not None:
            on_thread(thread, report, summary)

    return summary
