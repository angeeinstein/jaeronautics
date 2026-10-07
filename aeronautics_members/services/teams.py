"""Teams: groups inside the association with their own members and leads.

A feature that can be switched off, and is off until somebody switches it on.
Off, nothing about teams is shown to members and nothing runs; the membership
works exactly as it does without them. So everything team-specific lives here
and in its own tables, and the rest of the portal only ever asks this module.

Teams are created in the portal, not in code, so that teams can work
differently from one another and the software can serve another association.
For the same reason the values that could one day take another state --
statuses, admission modes, roles -- are text, and what each one means is
written down here.

What has been discussed and why is in docs/teams-plan.md. None of it is fixed.

**Who may do what inside a team** is decided here, not in ``permissions.py``:
a lead leads one team, and the global roles hold for the whole portal. A team
role counts only while its holder is an active member of the association *and*
of the team -- a lead is a team member with a role on top. The role itself is
never taken away by a lapse; it simply stops counting until the membership is
back. Site admins, through ``Permission.TEAMS_MANAGE``, can do everything in
every team, which is what keeps a team with no active lead manageable.
"""

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

from flask import current_app

from ..db_models import Setting, Team, TeamMembership, TeamRole, User, db
from ..permissions import Permission
from . import ConflictError, NotFoundError, ValidationError
from .audit import log_audit_event
from .clock import get_now_utc
from .locking import locked
from .membership import member_has_active_access

SETTING_ENABLED = "teams_enabled"
SETTING_LABEL_SINGULAR = "teams_label_singular"
SETTING_LABEL_PLURAL = "teams_label_plural"
SETTING_KEYS = (SETTING_ENABLED, SETTING_LABEL_SINGULAR, SETTING_LABEL_PLURAL)

DEFAULT_LABEL_SINGULAR = "Team"
DEFAULT_LABEL_PLURAL = "Teams"

STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"

#: Joining directly, or applying and being approved by the leads.
ADMISSION_OPEN = "open"
ADMISSION_APPROVAL = "approval"
ADMISSION_MODES = (ADMISSION_APPROVAL, ADMISSION_OPEN)

#: A free team. The others are in services/team_payments.py.
PAYMENT_NONE = "none"

# The states of one attempt at being in a team (a TeamMembership row).
APPLIED = "applied"
INVITED = "invited"
APPROVED = "approved"
ACTIVE = "active"
ENDED = "ended"
REJECTED = "rejected"
WITHDRAWN = "withdrawn"
#: An attempt still under way. A person has at most one of these per team.
ONGOING = frozenset({APPLIED, INVITED, APPROVED, ACTIVE})


class TeamPermission:
    """What a role may do inside its own team."""

    VIEW_MEMBERS = "team.view_members"
    REVIEW_APPLICATIONS = "team.review_applications"
    REMOVE_MEMBERS = "team.remove_members"
    WRITE_NOTES = "team.write_notes"
    EXPORT = "team.export"
    EDIT_SETTINGS = "team.edit_settings"
    SEND_ACCESS_LIST = "team.send_access_list"
    VIEW_MONEY = "team.view_money"
    EDIT_BANK_DETAILS = "team.edit_bank_details"
    #: Give and take the treasurer role, among the team's own members.
    APPOINT_TREASURER = "team.appoint_treasurer"

    ALL = frozenset({
        VIEW_MEMBERS, REVIEW_APPLICATIONS, REMOVE_MEMBERS,
        WRITE_NOTES, EXPORT, EDIT_SETTINGS, SEND_ACCESS_LIST,
        VIEW_MONEY, EDIT_BANK_DETAILS, APPOINT_TREASURER,
    })
    #: The team's money, and the account it is paid to -- nothing about people.
    MONEY = frozenset({VIEW_MONEY, EDIT_BANK_DETAILS})


ROLE_LEAD = "lead"
ROLE_TREASURER = "treasurer"

#: Every team role, and what it may do. A new role -- a treasurer who may only
#: export, say -- is a new entry here and needs no change to the database.
TEAM_ROLE_PERMISSIONS = {
    ROLE_LEAD: TeamPermission.ALL,
    ROLE_TREASURER: TeamPermission.MONEY,
}

TEAM_ROLE_LABELS = {
    ROLE_LEAD: "Lead",
    ROLE_TREASURER: "Treasurer",
}

#: Leave a field as it is: settings are saved part by part -- the leads'
#: sections, the admins' form -- and a part does not touch what it lacks.
KEEP = object()

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_MAX_LENGTH = 60


# --- The switch and the label ----------------------------------------------


def _setting(key):
    row = db.session.get(Setting, key)
    return row.value if row is not None else None


def _write_setting(key, value):
    row = db.session.get(Setting, key)
    if value is None or value == "":
        if row is not None:
            db.session.delete(row)
        return
    if row is None:
        db.session.add(Setting(key=key, value=str(value)))
    else:
        row.value = str(value)


def teams_enabled():
    return _setting(SETTING_ENABLED) == "True"


def team_labels():
    """What teams are called here, as (singular, plural)."""
    return (
        _setting(SETTING_LABEL_SINGULAR) or DEFAULT_LABEL_SINGULAR,
        _setting(SETTING_LABEL_PLURAL) or DEFAULT_LABEL_PLURAL,
    )


def save_team_settings(actor, *, enabled, label_singular, label_plural):
    """Switch teams on or off and name them. Returns whether anything changed."""
    label_singular = (label_singular or "").strip()[:50]
    label_plural = (label_plural or "").strip()[:50]
    before = {
        "enabled": teams_enabled(),
        "label_singular": _setting(SETTING_LABEL_SINGULAR),
        "label_plural": _setting(SETTING_LABEL_PLURAL),
    }
    # Stored only when it differs from the default, so a later change of the
    # default reaches installations nobody has customised.
    after = {
        "enabled": bool(enabled),
        "label_singular": label_singular if label_singular not in ("", DEFAULT_LABEL_SINGULAR) else None,
        "label_plural": label_plural if label_plural not in ("", DEFAULT_LABEL_PLURAL) else None,
    }
    if before == after:
        return False
    if before["enabled"] and not after["enabled"]:
        paying = [m for team in all_teams(include_archived=False) for m in _paying(team)]
        if paying:
            raise ConflictError(
                f"{len(paying)} team membership(s) are still paid for; switched off, they would go on "
                "charging for a team nobody can see. Switch those teams to free first.",
                code="teams_off_subscriptions_running",
            )
        waiting = [m for team in all_teams(include_archived=False) for m in _waiting_to_pay(team)]
        if waiting:
            raise ConflictError(
                f"{len(waiting)} approved applicant(s) can still pay for a team; switched off, they could pay "
                "for a team nobody can see. Reject them on the team's page, or wait until their approval lapses.",
                code="teams_off_approvals_waiting",
            )
    _write_setting(SETTING_ENABLED, "True" if after["enabled"] else None)
    _write_setting(SETTING_LABEL_SINGULAR, after["label_singular"])
    _write_setting(SETTING_LABEL_PLURAL, after["label_plural"])
    log_audit_event("teams", "team_settings_changed", actor_user=actor, before=before, after=after)
    return True


# --- Teams -----------------------------------------------------------------


def suggest_slug(name):
    """A slug made from a team's name: "Rocket Team" -> "rocket-team"."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug[:SLUG_MAX_LENGTH].strip("-")


def all_teams(include_archived=True):
    query = db.select(Team).order_by(Team.status, Team.name)
    if not include_archived:
        query = query.where(Team.status == STATUS_ACTIVE)
    return db.session.execute(query).scalars().all()


def get_team(slug):
    team = db.session.execute(db.select(Team).filter_by(slug=(slug or "").strip().lower())).scalar_one_or_none()
    if team is None:
        raise NotFoundError("That team does not exist.")
    return team


def _clean_team_fields(*, name, admission_mode, max_members, forum_group,
                       description=KEEP, applications_open=KEEP, application_prompt=KEEP):
    name = (name or "").strip()
    if not name:
        raise ValidationError("A team needs a name.", code="team_name_missing")
    if len(name) > 120:
        raise ValidationError("That name is too long.", code="team_name_too_long")
    if admission_mode not in ADMISSION_MODES:
        raise ValidationError("Choose how people join.", code="team_admission_mode_invalid")

    if max_members in (None, ""):
        max_members = None
    else:
        try:
            max_members = int(max_members)
        except (TypeError, ValueError):
            raise ValidationError("The maximum size must be a number.", code="team_max_members_invalid") from None
        if max_members <= 0:
            raise ValidationError("The maximum size must be above zero.", code="team_max_members_invalid")

    forum_group = (forum_group or "").strip()[:100] or None
    if forum_group and forum_group.lower() in reserved_forum_groups():
        raise ValidationError(
            f"The forum group {forum_group} is already used for something else. A team needs a forum group of "
            "its own: everybody not in the team is taken out of it.",
            code="team_forum_group_reserved",
        )
    cleaned = {
        "name": name,
        "admission_mode": admission_mode,
        "max_members": max_members,
        "forum_group": forum_group,
    }
    # The leads' part, only when given: the admins' form leaves it to them.
    if description is not KEEP:
        cleaned["description"] = (description or "").strip() or None
    if applications_open is not KEEP:
        cleaned["applications_open"] = bool(applications_open)
    if application_prompt is not KEEP:
        cleaned["application_prompt"] = (application_prompt or "").strip()[:255] or None
    return cleaned


#: Discourse's own groups, which it fills by itself.
DISCOURSE_AUTOMATIC_GROUPS = frozenset({
    "everyone", "admins", "moderators", "staff",
    "trust_level_0", "trust_level_1", "trust_level_2", "trust_level_3", "trust_level_4",
})


def reserved_forum_groups():
    """Forum groups a team cannot have, lower-case: Discourse's own, and those
    the portal fills for other reasons (members, staff, member kinds)."""
    from ..forum_service import member_category_groups, normalize_forum_settings
    from .forum import get_forum_settings_map

    settings = normalize_forum_settings(get_forum_settings_map())
    reserved = set(DISCOURSE_AUTOMATIC_GROUPS)
    for key in ("forum_onboarding_group", "forum_member_group", "forum_inactive_group", "forum_staff_group"):
        if settings.get(key):
            reserved.add(str(settings[key]).strip().lower())
    reserved.update(str(group).strip().lower() for group in member_category_groups(settings).values())
    return reserved


def _snapshot(team):
    return {
        "slug": team.slug,
        "name": team.name,
        "description": team.description,
        "status": team.status,
        "admission_mode": team.admission_mode,
        "applications_open": team.applications_open,
        "application_prompt": team.application_prompt,
        "max_members": team.max_members,
        "forum_group": team.forum_group,
        "payment_mode": team.payment_mode,
    }


#: Short names the addresses use themselves: /admin/teams/new is the form for a
#: new team, so a team called "new" could never be opened; the same for
#: /api/v1/admin/money/overview beside a team's /api/v1/admin/money/<slug>.
RESERVED_SLUGS = frozenset({"new", "overview"})


def create_team(actor, *, slug, **fields):
    cleaned = _clean_team_fields(**fields)
    slug = (slug or "").strip().lower() or suggest_slug(cleaned["name"])
    if not slug or len(slug) > SLUG_MAX_LENGTH or not SLUG_PATTERN.match(slug):
        raise ValidationError(
            "The short name may only contain lowercase letters, digits and hyphens.",
            code="team_slug_invalid",
        )
    if slug in RESERVED_SLUGS:
        raise ValidationError("That short name is used by the pages themselves. Choose another.",
                              code="team_slug_reserved")
    if db.session.execute(db.select(Team.id).filter_by(slug=slug)).first() is not None:
        raise ConflictError("Another team already has that short name.", code="team_slug_taken")

    team = Team(slug=slug, status=STATUS_ACTIVE, payment_mode=PAYMENT_NONE, **cleaned)
    db.session.add(team)
    db.session.flush()
    log_audit_event("teams", "team_created", actor_user=actor, after=_snapshot(team))
    return team


def update_team(actor, team, **fields):
    """Change what a team is called and how people join it.

    The short name stays as it was created: it is in links people have
    bookmarked and, later, in what Stripe and the forum know the team by.
    """
    cleaned = _clean_team_fields(**fields)
    before = _snapshot(team)
    for key, value in cleaned.items():
        setattr(team, key, value)
    after = _snapshot(team)
    if before != after:
        log_audit_event("teams", "team_updated", actor_user=actor, before=before, after=after)
    if before["forum_group"] != after["forum_group"]:
        _sync_forum([m.user for m in team.memberships if m.status == ACTIVE], team,
                    f"forum group of team {team.slug} changed")
    return team


def _paying(team):
    """Memberships of this team still paying: a subscription charging, a
    period paid once that has not run out, or a payment on its way."""
    from .clock import get_membership_today

    today = get_membership_today()
    return [m for m in team.memberships if m.status in (APPROVED, ACTIVE) and (
        (m.stripe_subscription_id and m.payment_mode == "subscription")
        or (m.payment_mode == "one_time" and m.paid_until is not None and m.paid_until >= today)
        or m.payment_state == "processing"
    )]


def _waiting_to_pay(team):
    """Approvals of a team that charges, their payment not made yet."""
    if team.payment_mode == PAYMENT_NONE:
        return []
    paying = _paying(team)
    return [m for m in team.memberships if m.status == APPROVED and m not in paying]


def set_team_archived(actor, team, archived, *, confirmed_name=None):
    """Archive a team, or bring it back. Nothing is deleted either way.

    Archiving takes the team away from its members at once -- its pages, its
    forum group -- so it asks for the team's name to be typed, and is refused
    while anybody is still paying for it: switch the team to free first.
    """
    target = STATUS_ARCHIVED if archived else STATUS_ACTIVE
    if team.status == target:
        return team
    if archived:
        if (confirmed_name or "").strip().casefold() != team.name.strip().casefold():
            raise ValidationError("Type the team's name to archive it.", code="team_archive_unconfirmed")
        paying = _paying(team)
        if paying:
            raise ConflictError(
                f"{len(paying)} member(s) still pay for this team by subscription. Switch the team to free "
                "first; their subscriptions then stop at the end of what is paid.",
                code="team_archive_subscriptions_running",
            )
    before = _snapshot(team)
    if archived:
        _close_applications(team)
    team.status = target
    team.archived_at = get_now_utc() if archived else None
    _sync_forum([m.user for m in team.memberships if m.status == ACTIVE], team,
                f"team {team.slug} {'archived' if archived else 'restored'}")
    log_audit_event(
        "teams", "team_archived" if archived else "team_restored",
        actor_user=actor, before=before, after=_snapshot(team),
    )
    return team


def _close_applications(team):
    """An archived team takes nobody: applications under way end, and any
    payment page still open is closed, so nobody pays for a team that is gone."""
    now = get_now_utc()
    for found in list(team.memberships):
        if found.status not in (APPLIED, INVITED, APPROVED):
            continue
        membership = _locked_membership(found.id, team)
        if membership.status not in (APPLIED, INVITED, APPROVED):
            continue
        _stop_charging(membership, "team archived")
        membership.status = WITHDRAWN
        membership.ended_at = now
        membership.end_reason = END_TEAM_CLOSED
        _audit("team_application_closed", None, membership)
        _tell_person("team_closed", membership)


# --- The logo ----------------------------------------------------------------
#
# Optional: plenty of teams have none. Whatever is uploaded is decoded and
# written out again as a PNG, so what is stored and served is a picture this
# code made, never the uploaded file -- the same care as with profile pictures.

LOGO_FORMATS = ("PNG", "JPEG", "WEBP")
LOGO_MAX_BYTES = 5 * 1024 * 1024
LOGO_MAX_SIDE = 512


def logo_storage_dir():
    configured = current_app.config.get("TEAM_LOGO_DIR")
    return Path(configured) if configured else Path(current_app.root_path).parent / "storage" / "team_logos"


def logo_file(team):
    """The stored logo as a path, if there is one and it is still there."""
    if team is None or not team.logo_path:
        return None
    path = Path(team.logo_path)
    return path if path.is_file() else None


def _delete_logo_file(path):
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        current_app.logger.warning("Could not delete the old team logo %s", path)


def set_team_logo(actor, team, raw_bytes):
    from ..forum_service import ForumProviderError, _load_image_for_processing

    if not raw_bytes:
        raise ValidationError("Choose a picture.", code="team_logo_missing")
    if len(raw_bytes) > LOGO_MAX_BYTES:
        raise ValidationError("The logo may be at most 5 MB.", code="team_logo_too_large")
    try:
        image = _load_image_for_processing(raw_bytes, formats=LOGO_FORMATS)
    except ForumProviderError:
        raise ValidationError("Please upload a PNG, JPG or WebP picture.", code="team_logo_invalid") from None

    image = image.convert("RGBA")
    image.thumbnail((LOGO_MAX_SIDE, LOGO_MAX_SIDE))
    directory = logo_storage_dir()
    directory.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    path = directory / f"{team.slug}-{token}.png"
    image.save(path, format="PNG", optimize=True)

    previous = team.logo_path
    team.logo_path, team.logo_token = str(path), token
    _delete_logo_file(previous)
    log_audit_event("teams", "team_logo_changed", actor_user=actor, metadata={"team": team.slug})
    return team


def remove_team_logo(actor, team):
    if not team.logo_path and not team.logo_token:
        return team
    _delete_logo_file(team.logo_path)
    team.logo_path, team.logo_token = None, None
    log_audit_event("teams", "team_logo_removed", actor_user=actor, metadata={"team": team.slug})
    return team


def team_by_logo_token(token):
    if not token:
        return None
    return db.session.execute(db.select(Team).filter_by(logo_token=token)).scalar_one_or_none()


# --- The team's rules ----------------------------------------------------------
#
# Kept with the association's legal texts, which approves them like its own:
# legal/teams/<slug>/team-rules/<language>/<day>.md (services/legal_texts.py),
# German, optionally with an English translation, each version dated, a PDF
# of each.


@dataclass(frozen=True)
class TeamRules:
    """The rules somebody joining a team accepts: the team's file in force."""

    version: object  # a legal_texts.Version
    stamp: datetime  # kept with a membership as the version accepted

    @property
    def day(self):
        return self.stamp.date()


def team_rules(team, today=None):
    """The rules in force for ``team``, or None when it has none."""
    from . import legal_texts as legal

    version = legal.current_version(legal.TEAM_RULES, today, team=team.slug)
    if version is None:
        return None
    return TeamRules(version=version, stamp=datetime.combine(version.version, time()))


def accepted_rules_in_force(membership, rules):
    """Whether the member accepted the rules now in force (by their day), not an earlier version."""
    if rules is None or membership is None or membership.terms_version is None:
        return rules is None
    return membership.terms_version.date() == rules.day


# --- The team's page: a longer text, a picture, a gallery ---------------------------

PICTURE_MAX_BYTES = 10 * 1024 * 1024
PICTURE_MAX_SIDE = 1600
ABOUT_MAX_LENGTH = 10000
#: The gallery on the About page.
PHOTOS_MAX = 8
PHOTO_MAX_SIDE = 2000
CAPTION_MAX_LENGTH = 200


def picture_storage_dir():
    configured = current_app.config.get("TEAM_PICTURE_DIR")
    return Path(configured) if configured else Path(current_app.root_path).parent / "storage" / "team_pictures"


def picture_file(team):
    if team is None or not team.picture_path:
        return None
    path = Path(team.picture_path)
    return path if path.is_file() else None


def _store_jpeg(team, raw_bytes, max_side, what):
    """An upload re-encoded as a JPEG, at most ``max_side`` on its longer side:
    what is served is a picture this code made, never the upload. Returns
    ``(path, token, width, height)``."""
    from PIL import Image

    from ..forum_service import ForumProviderError, _load_image_for_processing

    if not raw_bytes:
        raise ValidationError(f"Choose a {what}.", code="team_picture_missing")
    if len(raw_bytes) > PICTURE_MAX_BYTES:
        raise ValidationError(f"The {what} may be at most 10 MB.", code="team_picture_too_large")
    try:
        image = _load_image_for_processing(raw_bytes, formats=LOGO_FORMATS)
    except ForumProviderError:
        raise ValidationError(f"Please upload a PNG, JPG or WebP {what}.", code="team_picture_invalid") from None

    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        flat = Image.new("RGB", image.size, (22, 23, 24))  # the page's dark background
        flat.paste(image, mask=image.split()[-1])
        image = flat
    else:
        image = image.convert("RGB")
    image.thumbnail((max_side, max_side))
    directory = picture_storage_dir()
    directory.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    path = directory / f"{team.slug}-{token}.jpg"
    image.save(path, format="JPEG", quality=85, optimize=True, progressive=True)
    return path, token, image.width, image.height


def set_team_picture(actor, team, raw_bytes):
    """The picture on the team's page -- the cover of its About page. Re-encoded
    as a JPEG, like the logo is as a PNG."""
    path, token, _width, _height = _store_jpeg(team, raw_bytes, PICTURE_MAX_SIDE, "picture")
    previous = team.picture_path
    team.picture_path, team.picture_token = str(path), token
    _delete_logo_file(previous)
    log_audit_event("teams", "team_picture_changed", actor_user=actor, metadata={"team": team.slug})
    return team


def remove_team_picture(actor, team):
    if not team.picture_path and not team.picture_token:
        return team
    _delete_logo_file(team.picture_path)
    team.picture_path, team.picture_token = None, None
    log_audit_event("teams", "team_picture_removed", actor_user=actor, metadata={"team": team.slug})
    return team


def _caption(text):
    text = " ".join((text or "").split())
    if len(text) > CAPTION_MAX_LENGTH:
        raise ValidationError(f"A caption may be at most {CAPTION_MAX_LENGTH} characters.",
                              code="team_caption_too_long")
    return text or None


def add_team_photo(actor, team, raw_bytes, caption=None):
    """A photo for the gallery on the About page, after the ones there."""
    from ..db_models import TeamPhoto

    if len(team.photos) >= PHOTOS_MAX:
        raise ValidationError(f"The gallery holds {PHOTOS_MAX} photos. Remove one first.",
                              code="team_photos_full")
    caption = _caption(caption)
    path, token, width, height = _store_jpeg(team, raw_bytes, PHOTO_MAX_SIDE, "photo")
    photo = TeamPhoto(team=team, path=str(path), token=token, caption=caption, width=width, height=height,
                      position=max((photo.position for photo in team.photos), default=-1) + 1)
    db.session.add(photo)
    log_audit_event("teams", "team_photo_added", actor_user=actor, metadata={"team": team.slug})
    return photo


def arrange_team_photos(actor, team, arrangement):
    """The gallery's order and captions: ``[(photo id, caption), ...]``, every
    photo of the team once, in the order to show them."""
    photos = {photo.id: photo for photo in team.photos}
    ids = [photo_id for photo_id, _caption_text in arrangement]
    if sorted(ids) != sorted(photos):
        raise ConflictError("The gallery changed meanwhile. Reload the page and try again.",
                            code="team_photos_changed")
    changed = False
    for position, (photo_id, caption) in enumerate(arrangement):
        photo, caption = photos[photo_id], _caption(caption)
        if (photo.position, photo.caption) != (position, caption):
            photo.position, photo.caption = position, caption
            changed = True
    if changed:
        team.photos.sort(key=lambda photo: photo.position)
        log_audit_event("teams", "team_photos_arranged", actor_user=actor, metadata={"team": team.slug})
    return changed


def remove_team_photo(actor, team, photo_id):
    photo = next((photo for photo in team.photos if photo.id == photo_id), None)
    if photo is None:
        raise NotFoundError("That photo is not in this team's gallery.", code="team_photo_missing")
    team.photos.remove(photo)
    db.session.delete(photo)
    _delete_logo_file(photo.path)
    for position, rest in enumerate(team.photos):
        rest.position = position
    log_audit_event("teams", "team_photo_removed", actor_user=actor, metadata={"team": team.slug})


def team_photo_by_token(token):
    from ..db_models import TeamPhoto

    if not token:
        return None
    return db.session.execute(db.select(TeamPhoto).filter_by(token=token)).scalar_one_or_none()


def photo_file(photo):
    if photo is None or not photo.path:
        return None
    path = Path(photo.path)
    return path if path.is_file() else None


def _about_markdown():
    """Light formatting for the text about a team: paragraphs, headings, lists,
    emphasis, links. No HTML and no images (an image from elsewhere would be
    refused by the security policy anyway; photos go in the gallery). A line
    break is kept, so a text typed before formatting existed looks as it did.
    Headings start one below the page's own: ``#`` is a section, not a title."""
    from markdown_it import MarkdownIt

    markdown = MarkdownIt("commonmark", {"html": False, "breaks": True, "linkify": False, "typographer": True})
    markdown.disable("image")

    def demote(state):
        for token in state.tokens:
            if token.type in ("heading_open", "heading_close"):
                token.tag = f"h{min(int(token.tag[1]) + 1, 6)}"

    def outside_links(renderer, tokens, idx, options, env):
        # Links leave the portal in a tab of their own.
        token = tokens[idx]
        href = str(token.attrGet("href") or "")
        if href.startswith(("http://", "https://", "mailto:")):
            token.attrSet("target", "_blank")
            token.attrSet("rel", "noopener noreferrer")
        return renderer.renderToken(tokens, idx, options, env)

    markdown.core.ruler.push("demote_headings", demote)
    markdown.add_render_rule("link_open", outside_links)
    return markdown


_about_renderer = None


def render_about(text):
    """The text about a team as safe HTML (see _about_markdown), or None."""
    global _about_renderer
    if not text:
        return None
    if _about_renderer is None:
        _about_renderer = _about_markdown()
    return _about_renderer.render(text)


def team_by_picture_token(token):
    if not token:
        return None
    return db.session.execute(db.select(Team).filter_by(picture_token=token)).scalar_one_or_none()


def _clean_long_text(text, limit, what):
    text = (text or "").replace("\r\n", "\n").strip()
    if len(text) > limit:
        raise ValidationError(f"The {what} is too long (at most {limit} characters).", code="team_text_too_long")
    return text or None


def update_team_page(actor, team, *, about=None):
    """What the team's page says about it. Every change is logged with the old
    and new text. (Its rules are not changed here: see team_rules.)"""
    about = _clean_long_text(about, ABOUT_MAX_LENGTH, "text about the team")
    if about != team.about:
        log_audit_event("teams", "team_about_changed", actor_user=actor, metadata={"team": team.slug},
                        before={"about": team.about}, after={"about": about})
        team.about = about
    return team


# --- Who is in a team, and what they may do there --------------------------


def is_active_association_member(user):
    """Whether this account currently belongs to the association."""
    if user is None or user.deleted_at is not None or user.disabled_at is not None:
        return False
    return member_has_active_access(user.member)


def active_team_membership(user, team):
    if user is None or team is None:
        return None
    return db.session.execute(
        db.select(TeamMembership).filter_by(team_id=team.id, user_id=user.id, status=ACTIVE)
    ).scalars().first()


def role_counts(team_role):
    """Whether a role held is in force: its holder is in the association and the team."""
    return (
        is_active_association_member(team_role.user)
        and active_team_membership(team_role.user, team_role.team) is not None
    )


def team_permissions(user, team):
    """Everything ``user`` may do in ``team``.

    Site admins may do everything in every team. Anybody else only what their
    roles in this team carry, only while those roles are in force, and only
    while the team is not archived.
    """
    if user is None or team is None or not getattr(user, "is_authenticated", True):
        return frozenset()
    if user.can(Permission.TEAMS_MANAGE):
        return TeamPermission.ALL
    # The association's treasurer: every team's money, nothing about its people.
    granted = set(TeamPermission.MONEY) if user.can(Permission.TEAMS_MONEY) else set()
    if team.status != STATUS_ACTIVE:
        return frozenset(granted)

    held = [team_role for team_role in team.roles if team_role.user_id == user.id]
    if not held or not role_counts(held[0]):
        return frozenset(granted)
    for team_role in held:
        granted |= TEAM_ROLE_PERMISSIONS.get(team_role.role, frozenset())
    return frozenset(granted)


def can_in_team(user, team, permission):
    return permission in team_permissions(user, team)


def teams_led_by(user):
    """The teams whose page this person may open as somebody with a role in it."""
    if user is None:
        return []
    teams = {team_role.team for team_role in db.session.execute(
        db.select(TeamRole).filter_by(user_id=user.id)
    ).scalars()}
    return sorted(
        (team for team in teams if team_permissions(user, team)),
        key=lambda team: team.name,
    )


def role_holders(team, role=None):
    roles = [team_role for team_role in team.roles if role is None or team_role.role == role]
    return sorted(roles, key=lambda team_role: (team_role.role, (team_role.user.email or "")))


def has_lead_in_force(team):
    return any(role_counts(team_role) for team_role in role_holders(team, ROLE_LEAD))


def _lock_team(team):
    """Make role changes to one team happen one after another.

    "Is this the last lead?" is a question about every lead of the team, so two
    admins each removing a different lead would otherwise both see one left.
    """
    db.session.execute(locked(db.select(Team.id).where(Team.id == team.id))).all()


def grant_team_role(actor, team, user, role):
    """Give somebody a role in a team.

    Allowed before they are a team member -- the first lead of a new team
    joins like anybody else and is approved by a site admin -- but the role
    only counts from the moment they are.
    """
    if role not in TEAM_ROLE_PERMISSIONS:
        raise ValidationError("That role does not exist.", code="team_role_unknown")
    if user is None or user.deleted_at is not None:
        raise ValidationError("That account cannot hold a role.", code="team_role_holder_invalid")

    _lock_team(team)
    existing = db.session.execute(
        db.select(TeamRole).filter_by(team_id=team.id, user_id=user.id, role=role)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    team_role = TeamRole(team=team, user=user, role=role, granted_by_user_id=getattr(actor, "id", None))
    db.session.add(team_role)
    db.session.flush()
    log_audit_event(
        "teams", "team_role_granted", actor_user=actor, target_user=user,
        metadata={"team": team.slug, "role": role},
    )
    return team_role


def revoke_team_role(actor, team, user, role, *, confirmed=False):
    """Take a role away.

    Taking away the last lead is allowed -- site admins can always run the
    team -- but only once the person doing it has confirmed they mean it.
    """
    _lock_team(team)
    team_role = db.session.execute(
        locked(db.select(TeamRole).filter_by(team_id=team.id, user_id=user.id, role=role))
    ).scalar_one_or_none()
    if team_role is None:
        return False

    if role == ROLE_LEAD and not confirmed:
        leads = db.session.scalar(
            db.select(db.func.count()).select_from(TeamRole).filter_by(team_id=team.id, role=ROLE_LEAD)
        )
        if leads <= 1:
            raise ConflictError(
                "This is the team's last lead. Confirm to remove them anyway.",
                code="team_last_lead",
            )

    db.session.delete(team_role)
    db.session.flush()
    log_audit_event(
        "teams", "team_role_revoked", actor_user=actor, target_user=user,
        metadata={"team": team.slug, "role": role},
    )
    return True


def appoint_treasurer(actor, team, user_id):
    """A lead makes one of the team's own members its treasurer. Leads are
    appointed by site admins; the treasurer by the team itself."""
    user = db.session.get(User, user_id) if user_id else None
    if user is None or active_team_membership(user, team) is None:
        raise ValidationError("Choose one of the team's members.", code="team_treasurer_not_member")
    return grant_team_role(actor, team, user, ROLE_TREASURER)


def dismiss_treasurer(actor, team, user_id):
    user = db.session.get(User, user_id) if user_id else None
    if user is None:
        raise NotFoundError("That person does not exist.")
    return revoke_team_role(actor, team, user, ROLE_TREASURER)


def find_account(address):
    """The account an address belongs to, for giving somebody a role."""
    from .identity import user_for_login_address

    user = user_for_login_address(address)
    if user is None or user.deleted_at is not None:
        raise NotFoundError("No account uses that address.", code="team_account_not_found")
    return user


# --- The forum ---------------------------------------------------------------
#
# A team may name a forum group; its active members are in it, everybody else
# is not. Sent with every forum sync, in both directions, like the other
# groups -- so nobody keeps a team's group after leaving it. While teams are
# switched off, nothing about team groups is sent at all.


def forum_groups_for(user):
    """(add, remove): the team groups for this person's next forum sync."""
    if user is None or not teams_enabled():
        return [], []
    add, remove = [], []
    for team in db.session.execute(db.select(Team).where(Team.forum_group.is_not(None))).scalars():
        inside = team.status == STATUS_ACTIVE and active_team_membership(user, team) is not None
        (add if inside else remove).append(team.forum_group)
    add = list(dict.fromkeys(add))
    # Two teams may share a group; being in either keeps somebody in it.
    return add, [group for group in dict.fromkeys(remove) if group not in add]


def _sync_forum(users, team, reason):
    """Queue a forum sync for these people, if the team has a forum group."""
    if not team.forum_group or not teams_enabled():
        return
    from .outbox import enqueue_forum_sync

    for user in users:
        enqueue_forum_sync(getattr(user, "member", None), reason=reason)


# --- Joining and leaving ---------------------------------------------------
#
# Every change to one attempt is made under a lock on its row, and every new
# attempt under a lock on its team, so two clicks -- or a lead approving while
# the applicant withdraws -- happen one after the other and the second one
# finds what the first decided.

#: Why an attempt ended.
END_LEFT = "left"
END_REMOVED = "removed"
END_MEMBERSHIP_ENDED = "membership_ended"
END_ACCOUNT_ERASED = "account_erased"
END_NOT_PAID = "not_paid_in_time"
END_PAYMENT_FAILED = "payment_failed"
END_NOT_RENEWED = "not_renewed"
END_TEAM_CLOSED = "team_closed"

#: How long an approval waits for its payment before it lapses and the person
#: has to apply again.
APPROVAL_PAYMENT_DAYS = 14
#: A SEPA debit takes days, never weeks: one "on its way" for longer than this
#: lost its answer from Stripe, and the approval lapses too.
APPROVAL_PROCESSING_DAYS = 45

#: Whoever's membership ended unpaid may rejoin by paying, for about one period.
REJOIN_DAYS = 183

#: What applicants and members see for each state.
STATUS_LABELS = {
    APPLIED: "Application received",
    INVITED: "Invited",
    APPROVED: "Approved, payment open",
    ACTIVE: "Member",
    ENDED: "Ended",
    REJECTED: "Not accepted",
    WITHDRAWN: "Withdrawn",
}

END_REASON_LABELS = {
    END_LEFT: "Left",
    END_REMOVED: "Removed",
    END_MEMBERSHIP_ENDED: "Association membership ended",
    END_ACCOUNT_ERASED: "Account erased",
    END_NOT_PAID: "Not paid in time",
    END_PAYMENT_FAILED: "Payment failed",
    END_NOT_RENEWED: "Not renewed",
    END_TEAM_CLOSED: "Team closed",
}


def ongoing_membership(user, team):
    """This person's attempt under way in this team, if any."""
    if user is None or team is None:
        return None
    return db.session.execute(
        db.select(TeamMembership)
        .where(TeamMembership.team_id == team.id, TeamMembership.user_id == user.id,
               TeamMembership.status.in_(ONGOING))
        .order_by(TeamMembership.id.desc())
    ).scalars().first()


def invite_to_teams(user):
    """Whether to point this person to the Teams page: teams are on, there is
    one to join, they may join, and they are not in or applying to any yet."""
    if user is None or not teams_enabled() or not is_active_association_member(user):
        return False
    if not all_teams(include_archived=False):
        return False
    return not any(membership.status in ONGOING for membership in memberships_of(user))


def memberships_of(user, include_archived=False):
    """Every attempt of this person, newest first; by default in teams still running."""
    query = db.select(TeamMembership).join(Team).where(TeamMembership.user_id == user.id)
    if not include_archived:
        query = query.where(Team.status == STATUS_ACTIVE)
    return db.session.execute(query.order_by(TeamMembership.id.desc())).scalars().all()


def roles_of(user):
    """The roles this person holds, in every team."""
    return db.session.execute(
        db.select(TeamRole).join(Team).where(TeamRole.user_id == user.id).order_by(Team.name, TeamRole.role)
    ).scalars().all()


def active_member_count(team):
    return db.session.scalar(
        db.select(db.func.count()).select_from(TeamMembership).filter_by(team_id=team.id, status=ACTIVE)
    ) or 0


def is_full(team):
    """No place left: the members, and those approved who are paying to join, fill it."""
    if team.max_members is None:
        return False
    taken = db.session.scalar(
        db.select(db.func.count()).select_from(TeamMembership)
        .where(TeamMembership.team_id == team.id, TeamMembership.status.in_((ACTIVE, APPROVED)))
    ) or 0
    return taken >= team.max_members


def why_not_joinable(user, team):
    """None if ``user`` may join or apply to ``team`` now, else the reason."""
    if not teams_enabled() or team.status != STATUS_ACTIVE:
        return "That team is not available."
    if not is_active_association_member(user):
        return "Teams are for members of the association."
    if ongoing_membership(user, team) is not None:
        return "You are already in this team or have applied."
    if not team.applications_open:
        return "This team is not taking new members at the moment."
    if is_full(team):
        return "This team is full."
    return None


def _member_name(user):
    member = getattr(user, "member", None)
    if member is not None and (member.first_name or member.last_name):
        return f"{member.first_name} {member.last_name}".strip()
    return user.email


def _first_name(user):
    member = getattr(user, "member", None)
    return member.first_name if member is not None else None


def _tell_person(event_type, membership, **extra):
    from .notifications import queue_user_status_notification

    user = membership.user
    payload = {
        "first_name": _first_name(user),
        "team_name": membership.team.name,
        "team_slug": membership.team.slug,
        "team_logo_token": membership.team.logo_token,
        **extra,
    }
    queue_user_status_notification(
        event_type, f"{membership.team.name}: {STATUS_LABELS.get(membership.status, membership.status)}",
        user.email, payload=payload, target_user=user, object_type="team_membership", object_id=membership.id,
    )


def _tell_leads(team, event_type, summary, **extra):
    """Email the team's leads; with none in force, the site admins instead."""
    from .notifications import queue_curated_admin_notification, queue_user_status_notification

    leads = [team_role.user for team_role in role_holders(team, ROLE_LEAD) if role_counts(team_role)]
    payload = {"team_name": team.name, "team_slug": team.slug, "team_logo_token": team.logo_token, **extra}
    if not leads:
        from ..notification_service import ADMIN_GENERAL_CHANNEL

        queue_curated_admin_notification(
            ADMIN_GENERAL_CHANNEL, event_type, f"{summary} (no active lead)",
            payload={**payload, "what_to_do": "Appoint a lead, or handle it on the team's page."},
            object_type="team", object_id=team.id, severity="info",
        )
        return
    for lead in leads:
        queue_user_status_notification(
            event_type, summary, lead.email,
            payload={**payload, "first_name": _first_name(lead)},
            target_user=lead, object_type="team", object_id=team.id,
        )


def _audit(event_type, actor, membership, **metadata):
    log_audit_event(
        "teams", event_type, actor_user=actor, target_user=membership.user,
        metadata={"team": membership.team.slug, "membership_id": membership.id,
                  "status": membership.status, **metadata},
    )


def join_or_apply(user, team, application_text=None, accepted_terms=False):
    """Join an open team, or apply to one that approves its members.

    A team with rules of its own needs them accepted; which version was
    accepted -- its day -- and when, stays with the membership.
    """
    _lock_team(team)
    reason = why_not_joinable(user, team)
    if reason:
        raise ConflictError(reason, code="team_not_joinable")
    rules = team_rules(team)
    if rules is not None and not accepted_terms:
        raise ValidationError(f"Please accept the rules of {team.name}.", code="team_terms_not_accepted")

    now = get_now_utc()
    membership = TeamMembership(team=team, user=user, applied_at=now)
    if rules is not None:
        membership.terms_accepted_at = now
        membership.terms_version = rules.stamp
    if team.admission_mode == ADMISSION_OPEN or may_rejoin_by_paying(user, team):
        # Joining an open team is being approved at once; the payment step
        # comes next, as after any approval. So is coming back soon after a
        # payment did not go through: paying again is all it takes.
        membership.status = APPROVED
        membership.approved_at = now
    else:
        membership.status = APPLIED
        if team.application_prompt:
            membership.application_text = (application_text or "").strip()[:5000] or None
    db.session.add(membership)
    db.session.flush()
    if membership.status == APPROVED:
        _payment_step(membership, now)

    name = _member_name(user)
    if membership.status == ACTIVE:
        _audit("team_joined", user, membership)
        _tell_leads(team, "team_member_joined", f"{name} joined {team.name}.", person_name=name)
    elif membership.status == APPROVED:
        # An open team that charges: the leads hear once the payment is in.
        _audit("team_joined", user, membership)
    else:
        _audit("team_applied", user, membership)
        _tell_leads(team, "team_application_received", f"{name} applied to {team.name}.", person_name=name)
    return membership


def _locked_membership(membership_id, team=None):
    membership = db.session.execute(
        locked(db.select(TeamMembership).where(TeamMembership.id == membership_id))
    ).scalar_one_or_none()
    if membership is None or (team is not None and membership.team_id != team.id):
        raise NotFoundError("That application or membership does not exist.")
    return membership


def _require_status(membership, allowed, message):
    if membership.status not in allowed:
        raise ConflictError(message, code="team_membership_state_changed")


def withdraw(user, team):
    """The applicant takes their application back."""
    current = ongoing_membership(user, team)
    if current is None:
        raise ConflictError("There is no application to withdraw.", code="team_nothing_to_withdraw")
    membership = _locked_membership(current.id, team)
    _require_status(membership, {APPLIED, INVITED, APPROVED}, "There is no application to withdraw.")
    if membership.payment_state == "processing":
        raise ConflictError("Your payment is on its way, so the application can no longer be withdrawn. "
                            "Once you are in, you can leave.", code="team_payment_processing")
    _stop_charging(membership, "withdrawn")
    membership.status = WITHDRAWN
    membership.ended_at = get_now_utc()
    _audit("team_application_withdrawn", user, membership)
    return membership


def leave(user, team, message=None):
    """The member leaves the team.

    Paid by subscription, it runs to the end of what is paid and the
    subscription stops then; there is no refund. Otherwise it ends now.
    ``message`` is passed on to the leads and kept with the membership.
    """
    message = (message or "").strip()[:1000] or None
    said = f" Their message: {message}" if message else ""
    current = ongoing_membership(user, team)
    if current is None or current.status != ACTIVE:
        raise ConflictError("You are not a member of this team.", code="team_not_a_member")
    membership = _locked_membership(current.id, team)
    _require_status(membership, {ACTIVE}, "You are not a member of this team.")
    from .clock import get_membership_today

    paid_once = (membership.payment_mode == "one_time" and membership.paid_until is not None
                 and membership.paid_until >= get_membership_today())
    if paid_once or (membership.stripe_subscription_id and membership.payment_mode == "subscription"):
        from . import payments
        from .membership import format_membership_date_display

        if membership.ends_on is not None:
            return membership
        if not paid_once:
            # Stripe stops at the end of what is paid; the membership runs to then.
            payments.set_cancel_at_period_end(membership.stripe_subscription_id, True)
        membership.ends_on = membership.paid_until or get_now_utc().date()
        membership.end_note = message
        _audit("team_leaving", user, membership, ends_on=membership.ends_on.isoformat())
        name = _member_name(user)
        _tell_leads(team, "team_member_leaving",
                    f"{name} is leaving {team.name} on {format_membership_date_display(membership.ends_on)}.{said}",
                    person_name=name)
        return membership
    membership.status = ENDED
    membership.ended_at = get_now_utc()
    membership.end_reason = END_LEFT
    membership.end_note = message
    _audit("team_left", user, membership)
    _sync_forum([user], team, f"left team {team.slug}")
    name = _member_name(user)
    _tell_leads(team, "team_member_left", f"{name} left {team.name}.{said}", person_name=name)
    return membership


def stay(user, team):
    """Take back leaving, before the day comes."""
    current = ongoing_membership(user, team)
    if current is None or current.status != ACTIVE or current.ends_on is None:
        raise ConflictError("You are not leaving this team.", code="team_not_leaving")
    if current.ends_with_association:
        raise ConflictError("This ends with your association membership. Keep that, and the team continues too.",
                            code="team_ends_with_association")
    membership = _locked_membership(current.id, team)
    from . import payments

    if membership.stripe_subscription_id and membership.payment_mode == "subscription":
        payments.set_cancel_at_period_end(membership.stripe_subscription_id, False)
    membership.ends_on = None
    _audit("team_leaving_cancelled", user, membership)
    return membership


def stop_charging(membership, why):
    """Cancel the membership's subscription and close its Checkout, now, without refund.

    Never fails the caller: what they decided stands. If Stripe cannot be
    reached, the site admins are told to cancel it by hand.
    """
    from . import ExternalServiceError, payments

    if membership.stripe_checkout_session_id:
        payments.expire_checkout_session(membership.stripe_checkout_session_id)
        membership.stripe_checkout_session_id = None
    subscription_id = membership.stripe_subscription_id
    if not subscription_id:
        return
    try:
        payments.cancel_subscription(subscription_id, reason=f"team membership {membership.id}: {why}")
    except ExternalServiceError as exc:
        from ..notification_service import ADMIN_ERROR_CHANNEL
        from .notifications import queue_curated_admin_notification

        current_app.logger.error("Could not cancel team subscription %s: %s", subscription_id, exc)
        queue_curated_admin_notification(
            ADMIN_ERROR_CHANNEL, "team_subscription_not_cancelled",
            "A team membership ended, but its subscription could not be cancelled. Cancel it in Stripe.",
            payload={"team": membership.team.slug, "team_membership_id": membership.id,
                     "subscription_id": subscription_id, "why": why},
            severity="warning",
        )
        return
    membership.stripe_subscription_id = None


_stop_charging = stop_charging


def rejoin_by_paying_until(user, team, today=None):
    """The last day the person may come back by paying, without applying --
    their last membership here ended unpaid not long ago -- or None."""
    from datetime import timedelta

    from .clock import get_membership_today

    if user is None or team.payment_mode == PAYMENT_NONE:
        return None
    today = today or get_membership_today()
    last = db.session.execute(
        db.select(TeamMembership).filter_by(team_id=team.id, user_id=user.id, status=ENDED)
        .order_by(TeamMembership.id.desc())
    ).scalars().first()
    if last is None or last.end_reason not in (END_PAYMENT_FAILED, END_NOT_RENEWED) or last.ended_at is None:
        return None
    until = last.ended_at.date() + timedelta(days=REJOIN_DAYS)
    return until if today <= until else None


def may_rejoin_by_paying(user, team, today=None):
    """Whether the person's last membership here ended unpaid recently enough
    to come back by paying, without applying."""
    return rejoin_by_paying_until(user, team, today) is not None


def invite(actor, team, membership_id, meeting_details):
    meeting_details = (meeting_details or "").strip()
    if not meeting_details:
        raise ValidationError("Say when and where you would like to meet.", code="team_meeting_missing")
    membership = _locked_membership(membership_id, team)
    _require_status(membership, {APPLIED, INVITED}, "This application has already been decided.")
    membership.status = INVITED
    membership.invited_at = get_now_utc()
    membership.meeting_details = meeting_details[:5000]
    membership.decided_by_user_id = getattr(actor, "id", None)
    _audit("team_invited", actor, membership)
    _tell_person("team_invited", membership, meeting_details=membership.meeting_details)
    return membership


def _payment_step(membership, now):
    """The step between being approved and being a member, for every team.

    Every approval -- and joining an open team, which is approval at once --
    passes through here, whether or not the team charges anything, so the flow
    is the same for every team and payment can be added without changing it.

    A free team settles the step on the spot: the record says so, the person
    becomes a member, and nobody is sent anything about a payment that did not
    happen. Returns whether the person is now a member. A team that charges
    stays *approved* here until its first payment arrives; see
    services/team_payments.py.
    """
    team = membership.team
    membership.payment_mode = team.payment_mode
    if team.payment_mode != PAYMENT_NONE:
        return False
    membership.payment_settled_at = now
    membership.status = ACTIVE
    membership.started_at = now
    _audit("team_payment_settled", None, membership, payment_mode=team.payment_mode)
    _sync_forum([membership.user], team, f"joined team {team.slug}")
    return True


def approve(actor, team, membership_id):
    """Accept an applicant. They become a member once the payment step is settled."""
    _lock_team(team)
    membership = _locked_membership(membership_id, team)
    _require_status(membership, {APPLIED, INVITED}, "This application has already been decided.")
    if is_full(team):
        raise ConflictError("The team is full.", code="team_full")
    now = get_now_utc()
    membership.status = APPROVED
    membership.approved_at = now
    membership.decided_by_user_id = getattr(actor, "id", None)
    _audit("team_approved", actor, membership)
    if _payment_step(membership, now):
        # One email: welcome. The payment step of a free team is not worth one.
        _tell_person("team_approved", membership)
    else:
        _tell_person("team_payment_due", membership)
    return membership


def reject(actor, team, membership_id):
    membership = _locked_membership(membership_id, team)
    _require_status(membership, {APPLIED, INVITED, APPROVED}, "This application has already been decided.")
    _stop_charging(membership, "not accepted")
    membership.status = REJECTED
    membership.ended_at = get_now_utc()
    membership.decided_by_user_id = getattr(actor, "id", None)
    _audit("team_rejected", actor, membership)
    _tell_person("team_rejected", membership)
    return membership


def remove(actor, team, membership_id, reason):
    """End somebody's team membership now, with a reason for the record.

    The reason is for the leads and the audit log; the person is told only
    that their membership has ended.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Give a reason; it stays in the record.", code="team_removal_reason_missing")
    membership = _locked_membership(membership_id, team)
    _require_status(membership, {ACTIVE}, "This person is no longer a member.")
    _stop_charging(membership, "removed by a lead")
    membership.status = ENDED
    membership.ended_at = get_now_utc()
    membership.ends_on = None
    membership.end_reason = END_REMOVED
    membership.end_note = reason[:2000]
    membership.decided_by_user_id = getattr(actor, "id", None)
    _audit("team_member_removed", actor, membership)
    _tell_person("team_removed", membership)
    _sync_forum([membership.user], team, f"removed from team {team.slug}")
    return membership


# --- What the leads see ----------------------------------------------------


def team_memberships(team, statuses):
    return db.session.execute(
        db.select(TeamMembership)
        .where(TeamMembership.team_id == team.id, TeamMembership.status.in_(tuple(statuses)))
        .order_by(TeamMembership.id.desc())
    ).scalars().all()


def former_members(team):
    """Everybody who was in the team and is not now, most recently active first.

    One entry per person, with every period they were in it, kept for good:
    who was in the team in which year is worth knowing long after.
    """
    from .membership import format_date_display

    ended = team_memberships(team, {ENDED})
    current = {membership.user_id for membership in team_memberships(team, {ACTIVE})}
    people = {}
    for membership in ended:
        if membership.user_id not in current:
            people.setdefault(membership.user_id, []).append(membership)

    def began(membership):
        return membership.started_at or membership.created_at

    def period(membership):
        start, end = began(membership).year, (membership.ended_at or began(membership)).year
        return str(start) if start == end else f"{start}–{end}"

    rows = []
    for user_id, stints in people.items():
        stints.sort(key=began)
        last = stints[-1]
        rows.append({
            "user_id": user_id,
            "name": _member_name(last.user),
            "periods": ", ".join(dict.fromkeys(period(stint) for stint in stints)),
            "last_active": last.ended_at,
            "last_active_shown": format_date_display(last.ended_at) if last.ended_at else "",
            "end_reason": last.end_reason,
        })
    return sorted(rows, key=lambda row: row["last_active"] or row["user_id"], reverse=True)


def history_of(team, user):
    return db.session.execute(
        db.select(TeamMembership).filter_by(team_id=team.id, user_id=user.id).order_by(TeamMembership.id.desc())
    ).scalars().all()


def notes_about(team, user):
    from ..db_models import TeamNote

    return db.session.execute(
        db.select(TeamNote).filter_by(team_id=team.id, user_id=user.id).order_by(TeamNote.id.desc())
    ).scalars().all()


def add_note(actor, team, user, body):
    from ..db_models import TeamNote

    body = (body or "").strip()
    if not body:
        raise ValidationError("The note is empty.", code="team_note_empty")
    if not history_of(team, user):
        raise NotFoundError("That person has never been in this team.")
    note = TeamNote(team=team, user=user, author_user_id=getattr(actor, "id", None), body=body[:5000])
    db.session.add(note)
    db.session.flush()
    log_audit_event("teams", "team_note_added", actor_user=actor, target_user=user,
                    metadata={"team": team.slug, "note_id": note.id})
    return note


def update_team_by_lead(actor, team, *, description=KEEP, application_prompt=KEEP, applications_open=KEEP):
    """The part of a team's settings that belongs to its leads; what is not
    given stays as it is."""
    before = _snapshot(team)
    if description is not KEEP:
        team.description = (description or "").strip() or None
    if application_prompt is not KEEP:
        team.application_prompt = (application_prompt or "").strip()[:255] or None
    if applications_open is not KEEP:
        team.applications_open = bool(applications_open)
    after = _snapshot(team)
    if before != after:
        log_audit_event("teams", "team_updated", actor_user=actor, before=before, after=after)
    return team


def avatar_token_for(user):
    """The public token of the approved forum picture, or None."""
    from ..forum_service import FORUM_AVATAR_STATUS_APPROVED

    for submission in user.forum_avatar_submissions or []:
        if submission.status == FORUM_AVATAR_STATUS_APPROVED and submission.public_token:
            return submission.public_token
    return None


def person_details(user):
    """What a lead sees about a person: no address, nothing about payment."""
    member = user.member
    return {
        "name": _member_name(user),
        "university_email": member.email_work if member else None,
        "private_email": user.email,
        "phone": member.phone_private if member else None,
        "cohort": member.year_group if member else None,
    }


def member_count(team):
    """How many are in the team now."""
    return db.session.execute(
        db.select(db.func.count(TeamMembership.id)).filter_by(team_id=team.id, status=ACTIVE)
    ).scalar_one()


def roster(team):
    """The active members, by surname, for the team's own page and the lead's list."""
    members = team_memberships(team, {ACTIVE})
    return sorted(
        (
            {
                "membership": membership,
                "user": membership.user,
                "avatar_token": avatar_token_for(membership.user),
                "is_lead": any(team_role.user_id == membership.user_id and team_role.role == ROLE_LEAD
                               for team_role in team.roles),
                **person_details(membership.user),
            }
            for membership in members
        ),
        key=_surname_first,
    )


def _surname_first(row):
    member = getattr(row["user"], "member", None)
    if member is not None:
        return ((member.last_name or "").lower(), (member.first_name or "").lower())
    return (row["name"].lower(), "")


EXPORT_COLUMNS = ("Name", "University email", "Private email", "Phone", "Cohort", "Member since", "Paid until")


def csv_cell(value):
    """A value for a CSV cell that a spreadsheet will show, never run.

    Names are typed by members themselves, and a spreadsheet takes a cell
    starting with =, +, - or @ for a formula. A phone number (+43 ...) stays
    as it is.
    """
    text = "" if value is None else str(value)
    if text[:1] in ("=", "@", "\t", "\r") or (
        text[:1] in ("+", "-") and not re.fullmatch(r"[+-][\d ()/.-]*", text)
    ):
        return "'" + text
    return text


def export_rows(team):
    from .membership import format_date_display

    for row in roster(team):
        started = row["membership"].started_at
        paid_until = row["membership"].paid_until
        yield tuple(csv_cell(value) for value in (
            row["name"], row["university_email"] or "", row["private_email"] or "",
            row["phone"] or "", row["cohort"] or "", format_date_display(started) if started else "",
            format_date_display(paid_until) if paid_until else "",
        ))


# --- Keeping teams in step with the association ----------------------------


def end_lapsed_team_memberships():
    """End team memberships and applications of people no longer in the association.

    Run every night. Applications become withdrawn, memberships ended, both
    with the same reason; the leads get one message per team naming everybody.
    Roles are left alone: they stop counting by themselves, and come back if
    the person returns and joins again.
    """
    if not teams_enabled():
        return 0
    ongoing = db.session.execute(
        db.select(TeamMembership).where(TeamMembership.status.in_(ONGOING))
    ).scalars().all()
    ended_by_team = {}
    now = get_now_utc()
    for membership in ongoing:
        if is_active_association_member(membership.user) or membership.user.disabled_at is not None:
            continue
        membership = _locked_membership(membership.id)
        if membership.status not in ONGOING:
            continue
        was_member = membership.status == ACTIVE
        _stop_charging(membership, "association membership ended")
        membership.status = ENDED if was_member else WITHDRAWN
        membership.ended_at = now
        membership.ends_on = None
        membership.end_reason = END_MEMBERSHIP_ENDED
        _audit("team_membership_lapsed", None, membership)
        if was_member:
            _sync_forum([membership.user], membership.team, f"left team {membership.team.slug}")
            ended_by_team.setdefault(membership.team, []).append(_member_name(membership.user))

    for team, names in ended_by_team.items():
        _tell_leads(
            team, "team_members_lapsed",
            f"{len(names)} member(s) of {team.name} left with their association membership: {', '.join(sorted(names))}.",
            names=sorted(names),
        )
    return sum(len(names) for names in ended_by_team.values())


def lapse_unpaid_approvals(now=None):
    """Approvals not paid for within APPROVAL_PAYMENT_DAYS lapse.

    Run every night. Only teams that charge have approvals waiting for a
    payment; a free team settles the step at once. The person is told they can
    apply again.
    """
    from datetime import timedelta

    if not teams_enabled():
        return 0
    now = now or get_now_utc()
    cutoff = now - timedelta(days=APPROVAL_PAYMENT_DAYS)
    lapsed = 0
    for waiting in db.session.execute(
        db.select(TeamMembership).where(TeamMembership.status == APPROVED)
    ).scalars().all():
        approved_at = waiting.approved_at
        if approved_at is None:
            continue
        if approved_at.tzinfo is None:
            from datetime import timezone

            approved_at = approved_at.replace(tzinfo=timezone.utc)
        if approved_at > cutoff:
            continue
        if waiting.payment_state == "processing" and approved_at > now - timedelta(days=APPROVAL_PROCESSING_DAYS):
            continue  # a SEPA debit on its way is paid, only not yet arrived
        membership = _locked_membership(waiting.id)
        if membership.status != APPROVED:
            continue
        _stop_charging(membership, "approval lapsed unpaid")
        membership.status = WITHDRAWN
        membership.ended_at = now
        membership.end_reason = END_NOT_PAID
        _audit("team_approval_lapsed", None, membership)
        _tell_person("team_approval_lapsed", membership)
        lapsed += 1
    return lapsed


def forget_for_erasure(user):
    """What erasing an account does to its teams.

    Its memberships and applications end, its roles go, what the person
    wrote in their applications is blanked and the leads' notes about them
    are deleted. Notes they wrote about others as a lead stay.
    """
    from ..db_models import TeamNote

    now = get_now_utc()
    for membership in db.session.execute(
        db.select(TeamMembership).filter_by(user_id=user.id)
    ).scalars():
        membership.application_text = None
        _stop_charging(membership, "account erased")
        if membership.status in ONGOING:
            membership.status = ENDED if membership.status == ACTIVE else WITHDRAWN
            membership.ended_at = now
            membership.end_reason = END_ACCOUNT_ERASED
    for team_role in db.session.execute(db.select(TeamRole).filter_by(user_id=user.id)).scalars():
        db.session.delete(team_role)
    for note in db.session.execute(db.select(TeamNote).filter_by(user_id=user.id)).scalars():
        db.session.delete(note)


# --- The access list ---------------------------------------------------------
#
# The team's current members, by name and university address, emailed to
# whoever gives access to the team's rooms -- on a button, or by itself on the
# days the team set. The leads are copied in, visibly, as they would be if
# they wrote it themselves.

_ADDRESS = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


def parse_recipients(text):
    """Addresses from text separated by commas, semicolons or lines."""
    addresses = [part.strip().lower() for part in re.split(r"[,;\s]+", text or "") if part.strip()]
    invalid = [address for address in addresses if not _ADDRESS.match(address)]
    if invalid:
        raise ValidationError(
            f"Not an email address: {', '.join(invalid)}", code="team_access_list_recipient_invalid",
        )
    return list(dict.fromkeys(addresses))


def parse_dates(text):
    """Days of the year from "15.10, 15.03" (or one per line), in calendar order."""
    from datetime import date

    found = set()
    for part in re.split(r"[,;\s]+", text or ""):
        part = part.strip().rstrip(".")
        if not part:
            continue
        match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})", part)
        try:
            day, month = int(match.group(1)), int(match.group(2))
            date(2000, month, day)  # a real day; 2000 was a leap year
        except (AttributeError, ValueError):
            raise ValidationError(
                f"Not a day of the year: {part}. Write day and month, like 15.10.",
                code="team_access_list_date_invalid",
            ) from None
        found.add((month, day))
    return [(day, month) for month, day in sorted(found)]


def format_dates(dates):
    return ", ".join(f"{day:02d}.{month:02d}" for day, month in dates)


def set_access_list_enabled(actor, team, enabled):
    """Whether the team has an access list at all -- for site admins. Off,
    nothing is sent; what was set up is kept for when it is switched on."""
    enabled = bool(enabled)
    if team.access_list_enabled == enabled:
        return team
    team.access_list_enabled = enabled
    log_audit_event("teams", "team_access_list_switched", actor_user=actor,
                    metadata={"team": team.slug, "enabled": enabled})
    return team


def update_access_list(actor, team, *, recipients, dates, auto_send):
    recipients = parse_recipients(recipients)
    dates = parse_dates(dates)
    if auto_send and not (recipients and dates):
        raise ValidationError(
            "Sending by itself needs at least one recipient and one date.",
            code="team_access_list_incomplete",
        )
    before = {
        "recipients": team.access_list_recipients,
        "dates": team.access_list_dates,
        "auto_send": team.access_list_auto_send,
    }
    team.access_list_recipients = "\n".join(recipients) or None
    team.access_list_dates = format_dates(dates) or None
    team.access_list_auto_send = bool(auto_send)
    after = {
        "recipients": team.access_list_recipients,
        "dates": team.access_list_dates,
        "auto_send": team.access_list_auto_send,
    }
    if before != after:
        log_audit_event("teams", "team_access_list_settings_changed", actor_user=actor,
                        before=before, after=after, metadata={"team": team.slug})
    return team


def last_access_list(team):
    from ..db_models import TeamAccessListSend

    return db.session.execute(
        db.select(TeamAccessListSend).filter_by(team_id=team.id).order_by(TeamAccessListSend.id.desc())
    ).scalars().first()


def access_list_comparison(team):
    """The list as it would go out now, against the last one sent.

    Not the truth about who can open the door -- people get access in other
    ways too -- only a help: who is new on our list, and who was on it last
    time and is no longer in the team. Compared by account, so a changed name
    or address is not mistaken for somebody new.
    """
    previous = last_access_list(team)
    before = {entry["user_id"]: entry for entry in (previous.entries if previous else [])}
    current = roster(team)
    now_ids = {row["user"].id for row in current}
    return {
        "rows": [
            {
                "user_id": row["user"].id,
                "name": row["name"],
                "email": row["university_email"] or "",
                "new": previous is not None and row["user"].id not in before,
            }
            for row in current
        ],
        "gone": sorted(
            (entry for user_id, entry in before.items() if user_id not in now_ids),
            key=lambda entry: (entry.get("name") or "").lower(),
        ),
        "compared_with": previous.sent_on if previous else None,
    }


def next_access_list_date(team, today=None):
    """The next day the list goes out by itself, or None."""
    from datetime import date

    from .clock import get_membership_today

    if not team.access_list_auto_send:
        return None
    today = today or get_membership_today()
    candidates = []
    for day, month in parse_dates(team.access_list_dates):
        for year in (today.year, today.year + 1):
            try:
                candidate = date(year, month, day)
            except ValueError:  # 29.02 outside a leap year
                continue
            if candidate >= today:
                candidates.append(candidate)
                break
    return min(candidates) if candidates else None


def access_list_cc(team):
    """The leads in force, copied in."""
    return [team_role.user.email for team_role in role_holders(team, ROLE_LEAD) if role_counts(team_role)]


def access_list_message(team, today=None):
    """Subject and template values of the email, for sending and for the preview."""
    from .clock import get_membership_today
    from .membership import format_date_display

    today = today or get_membership_today()
    comparison = access_list_comparison(team)
    rows = comparison["rows"]
    shown = format_date_display(today)
    compared_with = comparison["compared_with"]
    return (
        f"{team.name}: current members ({shown})",
        {
            "preview_text": f"{len(rows)} current member(s) of {team.name}.",
            "heading": f"Current members of {team.name}",
            "intro": f"These members of {team.name} should have access, as of {shown}:",
            "rows": rows,
            "gone": comparison["gone"],
            "compared_note": (
                f"New: not on our list of {format_date_display(compared_with)}." if compared_with else None
            ),
            "team_badge_name": team.name,
        },
    )


def send_access_list(actor, team, *, today=None, automatic=False):
    """Email the list now. Raises if there is nobody to send it to or sending fails."""
    from ..mail_utils import send_mail
    from . import ExternalServiceError
    from .clock import get_membership_today
    from .notifications import get_notification_service

    today = today or get_membership_today()
    if not team.access_list_enabled:
        raise ConflictError("This team has no access list.", code="team_access_list_off")
    recipients = parse_recipients(team.access_list_recipients)
    if not recipients:
        raise ValidationError("Add who receives the list first.", code="team_access_list_no_recipients")
    sender = get_notification_service().get_sender_account()
    if not sender:
        raise ExternalServiceError("No sender address is set up for notifications.",
                                   code="team_access_list_no_sender")

    subject, template_vars = access_list_message(team, today)
    attachments = None
    logo = logo_file(team)
    if logo is not None:
        attachments = [{"path": str(logo), "cid": "teamlogo"}]
        template_vars["team_logo_cid"] = "teamlogo"
    ok, error = send_mail(
        from_account=sender,
        to_email=recipients[0],
        cc_emails=[*recipients[1:], *access_list_cc(team)],
        subject=subject,
        template_name="team_access_list.html",
        attachments=attachments,
        return_error=True,
        **template_vars,
    )
    if not ok:
        raise ExternalServiceError(f"The list could not be sent: {error}", code="team_access_list_send_failed")

    team.access_list_last_sent_on = today
    _remember_access_list(team, today, template_vars["rows"], automatic)
    log_audit_event(
        "teams", "team_access_list_sent", actor_user=actor,
        metadata={"team": team.slug, "members": len(template_vars["rows"]),
                  "recipients": len(recipients), "automatic": automatic},
    )
    return len(template_vars["rows"])


def _remember_access_list(team, today, rows, automatic):
    """Keep what was just sent, in place of what was sent before."""
    from ..db_models import TeamAccessListSend

    db.session.execute(db.delete(TeamAccessListSend).where(TeamAccessListSend.team_id == team.id))
    db.session.add(TeamAccessListSend(
        team=team, sent_on=today, automatic=automatic,
        entries=[{"user_id": row["user_id"], "name": row["name"], "email": row["email"]} for row in rows],
    ))


def send_due_access_lists(today=None):
    """Send every list due today that has not gone out today. Returns how many went.

    Run every night, after the memberships of people who left the association
    have been ended, so the list is current. A list that cannot be sent is
    reported to the admins, who can send it from the team's page once the
    problem is fixed; it is not tried again by itself.
    """
    from ..notification_service import ADMIN_ERROR_CHANNEL
    from . import ServiceError
    from .clock import get_membership_today
    from .notifications import queue_curated_admin_notification

    if not teams_enabled():
        return 0
    today = today or get_membership_today()
    sent = 0
    for team in all_teams(include_archived=False):
        if (not team.access_list_enabled or not team.access_list_auto_send
                or team.access_list_last_sent_on == today):
            continue
        try:
            due = (today.day, today.month) in parse_dates(team.access_list_dates)
        except ValidationError:
            due = False
        if not due:
            continue
        try:
            send_access_list(None, team, today=today, automatic=True)
        except ServiceError as error:
            queue_curated_admin_notification(
                ADMIN_ERROR_CHANNEL, "team_access_list_failed",
                f"The access list of {team.name} could not be sent: {error.message}",
                payload={"team": team.slug, "what_to_do": "Check the recipients and the mail setup, "
                         "then send it from the team's page."},
                object_type="team", object_id=team.id,
            )
            continue
        sent += 1
    return sent
