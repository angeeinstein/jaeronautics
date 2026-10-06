"""A team's rules as the association keeps its own legal texts.

legal/teams/<team>/team-rules/<language>/<day>.md, approved by the association,
versioned, German applying and English a translation, each version a PDF with
the team's logo. Applying or joining ticks the version in force, read in a
window over the form; the membership keeps its day.
"""
from datetime import datetime, timedelta

import pytest

from conftest import db
from aeronautics_members import legal_pdf
from aeronautics_members.services import legal_texts as legal
from aeronautics_members.services import teams
from aeronautics_members.services.clock import get_membership_today
from test_legal_texts import legal_dir  # noqa: F401 -- fixture
from test_team_page import _png
from test_teams_flow import _led, _login, _person, switched_on  # noqa: F401
from test_teams_foundation import _in_team

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

        body = client.get("/teams/rocket/about").get_data(as_text=True)

        tick = body.split('name="accept_terms"')[1].split("</div>")[0]
        assert 'href="/teams/rocket/rules"' in tick and "data-legal-dialog" in tick
        assert f"version of {TODAY.strftime('%d.%m.%Y')}" in tick
        assert 'href="/teams/rocket/rules/pdf"' in body and "legal-dialog.js" in body

    def test_the_version_ticked_is_kept_with_the_membership(self, app, client, rules_file):
        team, _lead = _led()
        rules_file()
        anna = _person()
        _login(client, anna.id)

        client.post("/teams/rocket/join", data={})
        assert teams.ongoing_membership(anna, team) is None
        client.post("/teams/rocket/join", data={"accept_terms": "on"})

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

        english = client.get("/teams/rocket/rules").get_data(as_text=True)
        german = client.get("/teams/rocket/rules/de").get_data(as_text=True)

        assert "Briefing before every flight." in english and "the German version applies" in english
        assert "Einweisung vor jedem Flug." in german
        assert 'href="/teams/rocket/about"' in german and "PDF, German and English" in german

    def test_alone_for_the_window(self, app, client, rules_file):
        _led()
        rules_file()
        _login(client, _person().id)

        body = client.get("/teams/rocket/rules?part=body").get_data(as_text=True)

        assert "Einweisung vor jedem Flug." in body and "<html" not in body

    def test_an_earlier_version_and_a_team_without_rules(self, app, client, rules_file):
        _led()
        _led("Glider")
        earlier = (TODAY - timedelta(days=30)).isoformat()
        rules_file(day=earlier, body="Alt.")
        rules_file()
        _login(client, _person().id)

        assert "Alt." in client.get(f"/teams/rocket/rules/de/{earlier}").get_data(as_text=True)
        assert client.get("/teams/glider/rules").status_code == 404
        assert client.get("/teams/rocket/rules/de/2001-01-01").status_code == 404

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

        assert "The rules have changed" not in client.get("/teams/rocket").get_data(as_text=True)

        rules_file()
        body = client.get("/teams/rocket").get_data(as_text=True)

        assert f"You accepted the version of {earlier.strftime('%d.%m.%Y')}" in body
        assert "The rules have changed" in body


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
