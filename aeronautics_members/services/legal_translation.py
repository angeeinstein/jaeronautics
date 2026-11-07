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
``(i)`` and leaves others, so they would no longer match. A paragraph whose
sentence the next ones finish ("verpflichtet sich jedes Mitglied," / "i) sich
… zu verhalten;") goes together with them, as one text.

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
from .outbox import enqueue, register_handler


def source_hash(record):
    """Of what the English is made from: the German title, revision and text."""
    return hashlib.sha256("\n".join((record.title or "", record.revision or "", record.body or "")).encode()
                          ).hexdigest()


#: A paragraph that starts with its own number: ``<p>iii) …``, ``<p>(a) …``, ``<li>2) …``.
_NUMBERED = re.compile(r"^(\s*<(?:p|li)>)(\(?(?:[ivxlcdm]{1,6}|[a-z]|\d{1,3})\))[ \t]+", re.IGNORECASE)
_OPENING = re.compile(r"^\s*<(?:p|li)>")
#: One paragraph, as a block is; and the paragraphs of a translated run.
_PARAGRAPH = re.compile(r"\s*<p>(?:(?!</p>).)*</p>\s*", re.DOTALL)
_PARAGRAPHS = re.compile(r"<p>.*?</p>", re.DOTALL)


def _plain(block):
    return html.unescape(re.sub(r"<[^>]+>", "", block)).strip()


def _plan(german):
    """What is sent of a version, and how its page is put together again: its blocks with their
    numbers taken off, the numbers, and the groups of blocks sent as one text.

    A group is one block -- or a *run*: a paragraph ending in "," or ":" and the paragraphs after it
    that finish its sentence (starting with a small letter, after their number). Sent apart, each
    half was translated without the other ("undertakes to, / i) to behave …")."""
    blocks, numbers = [], []
    for block in legal.blocks(german):
        numbered = _NUMBERED.match(block)
        numbers.append(numbered.group(2) if numbered else None)
        blocks.append(numbered.group(1) + block[numbered.end():] if numbered else block)
    groups, index = [], 0
    while index < len(blocks):
        group = [index]
        if _PARAGRAPH.fullmatch(blocks[index]) and _plain(blocks[index]).endswith((",", ":")):
            following = index + 1
            while (following < len(blocks) and _PARAGRAPH.fullmatch(blocks[following])
                   and _plain(blocks[following])[:1].islower()):
                group.append(following)
                following += 1
        groups.append(group)
        index = group[-1] + 1
    return blocks, numbers, groups


def _sent(blocks, groups):
    return ["".join(blocks[index] for index in group) for group in groups]


def translate_version(german):
    """``(title, html)``: a German Version's title and page in English."""
    blocks, numbers, groups = _plan(german)
    english = [None] * len(blocks)
    apart = []  # the paragraphs of a run that did not come back as as many paragraphs
    for group, made in zip(groups, translation.translate(_sent(blocks, groups))):
        if len(group) == 1:
            english[group[0]] = made
            continue
        parts = _PARAGRAPHS.findall(made)
        if len(parts) == len(group):
            for index, part in zip(group, parts):
                english[index] = part + "\n"
        else:
            apart += group
    if apart:
        for index, made in zip(apart, translation.translate([blocks[index] for index in apart])):
            english[index] = made
    [title] = translation.translate([german.title], as_html=False)
    return title, "".join(_numbered(text, number) for text, number in zip(english, numbers))


def forget_kept(german):
    """The kept translations of a version's texts forgotten, so they are asked for again."""
    blocks, _numbers, groups = _plan(german)
    runs = [blocks[index] for group in groups if len(group) > 1 for index in group]
    return (translation.forget(_sent(blocks, groups) + runs)
            + translation.forget([german.title], as_html=False))


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
    title, page = translate_version(legal.record(record.id))
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


def _outstanding(record):
    """Its translation job not done yet: waiting, running, or given up."""
    return db.session.execute(db.select(ExternalWorkItem).where(
        ExternalWorkItem.dedupe_key == _dedupe_key(record),
        ExternalWorkItem.status.in_([ExternalWorkItem.STATUS_PENDING, ExternalWorkItem.STATUS_PROCESSING,
                                     ExternalWorkItem.STATUS_FAILED]))).scalar_one_or_none()


def english_state(record):
    """``(state, problem)`` of a published version's English, for Admin › Legal texts: "machine",
    "hand" (uploaded before the English was made by machine), "making" -- with what went wrong in
    the last try, if one failed -- "failed" (given up; a saved key or *Translate again* brings it
    back), or "none"."""
    item = _outstanding(record)
    if item is not None:
        if item.status == ExternalWorkItem.STATUS_FAILED:
            return "failed", item.last_error
        return "making", item.last_error if item.attempts else None
    english = english_of(record)
    if english is None:
        return "none", None
    return ("machine" if english.machine_translated else "hand"), None


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
    item = _outstanding(record)
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
