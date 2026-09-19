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
# Only these two programmes exist; an unknown letter is left alone rather than
# guessed, because a wrong year group is worse than a missing one.
PROGRAMME_PREFIXES = {"L": "LAV", "M": "MAV"}

AVATAR_EXTENSIONS = ("jpg", "jpeg", "png", "webp")
AVATAR_MAX_BYTES = 512 * 1024


def imported_email_for(source_system, source_user_id):
    return f"forum-{source_system}-{source_user_id}@{IMPORTED_EMAIL_DOMAIN}"


def derive_year_group(username):
    """``PopovicA_L23`` -> ``LAV23``. Returns None when the rule does not apply.

    The suffix is the same one ``build_forum_username_base`` writes, read in
    reverse. Anything that does not match exactly is left as None: an invented
    year group would be indistinguishable from an exported one.
    """
    if not username or "_" not in username:
        return None
    suffix = username.rsplit("_", 1)[1].strip().upper()
    if len(suffix) != 3 or not suffix[1:].isdigit():
        return None
    programme = PROGRAMME_PREFIXES.get(suffix[0])
    return f"{programme}{suffix[1:]}" if programme else None


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


def _store_avatar(user_id, avatar_dir, avatar_file):
    """Copy one avatar through the same normalisation an upload would get.

    Old forum avatars are whatever a student uploaded in 2014 -- any size, any
    format, occasionally enormous. Running them through the existing pipeline
    means the imported ones cannot be a second class of file that the rest of
    the application has never seen.
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

    storage_dir = get_forum_storage_dir()
    storage_dir.mkdir(parents=True, exist_ok=True)
    destination = storage_dir / f"imported-{user_id}-{secrets.token_hex(8)}.{extension}"
    destination.write_bytes(normalized_bytes)
    return str(destination), None


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
        profile.post_count = _as_int(entry.get("post_count"))
        profile.joined_on = _as_date(entry.get("joined_on"))
        profile.last_posted_on = _as_date(entry.get("last_posted_on"))

        avatar_file = (entry.get("avatar_file") or "").strip()
        if avatar_dir and avatar_file and not profile.avatar_path:
            db.session.flush()
            stored, problem = _store_avatar(profile.user_id, avatar_dir, avatar_file)
            if stored:
                profile.avatar_path = stored
                report["avatars_stored"] += 1
            elif problem:
                report["problems"].append(f"{source_username}: {problem}")

    if dry_run:
        db.session.rollback()
    else:
        db.session.flush()

    current_app.logger.info("Forum import: %s", {k: v for k, v in report.items() if k != "problems"})
    return report
