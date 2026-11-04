"""Legal texts uploaded in the portal (services/legal_uploads.py,
docs/legal-texts-in-portal-plan.md): a draft uploaded, published with its day
and approval, read like a file in legal/ from then on; corrected only while
nobody has accepted it and within 24 hours; a draft deleted, a published
version never; a team's leads uploading their own rules, and who is told.
"""
import io
from datetime import timedelta

import pytest

from api_helpers import send
from conftest import db
from aeronautics_members.db_models import AuditLog, LegalTextVersion, NotificationEvent, TeamMembership
from aeronautics_members.permissions import ROLE_PERMISSIONS, Permission
from aeronautics_members.services import ConflictError, ValidationError
from aeronautics_members.services import legal_texts as legal
from aeronautics_members.services import legal_uploads, teams
from aeronautics_members.services.clock import get_membership_today, get_now_utc
from test_admin_reviews import _login, _staff
from test_legal_texts import legal_dir, texts  # noqa: F401 -- fixtures
from test_teams_flow import switched_on  # noqa: F401 -- fixture
from test_teams_foundation import _association_member, _in_team, _team


def _file(slug="statutes", title="Statuten", body="§ 1 Name.", language="de", team=None, effective_from=None,
          extra=""):
    day = get_membership_today().isoformat()
    team_line = f'team: "{team}"\n' if team else ""
    return (f'---\ntitle: "{title}"\ndocument: "{slug}"\nlanguage: "{language}"\n{team_line}'
            f'version: "{day}"\neffective_from: "{effective_from or day}"\nstatus: "draft"\n{extra}---\n\n'
            f"{body}\n").encode(), "anything.md"


@pytest.fixture
def boss(app, legal_dir):  # noqa: F811
    return _staff("boss@example.org", "superadmin")


def _publish(boss, record, **fields):
    fields.setdefault("effective_from", get_membership_today())
    legal_uploads.publish(boss, record.id, **fields)
    db.session.commit()


class TestUploadingAndPublishing:
    def test_a_draft_is_waiting_not_shown(self, boss):
        record = legal_uploads.upload_draft(boss, _file())
        db.session.commit()

        assert legal.current_version("statutes") is None
        [waiting] = legal.waiting("statutes")
        assert waiting.record_id == record.id and waiting.status == "draft"

    def test_published_it_is_in_force_with_todays_day(self, boss):
        record = legal_uploads.upload_draft(boss, _file(body="§ 1 Der Verein heißt."))
        db.session.commit()

        _publish(boss, record, approved_by="Vorstand", approved_on=get_membership_today() - timedelta(days=3))

        version = legal.current_version("statutes")
        assert version.record_id == record.id and version.version == get_membership_today()
        assert version.approved_by == "Vorstand"
        assert "Der Verein heißt" in str(legal.render(version)["html"])
        assert db.session.query(AuditLog).filter_by(event_type="legal_text_published").count() == 1

    def test_newer_than_a_file_it_takes_over_from_it(self, boss, texts):  # noqa: F811
        texts("statutes", "2019-03-17", "Alt.")
        record = legal_uploads.upload_draft(boss, _file())
        db.session.commit()
        _publish(boss, record)

        assert legal.current_version("statutes").record_id == record.id
        assert [v.version.isoformat() for v in legal.versions("statutes")][-1] == "2019-03-17"

    def test_with_its_translation(self, boss):
        record = legal_uploads.upload_draft(
            boss, _file(), _file(title="Statutes", body="§ 1 Name (English).", language="en"))
        db.session.commit()
        _publish(boss, record)

        english = legal.translation(legal.current_version("statutes"))
        assert english is not None and "English" in str(legal.render(english)["html"])

    def test_not_from_a_day_gone(self, boss):
        record = legal_uploads.upload_draft(boss, _file())
        db.session.commit()

        with pytest.raises(ValidationError):
            legal_uploads.publish(boss, record.id, effective_from=get_membership_today() - timedelta(days=1))

    def test_one_a_day(self, boss):
        first = legal_uploads.upload_draft(boss, _file())
        second = legal_uploads.upload_draft(boss, _file(body="Anders."))
        db.session.commit()
        _publish(boss, first)

        with pytest.raises(ConflictError):
            legal_uploads.publish(boss, second.id, effective_from=get_membership_today() + timedelta(days=30))

    def test_a_file_that_would_not_preview_is_refused(self, boss):
        with pytest.raises(ValidationError) as refused:
            legal_uploads.upload_draft(boss, (b"no front matter", "x.md"))

        assert refused.value.details["problems"]

    def test_the_file_name_does_not_matter(self, boss):
        record = legal_uploads.upload_draft(boss, _file())  # called anything.md

        assert record.id is not None


class TestCorrecting:
    def test_within_a_day_while_nobody_accepted_it(self, boss):
        record = legal_uploads.upload_draft(boss, _file(body="§ 1 Nmae."))
        db.session.commit()
        _publish(boss, record)

        legal_uploads.correct(boss, record.id, _file(body="§ 1 Name."))
        db.session.commit()

        version = legal.current_version("statutes")
        assert "Name." in str(legal.render(version)["html"]) and "Nmae" not in str(legal.render(version)["html"])
        [entry] = db.session.query(AuditLog).filter_by(event_type="legal_text_corrected").all()
        assert "Nmae" in entry.before_state["body"]

    def test_not_once_somebody_accepted_it(self, boss):
        record = legal_uploads.upload_draft(boss, _file())
        db.session.commit()
        _publish(boss, record)
        member = _association_member("new@example.org")
        member.member.legal_versions_accepted = {"statutes": get_membership_today().isoformat()}
        db.session.commit()

        with pytest.raises(ConflictError, match="accepted"):
            legal_uploads.correct(boss, record.id, _file(body="Anders."))

    def test_not_after_a_day(self, boss, monkeypatch):
        record = legal_uploads.upload_draft(boss, _file())
        db.session.commit()
        _publish(boss, record)
        later = get_now_utc() + timedelta(hours=25)
        monkeypatch.setattr(legal_uploads, "get_now_utc", lambda: later)

        with pytest.raises(ConflictError, match="24 hours"):
            legal_uploads.correct(boss, record.id, _file(body="Anders."))

    def test_never_a_different_text(self, boss):
        record = legal_uploads.upload_draft(boss, _file())
        db.session.commit()
        _publish(boss, record)

        with pytest.raises(ValidationError):
            legal_uploads.correct(boss, record.id, _file(slug="legal-notice", title="Impressum"))


class TestDeleting:
    def test_a_draft(self, boss):
        record = legal_uploads.upload_draft(boss, _file(), _file(language="en", title="Statutes"))
        db.session.commit()

        legal_uploads.delete_draft(boss, record.id)
        db.session.commit()

        assert db.session.query(LegalTextVersion).count() == 0

    def test_never_a_published_version(self, boss):
        record = legal_uploads.upload_draft(boss, _file())
        db.session.commit()
        _publish(boss, record)

        with pytest.raises(ConflictError):
            legal_uploads.delete_draft(boss, record.id)


@pytest.fixture
def rocket(boss):
    team = _team("Rocket", slug="rocket")
    lead = _association_member("lead@example.org")
    _in_team(lead, team)
    teams.grant_team_role(None, team, lead, teams.ROLE_LEAD)
    db.session.commit()
    return team, lead


def _emails(kind):
    return db.session.query(NotificationEvent).filter_by(event_type=kind).all()


class TestATeamsRules:
    def test_its_leads_upload_and_the_association_is_told(self, boss, rocket):
        team, lead = rocket
        boss.email_verified_at = get_now_utc()
        db.session.commit()

        record = legal_uploads.upload_draft(lead, _file("team-rules", "Teamordnung", team="rocket"), team=team)
        db.session.commit()

        assert [draft.id for draft in legal_uploads.team_drafts(team)] == [record.id]
        assert [event.recipient_email for event in _emails(legal_uploads.DRAFT_UPLOADED)] == ["boss@example.org"]

    def test_only_their_own(self, boss, rocket):
        team, lead = rocket

        with pytest.raises(ValidationError) as refused:
            legal_uploads.upload_draft(lead, _file(), team=team)

        assert "not the rules of Rocket" in refused.value.details["problems"][0]

    def test_published_the_leads_are_told_and_joiners_accept_it(self, boss, rocket):
        team, lead = rocket
        record = legal_uploads.upload_draft(lead, _file("team-rules", "Teamordnung", team="rocket"), team=team)
        db.session.commit()

        _publish(boss, record)

        assert teams.team_rules(team).version.record_id == record.id
        assert [event.recipient_email for event in _emails(legal_uploads.RULES_PUBLISHED)] == ["lead@example.org"]

    def test_accepted_by_somebody_joining_it_is_kept(self, boss, rocket):
        team, lead = rocket
        record = legal_uploads.upload_draft(lead, _file("team-rules", "Teamordnung", team="rocket"), team=team)
        db.session.commit()
        _publish(boss, record)
        joiner = _association_member("joiner@example.org")
        db.session.add(TeamMembership(team=team, user=joiner, status=teams.ACTIVE,
                                      terms_version=teams.team_rules(team).stamp))
        db.session.commit()

        assert legal_uploads.why_not_correctable(record) is not None


def _upload(client, path, german, english=None):
    data = {"german": (io.BytesIO(german[0]), german[1])}
    if english is not None:
        data["english"] = (io.BytesIO(english[0]), english[1])
    return client.post(path, data=data, content_type="multipart/form-data")


class TestTheAdminsPage:
    def test_upload_publish_and_it_is_in_force(self, client, boss):
        _login(client, boss.id)

        uploaded = _upload(client, "/api/v1/admin/legal/drafts", _file(), _file(language="en", title="Statutes"))
        record_id = uploaded.get_json()["record_id"]
        [waiting] = [item for item in client.get("/api/v1/admin/legal").get_json()["waiting"]
                     if item["record_id"] == record_id]
        assert waiting["status"] == "draft" and waiting["has_english"] and f"record={record_id}" in waiting["pdf_url"]

        published = send(client, "POST", f"/api/v1/admin/legal/drafts/{record_id}/publish", {
            "effective_from": get_membership_today().isoformat(), "approved_by": "Generalversammlung",
            "approved_on": (get_membership_today() - timedelta(days=1)).isoformat()})
        assert published.status_code == 200

        [in_force] = [item for item in client.get("/api/v1/admin/legal").get_json()["in_force"]
                      if item["record_id"] == record_id]
        assert in_force["correctable"] and "genehmigt von der Generalversammlung am" in in_force["validity"]

    def test_a_draft_s_pdf(self, client, boss, monkeypatch):
        from aeronautics_members import legal_pdf

        monkeypatch.setattr(legal_pdf, "build", lambda german, team=None, versions=None, watermark=None:
                            b"%PDF-1.7 " + watermark.encode())
        record = legal_uploads.upload_draft(boss, _file())
        db.session.commit()
        _login(client, boss.id)

        response = client.get(f"/admin/legal/waiting.pdf?document=statutes&record={record.id}")

        assert response.status_code == 200 and response.data == b"%PDF-1.7 ENTWURF"

    def test_what_is_wrong_with_the_file_is_listed(self, client, boss):
        _login(client, boss.id)

        response = _upload(client, "/api/v1/admin/legal/drafts", (b"no front matter", "x.md"))

        assert response.status_code == 400
        assert response.get_json()["error"]["details"]["problems"]

    def test_deleting_a_draft(self, client, boss):
        record = legal_uploads.upload_draft(boss, _file())
        db.session.commit()
        _login(client, boss.id)

        assert send(client, "DELETE", f"/api/v1/admin/legal/drafts/{record.id}").status_code == 200
        assert db.session.query(LegalTextVersion).count() == 0

    def test_not_for_somebody_without_the_permission(self, client, boss):
        _login(client, _staff("treasurer@example.org", "treasurer").id)

        assert client.get("/api/v1/admin/legal").status_code == 403
        assert _upload(client, "/api/v1/admin/legal/drafts", _file()).status_code == 403


class TestTheLeadsPage:
    def test_upload_see_and_delete_their_draft(self, client, rocket, switched_on):  # noqa: F811
        team, lead = rocket
        _login(client, lead.id)

        uploaded = _upload(client, "/api/v1/teams/rocket/manage/rules/drafts",
                           _file("team-rules", "Teamordnung", team="rocket"))
        assert uploaded.status_code == 200
        [draft] = uploaded.get_json()["waiting"]
        assert draft["deletable"] and draft["pdf_url"].startswith("/teams/rocket/manage/rules/waiting.pdf")

        left = send(client, "DELETE", f"/api/v1/teams/rocket/manage/rules/drafts/{draft['record_id']}")
        assert left.status_code == 200 and left.get_json()["waiting"] == []

    def test_its_template(self, client, rocket, switched_on):  # noqa: F811
        _team_, lead = rocket
        _login(client, lead.id)

        template = client.get("/teams/rocket/manage/rules/template").get_data(as_text=True)

        assert 'document: "team-rules"' in template and 'team: "rocket"' in template

    def test_not_for_a_member(self, client, rocket, switched_on):  # noqa: F811
        team, _lead = rocket
        member = _association_member("member@example.org")
        _in_team(member, team)
        db.session.commit()
        _login(client, member.id)

        assert client.get("/api/v1/teams/rocket/manage/rules").status_code == 403


def test_the_template_as_it_is_is_uploaded(boss, rocket):
    from flask import render_template

    team, lead = rocket
    for actor, team_given in ((boss, None), (lead, team)):
        text = render_template("legal/template.md", day=get_membership_today().isoformat(), team=team_given)
        legal_uploads.upload_draft(actor, (text.encode(), "template.md"), team=team_given)


def test_admins_and_super_admins_may_manage_the_legal_texts():
    assert Permission.LEGAL_TEXTS in ROLE_PERMISSIONS["admin"]
    assert Permission.LEGAL_TEXTS in ROLE_PERMISSIONS["superadmin"]
