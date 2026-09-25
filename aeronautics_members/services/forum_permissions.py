"""Who may read, reply and start topics where, applied by the API.

A Discourse category with no group permission on it is public -- not "visible
once you are logged in", public. So the 107 categories the importer makes
arrive open to the internet unless something says otherwise, and what goes into
them is thirteen years of exams, transcripts and summaries with students' names
on them. This has to be right *before* the posts land, because afterwards it
has been indexed, and narrowing access then is a decision somebody else has
already copied.

Permission is granted to groups, never to people, and the groups are already
driven by the portal: an active membership puts somebody in ``members`` through
Discourse Connect, a lapsed one takes it away again, and nobody has to remember
to do either. So the whole arrangement is two sentences -- make sure the groups
exist, and say what each may do in each category -- and this module is both.

Two levels, because the material is of two kinds:

    live lecture   members may start topics and reply. Students keep adding
                   exams and summaries; a read-only lecture category would make
                   the forum a museum rather than somewhere people look things
                   up and then contribute back.
    archive        members may read and search it, and nothing else. It is kept
                   because throwing it away would be worse, not because anybody
                   should be adding to a lecture that stopped running in 2017.

Categories this arrangement did not make are left alone, by name: Discourse's
own "Uncategorized" and whatever else the forum has is not ours to restrict,
and a command that quietly locked them would be a command nobody could run
twice with confidence.
"""

from .forum_board import CATEGORY_NAME_LIMIT, _fit
from .forum_mapping import ARCHIVE_ROOT, split_target

#: Discourse's own numbering, from ``CategoryGroup.permission_types``. Sent as
#: integers, so getting them the wrong way round would silently grant writing
#: where reading was meant.
CREATE = 1  # start topics, reply, see
REPLY = 2   # reply, see
SEE = 3     # see, and nothing else

LEVEL_NAMES = {CREATE: "create", REPLY: "reply", SEE: "see"}

#: Discourse's built-in group for the people who run the place. It always
#: exists, so it is granted rather than created.
STAFF_GROUP = "staff"

#: Companies and university staff, who are not members of the association. It
#: is made so that it is there to put people in, and granted nothing at all:
#: what they should see is an open question, and the answer that cannot be
#: wrong is "not the lecture material".
GUEST_GROUP = "partners"

#: Discourse's everybody-including-anonymous group. Never granted; named here
#: because taking it away is the point and a permission set that still has it
#: is the failure this module is about.
EVERYONE = "everyone"


def owned_roots(payload, archive_root=ARCHIVE_ROOT):
    """The top-level category names this arrangement is responsible for.

    Taken from the worksheet rather than from the forum, because the forum
    cannot tell "Bachelor 4. Semester", which we made, from "Site Feedback",
    which came with Discourse. Everything under one of these names is ours;
    everything else is somebody else's and is left as it is.
    """
    roots = {archive_root}
    rows = payload.get("mapping") or payload.get("rows") or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        names = split_target(row.get("target") or "")
        if len(names) >= 2 and names[0]:
            # The same shortening the importer applies when it makes the
            # category, so that a semester whose name runs past the limit is
            # still recognised as ours.
            roots.add(_fit(names[0], CATEGORY_NAME_LIMIT))
    return roots


def live_tree(categories, depth_limit=8):
    """``{id: [name, ...]}`` from the top, for every category on the forum.

    The depth limit is a guard rather than a rule: Discourse nests two deep
    here, and a parent chain that loops would otherwise hang the command
    instead of reporting a forum in a strange state.
    """
    by_id = {row.get("id"): row for row in categories if row.get("id") is not None}
    paths = {}

    for category_id, row in by_id.items():
        names, seen = [], set()
        while row is not None and len(names) < depth_limit:
            if row.get("id") in seen:
                break
            seen.add(row.get("id"))
            names.insert(0, (row.get("name") or "").strip())
            row = by_id.get(row.get("parent_category_id"))
        paths[category_id] = names

    return paths


def permission_plan(categories, roots, *, member_group,
                    staff_group=STAFF_GROUP, portal_staff_group="",
                    archive_root=ARCHIVE_ROOT):
    """``{category id: {group: level}}``, and which categories are not ours.

    Returns ``(plan, untouched)``. The second is reported rather than acted on:
    a forum where "Uncategorized" is still public is a thing to know about, and
    a command that fixed it without being asked would be one that could not be
    run without reading it first.
    """
    plan, untouched = {}, []
    for category_id, path in live_tree(categories).items():
        if not path:
            continue
        if path[0] not in roots:
            untouched.append((category_id, " / ".join(path)))
            continue
        archived = path[0] == archive_root
        grants = {member_group: SEE if archived else CREATE}
        for name in (staff_group, portal_staff_group):
            if name:
                # Everywhere, and at full level: somebody has to be able to
                # move a thread that was filed under the wrong lecture, and in
                # the archive that is most of the work that is left.
                grants[name] = CREATE
        plan[category_id] = grants
    return plan, untouched


def groups_wanted(settings, *, staff_group=STAFF_GROUP, guest_group=GUEST_GROUP):
    """Every group that has to exist before any of this works.

    ``add_groups`` in a Connect payload is not a way to *make* a group.
    Discourse matches the names against the groups it already has and drops the
    rest without a word, so a member published before ``members`` existed is in
    no group at all, and sending the same payload again changes nothing. The
    groups come first, always.
    """
    wanted = []
    for key in ("forum_member_group", "forum_onboarding_group",
                "forum_inactive_group", "forum_staff_group"):
        name = (settings.get(key) or "").strip()
        if name and name not in wanted:
            wanted.append(name)
    for name in (staff_group, guest_group):
        if name and name not in wanted:
            wanted.append(name)
    return wanted


def current_permissions(category):
    """``{group: level}`` as the forum has it now, from whichever shape it sent.

    Discourse has called this ``group_permissions`` and ``permissions`` in
    different places and versions, and an unrecognised shape reads as "no
    permissions at all" -- which would make every category look like it needed
    changing, on every run, forever.
    """
    rows = category.get("group_permissions")
    if rows is None:
        rows = category.get("permissions")
    if isinstance(rows, dict):
        return {str(name): int(level) for name, level in rows.items()}
    found = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = row.get("group_name") or row.get("name")
        level = row.get("permission_type", row.get("permission"))
        if name and level is not None:
            found[str(name)] = int(level)
    return found


def describe(grants):
    """``members: create, staff: create`` -- for a person reading a dry run."""
    return ", ".join(
        f"{name}: {LEVEL_NAMES.get(level, level)}"
        for name, level in sorted(grants.items())
    )


def apply_permissions(poster, plan, *, dry_run=False, on_category=None):
    """Set each category's permissions to what the plan says. Idempotent.

    A category already set correctly is left alone rather than written again:
    on a forum of 107 categories that is the difference between a command that
    is run whenever anybody is unsure and one that is run once a month because
    it takes a while.
    """
    report = {"categories": len(plan), "set": 0, "already": 0,
              "opened": 0, "problems": []}

    for category_id, grants in sorted(plan.items()):
        try:
            category = poster.category(category_id)
        except Exception as exc:  # provider errors differ; none is fatal here
            report["problems"].append(f"category {category_id}: {exc}")
            continue

        name = (category.get("name") or str(category_id)).strip()
        now = current_permissions(category)
        # Counted because it is the number that says whether this mattered: a
        # category with nothing on it is one anybody on the internet can read.
        was_public = not now or EVERYONE in now
        if now == grants:
            report["already"] += 1
            if on_category is not None:
                on_category(name, grants, changed=False, was_public=was_public)
            continue

        if not dry_run:
            try:
                poster.set_category_permissions(category_id, grants)
            except Exception as exc:
                report["problems"].append(f"{name}: {exc}")
                continue

        report["set"] += 1
        if was_public:
            report["opened"] += 1
        if on_category is not None:
            on_category(name, grants, changed=True, was_public=was_public)

    return report
