"""A team's rules as the association keeps its own legal texts.

legal/teams/<team>/team-rules/<language>/<day>.md, approved by the association,
versioned, German applying and English a translation, each version a PDF with
the team's logo. Applying or joining ticks the version in force, read in a
window over the form; the membership keeps its day.
"""
from datetime import datetime, timedelta

import pytest

from api_helpers import send

from conftest import db
from aeronautics_members import legal_pdf
from aeronautics_members.services import legal_texts as legal
from aeronautics_members.services import teams
from aeronautics_members.services.clock import get_membership_today
from test_legal_texts import legal_dir  # noqa: F401 -- fixture
from test_team_page import _png
from test_teams_flow import _led, _login, _person, switched_on  # noqa: F401
from test_teams_foundation import _in_team

API = "/api/v1/teams"
TODAY = get_membership_today()


@pytest.fixture
def rules_file(legal_dir):  # noqa: F811
    """``put(day, body, language, team=, status=)`` writes a version of a team's rules."""

    def put(day=TODAY.isoformat(), body="## § 1 Sicherheit\n\nEinweisung vor jedem Flug.", language="de",
            team="rocket", status="published", front_team=None):
        folder = legal_dir / "teams" / team / "team-rules" / language
        folder.mkdir(parents=True, exist_ok=True)
        title = "Teamordnung" if language == "de" else "Team Rules"
        (folder / f"{day}.md").write_text(
            f'---\ntitle: "{title}"\ndocument: "team-rules"\nlanguage: "{language}"\nteam: "{front_team or team}"\n'
            f'version: "{day}"\neffective_from: "{day}"\nstatus: "{status}"\n---\n\n{body}\n', encoding="utf-8")
        return folder / f"{day}.md"

    return put


@pytest.mark.usefixtures("switched_on")
class TestApplying:
    def test_ticked_in_a_window_over_the_form_with_the_pdf_beside(self, app, client, rules_file):
        _led()
        rules_file()
        _login(client, _person().id)

        body = client.get(f"{API}/rocket").get_json()
        dialog = client.get(f"{API}/rocket/rules").get_json()

        # The form ticks the version in force; the dialog over it shows that text, and its PDF.
        assert body["rules"]["version"] == TODAY.isoformat() and body["joining"] is not None
        assert dialog["version"] == TODAY.isoformat() and dialog["pdf_url"] == "/teams/rocket/rules/pdf"

    def test_the_version_ticked_is_kept_with_the_membership(self, app, client, rules_file):
        team, _lead = _led()
        rules_file()
        anna = _person()
        _login(client, anna.id)

        send(client, "POST", f"{API}/rocket/join", {})
        assert teams.ongoing_membership(anna, team) is None
        send(client, "POST", f"{API}/rocket/join", {"accept_rules": True})

        membership = teams.ongoing_membership(anna, team)
        assert membership.terms_version == datetime.combine(TODAY, datetime.min.time())

    def test_a_draft_or_another_teams_file_is_no_rules(self, app, client, rules_file):
        team, _lead = _led()
        rules_file(status="draft")
        rules_file(team="glider")

        assert teams.team_rules(team) is None


@pytest.mark.usefixtures("switched_on")
class TestReading:
    def test_the_rules_with_their_translation_and_the_way_back(self, app, client, rules_file):
        _led()
        rules_file()
        rules_file(language="en", body="## § 1 Safety\n\nBriefing before every flight.")
        _login(client, _person().id)

        english = client.get(f"{API}/rocket/rules").get_json()
        german = client.get(f"{API}/rocket/rules?language=de").get_json()

        assert "Briefing before every flight." in english["html"] and english["is_translation"] is True
        assert english["german_url"] == "/teams/rocket/rules/de"
        assert "Einweisung vor jedem Flug." in german["html"] and german["is_translation"] is False
        assert german["english_url"] == "/teams/rocket/rules/en" and german["pdf_has_english"] is True

    def test_its_page_is_the_apps(self, app, client, rules_file):
        _led()
        rules_file()
        _login(client, _person().id)

        for path in ("/teams/rocket/rules", "/teams/rocket/rules/de", f"/teams/rocket/rules/de/{TODAY}"):
            assert client.get(path).status_code == 200, path

    def test_no_html_of_its_own_gets_through(self, app, client, rules_file):
        _led()
        rules_file(body="<script>alert(1)</script>\n\nEinweisung vor jedem Flug.")
        _login(client, _person().id)

        html = client.get(f"{API}/rocket/rules").get_json()["html"]

        assert "<script>" not in html and "Einweisung vor jedem Flug." in html

    def test_an_earlier_version_and_a_team_without_rules(self, app, client, rules_file):
        _led()
        _led("Glider")
        earlier = (TODAY - timedelta(days=30)).isoformat()
        rules_file(day=earlier, body="Alt.")
        rules_file()
        _login(client, _person().id)

        older = client.get(f"{API}/rocket/rules?language=de&version={earlier}").get_json()
        assert "Alt." in older["html"] and older["in_force"] == TODAY.isoformat()
        assert client.get(f"{API}/glider/rules").status_code == 404
        assert client.get(f"{API}/rocket/rules?language=de&version=2001-01-01").status_code == 404

    def test_the_pdf_with_the_teams_logo(self, app, client, rules_file, tmp_path):
        team, _lead = _led()
        app.config["TEAM_LOGO_DIR"] = str(tmp_path / "logos")
        teams.set_team_logo(None, team, _png((300, 300)))
        db.session.commit()
        rules_file()
        _login(client, _person().id)

        response = client.get("/teams/rocket/rules/pdf")

        assert response.status_code == 200 and response.mimetype == "application/pdf"
        assert "Teamordnung" in response.headers["Content-Disposition"]
        german = teams.team_rules(team).version
        html = legal_pdf.render_template(
            legal_pdf.TEMPLATE, sections=legal_pdf._sections(german), german=german,
            association=legal_pdf.ASSOCIATION, team=team, team_logo="file:///logo.png")
        assert 'class="team-mark"' in html and "Rocket" in html

    def test_its_logo_is_the_one_file_outside_static_read(self, app):
        from pathlib import Path

        logo = Path("/tmp/some-team-logo.png")
        fetcher = legal_pdf._static_only_fetcher(logo)

        with pytest.raises(ValueError):
            fetcher.fetch("file:///tmp/another-file.png")


@pytest.mark.usefixtures("switched_on")
class TestMembers:
    def test_see_the_version_they_accepted_and_when_it_changed(self, app, client, rules_file):
        team, _lead = _led()
        earlier = TODAY - timedelta(days=30)
        rules_file(day=earlier.isoformat())
        anna = _person()
        _in_team(anna, team)
        membership = teams.active_team_membership(anna, team)
        membership.terms_version = datetime.combine(earlier, datetime.min.time())
        db.session.commit()
        _login(client, anna.id)

        assert client.get(f"{API}/rocket").get_json()["rules"]["changed_since"] is False

        rules_file()
        rules = client.get(f"{API}/rocket").get_json()["rules"]

        assert rules == {"version": TODAY.isoformat(), "accepted": earlier.isoformat(), "changed_since": True}


class TestTheFiles:
    def test_a_teams_file_in_order_has_no_problems(self, app, rules_file):
        rules_file()
        rules_file(language="en")

        assert legal.problems() == []

    def test_what_is_wrong_with_one(self, app, rules_file, legal_dir):  # noqa: F811
        rules_file(front_team="glider")
        (legal_dir / "teams" / "rocket" / "statutes" / "de").mkdir(parents=True)
        (legal_dir / "teams" / "rocket" / "statutes" / "de" / "2026-01-01.md").write_text("x")

        problems = "\n".join(legal.problems())

        assert 'team is "glider", but the file is in teams/rocket/' in problems
        assert "teams/rocket/statutes/de/2026-01-01.md: not where a version goes" in problems

    def test_a_folder_for_no_team_is_reported_when_the_pdfs_are_made(self, app, rules_file):
        rules_file(team="ghost")

        [(version, result)] = [(v, r) for v, r in legal_pdf.build_all() if v.team == "ghost"]

        assert isinstance(result, LookupError) and "ghost" in str(result)
