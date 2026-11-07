"""The legal texts' editor (docs/legal-texts-in-portal-plan.md, "The editor").

A draft (``LegalTextVersion``) is written in the browser: its title and
revision as fields, its Markdown, German and -- optionally -- English. It
saves itself; each save names the count it started from (``edit_count``),
so a second person's save over it is refused rather than overwriting it.
When an editing session starts, the draft's state is kept
(``LegalTextSnapshot``): *Discard changes* and *History* go back to one, in
the browser, as an ordinary change that is saved again.

A draft is started from the version in force, from the template, or by
uploading (services/legal_uploads.py). A draft file in legal/ is copied into
the portal the first time it is edited; the file is no longer listed.

**The lock.** One person edits a draft at a time: whoever opens it first
holds it, and anyone else sees it read-only, told who is editing it and
since when. The holder's editor gives a sign of life every 30 seconds; when
it stops -- the tab closed, the connection lost, the laptop asleep -- the
draft is free again after ``LOCK_GONE_AFTER``, without anybody unlocking it.
After ``TAKE_OVER_AFTER`` without typing, the others may take it over. Saving,
correcting and publishing need it (the save check stays behind it).

Who: whoever may write legal texts (``Permission.LEGAL_TEXTS``) any draft; a
team's leads (``TeamPermission.RULES``) the drafts of their team's rules,
also one an admin started. Publishing and correcting need
``Permission.LEGAL_PUBLISH`` (services/legal_uploads.py).
"""

import json
import re
import tempfile
from datetime import timedelta

from .. import legal_pdf
from ..db_models import LegalTextSnapshot, LegalTextVersion, db
from ..permissions import Permission
from . import ConflictError, NotFoundError, PermissionError_, ValidationError
from . import legal_texts as legal
from . import legal_uploads
from .audit import log_audit_event
from .clock import get_membership_today, get_now_utc

#: The editor gives a sign of life every 30 seconds: two missed, and the draft is free.
LOCK_GONE_AFTER = timedelta(seconds=75)
#: Without typing this long, the holder may be taken over.
TAKE_OVER_AFTER = timedelta(minutes=10)

#: Earlier states kept per draft; the oldest go first.
SNAPSHOTS_KEPT = 20
TITLE_MAX = 200
REVISION_MAX = 100
STARTS = ("in_force", "template")


# --- Who may write what ------------------------------------------------------------------


def _team(slug):
    from . import teams as teams_service

    try:
        return teams_service.get_team(slug)
    except NotFoundError:
        return None


def may_write(user, document, team_slug):
    """Whether ``user`` may write drafts of this text: any, with the permission; a team's
    rules, as one of its leads."""
    if user.can(Permission.LEGAL_TEXTS):
        return True
    if team_slug is None or document != legal.TEAM_RULES:
        return False
    from . import teams as teams_service

    team = _team(team_slug)
    return team is not None and teams_service.can_in_team(user, team, teams_service.TeamPermission.RULES)


def _require_write(user, document, team_slug):
    if not may_write(user, document, team_slug):
        raise PermissionError_("You may not write this text.", code="legal_not_yours")


def editable(user, record_id):
    """The German record of a draft ``user`` may edit -- or of a published version a publisher may
    still correct; NotFoundError/PermissionError_/ConflictError otherwise."""
    record = db.session.get(LegalTextVersion, record_id)
    if record is None or record.translation_of_id is not None:
        raise NotFoundError("There is no such version.", code="no_version")
    _require_write(user, record.document, record.team_slug)
    if record.status != "draft":
        if not user.can(Permission.LEGAL_PUBLISH):
            raise PermissionError_("Only who may publish legal texts corrects one.", code="legal_not_publisher")
        reason = legal_uploads.why_not_correctable(record)
        if reason is not None:
            raise ConflictError(f"This version can no longer be corrected. {reason}", code="legal_not_correctable")
    return record


# --- What a draft holds ------------------------------------------------------------------


def _english(record):
    return next(iter(record.translations), None)


def content(record):
    """{"german": {"title", "revision", "body"}, "english": {...} or None}."""
    english = _english(record)
    return {
        "german": {"title": record.title, "revision": record.revision, "body": record.body},
        "english": None if english is None else {"title": english.title, "revision": english.revision,
                                                  "body": english.body},
    }


def _clean(part, role):
    title = str(part.get("title") or "").strip()
    revision = str(part.get("revision") or "").strip() or None
    body = str(part.get("body") or "")
    fields = {}
    if len(title) > TITLE_MAX:
        fields[f"{role}.title"] = f"At most {TITLE_MAX} characters."
    if revision and len(revision) > REVISION_MAX:
        fields[f"{role}.revision"] = f"At most {REVISION_MAX} characters."
    if len(body.encode("utf-8")) > legal_pdf.PREVIEW_MAX_BYTES:
        fields[f"{role}.body"] = "At most 1 MB."
    if fields:
        raise ValidationError("Too long.", details={"fields": fields})
    return {"title": title, "revision": revision, "body": body}


# --- Starting a draft --------------------------------------------------------------------


def _template_body(text):
    """The template's text without its front matter: the fields are the editor's."""
    match = legal.FRONT_MATTER.match(text)
    return text[match.end():].lstrip("\n") if match else text


def _known(document, team_slug):
    if team_slug is None:
        if document not in legal.BY_SLUG:
            raise ValidationError("There is no such text.", code="legal_unknown_text")
        return legal.BY_SLUG[document]
    if document not in legal.TEAM_BY_SLUG or _team(team_slug) is None:
        raise ValidationError("There is no such team's text.", code="legal_unknown_text")
    return legal.TEAM_BY_SLUG[document]


def _new_record(actor, document, team_slug, german, english, imported_from=(None, None)):
    today, now = get_membership_today(), get_now_utc()
    record = LegalTextVersion(
        document=document, team_slug=team_slug, language=legal.AUTHORITATIVE, status="draft", version=today,
        effective_from=today, title=german["title"], revision=german["revision"], body=german["body"],
        uploaded_at=now, uploaded_by=actor, imported_from=imported_from[0])
    db.session.add(record)
    if english is not None:
        db.session.add(LegalTextVersion(
            document=document, team_slug=team_slug, language="en", translation_of=record, status="draft",
            version=today, effective_from=today, title=english["title"], revision=english["revision"],
            body=english["body"], uploaded_at=now, uploaded_by=actor, imported_from=imported_from[1]))
    db.session.flush()
    return record


def start_draft(actor, document, team_slug=None, start="in_force", template=""):
    """A new draft of a text: a copy of the version in force (the usual case: a change to it),
    or ``template`` -- the template file's text, rendered by the caller. Returns its German record."""
    _require_write(actor, document, team_slug)
    text = _known(document, team_slug)
    if start not in STARTS:
        raise ValidationError("Start from the version in force or the template.", code="legal_start")
    in_force = legal.current_version(document, team=team_slug) if start == "in_force" else None
    if in_force is not None:
        german = {"title": in_force.title, "revision": in_force.revision, "body": legal.body(in_force)}
        translated = legal.translation(in_force)
        english = None if translated is None else {
            "title": translated.title, "revision": translated.revision, "body": legal.body(translated)}
    else:
        german = {"title": text.title, "revision": None,
                  "body": _template_body(template)}
        english = None
    record = _new_record(actor, document, team_slug, german, english)
    log_audit_event("legal", "legal_text_started", actor_user=actor, target_user=actor,
                    metadata={"document": document, "team": team_slug, "record": record.id,
                              "from": "in_force" if in_force is not None else "template"})
    _tell_if_a_lead(actor, record)
    return record


def import_file(actor, document, team_slug, version_day):
    """The draft file in legal/ of that day copied into the portal, to be edited and published
    there -- or the copy made before. Returns its German record."""
    _require_write(actor, document, team_slug)
    _known(document, team_slug)
    path = None
    for candidate in legal._files(document, legal.AUTHORITATIVE, team_slug):
        version, _problems = legal._check(candidate, document, legal.AUTHORITATIVE, team_slug)
        if version is not None and version.version == version_day and version.status == "draft":
            path = candidate
            break
    if path is None:
        raise NotFoundError("There is no such draft in legal/.", code="no_version")
    where = legal.relative(path)
    copied = db.session.execute(db.select(LegalTextVersion).where(
        LegalTextVersion.imported_from == where, LegalTextVersion.translation_of_id.is_(None))).scalars().first()
    if copied is not None:
        return copied
    german_version, _ = legal._check(path, document, legal.AUTHORITATIVE, team_slug)
    german = {"title": german_version.title, "revision": german_version.revision, "body": legal.body(german_version)}
    english, english_where = None, None
    for candidate in legal._files(document, "en", team_slug):
        version, _problems = legal._check(candidate, document, "en", team_slug)
        if version is not None and version.version == version_day:
            english = {"title": version.title, "revision": version.revision, "body": legal.body(version)}
            english_where = legal.relative(candidate)
    record = _new_record(actor, document, team_slug, german, english, (where, english_where))
    record.approved_by, record.approved_on = german_version.approved_by, german_version.approved_on
    log_audit_event("legal", "legal_text_imported", actor_user=actor, target_user=actor,
                    metadata={"document": document, "team": team_slug, "record": record.id, "file": where})
    return record


def _tell_if_a_lead(actor, record):
    """A team's lead started new rules: whoever may publish them is told, as for an upload."""
    if record.team_slug is None or actor.can(Permission.LEGAL_TEXTS):
        return
    team = _team(record.team_slug)
    if team is not None:
        legal_uploads._tell_the_association(team, record)


# --- Editing -------------------------------------------------------------------------------


# --- The lock --------------------------------------------------------------------------------


def _now():
    """Now, as the lock's columns keep it: UTC without a zone, as the database stores any moment."""
    return get_now_utc().replace(tzinfo=None)


def _plain(moment):
    return moment.replace(tzinfo=None) if moment is not None and moment.tzinfo is not None else moment


def lock_state(record, user, now=None):
    """{"mine", "free", "holder" (a user, not this one), "since", "can_take_over"}."""
    now = now or _now()
    seen = _plain(record.lock_seen_at)
    if record.lock_user_id is None or seen is None or now - seen > LOCK_GONE_AFTER:
        return {"mine": False, "free": True, "holder": None, "since": None, "can_take_over": False}
    mine = record.lock_user_id == user.id
    typed = _plain(record.lock_typed_at or record.lock_since) or seen
    return {"mine": mine, "free": False, "holder": None if mine else record.lock_user,
            "since": record.lock_since, "can_take_over": not mine and now - typed >= TAKE_OVER_AFTER}


def held_by_another(record, user):
    """The person editing the draft now, when it is not ``user``; else None."""
    state = lock_state(record, user)
    return None if state["free"] or state["mine"] else (state["holder"] or True)


def take_lock(actor, record, *, take_over=False, typed=False, now=None):
    """Hold the draft: when it is free, the holder's editor has gone quiet, or ``actor`` holds it
    already -- with ``take_over``, also when the holder has not typed for ``TAKE_OVER_AFTER``.
    One statement, so two people taking it at once cannot both have it. True when ``actor`` holds it."""
    now = now or _now()
    column = LegalTextVersion
    gone = now - LOCK_GONE_AFTER
    takeable = db.or_(column.lock_user_id.is_(None), column.lock_user_id == actor.id,
                      column.lock_seen_at.is_(None), column.lock_seen_at < gone)
    if take_over:
        takeable = db.or_(takeable, db.func.coalesce(column.lock_typed_at, column.lock_since) < now - TAKE_OVER_AFTER)
    before = record.lock_user_id
    kept = db.and_(column.lock_user_id == actor.id, column.lock_seen_at >= gone)
    taken = db.session.execute(
        db.update(column).where(column.id == record.id, takeable)
        .values(lock_user_id=actor.id, lock_seen_at=now,
                lock_since=db.case((kept, column.lock_since), else_=now),
                lock_typed_at=now if typed else db.case((kept, column.lock_typed_at), else_=now))
        .execution_options(synchronize_session=False))
    db.session.refresh(record, ["lock_user_id", "lock_since", "lock_seen_at", "lock_typed_at"])
    if taken.rowcount == 1 and take_over and before not in (None, actor.id):
        log_audit_event("legal", "legal_draft_taken_over", actor_user=actor, target_user=actor,
                        metadata={"record": record.id, "from_user": before})
    return taken.rowcount == 1


def beat(actor, record, *, typed=False, now=None):
    """The holder's sign of life (and whether they typed since the last one). False: somebody took
    the draft over meanwhile."""
    now = now or _now()
    values = {"lock_seen_at": now, **({"lock_typed_at": now} if typed else {})}
    kept = db.session.execute(
        db.update(LegalTextVersion).where(LegalTextVersion.id == record.id,
                                          LegalTextVersion.lock_user_id == actor.id)
        .values(**values).execution_options(synchronize_session=False))
    db.session.refresh(record, ["lock_user_id", "lock_since", "lock_seen_at", "lock_typed_at"])
    return kept.rowcount == 1


def release(actor, record):
    """The holder leaves: the draft is free at once."""
    db.session.execute(
        db.update(LegalTextVersion).where(LegalTextVersion.id == record.id,
                                          LegalTextVersion.lock_user_id == actor.id)
        .values(lock_user_id=None, lock_since=None, lock_seen_at=None, lock_typed_at=None)
        .execution_options(synchronize_session=False))
    db.session.refresh(record, ["lock_user_id", "lock_since", "lock_seen_at", "lock_typed_at"])


def _require_lock(actor, record):
    if not take_lock(actor, record, typed=True):
        raise ConflictError("Somebody else is editing this draft now; what you changed is not saved.",
                            code="legal_draft_locked")


def open_session(actor, record):
    """An editing session starts: the draft's state is kept, unless the last kept one is the same.
    Returns that snapshot -- what *Discard changes* goes back to."""
    now_content = content(record)
    last = record.snapshots[-1] if record.snapshots else None
    if last is not None and json.dumps(last.content, sort_keys=True) == json.dumps(now_content, sort_keys=True):
        return last
    snapshot = LegalTextSnapshot(record=record, created_by=actor, content=now_content, created_at=get_now_utc())
    db.session.add(snapshot)
    for old in record.snapshots[:-SNAPSHOTS_KEPT]:
        db.session.delete(old)
    db.session.flush()
    return snapshot


def snapshot(record, snapshot_id):
    found = db.session.get(LegalTextSnapshot, snapshot_id)
    if found is None or found.record_id != record.id:
        raise NotFoundError("There is no such earlier state.", code="no_snapshot")
    return found


def save(actor, record, german, english, edit_count):
    """Save a draft as written. ``edit_count``: the count it was loaded with; another save in
    between refuses this one. ``english`` None: no translation (an existing one is removed)."""
    if record.status != "draft":
        raise ConflictError("A published version is corrected, not saved.", code="legal_published_already")
    german = _clean(german, "german")
    english = _clean(english, "english") if english is not None else None
    _require_lock(actor, record)
    now = get_now_utc()
    # Compared and counted up in one statement: of two saves at the same moment, only one
    # finds the count it names; the other is refused, never overwriting it.
    claimed = db.session.execute(
        db.update(LegalTextVersion)
        .where(LegalTextVersion.id == record.id, LegalTextVersion.edit_count == edit_count)
        .values(edit_count=edit_count + 1, edited_at=now)
        .execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        raise ConflictError("Somebody else saved this draft meanwhile. Reload it to see their changes.",
                            code="legal_draft_changed")
    db.session.refresh(record, ["edit_count", "edited_at"])
    record.title, record.revision, record.body = german["title"], german["revision"], german["body"]
    translation = _english(record)
    if english is None:
        if translation is not None:
            record.translations.remove(translation)
    else:
        if translation is None:
            translation = LegalTextVersion(
                document=record.document, team_slug=record.team_slug, language="en", translation_of=record,
                status="draft", version=record.version, effective_from=record.effective_from,
                uploaded_at=now, uploaded_by=actor, title="", body="")
            db.session.add(translation)
        translation.title, translation.revision, translation.body = (
            english["title"], english["revision"], english["body"])
        translation.edited_at = now
        translation.edit_count = (translation.edit_count or 0) + 1
    db.session.flush()
    return record


# --- Checking, as the preview and CI check a file -----------------------------------------


def as_file(record, language, part):
    """The file a text written in the editor would be: front matter from its fields, then its body."""
    lines = ["---", f"title: {json.dumps(part.get('title') or '')}", f'document: "{record.document}"',
             f'language: "{language}"']
    if record.team_slug:
        lines.append(f'team: "{record.team_slug}"')
    lines += [f'version: "{record.version.isoformat()}"', f'effective_from: "{record.effective_from.isoformat()}"',
              'status: "draft"']
    if part.get("revision"):
        lines.append(f"source_revision: {json.dumps(part['revision'])}")
    if record.approved_by:
        lines.append(f"approved_by: {json.dumps(record.approved_by)}")
    if record.approved_on:
        lines.append(f'approved_on: "{record.approved_on.isoformat()}"')
    return ("\n".join(lines) + "\n---\n\n" + (part.get("body") or "")).encode("utf-8")


def read_file(actor, record, german_upload, english_upload=None):
    """*Replace with a file* in the editor: uploaded files, checked as an upload is, read into
    {"german": part, "english": part or None} -- None: the translation stays as it is. Must be the
    same text. The draft's state before is kept as an earlier state; the editor puts the text in as a
    change undo takes back, and saves it as any other."""
    versions, bodies, problems = legal_uploads._checked(german_upload, english_upload)
    if problems or not versions:
        legal_uploads._invalid(problems or ["The German file is missing."], action="taken in")
    if (versions[0].slug, versions[0].team) != (record.document, record.team_slug):
        legal_uploads._invalid(["The file is a different text: the front matter must name the same document and "
                                "team."], action="taken in")
    open_session(actor, record)
    parts = [{"title": v.title, "revision": v.revision, "body": text} for v, text in zip(versions, bodies)]
    return {"german": parts[0], "english": parts[1] if len(parts) == 2 else None}


def markdown_file(german, language="de"):
    """``(data, filename)`` of a version's Markdown file -- German, or its English translation --
    front matter included, as an upload takes it back; None when there is none. A file in legal/ as
    it is; one kept in the portal as its fields and text make it."""
    version = german if language == legal.AUTHORITATIVE else legal.translation_any(german)
    if version is None:
        return None
    name = "-".join(part for part in (german.team, german.slug, language, german.version.isoformat()) if part) + ".md"
    if version.path is not None:
        return version.path.read_bytes(), name
    record = db.session.get(LegalTextVersion, german.record_id)
    part = content(record)["german" if language == legal.AUTHORITATIVE else "english"]
    return as_file(record, language, part), name


_ROLE = re.compile(r"^(German|English): ")


def check(record, language, part):
    """``(html, contents, problems)`` of one language as written, rendered as its page will be."""
    part = _clean(part, "german" if language == legal.AUTHORITATIVE else "english")
    problems = []
    if not part["body"].strip():
        problems.append("The text is empty.")
    with tempfile.TemporaryDirectory() as folder:
        role = "German" if language == legal.AUTHORITATIVE else "English"
        path, slug, _found_language, team, found = legal_pdf._uploaded(
            folder, as_file(record, language, part), "draft.md", role)
        problems += found
        html, contents = "", []
        if path is not None:
            version, found = legal._check(path, slug, language, team)
            problems += found
            if version is not None:
                rendered = legal.render(version)
                html, contents = str(rendered["html"]), rendered["contents"]
            legal.forget([path])
    return html, contents, list(dict.fromkeys(_ROLE.sub("", problem) for problem in problems))


def problems_of(record):
    """What keeps a draft from being published: its texts as written, checked."""
    found = []
    for language, part in (("de", content(record)["german"]), ("en", content(record)["english"])):
        if part is None:
            continue
        _html, _contents, problems = check(record, language, part)
        label = "German" if language == "de" else "English"
        found += [f"{label}: {problem}" for problem in problems]
    return found


def correct(actor, record, german, english):
    """A published version corrected in the editor: as an upload would correct it."""
    german = _clean(german, "german")
    english = _clean(english, "english") if english is not None else None
    _require_lock(actor, record)
    files = [(as_file(record, "de", german), "draft.md")]
    if english is not None:
        files.append((as_file(record, "en", english), "draft.md"))
    # The translation removed in the editor: removed, not kept as an upload without one would.
    return legal_uploads.correct(actor, record.id, *files, remove_english=english is None)
