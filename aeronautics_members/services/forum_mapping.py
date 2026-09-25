"""Where each old forum's threads go on the new board, as decided by a person.

The other importer mirrors the old board: the categories it makes are the ones
MyBB had, which is the right shape for a test and the wrong one to keep. The
new forum is not the old one rearranged. It is where students look things up,
so its categories are the lectures that run *now*, and everything else is an
archive -- readable and searched, out of the way, and not presented as current.

Which is which is a curriculum question and cannot be computed. It is answered
in the worksheet (``forum-category-worksheet``), by somebody who knows the
curriculum, and exported as JSON. This module reads that file and turns it into
the categories to make and the thread-by-thread placement to post into.

Two levels are all Discourse allows here, which decides the shape:

    Bachelor 4. Semester            <- the semester, as the curriculum writes it
      └── Angewandte Mathematik 2   <- the lecture, holding every year of it

    Archiv
      └── Bachelor                  <- one per degree, holding everything retired

An archived topic therefore sits several lectures deep in one category, so it
carries its old lecture in its title -- "Klausuren (02-09 Angewandte Mathematik
2)" -- because "Klausuren" alone, of ninety-one, says nothing at all.
"""

from .forum_board import (
    CATEGORY_NAME_LIMIT,
    _fit,
    _subject_of,
    _title_key,
    forum_tree,
)

#: Everything retired lives under this, one category per degree below it.
ARCHIVE_ROOT = "Archiv"

#: What a target of this means: not a lecture that still runs.
ARCHIVE = "ARCHIVE"

#: Where an old forum goes when its own path does not begin with a degree.
OTHER_DEGREE = "Allgemein"


def degree_of(old_path):
    """"Bachelor" or "Master" from an old forum's path, or Allgemein.

    The worksheet says only whether a forum is archive; which degree it was is
    in the path it had, and that is enough to keep a master's student from
    wading through six semesters of bachelor's material.
    """
    head = (old_path or "").split("/")[0].strip()
    for degree in ("Bachelor", "Master"):
        if head.lower().startswith(degree.lower()):
            return degree
    return OTHER_DEGREE


def split_target(target):
    """``"Bachelor 4. Semester / Mechanik 2"`` as ``["...", "Mechanik 2"]``.

    At the first slash, because the semester is the first field and lecture
    names contain slashes: this curriculum has "CNS/ATM Systems" and
    "Professional Internship (Seminar / Advising)".
    """
    text = (target or "").strip()
    cut = text.find("/")
    if cut < 0:
        return [text] if text else []
    return [text[:cut].strip(), text[cut + 1:].strip()]


def read_mapping(payload, forums=(), threads=()):
    """The worksheet's JSON as ``{fid: [category names]}``, plus what is wrong.

    Undecided rows are archived rather than refused. The worksheet's own
    default is archive and its meaning is "not a lecture that still runs",
    which is exactly what not having decided amounts to -- but it is counted
    and said, because a file that is half-finished should not look finished.
    """
    rows = payload.get("mapping") or payload.get("rows") or []
    if not isinstance(rows, list) or not rows:
        raise ValueError(
            "That file has no mapping in it. Export it from the worksheet "
            "with the Export JSON button."
        )

    holds_threads = {row.get("fid") for row in forums} if forums else None
    with_threads = {row.get("fid") for row in threads}

    paths, archived, problems = {}, set(), []
    undecided = 0
    for row in rows:
        fid = str(row.get("old_fid") or "").strip()
        if not fid:
            continue
        if holds_threads is not None and fid not in holds_threads:
            problems.append(
                f"forum {fid} is in the mapping and not in this dump; ignored"
            )
            continue
        target = (row.get("target") or "").strip()
        if not row.get("decided") or not target:
            undecided += 1
            target = ARCHIVE
        if target == ARCHIVE:
            archived.add(fid)
            paths[fid] = [ARCHIVE_ROOT, degree_of(row.get("old_path"))]
            continue
        names = split_target(target)
        if len(names) < 2:
            # A lecture with no semester in front of it would become a
            # top-level category of its own, which is not what anybody meant.
            problems.append(
                f"forum {fid} points at {target!r}, which has no semester in "
                f"front of it; archived instead"
            )
            archived.add(fid)
            paths[fid] = [ARCHIVE_ROOT, degree_of(row.get("old_path"))]
            continue
        paths[fid] = names

    if undecided:
        problems.append(
            f"{undecided} forums were never decided in the worksheet and are "
            f"archived, which is what undecided means there"
        )
    missing = [fid for fid in with_threads if fid and fid not in paths]
    if missing:
        problems.append(
            f"{len(missing)} forums in this dump are not in the mapping at all "
            f"and are archived: {', '.join(sorted(missing)[:10])}"
            + (" ..." if len(missing) > 10 else "")
        )
        tree = forum_tree(forums)
        for fid in missing:
            path = tree.get(fid, [])
            old_path = " / ".join((row.get("name") or "") for row in path)
            archived.add(fid)
            paths[fid] = [ARCHIVE_ROOT, degree_of(old_path)]

    return {"paths": paths, "archived": archived, "problems": problems}


def mapping_plan(paths_by_fid, threads):
    """The categories to make, shallowest first, and which forum goes in which.

    The same shape ``category_plan`` returns, so the same ``ensure_categories``
    makes them: several old forums share one category here by design, which is
    the whole point of the exercise.
    """
    counts = {}
    for thread in threads:
        fid = thread.get("fid")
        counts[fid] = counts.get(fid, 0) + 1

    rows, taken = {}, {}

    def place(names, fid=None, threads_here=0):
        parent_key = place(names[:-1]) if len(names) > 1 else None
        wanted = _fit(names[-1])
        # Two lectures whose names differ past the fiftieth character would
        # otherwise become one category, and one would lose its threads into
        # the other.
        clash = taken.get((parent_key, wanted))
        if clash is not None and clash != tuple(names):
            suffix = f" ({len(taken)})"
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

    for fid, names in sorted(paths_by_fid.items()):
        if not names or not counts.get(fid):
            # A forum nobody ever posted in needs no category. Making one for
            # every lecture in the curriculum would fill the new board with
            # empty rooms on its first day.
            continue
        place(names, fid=fid, threads_here=counts.get(fid, 0))

    return sorted(rows.values(), key=lambda row: (row["depth"], row["key"]))


def titles_for(forums, threads, archived=frozenset(), limit=None):
    """A title per thread that no other thread on the board shares.

    Discourse refuses a second topic with a title it already has, and 323 of
    these 720 threads share a subject. What tells them apart is the lecture
    they were filed under, which is also the thing an archived topic needs in
    its title anyway: one category holds every retired lecture of a degree, so
    "Klausuren" on its own is one of ninety-one.

    So an archived thread is labelled whether it collides or not, and a live
    one only when it has to be -- inside a lecture's own category the subject
    is already unambiguous, and "Klausuren" reads better than "Klausuren
    (02-09 Angewandte Mathematik 2)" when the category says the same thing.
    """
    from .forum_board import TITLE_LENGTH_LIMIT, _year_of

    limit = TITLE_LENGTH_LIMIT if limit is None else limit
    tree = forum_tree(forums)

    def lecture_of(thread):
        path = tree.get(thread.get("fid"), [])
        return (path[-1].get("name") or "").strip() if path else ""

    def year_of(thread):
        return _year_of(thread.get("dateline")) or ""

    def fit(text):
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

    def base(thread):
        subject = _subject_of(thread)
        lecture = lecture_of(thread)
        if thread.get("fid") in archived and lecture:
            return f"{subject} ({lecture})"
        return subject

    # Tried in turn, and the first that tells every thread in the group apart
    # is used for all of them, so that one "Klausuren (Luftfahrtrecht)" never
    # sits beside a "Klausuren (Luftfahrtrecht 2019)" that is from the same year.
    def with_lecture(thread):
        lecture = lecture_of(thread)
        if not lecture:
            return None
        text = base(thread)
        return text if f"({lecture})" in text else f"{text} ({lecture})"

    def with_year(thread):
        lecture = lecture_of(thread)
        text = base(thread)
        if lecture and f"({lecture})" in text:
            return f"{text[:text.rindex(')')]} {year_of(thread)})"
        return f"{text} ({lecture} {year_of(thread)})" if lecture \
            else f"{text} ({year_of(thread)})"

    schemes = (base, with_lecture, with_year,
               lambda thread: f"{base(thread)} #{thread.get('tid')}")

    groups = {}
    for thread in threads:
        groups.setdefault(_title_key(base(thread)), []).append(thread)

    titles, used = {}, set()
    for sharing in groups.values():
        names = [fit(_subject_of(thread)) for thread in sharing]
        for scheme in schemes:
            attempt = [scheme(thread) for thread in sharing]
            if any(name is None for name in attempt):
                continue
            attempt = [fit(name) for name in attempt]
            keys = [_title_key(name) for name in attempt]
            if len(set(keys)) == len(keys) and not (set(keys) & used):
                names = attempt
                break
        for thread, name in zip(sharing, names):
            titles[thread.get("tid")] = name
            used.add(_title_key(name))
    return titles
