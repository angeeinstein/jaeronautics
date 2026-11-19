"""Translation by machine, German to English (docs/legal-translation-plan.md):
DeepL, Azure AI Translator and a language model (Anthropic, OpenAI, or a
server of one's own with OpenAI's interface, such as Ollama) -- any of them,
tried in the order set under Settings › Translation, each the next one's
fallback.

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
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from flask import current_app, g, has_request_context

from ..db_models import Translation, db
from . import ValidationError
from .audit import log_audit_event
from .clock import get_now_utc
from .settings import get_settings_map, set_setting_value

DEEPL, AZURE, LLM = "deepl", "azure", "llm"
SERVICES = (DEEPL, AZURE, LLM)
NAMES = {DEEPL: "DeepL", AZURE: "Azure AI Translator", LLM: "Language model"}
#: Where the language model runs: Anthropic (its own package), OpenAI, or another server with
#: OpenAI's interface -- Ollama, llama.cpp, LM Studio, on the portal's server or in the network.
ANTHROPIC, OPENAI, OTHER = "anthropic", "openai", "other"
PROVIDERS = (ANTHROPIC, OPENAI, OTHER)
OPENAI_ADDRESS = "https://api.openai.com/v1"

ORDER_KEY = "translation.order"
DEEPL_KEY = "translation.deepl_key"
AZURE_KEY = "translation.azure_key"
AZURE_REGION_KEY = "translation.azure_region"
LLM_PROVIDER_KEY = "translation.llm_provider"
LLM_KEY = "translation.llm_key"
LLM_MODEL_KEY = "translation.llm_model"
LLM_ADDRESS_KEY = "translation.llm_address"
GLOSSARY_KEY = "translation.glossary"
DEEPL_GLOSSARY_KEY = "translation.deepl_glossary"
KEYS = [ORDER_KEY, DEEPL_KEY, AZURE_KEY, AZURE_REGION_KEY, GLOSSARY_KEY, DEEPL_GLOSSARY_KEY, LLM_PROVIDER_KEY,
        LLM_KEY, LLM_MODEL_KEY, LLM_ADDRESS_KEY]
SECRETS = {DEEPL_KEY, AZURE_KEY, LLM_KEY}

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
#: How long translating may take in a page's request (*Try it*; the editor's English is made in the
#: background); the rest goes to the next service. Not a limit of the server's: gunicorn's
#: --timeout does not end a request on one of its threads; Cloudflare gives up after 100 seconds,
#: nginx after 120 -- but a person waits, and a server thread is held, all that time.
REQUEST_SECONDS = 45
#: A language model gets a whole text in one request, as a rule -- in portions, it named one body
#: two ways ("General Main Assembly", "general assembly"), as no portion saw the others. Its
#: answer, as long, must come back in one piece; with its thinking, within LLM_MAX_TOKENS.
LLM_BATCH_CHARACTERS = 60_000
LLM_BATCH_TEXTS = 1_000
LLM_MAX_TOKENS = 64_000
#: How long one request may take in the background (a local model is slow) -- within JOB_SECONDS.
LLM_TIMEOUT_SECONDS = 600
#: How long a translation in the background may take in all, waits and requests together: what is
#: not done by then goes to the next service. Below the outbox's lease (10 minutes: another worker
#: would take the job over) and the worker service's 600 seconds (process-external-work claims no
#: new job after OUTBOX_CLAIM_SECONDS, so a run with one of these ends in time).
JOB_SECONDS = 420
#: A longer text's portions asked at once.
LLM_AT_ONCE = 6
#: How much a model thinks before answering (Anthropic's effort): a legal text is worth it.
LLM_EFFORT = "high"
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
    def __init__(self, message, *, code=None, retry_after=None, wrong_answer=False):
        super().__init__(message)
        self.code = code
        #: The service answered, but not as asked (a language model's answer failed the checks):
        #: asked again for fewer texts, it may well manage.
        self.wrong_answer = wrong_answer
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
    provider = saved.get(LLM_PROVIDER_KEY) if saved.get(LLM_PROVIDER_KEY) in PROVIDERS else ANTHROPIC
    return {"order": order, "deepl_key": saved.get(DEEPL_KEY) or "", "azure_key": saved.get(AZURE_KEY) or "",
            "azure_region": saved.get(AZURE_REGION_KEY) or "", "glossary": glossary,
            "llm_provider": provider, "llm_key": saved.get(LLM_KEY) or "", "llm_model": saved.get(LLM_MODEL_KEY) or "",
            "llm_address": saved.get(LLM_ADDRESS_KEY) or ""}


def configured(current=None):
    """The services set up, in the order they are tried."""
    current = current or settings()
    # A language model needs its model, and a key -- or, on another server, its address (a local
    # one may need no key).
    llm = bool(current["llm_model"]) and bool(
        current["llm_address"] if current["llm_provider"] == OTHER else current["llm_key"])
    have = {DEEPL: bool(current["deepl_key"]), AZURE: bool(current["azure_key"]), LLM: llm}
    return [name for name in current["order"] if have[name]]


def _clean_address(address):
    address = (address or "").strip().rstrip("/")
    if not address:
        return ""
    parsed = urlparse(address)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.query or parsed.fragment:
        raise ValidationError("Not an address.", details={"fields": {
            "llm_address": "Like http://localhost:11434/v1 -- where its OpenAI interface is."}})
    return address


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
                  forget=(), llm_provider=None, llm_key=None, llm_model=None, llm_address=None):
    """Save the services and the glossary. A key left empty keeps the one set; ``forget`` removes one.
    ``llm_*`` left None keeps what is set."""
    order = [name for name in order if name in SERVICES]
    order += [name for name in SERVICES if name not in order]
    region = (azure_region or "").strip()
    if region and not re.fullmatch(r"[a-z0-9]{2,40}", region):
        raise ValidationError("Not a region.", details={"fields": {"azure_region": "Like westeurope."}})
    values = {ORDER_KEY: ",".join(order), AZURE_REGION_KEY: region}
    if llm_provider is not None:
        if llm_provider not in PROVIDERS:
            raise ValidationError("No such provider.", details={"fields": {"llm_provider": "Choose one."}})
        values[LLM_PROVIDER_KEY] = llm_provider
    if llm_model is not None:
        model = llm_model.strip()
        if len(model) > 100 or any(character.isspace() for character in model):
            raise ValidationError("Not a model.", details={"fields": {
                "llm_model": "Its name as the provider lists it: no spaces, at most 100 characters."}})
        values[LLM_MODEL_KEY] = model
    if llm_address is not None:
        values[LLM_ADDRESS_KEY] = _clean_address(llm_address)
    if glossary is not None:
        values[GLOSSARY_KEY] = json.dumps(_clean_glossary(glossary), ensure_ascii=False)
    for key, new in ((DEEPL_KEY, deepl_key), (AZURE_KEY, azure_key), (LLM_KEY, llm_key)):
        if new and new.strip():
            values[key] = new.strip()
    for name in forget:
        values[{DEEPL: DEEPL_KEY, AZURE: AZURE_KEY, LLM: LLM_KEY}[name]] = ""
    before = get_settings_map(KEYS)
    changed = [key.split(".", 1)[1] for key, value in values.items() if (before.get(key) or "") != value]
    for key, value in values.items():
        set_setting_value(key, value)
    if changed:
        log_audit_event("system", "translation_settings_changed", actor_user=actor, target_user=actor,
                        metadata={"changed": changed},
                        after={key: ("set" if value else "") if key in SECRETS else value
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


def translate(texts, *, as_html=True, context="", patient=False, report=None, only_kept=False):
    """``texts`` in English, in their order: what was translated before from the store, the rest by
    the first service that manages it, portion by portion. TranslationUnavailable when none does.

    ``context``: the German the texts stand in, as plain text -- told to DeepL, not translated, not
    counted. ``patient``: in the background (publishing), so a service that asks to wait is
    waited for, a few minutes in all; in a page's request it goes to the next service at once.
    ``report``, a dict, gets ``services`` (a set: who translated these texts, now or before) and
    ``failures`` ("DeepL: …" of a service that failed while another stepped in) and ``missing`` (how
    many texts are not translated yet). ``only_kept``: no service is asked -- what is not kept yet
    stays German, and ``missing`` counts it."""
    current = settings()
    keys = [_key(text, as_html, current["glossary"]) for text in texts]
    rows = {row.source_hash: row for row in db.session.execute(
        db.select(Translation).where(Translation.source_hash.in_(set(keys)))).scalars()} if keys else {}
    known = {key: row.target for key, row in rows.items()}
    missing = list(dict.fromkeys(text for text, key in zip(texts, keys) if key not in known and text.strip()))
    first = (configured(current) or [None])[0]
    # Made by a later service before this translation -- while the first failed, some time ago.
    # Not what a fallback makes just now: the first has just failed at it.
    earlier = {key for key, row in rows.items() if row.service != first}
    patience = _Patience(patient)  # one time for all of this translation's requests
    failures = []
    if missing and not only_kept:
        made, failures = _by_a_service(missing, as_html, current, context=context, patient=patient,
                                       patience=patience)
        _keep(missing, made, as_html, current, rows)
    if patient and first and not only_kept:
        # In the background: a paragraph a later service made earlier is given to the first once
        # more. Failing again, it stays.
        again = list(dict.fromkeys(text for text, key in zip(texts, keys) if key in earlier and text.strip()))
        if again:
            try:
                made, _none = _by_a_service(again, as_html, current, only=first, context=context, patient=True,
                                            patience=patience)
                _keep(again, made, as_html, current, rows)
            except TranslationUnavailable as exc:
                failures.append(str(exc))
    for key, row in rows.items():
        known[key] = row.target
    if report is not None:
        report.setdefault("services", set()).update(
            rows[key].service for key in keys if key in rows and rows[key].service)
        report.setdefault("failures", []).extend(failures)
        report["missing"] = report.get("missing", 0) + len(
            {key for text, key in zip(texts, keys) if text.strip() and key not in rows})
    return [known.get(key, text) for text, key in zip(texts, keys)]


def _keep(texts, made, as_html, current, rows):
    """``made`` (``[(english, service)]``, one per text) kept, and put into ``rows`` by key."""
    now = get_now_utc()
    for text, (english, service) in zip(texts, made):
        key = _key(text, as_html, current["glossary"])
        row = rows.get(key) or db.session.get(Translation, key)
        if row is None:
            row = Translation(source_hash=key, source=text, target=english, service=service, created_at=now)
            db.session.add(row)
        else:
            row.target, row.service, row.created_at = english, service, now
        rows[key] = row
    db.session.flush()


def forget(texts, *, as_html=True):
    """The kept translations of ``texts`` forgotten: asked for again next time. Returns how many."""
    glossary = settings()["glossary"]
    keys = list({_key(text, as_html, glossary) for text in texts})
    if not keys:
        return 0
    return db.session.execute(db.delete(Translation).where(Translation.source_hash.in_(keys))).rowcount


def _batches(texts, name):
    """``texts`` in portions a request of this service takes: at most BATCH texts, and so many
    characters -- fewer for Azure's free tier and a language model."""
    for portion in _portions(texts, range(len(texts)), name):
        yield [texts[index] for index in portion]


def _portions(texts, indexes, name):
    """Of ``texts``, those at ``indexes`` in portions (lists of indexes) as ``_batches`` makes them."""
    most = {AZURE: AZURE_BATCH_CHARACTERS, LLM: LLM_BATCH_CHARACTERS}.get(name, BATCH_CHARACTERS)
    count = LLM_BATCH_TEXTS if name == LLM else BATCH
    portion, size = [], 0
    for index in indexes:
        if portion and (len(portion) == count or size + len(texts[index]) > most):
            yield portion
            portion, size = [], 0
        portion.append(index)
        size += len(texts[index])
    if portion:
        yield portion


class _Patience:
    """How long a translation may still wait, and what was sent to Azure in the last minute."""

    def __init__(self, patient):
        self.left = WAIT_MAX_SECONDS if patient else 0
        self.sent = deque()  # (when, characters)
        #: By when the answers must be there, for all of a translation together: in a page's
        #: request REQUEST_SECONDS, in the background JOB_SECONDS.
        self.until = _clock() + (JOB_SECONDS if patient else REQUEST_SECONDS)
        if not patient and has_request_context():
            self.until = g.setdefault("translation_until", self.until)

    def time_for(self, most):
        """How long the next request may take: ``most``, or what is left of the time -- too little
        left, and the next service gets the rest."""
        left = self.until - _clock()
        if left < 5:
            raise _ServiceError("no time left; the rest by the next service")
        return min(most, left)

    def wait(self, seconds):
        seconds = max(1, min(seconds, 120))
        if seconds > self.left or _clock() + seconds > self.until:
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


def _by_a_service(texts, as_html, current, only=None, context="", patient=False, patience=None):
    """``([(english, service), …], failures)``: each portion by the first service that translates it.
    ``patience``: the time and waits of a translation that asks more than once (``translate``)."""
    services = [only] if only else configured(current)
    if not services:
        raise TranslationUnavailable("No translation service is set up (Settings › Translation).")
    done, left, reasons = {}, list(range(len(texts))), []
    patience = patience or _Patience(patient)
    for name in services:
        left, failure = _with_service(name, texts, left, done, as_html, current, context, patience)
        if failure is not None:
            current_app.logger.warning("Translating with %s failed: %s", NAMES[name], failure)
            reasons.append(f"{NAMES[name]}: {failure}")
        if not left:
            return [done[index] for index in range(len(texts))], reasons
    raise TranslationUnavailable("; ".join(reasons))


def _with_service(name, texts, left, done, as_html, current, context, patience):
    """The texts at the indexes ``left`` by one service, into ``done``: ``(still left, why)``.

    DeepL and Azure portion by portion, stopping at the first that fails. A language model's
    portions all at once -- one after another, a long text took longer than a page's request may
    (the editor), and publishing waited minutes; one that fails goes to the next service alone."""

    def one(portion):
        batch = [texts[index] for index in portion]
        if name == DEEPL:
            return _deepl(batch, as_html, current, context, patience)
        return _azure(batch, as_html, current, patience)  # Azure takes no context

    def keep(portion, english):
        for index, text in zip(portion, english):
            if text is not None:
                done[index] = (clean_html(text) if as_html else text, name)

    portions = list(_portions(texts, left, name))
    if name == LLM:
        with ThreadPoolExecutor(max_workers=LLM_AT_ONCE) as pool:
            # The app's logger taken along: the threads have no application context.
            asked = [(portion, pool.submit(_llm_in_halves, [texts[index] for index in portion], as_html, current,
                                           context, patience, current_app.logger)) for portion in portions]
        failed, failure = [], None
        for portion, answer in asked:
            try:
                english, why = answer.result()
            except _ServiceError as exc:
                english, why = [None] * len(portion), exc
            keep(portion, english)
            failed += [index for index, text in zip(portion, english) if text is None]
            failure = failure or why
        return failed, failure
    for at, portion in enumerate(portions):
        try:
            keep(portion, one(portion))
        except _ServiceError as exc:
            return [index for rest in portions[at:] for index in rest], exc
    return [], None


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


def _call(url, *, data=None, headers=None, method="POST", timeout=TIMEOUT_SECONDS):
    # ensure_ascii off: an umlaut is two bytes, not six -- a DeepL request, context included, is
    # at most 128 KiB.
    body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
    request = Request(url, data=body, method=method,
                      headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "jaeronautics",
                               **(headers or {})})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 -- the services' addresses, set by admins
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


# --- A language model ------------------------------------------------------------------------

LLM_INSTRUCTIONS = """You translate the legal texts of a student association in Austria from German into English: \
its statutes, rules of procedure, membership terms, privacy policy and its teams' rules.

- Formal British English, as legal texts are written. Translate faithfully: leave nothing out, add nothing, \
explain nothing.
- Gender-neutral: a member or any other person is "they" and "their", never "he", "his", "she" or "her".
- Keep every HTML tag and its attributes exactly as they are, in the same places; translate only the text \
between them. Leave email addresses, web addresses and names as they are, and use English quotation \
marks ("...") where the German has its own („...“).
- Each text you are given is a part of the whole German text, which follows for context: a heading, a \
paragraph, or a list item that finishes a sentence begun before it. Translate each so that it reads right \
in its place.
- Be consistent: translate a German term the same way wherever it occurs, and give every body, role and \
name one English form throughout (the glossary's, where it has one).
- Answer with JSON: {"translations": [...]}, one English string for each text given, in the same order -- \
exactly as many as were given."""

_ANSWER = {"type": "object", "properties": {"translations": {"type": "array", "items": {"type": "string"}}},
           "required": ["translations"], "additionalProperties": False}


def _llm_prompt(texts, current, context):
    """``(instructions, the whole German text, the request)`` for a language model."""
    instructions = LLM_INSTRUCTIONS
    if current["glossary"]:
        instructions += ("\n- Always translate these terms so, in whatever form the German word takes:\n"
                         + "\n".join(f"  {german} -> {english}" for german, english in current["glossary"]))
    whole = f"The whole German text, for context:\n\n{_around(context, texts)}" if context.strip() else ""
    asked = f"Translate these {len(texts)} texts:\n{json.dumps(texts, ensure_ascii=False)}"
    return instructions, whole, asked


def _tags(text):
    return [" ".join(tag.split()) for tag in re.findall(r"<[^>]*>", text)]


def _llm_in_halves(texts, as_html, current, context, patience, log):
    """``(english, why)``: the texts by a language model, ``None`` for any it did not manage. An
    answer that fails the checks is asked for again in halves, down to a single text, so one bad
    paragraph does not send a whole text to the next service."""
    try:
        return _llm(texts, as_html, current, context, patience), None
    except _ServiceError as exc:
        if not exc.wrong_answer or len(texts) < 2:
            if len(texts) == 1 or exc.wrong_answer:
                return [None] * len(texts), exc
            raise
        log.info("A language model's answer for %s texts failed the checks (%s); asking for them in halves.",
                 len(texts), exc)
    half = len(texts) // 2
    first, why_first = _llm_in_halves(texts[:half], as_html, current, context, patience, log)
    second, why_second = _llm_in_halves(texts[half:], as_html, current, context, patience, log)
    return first + second, why_first or why_second


def _llm(texts, as_html, current, context="", patience=None):
    """The texts in English by a language model: one request per portion, the answer a list in a
    fixed format, checked -- as many texts as sent, and with HTML the same tags -- or the portion
    goes to the next service."""
    patience = patience or _Patience(False)
    instructions, whole, asked = _llm_prompt(texts, current, context)
    ask = _ask_anthropic if current["llm_provider"] == ANTHROPIC else _ask_openai
    answer = _patiently(patience, lambda: ask(current, instructions, whole, asked,
                                              patience.time_for(LLM_TIMEOUT_SECONDS)))
    try:
        made = json.loads(answer)["translations"]
    except (ValueError, TypeError, KeyError) as exc:
        raise _ServiceError("an answer that is not the list asked for", wrong_answer=True) from exc
    if not isinstance(made, list) or len(made) != len(texts) or not all(isinstance(text, str) for text in made):
        raise _ServiceError("an answer of the wrong length", wrong_answer=True)
    for german, english in zip(texts, made):
        if german.strip() and not english.strip():
            raise _ServiceError("a text left empty", wrong_answer=True)
        if as_html and _tags(german) != _tags(english):
            raise _ServiceError("an answer whose HTML differs from the text's", wrong_answer=True)
    return made


def _ask_anthropic(current, instructions, whole, asked, timeout):
    """The answer's text, from Anthropic's Messages API. The whole German text is cached there:
    sent with every portion, it is paid in full once."""
    import anthropic

    system = [{"type": "text", "text": instructions}]
    if whole:
        system.append({"type": "text", "text": whole, "cache_control": {"type": "ephemeral"}})
    client = anthropic.Anthropic(api_key=current["llm_key"], timeout=timeout, max_retries=1)
    model = current["llm_model"]

    def send(output_config):
        with client.messages.stream(model=model, max_tokens=LLM_MAX_TOKENS, system=system,
                                    messages=[{"role": "user", "content": asked}],
                                    output_config=output_config) as stream:
            return stream.get_final_message()

    output_config = {"format": {"type": "json_schema", "schema": _ANSWER}}
    try:
        if model in _NO_EFFORT:
            message = send(output_config)
        else:
            try:
                message = send({**output_config, "effort": LLM_EFFORT})
            except anthropic.BadRequestError as exc:
                if "effort" not in str(exc.message).lower():
                    raise
                # An older model without effort: asked without, and from then on.
                _NO_EFFORT.add(model)
                message = send(output_config)
    except anthropic.RateLimitError as exc:
        retry = exc.response.headers.get("retry-after") if exc.response is not None else None
        raise _ServiceError(f"too many requests just now ({exc.message})", code=429,
                            retry_after=int(float(retry)) if retry and retry.replace(".", "", 1).isdigit() else 60
                            ) from exc
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        raise _ServiceError(f"the key is not accepted ({exc.message})", code=exc.status_code) from exc
    except anthropic.NotFoundError as exc:
        raise _ServiceError(f"no such model ({exc.message})", code=404) from exc
    except anthropic.APIStatusError as exc:
        raise _ServiceError(f"error {exc.status_code} ({exc.message})", code=exc.status_code) from exc
    except anthropic.APIConnectionError as exc:
        raise _ServiceError(f"not reached ({exc})") from exc
    if message.stop_reason == "refusal":
        raise _ServiceError("it declined to translate this")
    if message.stop_reason == "max_tokens":
        raise _ServiceError("the answer was too long", wrong_answer=True)
    return "".join(block.text for block in message.content if block.type == "text")


#: Models that refused the effort setting (older ones), asked without it until the portal restarts.
_NO_EFFORT = set()


def _ask_openai(current, instructions, whole, asked, timeout):
    """The answer's text, from an OpenAI chat interface: OpenAI's own, or another server's
    (Ollama, llama.cpp, LM Studio), which may need no key."""
    address = OPENAI_ADDRESS if current["llm_provider"] == OPENAI else current["llm_address"]
    headers = {"Authorization": f"Bearer {current['llm_key']}"} if current["llm_key"] else {}
    system = f"{instructions}\n\n{whole}" if whole else instructions
    answer = _call(f"{address}/chat/completions", headers=headers, timeout=timeout, data={
        "model": current["llm_model"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": asked}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "translations", "strict": True, "schema": _ANSWER}}})
    choice = ((answer or {}).get("choices") or [{}])[0]
    said = choice.get("message") or {}
    if said.get("refusal"):
        raise _ServiceError("it declined to translate this")
    if choice.get("finish_reason") == "length":
        raise _ServiceError("the answer was too long", wrong_answer=True)
    return said.get("content") or ""


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
