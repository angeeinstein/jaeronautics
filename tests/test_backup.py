"""Backup and restore: a fresh install plus one backup is the old portal again.

These run the real thing end to end on SQLite: the database is emptied and
rebuilt through the migrations, exactly as on a new server, and the rows,
files and settings come back from the encrypted file.
"""

import json

import pytest

from conftest import db, make_member
from aeronautics_members.db_models import ForumAvatarSubmission, MailAccount, Member, Setting, User
from aeronautics_members.services import backup as backup_service
from aeronautics_members.services import background_jobs
from aeronautics_members.services.backup import BackupError

PASSPHRASE = "correct horse battery staple"


@pytest.fixture
def portal(app, tmp_path):
    """The app with its backup, storage and .env locations in the test's own folder."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SECRET_KEY='old-secret'\n"
        "DB_PASSWORD='machine-password'\n"
        "STRIPE_SECRET_KEY='sk_test_old'\n"
        "MAIL_ACCOUNTS_JSON='{\"a\": 1}'\n"
    )
    app.config.update(
        BACKUP_DIR=str(tmp_path / "backups"),
        BACKUP_STORAGE_DIR=str(tmp_path / "storage"),
        BACKUP_ENV_FILE=str(env_file),
        BACKUP_SCRYPT_N=2**10,
    )
    return app


def _fill(tmp_path):
    """A portal with members, settings, a mail account and an uploaded picture."""
    storage = tmp_path / "storage" / "forum_avatar_staging"
    storage.mkdir(parents=True)
    picture = storage / "avatar-1-abc.png"
    picture.write_bytes(b"\x89PNG picture bytes")

    anna = make_member("anna@example.org", first_name="Anna", last_name="Popović", payment_status="paid")
    make_member("bernd@example.org", first_name="Bernd")
    db.session.add(Setting(key="forum_base_url", value="https://forum.example.org"))
    db.session.add(MailAccount(account_key="office", host="smtp.example.org", port=465,
                               username="office", password="smtp-secret"))
    db.session.add(ForumAvatarSubmission(user_id=anna.user_id, member_id=anna.id, status="pending",
                                         storage_path=str(picture), public_token="tok"))
    db.session.commit()
    return anna


def _fresh_install(tmp_path):
    """What a brand-new server looks like: an empty schema and its first admin."""
    backup_service._drop_everything()
    backup_service._migrate("head")
    admin = User(email="installer-admin@example.org")
    admin.set_password("installer-password")
    db.session.add(admin)
    db.session.commit()
    leftover = tmp_path / "storage" / "left-over.txt"
    leftover.parent.mkdir(parents=True, exist_ok=True)
    leftover.write_text("from the fresh install")


def _backup(tmp_path, passphrase=PASSPHRASE):
    return backup_service.create_backup(tmp_path / "backups" / "b.jabackup", passphrase, created_by="me@example.org")


class TestTheRoundTrip:
    def test_a_fresh_install_plus_the_backup_is_the_old_portal(self, portal, tmp_path):
        anna = _fill(tmp_path)
        password_hash = anna.user.password_hash
        summary = _backup(tmp_path)
        assert summary["files"] == 1 and summary["rows"] > 0

        _fresh_install(tmp_path)
        result = backup_service.restore_backup(tmp_path / "backups" / "b.jabackup", PASSPHRASE)

        emails = sorted(db.session.execute(db.select(User.email)).scalars())
        assert emails == ["anna@example.org", "bernd@example.org"]  # the installer's admin is gone
        restored = db.session.execute(db.select(User).filter_by(email="anna@example.org")).scalar_one()
        assert restored.password_hash == password_hash  # the same passwords still work
        assert restored.member.last_name == "Popović" and restored.member.payment_status == "paid"
        assert db.session.get(Setting, "forum_base_url").value == "https://forum.example.org"
        assert db.session.execute(db.select(MailAccount)).scalar_one().password == "smtp-secret"

        storage = tmp_path / "storage"
        assert (storage / "forum_avatar_staging" / "avatar-1-abc.png").read_bytes() == b"\x89PNG picture bytes"
        assert not (storage / "left-over.txt").exists()

        # The machine's own values stay behind; configuration comes along.
        assert result["env"] == {
            "SECRET_KEY": "old-secret", "STRIPE_SECRET_KEY": "sk_test_old", "MAIL_ACCOUNTS_JSON": '{"a": 1}',
        }

    def test_the_file_is_encrypted(self, portal, tmp_path):
        _fill(tmp_path)
        _backup(tmp_path)
        raw = (tmp_path / "backups" / "b.jabackup").read_bytes()
        for secret in (b"anna@example.org", b"smtp-secret", b"old-secret", b"Popovi"):
            assert secret not in raw

    def test_the_backup_is_checked_after_writing_and_nothing_else_is_kept(self, portal, tmp_path):
        _fill(tmp_path)
        _backup(tmp_path)
        assert [p.name for p in (tmp_path / "backups").iterdir()] == ["b.jabackup"]


class TestNothingIsTouchedWhenTheBackupCannotBeUsed:
    def _users(self):
        return sorted(db.session.execute(db.select(User.email)).scalars())

    def test_wrong_passphrase(self, portal, tmp_path):
        _fill(tmp_path)
        _backup(tmp_path)
        _fresh_install(tmp_path)
        before = self._users()

        with pytest.raises(BackupError, match="passphrase is wrong"):
            backup_service.restore_backup(tmp_path / "backups" / "b.jabackup", "not the passphrase at all")
        assert self._users() == before

    def test_a_file_cut_short(self, portal, tmp_path):
        _fill(tmp_path)
        _backup(tmp_path)
        path = tmp_path / "backups" / "b.jabackup"
        path.write_bytes(path.read_bytes()[:-40])
        _fresh_install(tmp_path)
        before = self._users()

        with pytest.raises(BackupError, match="incomplete|damaged"):
            backup_service.restore_backup(path, PASSPHRASE)
        assert self._users() == before

    def test_a_backup_from_a_newer_version(self, portal, tmp_path, monkeypatch):
        """Restoring onto older code is refused: it could not rebuild that schema."""
        _fill(tmp_path)
        monkeypatch.setattr(backup_service, "schema_revision", lambda: "f00dfeedbeef")
        _backup(tmp_path)
        monkeypatch.undo()
        before = self._users()

        with pytest.raises(BackupError, match="newer version"):
            backup_service.restore_backup(tmp_path / "backups" / "b.jabackup", PASSPHRASE)
        assert self._users() == before

    def test_not_a_backup_at_all(self, portal, tmp_path):
        path = tmp_path / "holiday.jpg"
        path.write_bytes(b"\xff\xd8 a photo")
        with pytest.raises(BackupError, match="not a backup"):
            backup_service.restore_backup(path, PASSPHRASE)


def test_a_short_passphrase_is_refused(portal, tmp_path):
    with pytest.raises(BackupError, match="at least"):
        _backup(tmp_path, passphrase="short")


def test_an_older_backup_is_migrated_forward_like_an_update(portal, tmp_path):
    """A backup from an earlier version is rebuilt at its own version, then upgraded."""
    script = backup_service._script_directory()
    head = script.get_current_head()
    older = script.get_revision(head).down_revision

    backup_service._drop_everything()
    backup_service._migrate(older)
    db.session.execute(db.text("INSERT INTO setting (key, value) VALUES ('forum_base_url', 'https://old.example.org')"))
    db.session.commit()
    summary = _backup(tmp_path)
    assert summary["schema_revision"] == older

    _fresh_install(tmp_path)
    backup_service.restore_backup(tmp_path / "backups" / "b.jabackup", PASSPHRASE)

    assert backup_service.schema_revision() == head
    assert db.session.get(Setting, "forum_base_url").value == "https://old.example.org"


def test_pictures_follow_a_new_install_directory(portal, tmp_path):
    _fill(tmp_path)
    _backup(tmp_path)
    moved = tmp_path / "elsewhere" / "storage"
    portal.config["BACKUP_STORAGE_DIR"] = str(moved)

    backup_service.restore_backup(tmp_path / "backups" / "b.jabackup", PASSPHRASE)

    submission = db.session.execute(db.select(ForumAvatarSubmission)).scalar_one()
    assert submission.storage_path == str(moved / "forum_avatar_staging" / "avatar-1-abc.png")
    assert (moved / "forum_avatar_staging" / "avatar-1-abc.png").is_file()


def test_carried_env_values_replace_only_their_own_lines(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET_KEY='new-machine'\nDB_PASSWORD='keep-me'\nPUBLIC_BASE_URL='https://new.test'\n")

    backup_service.apply_env_values(env_file, {
        "SECRET_KEY": "old-secret", "STRIPE_SECRET_KEY": "sk_o'ld", "DB_PASSWORD": "never-carried",
    })

    assert env_file.read_text() == (
        "SECRET_KEY='old-secret'\n"
        "DB_PASSWORD='keep-me'\n"
        "PUBLIC_BASE_URL='https://new.test'\n"
        "STRIPE_SECRET_KEY=\"sk_o'ld\"\n"
    )
    from dotenv import dotenv_values
    assert dotenv_values(env_file)["STRIPE_SECRET_KEY"] == "sk_o'ld"


def test_kept_backups_cannot_be_reached_outside_their_folder(portal, tmp_path):
    (tmp_path / "backups").mkdir()
    (tmp_path / "secret.jabackup").write_text("x")
    assert backup_service.backup_path("../secret.jabackup") is None
    assert backup_service.backup_path("nothing.jabackup") is None


@pytest.mark.parametrize("value", ["plain-hex-0123", "it's", 'say "hi"', "back\\slash's"])
def test_env_values_read_back_the_same_in_bash_and_python(tmp_path, value):
    """The installer sources .env with bash, the portal reads it with python-dotenv."""
    import subprocess

    from dotenv import dotenv_values

    env_file = tmp_path / ".env"
    backup_service.apply_env_values(env_file, {"SECRET_KEY": value})

    assert dotenv_values(env_file)["SECRET_KEY"] == value
    from_bash = subprocess.run(
        ["bash", "-c", f"source '{env_file}'; printf '%s' \"$SECRET_KEY\""],
        capture_output=True, text=True, check=True,
    ).stdout
    assert from_bash == value


def test_a_value_neither_reader_would_agree_on_is_refused(tmp_path):
    with pytest.raises(BackupError, match="cannot hold"):
        backup_service.apply_env_values(tmp_path / ".env", {"SECRET_KEY": "it's $5"})


class TestTheCommands:
    def test_backup_then_restore_leaves_the_background_jobs_paused(self, portal, tmp_path):
        _fill(tmp_path)
        runner = portal.test_cli_runner()

        made = runner.invoke(args=["create-backup", "--passphrase-stdin", "--created-by", "anna@example.org"],
                             input=PASSPHRASE + "\n")
        assert made.exit_code == 0, made.output
        [kept] = backup_service.list_backups()
        status = backup_service.read_status()
        assert status["state"] == "completed" and all(s["state"] == "done" for s in status["steps"])

        _fresh_install(tmp_path)
        env_out = tmp_path / "restored-env.json"
        restored = runner.invoke(
            args=["restore-backup", str(tmp_path / "backups" / kept["name"]), "--passphrase-stdin", "--yes",
                  "--env-out", str(env_out)],
            input=PASSPHRASE + "\n",
        )
        assert restored.exit_code == 0, restored.output

        assert json.loads(env_out.read_text())["SECRET_KEY"] == "old-secret"
        assert background_jobs.is_paused()
        assert db.session.execute(db.select(Member)).scalars().all()  # the members are back

        stood_down = runner.invoke(args=["process-external-work"])
        assert "paused" in stood_down.output
        seen_at, was_paused = background_jobs.last_check_in("external-work")
        assert seen_at is not None and was_paused

    def test_restore_refuses_the_wrong_passphrase_before_asking_anything(self, portal, tmp_path):
        _fill(tmp_path)
        _backup(tmp_path)
        result = portal.test_cli_runner().invoke(
            args=["restore-backup", str(tmp_path / "backups" / "b.jabackup"), "--passphrase-stdin", "--yes",
                  "--env-out", str(tmp_path / "e.json")],
            input="wrong wrong wrong\n",
        )
        assert result.exit_code != 0 and "passphrase is wrong" in result.output
        assert not background_jobs.is_paused()

    def test_a_failed_backup_says_so_on_the_page(self, portal, tmp_path):
        result = portal.test_cli_runner().invoke(args=["create-backup", "--passphrase-stdin"], input="short\n")
        assert result.exit_code != 0
        status = backup_service.read_status()
        assert status["state"] == "failed" and "at least" in status["error"]
        assert backup_service.list_backups() == []


def _sign_in(client, *roles):
    from conftest import app_module

    user = User(email=f"{'-'.join(roles)}@example.org")
    user.set_password("x")
    db.session.add(user)
    for role in roles:
        user.grant_role(app_module.get_role(role))
    db.session.commit()
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
    return user


class TestThePage:
    def test_only_superadmins_see_or_use_it(self, portal, client):
        portal.config["BACKUP_RUN_INLINE"] = True
        _sign_in(client, "admin")
        refused = client.post("/admin/backup", data={"passphrase": PASSPHRASE, "passphrase_confirm": PASSPHRASE})
        assert refused.status_code == 302 and backup_service.read_status() is None
        assert client.get("/admin/backup/status").status_code == 302
        assert 'id="backup-restore"' not in client.get("/admin/settings").get_data(as_text=True)

    def test_a_backup_made_from_the_page_can_be_downloaded(self, portal, client, tmp_path):
        portal.config["BACKUP_RUN_INLINE"] = True
        _fill(tmp_path)
        _sign_in(client, "admin", "superadmin")

        client.post("/admin/backup", data={"passphrase": PASSPHRASE, "passphrase_confirm": PASSPHRASE})

        status = client.get("/admin/backup/status").get_json()
        assert status["state"] == "completed", status
        [kept] = backup_service.list_backups()
        page = client.get("/admin/settings").get_data(as_text=True)
        assert kept["name"] in page and 'id="backup-restore"' in page
        download = client.get(f"/admin/backup/files/{kept['name']}")
        assert download.status_code == 200
        assert download.data.startswith(backup_service.MAGIC)

    def test_mismatched_passphrases_start_nothing(self, portal, client):
        portal.config["BACKUP_RUN_INLINE"] = True
        _sign_in(client, "admin", "superadmin")
        client.post("/admin/backup", data={"passphrase": PASSPHRASE, "passphrase_confirm": PASSPHRASE + "!"})
        assert backup_service.read_status() is None

    def test_a_download_cannot_leave_the_backups_folder(self, portal, client):
        _sign_in(client, "admin", "superadmin")
        assert client.get("/admin/backup/files/..%2F.env").status_code == 404


class TestResuming:
    @pytest.fixture
    def checks(self, monkeypatch):
        """The three service checks, without reaching out to the internet."""
        from aeronautics_members.services import resume_checks

        ran = []

        def fake(name):
            def run():
                ran.append(name)
                return "ok", f"{name} works"
            return run

        monkeypatch.setattr(resume_checks, "_RUNNERS", {name: fake(name) for name in ("stripe", "forum", "mail")})
        return ran

    def test_paused_until_someone_confirms(self, portal, client, checks):
        _sign_in(client, "admin", "superadmin")
        background_jobs.pause("restore", backup_created_at="2026-09-01T10:00:00+00:00")
        db.session.commit()
        assert "restored from a backup" in client.get("/admin").get_data(as_text=True)

        client.post("/admin/background-jobs/resume", data={})
        assert background_jobs.is_paused()

        client.post("/admin/background-jobs/resume", data={"confirm": "resume"})
        assert not background_jobs.is_paused()
        assert "restored from a backup" not in client.get("/admin").get_data(as_text=True)

    def test_the_checklist_runs_one_check_per_look(self, portal, client, checks):
        _sign_in(client, "admin", "superadmin")
        background_jobs.resume("someone@example.org")
        db.session.commit()

        first = client.get("/admin/background-jobs/checklist").get_json()
        states = {item["key"]: item["state"] for item in first["items"]}
        assert checks == ["stripe"]
        assert states["stripe"] == "ok" and states["forum"] == "running" and states["mail"] == "running"
        assert states["job-external-work"] == "waiting"
        assert not first["done"]

        client.get("/admin/background-jobs/checklist")
        client.get("/admin/background-jobs/checklist")
        assert checks == ["stripe", "forum", "mail"]

    def test_a_timer_counts_once_it_has_run_since_resuming(self, portal, client, checks):
        _sign_in(client, "admin", "superadmin")
        background_jobs.resume("someone@example.org")
        db.session.commit()
        portal.test_cli_runner().invoke(args=["process-external-work"])

        items = client.get("/admin/background-jobs/checklist").get_json()["items"]
        assert {i["key"]: i["state"] for i in items}["job-external-work"] == "ok"


def test_backups_kept_in_storage_are_neither_backed_up_nor_wiped(portal, tmp_path):
    """The default place for backups is storage/backups, inside what gets backed up."""
    portal.config.pop("BACKUP_DIR")
    _fill(tmp_path)
    older = tmp_path / "storage" / "backups" / "portal-backup-older.jabackup"
    older.parent.mkdir(parents=True)
    older.write_bytes(b"an older backup")

    summary = backup_service.create_backup(tmp_path / "storage" / "backups" / "new.jabackup", PASSPHRASE)
    assert summary["files"] == 1  # the picture, not the backups

    backup_service.restore_backup(tmp_path / "storage" / "backups" / "new.jabackup", PASSPHRASE)
    assert older.read_bytes() == b"an older backup"
