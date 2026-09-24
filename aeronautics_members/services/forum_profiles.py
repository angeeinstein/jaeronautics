"""Publishing the old forum's people to the new one, so they can be found.

The old board doubles as the association's register of everyone who was ever a
member: name, year group, face. People use it to find somebody from an earlier
cohort -- to ask about an internship at the company they ended up at, or simply
to work out whether they know them. That is worth keeping, and rebuilding it in
the portal would mean writing a second member directory when the forum already
has one.

So every imported person gets a forum profile, not only the 242 who posted.
Somebody who never wrote anything is still in the register, exactly as they are
on the old board today.

These are not accounts anybody can use. They have no password here, so no
DiscourseConnect login can ever resolve to one, and the address on them is on a
domain that cannot exist. They are a name, a face and a year group, and nothing
else.

Grouped by year group, one Discourse group each. A group page lists all of its
members whichever way the user directory happens to be sorted or filtered, and
"who was in LAV18" is the question people actually ask.
"""

import secrets

from flask import current_app

from ..db_models import ImportedForumProfile, db
from ..forum_service import ForumProviderError
from ..security_utils import build_public_url
from .clock import get_now_utc

# Everyone imported lands here as well as in their year group, so the whole
# register can be addressed at once -- given a category to read, or taken away
# from one -- without naming thirty-three groups.
ARCHIVE_GROUP = "old_forum"

# The custom field the year group is shown in on a forum profile.
YEAR_GROUP_FIELD_NAME = "Year group"

# Discourse group names take letters, numbers and underscores. Year groups are
# already of the form LAV23, but the export holds whatever people typed.
def group_name_for_year_group(year_group):
    cleaned = "".join(
        character if character.isalnum() else "_"
        for character in (year_group or "").strip()
    ).strip("_")
    return cleaned.lower() or None


def _avatar_url_for(profile, *, dry_run=False):
    """A URL the forum can fetch this avatar from, minting a token if needed."""
    if not profile.avatar_path:
        return None
    if not profile.avatar_public_token:
        if dry_run:
            # Reported, not stored: a rehearsal that wrote tokens would leave
            # them behind when the transaction it ran in was rolled back.
            return "(a token would be minted)"
        # Hex rather than token_urlsafe: that alphabet contains "-", so about
        # one token in thirty-two begins with one and is then swallowed as an
        # option flag the first time somebody pastes the URL into curl to find
        # out why an avatar is not showing. A token that cannot be pasted
        # wastes an afternoon looking for a fault that is not there.
        profile.avatar_public_token = secrets.token_hex(32)
        db.session.flush()
    return build_public_url(
        "forum.forum_imported_avatar_public_file", token=profile.avatar_public_token
    )


def build_profile_payload(profile, avatar_url=None, year_group_field=None):
    """What the forum is told about one imported person.

    Deliberately not built from ``build_sso_payload``: that one describes a
    member, and for somebody with no membership it falls back to using their
    email address as their name -- which here is a placeholder on a domain that
    does not resolve, so every profile would be called
    ``forum-mybb-645@imported.invalid``.

    ``require_activation`` is false. It is true on the member path because an
    unverified address should be proved before it is trusted; here the address
    is known not to be real, and asking Discourse to activate it would send 740
    emails to a domain reserved for never resolving.
    """
    groups = [ARCHIVE_GROUP]
    year_group = (profile.year_group or "").strip()
    cohort_group = group_name_for_year_group(year_group)
    if cohort_group:
        groups.append(cohort_group)

    payload = {
        # Carried for parity with the member sync, which is the path known to
        # work. The admin sync endpoint does not check it, but there is no
        # reason for this payload to differ from that one in any way that is
        # not deliberate.
        "nonce": f"import-{profile.user_id}-{secrets.token_hex(8)}",
        "external_id": str(profile.user_id),
        "email": profile.user.email,
        "username": profile.source_username,
        "name": profile.display_name or profile.source_username,
        "require_activation": "false",
        "add_groups": ",".join(groups),
    }
    if avatar_url:
        payload["avatar_url"] = avatar_url
        payload["avatar_force_update"] = "true"
    if year_group and year_group_field:
        payload[f"custom.{year_group_field}"] = year_group
    return payload


def profiles_to_publish(only_unsynced=False):
    query = db.select(ImportedForumProfile).order_by(ImportedForumProfile.id)
    if only_unsynced:
        query = query.where(ImportedForumProfile.forum_synced_at.is_(None))
    return db.session.execute(query).scalars().all()


def groups_for_profiles(profiles):
    """Which group holds whom: ``{group name: [username, ...]}``.

    Worked out from the database rather than from what a run reported, so the
    groups can be made *before* anybody is published. That order matters:
    Discourse's SSO ``add_groups`` matches the names it is given against the
    groups that already exist and drops the others without a word, so a profile
    published before its cohort group existed lands in no group at all -- and
    the report says otherwise, because it counts what was sent.
    """
    plan = {}
    for profile in profiles:
        names = [ARCHIVE_GROUP]
        cohort = group_name_for_year_group(profile.year_group)
        if cohort:
            names.append(cohort)
        for name in names:
            plan.setdefault(name, []).append(profile.source_username)
    return plan


def sync_profile_groups(provider, plan, *, add_members=True, on_group=None):
    """Make each group and, unless told otherwise, put its people in it.

    Adding the members here rather than leaving it to the SSO payload is both
    the repair for a run that published into groups that did not exist yet and
    the check that the payload did what it claimed: it is one call per hundred
    people, against fourteen hundred for publishing everybody again.
    """
    report = {"groups": 0, "created": 0, "members": 0, "problems": []}
    for name in sorted(plan):
        usernames = plan[name]
        created = False
        try:
            group, created = provider.ensure_group(name)
            report["groups"] += 1
            if created:
                report["created"] += 1
            if add_members:
                group_id = (group or {}).get("id")
                if not group_id:
                    raise ForumProviderError(
                        "the forum did not say which group that is, so nobody "
                        "can be added to it"
                    )
                report["members"] += provider.add_group_members(group_id, usernames)
        except ForumProviderError as exc:
            # One group out of thirty-four must not cost the other thirty-three.
            report["problems"].append(f"{name}: {exc}")
        if on_group is not None:
            on_group(name, len(usernames), created)
    return report


def publish_imported_profiles(
    provider,
    *,
    dry_run=False,
    limit=None,
    only_unsynced=False,
    year_group_field=None,
    on_progress=None,
):
    """Push every imported person to the forum. Returns a report.

    Safe to run again: Discourse matches on ``external_id``, so a second run
    updates the same profile rather than making another. That matters because
    this is one API call per person and something will go wrong partway through
    740 of them -- a run that cannot be resumed is one that has to start over.

    ``on_progress(done, total, report)`` is called after each person, whether
    they were published or not. Seven hundred people at the forum's rate limit
    is half an hour, and a command that prints nothing for half an hour is
    indistinguishable from one that has hung -- which is exactly how it was
    read the first time this was run for real.
    """
    report = {
        "seen": 0,
        "published": 0,
        "skipped": 0,
        "failed": 0,
        "with_avatar": 0,
        "groups": {},
        "problems": [],
        "people": [],
    }

    profiles = profiles_to_publish(only_unsynced=only_unsynced)
    if limit:
        profiles = profiles[:limit]
    total = len(profiles)

    for profile in profiles:
        report["seen"] += 1
        # try/finally rather than a call at the end of the body: the body leaves
        # by three different routes, and progress that stops being reported the
        # moment something goes wrong reports it least when it matters most.
        try:
            avatar_url = _avatar_url_for(profile, dry_run=dry_run)
            payload = build_profile_payload(
                profile, avatar_url=avatar_url, year_group_field=year_group_field
            )
            record = {
                "username": profile.source_username,
                "name": payload["name"],
                "year_group": profile.year_group or "",
                "groups": payload["add_groups"],
                "avatar": "yes" if avatar_url else "none",
                "result": "",
            }
            report["people"].append(record)
            if avatar_url:
                report["with_avatar"] += 1
            for group in payload["add_groups"].split(","):
                report["groups"][group] = report["groups"].get(group, 0) + 1

            if dry_run:
                record["result"] = "would publish"
                report["published"] += 1
                continue

            try:
                provider.sync_imported_profile(payload)
                if avatar_url:
                    # Sent again, because Discourse does not take the avatar on
                    # the call that creates the account. Verified on the real
                    # forum: a profile created in one call shows a letter, and
                    # the identical payload sent a second time puts the
                    # photograph on it -- the account exists by then, so the
                    # second call is an update.
                    #
                    # Unconditional rather than only on creation: the sync
                    # endpoint does not say whether it made the account or found
                    # it, and an extra call for somebody who already has their
                    # picture costs far less than a register of seven hundred
                    # blank faces.
                    provider.sync_imported_profile(
                        build_profile_payload(
                            profile,
                            avatar_url=avatar_url,
                            year_group_field=year_group_field,
                        )
                    )
            except ForumProviderError as exc:
                # One unhappy profile out of 740 must not end the run; the rest
                # are still worth publishing and this one is named so it can be
                # retried.
                report["failed"] += 1
                record["result"] = f"failed: {exc}"
                report["problems"].append(f"{profile.source_username}: {exc}")
                continue

            profile.forum_synced_at = get_now_utc()
            report["published"] += 1
            record["result"] = "published"
        finally:
            if on_progress is not None:
                on_progress(report["seen"], total, report)

    if dry_run:
        db.session.rollback()
    else:
        db.session.flush()

    current_app.logger.info(
        "Published imported forum profiles: %s",
        {key: value for key, value in report.items() if isinstance(value, int)},
    )
    return report
