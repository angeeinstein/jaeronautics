"""Rebuilding the portal should be a restore, not a reconstruction.

A fresh install starts with a blank settings page. Filling it in is forty
minutes of copying values that all have to be exactly right and none of which
announce themselves when they are wrong: a missing lecture group is an import
that refuses, a missing Connect secret is a login that silently does not work.

Two properties matter more than the rest and both are about not lying. A file
exported without secrets must not look as though it has them, and a restore
must never remove something it does not know about -- a fresh install and a
partial file would otherwise blank whatever the file had not heard of.
"""
import json
import stat

import pytest

from conftest import db

from aeronautics_members.db_models import MailAccount, Setting
from aeronautics_members.services.portal_settings import (
    REDACTED,
    export_settings,
    import_settings,
)

STORED = {
    "forum_base_url": "https://lavboard.example.at",
    "forum_member_group": "members",
    "forum_lecture_groups": "students, alumni",
    "forum_category_groups": "student = students",
    "discourse_api_key": "an-api-key",
    "discourse_connect_secret": "a-connect-secret",
    "stripe_secret_key": "sk_live_xyz",
    "stripe_publishable_key": "pk_live_xyz",
    "invoice_payments_enabled": "True",
    "notification_sender": "noreply",
}


@pytest.fixture
def configured(app):
    for key, value in STORED.items():
        db.session.add(Setting(key=key, value=value))
    db.session.add(MailAccount(
        account_key="noreply", host="smtp.example.at", port=587,
        username="noreply@example.at", password="the-smtp-password",
        starttls=True,
    ))
    db.session.commit()


def _flat(payload):
    return {
        key: value
        for values in payload["settings"].values()
        for key, value in values.items()
    }


class TestWhatGoesIntoTheFile:
    def test_the_settings_an_administrator_typed(self, configured):
        exported = _flat(export_settings())
        assert exported["forum_base_url"] == "https://lavboard.example.at"
        assert exported["forum_lecture_groups"] == "students, alumni"
        assert exported["invoice_payments_enabled"] == "True"

    def test_they_are_grouped_by_the_page_they_are_edited_on(self, configured):
        payload = export_settings()
        assert "forum_base_url" in payload["settings"]["forum"]
        assert "stripe_publishable_key" in payload["settings"]["stripe"]
        assert "notification_sender" in payload["settings"]["notifications"]

    def test_mail_accounts_come_with_it(self, configured):
        account = export_settings()["mail_accounts"][0]
        assert account["host"] == "smtp.example.at"
        assert account["port"] == 587
        assert account["starttls"] is True

    def test_a_setting_nobody_edits_is_not_carried_to_another_machine(self, app):
        """Only what the pages offer, not every row something kept there."""
        db.session.add(Setting(key="some_internal_bookkeeping", value="7"))
        db.session.commit()
        assert "some_internal_bookkeeping" not in _flat(export_settings())


class TestSecrets:
    def test_they_are_left_out_unless_asked_for(self, configured):
        exported = _flat(export_settings())
        assert exported["discourse_api_key"] == REDACTED
        assert exported["stripe_secret_key"] == REDACTED

    def test_and_the_smtp_password_with_them(self, configured):
        assert "password" not in export_settings()["mail_accounts"][0]

    def test_the_file_says_which_it_held_back_rather_than_going_quiet(
        self, configured
    ):
        held = export_settings()["held_back"]
        assert "discourse_api_key" in held
        assert "mail account noreply" in held

    def test_asking_for_them_gets_them(self, configured):
        exported = _flat(export_settings(with_secrets=True))
        assert exported["stripe_secret_key"] == "sk_live_xyz"
        assert exported["discourse_connect_secret"] == "a-connect-secret"
        payload = export_settings(with_secrets=True)
        assert payload["mail_accounts"][0]["password"] == "the-smtp-password"

    def test_a_file_with_secrets_says_so_about_itself(self, configured):
        assert export_settings(with_secrets=True)["with_secrets"] is True
        assert export_settings()["with_secrets"] is False


class TestPuttingThemBack:
    def _fresh(self, payload, **kwargs):
        for row in db.session.execute(db.select(Setting)).scalars().all():
            db.session.delete(row)
        for row in db.session.execute(db.select(MailAccount)).scalars().all():
            db.session.delete(row)
        db.session.commit()
        return import_settings(payload, **kwargs)

    def test_a_blank_install_ends_up_with_what_was_exported(self, configured):
        payload = export_settings(with_secrets=True)
        self._fresh(payload)
        db.session.commit()
        restored = {
            row.key: row.value
            for row in db.session.execute(db.select(Setting)).scalars()
        }
        assert restored["forum_lecture_groups"] == "students, alumni"
        assert restored["discourse_api_key"] == "an-api-key"

    def test_the_mail_account_comes_back_able_to_send(self, configured):
        self._fresh(export_settings(with_secrets=True))
        db.session.commit()
        account = db.session.execute(db.select(MailAccount)).scalar_one()
        assert account.password == "the-smtp-password"
        assert account.starttls is True

    def test_a_placeholder_is_never_written_as_though_it_were_a_value(
        self, configured
    ):
        """An empty box says what it is; "<not exported>" reads like a key."""
        report = self._fresh(export_settings())
        db.session.commit()
        stored = {
            row.key: row.value
            for row in db.session.execute(db.select(Setting)).scalars()
        }
        assert "discourse_api_key" not in stored
        assert "discourse_api_key" in report["skipped"]

    def test_running_it_twice_changes_nothing_the_second_time(self, configured):
        payload = export_settings(with_secrets=True)
        self._fresh(payload)
        db.session.commit()
        again = import_settings(payload)
        assert again["set"] == []
        assert len(again["already"]) == len(STORED)

    def test_it_never_removes_a_setting_the_file_does_not_mention(
        self, configured
    ):
        """The file describes one machine, not every machine."""
        payload = export_settings(with_secrets=True)
        payload["settings"]["forum"].pop("forum_member_group")
        import_settings(payload)
        db.session.commit()
        kept = db.session.execute(
            db.select(Setting).where(Setting.key == "forum_member_group")
        ).scalar_one()
        assert kept.value == "members"

    def test_a_dry_run_reports_and_writes_nothing(self, configured):
        payload = export_settings(with_secrets=True)
        self._fresh(payload, dry_run=True)
        db.session.commit()
        assert db.session.execute(db.select(Setting)).scalars().all() == []

    def test_a_key_this_portal_does_not_have_is_reported_not_stored(
        self, configured
    ):
        payload = export_settings(with_secrets=True)
        payload["settings"]["forum"]["forum_something_invented"] = "x"
        report = import_settings(payload)
        assert any("forum_something_invented" in p for p in report["problems"])

    def test_a_mail_account_with_no_password_and_nothing_here_is_said_not_faked(
        self, configured
    ):
        """A blank password is an account that fails at the first send."""
        report = self._fresh(export_settings())
        assert any("noreply" in problem for problem in report["problems"])
        assert db.session.execute(db.select(MailAccount)).scalars().all() == []

    def test_a_file_that_is_not_one_of_ours_is_refused(self, app):
        with pytest.raises(ValueError, match="no settings in it"):
            import_settings({"hello": "world"})

    def test_a_file_from_a_later_format_is_refused_rather_than_guessed_at(
        self, app
    ):
        with pytest.raises(ValueError, match="format"):
            import_settings({"format": 99, "settings": {}})


class TestTheCommands:
    def test_it_writes_a_file_and_says_what_was_held_back(
        self, app, configured, tmp_path
    ):
        out = tmp_path / "portal-settings.json"
        result = app.test_cli_runner().invoke(
            args=["dump-portal-settings", "--out", str(out)]
        )
        assert result.exit_code == 0, result.output
        assert "entered by hand" in result.output
        assert json.loads(out.read_text())["with_secrets"] is False

    def test_a_file_with_secrets_is_not_readable_by_anybody_else(
        self, app, configured, tmp_path
    ):
        out = tmp_path / "portal-settings.json"
        app.test_cli_runner().invoke(
            args=["dump-portal-settings", "--out", str(out), "--with-secrets"]
        )
        assert stat.S_IMODE(out.stat().st_mode) == 0o600

    def test_and_it_says_plainly_what_the_file_now_is(
        self, app, configured, tmp_path
    ):
        result = app.test_cli_runner().invoke(args=[
            "dump-portal-settings", "--out", str(tmp_path / "s.json"),
            "--with-secrets",
        ])
        assert "SMTP password" in result.output
        assert "gpg -c" in result.output

    def test_the_round_trip_through_the_commands(self, app, configured, tmp_path):
        out = tmp_path / "portal-settings.json"
        app.test_cli_runner().invoke(args=[
            "dump-portal-settings", "--out", str(out), "--with-secrets",
        ])
        for row in db.session.execute(db.select(Setting)).scalars().all():
            db.session.delete(row)
        db.session.commit()

        result = app.test_cli_runner().invoke(
            args=["restore-portal-settings", str(out)]
        )
        assert result.exit_code == 0, result.output
        assert "forum_lecture_groups" in result.output
        restored = db.session.execute(
            db.select(Setting).where(Setting.key == "forum_lecture_groups")
        ).scalar_one()
        assert restored.value == "students, alumni"
