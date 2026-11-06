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
``(i)`` and leaves others, so they would no longer match.
"""

import hashlib
import re

from ..db_models import ExternalWorkItem, LegalTextVersion, db
from . import legal_texts as legal
from . import translation
from .clock import get_now_utc
from .outbox import enqueue, register_handler


def source_hash(record):
    """Of what the English is made from: the German title, revision and text."""
    return hashlib.sha256("\n".join((record.title or "", record.revision or "", record.body or "")).encode()
                          ).hexdigest()


#: A paragraph that starts with its own number: ``<p>iii) …``, ``<p>(a) …``, ``<li>2) …``.
_NUMBERED = re.compile(r"^(\s*<(?:p|li)>)(\(?(?:[ivxlcdm]{1,6}|[a-z]|\d{1,3})\))[ \t]+", re.IGNORECASE)
_OPENING = re.compile(r"^\s*<(?:p|li)>")


def translate_version(german):
    """``(title, html)``: a German Version's title and page in English."""
    blocks, numbers = [], []
    for block in legal.blocks(german):
        numbered = _NUMBERED.match(block)
        numbers.append(numbered.group(2) if numbered else None)
        blocks.append(numbered.group(1) + block[numbered.end():] if numbered else block)
    english_blocks = [_numbered(english, number)
                      for english, number in zip(translation.translate(blocks), numbers)]
    [title] = translation.translate([german.title], as_html=False)
    return title, "".join(english_blocks)


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
    title, html = translate_version(legal.record(record.id))
    if english is None:
        english = LegalTextVersion(document=record.document, team_slug=record.team_slug, language="en",
                                   translation_of=record, uploaded_at=get_now_utc(), uploaded_by=None,
                                   title="", body="")
        db.session.add(english)
    english.status, english.version, english.effective_from = record.status, record.version, record.effective_from
    english.approved_by, english.approved_on = record.approved_by, record.approved_on
    english.published_at, english.published_by = record.published_at, record.published_by
    english.title, english.revision, english.body = title, record.revision, html
    english.machine_translated, english.source_hash = True, wanted
    english.corrected_at = record.corrected_at
    db.session.flush()
    return english


def queue(record, reason):
    """Make the English of a published record soon: at once after the commit, retried if no service
    answers."""
    return enqueue(ExternalWorkItem.KIND_LEGAL_TRANSLATE, payload={"record": record.id},
                   dedupe_key=f"legal-translate-{record.id}", reason=reason)


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
