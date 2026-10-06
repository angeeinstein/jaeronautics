"""The legal texts as PDFs: the German version, then its English translation.

Made with WeasyPrint from the same Markdown as the pages, kept until the text
changes, and only ever reading the fonts and the logo while laid out.
"""
from datetime import date
from pathlib import Path

import pytest
from flask import render_template

from aeronautics_members import legal_pdf
from aeronautics_members.services import backup
from aeronautics_members.services import legal_texts as legal
from test_legal_texts import REPO_TEXTS, legal_dir, texts  # noqa: F401 -- fixtures

TODAY = date(2026, 10, 5)


@pytest.fixture
def with_translation(texts):  # noqa: F811
    texts("membership-terms", "2026-10-04", "## 1. Verein\n\nSiehe [Punkt 1](#1-verein).\n\n| A | B |\n|---|---|\n| 1 | 2 |",
          title="Mitgliedschaftsbedingungen")
    texts("membership-terms", "2026-10-04", "## 1. Association\n\nText.", language="en",
          title="Membership Terms and Conditions")
    return legal.current_version("membership-terms", today=TODAY)


def _html(app, german):
    return render_template(legal_pdf.TEMPLATE, sections=legal_pdf._sections(german), german=german,
                           association=legal_pdf.ASSOCIATION)


class TestWhatIsInIt:
    def test_the_german_version_then_the_english_translation(self, app, with_translation):
        html = _html(app, with_translation)

        german, english = html.split('id="part-en"')
        assert "Mitgliedschaftsbedingungen" in german and "Fassung vom 04.10.2026" in german
        assert "Membership Terms and Conditions" in english and "the German version applies" in english
        assert "<table>" in german

    def test_each_languages_anchors_and_links_are_its_own(self, app, with_translation):
        html = _html(app, with_translation)

        assert 'id="de-1-verein"' in html and 'href="#de-1-verein"' in html
        assert 'id="en-1-association"' in html

    def test_german_alone_where_there_is_no_translation(self, app, texts):  # noqa: F811
        texts("statutes", "2019-03-17", "## § 1 Name\n\nText.", title="Statuten")

        html = _html(app, legal.current_version("statutes", today=TODAY))

        assert 'id="part-en"' not in html and "translation" not in html.lower().split("<body>")[1]

    def test_it_is_a_pdf(self, app, with_translation):
        assert legal_pdf.build(with_translation).startswith(b"%PDF")

    def test_named_for_saving(self, app, with_translation):
        assert legal_pdf.filename(with_translation) == \
            "Joanneum-Aeronautics_Mitgliedschaftsbedingungen_2026-10-04.pdf"


class TestKept:
    def test_made_once_until_the_text_changes(self, app, with_translation, legal_dir, monkeypatch):  # noqa: F811
        made = []
        monkeypatch.setattr(legal_pdf, "build", lambda version, team=None, **_: made.append(version) or b"%PDF-1.7 fake")

        legal_pdf.pdf_for(with_translation)
        legal_pdf.pdf_for(with_translation)
        assert len(made) == 1

        english = legal_dir / "membership-terms" / "en" / "2026-10-04.md"
        english.write_text(english.read_text(encoding="utf-8") + "\nMore.\n", encoding="utf-8")
        legal_pdf.pdf_for(with_translation)

        assert len(made) == 2
        assert len(list(legal_pdf.cache_dir().glob("membership-terms_2026-10-04_*.pdf"))) == 1

    def test_not_in_the_backup(self, app, with_translation):
        legal_pdf.pdf_for(with_translation)
        app.config["BACKUP_STORAGE_DIR"] = str(legal_pdf.cache_dir().parent)

        assert not [path for path in backup._storage_files() if path.suffix == ".pdf"]


class TestOnlyTheStaticFolderIsRead:
    def test_a_font_is_read(self, app):
        static = Path(app.static_folder) / "fonts" / "inter-latin-wght-normal.woff2"

        assert legal_pdf._static_only_fetcher().fetch(static.as_uri())

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "https://example.com/x.png",
                                     "file:///static/../../etc/passwd"])
    def test_nothing_else(self, app, url):
        with pytest.raises(ValueError):
            legal_pdf._static_only_fetcher().fetch(url)

    def test_an_image_in_a_text_from_elsewhere_is_left_out(self, app, texts, monkeypatch):  # noqa: F811
        texts("statutes", "2019-03-17", "![x](https://example.com/x.png)\n\n![y](file:///etc/passwd)\n\nText.")
        asked = []
        original = legal_pdf._static_only_fetcher

        def spying(also=None):
            fetcher = original(also)
            fetch = fetcher.fetch
            fetcher.fetch = lambda url, headers=None: asked.append(url) or fetch(url, headers)
            return fetcher

        monkeypatch.setattr(legal_pdf, "_static_only_fetcher", spying)

        assert legal_pdf.build(legal.current_version("statutes", today=TODAY)).startswith(b"%PDF")
        assert "https://example.com/x.png" in asked  # asked for, and refused


class TestThePages:
    def test_the_pdf_of_the_version_in_force(self, client, with_translation):
        response = client.get("/legal/membership-terms/pdf")

        assert response.status_code == 200 and response.mimetype == "application/pdf"
        assert "Joanneum-Aeronautics_Mitgliedschaftsbedingungen_2026-10-04.pdf" in response.headers["Content-Disposition"]
        assert response.headers["Content-Disposition"].startswith("inline")

    @pytest.mark.parametrize("path", ["/legal/nothing/pdf", "/legal/membership-terms/pdf/2020-01-01",
                                      "/legal/membership-terms/pdf/not-a-day", "/legal/statutes/pdf"])
    def test_what_does_not_exist(self, client, with_translation, path):
        assert client.get(path).status_code == 404

    def test_when_it_cannot_be_made_the_text_is_shown(self, client, with_translation, monkeypatch):
        monkeypatch.setattr(legal_pdf, "ready", lambda version, team=None: 1 / 0)

        response = client.get("/legal/membership-terms/pdf")

        assert response.status_code == 302 and response.headers["Location"].endswith("/legal/membership-terms/de")

    def test_linked_from_the_text_and_the_list(self, client, with_translation):
        page = client.get("/legal/membership-terms").get_data(as_text=True)
        listing = client.get("/legal").get_data(as_text=True)

        assert 'href="/legal/membership-terms/pdf"' in page and "PDF, German and English" in page
        assert "data-legal-file" in page  # opens in a tab of its own from the window over the signup form
        assert 'href="/legal/membership-terms/pdf"' in listing

    def test_an_earlier_version_links_its_own(self, client, texts):  # noqa: F811
        texts("statutes", "2019-03-17", title="Statuten")
        texts("statutes", "2026-01-01", title="Statuten")

        page = client.get("/legal/statutes/de/2019-03-17").get_data(as_text=True)

        assert 'href="/legal/statutes/pdf/2019-03-17"' in page


class TestMadeBeforeItIsOpened:
    """The page's script asks for the PDF to be made (?prepare=1), then opens it."""

    def test_made_and_its_address_given(self, client, with_translation):
        answer = client.get("/legal/membership-terms/pdf?prepare=1")

        assert answer.status_code == 200 and answer.is_json
        url = answer.get_json()["url"]
        assert url.startswith("/legal/membership-terms/pdf?v=") and "prepare" not in url
        assert list(legal_pdf.cache_dir().glob("membership-terms_2026-10-04_*.pdf"))
        response = client.get(url)
        assert response.status_code == 200 and response.mimetype == "application/pdf"

    def test_the_address_changes_with_the_pdf(self, client, with_translation, legal_dir):  # noqa: F811
        first = client.get("/legal/membership-terms/pdf?prepare=1").get_json()["url"]
        english = legal_dir / "membership-terms" / "en" / "2026-10-04.md"
        english.write_text(english.read_text(encoding="utf-8") + "\nMore.\n", encoding="utf-8")

        second = client.get("/legal/membership-terms/pdf?prepare=1").get_json()["url"]

        assert first != second

    def test_never_kept_by_the_browser(self, client, with_translation):
        response = client.get("/legal/membership-terms/pdf")

        assert "no-store" in response.headers["Cache-Control"]

    def test_when_it_cannot_be_made_the_script_is_told(self, client, with_translation, monkeypatch):
        monkeypatch.setattr(legal_pdf, "ready", lambda version, team=None: 1 / 0)

        answer = client.get("/legal/membership-terms/pdf?prepare=1")

        assert answer.status_code == 500 and answer.get_json()["error"]

    def test_every_pdf_link_is_made_first(self, client, with_translation):
        page = client.get("/legal").get_data(as_text=True)

        assert 'href="/legal/membership-terms/pdf" target="_blank" rel="noopener" data-legal-file' in page
        assert "legal-pdf-open.js" in page


class TestMadeAgain:
    def test_remake_replaces_the_kept_file(self, app, with_translation):
        path, _digest = legal_pdf.ready(with_translation)
        path.write_bytes(b"%PDF stale")

        assert legal_pdf.remake(with_translation) > 1000
        assert path.read_bytes() != b"%PDF stale"

    def test_forget_all(self, app, with_translation):
        legal_pdf.pdf_for(with_translation)

        assert legal_pdf.forget_all() == 1
        assert not list(legal_pdf.cache_dir().glob("*.pdf"))


def test_every_text_in_the_repository_makes_a_pdf(app):
    """So a text that cannot be laid out fails the build, not the download."""
    app.config["LEGAL_TEXTS_DIR"] = str(REPO_TEXTS)

    made = legal_pdf.build_all()

    assert made
    assert [(version.path.name, result) for version, result in made if isinstance(result, Exception)] == []
