"""Translation by machine (services/translation.py, docs/legal-translation-plan.md):
DeepL and Azure, one first and the other the fallback; what was translated
once is never sent again; the glossary; what comes back is cleaned.
"""
import json
from urllib.parse import urlparse

import pytest

from conftest import db
from aeronautics_members.db_models import Translation
from aeronautics_members.services import ValidationError
from aeronautics_members.services import translation
from aeronautics_members.services.settings import set_setting_value


class FakeServices:
    """Both services, answering with the text marked by who translated it -- or failing."""

    def __init__(self):
        self.calls = []
        self.failing = set()
        self.glossaries = {}  # what DeepL holds: id -> name

    def __call__(self, url, *, data=None, headers=None, method="POST", timeout=None):
        self.calls.append((url, data, headers, method))
        service = "deepl" if "deepl" in url else "llm" if "chat/completions" in url else "azure"
        if service in self.failing:
            raise translation._ServiceError("the key is not accepted")
        if method == "DELETE":
            self.glossaries.pop(url.rsplit("/", 1)[1], None)
            return None
        if url.endswith("/v2/glossaries") and method == "GET":
            return {"glossaries": [{"glossary_id": id_, "name": name} for id_, name in self.glossaries.items()]}
        if url.endswith("/v2/glossaries") and method == "POST":
            made = f"g-{len(self.calls)}"
            self.glossaries[made] = data["name"]
            return {"glossary_id": made}
        if url.endswith("/v2/usage"):
            return {"character_count": 1234, "character_limit": 500000}
        if url.endswith("/chat/completions"):  # a language model with OpenAI's interface
            if "llm" in self.failing:
                raise translation._ServiceError("not reached")
            asked = json.loads(data["messages"][1]["content"].split("\n", 1)[1])
            return {"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps({"translations": [f"[LLM] {text}" for text in asked]})}}]}
        if service == "deepl" and isinstance(data, dict) and data.get("glossary_id") and data["glossary_id"] not in self.glossaries:
            raise translation._ServiceError("error 404 (Glossary not found)")
        if service == "deepl":
            return {"translations": [{"text": f"[DeepL] {text}"} for text in data["text"]]}
        return [{"translations": [{"text": f"[Azure] {item['Text']}"}]} for item in data]


@pytest.fixture
def services(app, monkeypatch):
    fake = FakeServices()
    monkeypatch.setattr(translation, "_call", fake)
    return fake


def _set_up(deepl="key:fx", azure="", order="deepl,azure"):
    set_setting_value(translation.DEEPL_KEY, deepl)
    set_setting_value(translation.AZURE_KEY, azure)
    set_setting_value(translation.ORDER_KEY, order)
    db.session.commit()


class TestTranslating:
    def test_by_the_first_service_and_kept(self, services):
        _set_up()

        assert translation.translate(["<p>Hallo.</p>"]) == ["[DeepL] <p>Hallo.</p>"]
        assert translation.translate(["<p>Hallo.</p>"]) == ["[DeepL] <p>Hallo.</p>"]

        sent = [call for call in services.calls if call[0].endswith("/v2/translate")]
        assert len(sent) == 1 and urlparse(sent[0][0]).hostname == "api-free.deepl.com"
        assert sent[0][1]["tag_handling"] == "html" and sent[0][1]["target_lang"] == "EN-GB"
        assert db.session.query(Translation).count() == 1

    def test_only_what_is_new_is_sent(self, services):
        _set_up()
        translation.translate(["<p>Eins.</p>", "<p>Zwei.</p>"])

        translation.translate(["<p>Eins.</p>", "<p>Drei.</p>"])

        last = [call for call in services.calls if call[0].endswith("/v2/translate")][-1]
        assert last[1]["text"] == ["<p>Drei.</p>"]

    def test_the_other_service_when_the_first_fails(self, services):
        _set_up(azure="azure-key", order="deepl,azure")
        services.failing.add("deepl")

        assert translation.translate(["<p>Hallo.</p>"]) == ["[Azure] <p>Hallo.</p>"]
        assert db.session.query(Translation).one().service == "azure"

    def test_azure_first_when_chosen(self, services):
        _set_up(azure="azure-key", order="azure,deepl")

        assert translation.translate(["<p>Hallo.</p>"]) == ["[Azure] <p>Hallo.</p>"]
        # Azure's English is "en": it has no British variant.
        [url] = [call[0] for call in services.calls if "microsofttranslator" in call[0]]
        assert "&to=en&" in url

    def test_none_set_up_or_all_failing_says_why(self, services):
        _set_up(deepl="")
        with pytest.raises(translation.TranslationUnavailable, match="No translation service"):
            translation.translate(["<p>Hallo.</p>"])

        _set_up(azure="azure-key")
        services.failing.update({"deepl", "azure"})
        with pytest.raises(translation.TranslationUnavailable, match="DeepL: .*; Azure AI Translator: "):
            translation.translate(["<p>Hallo.</p>"])


class TestTheGlossary:
    def test_deepl_gets_a_glossary_of_its_own_made_anew_when_it_changes(self, services):
        _set_up()
        def made():
            return [call for call in services.calls if call[0].endswith("/v2/glossaries") and call[3] == "POST"]

        translation.translate(["<p>Der Vorstand.</p>"])
        assert len(made()) == 1 and "Vorstand\tboard" in made()[0][1]["entries"]
        translation.translate(["<p>Der Verein.</p>"])
        assert len(made()) == 1

        set_setting_value(translation.GLOSSARY_KEY, json.dumps([["Vorstand", "executive board"]]))
        translation.translate(["<p>Neu.</p>"])
        assert len(made()) == 2 and len(services.glossaries) == 1  # the old one deleted

    def test_none_left_over_at_deepl_when_its_id_was_not_kept(self, services):
        # Trying the key once did not keep the id: each translation made another glossary, until
        # DeepL refused ("a limit of the account is reached").
        _set_up()
        services.glossaries = {"g-old": translation.GLOSSARY_NAME, "g-theirs": "another program's"}
        translation.translate(["<p>Eins.</p>"])
        mine = {id_ for id_, name in services.glossaries.items() if name.startswith(translation.GLOSSARY_NAME)}

        set_setting_value(translation.DEEPL_GLOSSARY_KEY, "")  # the id lost again
        translation.translate(["<p>Zwei.</p>"])

        assert services.glossaries.keys() == mine | {"g-theirs"} and len(mine) == 1
        sent = [call for call in services.calls if call[0].endswith("/v2/translate")]
        assert [call[1]["glossary_id"] for call in sent] == [next(iter(mine))] * 2

    def test_another_installations_glossary_left_alone(self, services, app):
        # The test server with the same key: its glossaries have its address in their name.
        _set_up()
        theirs = f"{translation.GLOSSARY_NAME} test.example.org 0123456789abcdef"
        services.glossaries = {"g-test": theirs}

        translation.translate(["<p>Eins.</p>"])

        assert services.glossaries["g-test"] == theirs and len(services.glossaries) == 2
        [mine] = [name for name in services.glossaries.values() if name != theirs]
        assert mine.startswith(translation._glossary_prefix() + " ")

    def test_a_glossary_gone_at_deepl_made_anew(self, services):
        _set_up()
        translation.translate(["<p>Eins.</p>"])
        services.glossaries.clear()  # deleted at DeepL; the setting still has its id

        assert translation.translate(["<p>Zwei.</p>"]) == ["[DeepL] <p>Zwei.</p>"]
        assert len(services.glossaries) == 1

    def test_azure_gets_the_english_in_place_not_to_be_translated(self, services):
        _set_up(deepl="", azure="azure-key")

        [english] = translation.translate(["<p>Der Vorstand beschließt; Vorstandssitzung bleibt.</p>"])

        sent = [call for call in services.calls if "microsofttranslator" in call[0]][0][1][0]["Text"]
        assert '<span class="notranslate">board</span>' in sent and "Vorstandssitzung" in sent
        assert "notranslate" not in english  # the mark is gone again

    def test_kept_clean(self, app):
        with pytest.raises(ValidationError):
            translation._clean_glossary([["x" * 200, "y"]])
        assert translation._clean_glossary([[" Verein ", "association"], ["Verein", "club"], ["", "x"]]) == [
            ["Verein", "association"]]


class TestWhatComesBack:
    def test_only_the_pages_tags_stay(self):
        dirty = ('<h2 id="paragraph-1" onclick="x()">§ 1</h2><script>alert(1)</script>'
                 '<p>A <a href="javascript:x()">bad</a> and <a href="https://example.org">good</a> link.</p>')

        clean = translation.clean_html(dirty)

        assert clean == ('<h2 id="paragraph-1">§ 1</h2>alert(1)'
                         '<p>A <a>bad</a> and <a href="https://example.org">good</a> link.</p>')


def test_trying_a_service(services):
    _set_up()

    assert translation.try_service("deepl").startswith("[DeepL] Der Vorstand")
    assert db.session.query(Translation).count() == 0


def test_the_services_own_message_said(app):
    import io
    from urllib.error import HTTPError

    def refused(code, body):
        return translation._said(HTTPError("https://x", code, "", {}, io.BytesIO(body)))

    assert refused(456, b'{"message": "Quota Exceeded", "detail": "Too many glossaries."}') == (
        "a limit of the account is reached (Quota Exceeded Too many glossaries.)")
    assert refused(401, b'{"error": {"code": 401000, "message": "Access denied."}}') == (
        "the key is not accepted (Access denied.)")
    assert refused(500, b"<html>down</html>") == "error 500"


def test_trying_keeps_deepls_glossary(client, services):
    from test_admin_reviews import _login, _staff

    _login(client, _staff("boss@example.org", "superadmin").id)
    _set_up()

    for _ in range(2):
        assert client.post("/api/v1/admin/settings/translation/deepl/try").get_json()["ok"]

    assert len([call for call in services.calls if call[0].endswith("/v2/glossaries") and call[3] == "POST"]) == 1


def test_deepls_characters_used(services):
    _set_up()

    assert translation.deepl_usage() == {"used": 1234, "limit": 500000}


class TestTheSettingsPage:
    def test_saved_keys_never_shown_order_and_glossary_kept(self, client, services):
        from api_helpers import send
        from test_admin_reviews import _login, _staff

        _login(client, _staff("boss@example.org", "superadmin").id)

        saved = send(client, "PUT", "/api/v1/admin/settings/translation", {
            "order": ["azure", "deepl"], "deepl_key": "secret:fx", "azure_key": "", "azure_region": "westeurope",
            "glossary": [["Verein", "association"]]})
        shown = client.get("/api/v1/admin/settings/translation").get_json()

        assert saved.status_code == 200 and "deepl_key" in saved.get_json()["changed"]
        assert [s["name"] for s in shown["services"]] == ["azure", "deepl", "llm"]
        assert [s["key_set"] for s in shown["services"]] == [False, True, False]
        assert "secret" not in str(shown) and shown["glossary"] == [["Verein", "association"]]

    def test_the_default_glossary_saved_whole(self, client, services):
        # SQLite keeps a value longer than its column; MariaDB refuses it. So the
        # column itself is checked: the glossary is longer than 255 characters.
        from api_helpers import send
        from test_admin_reviews import _login, _staff
        from aeronautics_members.db_models import Setting

        _login(client, _staff("boss@example.org", "superadmin").id)
        glossary = [list(pair) for pair in translation.DEFAULT_GLOSSARY] + [[f"Wort {n}", f"word {n}"] for n in range(40)]

        saved = send(client, "PUT", "/api/v1/admin/settings/translation", {
            "order": ["deepl", "azure"], "deepl_key": "secret:fx", "azure_key": "", "azure_region": "",
            "glossary": glossary})
        stored = db.session.get(Setting, translation.GLOSSARY_KEY).value

        assert saved.status_code == 200
        assert client.get("/api/v1/admin/settings/translation").get_json()["glossary"] == glossary
        assert len(stored) > 255 and getattr(Setting.__table__.c.value.type, "length", None) is None

    def test_only_with_the_credentials_permission(self, client):
        from test_admin_reviews import _login, _staff

        _login(client, _staff("treasurer@example.org", "treasurer").id)

        assert client.get("/api/v1/admin/settings/translation").status_code == 403


def test_a_paragraphs_own_number_kept_as_written(services, monkeypatch):
    # DeepL wrote some "i)" as "(i)" and left others: the number no longer goes to it.
    from types import SimpleNamespace

    from aeronautics_members.services import legal_translation

    _set_up()
    monkeypatch.setattr(legal_translation.legal, "blocks", lambda _german: [
        "<p>iii) mit Räumen sorgsam umzugehen;</p>\n", "<p>(b) Text.</p>\n", "<h2>§ 1 Geltung</h2>\n",
        "<p>Ein Satz.</p>\n"])

    sent = []

    def deepl(url, *, data=None, headers=None, method="POST"):  # the text back as it went
        if url.endswith("/v2/translate"):
            sent.append(data)
            return {"translations": [{"text": text} for text in data["text"]]}
        return services(url, data=data, headers=headers, method=method)

    monkeypatch.setattr(translation, "_call", deepl)

    _title, english = legal_translation.translate_version(SimpleNamespace(title="Teamordnung"))

    assert sent[0]["text"][:3] == ["<h1>Teamordnung</h1>", "<p>mit Räumen sorgsam umzugehen;</p>\n",
                                   "<p>Text.</p>\n"]
    assert english == ("<p>iii) mit Räumen sorgsam umzugehen;</p>\n<p>(b) Text.</p>\n"
                       "<h2>§ 1 Geltung</h2>\n<p>Ein Satz.</p>\n")


class TestTheWholeTextAsContext:
    """Every block is translated on its own, told the whole German text -- a heading of one word,
    or a list item finishing a sentence begun above it, is translated knowing where it stands."""

    BLOCKS = ["<h2>§ 3 Beschwerderecht</h2>\n", "<p>Mit dem Beitritt verpflichtet sich jedes Mitglied,</p>\n",
              "<p>i) fair zu sein;</p>\n", "<p>ii) zu helfen.</p>\n"]

    def _translate(self, services, monkeypatch, blocks=None):
        from types import SimpleNamespace

        from aeronautics_members.services import legal_translation

        monkeypatch.setattr(legal_translation.legal, "blocks", lambda _german: list(blocks or self.BLOCKS))
        sent = []

        def deepl(url, *, data=None, headers=None, method="POST"):
            if url.endswith("/v2/translate"):
                sent.append(data)
                return {"translations": [{"text": text} for text in data["text"]]}
            return services(url, data=data, headers=headers, method=method)

        monkeypatch.setattr(translation, "_call", deepl)
        report = {}
        _title, english = legal_translation.translate_version(SimpleNamespace(title="Teamordnung"), report=report)
        return sent, english, report

    def test_each_on_its_own_told_the_whole_text(self, services, monkeypatch):
        _set_up()
        sent, english, report = self._translate(services, monkeypatch)

        [blocks] = sent  # the title with the text, as a heading: one request
        assert blocks["text"][0] == "<h1>Teamordnung</h1>"
        assert blocks["text"][3] == "<p>fair zu sein;</p>\n"  # on its own, its number off
        assert blocks["context"] == (
            "Teamordnung\n§ 3 Beschwerderecht\nMit dem Beitritt verpflichtet sich jedes Mitglied,\n"
            "i) fair zu sein;\nii) zu helfen.")
        assert blocks["custom_instructions"] == translation.INSTRUCTIONS and blocks["model_type"] == translation.MODEL
        assert english == "".join(self.BLOCKS)
        assert report == {"services": {"deepl"}, "failures": [], "missing": 0}

    def test_another_paragraph_changed_only_that_one_sent(self, services, monkeypatch):
        # Kept by its own text, not its context: a change elsewhere costs only what changed.
        _set_up()
        self._translate(services, monkeypatch)

        sent, _english, report = self._translate(services, monkeypatch,
                                                 blocks=self.BLOCKS[:3] + ["<p>ii) anderen zu helfen.</p>\n"])

        assert [data["text"] for data in sent] == [["<p>anderen zu helfen.</p>\n"]]
        assert report["services"] == {"deepl"}  # the kept ones' service too

    def test_kept_and_forgotten(self, services, monkeypatch):
        from types import SimpleNamespace

        from aeronautics_members.services import legal_translation

        _set_up()
        self._translate(services, monkeypatch)
        assert db.session.query(Translation).count() == 5  # four blocks and the title

        legal_translation.forget_kept(SimpleNamespace(title="Teamordnung"))

        assert db.session.query(Translation).count() == 0

    def test_a_long_text_told_the_part_around(self, app):
        context = "".join(f"Absatz {n}. " for n in range(10_000))
        part = translation._around(context, ["<p>Absatz 9000.</p>"])

        assert len(part) == translation.CONTEXT_MAX and "Absatz 9000." in part


class TestDeepLsInstructionsAndModel:
    def _refusing(self, services, monkeypatch, refused):
        _set_up()
        monkeypatch.setattr(translation, "_REFUSED", {})
        sent = []

        def deepl(url, *, data=None, headers=None, method="POST"):
            if url.endswith("/v2/translate"):
                sent.append(sorted(name for name in ("custom_instructions", "model_type") if name in data))
                if refused & data.keys():
                    raise translation._ServiceError("error 400 (not supported)", code=400)
                return {"translations": [{"text": f"[DeepL] {text}"} for text in data["text"]]}
            return services(url, data=data, headers=headers, method=method)

        monkeypatch.setattr(translation, "_call", deepl)
        assert translation.translate(["<p>Eins.</p>"]) == ["[DeepL] <p>Eins.</p>"]
        assert translation.translate(["<p>Zwei.</p>"]) == ["[DeepL] <p>Zwei.</p>"]
        return sent

    def test_instructions_refused_the_better_model_kept(self, services, monkeypatch):
        sent = self._refusing(services, monkeypatch, {"custom_instructions"})

        assert sent == [["custom_instructions", "model_type"], ["model_type"], ["model_type"]]

    def test_both_refused_asked_without_from_then_on(self, services, monkeypatch):
        sent = self._refusing(services, monkeypatch, {"custom_instructions", "model_type"})

        assert sent == [["custom_instructions", "model_type"], ["model_type"], [], []]

    def test_a_key_not_accepted_not_taken_for_a_plan_without_them(self, services, monkeypatch):
        _set_up()
        monkeypatch.setattr(translation, "_REFUSED", {})
        services.failing.add("deepl")

        with pytest.raises(translation.TranslationUnavailable):
            translation.translate(["<p>Eins.</p>"])

        assert not any(translation._REFUSED.values())


class TestAzuresFreeTier:
    """Azure's free tier refuses more than about 33,000 characters a minute ("too many requests"):
    on the live server it refused a whole privacy policy, and DeepL stepped in unnoticed."""

    @pytest.fixture
    def clock(self, monkeypatch):
        now = {"t": 1000.0, "slept": []}

        def sleep(seconds):
            now["slept"].append(seconds)
            now["t"] += seconds

        monkeypatch.setattr(translation, "_sleep", sleep)
        monkeypatch.setattr(translation, "_clock", lambda: now["t"])
        return now

    def test_sent_in_portions_paced_when_publishing(self, services, clock):
        _set_up(deepl="", azure="azure-key")
        texts = [f"<p>{n} {'x' * 4_000}</p>" for n in range(20)]  # 80,000 characters

        translation.translate(texts, patient=True)

        sent = [call[1] for call in services.calls if "microsofttranslator" in call[0]]
        assert all(sum(len(item["Text"]) for item in batch) <= translation.AZURE_BATCH_CHARACTERS for batch in sent)
        assert len(sent) == 10 and clock["slept"]  # waited for the minute to pass

    def test_too_many_requests_waited_for_when_publishing(self, services, clock, monkeypatch):
        _set_up(azure="azure-key", order="azure,deepl")
        refusals = {"left": 2}

        def azure(url, *, data=None, headers=None, method="POST"):
            if "microsofttranslator" in url and refusals["left"]:
                refusals["left"] -= 1
                raise translation._ServiceError("too many requests just now", code=429, retry_after=30)
            return services(url, data=data, headers=headers, method=method)

        monkeypatch.setattr(translation, "_call", azure)
        report = {}

        assert translation.translate(["<p>Hallo.</p>"], patient=True, report=report) == ["[Azure] <p>Hallo.</p>"]
        assert clock["slept"] == [30, 30] and report == {"services": {"azure"}, "failures": [], "missing": 0}

    def test_in_a_pages_request_the_next_service_at_once_and_why_kept(self, services, clock, monkeypatch):
        _set_up(azure="azure-key", order="azure,deepl")

        def azure(url, *, data=None, headers=None, method="POST"):
            if "microsofttranslator" in url:
                raise translation._ServiceError("too many requests just now", code=429, retry_after=30)
            return services(url, data=data, headers=headers, method=method)

        monkeypatch.setattr(translation, "_call", azure)
        report = {}

        assert translation.translate(["<p>Hallo.</p>"], report=report) == ["[DeepL] <p>Hallo.</p>"]
        assert clock["slept"] == []
        assert report == {"services": {"deepl"}, "failures": ["Azure AI Translator: too many requests just now"],
                          "missing": 0}

    def test_what_azure_managed_kept_the_rest_by_the_next(self, services, clock, monkeypatch):
        _set_up(azure="azure-key", order="azure,deepl")
        texts = [f"<p>{n} {'x' * 6_000}</p>" for n in range(3)]  # one portion each
        answered = {"count": 0}

        def azure(url, *, data=None, headers=None, method="POST"):
            if "microsofttranslator" in url:
                answered["count"] += 1
                if answered["count"] > 1:
                    raise translation._ServiceError("too many requests just now", code=429, retry_after=30)
            return services(url, data=data, headers=headers, method=method)

        monkeypatch.setattr(translation, "_call", azure)

        english = translation.translate(texts)

        assert english[0].startswith("[Azure]") and all(text.startswith("[DeepL]") for text in english[1:])
        sent_to_deepl = [call[1]["text"] for call in services.calls if call[0].endswith("/v2/translate")]
        assert sent_to_deepl == [texts[1:]]

    def test_retry_after_read(self, app):
        import io
        from urllib.error import HTTPError

        def refused(code, headers):
            return translation._retry_after(HTTPError("https://x", code, "", headers, io.BytesIO(b"")))

        assert refused(429, {"Retry-After": "12"}) == 12
        assert refused(429, {}) == 60
        assert refused(500, {"Retry-After": "12"}) is None


def _set_up_llm(provider="other", key="", model="a-model", address="http://localhost:11434/v1", order="llm,deepl,azure",
                deepl="key:fx"):
    _set_up(deepl=deepl, order=order)
    for setting, value in ((translation.LLM_PROVIDER_KEY, provider), (translation.LLM_KEY, key),
                           (translation.LLM_MODEL_KEY, model), (translation.LLM_ADDRESS_KEY, address)):
        set_setting_value(setting, value)
    db.session.commit()


class TestALanguageModel:
    """A language model as a third service (docs/legal-translation-plan.md): Anthropic, OpenAI,
    or another server with OpenAI's interface -- a local Ollama, say."""

    def test_another_server_asked_with_the_whole_text_and_the_glossary(self, services):
        _set_up_llm()

        english = translation.translate(["<p>Der Vorstand.</p>", "<h2>§ 3 Beschwerderecht</h2>"],
                                        context="Statuten\n§ 3 Beschwerderecht")

        assert english == ["[LLM] <p>Der Vorstand.</p>", "[LLM] <h2>§ 3 Beschwerderecht</h2>"]
        [call] = [c for c in services.calls if c[0].endswith("/chat/completions")]
        url, data, headers, _method = call
        assert url == "http://localhost:11434/v1/chat/completions" and "Authorization" not in headers
        assert data["model"] == "a-model" and data["response_format"]["type"] == "json_schema"
        system = data["messages"][0]["content"]
        assert "Vorstand -> board" in system and "§ 3 Beschwerderecht" in system and "gender-neutral" in system.lower()
        assert db.session.query(Translation).filter_by(service="llm").count() == 2

    def test_openai_at_its_own_address_with_the_key(self, services):
        _set_up_llm(provider="openai", key="sk-test", address="")

        translation.translate(["<p>Eins.</p>"])

        [call] = [c for c in services.calls if c[0].endswith("/chat/completions")]
        assert call[0] == "https://api.openai.com/v1/chat/completions"
        assert call[2]["Authorization"] == "Bearer sk-test"

    def test_set_up_only_with_a_model_and_a_key_or_an_address(self, app):
        _set_up_llm(model="")
        assert "llm" not in translation.configured()
        _set_up_llm(provider="anthropic", key="")
        assert "llm" not in translation.configured()
        _set_up_llm(provider="anthropic", key="k")
        assert translation.configured()[0] == "llm"
        _set_up_llm(provider="other", key="", address="http://ollama.local:11434/v1")
        assert translation.configured()[0] == "llm"  # a local server may need no key

    @pytest.mark.parametrize("spoil, why", [
        (lambda texts: texts[:-1], "wrong length"),
        (lambda texts: [text.replace("<p>", "").replace("</p>", "") if "Zwei" in text else text for text in texts],
         "HTML differs"),
        (lambda texts: ["" if "Zwei" in text else text for text in texts], "left empty"),
    ])
    def test_an_answer_that_does_not_fit_asked_again_in_halves(self, services, monkeypatch, spoil, why):
        # One bad paragraph does not send the whole text to the next service: asked again in halves,
        # only the paragraph the model still gets wrong goes on.
        _set_up_llm()

        def answer(current, instructions, whole, asked, timeout):
            texts = json.loads(asked.split("\n", 1)[1])
            made = [f"[LLM] {text}" for text in texts]
            return json.dumps({"translations": spoil(made) if any("Zwei" in text for text in texts) else made})

        monkeypatch.setattr(translation, "_ask_openai", answer)
        report = {}

        english = translation.translate(["<p>Eins.</p>", "<p>Zwei.</p>", "<p>Drei.</p>"], report=report)

        assert english == ["[LLM] <p>Eins.</p>", "[DeepL] <p>Zwei.</p>", "[LLM] <p>Drei.</p>"]
        assert why in report["failures"][0] and report["services"] == {"llm", "deepl"}

    def test_a_whole_text_in_one_request(self, services, monkeypatch):
        _set_up_llm()
        asked = []

        def answer(current, instructions, whole, request, timeout):
            texts = json.loads(request.split("\n", 1)[1])
            asked.append(len(texts))
            return json.dumps({"translations": [f"[LLM] {text}" for text in texts]})

        monkeypatch.setattr(translation, "_ask_openai", answer)

        translation.translate([f"<p>Absatz {n}. {'x' * 300}</p>" for n in range(120)])  # 36,000 characters

        assert asked == [120]

    def test_a_very_long_text_in_portions_asked_all_at_once(self, services, monkeypatch):
        # Longer than one request takes: its portions go out together (each waits here until all
        # three have arrived).
        import threading

        _set_up_llm()
        texts = [f"<p>{n} {'x' * 40_000}</p>" for n in range(3)]  # a portion each
        together = threading.Barrier(3, timeout=5)

        def at_once(current, instructions, whole, asked, timeout):
            together.wait()
            return json.dumps({"translations": [f"[LLM] {text}" for text in json.loads(asked.split("\n", 1)[1])]})

        monkeypatch.setattr(translation, "_ask_openai", at_once)

        english = translation.translate(texts)

        assert all(text.startswith("[LLM] ") for text in english)

    def test_one_portion_failing_goes_to_the_next_service_alone(self, services, monkeypatch):
        _set_up_llm()
        texts = [f"<p>{n} {'x' * 40_000}</p>" for n in range(3)]

        def second_fails(current, instructions, whole, asked, timeout):
            sent = json.loads(asked.split("\n", 1)[1])
            if sent[0].startswith("<p>1 "):
                raise translation._ServiceError("not reached")
            return json.dumps({"translations": [f"[LLM] {text}" for text in sent]})

        monkeypatch.setattr(translation, "_ask_openai", second_fails)

        english = translation.translate(texts)

        assert [text[:6] for text in english] == ["[LLM] ", "[DeepL", "[LLM] "]

    def test_in_a_pages_request_the_next_service_when_no_time_is_left(self, services, monkeypatch):
        _set_up_llm()
        ticks = iter([0.0])  # the request starts at 0; every later look at the clock: 42 s on
        monkeypatch.setattr(translation, "_clock", lambda: next(ticks, 42.0))
        monkeypatch.setattr(translation, "_ask_openai", lambda *_a: pytest.fail("asked without time"))
        report = {}

        assert translation.translate(["<p>Eins.</p>"], report=report) == ["[DeepL] <p>Eins.</p>"]
        assert "no time left" in report["failures"][0]

    def test_publishing_gives_what_a_fallback_made_to_the_first_once_more(self, services):
        _set_up_llm()
        services.failing.add("llm")
        translation.translate(["<p>Eins.</p>"])  # in the editor: the model away, DeepL stepped in
        services.failing.clear()
        report = {}

        english = translation.translate(["<p>Eins.</p>"], patient=True, report=report)

        assert english == ["[LLM] <p>Eins.</p>"] and report["services"] == {"llm"}
        assert db.session.query(Translation).one().service == "llm"

    def test_what_a_fallback_made_just_now_not_given_to_the_first_again(self, services):
        # The first service has just failed at it: asking it again at once spends its time twice
        # (Codex, #85). Only what a fallback made earlier gets another try.
        _set_up_llm()
        services.failing.add("llm")

        translation.translate(["<p>Eins.</p>"], patient=True)

        # Asked once (and failing), DeepL once -- not the model a second time.
        assert len([call for call in services.calls if call[0].endswith("/chat/completions")]) == 1
        assert len([call for call in services.calls if call[0].endswith("/v2/translate")]) == 1

    def test_in_the_background_a_translation_ends_in_time(self, services, monkeypatch):
        # The outbox gives a job to another worker after ten minutes, and the worker service ends
        # a run after 600 seconds: a translation in the background takes at most JOB_SECONDS.
        _set_up_llm()
        ticks = iter([0.0])
        monkeypatch.setattr(translation, "_clock", lambda: next(ticks, translation.JOB_SECONDS - 2.0))
        monkeypatch.setattr(translation, "_ask_openai", lambda *_a: pytest.fail("asked without time"))
        report = {}

        assert translation.translate(["<p>Eins.</p>"], patient=True, report=report) == ["[DeepL] <p>Eins.</p>"]
        assert "no time left" in report["failures"][0]

    def test_publishing_keeps_the_fallbacks_when_the_first_fails_again(self, services):
        _set_up_llm()
        services.failing.add("llm")
        translation.translate(["<p>Eins.</p>"])
        report = {}

        english = translation.translate(["<p>Eins.</p>"], patient=True, report=report)

        assert english == ["[DeepL] <p>Eins.</p>"] and report["services"] == {"deepl"}
        assert report["failures"] and "Language model" in report["failures"][0]

    def test_anthropic_asked_with_the_text_cached_and_a_fixed_answer(self, app, monkeypatch):
        import anthropic

        seen = {}

        class Stream:
            def __init__(self, **kwargs):
                seen.update(kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def get_final_message(self):
                from types import SimpleNamespace
                texts = json.loads(seen["messages"][0]["content"].split("\n", 1)[1])
                return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(
                    type="text", text=json.dumps({"translations": [f"[A] {t}" for t in texts]}))])

        class Client:
            def __init__(self, **kwargs):
                seen["client"] = kwargs
                from types import SimpleNamespace
                self.messages = SimpleNamespace(stream=lambda **kw: Stream(**kw))

        monkeypatch.setattr(anthropic, "Anthropic", Client)
        _set_up_llm(provider="anthropic", key="sk-ant-test", model="the-model")

        english = translation.translate(["<p>Eins.</p>"], context="Statuten\nEins.")

        assert english == ["[A] <p>Eins.</p>"]
        assert seen["client"]["api_key"] == "sk-ant-test" and seen["model"] == "the-model"
        assert seen["system"][1]["cache_control"] == {"type": "ephemeral"} and "Statuten" in seen["system"][1]["text"]
        assert seen["output_config"]["format"]["type"] == "json_schema"

    def test_anthropic_asked_to_think_hard_unless_the_model_does_not_know_effort(self, app, monkeypatch):
        import anthropic
        import httpx2

        sent = []

        class Stream:
            def __init__(self, **kwargs):
                sent.append(kwargs["output_config"])
                self.kwargs = kwargs

            def __enter__(self):
                if "effort" in self.kwargs["output_config"]:
                    response = httpx2.Response(400, request=httpx2.Request("POST", "https://api.anthropic.com"))
                    raise anthropic.BadRequestError("effort: not supported by this model", response=response,
                                                    body=None)
                return self

            def __exit__(self, *_exc):
                return False

            def get_final_message(self):
                from types import SimpleNamespace
                return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(
                    type="text", text=json.dumps({"translations": ["[A] <p>Eins.</p>"]}))])

        class Client:
            def __init__(self, **_kwargs):
                from types import SimpleNamespace
                self.messages = SimpleNamespace(stream=lambda **kw: Stream(**kw))

        monkeypatch.setattr(anthropic, "Anthropic", Client)
        monkeypatch.setattr(translation, "_NO_EFFORT", set())
        _set_up_llm(provider="anthropic", key="sk-ant-test", model="an-older-model")

        assert translation.translate(["<p>Eins.</p>"]) == ["[A] <p>Eins.</p>"]
        translation.translate(["<p>Zwei.</p>"])

        assert [config.get("effort") for config in sent] == ["high", None, None]

    def test_saved_and_checked(self, client, services):
        from api_helpers import send
        from test_admin_reviews import _login, _staff

        _login(client, _staff("boss@example.org", "superadmin").id)
        body = {"order": ["llm", "deepl", "azure"], "deepl_key": "", "azure_key": "", "azure_region": "",
                "glossary": [], "llm_provider": "other", "llm_key": "", "llm_model": "a model",
                "llm_address": "http://localhost:11434/v1/"}

        refused = send(client, "PUT", "/api/v1/admin/settings/translation", body)
        body["llm_model"] = "a-model"
        saved = send(client, "PUT", "/api/v1/admin/settings/translation", body)
        shown = client.get("/api/v1/admin/settings/translation").get_json()

        assert refused.status_code == 400 and saved.status_code == 200
        assert [s["name"] for s in shown["services"]] == ["llm", "deepl", "azure"]
        assert shown["llm_model"] == "a-model" and shown["llm_address"] == "http://localhost:11434/v1"
        body["llm_address"] = "file:///etc/passwd"
        assert send(client, "PUT", "/api/v1/admin/settings/translation", body).status_code == 400
