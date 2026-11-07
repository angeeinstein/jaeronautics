"""Legal texts, the admins' part: the texts in force, the teams' rules, the
versions waiting for their day, what is wrong with the files on this server --
a new text's PDF from uploaded files, made as it will be and not kept -- and
the versions uploaded in the portal: uploading a draft, publishing it,
correcting it while that is still allowed, deleting a draft
(docs/legal-texts-in-portal-plan.md).

The texts are Markdown, in legal/ in the repository or uploaded here
(services/legal_texts.py reads both, services/legal_uploads.py keeps the
uploaded ones); their PDFs are made and kept by legal_pdf.py. Drawn by
frontend/src/pages/admin/LegalTexts.tsx.
"""

import io
from datetime import date
from typing import Literal

from flask import current_app, g, send_file, url_for
from flask_login import current_user

from .. import legal_pdf
from ..db_models import LegalTextVersion, db
from ..permissions import Permission
from ..services import NotFoundError, ServiceError, ValidationError
from ..services import legal_texts as legal
from ..services import legal_translation, legal_uploads
from ..services import teams as teams_service
from ..services.audit import log_audit_event
from ._core import Model, UtcDateTime, endpoint

TAG = "Admin"
PERMISSIONS = [Permission.LEGAL_TEXTS]
#: Publishing and correcting: a permission of its own.
PUBLISHING = [Permission.LEGAL_TEXTS, Permission.LEGAL_PUBLISH]


EnglishState = Literal["machine", "hand", "making", "failed", "none"]


class InForce(Model):
    title: str
    #: The team whose rules these are; ``None`` for the association's own texts.
    team_name: str | None
    version: date
    revision: str | None
    effective_from: date
    #: The line under its title ("Gültig ab … · genehmigt vom Vorstand am …").
    validity: str
    page_url: str
    #: Made on first request: open it with ``?prepare=1`` first.
    pdf_url: str
    #: Uploaded in the portal (rather than a file in legal/): its id.
    record_id: int | None = None
    #: May still be corrected: uploaded here, published under 24 hours ago, accepted by nobody.
    correctable: bool = False
    #: Published here: its English (``legal_translation.english_state``). None: a file in legal/.
    english: EnglishState | None = None
    english_problem: str | None = None


class UpcomingVersionOut(Model):
    """A version not shown yet: a draft, or one whose day has not come."""

    title: str
    team_name: str | None
    version: date
    revision: str | None
    status: Literal["draft", "scheduled"]
    effective_from: date
    has_english: bool
    #: Marked ENTWURF or VORSCHAU on every page; open it with ``?prepare=1`` first.
    pdf_url: str
    #: Uploaded in the portal: its id, for publishing or deleting it. None: a file in legal/.
    record_id: int | None = None
    uploaded_at: UtcDateTime | None = None
    #: Who uploaded it; a team's lead for a team's rules.
    uploaded_by: str | None = None
    #: The approval written into the file, offered when publishing.
    approved_by: str | None = None
    approved_on: date | None = None
    #: Which text, for *Edit* on a draft file in legal/ (copied into the portal first).
    document: str = ""
    team: str | None = None
    #: Its Markdown file, to download: German, and the English one if there is one.
    markdown_url: str = ""
    #: Publishing emails the members unless unticked: the texts accepted at signup, a team's rules.
    tells_by_default: bool = False
    #: How many members that email would reach: all active ones, or the team's.
    members_to_tell: int = 0
    #: Published here for a later day: its English, as in ``InForce``. None: a draft, or a file.
    english: EnglishState | None = None
    english_problem: str | None = None


class PdfJob(Model):
    """One PDF that "Make all PDFs again" makes."""

    key: str
    title: str
    team_name: str | None
    version: date


class WritableText(Model):
    document: str
    team: str | None
    #: "Statuten", "Teamordnung · Rocket Team".
    name: str
    #: There is a version in force to start from.
    has_in_force: bool


class LegalOut(Model):
    in_force: list[InForce]
    #: Teams with no rules in force.
    teams_without_rules: list[str]
    waiting: list[UpcomingVersionOut]
    pdf_jobs: list[PdfJob]
    #: What is wrong with the files in legal/ on this server.
    problems: list[str]
    preview_max_kb: int
    template_url: str
    #: Whether this person may publish and correct (``legal.publish``), not only write.
    may_publish: bool = False
    #: Every text a new draft can be written of, for *Write a new version*.
    texts: list[WritableText] = []


def _team_names():
    return {team.slug: team.name for team in teams_service.all_teams(include_archived=True)}


def _members_to_tell(document, team):
    """Counted once per request and audience: all members, or one team's."""
    counted = g.setdefault("legal_members_to_tell", {})
    if team not in counted:
        counted[team] = legal_uploads.members_to_tell(document, team)
    return counted[team]


def _person(user):
    if user is None:
        return None
    member = getattr(user, "member", None)
    name = " ".join(part for part in (getattr(member, "first_name", None), getattr(member, "last_name", None))
                    if part)
    return name or user.email


def _english(record):
    """``english`` and ``english_problem`` of a version published here."""
    if record is None or record.status != "published":
        return {}
    state, problem = legal_translation.english_state(record)
    return {"english": state, "english_problem": problem}


def _correcting(version):
    """``record_id``, ``correctable`` and the English of a version in force."""
    if version.record_id is None:
        return {}
    record = db.session.get(LegalTextVersion, version.record_id)
    return {"record_id": version.record_id,
            "correctable": legal_uploads.why_not_correctable(record) is None, **_english(record)}


@endpoint("GET", "/admin/legal", response=LegalOut, permissions=PERMISSIONS, tag=TAG)
def admin_legal():
    """The texts in force, the versions waiting, the PDFs, and what is wrong with the files."""
    names = _team_names()
    in_force = [
        InForce(title=version.title, team_name=None, version=version.version, revision=version.revision,
                effective_from=version.effective_from, validity=legal.validity(version),
                page_url=url_for("public.legal_text", slug=text.slug),
                pdf_url=url_for("public.legal_text_pdf", slug=text.slug), **_correcting(version))
        for text, version in legal.available()
    ]
    without_rules = []
    for team in teams_service.all_teams(include_archived=False):
        rules = teams_service.team_rules(team)
        if rules is None:
            without_rules.append(team.name)
            continue
        version = rules.version
        in_force.append(InForce(
            title=version.title, team_name=team.name, version=version.version, revision=version.revision,
            effective_from=version.effective_from, validity=legal.validity(version),
            page_url=url_for("teams.team_rules_text", slug=team.slug),
            pdf_url=url_for("teams.team_rules_pdf", slug=team.slug), **_correcting(version)))

    waiting = []
    for slug, team in [(text.slug, None) for text in legal.LEGAL_TEXTS] + [
        (text.slug, team_slug) for team_slug in legal.teams_with_texts() for text in legal.TEAM_TEXTS
    ]:
        for version in legal.waiting(slug, team=team):
            record = db.session.get(LegalTextVersion, version.record_id) if version.record_id else None
            which = {"document": slug, "team": team, **({"record": version.record_id} if version.record_id
                                                         else {"version": version.version.isoformat()})}
            waiting.append(UpcomingVersionOut(
                markdown_url=url_for("admin.admin_legal_waiting_markdown", **which),
                title=version.title, team_name=names.get(team, team) if team else None,
                version=version.version, revision=version.revision,
                status="draft" if version.status == "draft" else "scheduled",
                effective_from=version.effective_from,
                has_english=legal.translation_any(version) is not None,
                pdf_url=url_for("admin.admin_legal_waiting_pdf", document=slug,
                                version=version.version.isoformat(), team=team, record=version.record_id),
                record_id=version.record_id,
                uploaded_at=record.uploaded_at if record else None,
                uploaded_by=_person(record.uploaded_by) if record else None,
                approved_by=version.approved_by, approved_on=version.approved_on,
                document=slug, team=team, tells_by_default=legal_uploads.tells_by_default(slug, team),
                members_to_tell=_members_to_tell(slug, team) if record and record.status == "draft" else 0,
                **_english(record)))

    orphans = [f"legal/teams/{slug}/: no team has this short name, so these rules are shown nowhere."
               for slug in legal.teams_with_texts() if slug not in names]
    texts = [WritableText(document=text.slug, team=None, name=text.title,
                          has_in_force=legal.current_version(text.slug) is not None) for text in legal.LEGAL_TEXTS]
    texts += [WritableText(document=legal.TEAM_RULES, team=team.slug, name=f"Teamordnung · {team.name}",
                           has_in_force=teams_service.team_rules(team) is not None)
              for team in teams_service.all_teams(include_archived=False)]
    return LegalOut(
        texts=texts,
        may_publish=current_user.can(Permission.LEGAL_PUBLISH),
        in_force=in_force,
        teams_without_rules=without_rules,
        waiting=waiting,
        pdf_jobs=[PdfJob(key=legal_pdf.job_key(version), title=version.title,
                         team_name=names.get(version.team, version.team) if version.team else None,
                         version=version.version)
                  for version, _team in legal_pdf.all_jobs()],
        problems=[*legal.problems(), *orphans],
        preview_max_kb=legal_pdf.PREVIEW_MAX_BYTES // 1024,
        template_url=url_for("admin.admin_legal_template"),
    )


@endpoint("POST", "/admin/legal/preview", permissions=PERMISSIONS, tag=TAG,
          uploads={"german": True}, produces="application/pdf")
def admin_legal_preview(files):
    """A new text as the PDF it will be, from its German Markdown file, checked as the build checks
    it and not kept. What keeps it from being laid out is a 400 with ``details.problems``."""
    try:
        data, problems, name = legal_pdf.preview(_upload(files))
    except Exception:  # noqa: BLE001 -- a text WeasyPrint cannot lay out, said as such
        current_app.logger.exception("Could not make a preview PDF")
        data, problems = None, ["The PDF could not be made. See the log for why."]
    if data is None:
        raise ValidationError("The text was not laid out.", code="legal_preview_invalid",
                              details={"problems": list(problems)})
    return send_file(io.BytesIO(data), mimetype="application/pdf", download_name=f"VORSCHAU_{name}")


# --- Making the PDFs again --------------------------------------------------------------


class ForgottenOut(Model):
    removed: int


@endpoint("POST", "/admin/legal/pdfs/forget", response=ForgottenOut, permissions=PERMISSIONS, tag=TAG)
def admin_legal_forget_pdfs():
    """Remove every kept PDF -- the first step of making them all again; each is
    then made by ``.../pdfs/make``, one request each."""
    removed = legal_pdf.forget_all()
    log_audit_event(category="system", event_type="legal_pdfs_remade", actor_user=current_user,
                    target_user=current_user, metadata={"pdfs": len(legal_pdf.all_jobs())})
    db.session.commit()
    return ForgottenOut(removed=removed)


class MakeIn(Model):
    #: A ``key`` from ``pdf_jobs``.
    key: str


class MadeOut(Model):
    size_kb: int


@endpoint("POST", "/admin/legal/pdfs/make", response=MadeOut, body=MakeIn, permissions=PERMISSIONS, tag=TAG)
def admin_legal_make_pdf(body):
    """Make one PDF again. What keeps it from being made is said (422 ``legal_pdf_failed``)."""
    for version, team in legal_pdf.all_jobs():
        if legal_pdf.job_key(version) != body.key:
            continue
        if isinstance(team, Exception):
            raise ServiceError(str(team), code="legal_pdf_failed", http_status=422)
        try:
            size = legal_pdf.remake(version, team)
        except Exception as exc:  # noqa: BLE001 -- said for its line; the others go on
            current_app.logger.exception("Could not make the PDF of %s", version.label)
            raise ServiceError(str(exc) or exc.__class__.__name__, code="legal_pdf_failed",
                               http_status=422) from exc
        return MadeOut(size_kb=max(1, size // 1024))
    raise NotFoundError("No longer in legal/.")


# --- Versions uploaded in the portal -----------------------------------------------------


class UploadedOut(Model):
    record_id: int


def _upload(files):
    """The German file uploaded, ``(data, filename)``: the English is made by machine
    (docs/legal-translation-plan.md)."""
    return files["german"].read(legal_pdf.PREVIEW_MAX_BYTES + 1), files["german"].filename


@endpoint("POST", "/admin/legal/drafts", response=UploadedOut, permissions=PERMISSIONS, tag=TAG,
          uploads={"german": True})
def admin_legal_upload(files):
    """Keep a new version as a draft: its German Markdown file (the English is made by machine once
    it is published). Checked as the preview checks it; what is wrong is a 400 with ``details.problems``."""
    record = legal_uploads.upload_draft(current_user, _upload(files))
    db.session.commit()
    return UploadedOut(record_id=record.id)


class PublishIn(Model):
    #: The day it applies from: today or later.
    effective_from: date
    #: Who approved it ("Vorstand", "Generalversammlung", ...); empty: no approval to name.
    approved_by: str | None = None
    approved_on: date | None = None
    #: Email the members it applies to; None: as the text's default (``tells_by_default``).
    tell: bool | None = None
    #: What changed, a few lines for that email (Markdown).
    summary: str | None = None


@endpoint("POST", "/admin/legal/drafts/<int:record_id>/publish", response=UploadedOut, body=PublishIn,
          permissions=PUBLISHING, tag=TAG)
def admin_legal_publish(record_id, body):
    """Publish a draft. Its day is today's; it applies from ``effective_from``. Not to be undone:
    a published version is only ever corrected, within 24 hours, while nobody has accepted it."""
    record = legal_uploads.publish(current_user, record_id, effective_from=body.effective_from,
                                   approved_by=body.approved_by, approved_on=body.approved_on,
                                   tell=body.tell, summary=body.summary)
    db.session.commit()
    return UploadedOut(record_id=record.id)


@endpoint("POST", "/admin/legal/versions/<int:record_id>/correct", response=UploadedOut, permissions=PUBLISHING,
          tag=TAG, uploads={"german": True})
def admin_legal_correct(record_id, files):
    """Replace a published version's text -- within 24 hours of publishing, while nobody has
    accepted it. The earlier wording is kept in the log."""
    record = legal_uploads.correct(current_user, record_id, _upload(files))
    db.session.commit()
    return UploadedOut(record_id=record.id)


class DraftDeletedOut(Model):
    deleted: bool


class VersionEnglishOut(Model):
    english: EnglishState
    english_problem: str | None = None


def _published(record_id):
    record = db.session.get(LegalTextVersion, record_id)
    if record is None or record.translation_of_id is not None:
        raise NotFoundError("No such version.")
    return record


@endpoint("POST", "/admin/legal/versions/<int:record_id>/english", response=VersionEnglishOut, permissions=PUBLISHING,
          tag=TAG)
def admin_legal_translate_again(record_id):
    """Make a published version's English again, from scratch, in the background."""
    record = _published(record_id)
    legal_translation.translate_again(current_user, record)
    db.session.commit()
    state, problem = legal_translation.english_state(record)
    return VersionEnglishOut(english=state, english_problem=problem)


@endpoint("DELETE", "/admin/legal/versions/<int:record_id>/english", response=VersionEnglishOut,
          permissions=PUBLISHING, tag=TAG)
def admin_legal_remove_english(record_id):
    """Take a published version's English away; its English page then says there is none."""
    record = _published(record_id)
    legal_translation.remove_english(current_user, record)
    db.session.commit()
    return VersionEnglishOut(english="none")


@endpoint("DELETE", "/admin/legal/drafts/<int:record_id>", response=DraftDeletedOut, permissions=PERMISSIONS, tag=TAG)
def admin_legal_delete_draft(record_id):
    """Delete a draft, with its translation. A published version is never deleted."""
    legal_uploads.delete_draft(current_user, record_id)
    db.session.commit()
    return DraftDeletedOut(deleted=True)
