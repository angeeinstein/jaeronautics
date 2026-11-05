"""The legal texts' editor (services/legal_editor.py, api/legal_editor.py,
docs/legal-texts-in-portal-plan.md, "The editor"): a draft started from the
version in force, the template or a draft file in legal/; saved as written,
never over somebody else's save; its earlier states kept per session; one
language checked and rendered as written; a correction within the 24 hours;
who may do what -- and publishing refusing a text the checks refuse.
"""
import io
from datetime import timedelta

import pytest

from api_helpers import send
from conftest import db
from aeronautics_members.db_models import AuditLog, LegalTextSnapshot, LegalTextVersion
from aeronautics_members.services import ConflictError, PermissionError_, ValidationError
from aeronautics_members.services import legal_editor as editor
from aeronautics_members.services import legal_texts as legal
from aeronautics_members.services import legal_uploads
from aeronautics_members.services.clock import get_membership_today
from test_admin_reviews import _login, _staff
from test_legal_texts import legal_dir, texts  # noqa: F401 -- fixtures
from test_legal_uploads import _file, _publish, boss, rocket  # noqa: F401 -- fixtures
from test_teams_flow import switched_on  # noqa: F401 -- fixture
from test_teams_foundation import _association_member


def _template(team=None):
    from flask import render_template

    return render_template("legal/template.md", day=get_membership_today().isoformat(), team=team)


def _content(body="§ 1 Name.", title="Statuten"):
    return {"title": title, "revision": None, "body": body}


class TestStarting:
    def test_from_the_version_in_force_its_german_only(self, boss, texts):  # noqa: F811
        texts("membership-terms", "2026-10-04", "§ 1 Alt.", title="Mitgliedschaftsbedingungen")
        texts("membership-terms", "2026-10-04", "§ 1 Old.", language="en", title="Membership Terms")

        record = editor.start_draft(boss, "membership-terms")
        db.session.commit()

        # The English is made by machine once it is published (docs/legal-translation-plan.md).
        assert record.status == "draft" and "§ 1 Alt." in record.body and record.translations == []

    def test_from_the_template_when_nothing_is_in_force(self, boss):  # noqa: F811
        record = editor.start_draft(boss, "statutes", template=_template())

        assert record.title == "Statuten" and "## § 1" in record.body and "---" not in record.body[:5]

    def test_a_draft_file_in_legal_is_copied_once_and_no_longer_listed(self, boss, texts):  # noqa: F811
        day = get_membership_today().isoformat()
        texts("privacy-policy", day, "§ 1 Entwurf.", status="draft", title="Datenschutz")
        [file_draft] = legal.waiting("privacy-policy")

        record = editor.import_file(boss, "privacy-policy", None, file_draft.version)
        again = editor.import_file(boss, "privacy-policy", None, file_draft.version)
        db.session.commit()

        assert again.id == record.id and "Entwurf" in record.body
        assert [v.record_id for v in legal.waiting("privacy-policy")] == [record.id]


class TestSaving:
    def test_as_written_and_counted(self, boss):  # noqa: F811
        record = editor.start_draft(boss, "statutes", template=_template())

        editor.save(boss, record, _content("§ 1 Neu."), 0)
        db.session.commit()

        assert record.body == "§ 1 Neu." and record.edit_count == 1

    def test_never_over_somebody_elses_save(self, boss):  # noqa: F811
        record = editor.start_draft(boss, "statutes")
        editor.save(boss, record, _content("Erster."), 0)

        with pytest.raises(ConflictError, match="Somebody else"):
            editor.save(boss, record, _content("Zweiter."), 0)

    def test_two_saves_at_the_same_moment_one_is_refused(self, boss):  # noqa: F811
        record = editor.start_draft(boss, "statutes", template=_template())
        db.session.commit()
        # Another request saved between this one loading the draft and saving it: the count in
        # the database moved on, the one this request holds did not.
        db.session.execute(db.update(LegalTextVersion).where(LegalTextVersion.id == record.id)
                           .values(edit_count=1).execution_options(synchronize_session=False))

        with pytest.raises(ConflictError, match="Somebody else"):
            editor.save(boss, record, _content("Zu spät."), 0)

    def test_what_is_saved_is_what_is_rendered(self, boss):  # noqa: F811
        record = editor.start_draft(boss, "statutes", template=_template())
        db.session.commit()
        assert "Zuerst." not in str(legal.render(legal.record(record.id))["html"])

        editor.save(boss, record, _content("## § 1 Name\n\nZuerst."), 0)
        db.session.commit()
        assert "Zuerst." in str(legal.render(legal.record(record.id))["html"])

        editor.save(boss, record, _content("## § 1 Name\n\nDanach."), 1)
        db.session.commit()
        assert "Danach." in str(legal.render(legal.record(record.id))["html"])


class TestEarlierStates:
    def test_kept_when_a_session_starts_once_per_state(self, boss):  # noqa: F811
        record = editor.start_draft(boss, "statutes")
        first = editor.open_session(boss, record)
        assert editor.open_session(boss, record).id == first.id

        editor.save(boss, record, _content("Geändert."), 0)
        second = editor.open_session(boss, record)

        assert second.id != first.id and editor.snapshot(record, first.id).content["german"]["body"] != "Geändert."

    def test_only_the_last_twenty(self, boss):  # noqa: F811
        record = editor.start_draft(boss, "statutes")
        for count in range(25):
            editor.save(boss, record, _content(f"Stand {count}."), count)
            editor.open_session(boss, record)
        db.session.commit()

        assert db.session.query(LegalTextSnapshot).count() == editor.SNAPSHOTS_KEPT


class TestChecking:
    def test_rendered_as_its_page_will_be(self, boss):  # noqa: F811
        record = editor.start_draft(boss, "statutes")

        html, contents, problems = editor.check(record, "de", {"title": "Statuten", "body": "## § 1 Name\n\nText."})

        assert problems == [] and "Text." in html and contents[0][1] == "§ 1 Name"

    def test_what_is_wrong_is_listed(self, boss):  # noqa: F811
        record = editor.start_draft(boss, "statutes")

        _html, _contents, problems = editor.check(record, "de", {"title": "", "body": ""})

        assert "The text is empty." in problems and any("title" in problem for problem in problems)

    def test_publishing_refuses_a_text_the_checks_refuse(self, boss):  # noqa: F811
        record = editor.start_draft(boss, "statutes")
        editor.save(boss, record, {"title": "", "revision": None, "body": "x"}, 0)
        db.session.commit()

        with pytest.raises(ValidationError):
            _publish(boss, record)


class TestCorrecting:
    def test_within_the_24_hours_as_an_upload_would(self, boss):  # noqa: F811
        record = legal_uploads.upload_draft(boss, _file(body="§ 1 Nmae."))
        db.session.commit()
        _publish(boss, record)

        editor.correct(boss, editor.editable(boss, record.id), _content("§ 1 Name."))
        db.session.commit()

        assert "Nmae" not in str(legal.render(legal.current_version("statutes"))["html"])


class TestWho:
    def test_a_lead_writes_their_teams_rules_and_the_publishers_are_told(self, boss, rocket):  # noqa: F811
        team, lead = rocket
        record = editor.start_draft(lead, legal.TEAM_RULES, team.slug, "template", _template(team))
        db.session.commit()

        assert editor.editable(lead, record.id).id == record.id
        with pytest.raises(PermissionError_):
            editor.start_draft(lead, "statutes")

    def test_a_lead_edits_a_draft_an_admin_started(self, boss, rocket):  # noqa: F811
        team, lead = rocket
        record = editor.start_draft(boss, legal.TEAM_RULES, team.slug, "template", _template(team))
        db.session.commit()

        assert editor.editable(lead, record.id).id == record.id

    def test_somebody_else_does_not(self, boss, rocket):  # noqa: F811
        team, _lead = rocket
        record = editor.start_draft(boss, legal.TEAM_RULES, team.slug, "template", _template(team))
        outsider = _association_member("outsider@example.org")
        db.session.commit()

        with pytest.raises(PermissionError_):
            editor.editable(outsider, record.id)

    def test_only_a_publisher_corrects(self, boss, rocket):  # noqa: F811
        team, lead = rocket
        record = editor.start_draft(lead, legal.TEAM_RULES, team.slug, "template", _template(team))
        editor.save(lead, record, {"title": "Teamordnung", "revision": None, "body": "§ 1 Regeln."}, 0)
        editor.release(lead, record)  # the lead closed the editor
        db.session.commit()
        _publish(boss, record)

        with pytest.raises(PermissionError_):
            editor.editable(lead, record.id)

    def test_writing_without_publishing(self, client, boss, monkeypatch):  # noqa: F811
        from aeronautics_members import permissions

        writer = _staff("writer@example.org", "admin")
        monkeypatch.setitem(permissions.ROLE_PERMISSIONS, "admin",
                            permissions.ROLE_PERMISSIONS["admin"] - {permissions.Permission.LEGAL_PUBLISH})
        record = editor.start_draft(writer, "statutes")
        db.session.commit()
        _login(client, writer.id)

        published = send(client, "POST", f"/api/v1/admin/legal/drafts/{record.id}/publish",
                         {"effective_from": get_membership_today().isoformat()})

        assert published.status_code == 403
        assert client.get("/api/v1/admin/legal").get_json()["may_publish"] is False


class TestTheEnglishView:
    def test_the_german_as_written_in_english_only_new_blocks_sent(self, client, boss, monkeypatch):  # noqa: F811
        from aeronautics_members.services import translation

        sent = []

        def fake(blocks, as_html=True):
            sent.extend(blocks)
            return [f"EN:{block}" for block in blocks]

        monkeypatch.setattr(translation, "_by_a_service", lambda blocks, as_html, current, only=None:
                            (fake(blocks, as_html), "deepl"))
        monkeypatch.setattr(translation, "configured", lambda current=None: ["deepl"])
        record = editor.start_draft(boss, "statutes", template=_template())
        db.session.commit()
        _login(client, boss.id)
        text = {"title": "Statuten", "body": "## § 1 Name\n\nEins.\n\n## § 2 Zweck\n\nZwei."}

        first = send(client, "POST", f"/api/v1/legal/drafts/{record.id}/english", text).get_json()
        text["body"] = text["body"].replace("Zwei.", "Zwei, geändert.")
        sent.clear()
        send(client, "POST", f"/api/v1/legal/drafts/{record.id}/english", text)

        assert first["title"] == "EN:Statuten" and "EN:<p>Eins.</p>" in first["html"]
        assert sent == ["<p>Zwei, geändert.</p>\n"]

    def test_says_when_no_service_is_set_up(self, client, boss):  # noqa: F811
        record = editor.start_draft(boss, "statutes", template=_template())
        db.session.commit()
        _login(client, boss.id)

        answer = send(client, "POST", f"/api/v1/legal/drafts/{record.id}/english",
                      {"title": "Statuten", "body": "## § 1 Name\n\nEins."}).get_json()

        assert answer["html"] == "" and "No translation service" in answer["unavailable"]


class TestTheApi:
    def test_start_open_save_check(self, client, boss):  # noqa: F811
        _login(client, boss.id)

        started = send(client, "POST", "/api/v1/legal/drafts", {"document": "statutes", "start": "template"})
        record_id = started.get_json()["record_id"]
        assert started.get_json()["editor_url"] == f"/admin/legal/drafts/{record_id}"

        opened = send(client, "POST", f"/api/v1/legal/drafts/{record_id}/session").get_json()
        assert opened["mode"] == "draft" and opened["edit_count"] == 0 and opened["snapshots"]

        saved = send(client, "PUT", f"/api/v1/legal/drafts/{record_id}", {
            "german": {"title": "Statuten", "body": "## § 1 Name\n\nNeu."}, "edit_count": 0})
        assert saved.get_json()["edit_count"] == 1
        stale = send(client, "PUT", f"/api/v1/legal/drafts/{record_id}", {
            "german": {"title": "Statuten", "body": "Alt."}, "edit_count": 0})
        assert stale.status_code == 409 and stale.get_json()["error"]["code"] == "legal_draft_changed"

        checked = send(client, "POST", f"/api/v1/legal/drafts/{record_id}/check",
                       {"title": "Statuten", "body": "## § 1 Name\n\nNeu."}).get_json()
        assert checked["problems"] == [] and checked["contents"][0]["label"] == "§ 1 Name"

        snapshot = opened["snapshots"][0]["id"]
        earlier = client.get(f"/api/v1/legal/drafts/{record_id}/snapshots/{snapshot}").get_json()
        assert "Neu." not in earlier["german"]["body"]

    def test_the_editor_page_for_a_lead(self, client, rocket, switched_on):  # noqa: F811
        team, lead = rocket
        _login(client, lead.id)

        started = send(client, "POST", "/api/v1/legal/drafts",
                       {"document": "team-rules", "team": "rocket", "start": "template"}).get_json()

        assert started["editor_url"].startswith("/teams/rocket/manage/rules/drafts/")
        assert client.get(started["editor_url"]).status_code == 200


class TestAsAFile:
    def test_downloaded_with_its_front_matter_and_taken_back_as_it_is(self, client, boss):  # noqa: F811
        record = legal_uploads.upload_draft(boss, _file(body="§ 1 Name.", extra='approved_by: "Vorstand"\n'))
        db.session.commit()
        _login(client, boss.id)

        german = client.get(f"/admin/legal/waiting.md?document=statutes&record={record.id}")

        assert german.status_code == 200 and german.mimetype == "text/markdown"
        assert 'filename="statutes-de-' in german.headers["Content-Disposition"]
        text = german.get_data(as_text=True)
        assert text.startswith("---\n") and 'approved_by: "Vorstand"' in text and "§ 1 Name." in text
        again = legal_uploads.upload_draft(boss, (german.data, "x.md"))
        assert again.body.strip() == "§ 1 Name." and again.approved_by == "Vorstand"

    def test_a_file_in_legal_as_it_is(self, client, boss, texts):  # noqa: F811
        day = get_membership_today().isoformat()
        texts("privacy-policy", day, "§ 1 Datei.", status="draft", title="Datenschutz")
        _login(client, boss.id)

        response = client.get(f"/admin/legal/waiting.md?document=privacy-policy&version={day}")

        assert response.status_code == 200 and "§ 1 Datei." in response.get_data(as_text=True)

    def test_for_a_lead_their_rules_only(self, client, boss, rocket, switched_on):  # noqa: F811
        team, lead = rocket
        record = editor.start_draft(lead, legal.TEAM_RULES, team.slug, "template", _template(team))
        db.session.commit()
        _login(client, lead.id)

        assert client.get(f"/teams/rocket/manage/rules/waiting.md?record={record.id}").status_code == 200
        # Not through the admins' address: sent away, not given the file.
        assert client.get(f"/admin/legal/waiting.md?document=team-rules&team=rocket&record={record.id}") \
            .status_code in (302, 403)

    def test_replaced_with_a_file_the_state_before_kept(self, client, boss):  # noqa: F811
        record = editor.start_draft(boss, "statutes", template=_template())
        db.session.commit()
        _login(client, boss.id)
        data, _name = _file(body="§ 1 Aus der Datei.")

        read = client.post(f"/api/v1/legal/drafts/{record.id}/file",
                           data={"german": (io.BytesIO(data), "anything.md")},
                           content_type="multipart/form-data").get_json()

        assert read["german"]["body"].strip() == "§ 1 Aus der Datei."
        assert "Aus der Datei" not in record.body  # put in by the editor, then saved
        assert db.session.query(LegalTextSnapshot).filter_by(record_id=record.id).count() == 1

    def test_never_a_different_text(self, client, boss):  # noqa: F811
        record = editor.start_draft(boss, "statutes", template=_template())
        db.session.commit()
        _login(client, boss.id)
        data, _name = _file("legal-notice", "Impressum")

        refused = client.post(f"/api/v1/legal/drafts/{record.id}/file", data={"german": (io.BytesIO(data), "x.md")},
                              content_type="multipart/form-data")

        assert refused.status_code == 400 and "different text" in refused.get_json()["error"]["details"]["problems"][0]


class TestTheLock:
    """One person edits a draft at a time (docs/legal-texts-in-portal-plan.md, "The editor")."""

    @pytest.fixture
    def two(self, boss):  # noqa: F811
        other = _staff("other@example.org", "admin")
        record = editor.start_draft(boss, "statutes", template=_template())
        db.session.commit()
        return record, other

    def test_the_first_holds_it_the_second_sees_who(self, boss, two):  # noqa: F811
        record, other = two

        assert editor.take_lock(boss, record)
        assert not editor.take_lock(other, record)
        state = editor.lock_state(record, other)
        assert not state["free"] and not state["mine"] and state["holder"].id == boss.id
        assert not state["can_take_over"]

    def test_the_second_cannot_save(self, boss, two):  # noqa: F811
        record, other = two
        editor.take_lock(boss, record)

        with pytest.raises(ConflictError, match="Somebody else is editing"):
            editor.save(other, record, _content("Nicht ich."), 0)

    def test_free_again_once_the_holders_editor_goes_quiet(self, boss, two):  # noqa: F811
        from datetime import datetime

        record, other = two
        start = datetime(2026, 11, 5, 10, 0)
        editor.take_lock(boss, record, now=start)

        later = start + editor.LOCK_GONE_AFTER + timedelta(seconds=1)
        assert editor.lock_state(record, other, now=later)["free"]
        assert editor.take_lock(other, record, now=later)
        # The first one's next sign of life says it is gone.
        assert not editor.beat(boss, record, now=later)

    def test_taken_over_after_ten_minutes_without_typing(self, boss, two):  # noqa: F811
        from datetime import datetime

        record, other = two
        start = datetime(2026, 11, 5, 10, 0)
        editor.take_lock(boss, record, typed=True, now=start)
        # The tab stays open: signs of life, but no typing.
        idle = start + editor.TAKE_OVER_AFTER + timedelta(seconds=5)
        editor.beat(boss, record, now=idle)

        assert not editor.take_lock(other, record, now=idle)
        assert editor.lock_state(record, other, now=idle)["can_take_over"]
        assert editor.take_lock(other, record, take_over=True, now=idle)
        assert db.session.query(AuditLog).filter_by(event_type="legal_draft_taken_over").count() == 1

    def test_not_taken_over_while_the_holder_types(self, boss, two):  # noqa: F811
        from datetime import datetime

        record, other = two
        start = datetime(2026, 11, 5, 10, 0)
        editor.take_lock(boss, record, typed=True, now=start)
        editor.beat(boss, record, typed=True, now=start + timedelta(minutes=9))
        editor.beat(boss, record, now=start + timedelta(minutes=11))  # the tab open, not typing since minute 9

        assert not editor.take_lock(other, record, take_over=True, now=start + timedelta(minutes=11))

    def test_leaving_frees_it_at_once(self, boss, two):  # noqa: F811
        record, other = two
        editor.take_lock(boss, record)

        editor.release(boss, record)

        assert editor.lock_state(record, other)["free"] and editor.take_lock(other, record)

    def test_no_publishing_while_somebody_edits(self, boss, two):  # noqa: F811
        record, other = two
        editor.save(other, record, _content("## § 1 Name\n\nText."), 0)
        db.session.commit()

        with pytest.raises(ConflictError, match="editing this draft now"):
            _publish(boss, record)

    def test_the_api(self, client, boss, two):  # noqa: F811
        record, other = two
        _login(client, other.id)
        editor.take_lock(boss, record)
        db.session.commit()

        opened = send(client, "POST", f"/api/v1/legal/drafts/{record.id}/session").get_json()
        assert opened["lock"]["mine"] is False and opened["lock"]["holder"]
        looked = send(client, "POST", f"/api/v1/legal/drafts/{record.id}/lock", {"action": "look"}).get_json()
        assert looked["free"] is False

        editor.release(boss, record)
        db.session.commit()
        taken = send(client, "POST", f"/api/v1/legal/drafts/{record.id}/lock", {"action": "take"}).get_json()
        assert taken["mine"] is True
        left = send(client, "DELETE", f"/api/v1/legal/drafts/{record.id}/lock").get_json()
        assert left["free"] is True
