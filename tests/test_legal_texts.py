"""The legal texts: legal/<text>/<language>/<day>.md, with front matter.

The newest published version whose effective_from has come is in force; one
can be committed early and switch over by itself, or wait as a draft. German
applies; an English translation of the version shown is offered first, saying
so. At signup every text accepted then is linked from the one tick -- opening
over the form, so nothing typed is lost -- and the versions ticked are kept
with the member.
"""
import types
from datetime import date
from pathlib import Path

import pytest

from conftest import db
from aeronautics_members.db_models import Member
from aeronautics_members.services import legal_texts as legal
from aeronautics_members.services import privacy

REPO_TEXTS = Path(__file__).resolve().parent.parent / "legal"
TRANSLATION_NOTICE = "the German version applies"


@pytest.fixture
def legal_dir(app, tmp_path):
    folder = tmp_path / "legal"
    folder.mkdir()
    app.config["LEGAL_TEXTS_DIR"] = str(folder)
    return folder


@pytest.fixture
def texts(legal_dir):
    """An empty legal/ folder of the test's own; ``put`` writes a version into it."""

    def put(slug, day, body="Text.", language="de", effective_from=None, status="published",
            title=None, front_matter=None):
        folder = legal_dir / slug / language
        folder.mkdir(parents=True, exist_ok=True)
        if front_matter is None:
            front_matter = (
                f'title: "{title or slug.title()}"\ndocument: "{slug}"\nlanguage: "{language}"\n'
                f'version: "{day}"\neffective_from: "{effective_from or day}"\nstatus: "{status}"\n'
            )
        (folder / f"{day}.md").write_text(f"---\n{front_matter}---\n\n{body}\n", encoding="utf-8")

    return put


class TestWhichVersion:
    def test_the_newest_published_whose_day_has_come(self, texts):
        texts("statutes", "2019-03-17", "Alt.")
        texts("statutes", "2026-01-01", "Neu.")
        texts("statutes", "2027-01-01", "Kommt noch.")

        def in_force(today):
            version = legal.current_version("statutes", today=today)
            return version and version.version

        assert in_force(date(2026, 6, 1)) == date(2026, 1, 1)
        assert in_force(date(2027, 1, 1)) == date(2027, 1, 1)
        assert in_force(date(2019, 1, 1)) is None

    def test_by_effective_from_not_the_version(self, texts):
        texts("statutes", "2019-03-17")
        texts("statutes", "2026-10-04", effective_from="2027-01-01")

        assert legal.current_version("statutes", today=date(2026, 12, 31)).version == date(2019, 3, 17)
        assert legal.current_version("statutes", today=date(2027, 1, 1)).version == date(2026, 10, 4)

    def test_a_draft_never(self, texts):
        texts("statutes", "2019-03-17")
        texts("statutes", "2026-01-01", status="draft")

        assert legal.current_version("statutes", today=date(2030, 1, 1)).version == date(2019, 3, 17)
        assert [v.version for v in legal.versions("statutes", today=date(2030, 1, 1))] == [date(2019, 3, 17)]

    def test_a_text_without_a_german_file_is_not_shown(self, texts):
        texts("statutes", "2019-03-17")
        texts("legal-notice", "2026-10-04", language="en")

        assert [text.slug for text, _ in legal.available(today=date(2026, 10, 4))] == ["statutes"]

    def test_only_the_texts_accepted_at_signup_are_to_accept(self, texts):
        texts("privacy-policy", "2026-10-04")
        texts("legal-notice", "2026-10-04")

        assert legal.versions_to_accept(today=date(2026, 10, 4)) == {"privacy-policy": "2026-10-04"}


class TestTheFilesAreChecked:
    def test_good_files_have_no_problems(self, texts):
        texts("statutes", "2019-03-17")
        texts("privacy-policy", "2026-10-04")
        texts("privacy-policy", "2026-10-04", language="en")

        assert legal.problems() == []

    @pytest.mark.parametrize("front_matter, problem", [
        ('title: "S"\ndocument: "privacy-policy"\nlanguage: "de"\nversion: "2026-10-04"\n'
         'effective_from: "2026-10-04"\nstatus: "published"\n', 'document is "privacy-policy"'),
        ('title: "S"\ndocument: "statutes"\nlanguage: "en"\nversion: "2026-10-04"\n'
         'effective_from: "2026-10-04"\nstatus: "published"\n', 'language is "en"'),
        ('title: "S"\ndocument: "statutes"\nlanguage: "de"\nversion: "2026-10-05"\n'
         'effective_from: "2026-10-04"\nstatus: "published"\n', 'version is "2026-10-05"'),
        ('title: "S"\ndocument: "statutes"\nlanguage: "de"\nversion: "2026-10-04"\n'
         'effective_from: "soon"\nstatus: "published"\n', "effective_from"),
        ('title: "S"\ndocument: "statutes"\nlanguage: "de"\nversion: "2026-10-04"\n'
         'effective_from: "2026-10-04"\nstatus: "live"\n', 'status is "live"'),
        ('document: "statutes"\nlanguage: "de"\nversion: "2026-10-04"\n'
         'effective_from: "2026-10-04"\nstatus: "published"\n', "title is missing"),
        ("title: [unclosed\n", "not valid YAML"),
    ])
    def test_a_field_that_does_not_fit(self, texts, front_matter, problem):
        texts("statutes", "2026-10-04", front_matter=front_matter)

        [found] = legal.problems()
        assert problem in found and found.startswith("statutes/de/2026-10-04.md")
        assert legal.current_version("statutes", today=date(2026, 10, 4)) is None  # and it is not shown

    def test_a_file_without_front_matter(self, texts, legal_dir):
        (legal_dir / "statutes" / "de").mkdir(parents=True)
        (legal_dir / "statutes" / "de" / "2019-03-17.md").write_text("# Statuten\n")

        assert "no front matter" in legal.problems()[0]

    def test_a_file_in_the_wrong_place(self, texts, legal_dir):
        (legal_dir / "statutes").mkdir()
        (legal_dir / "statutes" / "2019-03-17.md").write_text("---\n---\n")
        (legal_dir / "statues" / "de").mkdir(parents=True)
        (legal_dir / "statues" / "de" / "2019-03-17.md").write_text("---\n---\n")

        assert all("not where a version goes" in problem for problem in legal.problems())
        assert len(legal.problems()) == 2

    def test_two_published_from_the_same_day(self, texts):
        texts("statutes", "2026-10-04")
        texts("statutes", "2026-10-05", effective_from="2026-10-04")

        assert "only one can be in force" in legal.problems()[0]

    def test_a_translation_of_nothing(self, texts):
        texts("statutes", "2019-03-17")
        texts("statutes", "2026-10-04", language="en")

        assert "no published German file" in legal.problems()[0]


class TestRendering:
    def test_front_matter_is_not_shown_and_paragraphs_get_anchors(self, texts):
        texts("statutes", "2019-03-17", "# Statuten des Vereins\n\n## §1. Name\n\nText.\n\n## Schluss\n",
              title="Statuten")
        version = legal.current_version("statutes", today=date(2026, 1, 1))

        rendered = legal.render(version)

        assert version.title == "Statuten"
        assert "<h1" not in rendered["html"] and "effective_from" not in rendered["html"]
        assert '<h2 id="paragraph-1">' in rendered["html"]
        assert rendered["contents"] == [("paragraph-1", "§1. Name", 2), ("schluss", "Schluss", 2)]

    def test_parts_further_down_are_headings_of_their_own_and_in_the_contents(self, texts):
        texts("privacy-policy", "2026-10-04", "Einleitung.\n\n## 1. Allgemein\n\n---\n\n# Teil B – Portal\n\n## 2. Konto\n")

        rendered = legal.render(legal.current_version("privacy-policy", today=date(2026, 10, 4)))

        assert '<h1 id="teil-b-portal" class="legal-part">' in rendered["html"]
        assert [level for _anchor, _label, level in rendered["contents"]] == [2, 1, 2]

    def test_the_texts_own_revision_is_shown_with_the_date(self, client, texts, legal_dir):
        (legal_dir / "statutes" / "de").mkdir(parents=True)
        (legal_dir / "statutes" / "de" / "2019-03-17.md").write_text(
            '---\ntitle: "Statuten"\ndocument: "statutes"\nlanguage: "de"\nversion: "2019-03-17"\n'
            'effective_from: "2019-03-17"\nstatus: "published"\nsource_revision: "Rev 1"\n---\n\nText.\n',
            encoding="utf-8")

        body = client.get("/legal/statutes").get_data(as_text=True)

        assert "Version of 17.03.2019 (Rev 1)" in body

    def test_html_in_a_text_is_shown_as_text_never_run(self, texts):
        texts("privacy-policy", "2026-10-04", "<script>alert(1)</script>\n\n[x](javascript:alert(1))")

        html = legal.render(legal.current_version("privacy-policy", today=date(2026, 10, 4)))["html"]

        assert "<script>" not in html and "&lt;script&gt;" in html
        assert 'href="javascript:' not in html


class TestThePages:
    def test_the_list(self, client, texts):
        texts("statutes", "2019-03-17")
        texts("privacy-policy", "2026-10-04")

        body = client.get("/legal").get_data(as_text=True)

        assert 'href="/legal/statutes"' in body and 'href="/legal/privacy-policy"' in body
        assert "/legal/membership-terms" not in body

    def test_the_english_translation_first_saying_german_applies(self, client, texts):
        texts("privacy-policy", "2026-10-04", "Deutscher Text.", title="Datenschutzerklärung")
        texts("privacy-policy", "2026-10-04", "English text.", language="en", title="Privacy Policy")

        body = client.get("/legal/privacy-policy").get_data(as_text=True)

        assert "English text." in body and "Privacy Policy" in body and TRANSLATION_NOTICE in body
        assert 'href="/legal/privacy-policy/de"' in body
        german = client.get("/legal/privacy-policy/de").get_data(as_text=True)
        assert "Deutscher Text." in german and TRANSLATION_NOTICE not in german
        assert 'href="/legal/privacy-policy/en"' in german

    def test_german_alone_where_there_is_no_translation(self, client, texts):
        texts("statutes", "2019-03-17", "Deutscher Text.")

        body = client.get("/legal/statutes").get_data(as_text=True)

        assert "Deutscher Text." in body and TRANSLATION_NOTICE not in body and "In German only." in body
        assert client.get("/legal/statutes/en").headers["Location"].endswith("/legal/statutes/de")

    def test_an_outdated_translation_is_not_passed_off_as_current(self, client, texts):
        texts("privacy-policy", "2026-01-01", "Alt.")
        texts("privacy-policy", "2026-01-01", "Old English.", language="en")
        texts("privacy-policy", "2026-09-01", "Neu.")

        body = client.get("/legal/privacy-policy").get_data(as_text=True)

        assert "Neu." in body and "Old English." not in body
        assert "No English translation of this version yet." in body
        old = client.get("/legal/privacy-policy/en/2026-01-01").get_data(as_text=True)
        assert "Old English." in old and "No longer in force" in old and TRANSLATION_NOTICE in old

    def test_the_version_in_force_with_the_earlier_ones_linked(self, client, texts):
        texts("statutes", "2019-03-17", "Alt.")
        texts("statutes", "2026-01-01", "Neu.")

        body = client.get("/legal/statutes").get_data(as_text=True)

        assert "Neu." in body and "Alt." not in body
        assert 'href="/legal/statutes/de/2019-03-17"' in body

    def test_an_earlier_version_says_it_is_no_longer_in_force(self, client, texts):
        texts("statutes", "2019-03-17", "Alt.")
        texts("statutes", "2026-01-01", "Neu.")

        body = client.get("/legal/statutes/de/2019-03-17").get_data(as_text=True)

        assert "Alt." in body and "No longer in force" in body

    def test_a_version_not_yet_in_force_or_a_draft_is_not_shown(self, client, texts):
        texts("statutes", "2019-03-17")
        texts("statutes", "2999-01-01", "Geheim.")
        texts("statutes", "2020-01-01", "Entwurf.", status="draft")

        assert client.get("/legal/statutes/de/2999-01-01").status_code == 404
        assert client.get("/legal/statutes/de/2020-01-01").status_code == 404
        body = client.get("/legal/statutes").get_data(as_text=True)
        assert "Geheim." not in body and "Entwurf." not in body

    @pytest.mark.parametrize("path", [
        "/legal/nonsense", "/legal/privacy-policy", "/legal/statutes/fr", "/legal/statutes/de/2020-01-01",
        "/legal/statutes/de/yesterday", "/legal/statutes/en/2019-03-17",
    ])
    def test_what_does_not_exist(self, client, texts, path):
        texts("statutes", "2019-03-17")

        assert client.get(path).status_code == 404

    def test_the_text_alone_for_the_window_over_the_form(self, client, texts):
        texts("privacy-policy", "2026-10-04", "Text.")
        texts("privacy-policy", "2026-10-04", "English.", language="en")

        body = client.get("/legal/privacy-policy?part=body").get_data(as_text=True)

        assert "English." in body and TRANSLATION_NOTICE in body
        assert "<html" not in body and "site-footer" not in body


class TestAtSignup:
    def test_each_text_is_linked_to_open_over_the_form(self, client, texts):
        texts("statutes", "2019-03-17")
        texts("privacy-policy", "2026-10-04")
        texts("legal-notice", "2026-10-04")

        body = client.get("/join").get_data(as_text=True)
        tick = body.split('name="terms_accepted"')[1].split("</label>")[0]

        for slug in ("statutes", "privacy-policy"):
            assert f'<a href="/legal/{slug}" target="_blank" rel="noopener" data-legal-dialog' in tick
        assert "/legal/legal-notice" not in tick
        assert "legal-dialog.js" in body

    def test_the_versions_ticked_are_kept(self, app, client, texts, monkeypatch):
        from aeronautics_members.blueprints import _signup, public

        texts("statutes", "2019-03-17")
        texts("privacy-policy", "2026-10-04")
        texts("privacy-policy", "2026-10-04", language="en")
        checkout = lambda member: (types.SimpleNamespace(url="https://checkout.stripe.test/s"), {})  # noqa: E731
        monkeypatch.setattr(_signup, "create_checkout_session_for_member", checkout)
        monkeypatch.setattr(public, "create_checkout_session_for_member", checkout)
        monkeypatch.setattr(_signup, "send_email_verification_email", lambda *a, **k: True)
        monkeypatch.setattr(_signup, "send_work_email_verification_email", lambda *a, **k: True)

        client.post("/process-membership", data={
            "salutation": "Ms", "first_name": "Lea", "last_name": "Legal", "street": "Main",
            "house_number": "1", "postal_code": "8010", "city": "Graz", "country": "Austria",
            "phone_private": "+43123", "email_private": "lea@example.com",
            "email_work": "lea.legal@edu.fh-joanneum.at", "member_category": "student",
            "year_group": "LAV25", "password": "right-password", "confirm_password": "right-password",
            "payment_method": "checkout", "terms_accepted": "y",
        })

        member = db.session.query(Member).filter_by(email_private="lea@example.com").one()
        assert member.legal_versions_accepted == {"statutes": "2019-03-17", "privacy-policy": "2026-10-04"}
        assert member.legal_accepted_at is not None
        profile = privacy.export_account_data(member.user)["member_profile"]
        assert profile["legal_texts_accepted"] == member.legal_versions_accepted


class TestTheTextsInTheRepository:
    """What is in legal/ -- checked here so a mistake fails the build, not the page."""

    def test_every_file_is_in_order(self, app):
        app.config["LEGAL_TEXTS_DIR"] = str(REPO_TEXTS)

        assert legal.problems() == []

    def test_every_published_version_renders(self, app):
        app.config["LEGAL_TEXTS_DIR"] = str(REPO_TEXTS)
        for text in legal.LEGAL_TEXTS:
            for language in legal.LANGUAGES:
                for version in legal.versions(text.slug, language, today=date(9999, 12, 31)):
                    assert legal.render(version)["html"], version.path

    def test_the_texts_accepted_at_signup_are_there(self, app):
        app.config["LEGAL_TEXTS_DIR"] = str(REPO_TEXTS)
        for text in legal.LEGAL_TEXTS:
            if text.accepted_at_signup:
                assert legal.versions(text.slug, today=date(9999, 12, 31)), f"legal/{text.slug}/de/ has nothing published"


def test_lettered_points_start_on_a_line_of_their_own():
    """ "a. ..." under a numbered point is not a Markdown list: without a line
    break (two spaces) at the end of the line before, the first one is glued to it."""
    import re

    glued = []
    for path in sorted(REPO_TEXTS.rglob("*.md")):
        lines = path.read_text(encoding="utf-8").split("\n")
        for number, (before, line) in enumerate(zip(lines, lines[1:]), start=2):
            if (re.match(r"^\s+[a-z]\. ", line) and before.strip() and not before.endswith("  ")
                    and not re.match(r"^\s+[a-z]\. ", before)):
                glued.append(f"{path.relative_to(REPO_TEXTS)}:{number}")
    assert glued == []
