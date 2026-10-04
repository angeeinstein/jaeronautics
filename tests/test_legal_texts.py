"""The legal texts: Markdown files in legal/<text>/<day>.md, the newest in force shown.

A version is put in early and switches over on its day; older ones stay
readable. At signup every text accepted then is linked from the one tick --
opening over the form, so nothing typed is lost -- and the versions ticked are
kept with the member.
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


@pytest.fixture
def texts(app, tmp_path):
    """An empty legal/ folder of the test's own; ``put`` writes a version into it."""
    app.config["LEGAL_TEXTS_DIR"] = str(tmp_path)

    def put(slug, day, body):
        folder = tmp_path / slug
        folder.mkdir(exist_ok=True)
        (folder / f"{day}.md").write_text(body, encoding="utf-8")

    return put


class TestWhichVersion:
    def test_the_newest_whose_day_has_come(self, texts):
        texts("statutes", "2019-03-17", "# Statuten\n\nAlt.")
        texts("statutes", "2026-01-01", "# Statuten\n\nNeu.")
        texts("statutes", "2027-01-01", "# Statuten\n\nKommt noch.")

        assert legal.current_version("statutes", today=date(2026, 6, 1)) == date(2026, 1, 1)
        assert legal.current_version("statutes", today=date(2027, 1, 1)) == date(2027, 1, 1)
        assert legal.current_version("statutes", today=date(2019, 1, 1)) is None

    def test_other_files_in_the_folder_are_not_versions(self, texts, tmp_path):
        texts("privacy", "2026-10-04", "# Datenschutzerklärung")
        (tmp_path / "privacy" / "notes.md").write_text("draft")
        (tmp_path / "privacy" / "2026-13-01.md").write_text("no such day")

        assert legal.versions("privacy") == [date(2026, 10, 4)]

    def test_a_text_without_a_file_is_not_shown(self, texts):
        texts("statutes", "2019-03-17", "# Statuten")

        assert [text.slug for text, _ in legal.available(today=date(2026, 1, 1))] == ["statutes"]
        assert legal.versions_to_accept(today=date(2026, 1, 1)) == {"statutes": "2019-03-17"}

    def test_only_the_texts_accepted_at_signup_are_to_accept(self, texts):
        texts("privacy", "2026-10-04", "# Datenschutzerklärung")
        texts("legal-notice", "2026-10-04", "# Impressum")

        assert legal.versions_to_accept(today=date(2026, 10, 4)) == {"privacy": "2026-10-04"}


class TestRendering:
    def test_the_heading_is_the_title_and_paragraphs_get_anchors(self, texts):
        texts("statutes", "2019-03-17", "# Statuten des Vereins\n\n## §1. Name\n\nText.\n\n## Schluss\n")

        rendered = legal.render("statutes", date(2019, 3, 17))

        assert rendered["title"] == "Statuten des Vereins"
        assert "<h1" not in rendered["html"]
        assert '<h2 id="paragraph-1">' in rendered["html"]
        assert rendered["contents"] == [("paragraph-1", "§1. Name"), ("schluss", "Schluss")]

    def test_html_in_a_text_is_shown_as_text_never_run(self, texts):
        texts("privacy", "2026-10-04", '# Datenschutz\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1))')

        html = legal.render("privacy", date(2026, 10, 4))["html"]

        assert "<script>" not in html and "&lt;script&gt;" in html
        assert 'href="javascript:' not in html


class TestThePages:
    def test_the_list(self, client, texts):
        texts("statutes", "2019-03-17", "# Statuten")
        texts("privacy", "2026-10-04", "# Datenschutzerklärung")

        body = client.get("/legal").get_data(as_text=True)

        assert 'href="/legal/statutes"' in body and 'href="/legal/privacy"' in body
        assert "/legal/membership-terms" not in body

    def test_the_version_in_force_with_the_earlier_ones_linked(self, client, texts):
        texts("statutes", "2019-03-17", "# Statuten\n\nAlt.")
        texts("statutes", "2026-01-01", "# Statuten\n\nNeu.")

        body = client.get("/legal/statutes").get_data(as_text=True)

        assert "Neu." in body and "Alt." not in body
        assert 'href="/legal/statutes/2019-03-17"' in body

    def test_an_earlier_version_says_it_is_no_longer_in_force(self, client, texts):
        texts("statutes", "2019-03-17", "# Statuten\n\nAlt.")
        texts("statutes", "2026-01-01", "# Statuten\n\nNeu.")

        body = client.get("/legal/statutes/2019-03-17").get_data(as_text=True)

        assert "Alt." in body and "No longer in force" in body

    def test_a_version_not_yet_in_force_is_not_shown(self, client, texts):
        texts("statutes", "2019-03-17", "# Statuten")
        texts("statutes", "2999-01-01", "# Statuten\n\nGeheim.")

        assert client.get("/legal/statutes/2999-01-01").status_code == 404
        assert "Geheim." not in client.get("/legal/statutes").get_data(as_text=True)

    @pytest.mark.parametrize("path", [
        "/legal/nonsense", "/legal/privacy", "/legal/statutes/2020-01-01", "/legal/statutes/yesterday",
    ])
    def test_what_does_not_exist(self, client, texts, path):
        texts("statutes", "2019-03-17", "# Statuten")

        assert client.get(path).status_code == 404

    def test_the_text_alone_for_the_window_over_the_form(self, client, texts):
        texts("privacy", "2026-10-04", "# Datenschutzerklärung\n\nText.")

        body = client.get("/legal/privacy?part=body").get_data(as_text=True)

        assert "Text." in body and "<html" not in body and "site-footer" not in body


class TestAtSignup:
    def test_each_text_is_linked_to_open_over_the_form(self, client, texts):
        texts("statutes", "2019-03-17", "# Statuten")
        texts("privacy", "2026-10-04", "# Datenschutzerklärung")
        texts("legal-notice", "2026-10-04", "# Impressum")

        body = client.get("/join").get_data(as_text=True)
        tick = body.split('name="terms_accepted"')[1].split("</label>")[0]

        for slug in ("statutes", "privacy"):
            assert f'<a href="/legal/{slug}" target="_blank" rel="noopener" data-legal-dialog' in tick
        assert "/legal/legal-notice" not in tick
        assert "legal-dialog.js" in body

    def test_the_versions_ticked_are_kept(self, app, client, texts, monkeypatch):
        from aeronautics_members.blueprints import _signup, public

        texts("statutes", "2019-03-17", "# Statuten")
        texts("privacy", "2026-10-04", "# Datenschutzerklärung")
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
        assert member.legal_versions_accepted == {"statutes": "2019-03-17", "privacy": "2026-10-04"}
        assert member.legal_accepted_at is not None
        profile = privacy.export_account_data(member.user)["member_profile"]
        assert profile["legal_texts_accepted"] == member.legal_versions_accepted


class TestTheTextsInTheRepository:
    """What is in legal/ -- checked here so a mistake fails the build, not the page."""

    def test_every_file_is_a_version_of_a_known_text(self):
        for path in REPO_TEXTS.rglob("*"):
            if path.is_file():
                relative = path.relative_to(REPO_TEXTS)
                assert len(relative.parts) == 2 and relative.parts[0] in legal.BY_SLUG, relative
                match = legal.VERSION_FILE.match(path.name)
                assert match, f"{relative}: name it <YYYY-MM-DD>.md"
                date.fromisoformat(match.group(1))

    def test_every_version_renders_with_a_title(self, app):
        app.config["LEGAL_TEXTS_DIR"] = str(REPO_TEXTS)
        for text in legal.LEGAL_TEXTS:
            for version in legal.versions(text.slug):
                assert (REPO_TEXTS / text.slug / f"{version}.md").read_text(encoding="utf-8").startswith("# ")
                assert legal.render(text.slug, version)["html"]

    def test_the_texts_accepted_at_signup_are_there(self, app):
        app.config["LEGAL_TEXTS_DIR"] = str(REPO_TEXTS)
        for text in legal.LEGAL_TEXTS:
            if text.accepted_at_signup:
                assert legal.versions(text.slug), f"legal/{text.slug}/ has no version"
