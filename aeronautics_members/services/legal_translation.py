"""The English of the legal texts, made by machine from the German
(docs/legal-translation-plan.md; the translating itself: services/translation.py).

Only the German is written. A published version's English is made when it
is published -- outbox work, started at once and retried while no translation
service answers -- and kept with that version, fixed: an English row of
``legal_text_versions`` marked ``machine_translated``, its body the
translated page as HTML. A correction makes it anew. Until it exists, the
English page says there is no English of this version yet.

The page is translated block by block (``legal_texts.blocks``), each kept by
its exact text, so a version that differs from the last in one paragraph
costs that paragraph. A paragraph's own number -- ``i)``, ``a)``, ``3)`` --
is taken off before and put back after: a translator writes some of them as
``(i)`` and leaves others, so they would no longer match. Every block is
sent knowing the whole German text, as context (DeepL; Azure takes none): a
paragraph whose sentence the next ones finish ("verpflichtet sich jedes
Mitglied," / "i) sich … zu verhalten;"), a heading of one word. Which
services made the English, and why the first did not, is kept with it.

In Admin › Legal texts a version's English can be made again from scratch
(``translate_again``) or taken away (``remove_english``).
"""

import hashlib
import html
import re

from ..db_models import ExternalWorkItem, LegalTextVersion, db
from . import legal_texts as legal
from . import ConflictError, translation
from .audit import log_audit_event
from .clock import get_now_utc
from .locking import locked
from .outbox import enqueue, register_handler


def source_hash(record):
    """Of what the English is made from: the German title, revision and text."""
    return hashlib.sha256("\n".join((record.title or "", record.revision or "", record.body or "")).encode()
                          ).hexdigest()


#: A paragraph that starts with its own number: ``<p>iii) …``, ``<p>(a) …``, ``<li>2) …``.
_NUMBERED = re.compile(r"^(\s*<(?:p|li)>)(\(?(?:[ivxlcdm]{1,6}|[a-z]|\d{1,3})\))[ \t]+", re.IGNORECASE)
_OPENING = re.compile(r"^\s*<(?:p|li)>")


def _plain(block):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", block)).split())


def _plan(german):
    """What is sent of a version: its blocks with their numbers taken off, the numbers, and the whole
    German text as plain text -- the context each block is translated in.

    Each block is still translated on its own, and kept by its own text: sent as one text, DeepL
    ran the paragraphs into each other and lost the glossary and the gender-neutral wording (tried
    on 2026-11-17). Told only of its neighbours, a list item was translated well, but a heading of
    one word not ("Beschwerderecht" -- of which right?)."""
    blocks, numbers, whole = [], [], [german.title]
    for block in legal.blocks(german):
        whole.append(_plain(block))
        numbered = _NUMBERED.match(block)
        numbers.append(numbered.group(2) if numbered else None)
        blocks.append(numbered.group(1) + block[numbered.end():] if numbered else block)
    return blocks, numbers, "\n".join(line for line in whole if line)


def _heading(title):
    """The title, sent as a heading with the text: one request less -- in the editor a second one
    for the title alone found the time used up, and the next service wrote "team rules …"."""
    return f"<h1>{html.escape(title, quote=False)}</h1>"


def translate_version(german, *, patient=False, report=None):
    """``(title, html)``: a German Version's title and page in English. ``patient`` and ``report``
    as for ``translation.translate``."""
    blocks, numbers, context = _plan(german)
    title, *english = translation.translate([_heading(german.title), *blocks], context=context,
                                            patient=patient, report=report)
    title = " ".join(html.unescape(re.sub(r"<[^>]+>", "", title)).split())
    return title, "".join(_numbered(text, number) for text, number in zip(english, numbers))


def forget_kept(german):
    """The kept translations of a version's texts forgotten, so they are asked for again."""
    blocks, _numbers, _context = _plan(german)
    return translation.forget([_heading(german.title), *blocks])


def made_by(report):
    """``(translated_by, note)`` of a ``report``: the services, comma-separated in the order of
    SERVICES, and the failures, joined."""
    services = [name for name in translation.SERVICES if name in report.get("services", ())]
    note = "; ".join(dict.fromkeys(report.get("failures", [])))
    return ",".join(services) or None, (note[:497] + "...") if len(note) > 500 else (note or None)


def _numbered(english, number):
    if not number:
        return english
    opening = _OPENING.match(english)
    return f"{opening.group(0)}{number} {english[opening.end():]}" if opening else f"{number} {english}"


def english_of(record):
    """The English row of a German record, if there is one."""
    return next((row for row in record.translations if row.language == "en"), None)


def make_english(record):
    """A published German record's English, made -- or left as it is when it is of this German
    already. TranslationUnavailable when no service translates it."""
    english = english_of(record)
    wanted = source_hash(record)
    if english is not None and english.machine_translated and english.source_hash == wanted:
        return english
    report = {}
    title, page = translate_version(legal.record(record.id), patient=True, report=report)
    if english is not None:
        # Made again: the page's render cache follows the count (legal_texts._from_record).
        english.edit_count = (english.edit_count or 0) + 1
    else:
        english = LegalTextVersion(document=record.document, team_slug=record.team_slug, language="en",
                                   translation_of=record, uploaded_at=get_now_utc(), uploaded_by=None,
                                   title="", body="")
        db.session.add(english)
    english.status, english.version, english.effective_from = record.status, record.version, record.effective_from
    english.approved_by, english.approved_on = record.approved_by, record.approved_on
    english.published_at, english.published_by = record.published_at, record.published_by
    english.title, english.revision, english.body = title, record.revision, page
    english.machine_translated, english.source_hash = True, wanted
    english.translated_by, english.translation_note = made_by(report)
    english.corrected_at = record.corrected_at
    db.session.flush()
    return english


def wake_waiting():
    """A service set up or changed (Settings › Translation): every English still waiting -- retried
    later, or given up after its last try -- is made now, with fresh tries. Returns how many."""
    from . import outbox

    items = db.session.execute(db.select(ExternalWorkItem).where(
        ExternalWorkItem.kind == ExternalWorkItem.KIND_LEGAL_TRANSLATE,
        ExternalWorkItem.status.in_([ExternalWorkItem.STATUS_PENDING, ExternalWorkItem.STATUS_FAILED]))).scalars().all()
    for item in items:
        outbox.revive(item)
    if items:
        outbox._start_after_commit()
    return len(items)


def queue(record, reason):
    """Make the English of a published record soon: at once after the commit, retried if no service
    answers."""
    return enqueue(ExternalWorkItem.KIND_LEGAL_TRANSLATE, payload={"record": record.id},
                   dedupe_key=_dedupe_key(record), reason=reason)


def _dedupe_key(record):
    return f"legal-translate-{record.id}"


def _outstanding(record, lock=False):
    """Its translation job not done yet: waiting, running, or given up. ``lock``: held until the
    commit, so the worker cannot claim it meanwhile (it claims with an UPDATE of the same row)."""
    query = db.select(ExternalWorkItem).where(
        ExternalWorkItem.dedupe_key == _dedupe_key(record),
        ExternalWorkItem.status.in_([ExternalWorkItem.STATUS_PENDING, ExternalWorkItem.STATUS_PROCESSING,
                                     ExternalWorkItem.STATUS_FAILED]))
    return db.session.execute(locked(query) if lock else query).scalar_one_or_none()


def english_state(record):
    """``(state, problem, shown)`` of a published version's English, for Admin › Legal texts.

    ``state``: "machine", "hand" (uploaded before the English was made by machine), "making" --
    with what went wrong in the last try, if one failed -- "failed" (given up; a saved key or
    *Translate again* brings it back), or "none". ``shown``: whether an English is there and shown
    now -- while being made again, or after that gave up, it is the one from before."""
    english = english_of(record)
    shown = english is not None
    item = _outstanding(record)
    if item is not None:
        if item.status == ExternalWorkItem.STATUS_FAILED:
            return "failed", item.last_error, shown
        return "making", item.last_error if item.attempts else None, shown
    if english is None:
        return "none", None, False
    return ("machine" if english.machine_translated else "hand"), None, True


def made_by_names(record):
    """``(by, note)`` of a published version's English made by machine: "DeepL", "DeepL and Azure AI
    Translator", and why the first service in the order did not translate it all."""
    english = english_of(record)
    if english is None or not english.machine_translated or not english.translated_by:
        return None, None
    return names(english.translated_by.split(",")), english.translation_note


def names(services):
    return " and ".join(translation.NAMES[name] for name in services if name in translation.NAMES) or None


def _published_german(record):
    if record.translation_of_id is not None or record.status != "published":
        raise ConflictError("Only a published German version has an English made of it.",
                            code="legal_not_published")


def translate_again(actor, record):
    """Its English made anew, from scratch: nothing kept is used. In the background, at once."""
    _published_german(record)
    forgotten = forget_kept(legal.record(record.id))
    english = english_of(record)
    if english is not None:
        english.source_hash = None  # so the job does not take it for done
    queue(record, "translated again")
    log_audit_event("legal", "legal_english_translated_again", actor_user=actor, target_user=actor,
                    metadata={"record": record.id, "document": record.document, "team": record.team_slug,
                              "version": record.version.isoformat(), "kept_forgotten": forgotten})


def remove_english(actor, record):
    """Its English taken away: the English page then says there is none. Not made again by itself --
    only by *Translate again* or a correction."""
    _published_german(record)
    # Locked: a job the worker has not claimed yet is cancelled here, never claimed in between.
    item = _outstanding(record, lock=True)
    if item is not None and item.status == ExternalWorkItem.STATUS_PROCESSING:
        raise ConflictError("The English is being made just now. Remove it once it is done.",
                            code="legal_english_busy")
    if item is not None:
        db.session.delete(item)
    english = english_of(record)
    drop_english(record)
    log_audit_event("legal", "legal_english_removed", actor_user=actor, target_user=actor,
                    metadata={"record": record.id, "document": record.document, "team": record.team_slug,
                              "version": record.version.isoformat()},
                    before={"title": english.title, "body": english.body} if english is not None else None)


def drop_english(record):
    """Its English goes: hand-made drafts (the German is what is written now), or a translation of
    a German that has just changed."""
    for row in [row for row in record.translations if row.language == "en"]:
        record.translations.remove(row)


def _handle(item):
    record = db.session.get(LegalTextVersion, (item.payload or {}).get("record"))
    if record is None or record.translation_of_id is not None or record.status != "published":
        return  # deleted, or never published: nothing to translate
    make_english(record)


register_handler(ExternalWorkItem.KIND_LEGAL_TRANSLATE, _handle)
