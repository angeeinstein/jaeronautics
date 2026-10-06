"""The legal texts' PDFs in the welcome emails, and a preview of a new text.

Behind a switch (Settings -> General): the association's welcome email
carries the texts the member accepted at signup, in the version they accepted;
"Welcome to <team>" carries the team's rules they accepted. A PDF that cannot
be made is left out, never the email.

The preview (Admin -> Legal Texts): upload the Markdown of a new version, get
it checked as CI checks it and back as its PDF, marked ENTWURF or VORSCHAU on
every page. Nothing is kept.
"""
import html
import io
from pathlib import Path
from datetime import datetime

import pytest

from conftest import db, make_member
from aeronautics_members import legal_pdf, mail_utils
from aeronautics_members.db_models import NotificationEvent, Setting
from aeronautics_members.services import legal_texts as legal
from aeronautics_members.services import teams, workflows
from test_admin_reviews import _login, _staff
from test_emails import _parts, outbox  # noqa: F401 -- fixture
from test_legal_texts import legal_dir, texts  # noqa: F401 -- fixtures
from test_team_rules import TODAY, rules_file  # noqa: F401 -- fixture
from test_teams_flow import _led, _person, switched_on  # noqa: F401



def _switch(on=True):
    db.session.merge(Setting(key=legal_pdf.SETTING_KEY, value=str(on)))
    db.session.commit()


@pytest.fixture
def quick_pdfs(monkeypatch):
    """PDFs without laying them out: the tests here are about which go where."""
    made = []

    def fake(german, team=None):
        made.append((german.slug, german.version, team))
        return b"%PDF-1.7 " + german.slug.encode()

    monkeypatch.setattr(legal_pdf, "pdf_for", fake)
    return made


class TestSendMail:
    def test_files_go_beside_the_body_and_its_pictures(self, app, outbox):  # noqa: F811
        with app.test_request_context():
            ok, _error = mail_utils.send_mail(
                from_account="office", to_email="anna@example.com", subject="Hi", body="<p>Hello</p>",
                attachments=None, return_error=True,
                files=[{"filename": "Statuten_2019-03-17.pdf", "data": b"%PDF-1.7 x", "mimetype": "application/pdf"}],
            )

        message = outbox[-1]
        assert ok and message.get_content_type() == "multipart/mixed"
        [pdf] = _parts(message, "application/pdf")
        assert pdf.get_filename() == "Statuten_2019-03-17.pdf" and pdf.get_payload(decode=True) == b"%PDF-1.7 x"
        assert _parts(message, "multipart/related") and _parts(message, "text/html")

    def test_without_files_as_before(self, app, outbox):  # noqa: F811
        with app.test_request_context():
            mail_utils.send_mail(from_account="office", to_email="anna@example.com", subject="Hi",
                                 body="<p>Hello</p>", return_error=True)

        assert outbox[-1].get_content_type() == "multipart/related"


class TestWhichVersions:
    def test_the_versions_the_member_accepted(self, app, texts, quick_pdfs):  # noqa: F811
        texts("statutes", "2019-03-17", title="Statuten")
        texts("statutes", "2026-01-01", title="Statuten")
        texts("privacy-policy", "2026-01-01", title="Datenschutzerklärung")
        texts("legal-notice", "2026-01-01", title="Impressum")
        member = make_member()
        member.legal_versions_accepted = {"statutes": "2019-03-17", "privacy-policy": "2026-01-01"}

        files = legal_pdf.files_for_member(member)

        assert [f["filename"] for f in files] == [
            "Joanneum-Aeronautics_Statuten_2019-03-17.pdf", "Joanneum-Aeronautics_Datenschutzerklaerung_2026-01-01.pdf"]

    def test_before_versions_were_kept_the_one_in_force_on_their_signup_day(self, app, texts, quick_pdfs):  # noqa: F811
        texts("statutes", "2019-03-17", title="Statuten")
        texts("statutes", "2026-01-01", title="Statuten")
        member = make_member()
        member.legal_versions_accepted, member.created_at = None, datetime(2025, 5, 1)

        [document] = legal_pdf.files_for_member(member)

        assert document["filename"].endswith("_2019-03-17.pdf")

    def test_one_that_cannot_be_made_is_left_out(self, app, texts, monkeypatch):  # noqa: F811
        texts("statutes", "2019-03-17", title="Statuten")
        monkeypatch.setattr(legal_pdf, "pdf_for", lambda german, team=None: 1 / 0)

        assert legal_pdf.files_for_member(make_member()) == []


def _welcome_settings():
    for key, value in {"automatic_emails_enabled": "True", "automatic_email_template": "welcome_email.html"}.items():
        db.session.merge(Setting(key=key, value=value))
    db.session.commit()


class TestTheWelcomeEmail:
    def test_carries_them_when_switched_on(self, app, outbox, texts, quick_pdfs):  # noqa: F811
        texts("statutes", "2019-03-17", title="Statuten")
        _welcome_settings()
        _switch(True)
        member = make_member()

        with app.test_request_context():
            assert workflows.send_member_welcome_email(app, member)

        message = outbox[-1]
        assert [part.get_filename() for part in _parts(message, "application/pdf")] == [
            "Joanneum-Aeronautics_Statuten_2019-03-17.pdf"]
        text = _parts(message, "text/plain")[0].get_payload(decode=True).decode()
        assert "Attached for your records" in text and "Statuten" in text

    def test_not_when_switched_off(self, app, outbox, texts, quick_pdfs):  # noqa: F811
        texts("statutes", "2019-03-17", title="Statuten")
        _welcome_settings()
        member = make_member()

        with app.test_request_context():
            workflows.send_member_welcome_email(app, member)

        assert not _parts(outbox[-1], "application/pdf") and not quick_pdfs


@pytest.mark.usefixtures("switched_on")
class TestWelcomeToTheTeam:
    def _approved(self, rules_file):  # noqa: F811
        team, _lead = _led(admission_mode="open")
        rules_file(team="rocket")
        membership = teams.join_or_apply(_person(), team, accepted_terms=True)
        db.session.commit()
        return NotificationEvent(
            channel="user_status", audience="user", event_type="team_approved", summary="x",
            payload={"first_name": "Anna", "team_name": team.name, "team_slug": team.slug},
            recipient_email="anna@example.com", object_type="team_membership", object_id=membership.id,
        )

    def _send(self, app, event):
        from aeronautics_members.services.notifications import get_notification_service

        service = get_notification_service()
        with app.test_request_context():
            subject, template_vars = service._build_user_status_message(event)
            return service._send_user_status_mail(event, subject, template_vars)

    def test_carries_the_rules_accepted(self, app, outbox, rules_file, quick_pdfs):  # noqa: F811
        event = self._approved(rules_file)
        _switch(True)

        assert self._send(app, event) == (True, None)

        [pdf] = _parts(outbox[-1], "application/pdf")
        assert pdf.get_filename() == f"Rocket_Teamordnung_{TODAY.isoformat()}.pdf"
        assert "rules of the team you accepted" in _parts(outbox[-1], "text/plain")[0].get_payload(decode=True).decode()

    def test_not_when_switched_off(self, app, outbox, rules_file, quick_pdfs):  # noqa: F811
        event = self._approved(rules_file)

        self._send(app, event)

        assert not _parts(outbox[-1], "application/pdf")


class TestTheSwitch:
    def test_set_on_the_general_settings_and_kept_by_the_others(self, app, client):
        _login(client, _staff("boss@example.org", "admin").id)

        client.post("/admin/settings", data={"save_settings": "1", "settings_section": "general",
                                             "legal_pdfs_in_welcome_emails": "on"})
        assert legal_pdf.attach_to_welcome_emails()

        client.post("/admin/settings", data={"save_settings": "1", "settings_section": "notifications"})
        assert legal_pdf.attach_to_welcome_emails()

        client.post("/admin/settings", data={"save_settings": "1", "settings_section": "general"})
        assert not legal_pdf.attach_to_welcome_emails()


def _md(document="statutes", language="de", version="2026-10-05", status="draft", team=None, body="## § 1 Name\n\nText."):
    team_line = f'team: "{team}"\n' if team else ""
    return (f'---\ntitle: "Statuten"\ndocument: "{document}"\nlanguage: "{language}"\n{team_line}'
            f'version: "{version}"\neffective_from: "{version}"\nstatus: "{status}"\n---\n\n{body}\n').encode()


class TestThePreview:
    def _post(self, client, german, english=None, german_name="2026-10-05.md", english_name="2026-10-05.md"):
        data = {"german": (io.BytesIO(german), german_name)}
        if english is not None:
            data["english"] = (io.BytesIO(english), english_name)
        return client.post("/admin/legal", data=data, content_type="multipart/form-data")

    def test_a_pdf_of_the_upload_marked_as_a_draft_and_nothing_kept(self, app, client, legal_dir):  # noqa: F811
        _login(client, _staff("boss@example.org", "admin").id)

        response = self._post(client, _md(), _md(language="en", body="## § 1 Name\n\nText in English."))

        assert response.status_code == 200 and response.mimetype == "application/pdf"
        assert response.data.startswith(b"%PDF")
        assert "VORSCHAU_Joanneum-Aeronautics_Statuten_2026-10-05.pdf" in response.headers["Content-Disposition"]
        assert list(legal_dir.iterdir()) == [] and legal.current_version("statutes") is None
        # The preview's files are gone, and nothing read from them is kept either.
        assert not [key for key in [*legal._parsed, *legal._rendered] if not Path(key[0]).exists()]

    @pytest.mark.parametrize("status, word", [("draft", "ENTWURF"), ("published", "VORSCHAU")])
    def test_the_word_across_every_page(self, app, client, monkeypatch, status, word):
        seen = {}
        monkeypatch.setattr(legal_pdf, "build", lambda german, team=None, versions=None, watermark=None:
                            seen.update(watermark=watermark, versions=versions) or b"%PDF-1.7 x")
        _login(client, _staff("boss@example.org", "admin").id)

        self._post(client, _md(status=status))

        assert seen["watermark"] == word and len(seen["versions"]) == 1

    @pytest.mark.parametrize("german, english, english_name, problem", [
        (b"no front matter", None, None, "no front matter"),
        (_md(document="unknown"), None, None, 'document "unknown" is not a text there is'),
        (_md(language="en"), None, None, 'this file must be "de"'),
        (_md(), _md(language="en", version="2026-10-06"), "2026-10-06.md", "must be the translation"),
        (_md(version="2026-10-06"), None, None, "but the file is named 2026-10-05.md"),
    ])
    def test_what_ci_would_refuse_is_listed_instead(self, app, client, german, english, english_name, problem):
        _login(client, _staff("boss@example.org", "admin").id)

        response = self._post(client, german, english, english_name=english_name or "2026-10-05.md")

        assert response.status_code == 200 and response.mimetype == "text/html"
        assert problem in html.unescape(response.get_data(as_text=True))

    def test_a_teams_rules_with_its_logo(self, app, client, monkeypatch):
        seen = {}
        monkeypatch.setattr(legal_pdf, "build", lambda german, team=None, versions=None, watermark=None:
                            seen.update(team=team) or b"%PDF-1.7 x")
        team, _lead = _led()
        _login(client, _staff("boss@example.org", "admin").id)

        response = self._post(client, _md(document="team-rules", team="rocket"))

        assert response.mimetype == "application/pdf" and seen["team"] == team


class TestTheTemplate:
    def test_dated_today_and_previewed_as_it_is(self, app, client):
        _login(client, _staff("boss@example.org", "admin").id)

        response = client.get("/admin/legal/template")
        name = f"{TODAY.isoformat()}.md"

        assert response.mimetype == "text/markdown" and f'filename="{name}"' in response.headers["Content-Disposition"]
        text = response.get_data(as_text=True)
        assert text.startswith("---\n") and f'version: "{TODAY.isoformat()}"' in text and "{{" not in text
        preview = client.post("/admin/legal", data={"german": (io.BytesIO(response.data), name)},
                              content_type="multipart/form-data")
        assert preview.mimetype == "application/pdf"

    def test_shows_every_kind_of_markup(self, app, client):
        _login(client, _staff("boss@example.org", "admin").id)
        text = client.get("/admin/legal/template").get_data(as_text=True)

        for shown in ("## § 1", "### ", "**bold**", "](https://", "1. ", "- ", "  \n   a. ", "|---|", "> ", "\n---\n",
                      "\n# Teil B", "source_revision", "team:"):
            assert shown in text, shown


class TestNotInForceYet:
    def test_drafts_and_versions_waiting_for_their_day_are_listed(self, app, client, texts, rules_file):  # noqa: F811
        texts("statutes", "2019-03-17", title="Statuten")
        texts("statutes", "2026-01-01", title="Statuten", status="draft")
        texts("privacy-policy", "2099-01-01", title="Datenschutzerklärung")
        texts("privacy-policy", "2099-01-01", title="Privacy Policy", language="en")
        _led()
        rules_file(team="rocket", status="draft")
        _login(client, _staff("boss@example.org", "admin").id)

        body = client.get("/admin/legal").get_data(as_text=True)
        waiting = body.split('id="legal-waiting"')[1].split('class="card"')[0]

        assert "Statuten" in waiting and "Draft" in waiting
        assert "Datenschutzerklärung" in waiting and "From 01.01.2099" in waiting and "with English" in waiting
        assert "Teamordnung" in waiting and "Rocket" in waiting
        assert "2019-03-17" not in waiting  # in force, so above
        assert "document=statutes&amp;version=2026-01-01" in waiting
        assert "document=team-rules&amp;version=" in waiting and "team=rocket" in waiting

    def test_their_pdf_marked_and_with_the_english(self, app, client, texts, monkeypatch):  # noqa: F811
        texts("privacy-policy", "2099-01-01", title="Datenschutzerklärung", status="draft")
        texts("privacy-policy", "2099-01-01", title="Privacy Policy", language="en", status="draft")
        seen = {}
        monkeypatch.setattr(legal_pdf, "build", lambda german, team=None, versions=None, watermark=None:
                            seen.update(watermark=watermark, languages=[v.language for v in versions])
                            or b"%PDF-1.7 x")
        _login(client, _staff("boss@example.org", "admin").id)

        response = client.get("/admin/legal/waiting.pdf?document=privacy-policy&version=2099-01-01")

        assert response.mimetype == "application/pdf" and "ENTWURF_" in response.headers["Content-Disposition"]
        assert seen == {"watermark": "ENTWURF", "languages": ["de", "en"]}

    @pytest.mark.parametrize("query", ["document=unknown&version=2099-01-01", "document=statutes&version=x",
                                       "document=statutes&version=2001-01-01", "document=statutes&version=2099-01-01&team=rocket"])
    def test_what_is_not_there(self, app, client, texts, query):  # noqa: F811
        texts("statutes", "2099-01-01", title="Statuten", status="draft")
        _login(client, _staff("boss@example.org", "admin").id)

        assert client.get(f"/admin/legal/waiting.pdf?{query}").status_code == 404

    def test_nothing_waiting(self, app, client, texts):  # noqa: F811
        texts("statutes", "2019-03-17", title="Statuten")
        _login(client, _staff("boss@example.org", "admin").id)

        assert "No drafts, and no versions waiting" in client.get("/admin/legal").get_data(as_text=True)


class TestThePage:
    def test_lists_what_is_in_force_and_what_is_wrong(self, app, client, texts, legal_dir, rules_file):  # noqa: F811
        texts("statutes", "2019-03-17", title="Statuten")
        rules_file(team="ghost")
        (legal_dir / "stray.md").write_text("x")
        _login(client, _staff("boss@example.org", "admin").id)

        body = client.get("/admin/legal").get_data(as_text=True)

        assert 'href="/legal/statutes/pdf"' in body
        assert "stray.md: not where a version goes" in body
        assert "legal/teams/ghost/: no team has this short name" in body

    def test_not_for_a_member(self, app, client):
        member = make_member()
        _login(client, member.user.id)

        assert client.get("/admin/legal").status_code in (302, 403)


class TestMakingThemAllAgain:
    """Admin -> Legal Texts -> "Make all PDFs again": one PDF per request, ticked off by the page."""

    def test_listed_one_line_each(self, app, client, texts):  # noqa: F811
        texts("statutes", "2019-03-17", title="Statuten")
        _login(client, _staff("boss@example.org", "admin").id)

        body = client.get("/admin/legal").get_data(as_text=True)
        progress = body.split('id="legal-pdfs-progress"')[1].split("</ul>")[0]

        assert 'data-item="stored"' in progress and 'data-item="/statutes/2019-03-17"' in progress
        assert "legal-pdf-remake.js" in body

    def test_kept_ones_removed_then_each_made(self, app, client, texts, monkeypatch):  # noqa: F811
        texts("statutes", "2019-03-17", title="Statuten")
        made = []
        monkeypatch.setattr(legal_pdf, "build", lambda german, team=None, **_: made.append(german.slug) or b"%PDF-1.7 x")
        _login(client, _staff("boss@example.org", "admin").id)
        legal_pdf.pdf_for(legal.current_version("statutes", today=TODAY))

        stored = client.post("/admin/legal/pdfs/remake", data={"item": "stored"}).get_json()
        assert stored["state"] == "ok" and stored["detail"] == "1 removed"
        assert not list(legal_pdf.cache_dir().glob("*.pdf"))

        one = client.post("/admin/legal/pdfs/remake", data={"item": "/statutes/2019-03-17"}).get_json()
        assert one == {"state": "ok", "detail": "1 KB"}
        assert made == ["statutes", "statutes"]

    def test_one_that_cannot_be_made_says_why(self, app, client, texts, monkeypatch):  # noqa: F811
        texts("statutes", "2019-03-17", title="Statuten")
        monkeypatch.setattr(legal_pdf, "build", lambda *a, **k: 1 / 0)
        _login(client, _staff("boss@example.org", "admin").id)

        answer = client.post("/admin/legal/pdfs/remake", data={"item": "/statutes/2019-03-17"}).get_json()

        assert answer["state"] == "failed" and "division" in answer["detail"]

    def test_one_no_longer_there(self, app, client, texts):  # noqa: F811
        _login(client, _staff("boss@example.org", "admin").id)

        response = client.post("/admin/legal/pdfs/remake", data={"item": "/statutes/1999-01-01"})

        assert response.status_code == 404 and response.get_json()["state"] == "failed"

    def test_without_the_script_all_in_one_go(self, app, client, texts, monkeypatch):  # noqa: F811
        texts("statutes", "2019-03-17", title="Statuten")
        monkeypatch.setattr(legal_pdf, "build", lambda german, team=None, **_: b"%PDF-1.7 x")
        _login(client, _staff("boss@example.org", "admin").id)

        response = client.post("/admin/legal/pdfs/remake", follow_redirects=True)

        assert "All 1 PDFs were made again." in response.get_data(as_text=True)
        assert len(list(legal_pdf.cache_dir().glob("statutes_*.pdf"))) == 1

    def test_only_for_those_who_manage_the_settings(self, app, client, texts):  # noqa: F811
        texts("statutes", "2019-03-17", title="Statuten")
        _login(client, make_member(email="someone@example.org").user.id)

        assert client.post("/admin/legal/pdfs/remake", data={"item": "stored"}).status_code in (302, 403)


class TestTheDraftsPdfMadeFirst:
    def test_prepare_then_open(self, app, client, texts, monkeypatch):  # noqa: F811
        texts("statutes", "2099-01-01", title="Statuten", status="draft")
        made = []
        monkeypatch.setattr(legal_pdf, "build", lambda german, team=None, **_: made.append(1) or b"%PDF-1.7 x")
        _login(client, _staff("boss@example.org", "admin").id)

        url = client.get("/admin/legal/waiting.pdf?document=statutes&version=2099-01-01&prepare=1").get_json()["url"]
        response = client.get(url)

        assert "&v=" in url and response.mimetype == "application/pdf"
        assert made == [1]  # made once, opened from the kept file
