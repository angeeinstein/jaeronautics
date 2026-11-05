"""Translation by machine (services/translation.py, docs/legal-translation-plan.md):
DeepL and Azure, one first and the other the fallback; what was translated
once is never sent again; the glossary; what comes back is cleaned.
"""
import json

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

    def __call__(self, url, *, data=None, headers=None, method="POST"):
        self.calls.append((url, data, headers, method))
        service = "deepl" if "deepl" in url else "azure"
        if service in self.failing:
            raise translation._ServiceError("the key is not accepted")
        if method == "DELETE":
            return None
        if url.endswith("/v2/glossaries") and method == "POST":
            return {"glossary_id": "g-1"}
        if url.endswith("/v2/usage"):
            return {"character_count": 1234, "character_limit": 500000}
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
        assert len(sent) == 1 and sent[0][0].startswith("https://api-free.deepl.com")
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
        translation.translate(["<p>Der Vorstand.</p>"])
        made = [call for call in services.calls if call[0].endswith("/v2/glossaries")]
        assert len(made) == 1 and "Vorstand\tboard" in made[0][1]["entries"]
        translation.translate(["<p>Der Verein.</p>"])
        assert len([call for call in services.calls if call[0].endswith("/v2/glossaries")]) == 1

        set_setting_value(translation.GLOSSARY_KEY, json.dumps([["Vorstand", "executive board"]]))
        translation.translate(["<p>Neu.</p>"])
        assert len([call for call in services.calls if call[0].endswith("/v2/glossaries")]) == 2

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


def test_deepls_characters_this_month(services):
    _set_up()

    assert translation.deepl_usage() == {"used": 1234, "limit": 500000}
