"""Bringing the old forum's people across.

Roughly 500-600 students from a decade of MyBB, imported so their posts keep a
name, a year group and a face. They are not members: they get a ``users`` row
with no password and an ``imported_forum_profiles`` row, and nothing else.

Three rules the rest of this module exists to keep:

* **Idempotent on the old forum's own key.** ``(source_system, source_user_id)``
  decides whether a person has been seen before, so a second run updates six
  hundred rows instead of creating six hundred more. Imports get re-run --
  because the first export was missing a column, because somebody spotted a
  mistake -- and one that cannot be re-run is one nobody dares fix.
* **The old address is never identity.** Those are university accounts,
  disabled when a student leaves and possibly reissued to a later student of the
  same name. The real address is recorded as history; ``users.email`` gets a
  placeholder on a domain that cannot resolve, so no sign-in or password-reset
  path can ever find its way to one of these accounts.
* **A username clash is reported, never resolved.** ``forum_username`` is
  unique, and the portal generates names in the old forum's format. A clash is
  almost certainly the same person coming back, which is a thing for somebody to
  look at, not for an importer to paper over with a numeric suffix.
"""

import json
import secrets
from datetime import date, datetime
from pathlib import Path

from flask import current_app

from ..db_models import ImportedForumProfile, User, db
from ..forum_service import get_forum_storage_dir, normalize_avatar_image
from . import ValidationError
from .clock import get_now_utc

SOURCE_MYBB = "mybb"

# RFC 2606 reserves .invalid so it can never resolve, which is the point: these
# addresses sit in a NOT NULL unique column and must never be deliverable.
IMPORTED_EMAIL_DOMAIN = "imported.invalid"

# The portal builds usernames as Lastname + initial + _ + programme letter +
# year, so the mapping runs backwards for a year group the export did not carry.
# An unknown letter is left alone rather than guessed, because a wrong year
# group is worse than a missing one.
PROGRAMME_PREFIXES = {"L": "LAV", "M": "MAV"}

# A handful of people registered before that convention settled and spelled the
# programme out: PopovicA_LAV23 rather than PopovicA_L23. Reading that is not a
# guess, it is the year group written down, so accept it -- and ATM, a third
# programme that appears in the old forum's Jahrgang field but never as a single
# letter, is only ever reachable this way.
PROGRAMME_NAMES = frozenset({"LAV", "MAV", "ATM"})

AVATAR_EXTENSIONS = ("jpg", "jpeg", "png", "webp")
AVATAR_MAX_BYTES = 512 * 1024


def imported_email_for(source_system, source_user_id):
    return f"forum-{source_system}-{source_user_id}@{IMPORTED_EMAIL_DOMAIN}"


def derive_year_group(username):
    """``PopovicA_L23`` -> ``LAV23``, ``PopovicA_LAV23`` -> ``LAV23``.

    Returns None when neither rule applies. The short suffix is the one
    ``build_forum_username_base`` writes, read in reverse; the long one is the
    programme already spelled out. Anything else is left as None: an invented
    year group would be indistinguishable from an exported one.

    On the real export the long form appears sixteen times, and where those
    people also had a Jahrgang field, thirteen of them agreed with it exactly.
    """
    if not username or "_" not in username:
        return None
    suffix = username.rsplit("_", 1)[1].strip().upper()

    if len(suffix) == 3 and suffix[1:].isdigit():
        programme = PROGRAMME_PREFIXES.get(suffix[0])
        return f"{programme}{suffix[1:]}" if programme else None

    if len(suffix) == 5 and suffix[3:].isdigit() and suffix[:3] in PROGRAMME_NAMES:
        return suffix

    return None


def _as_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def load_people(path):
    """Read the export. Raises rather than importing half a file."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"No such export file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(payload, list):
        raise ValidationError("The export must be a JSON array of people.")
    return payload


def _store_avatar(user_id, avatar_dir, avatar_file, *, dry_run=False):
    """Copy one avatar through the same normalisation an upload would get.

    Old forum avatars are whatever a student uploaded in 2014 -- any size, any
    format, occasionally enormous. Running them through the existing pipeline
    means the imported ones cannot be a second class of file that the rest of
    the application has never seen.

    On a dry run every check still runs -- the file is found, decoded and
    normalised, so a missing or corrupt one is reported -- and only the write
    is skipped. The rollback at the end of a dry run undoes the database and
    nothing else, so writing here would leave six hundred images in the
    staging directory with every row that referenced them discarded.
    """
    source = Path(avatar_dir) / avatar_file
    if not source.exists():
        return None, f"avatar file not found: {avatar_file}"
    try:
        normalized_bytes, _content_type, extension = normalize_avatar_image(
            source.read_bytes(),
            allowed_extensions=AVATAR_EXTENSIONS,
            max_output_bytes=AVATAR_MAX_BYTES,
        )
    except Exception as exc:  # noqa: BLE001 -- one bad image must not stop 600 people
        return None, f"avatar could not be read ({avatar_file}): {exc}"

    if dry_run:
        return None, None  # readable, and deliberately not written

    storage_dir = get_forum_storage_dir()
    storage_dir.mkdir(parents=True, exist_ok=True)
    destination = storage_dir / f"imported-{user_id}-{secrets.token_hex(8)}.{extension}"
    destination.write_bytes(normalized_bytes)
    return str(destination), None


def find_claimable_profile(email):
    """The archived forum account belonging to this address, if exactly one does.

    Matching is on the address the old forum held, not on a name: every one of
    the students who will come back is still studying, so their university
    address still works, and proving they can read it is the whole of the
    evidence. A name match would only ever be a guess.

    Returns None when nothing matches, when the profile was already claimed,
    and -- deliberately -- when *two* profiles share the address. One archived
    address is shared by two accounts, and picking either of them silently
    would be handing somebody an identity on a coin flip.
    """
    normalized = (email or "").strip().lower()
    if not normalized or normalized.endswith(f"@{IMPORTED_EMAIL_DOMAIN}"):
        return None

    matches = db.session.execute(
        db.select(ImportedForumProfile).filter(
            db.func.lower(ImportedForumProfile.source_email) == normalized,
            ImportedForumProfile.claimed_at.is_(None),
        )
    ).scalars().all()

    return matches[0] if len(matches) == 1 else None


def verified_addresses_for(user):
    """Every address this person has actually proved they can read.

    The university address is the one that matters here -- it is what the old
    forum registered them under -- and it lives on the membership, not the
    account, because it is not a login. The account address is included too,
    for the few who signed up using their university address as their login.

    Unverified addresses are deliberately absent. The proof is the whole basis
    of the claim: without it, typing somebody else's university address at
    signup would be enough to take their posts.
    """
    addresses = []
    if user is None:
        return addresses
    if user.email_is_verified and user.email:
        addresses.append(user.email)

    member = user.member
    if member is not None and member.email_work_is_verified and member.email_work:
        addresses.append(member.email_work)
    return addresses


def find_claimable_profile_for_user(user):
    """The archived account this person can claim, from their verified addresses."""
    for address in verified_addresses_for(user):
        profile = find_claimable_profile(address)
        if profile is not None:
            return profile
    return None


def _blocking_relationships(user):
    """Anything on the new account the claim is not prepared to carry across.

    Rows that merely name the account are repointed at the archived row below,
    which handles the ordinary case. These are the ones where that is not
    enough: either both accounts already have one and the column is unique, or
    deciding what the merged account should end up with is a judgement rather
    than a move. A brand-new member being verified has none of them, so hitting
    one means something unexpected has happened and the claim declines instead
    of guessing.
    """
    blocking = []
    if user.roles:
        blocking.append("roles")
    if user.forum_account is not None:
        blocking.append("a forum account")
    if user.imported_forum_profile is not None:
        blocking.append("an imported forum profile")
    if user.forum_avatar_submissions:
        blocking.append("avatar submissions")
    if user.requested_profile_changes or user.reviewed_profile_changes:
        blocking.append("profile change requests")
    return blocking


# Moved onto the archived row by the ORM in the claim itself, so the sweep
# below must leave them alone. Both are unique columns: writing them twice
# would collide with the archive's own profile row.
CLAIM_HANDLED_FOREIGN_KEYS = frozenset({
    ("member", "user_id"),
    ("imported_forum_profiles", "user_id"),
})


def _user_foreign_key_columns():
    """Every column in the schema that points at ``users.id``.

    Read out of the metadata rather than listed by hand. The list has to be
    complete -- the claim deletes a user row, and a table left pointing at it
    makes the database refuse the delete -- and a hand-written one goes stale
    the first time somebody adds a table, silently and in the worst place:
    MariaDB enforces foreign keys, SQLite under the tests does not, so the
    breakage would show up only in production.
    """
    for table in db.metadata.sorted_tables:
        for foreign_key in table.foreign_keys:
            if foreign_key.column.table.name == User.__tablename__:
                yield table, foreign_key.parent


def claim_archived_account(user):
    """Give a returning student their old forum identity back. Returns the profile.

    Called once the address is verified, because the verification is the proof:
    the archived account named this address, and only somebody who can read it
    could have got here.

    The membership moves onto the *archived* row rather than the archive moving
    onto the new one. Discourse knows people by ``external_id = str(user.id)``,
    so the old posts are attached to that id -- keeping it is the difference
    between a returning student finding their history and finding an empty
    profile beside it.

    Returns None when there is nothing to claim, which is the ordinary case.
    """
    if user is None or user.deleted_at is not None:
        return None
    if user.imported_forum_profile is not None:
        return None  # already an archived account

    profile = find_claimable_profile_for_user(user)
    if profile is None:
        return None

    archived = profile.user
    if archived is None or archived.id == user.id or archived.deleted_at is not None:
        return None

    blocking = _blocking_relationships(user)
    if blocking:
        current_app.logger.warning(
            "Forum claim skipped for user %s: the new account has %s",
            user.id, ", ".join(blocking),
        )
        return None

    member = user.member

    # Free the unique columns on the row being retired before reusing them.
    retired_email = user.email
    user.forum_username = None
    user.email = imported_email_for("retired", f"{user.id}-{secrets.token_hex(4)}")
    db.session.flush()

    archived.email = retired_email
    archived.password_hash = user.password_hash
    archived.email_verified_at = user.email_verified_at
    archived.email_verification_nonce = user.email_verification_nonce
    archived.password_reset_nonce = None  # links issued for the retired row die here

    if member is not None:
        # Both sides, not just the foreign key. The row being retired still
        # holds this membership through User.member, and deleting it below
        # would de-associate the child -- setting member.user_id straight back
        # to NULL and orphaning the membership that was just moved.
        user.member = None
        archived.member = member

    profile.claimed_at = get_now_utc()
    db.session.flush()

    # Everything else in the schema that names the row about to go. It is the
    # same person on either side of the claim, so it all moves across: the
    # audit entries written during signup, and -- the one that actually bites
    # -- the verification email queued minutes ago, because queueing it is what
    # led here. Every returning student has one.
    for table, column in _user_foreign_key_columns():
        if (table.name, column.name) in CLAIM_HANDLED_FOREIGN_KEYS:
            continue
        db.session.execute(
            db.update(table).where(column == user.id).values({column.name: archived.id})
        )
    db.session.expire_all()  # the sweep went round the session; loaded rows are stale
    db.session.flush()

    # And then it goes, rather than being kept as an emptied husk. Leaving one
    # behind would mean a tombstone account per returning student -- around 250
    # of them in one October -- each showing up in the directory as an erased
    # account with a placeholder address, for ever. Somebody who comes back
    # should end up with exactly what a first-time member has: one account, one
    # membership, and the old forum hanging off it as history.
    db.session.delete(user)
    db.session.flush()

    current_app.logger.info(
        "Forum account %s claimed by member account %s (archived user %s)",
        profile.source_username, archived.id, archived.id,
    )
    return profile


def import_forum_people(people, *, source_system=SOURCE_MYBB, avatar_dir=None, dry_run=False):
    """Create or update an account per exported person. Returns a report.

    Does not commit when ``dry_run`` is set, and the caller commits otherwise --
    so a run either lands completely or not at all, which matters when the input
    is six hundred rows and the four hundredth is malformed.
    """
    report = {
        "seen": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "avatars_stored": 0,
        "year_groups_derived": 0,
        "year_groups_disagreeing": 0,
        # Every distinct year group and how many people carry it, counted on the
        # exact stored string rather than a normalised one: a stray "lav23" or a
        # trailing space is a typo somebody should see, not one to tidy away.
        "year_group_counts": {},
        # The people the rules could not place at all. Small enough to fix by
        # hand, which is the point of listing them rather than counting them.
        "unknown_year_group": [],
        "problems": [],
    }
    now = get_now_utc()

    for entry in people:
        report["seen"] += 1
        source_user_id = str(entry.get("source_user_id") or "").strip()
        source_username = (entry.get("source_username") or "").strip()
        if not source_user_id or not source_username:
            report["skipped"] += 1
            report["problems"].append(f"entry {report['seen']}: missing source_user_id or source_username")
            continue

        profile = db.session.execute(
            db.select(ImportedForumProfile).filter_by(
                source_system=source_system, source_user_id=source_user_id
            )
        ).scalar_one_or_none()

        year_group = (entry.get("year_group") or "").strip() or None
        if year_group is None:
            year_group = derive_year_group(source_username)
            if year_group:
                report["year_groups_derived"] += 1
        else:
            # The exported field wins, and where the two differ it is usually
            # right for a reason: somebody who joined as LAV21 and went on to
            # the master's is MAV24 in the field and still LAV21 in the name
            # they registered under. Counted rather than flagged, so a run that
            # suddenly disagrees about half the forum is visible.
            from_username = derive_year_group(source_username)
            if from_username and from_username.upper() != year_group.upper():
                report["year_groups_disagreeing"] += 1

        if year_group:
            report["year_group_counts"][year_group] = (
                report["year_group_counts"].get(year_group, 0) + 1
            )
        else:
            # The username is what somebody needs to recognise the person and
            # fill the gap in by hand, so it goes in whole, next to the year
            # they registered -- which is usually enough to guess the cohort.
            report["unknown_year_group"].append({
                "source_user_id": source_user_id,
                "source_username": source_username,
                "joined_on": entry.get("joined_on"),
            })

        display_name = (entry.get("display_name") or "").strip() or source_username

        if profile is None:
            # The username is unique across every account, and the portal
            # generates names in this same format. A clash is somebody's
            # business to look at, not an importer's to resolve.
            clash = db.session.execute(
                db.select(User).filter_by(forum_username=source_username)
            ).scalar_one_or_none()
            if clash is not None:
                report["skipped"] += 1
                report["problems"].append(
                    f"{source_username}: forum username already belongs to account "
                    f"{clash.id}; link them by hand if this is the same person"
                )
                continue

            user = User(
                email=imported_email_for(source_system, source_user_id),
                forum_username=source_username,
            )
            # No password is set, which is what stops these accounts being
            # signed into: check_password returns False on a NULL hash.
            db.session.add(user)
            db.session.flush()
            profile = ImportedForumProfile(
                user_id=user.id,
                source_system=source_system,
                source_user_id=source_user_id,
                imported_at=now,
            )
            db.session.add(profile)
            report["created"] += 1
        else:
            report["updated"] += 1

        profile.source_username = source_username
        profile.source_email = (entry.get("source_email") or "").strip() or None
        profile.display_name = display_name
        profile.year_group = year_group
        profile.source_group = (entry.get("source_group") or "").strip() or None
        profile.source_group_reason = (entry.get("source_group_reason") or "").strip() or None
        profile.post_count = _as_int(entry.get("post_count"))
        profile.joined_on = _as_date(entry.get("joined_on"))
        profile.last_posted_on = _as_date(entry.get("last_posted_on"))

        avatar_file = (entry.get("avatar_file") or "").strip()
        if avatar_dir and avatar_file and not profile.avatar_path:
            db.session.flush()
            stored, problem = _store_avatar(
                profile.user_id, avatar_dir, avatar_file, dry_run=dry_run
            )
            if problem:
                report["problems"].append(f"{source_username}: {problem}")
            else:
                # Counted either way, so the rehearsal reports the same number
                # the real run will store.
                report["avatars_stored"] += 1
                if stored:
                    profile.avatar_path = stored

    if dry_run:
        db.session.rollback()
    else:
        db.session.flush()

    # Counters only. The other keys hold six hundred usernames and a year group
    # per person, which belongs in the operator's terminal, not in a log file
    # that is kept, shipped and read by people with no reason to see it.
    counters = {
        key: value for key, value in report.items()
        if isinstance(value, int)
    }
    counters["year_groups_distinct"] = len(report["year_group_counts"])
    counters["year_groups_unknown"] = len(report["unknown_year_group"])
    current_app.logger.info("Forum import: %s", counters)
    return report
