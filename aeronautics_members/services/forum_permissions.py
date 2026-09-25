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

from ..member_categories import CATEGORY_ORDER
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


def permission_plan(categories, roots, *, lecture_groups, archive_groups=None,
                    staff_groups=(STAFF_GROUP,), archive_root=ARCHIVE_ROOT):
    """``{category id: {group: level}}``, and which categories are not ours.

    ``lecture_groups`` is the whole of the access decision and there is no
    default for it, because the obvious one is wrong. "Everybody who has paid"
    is not who may read the exams: a lecturer and a company representative are
    full members of this association who pay the same fee, and the material is
    a decade of exam papers and transcripts *about* the lectures they give.
    Granting it to the member group would show every exam to the people who
    set them.

    So the groups named here are the ones the portal fills from what kind of
    member somebody is -- and fills only while their membership is current, so
    that "has paid" and "is a student here" are both true of everybody in them.
    Discourse's own permission check is a union across groups, never an
    intersection, so the conjunction has to be made on this side. It is.

    The plan comes back **deepest first**, and that is not tidiness. Discourse
    refuses to restrict a category while one of its subcategories still lets in
    a group the parent would not:

        Any group that is allowed to access a subcategory must also be allowed
        to access the parent category. The following groups have access to one
        of the subcategories, but no access to parent category: everyone.

    Every category starts public, so restricting a semester before its lectures
    is restricting a parent while eleven children still admit everyone -- and
    all ten top-level categories are refused, which is exactly what happened the
    first time this ran against a real forum.

    Returns ``(plan, untouched)``. The second is reported rather than acted on:
    a forum where "Uncategorized" is still public is a thing to know about, and
    a command that fixed it without being asked would be one that could not be
    run without reading it first.
    """
    lecture = [name for name in lecture_groups if name]
    archive = [name for name in
               (lecture if archive_groups is None else archive_groups) if name]
    if not lecture and not archive:
        raise ValueError(
            "No group may read the lecture material, so there is nobody to "
            "grant it to. Name the groups under Admin -> Settings -> Forum "
            "before importing."
        )

    rows, untouched = [], []
    for category_id, path in live_tree(categories).items():
        if not path:
            continue
        if path[0] not in roots:
            untouched.append((category_id, " / ".join(path)))
            continue
        archived = path[0] == archive_root
        # Read and search, and nothing more, for a lecture that stopped
        # running: it is kept because throwing it away would be worse, not so
        # that anybody adds to it.
        grants = {name: SEE for name in archive} if archived \
            else {name: CREATE for name in lecture}
        for name in staff_groups:
            if name:
                # Everywhere, and at full level: somebody has to be able to
                # move a thread that was filed under the wrong lecture, and in
                # the archive that is most of the work that is left.
                grants[name] = CREATE
        rows.append((len(path), category_id, grants))

    rows.sort(key=lambda row: (-row[0], row[1]))
    return {category_id: grants for _depth, category_id, grants in rows}, untouched


def groups_wanted(settings, *, staff_group=STAFF_GROUP, extra=()):
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
    for name in tuple(extra) + (staff_group,):
        if name and name not in wanted:
            wanted.append(name)
    return wanted


def access_groups(settings, key, fallback=()):
    """The group names in one of the two access settings, in order.

    Comma or newline separated, because both are what somebody types into a
    box, and an empty one is empty rather than a group called "".
    """
    raw = str(settings.get(key) or "").replace("\n", ",")
    names = [name.strip() for name in raw.split(",") if name.strip()]
    return names or [name for name in fallback if name]


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


def apply_permissions(poster, plan, *, dry_run=False, enforce=False,
                      on_category=None):
    """Close every category that is still open. Idempotent, and not destructive.

    By default only categories that are **public** are written -- ones with no
    permissions at all, or which still grant ``everyone``. A category somebody
    has already given a deliberate set of permissions to is counted, reported
    and left exactly as it is.

    That is the important half. Who may see what is a judgement made on the
    forum, over months, one category at a time; a command that reimposed its
    own idea of the answer every time it ran would quietly undo all of it, and
    the undoing would look like nothing at all. So the standing job here is
    narrower and worth doing forever: nothing is ever left open.

    ``enforce`` is the other mode, for the first run and for putting a forum
    back to a known state on purpose.

    The plan arrives deepest first, because Discourse will not let a parent be
    restricted while a subcategory still admits a group the parent would not.
    Anything refused anyway is tried once more in the opposite order, which is
    what a *widening* needs: there the parent has to be opened before the child
    is allowed to be.
    """
    report = {"categories": len(plan), "set": 0, "already": 0, "opened": 0,
              "decided_elsewhere": 0, "problems": []}

    def attempt(category_id, grants):
        """Returns None when it is settled, or the reason it is not."""
        try:
            category = poster.category(category_id)
        except Exception as exc:  # provider errors differ; none is fatal here
            return f"category {category_id}: {exc}"

        name = (category.get("name") or str(category_id)).strip()
        now = current_permissions(category)
        # Counted because it is the number that says whether this mattered: a
        # category with nothing on it is one anybody on the internet can read.
        was_public = not now or EVERYONE in now
        if now == grants:
            report["already"] += 1
            if on_category is not None:
                on_category(name, grants, changed=False, was_public=was_public)
            return None

        if not was_public and not enforce:
            # Somebody decided this one, and they knew something this command
            # does not. Said, so that a deliberate difference is visible rather
            # than silent, and left alone.
            report["decided_elsewhere"] += 1
            if on_category is not None:
                on_category(name, now, changed=False, was_public=False)
            return None

        if not dry_run:
            try:
                poster.set_category_permissions(category_id, grants)
            except Exception as exc:
                return f"{name}: {exc}"

        report["set"] += 1
        if was_public:
            report["opened"] += 1
        if on_category is not None:
            on_category(name, grants, changed=True, was_public=was_public)
        return None

    refused = []
    for category_id, grants in plan.items():
        problem = attempt(category_id, grants)
        if problem is not None:
            refused.append((category_id, grants))

    for category_id, grants in reversed(refused):
        problem = attempt(category_id, grants)
        if problem is not None:
            report["problems"].append(problem)

    return report


def unknown_member_kinds(settings):
    """Lines in the mapping whose left-hand side is not a kind of member.

    The left-hand side is fixed: it is what this portal stores on a member, and
    there are five of them. A line naming anything else is not an error
    anywhere -- it is read, matched against nothing, and dropped -- so a typo
    means a group that is never made and people who are never sorted, with
    everything reporting success. Said out loud instead.
    """
    unknown = []
    for line in str(settings.get("forum_category_groups") or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        kind, sep, group = line.partition("=")
        if not sep or not group.strip():
            unknown.append(line)
            continue
        if kind.strip().lower() not in CATEGORY_ORDER:
            unknown.append(line)
    return unknown


def what_is_not_set_up(settings):
    """What still has to be decided before any of this means anything.

    Every one of these is a setting whose empty value is a silence rather than
    an error: no group to grant the material to, no way to tell a lecturer from
    a student, nobody named as running the place. A run with them empty does
    something defensible and not what anybody wanted, so they are said out loud
    before the run rather than deduced from its results afterwards.
    """
    missing = []
    if not access_groups(settings, "forum_lecture_groups"):
        missing.append((
            "forum_lecture_groups",
            "Nobody may read the lecture material, so there is nothing to "
            "grant and the run will stop. Name the groups that may -- students "
            "and alumni, not the member group.",
        ))
    if not str(settings.get("forum_category_groups") or "").strip():
        missing.append((
            "forum_category_groups",
            "Nobody is sorted by what kind of member they are, so the groups "
            "above will never have anybody in them. One 'student = students' "
            "line per kind.",
        ))
    strange = unknown_member_kinds(settings)
    if strange:
        missing.append((
            "forum_category_groups",
            f"{len(strange)} lines name something that is not a kind of member "
            f"and do nothing at all: {'; '.join(strange[:3])}. The left-hand "
            f"side must be one of {', '.join(CATEGORY_ORDER)}.",
        ))
    if not str(settings.get("forum_staff_group") or "").strip():
        missing.append((
            "forum_staff_group",
            "No group follows the portal's own forum-admin role, so whoever "
            "runs the forum is whoever Discourse's own staff flags say, "
            "maintained by hand over there.",
        ))
    if not str(settings.get("forum_inactive_group") or "").strip():
        missing.append((
            "forum_inactive_group",
            "People whose membership is not current are put in no group, so "
            "nothing can be shown to them -- an empty forum with no "
            "explanation of why.",
        ))
    return missing
