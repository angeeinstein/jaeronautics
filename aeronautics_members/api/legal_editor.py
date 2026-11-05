"""The legal texts' editor (services/legal_editor.py,
docs/legal-texts-in-portal-plan.md, "The editor"): starting a draft --
from the version in force, the template, or a draft file in legal/ --,
loading it for an editing session, saving it, checking and rendering a
language as written, an earlier state, and correcting a published version
within its 24 hours.

Who may: checked per text here rather than by a site-wide permission --
whoever may write legal texts any; a team's leads their own team's rules.
Drawn by frontend/src/pages/legal/Editor.tsx, under Admin › Legal texts and
a team's Rules page.
"""

from datetime import date
from typing import Literal

from flask import render_template, url_for
from flask_login import current_user

from ..db_models import db
from ..permissions import Permission
from ..services import NotFoundError
from ..services import legal_editor as editor
from ..services import legal_texts as legal
from ..services import legal_uploads
from ..services.clock import get_membership_today
from ._core import Model, UtcDateTime, endpoint
from .admin_legal import _person
from .legal import SectionOut

TAG = "Legal"


class StartIn(Model):
    document: str
    #: A team's short name, for its rules.
    team: str | None = None
    start: Literal["in_force", "template"] = "in_force"


class ImportIn(Model):
    document: str
    team: str | None = None
    #: The day of the draft file in legal/.
    version: date


class StartedOut(Model):
    record_id: int
    #: The editor's page for it.
    editor_url: str


def _editor_url(record):
    if current_user.can(Permission.LEGAL_TEXTS) or record.team_slug is None:
        return f"/admin/legal/drafts/{record.id}"
    return f"/teams/{record.team_slug}/manage/rules/drafts/{record.id}"


@endpoint("POST", "/legal/drafts", response=StartedOut, body=StartIn, tag=TAG)
def legal_draft_start(body):
    """Start a new draft of a text: a copy of the version in force, or the template."""
    template = render_template("legal/template.md", day=get_membership_today().isoformat(),
                               team=editor._team(body.team) if body.team else None)
    record = editor.start_draft(current_user, body.document, body.team, body.start, template)
    db.session.commit()
    return StartedOut(record_id=record.id, editor_url=_editor_url(record))


@endpoint("POST", "/legal/imports", response=StartedOut, body=ImportIn, tag=TAG)
def legal_draft_import(body):
    """Copy a draft file in legal/ into the portal, to be edited and published there (or the
    copy made before). The file is no longer listed."""
    record = editor.import_file(current_user, body.document, body.team, body.version)
    db.session.commit()
    return StartedOut(record_id=record.id, editor_url=_editor_url(record))


class TextIn(Model):
    title: str = ""
    revision: str | None = None
    body: str = ""


class TextOut(Model):
    title: str
    revision: str | None
    body: str


class SnapshotOut(Model):
    id: int
    created_at: UtcDateTime
    created_by: str | None


class InForceTextOut(Model):
    """The version in force, for the comparison."""

    german: TextOut
    english: TextOut | None
    validity: str


class LockOut(Model):
    """Who edits the draft now: one person at a time; the others see it read-only."""

    #: This person holds it: they write, and their editor gives a sign of life every 30 seconds.
    mine: bool
    #: Nobody holds it: *Edit* takes it.
    free: bool
    #: Who does, when somebody else does.
    holder: str | None
    since: UtcDateTime | None
    #: The holder has not typed for 10 minutes: *Take over* is offered.
    can_take_over: bool


def _lock_out(record):
    state = editor.lock_state(record, current_user)
    return LockOut(mine=state["mine"], free=state["free"],
                   holder=_person(state["holder"]) if state["holder"] not in (None, True) else None,
                   since=state["since"], can_take_over=state["can_take_over"])


class EditorOut(Model):
    record_id: int
    document: str
    #: "Statuten", or "Teamordnung · Rocket Team".
    name: str
    team: str | None
    team_name: str | None
    #: draft: written and saved as it goes; correcting: a published version within its 24 hours.
    mode: Literal["draft", "correcting"]
    german: TextOut
    english: TextOut | None
    #: Named by the next save: another save in between refuses it.
    edit_count: int
    edited_at: UtcDateTime | None
    in_force: InForceTextOut | None
    #: The draft as it was when this session started: *Discard changes* goes back to it.
    opened_snapshot_id: int
    #: Earlier states, newest first.
    snapshots: list[SnapshotOut]
    #: Its PDF, as saved (``?prepare=1`` first).
    pdf_url: str
    #: Its Markdown files as saved, to download: German, and English when there is a translation.
    markdown_url: str
    english_markdown_url: str | None
    #: Where the list of texts is: Admin › Legal texts, or the team's Rules page.
    back_url: str
    may_publish: bool
    approved_by: str | None
    approved_on: date | None
    effective_from: date
    #: Who edits it now; not this person: read-only.
    lock: LockOut
    #: Publishing emails the members unless unticked, and how many it would reach.
    tells_by_default: bool = False
    members_to_tell: int = 0


def _text(part):
    return TextOut(title=part["title"], revision=part["revision"], body=part["body"]) if part else None


def _editor_out(record, opened):
    team = editor._team(record.team_slug) if record.team_slug else None
    text = (legal.TEAM_BY_SLUG if record.team_slug else legal.BY_SLUG).get(record.document)
    name = text.title if text else record.document
    if team is not None:
        name = f"{name} · {team.name}"
    in_force = legal.current_version(record.document, team=record.team_slug)
    in_force_out = None
    # Correcting: the version in force is this one, as published -- what the correction changes.
    if in_force is not None:
        translated = legal.translation(in_force)
        in_force_out = InForceTextOut(
            german=TextOut(title=in_force.title, revision=in_force.revision, body=legal.body(in_force)),
            english=None if translated is None else TextOut(
                title=translated.title, revision=translated.revision, body=legal.body(translated)),
            validity=legal.validity(in_force))
    content = editor.content(record)
    admin = current_user.can(Permission.LEGAL_TEXTS)
    if admin or team is None:
        pdf_url = url_for("admin.admin_legal_waiting_pdf", document=record.document, team=record.team_slug,
                          record=record.id)
        markdown_url = url_for("admin.admin_legal_waiting_markdown", document=record.document,
                               team=record.team_slug, record=record.id)
        back_url = "/admin/legal"
    else:
        pdf_url = url_for("teams.team_rules_waiting_pdf", slug=team.slug, record=record.id)
        markdown_url = url_for("teams.team_rules_waiting_markdown", slug=team.slug, record=record.id)
        back_url = f"/teams/{team.slug}/manage/rules"
    if record.status != "draft":
        pdf_url = (url_for("teams.team_rules_pdf", slug=record.team_slug) if record.team_slug
                   else url_for("public.legal_text_pdf", slug=record.document))
    return EditorOut(
        record_id=record.id, document=record.document, name=name, team=record.team_slug,
        team_name=team.name if team else None, mode="draft" if record.status == "draft" else "correcting",
        german=_text(content["german"]), english=_text(content["english"]), edit_count=record.edit_count,
        edited_at=record.edited_at, in_force=in_force_out, opened_snapshot_id=opened.id,
        snapshots=[SnapshotOut(id=s.id, created_at=s.created_at, created_by=_person(s.created_by))
                   for s in reversed(record.snapshots)],
        pdf_url=pdf_url, back_url=back_url, markdown_url=markdown_url,
        english_markdown_url=f"{markdown_url}&language=en" if content["english"] else None,
        may_publish=current_user.can(Permission.LEGAL_TEXTS) and current_user.can(Permission.LEGAL_PUBLISH),
        approved_by=record.approved_by, approved_on=record.approved_on, effective_from=record.effective_from,
        lock=_lock_out(record),
        tells_by_default=legal_uploads.tells_by_default(record.document, record.team_slug),
        members_to_tell=legal_uploads.members_to_tell(record.document, record.team_slug)
        if record.status == "draft" and current_user.can(Permission.LEGAL_PUBLISH) else 0)


@endpoint("POST", "/legal/drafts/<int:record_id>/session", response=EditorOut, tag=TAG)
def legal_draft_session(record_id):
    """Start an editing session: the draft as it is now, kept as an earlier state to go back to. When
    nobody else is editing it, this person holds it from now on; otherwise they see it read-only."""
    record = editor.editable(current_user, record_id)
    editor.take_lock(current_user, record)
    opened = editor.open_session(current_user, record)
    db.session.commit()
    return _editor_out(record, opened)


class SaveIn(Model):
    german: TextIn
    #: None: no English translation (one there is removed).
    english: TextIn | None = None
    #: ``edit_count`` as loaded or last saved.
    edit_count: int


class SavedOut(Model):
    edit_count: int
    edited_at: UtcDateTime | None


@endpoint("PUT", "/legal/drafts/<int:record_id>", response=SavedOut, body=SaveIn, tag=TAG)
def legal_draft_save(record_id, body):
    """Save a draft as written. Refused (409, ``legal_draft_changed``) when somebody else saved it
    since ``edit_count``."""
    record = editor.editable(current_user, record_id)
    editor.save(current_user, record, body.german.model_dump(),
                body.english.model_dump() if body.english is not None else None, body.edit_count)
    db.session.commit()
    return SavedOut(edit_count=record.edit_count, edited_at=record.edited_at)


class CheckIn(TextIn):
    language: Literal["de", "en"] = "de"


class CheckedOut(Model):
    #: As the page shows it.
    html: str
    contents: list[SectionOut]
    #: What CI would refuse; empty when it may be published.
    problems: list[str]


@endpoint("POST", "/legal/drafts/<int:record_id>/check", response=CheckedOut, body=CheckIn, tag=TAG)
def legal_draft_check(record_id, body):
    """One language as written -- saved or not -- rendered as its page will be, and checked."""
    record = editor.editable(current_user, record_id)
    html, contents, problems = editor.check(record, body.language, body.model_dump(exclude={"language"}))
    return CheckedOut(html=html, contents=[SectionOut(anchor=a, label=label, level=level)
                                           for a, label, level in contents], problems=problems)


class ContentOut(Model):
    german: TextOut
    english: TextOut | None


@endpoint("GET", "/legal/drafts/<int:record_id>/snapshots/<int:snapshot_id>", response=ContentOut, tag=TAG)
def legal_draft_snapshot(record_id, snapshot_id):
    """An earlier state of the draft, to go back to in the editor (then saved as any change)."""
    record = editor.editable(current_user, record_id)
    kept = editor.snapshot(record, snapshot_id).content
    return ContentOut(german=_text(kept["german"]), english=_text(kept.get("english")))


@endpoint("POST", "/legal/drafts/<int:record_id>/file", response=ContentOut, tag=TAG,
          uploads={"german": True, "english": False})
def legal_draft_read_file(record_id, files):
    """*Replace with a file*: the text of uploaded files -- checked as an upload is -- for the editor to put
    in; ``english`` None: the translation stays. The draft as it was is kept as an earlier state.
    What is wrong is a 400 with ``details.problems``."""
    from .admin_legal import _uploads

    record = editor.editable(current_user, record_id)
    read = editor.read_file(current_user, record, *_uploads(files))
    db.session.commit()
    return ContentOut(german=_text(read["german"]), english=_text(read["english"]))


class LockIn(Model):
    #: look: who has it; beat: the holder's sign of life; take: *Edit* once it is free;
    #: take_over: once the holder has not typed for 10 minutes.
    action: Literal["look", "beat", "take", "take_over"] = "look"
    #: With beat: typed since the last one.
    typed: bool = False


@endpoint("POST", "/legal/drafts/<int:record_id>/lock", response=LockOut, body=LockIn, tag=TAG)
def legal_draft_lock(record_id, body):
    """Who edits the draft now -- every 30 seconds from the editor: the holder's sign of life, or the
    others asking whether it is free. Taking it, the editor loads the draft again."""
    record = editor.editable(current_user, record_id)
    if body.action == "beat":
        editor.beat(current_user, record, typed=body.typed)
    elif body.action in ("take", "take_over"):
        editor.take_lock(current_user, record, take_over=body.action == "take_over")
    db.session.commit()
    return _lock_out(record)


@endpoint("DELETE", "/legal/drafts/<int:record_id>/lock", response=LockOut, tag=TAG)
def legal_draft_unlock(record_id):
    """The holder leaves the editor: the draft is free at once (else a minute later by itself)."""
    record = editor.editable(current_user, record_id)
    editor.release(current_user, record)
    db.session.commit()
    return _lock_out(record)


class CorrectIn(Model):
    german: TextIn
    english: TextIn | None = None


@endpoint("POST", "/legal/drafts/<int:record_id>/correct", response=SavedOut, body=CorrectIn, tag=TAG)
def legal_draft_correct(record_id, body):
    """Correct a published version as written in the editor: within 24 hours of publishing, while
    nobody has accepted it. The earlier wording goes into the log."""
    record = editor.editable(current_user, record_id)
    if record.status == "draft":
        raise NotFoundError("A draft is saved, not corrected.", code="no_version")
    editor.correct(current_user, record, body.german.model_dump(),
                   body.english.model_dump() if body.english is not None else None)
    db.session.commit()
    return SavedOut(edit_count=record.edit_count, edited_at=record.corrected_at)
