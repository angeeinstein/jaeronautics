"""Translation by machine, German to English (docs/legal-translation-plan.md):
DeepL and Azure AI Translator, either or both, one first and the other as
the fallback -- as the email accounts are set up (Settings › Translation).

Texts go as HTML: the translator keeps the tags (headings with their anchors,
links, lists, tables) and translates what is between them; what comes back is
cleaned (``clean_html``) to the tags that went in. Every translation is kept by
the exact text it was made from (``Translation``), so a text translated once is
never sent again -- a change to one paragraph sends that paragraph.

The **glossary** keeps terms right (Verein → association): DeepL takes it as a
glossary of its own, made anew when it changes; Azure, which has no glossary
for HTML, gets the English word in place, marked not to be translated, for the
German word as written.

**Context:** DeepL is told the whole German text with every request -- not
translated, not counted -- so a paragraph is translated knowing the text it
stands in (a heading, a list item finishing a sentence); and asked for its
better model, with the house style as instructions (gender-neutral English).
Azure takes neither. A paragraph is kept by its own text only: when another
paragraph changes, it is not translated again.

**Azure's free tier** refuses more than about 33,000 characters a minute: it
is sent smaller portions, and when publishing (``patient``) the portal paces
itself and waits as long as Azure says when it refuses. Whatever a service
cannot do goes to the next one, portion by portion.
"""

import hashlib
import html
import json
import re
import time
from collections import deque
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from flask import current_app

from ..db_models import Translation, db
from . import ValidationError
from .audit import log_audit_event
from .clock import get_now_utc
from .settings import get_settings_map, set_setting_value

DEEPL, AZURE = "deepl", "azure"
SERVICES = (DEEPL, AZURE)
NAMES = {DEEPL: "DeepL", AZURE: "Azure AI Translator"}

ORDER_KEY = "translation.order"
DEEPL_KEY = "translation.deepl_key"
AZURE_KEY = "translation.azure_key"
AZURE_REGION_KEY = "translation.azure_region"
GLOSSARY_KEY = "translation.glossary"
DEEPL_GLOSSARY_KEY = "translation.deepl_glossary"
KEYS = [ORDER_KEY, DEEPL_KEY, AZURE_KEY, AZURE_REGION_KEY, GLOSSARY_KEY, DEEPL_GLOSSARY_KEY]

AZURE_ENDPOINT = "https://api.cognitive.microsofttranslator.com"
#: British English at DeepL: the association is in Austria, and its texts read as European ones.
#: (Azure has one English, "en".)
TARGET = "EN-GB"
TIMEOUT_SECONDS = 30
#: DeepL's instructions with every request (``custom_instructions``: at most 10, 300 characters each):
#: the German says "sein Verhalten" of a member, the English "their conduct".
INSTRUCTIONS = [
    "This is a legal text of a student association in Austria; write formal British English.",
    "Use gender-neutral language: refer to a member or any person as 'they' or 'their', "
    "never 'he', 'his', 'she' or 'her'.",
]
#: DeepL's better model; asked for, as it is not every account's default.
MODEL = "quality_optimized"
#: Texts per request: DeepL takes 50 at a time.
BATCH = 50
#: Characters per request: a DeepL request is at most 128 KiB, the context included.
BATCH_CHARACTERS = 30_000
#: Of the German around the texts, what DeepL is told at most: a whole legal text, as a rule.
CONTEXT_MAX = 50_000
#: Azure's free tier: 2 million characters an hour, counted over the last minute -- about 33,000.
AZURE_BATCH_CHARACTERS = 10_000
AZURE_PER_MINUTE = 30_000
#: How long, in all, a translation in the background waits for a service that asks to wait.
WAIT_MAX_SECONDS = 240
GLOSSARY_MAX = 200
#: The start of the name of every glossary this portal makes at DeepL.
GLOSSARY_NAME = "jaeronautics legal texts"
TERM_MAX = 100

#: Offered on a fresh installation; changed and added to under Settings › Translation.
DEFAULT_GLOSSARY = [
    ["Verein", "association"],
    ["Vorstand", "board"],
    ["Generalversammlung", "general assembly"],
    ["Mitglied", "member"],
    ["Mitglieder", "members"],
    ["Mitgliedsbeitrag", "membership fee"],
    ["Statuten", "statutes"],
    ["Geschäftsordnung", "rules of procedure"],
    ["Teamordnung", "team rules"],
    ["Teamleitung", "team lead"],
    ["Mitgliederportal", "members' portal"],
]


class TranslationUnavailable(Exception):
    """No service translated it: none set up, or every one failed (the reasons, joined)."""


class _ServiceError(Exception):
    def __init__(self, message, *, code=None, retry_after=None):
        super().__init__(message)
        self.code = code
        #: Seconds the service asked to wait before the next try ("too many requests").
        self.retry_after = retry_after


#: Slept on when a service asks to wait; a name of its own, so the tests do not wait.
_sleep = time.sleep
_clock = time.monotonic


# --- Settings -------------------------------------------------------------------------------


def settings():
    saved = get_settings_map(KEYS)
    order = [name for name in (saved.get(ORDER_KEY) or "deepl,azure").split(",") if name in SERVICES]
    order += [name for name in SERVICES if name not in order]
    try:
        glossary = json.loads(saved[GLOSSARY_KEY]) if saved.get(GLOSSARY_KEY) else DEFAULT_GLOSSARY
    except ValueError:
        glossary = DEFAULT_GLOSSARY
    return {"order": order, "deepl_key": saved.get(DEEPL_KEY) or "", "azure_key": saved.get(AZURE_KEY) or "",
            "azure_region": saved.get(AZURE_REGION_KEY) or "", "glossary": glossary}


def configured(current=None):
    """The services set up, in the order they are tried."""
    current = current or settings()
    have = {DEEPL: bool(current["deepl_key"]), AZURE: bool(current["azure_key"])}
    return [name for name in current["order"] if have[name]]


def _clean_glossary(entries):
    clean, seen = [], set()
    for entry in entries or []:
        german, english = (" ".join(str(part or "").split()) for part in (list(entry) + ["", ""])[:2])
        if not german or not english:
            continue
        if len(german) > TERM_MAX or len(english) > TERM_MAX or "\t" in german + english:
            raise ValidationError("A term is too long.", details={"fields": {"glossary": f"At most {TERM_MAX} "
                                                                                          "characters a term."}})
        if german not in seen:
            seen.add(german)
            clean.append([german, english])
    if len(clean) > GLOSSARY_MAX:
        raise ValidationError("Too many terms.", details={"fields": {"glossary": f"At most {GLOSSARY_MAX}."}})
    return clean


def save_settings(actor, *, order, deepl_key=None, azure_key=None, azure_region="", glossary=None,
                  forget=()):
    """Save the services and the glossary. A key left empty keeps the one set; ``forget`` removes one."""
    order = [name for name in order if name in SERVICES]
    order += [name for name in SERVICES if name not in order]
    region = (azure_region or "").strip()
    if region and not re.fullmatch(r"[a-z0-9]{2,40}", region):
        raise ValidationError("Not a region.", details={"fields": {"azure_region": "Like westeurope."}})
    values = {ORDER_KEY: ",".join(order), AZURE_REGION_KEY: region}
    if glossary is not None:
        values[GLOSSARY_KEY] = json.dumps(_clean_glossary(glossary), ensure_ascii=False)
    for key, new in ((DEEPL_KEY, deepl_key), (AZURE_KEY, azure_key)):
        if new and new.strip():
            values[key] = new.strip()
    for name in forget:
        values[{DEEPL: DEEPL_KEY, AZURE: AZURE_KEY}[name]] = ""
    before = get_settings_map(KEYS)
    changed = [key.split(".", 1)[1] for key, value in values.items() if (before.get(key) or "") != value]
    for key, value in values.items():
        set_setting_value(key, value)
    if changed:
        secret = {DEEPL_KEY, AZURE_KEY}
        log_audit_event("system", "translation_settings_changed", actor_user=actor, target_user=actor,
                        metadata={"changed": changed},
                        after={key: ("set" if value else "") if key in secret else value
                               for key, value in values.items()})
    return changed


def _fingerprint(glossary):
    return hashlib.sha256(json.dumps(glossary, ensure_ascii=False).encode()).hexdigest()[:16]


# --- Translating -----------------------------------------------------------------------------


def _key(text, as_html, glossary):
    # By the text alone, not its context: a paragraph translated once is not translated again when
    # another one changes. "2": kept since the context became the whole text (2026-11-19) -- the
    # translations kept before were made knowing less, and are made anew once.
    return hashlib.sha256(f"2|{TARGET}|{int(as_html)}|{_fingerprint(glossary)}|{text}".encode()).hexdigest()


def translate(texts, *, as_html=True, context="", patient=False, report=None):
    """``texts`` in English, in their order: what was translated before from the store, the rest by
    the first service that manages it, portion by portion. TranslationUnavailable when none does.

    ``context``: the German the texts stand in, as plain text -- told to DeepL, not translated, not
    counted. ``patient``: in the background (publishing), so a service that asks to wait is
    waited for, a few minutes in all; in a page's request it goes to the next service at once.
    ``report``, a dict, gets ``services`` (a set: who translated these texts, now or before) and
    ``failures`` ("DeepL: …" of a service that failed while another stepped in)."""
    current = settings()
    keys = [_key(text, as_html, current["glossary"]) for text in texts]
    rows = {row.source_hash: row for row in db.session.execute(
        db.select(Translation).where(Translation.source_hash.in_(set(keys)))).scalars()} if keys else {}
    known = {key: row.target for key, row in rows.items()}
    services = {row.service for row in rows.values() if row.service}
    missing = list(dict.fromkeys(text for text, key in zip(texts, keys) if key not in known and text.strip()))
    failures = []
    if missing:
        made, failures = _by_a_service(missing, as_html, current, context=context, patient=patient)
        now = get_now_utc()
        for text, (english, service) in zip(missing, made):
            key = _key(text, as_html, current["glossary"])
            known[key] = english
            services.add(service)
            if db.session.get(Translation, key) is None:
                db.session.add(Translation(source_hash=key, source=text, target=english, service=service,
                                           created_at=now))
        db.session.flush()
    if report is not None:
        report.setdefault("services", set()).update(services)
        report.setdefault("failures", []).extend(failures)
    return [known.get(key, text) for text, key in zip(texts, keys)]


def forget(texts, *, as_html=True):
    """The kept translations of ``texts`` forgotten: asked for again next time. Returns how many."""
    glossary = settings()["glossary"]
    keys = list({_key(text, as_html, glossary) for text in texts})
    if not keys:
        return 0
    return db.session.execute(db.delete(Translation).where(Translation.source_hash.in_(keys))).rowcount


def _batches(texts, name):
    """``texts`` in portions a request of this service takes: at most BATCH texts, and so many
    characters -- fewer for Azure's free tier."""
    most = AZURE_BATCH_CHARACTERS if name == AZURE else BATCH_CHARACTERS
    batch, size = [], 0
    for text in texts:
        if batch and (len(batch) == BATCH or size + len(text) > most):
            yield batch
            batch, size = [], 0
        batch.append(text)
        size += len(text)
    if batch:
        yield batch


class _Patience:
    """How long a translation may still wait, and what was sent to Azure in the last minute."""

    def __init__(self, patient):
        self.left = WAIT_MAX_SECONDS if patient else 0
        self.sent = deque()  # (when, characters)

    def wait(self, seconds):
        seconds = max(1, min(seconds, 120))
        if seconds > self.left:
            return False
        self.left -= seconds
        _sleep(seconds)
        return True

    def pace(self, characters):
        """Before sending ``characters`` to Azure: waits while the last minute's would go over its limit."""
        while self.left:
            now = _clock()
            while self.sent and now - self.sent[0][0] >= 60:
                self.sent.popleft()
            if not self.sent or sum(count for _when, count in self.sent) + characters <= AZURE_PER_MINUTE:
                break
            if not self.wait(int(60 - (now - self.sent[0][0])) + 1):
                break
        self.sent.append((_clock(), characters))


def _by_a_service(texts, as_html, current, only=None, context="", patient=False):
    """``([(english, service), …], failures)``: each portion by the first service that translates it."""
    services = [only] if only else configured(current)
    if not services:
        raise TranslationUnavailable("No translation service is set up (Settings › Translation).")
    made, left, reasons, patience = [], list(texts), [], _Patience(patient)
    for name in services:
        try:
            for batch in _batches(left, name):
                english = (_deepl(batch, as_html, current, context, patience) if name == DEEPL
                           else _azure(batch, as_html, current, patience))  # Azure takes no context
                made += [(clean_html(text) if as_html else text, name) for text in english]
                left = left[len(batch):]
            return made, reasons
        except _ServiceError as exc:
            current_app.logger.warning("Translating with %s failed: %s", NAMES[name], exc)
            reasons.append(f"{NAMES[name]}: {exc}")
    raise TranslationUnavailable("; ".join(reasons))


def _patiently(patience, send):
    """``send()``, tried again while the service asks to wait ("too many requests") and there is time."""
    while True:
        try:
            return send()
        except _ServiceError as exc:
            if exc.retry_after is None or not patience.wait(exc.retry_after):
                raise


def try_service(name):
    """*Try it* on the settings page: one sentence, through this service only, nothing kept."""
    if name not in SERVICES:
        raise ValidationError("No such service.")
    current = settings()
    try:
        [(english, _service)], _failures = _by_a_service(
            ["Der Vorstand beschließt über die Aufnahme neuer Mitglieder."], False, current, only=name)
    except TranslationUnavailable as exc:
        raise ValidationError(str(exc), code="translation_failed") from exc
    return english


def _call(url, *, data=None, headers=None, method="POST"):
    # ensure_ascii off: an umlaut is two bytes, not six -- a DeepL request, context included, is
    # at most 128 KiB.
    body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
    request = Request(url, data=body, method=method,
                      headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "jaeronautics",
                               **(headers or {})})
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 -- fixed https hosts
            body = response.read()
            return json.loads(body) if body else None
    except HTTPError as exc:
        raise _ServiceError(_said(exc), code=exc.code, retry_after=_retry_after(exc)) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise _ServiceError(f"not reached ({exc})") from exc
    except ValueError as exc:
        raise _ServiceError("an answer that is not JSON") from exc


def _said(exc):
    """What went wrong, in a few words, and the service's own message: a 456 at DeepL is any of
    several limits (characters, glossaries, a key's own limit), and only the message says which."""
    reason = {401: "the key is not accepted", 403: "the key is not accepted", 429: "too many requests just now",
              456: "a limit of the account is reached"}.get(exc.code, f"error {exc.code}")
    detail = _their_message(exc)
    return f"{reason} ({detail})" if detail else reason


def _retry_after(exc):
    """Seconds to wait before trying again, when the service said "too many requests" (429): its
    ``Retry-After``, or a minute -- Azure counts the characters of the last minute."""
    if exc.code != 429:
        return None
    try:
        return int(float((exc.headers or {}).get("Retry-After") or 60))
    except (TypeError, ValueError):
        return 60


def _their_message(exc):
    try:
        body = json.loads(exc.read() or b"{}")
    except (ValueError, OSError):
        return ""
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        body = body["error"]  # Azure's shape
    if not isinstance(body, dict):
        return ""
    said = " ".join(" ".join(str(body.get(part) or "").split()) for part in ("message", "detail")).strip()
    return said[:200]


# --- DeepL ---------------------------------------------------------------------------------


def _deepl_base(key):
    # A Free key ends in ":fx" and has its own address.
    return "https://api-free.deepl.com" if key.endswith(":fx") else "https://api.deepl.com"


def _deepl_headers(key):
    return {"Authorization": f"DeepL-Auth-Key {key}"}


def _glossary_prefix():
    """This installation's glossaries at DeepL: the portal's address in their name, so a test server
    with the same key leaves the live server's alone."""
    site = urlparse(current_app.config.get("PUBLIC_BASE_URL") or "").hostname or "portal"
    return f"{GLOSSARY_NAME} {site}"


#: A glossary made before the address was in the name (the first days on the test server).
_UNNAMED_GLOSSARY = re.compile(rf"{re.escape(GLOSSARY_NAME)}( [0-9a-f]{{16}})?")


def _deepl_glossary(current, fresh=False):
    """DeepL's glossary id for the glossary as it is now: made anew when it changed.

    The id is kept in a setting, but DeepL is also asked which glossaries it holds: one of this
    portal's made for the same terms is used again, the others are deleted. So none is left over
    when the setting was not saved -- DeepL takes only a few glossaries on a Free account."""
    if not current["glossary"]:
        return None
    key, fingerprint = current["deepl_key"], _fingerprint(current["glossary"])
    key_tag = hashlib.sha256(key.encode()).hexdigest()[:8]
    try:
        kept = json.loads(get_settings_map([DEEPL_GLOSSARY_KEY]).get(DEEPL_GLOSSARY_KEY) or "{}")
    except ValueError:
        kept = {}
    if not fresh and kept.get("fingerprint") == fingerprint and kept.get("key") == key_tag and kept.get("id"):
        return kept["id"]
    ours = _glossary_prefix()
    base, headers, wanted = _deepl_base(key), _deepl_headers(key), f"{ours} {fingerprint}"
    glossary_id = None
    try:
        held = (_call(f"{base}/v2/glossaries", headers=headers, method="GET") or {}).get("glossaries") or []
        for glossary in held:
            name = str(glossary.get("name") or "")
            if not (name.startswith(f"{ours} ") or _UNNAMED_GLOSSARY.fullmatch(name)):
                continue  # another installation's (the test server's, with the same key), or not this portal's
            if name == wanted and glossary_id is None:
                glossary_id = glossary.get("glossary_id")
                continue
            try:
                _call(f"{base}/v2/glossaries/{glossary.get('glossary_id')}", headers=headers, method="DELETE")
            except _ServiceError:
                pass  # an old glossary left over at DeepL does no harm
        if glossary_id is None:
            made = _call(f"{base}/v2/glossaries", headers=headers, data={
                "name": wanted, "source_lang": "de", "target_lang": "en", "entries_format": "tsv",
                "entries": "\n".join(f"{german}\t{english}" for german, english in current["glossary"])})
            glossary_id = (made or {}).get("glossary_id")
    except _ServiceError as exc:
        raise _ServiceError(f"the glossary: {exc}") from exc
    set_setting_value(DEEPL_GLOSSARY_KEY, json.dumps({"id": glossary_id, "fingerprint": fingerprint,
                                                      "key": key_tag}))
    return glossary_id


def _plain_text(text):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())


def _around(context, texts):
    """Of ``context``, what DeepL is told with ``texts``: all of it, or for a text longer than
    CONTEXT_MAX the part around where the texts stand in it."""
    if len(context) <= CONTEXT_MAX:
        return context
    start = context.find(_plain_text(texts[0])[:80]) if texts else -1
    start = min(max(0, start - CONTEXT_MAX // 3), len(context) - CONTEXT_MAX) if start >= 0 else 0
    return context[start:start + CONTEXT_MAX]


#: What a DeepL key's plan refused (``custom_instructions``, then ``model_type``): asked without
#: it from then on, until the portal restarts.
_REFUSED = {}


def _deepl(texts, as_html, current, context="", patience=None):
    key = current["deepl_key"]
    patience = patience or _Patience(False)
    key_tag = hashlib.sha256(key.encode()).hexdigest()[:8]
    data = {"text": texts, "source_lang": "DE", "target_lang": TARGET}
    if context.strip():
        data["context"] = _around(context, texts)
    refused = _REFUSED.setdefault(key_tag, set())
    extras = {name: value for name, value in (("custom_instructions", INSTRUCTIONS), ("model_type", MODEL))
              if name not in refused}
    data.update(extras)
    if as_html:
        data["tag_handling"] = "html"
    glossary_id = _deepl_glossary(current)
    if glossary_id:
        data["glossary_id"] = glossary_id

    def send():
        return _patiently(patience, lambda: _call(f"{_deepl_base(key)}/v2/translate",
                                                  headers=_deepl_headers(key), data=data))

    try:
        answer = send()
    except _ServiceError as exc:
        if glossary_id and "glossary" in str(exc).lower():
            # The glossary kept is gone at DeepL (deleted by hand, or the account changed): made anew.
            data["glossary_id"] = _deepl_glossary(current, fresh=True)
            answer = send()
        elif exc.code in (400, 403) and extras:
            # The instructions, or the better model, are not every plan's: asked again without the
            # instructions, then without the model too -- and from then on without what it took (a key
            # not accepted fails the same way every time, and nothing is marked).
            answer, dropped = None, []
            for name in list(extras):
                del data[name]
                dropped.append(name)
                try:
                    answer = send()
                    break
                except _ServiceError as again:
                    if again.code not in (400, 403):
                        raise
            if answer is None:
                raise
            current_app.logger.warning("DeepL refused %s (%s); translating without.", " and ".join(dropped), exc)
            refused.update(dropped)
        else:
            raise
    made = [item.get("text", "") for item in (answer or {}).get("translations", [])]
    if len(made) != len(texts):
        raise _ServiceError("an answer of the wrong length")
    return made


def deepl_usage():
    """{"used", "limit"} characters as DeepL counts them -- per month, or for good, by the plan --, or
    None when DeepL is not set up or does not answer."""
    key = settings()["deepl_key"]
    if not key:
        return None
    try:
        answer = _call(f"{_deepl_base(key)}/v2/usage", headers=_deepl_headers(key), method="GET") or {}
    except _ServiceError:
        return None
    return {"used": int(answer.get("character_count") or 0), "limit": int(answer.get("character_limit") or 0)}


# --- Azure AI Translator -------------------------------------------------------------------


def _with_glossary(text, glossary, as_html):
    """The glossary's English in place of the German words as written, marked not to be translated."""
    for german, english in sorted(glossary, key=lambda entry: -len(entry[0])):
        mark = f'<span class="notranslate">{html.escape(english)}</span>' if as_html else english
        text = re.sub(rf"(?<![\w-]){re.escape(german)}(?![\w-])", lambda _m, mark=mark: mark, text)
    return text


def _azure(texts, as_html, current, patience=None):
    patience = patience or _Patience(False)
    headers = {"Ocp-Apim-Subscription-Key": current["azure_key"]}
    if current["azure_region"]:
        headers["Ocp-Apim-Subscription-Region"] = current["azure_region"]
    # Azure has one English, "en" -- no British variant as DeepL's EN-GB.
    url = (f"{AZURE_ENDPOINT}/translate?api-version=3.0&from=de&to=en"
           f"&textType={'html' if as_html else 'plain'}")
    sent = [{"Text": _with_glossary(text, current["glossary"], as_html)} for text in texts]
    patience.pace(sum(len(item["Text"]) for item in sent))
    answer = _patiently(patience, lambda: _call(url, headers=headers, data=sent))
    made = [((item.get("translations") or [{}])[0]).get("text", "") for item in (answer or [])]
    if len(made) != len(texts):
        raise _ServiceError("an answer of the wrong length")
    return made


# --- What comes back ------------------------------------------------------------------------

_ALLOWED = {
    "p": (), "br": (), "strong": (), "em": (), "code": (), "blockquote": (), "hr": (),
    "h1": ("id", "class"), "h2": ("id", "class"), "h3": ("id",), "h4": ("id",),
    "ul": (), "ol": ("start",), "li": (),
    "table": (), "thead": (), "tbody": (), "tr": (), "th": ("class",), "td": ("class",),
    "a": ("href",),
}
_VOID = {"br", "hr"}


class _Cleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []

    def handle_starttag(self, tag, attrs):
        if tag not in _ALLOWED:
            return  # unwrapped: its text stays (Azure's notranslate span, anything unexpected)
        kept = []
        for name, value in attrs:
            if name not in _ALLOWED[tag] or value is None:
                continue
            if name == "href" and not re.match(r"^(https?:|mailto:|#)", value.strip(), re.IGNORECASE):
                continue
            kept.append(f' {name}="{html.escape(value, quote=True)}"')
        self.out.append(f"<{tag}{''.join(kept)}>")

    def handle_endtag(self, tag):
        if tag in _ALLOWED and tag not in _VOID:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        self.out.append(html.escape(data, quote=False))


def clean_html(text):
    """A translator's HTML kept to the tags and attributes the pages' HTML has, everything else as text."""
    cleaner = _Cleaner()
    cleaner.feed(text or "")
    cleaner.close()
    return "".join(cleaner.out)
