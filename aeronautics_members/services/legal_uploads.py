"""Legal texts uploaded in the portal (docs/legal-texts-in-portal-plan.md).

A version of one of the association's texts, or of a team's rules, is
uploaded as a Markdown file -- the same file the preview takes, checked the
same way -- and kept as a draft (``LegalTextVersion``). Whoever may manage the
legal texts (``Permission.LEGAL_TEXTS``) uploads any; a team's leads upload
drafts of their own team's rules, and whoever may publish them
(``Permission.LEGAL_PUBLISH``) is told. Publishing
gives it its day -- the day it is published, by which acceptances are kept --,
the day it applies from, and, if there was one, who approved it and when.
From then on it is a version like the files in legal/ (services/legal_texts.py
reads both).

A published version is never changed or deleted, with one exception: it may
be corrected within ``CORRECTION_WINDOW`` of publishing while nobody has
accepted it (a typo found a minute later needs no new version). The earlier
wording goes into the audit log. A draft may be deleted; nothing else may.
"""

import tempfile
from datetime import timedelta

from flask import current_app

from .. import legal_pdf
from ..db_models import LegalTextVersion, Member, Role, TeamMembership, User, db
from ..permissions import Permission, roles_with
from . import ConflictError, NotFoundError, ValidationError
from . import legal_texts as legal
from .audit import log_audit_event
from .clock import get_membership_today, get_now_utc

CORRECTION_WINDOW = timedelta(hours=24)
APPROVED_BY_MAX = 100

DRAFT_UPLOADED = "legal_text_draft_uploaded"
RULES_PUBLISHED = "team_rules_published"


# --- Checking an upload ---------------------------------------------------------------


def _checked(german_upload, english_upload=None):
    """``(versions, bodies, problems)`` of uploaded files, each ``(data, filename)``: checked as
    the preview and CI check a file, and read. Nothing is kept on disk."""
    with tempfile.TemporaryDirectory() as folder:
        uploads = [("German", german_upload, legal.AUTHORITATIVE)]
        if english_upload is not None:
            uploads.append(("English", english_upload, "en"))
        versions, bodies, problems, paths = [], [], [], []
        for role, (data, name), language in uploads:
            path, slug, found_language, team, found = legal_pdf._uploaded(folder, data, name, role)
            problems += found
            if path is None:
                continue
            paths.append(path)
            if found_language != language:
                problems.append(f'{role}: language is "{found_language}"; this file must be "{language}".')
                continue
            version, found = legal._check(path, slug, language, team)
            problems += [f"{role}: {problem}" for problem in found]
            if version is not None:
                versions.append(version)
                bodies.append(legal.body(version))
        legal.forget(paths)
    if len(versions) == 2:
        german, english = versions
        if (english.slug, english.team) != (german.slug, german.team):
            problems.append("The English file must be the translation of the German one: the same "
                            "document and team.")
    return versions, bodies, problems


def _invalid(problems, action="uploaded"):
    raise ValidationError(f"The text was not {action}.", code="legal_upload_invalid",
                          details={"problems": list(problems)})


def _team_of(slug):
    from . import teams as teams_service

    try:
        return teams_service.get_team(slug)
    except NotFoundError:
        return None


# --- Uploading a draft --------------------------------------------------------------------


def upload_draft(actor, german_upload, english_upload=None, *, team=None):
    """Keep uploaded files as a draft. ``team``: a team's leads uploading their own rules -- the
    file must be that team's rules. Returns the German version's record."""
    versions, bodies, problems = _checked(german_upload, english_upload)
    if problems or not versions:
        _invalid(problems or ["The German file is missing."])
    german = versions[0]
    if team is not None and (german.slug != legal.TEAM_RULES or german.team != team.slug):
        _invalid([f'This is not the rules of {team.name}: the front matter needs document: "team-rules" '
                  f'and team: "{team.slug}".'])
    if german.team is not None and _team_of(german.team) is None:
        _invalid([f'No team has the short name "{german.team}".'])

    now, today = get_now_utc(), get_membership_today()
    record = None
    for version, text in zip(versions, bodies):
        row = LegalTextVersion(
            document=version.slug, team_slug=version.team, language=version.language,
            translation_of=record, status="draft", version=today, effective_from=version.effective_from,
            title=version.title, revision=version.revision, body=text,
            approved_by=version.approved_by, approved_on=version.approved_on,
            uploaded_at=now, uploaded_by=actor,
        )
        db.session.add(row)
        record = record or row
    db.session.flush()
    log_audit_event("legal", "legal_text_uploaded", actor_user=actor, target_user=actor,
                    metadata={"document": german.slug, "team": german.team, "record": record.id,
                              "title": german.title, "english": len(versions) == 2,
                              "by_team_lead": team is not None})
    if team is not None:
        _tell_the_association(team, record)
    return record


# --- Publishing ---------------------------------------------------------------------------


def _german_record(record_id):
    record = db.session.get(LegalTextVersion, record_id)
    if record is None or record.translation_of_id is not None:
        raise NotFoundError("There is no such version.", code="no_version")
    return record


def _published_versions(record):
    """Every published German version of the record's text, files and records alike."""
    return [v for v in legal._all_versions(record.document, legal.AUTHORITATIVE, record.team_slug)
            if v.status == "published" and v.record_id != record.id]


def publish(actor, record_id, *, effective_from, approved_by=None, approved_on=None):
    """Publish a draft: today is its version's day; it applies from ``effective_from``."""
    record = _german_record(record_id)
    if record.status != "draft":
        raise ConflictError("This version is published already.", code="legal_published_already")
    today = get_membership_today()
    if effective_from < today:
        raise ValidationError("A version cannot apply from a day before it is published.",
                              code="legal_effective_past", details={"fields": {
                                  "effective_from": "Today or later."}})
    approved_by = (approved_by or "").strip() or None
    if approved_by is not None and len(approved_by) > APPROVED_BY_MAX:
        raise ValidationError("Too long.", details={"fields": {"approved_by": f"At most {APPROVED_BY_MAX} characters."}})
    if approved_on is not None and approved_on > today:
        raise ValidationError("An approval cannot be in the future.", details={"fields": {
            "approved_on": "Today or earlier."}})
    from .legal_editor import problems_of

    problems = problems_of(record)
    if problems:
        _invalid(problems, action="published")
    others = _published_versions(record)
    if any(v.version == today for v in others):
        raise ConflictError("A version of this text was published today already. Correct that one instead, "
                            "or publish this one tomorrow.", code="legal_published_today")
    if any(v.effective_from == effective_from for v in others):
        raise ConflictError("Another version of this text applies from that day; only one can.",
                            code="legal_effective_taken")

    now = get_now_utc()
    for row in [record, *record.translations]:
        row.status = "published"
        row.version = today
        row.effective_from = effective_from
        row.approved_by = approved_by
        row.approved_on = approved_on
        row.published_at = now
        row.published_by = actor
    log_audit_event("legal", "legal_text_published", actor_user=actor, target_user=actor,
                    metadata={"document": record.document, "team": record.team_slug, "record": record.id,
                              "title": record.title, "version": today.isoformat(),
                              "effective_from": effective_from.isoformat(), "approved_by": approved_by,
                              "approved_on": approved_on.isoformat() if approved_on else None})
    if record.team_slug is not None:
        _tell_the_leads(record)
    return record


# --- Correcting ---------------------------------------------------------------------------


def accepted_by_anyone(version):
    """Whether anybody has accepted this version: a member at signup, or a team's member when joining."""
    if version.team is not None:
        if version.slug != legal.TEAM_RULES:
            return False
        from ..db_models import Team

        accepted = db.session.execute(
            db.select(TeamMembership.terms_version).join(Team, TeamMembership.team_id == Team.id).where(
                Team.slug == version.team, TeamMembership.terms_version.is_not(None))).scalars()
        return any(stamp.date() == version.version for stamp in accepted)
    text = legal.BY_SLUG.get(version.slug)
    if text is None or not text.accepted_at_signup:
        return False
    day = version.version.isoformat()
    accepted = db.session.execute(
        db.select(Member.legal_versions_accepted).where(Member.legal_versions_accepted.is_not(None))).scalars()
    return any(isinstance(chosen, dict) and chosen.get(version.slug) == day for chosen in accepted)


def why_not_correctable(record, now=None):
    """Why a published version can no longer be corrected, or None if it can."""
    now = now or get_now_utc()
    if record.status != "published":
        return "It is a draft: delete it and upload it again."
    published_at = record.published_at
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=now.tzinfo)
    if now - published_at > CORRECTION_WINDOW:
        return "It was published more than 24 hours ago: publish a new version instead."
    if accepted_by_anyone(legal.record(record.id)):
        return "Somebody has accepted it already: publish a new version instead."
    return None


def correct(actor, record_id, german_upload, english_upload=None):
    """Replace a published version's text, while it may still be corrected. Its day, the day it
    applies from and its approval stay; its translation is replaced only if a new one comes."""
    record = _german_record(record_id)
    reason = why_not_correctable(record)
    if reason is not None:
        raise ConflictError(f"This version can no longer be corrected. {reason}", code="legal_not_correctable")
    versions, bodies, problems = _checked(german_upload, english_upload)
    if problems or not versions:
        _invalid(problems or ["The German file is missing."], action="corrected")
    german = versions[0]
    if (german.slug, german.team) != (record.document, record.team_slug):
        _invalid(["The file is a different text: the front matter must name the same document and team."],
                 action="corrected")

    now = get_now_utc()
    before = {"title": record.title, "revision": record.revision, "body": record.body}
    record.title, record.revision, record.body, record.corrected_at = german.title, german.revision, bodies[0], now
    if len(versions) == 2:
        english = next(iter(record.translations), None)
        if english is None:
            english = LegalTextVersion(
                document=record.document, team_slug=record.team_slug, language="en", translation_of=record,
                status=record.status, version=record.version, effective_from=record.effective_from,
                approved_by=record.approved_by, approved_on=record.approved_on, uploaded_at=now,
                uploaded_by=actor, published_at=record.published_at, published_by=record.published_by,
                title="", body="")
            db.session.add(english)
        english.title, english.revision, english.body = versions[1].title, versions[1].revision, bodies[1]
        english.corrected_at = now
    log_audit_event("legal", "legal_text_corrected", actor_user=actor, target_user=actor,
                    before=before, after={"title": record.title, "revision": record.revision, "body": record.body},
                    metadata={"document": record.document, "team": record.team_slug, "record": record.id,
                              "version": record.version.isoformat()})
    return record


# --- Deleting a draft ---------------------------------------------------------------------


def delete_draft(actor, record_id, *, team=None):
    """Delete a draft -- never a published version. ``team``: only that team's rules (a lead)."""
    record = _german_record(record_id)
    if team is not None and record.team_slug != team.slug:
        raise NotFoundError("There is no such version.", code="no_version")
    if record.status != "draft":
        raise ConflictError("A published version is never deleted.", code="legal_published_kept")
    log_audit_event("legal", "legal_draft_deleted", actor_user=actor, target_user=actor,
                    metadata={"document": record.document, "team": record.team_slug, "record": record.id,
                              "title": record.title})
    db.session.delete(record)


def team_drafts(team):
    """A team's rules uploaded in the portal and not published yet, newest first."""
    return db.session.execute(
        db.select(LegalTextVersion).where(
            LegalTextVersion.team_slug == team.slug, LegalTextVersion.translation_of_id.is_(None),
            LegalTextVersion.status == "draft")
        .order_by(LegalTextVersion.uploaded_at.desc())).scalars().all()


# --- Telling people -----------------------------------------------------------------------


def _publishers():
    slugs = roles_with(Permission.LEGAL_PUBLISH)
    if not slugs:
        return []
    return db.session.execute(
        db.select(User.email).where(User.email_verified_at.is_not(None), User.deleted_at.is_(None),
                                    User.roles.any(Role.slug.in_(slugs)))
        .order_by(User.email.asc())).scalars().all()


def _tell_the_association(team, record):
    """A team's leads uploaded new rules: whoever publishes them is told."""
    from .notifications import queue_user_status_notification

    for email in _publishers():
        queue_user_status_notification(DRAFT_UPLOADED, f"New rules of {team.name} to publish", email,
                                       payload={"team": team.name, "title": record.title})


def _tell_the_leads(record):
    """A team's rules are published: its leads are told."""
    from . import teams as teams_service
    from .notifications import queue_user_status_notification

    team = _team_of(record.team_slug)
    if team is None:
        return
    for team_role in teams_service.role_holders(team, teams_service.ROLE_LEAD):
        if teams_service.role_counts(team_role) and team_role.user.email:
            queue_user_status_notification(
                RULES_PUBLISHED, f"The rules of {team.name} are published", team_role.user.email,
                payload={"team": team.name, "title": record.title,
                         "effective_from": record.effective_from.isoformat(), "slug": team.slug})
    current_app.logger.info("Told the leads of %s that their rules are published", team.slug)
