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
"""

import hashlib
import html
import json
import re
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
#: Told to DeepL with every text, not translated and not counted: what the texts are, and that the
#: English is gender-neutral -- the German says "sein Verhalten" of a member, the English "their".
CONTEXT = ("Legal texts of a student association in Austria: statutes, rules, terms, privacy policy. "
           "Formal British English. Gender-neutral: a member or person is 'they', 'their', never 'he' or 'his'.")
#: Texts per request: DeepL takes 50 at a time.
BATCH = 50
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
    pass


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
    # The context is part of it: when it changes, what was translated is translated anew.
    return hashlib.sha256(f"{TARGET}|{int(as_html)}|{_fingerprint(glossary)}|{CONTEXT}|{text}".encode()
                          ).hexdigest()


def translate(texts, *, as_html=True):
    """``texts`` in English, in their order: what was translated before from the store, the rest by
    the first service that manages it. TranslationUnavailable when none does."""
    current = settings()
    keys = [_key(text, as_html, current["glossary"]) for text in texts]
    known = {row.source_hash: row.target for row in db.session.execute(
        db.select(Translation).where(Translation.source_hash.in_(set(keys)))).scalars()} if keys else {}
    missing = list(dict.fromkeys(text for text, key in zip(texts, keys) if key not in known and text.strip()))
    if missing:
        made, service = _by_a_service(missing, as_html, current)
        now = get_now_utc()
        for text, english in zip(missing, made):
            key = _key(text, as_html, current["glossary"])
            known[key] = english
            if db.session.get(Translation, key) is None:
                db.session.add(Translation(source_hash=key, source=text, target=english, service=service,
                                           created_at=now))
        db.session.flush()
    return [known.get(key, text) for text, key in zip(texts, keys)]


def forget(texts, *, as_html=True):
    """The kept translations of ``texts`` forgotten: asked for again next time. Returns how many."""
    glossary = settings()["glossary"]
    keys = list({_key(text, as_html, glossary) for text in texts})
    if not keys:
        return 0
    return db.session.execute(db.delete(Translation).where(Translation.source_hash.in_(keys))).rowcount


def _by_a_service(texts, as_html, current, only=None):
    services = [only] if only else configured(current)
    if not services:
        raise TranslationUnavailable("No translation service is set up (Settings › Translation).")
    reasons = []
    for name in services:
        try:
            made = []
            for start in range(0, len(texts), BATCH):
                batch = texts[start:start + BATCH]
                made += (_deepl if name == DEEPL else _azure)(batch, as_html, current)
            return [clean_html(text) if as_html else text for text in made], name
        except _ServiceError as exc:
            current_app.logger.warning("Translating with %s failed: %s", NAMES[name], exc)
            reasons.append(f"{NAMES[name]}: {exc}")
    raise TranslationUnavailable("; ".join(reasons))


def try_service(name):
    """*Try it* on the settings page: one sentence, through this service only, nothing kept."""
    if name not in SERVICES:
        raise ValidationError("No such service.")
    current = settings()
    try:
        [english], _service = _by_a_service(["Der Vorstand beschließt über die Aufnahme neuer Mitglieder."],
                                            False, current, only=name)
    except TranslationUnavailable as exc:
        raise ValidationError(str(exc), code="translation_failed") from exc
    return english


def _call(url, *, data=None, headers=None, method="POST"):
    request = Request(url, data=json.dumps(data).encode() if data is not None else None, method=method,
                      headers={"Content-Type": "application/json", "User-Agent": "jaeronautics", **(headers or {})})
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 -- fixed https hosts
            body = response.read()
            return json.loads(body) if body else None
    except HTTPError as exc:
        raise _ServiceError(_said(exc)) from exc
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


def _deepl(texts, as_html, current):
    key = current["deepl_key"]
    data = {"text": texts, "source_lang": "DE", "target_lang": TARGET, "context": CONTEXT}
    if as_html:
        data["tag_handling"] = "html"
    glossary_id = _deepl_glossary(current)
    if glossary_id:
        data["glossary_id"] = glossary_id
    try:
        answer = _call(f"{_deepl_base(key)}/v2/translate", headers=_deepl_headers(key), data=data)
    except _ServiceError as exc:
        if not glossary_id or "glossary" not in str(exc).lower():
            raise
        # The glossary kept is gone at DeepL (deleted by hand, or the account changed): made anew.
        data["glossary_id"] = _deepl_glossary(current, fresh=True)
        answer = _call(f"{_deepl_base(key)}/v2/translate", headers=_deepl_headers(key), data=data)
    made = [item.get("text", "") for item in (answer or {}).get("translations", [])]
    if len(made) != len(texts):
        raise _ServiceError("an answer of the wrong length")
    return made


def deepl_usage():
    """{"used", "limit"} characters this month, or None when DeepL is not set up or does not answer."""
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


def _azure(texts, as_html, current):
    headers = {"Ocp-Apim-Subscription-Key": current["azure_key"]}
    if current["azure_region"]:
        headers["Ocp-Apim-Subscription-Region"] = current["azure_region"]
    # Azure has one English, "en" -- no British variant as DeepL's EN-GB.
    url = (f"{AZURE_ENDPOINT}/translate?api-version=3.0&from=de&to=en"
           f"&textType={'html' if as_html else 'plain'}")
    sent = [{"Text": _with_glossary(text, current["glossary"], as_html)} for text in texts]
    answer = _call(url, headers=headers, data=sent)
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
