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

    def __call__(self, url, *, data=None, headers=None, method="POST"):
        self.calls.append((url, data, headers, method))
        service = "deepl" if "deepl" in url else "azure"
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
        if service == "deepl" and data.get("glossary_id") and data["glossary_id"] not in self.glossaries:
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


def test_deepls_characters_this_month(services):
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
        assert [s["name"] for s in shown["services"]] == ["azure", "deepl"]
        assert [s["key_set"] for s in shown["services"]] == [False, True]
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

    assert sent[0]["text"][:2] == ["<p>mit Räumen sorgsam umzugehen;</p>\n", "<p>Text.</p>\n"]
    assert sent[0]["context"] == translation.CONTEXT
    assert english == ("<p>iii) mit Räumen sorgsam umzugehen;</p>\n<p>(b) Text.</p>\n"
                       "<h2>§ 1 Geltung</h2>\n<p>Ein Satz.</p>\n")
