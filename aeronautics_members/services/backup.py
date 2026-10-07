"""Backup and restore: everything that makes this installation different from a fresh one.

A backup is one file. Restored onto a fresh install it gives back the whole
portal as it was: every account and membership, every setting and credential
entered on the settings pages, the uploaded pictures still waiting for review,
and the few values in ``.env`` that are configuration rather than machine
(``SECRET_KEY``, the Stripe keys, the mail accounts). Not carried: the database
password, paths, the domain -- they belong to the machine the backup lands on.

**The file is encrypted.** It holds every member's address and every password
the portal has, so it is only ever written encrypted: AES-256-GCM under a key
derived (scrypt) from a passphrase that is not stored anywhere. Without the
passphrase the backup cannot be restored -- by anyone, including us.

Inside, once decrypted, it is a gzipped tar:

* ``manifest.json`` -- first, so a restore can refuse a backup from a newer
  version before it touches anything;
* ``database/<table>.jsonl`` -- every table, the column names on the first
  line and one row per line after;
* ``files/...`` -- the portal's ``storage`` directory;
* ``env.json`` -- the carried ``.env`` values;
* ``contents.json`` -- last: row counts and file checksums, which the check
  after writing and every restore hold the rest against.

The database is copied table by table rather than with ``mysqldump``: it works
the same on MariaDB, MySQL and the SQLite the tests use, and it lets a restore
rebuild the schema at the backup's own version, load the rows, and then
migrate forward exactly as an update would.
"""

import base64
import datetime as dt
import decimal
import gzip
import hashlib
import io
import json
import os
import shutil
import struct
import tarfile
import zlib
from contextlib import contextmanager
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from dotenv import dotenv_values
from flask import current_app
from sqlalchemy import MetaData, select

from ..config import ROOT_ENV_PATH
from ..db_models import db

FORMAT = 1
MAGIC = b"JAERONAUTICS-PORTAL-BACKUP\n"
CHUNK_SIZE = 1 << 20
MIN_PASSPHRASE_LENGTH = 12
# Finished backups kept in the backups folder; older ones are removed when a
# new one is made. Where backups should live for good is decided later.
BACKUPS_KEPT = 5

# The .env values that are configuration rather than machine. The installer
# writes exactly these plus the database, domain and path settings.
CARRIED_ENV_KEYS = (
    "SECRET_KEY",
    "STRIPE_SECRET_KEY",
    "STRIPE_PUBLISHABLE_KEY",
    "STRIPE_PRICE_ID",
    "STRIPE_WEBHOOK_SECRET",
    "MAIL_ACCOUNTS_JSON",
)

# Columns that hold absolute paths into the storage directory. A restore onto
# an installation in another directory moves them along.
STORAGE_PATH_COLUMNS = {
    "imported_forum_profiles": ("avatar_path",),
    "forum_avatar_submissions": ("storage_path",),
    "teams": ("logo_path", "picture_path"),
    "team_photos": ("path",),
}

_TYPE_TAG = "__backup_type__"


class BackupError(Exception):
    """Something the person running the backup or restore needs to read."""


# ---- Where things are ----------------------------------------------------------


def storage_dir():
    return Path(current_app.config.get("BACKUP_STORAGE_DIR") or (Path(current_app.root_path).parent / "storage"))


def backup_dir():
    """Where finished backups are kept: ``storage/backups``.

    Inside the storage directory because that is the one place the portal's
    service may write (its systemd unit makes the rest read-only). Backups
    themselves are left out of every backup and every restore.
    """
    return Path(current_app.config.get("BACKUP_DIR") or (storage_dir() / "backups"))


def env_file_path():
    return Path(current_app.config.get("BACKUP_ENV_FILE") or ROOT_ENV_PATH)


def _scrypt_n():
    # 2**16 needs about 64 MB for a moment; the tests turn it down.
    return int(current_app.config.get("BACKUP_SCRYPT_N", 2**16))


def _private_dir(path):
    """Make a folder that only the portal's own user can look into."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass  # a folder given explicitly, not ours to change


# ---- Encryption --------------------------------------------------------------------


def _derive_key(passphrase, salt, n, r, p):
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(passphrase.encode("utf-8"))


def _nonce(prefix, counter, final):
    return prefix + struct.pack(">IB", counter, 1 if final else 0)


class _EncryptingWriter(io.RawIOBase):
    """Encrypts whatever is written, in 1 MB chunks, each sealed on its own.

    Every chunk's nonce carries its position and whether it is the last one,
    so a file cut short, reordered or extended fails to decrypt rather than
    restoring half a database.
    """

    def __init__(self, raw, passphrase, n):
        salt = os.urandom(16)
        self._prefix = os.urandom(7)
        header = json.dumps({
            "format": FORMAT,
            "cipher": "AES-256-GCM",
            "kdf": {"name": "scrypt", "n": n, "r": 8, "p": 1, "salt": base64.b64encode(salt).decode()},
            "nonce_prefix": base64.b64encode(self._prefix).decode(),
            "chunk_size": CHUNK_SIZE,
        }, sort_keys=True).encode() + b"\n"
        raw.write(MAGIC)
        raw.write(header)
        self._aad = MAGIC + header
        self._aes = AESGCM(_derive_key(passphrase, salt, n, 8, 1))
        self._raw = raw
        self._buffer = bytearray()
        self._counter = 0

    def writable(self):
        return True

    def write(self, data):
        self._buffer += data
        # Strictly more than a chunk: whatever is left at close() goes out as
        # the final chunk, even when that is empty.
        while len(self._buffer) > CHUNK_SIZE:
            self._seal(bytes(self._buffer[:CHUNK_SIZE]), final=False)
            del self._buffer[:CHUNK_SIZE]
        return len(data)

    def _seal(self, data, final):
        sealed = self._aes.encrypt(_nonce(self._prefix, self._counter, final), data, self._aad)
        self._raw.write(struct.pack(">I", len(sealed)))
        self._raw.write(sealed)
        self._counter += 1

    def close(self):
        if not self.closed:
            self._seal(bytes(self._buffer), final=True)
            self._buffer.clear()
        super().close()


class _DecryptingReader(io.RawIOBase):
    def __init__(self, raw, passphrase):
        if raw.read(len(MAGIC)) != MAGIC:
            raise BackupError("This is not a backup of the membership portal.")
        header_line = raw.readline()
        try:
            header = json.loads(header_line)
            if header["format"] > FORMAT:
                raise BackupError(
                    "This backup was written by a newer version of the portal. "
                    "Update this installation first."
                )
            kdf = header["kdf"]
            salt = base64.b64decode(kdf["salt"])
            self._prefix = base64.b64decode(header["nonce_prefix"])
            key = _derive_key(passphrase, salt, int(kdf["n"]), int(kdf["r"]), int(kdf["p"]))
        except BackupError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise BackupError("The backup file is damaged (its header cannot be read).") from exc
        self._aad = MAGIC + header_line
        self._aes = AESGCM(key)
        self._raw = raw
        self._buffer = b""
        self._counter = 0
        self._finished = False

    def readable(self):
        return True

    def readinto(self, target):
        while not self._buffer and not self._finished:
            self._open_next_chunk()
        count = min(len(target), len(self._buffer))
        target[:count] = self._buffer[:count]
        self._buffer = self._buffer[count:]
        return count

    def _open_next_chunk(self):
        length_bytes = self._raw.read(4)
        if len(length_bytes) < 4:
            raise BackupError("The backup file is incomplete: it ends before its last part.")
        (length,) = struct.unpack(">I", length_bytes)
        sealed = self._raw.read(length)
        if len(sealed) < length:
            raise BackupError("The backup file is incomplete: it ends before its last part.")
        for final in (False, True):
            try:
                plain = self._aes.decrypt(_nonce(self._prefix, self._counter, final), sealed, self._aad)
            except InvalidTag:
                continue
            self._counter += 1
            self._buffer = plain
            if final:
                self._finished = True
                if self._raw.read(1):
                    raise BackupError("The backup file is damaged: there is data after its end.")
            return
        if self._counter == 0:
            raise BackupError("The passphrase is wrong, or the backup file is damaged.")
        raise BackupError("The backup file is damaged and cannot be decrypted.")


@contextmanager
def _open_backup_tar(path, passphrase):
    with open(path, "rb") as raw:
        reader = io.BufferedReader(_DecryptingReader(raw, passphrase), buffer_size=CHUNK_SIZE)
        try:
            with tarfile.open(fileobj=reader, mode="r|gz") as archive:
                yield archive
        except (tarfile.TarError, EOFError, zlib.error, gzip.BadGzipFile) as exc:
            raise BackupError(f"The backup file is damaged: {exc}") from exc


# ---- Values --------------------------------------------------------------------------


def _encode(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dt.datetime):
        return {_TYPE_TAG: "datetime", "v": value.isoformat()}
    if isinstance(value, dt.date):
        return {_TYPE_TAG: "date", "v": value.isoformat()}
    if isinstance(value, dt.time):
        return {_TYPE_TAG: "time", "v": value.isoformat()}
    if isinstance(value, decimal.Decimal):
        return {_TYPE_TAG: "decimal", "v": str(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {_TYPE_TAG: "bytes", "v": base64.b64encode(bytes(value)).decode()}
    if isinstance(value, (dict, list)):
        return {_TYPE_TAG: "json", "v": value}
    raise BackupError(f"A value of type {type(value).__name__} cannot be backed up.")


def _decode(value):
    if not isinstance(value, dict) or _TYPE_TAG not in value:
        return value
    kind, raw = value[_TYPE_TAG], value["v"]
    if kind == "datetime":
        return dt.datetime.fromisoformat(raw)
    if kind == "date":
        return dt.date.fromisoformat(raw)
    if kind == "time":
        return dt.time.fromisoformat(raw)
    if kind == "decimal":
        return decimal.Decimal(raw)
    if kind == "bytes":
        return base64.b64decode(raw)
    if kind == "json":
        return raw
    raise BackupError(f"The backup holds a value of an unknown type ({kind}).")


# ---- Reading the installation -------------------------------------------------------


def _reflect():
    metadata = MetaData()
    metadata.reflect(bind=db.engine)
    return metadata


def schema_revision():
    try:
        return db.session.execute(db.text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001 -- no table means no revision
        return None


def _portal_revision():
    try:
        from .system_update import get_local_version

        return get_local_version().get("revision")
    except Exception:  # noqa: BLE001 -- a backup without a git checkout is still a backup
        return None


def _carried_env_values():
    path = env_file_path()
    values = dotenv_values(path) if path.is_file() else {}
    return {key: values[key] for key in CARRIED_ENV_KEYS if values.get(key)}


def _add_bytes(archive, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(dt.datetime.now().timestamp())
    info.mode = 0o600
    archive.addfile(info, io.BytesIO(data))


def _table_lines(connection, table):
    """One table as JSON lines: its column names, then a row per line."""
    columns = [column.name for column in table.columns]
    buffer = io.BytesIO()
    buffer.write(json.dumps({"columns": columns}).encode() + b"\n")
    count = 0
    result = connection.execution_options(stream_results=True).execute(select(table))
    for row in result:
        buffer.write(json.dumps([_encode(value) for value in row], separators=(",", ":")).encode() + b"\n")
        count += 1
    return buffer.getvalue(), count


def _storage_files():
    root = storage_dir()
    if not root.is_dir():
        return []
    from ..legal_pdf import cache_dir as legal_pdf_dir

    # Not the backups themselves, nor the legal texts' PDFs: made again from
    # the texts in the repository whenever they are missing.
    excluded = {backup_dir().resolve(), legal_pdf_dir().resolve()}
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and not excluded.intersection(path.resolve().parents):
            files.append(path)
    return files


def create_backup(destination, passphrase, *, created_by=None, progress=None):
    """Write an encrypted backup to ``destination`` and check it.

    ``progress(step, message)`` is told what is happening, for the page that
    shows it. Returns a summary; raises BackupError when anything is wrong, and
    then leaves no file behind.
    """
    if len(passphrase or "") < MIN_PASSPHRASE_LENGTH:
        raise BackupError(f"The passphrase must be at least {MIN_PASSPHRASE_LENGTH} characters long.")
    report = progress or (lambda step, message: None)
    destination = Path(destination)
    _private_dir(destination.parent)
    partial = destination.with_name(destination.name + ".partial")

    revision = schema_revision()
    if not revision:
        raise BackupError("The database has no schema version recorded, so a restore could not rebuild it.")

    manifest = {
        "format": FORMAT,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "created_by": created_by,
        "schema_revision": revision,
        "portal_revision": _portal_revision(),
        "database_dialect": db.engine.dialect.name,
        "storage_root": str(storage_dir()),
    }
    contents = {"tables": {}, "files": {}, "env_keys": []}

    try:
        with open(partial, "wb") as raw:
            os.chmod(partial, 0o600)
            writer = _EncryptingWriter(raw, passphrase, _scrypt_n())
            with tarfile.open(fileobj=writer, mode="w|gz") as archive:
                _add_bytes(archive, "manifest.json", json.dumps(manifest, indent=1).encode())

                metadata = _reflect()
                tables = metadata.sorted_tables
                report("database", f"Copying the database ({len(tables)} tables)")
                with db.engine.connect() as connection:
                    for table in tables:
                        data, count = _table_lines(connection, table)
                        _add_bytes(archive, f"database/{table.name}.jsonl", data)
                        contents["tables"][table.name] = count
                        report("database", f"{table.name}: {count} rows")

                files = _storage_files()
                report("files", f"Adding uploaded files ({len(files)})")
                root = storage_dir()
                for path in files:
                    relative = path.relative_to(root).as_posix()
                    data = path.read_bytes()
                    contents["files"][relative] = hashlib.sha256(data).hexdigest()
                    _add_bytes(archive, f"files/{relative}", data)

                report("configuration", "Adding configuration")
                env_values = _carried_env_values()
                contents["env_keys"] = sorted(env_values)
                _add_bytes(archive, "env.json", json.dumps(env_values).encode())
                _add_bytes(archive, "contents.json", json.dumps(contents, indent=1).encode())
            writer.close()

        report("verify", "Checking the finished backup")
        checked = inspect_backup(partial, passphrase)
        if checked["contents"] != contents:
            raise BackupError("The finished backup does not read back the same. It was not kept.")
        partial.replace(destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {
        "file": destination.name,
        "size": destination.stat().st_size,
        "sha256": digest,
        "tables": len(contents["tables"]),
        "rows": sum(contents["tables"].values()),
        "files": len(contents["files"]),
        "created_at": manifest["created_at"],
        "schema_revision": revision,
    }


# ---- Reading a backup ---------------------------------------------------------------


def inspect_backup(path, passphrase):
    """Decrypt and read the whole backup without changing anything.

    Checks every row count and file checksum against the backup's own list.
    Returns its manifest and contents.
    """
    manifest = contents = None
    seen_tables, seen_files, env_values = {}, {}, None
    with _open_backup_tar(path, passphrase) as archive:
        for member in archive:
            data = archive.extractfile(member).read() if member.isfile() else b""
            name = member.name
            if manifest is None:
                if name != "manifest.json":
                    raise BackupError("The backup file is damaged: it does not start with its manifest.")
                manifest = json.loads(data)
            elif name.startswith("database/") and name.endswith(".jsonl"):
                lines = data.splitlines()
                seen_tables[name[len("database/"):-len(".jsonl")]] = max(len(lines) - 1, 0)
            elif name.startswith("files/"):
                seen_files[name[len("files/"):]] = hashlib.sha256(data).hexdigest()
            elif name == "env.json":
                env_values = json.loads(data)
            elif name == "contents.json":
                contents = json.loads(data)
    if manifest is None or contents is None or env_values is None:
        raise BackupError("The backup file is incomplete: part of it is missing.")
    if seen_tables != contents["tables"] or seen_files != contents["files"]:
        raise BackupError("The backup file is damaged: its contents do not match its own list.")
    return {"manifest": manifest, "contents": contents}


def check_restorable(manifest):
    """Refuse a backup this code cannot rebuild. Returns the backup's schema revision."""
    if manifest.get("format", 0) > FORMAT:
        raise BackupError("This backup was written by a newer version of the portal. Update this installation first.")
    revision = manifest.get("schema_revision")
    if not revision or not _known_revision(revision):
        raise BackupError(
            "This backup was made by a newer version of the portal than this one "
            f"(database version {revision or 'unknown'} is unknown here). "
            "Update this installation first, then restore."
        )
    return revision


def _script_directory():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    directory = current_app.extensions["migrate"].directory
    config = Config(str(Path(directory) / "alembic.ini"))
    config.set_main_option("script_location", str(directory))
    return ScriptDirectory.from_config(config)


def _known_revision(revision):
    try:
        return _script_directory().get_revision(revision) is not None
    except Exception:  # noqa: BLE001 -- alembic raises several kinds for "no such revision"
        return False


# ---- Restoring ----------------------------------------------------------------------


@contextmanager
def _foreign_keys_off(connection):
    dialect = connection.dialect.name
    if dialect in ("mysql", "mariadb"):
        connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
    elif dialect == "sqlite":
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    try:
        yield
    finally:
        if dialect in ("mysql", "mariadb"):
            connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")


def _drop_everything():
    db.session.remove()
    metadata = _reflect()
    with db.engine.begin() as connection, _foreign_keys_off(connection):
        metadata.drop_all(bind=connection)


def _migrate(revision):
    from flask_migrate import upgrade

    db.session.remove()
    upgrade(revision=revision)
    db.session.remove()


def restore_backup(path, passphrase, *, progress=None):
    """Replace this installation's data with the backup's.

    Everything in the database goes, including any account made while
    installing: afterwards the portal holds exactly what the backup held. The
    backup is read in full before anything is touched, so a wrong passphrase
    or a damaged file changes nothing.

    Returns the carried ``.env`` values; writing them is the caller's job,
    because ``.env`` belongs to root and this may run as the portal's user.
    """
    report = progress or (lambda step, message: None)
    report("verify", "Reading and checking the backup")
    checked = inspect_backup(path, passphrase)
    manifest = checked["manifest"]
    revision = check_restorable(manifest)

    report("database", "Emptying the database")
    _drop_everything()
    report("database", f"Building the database as it was at version {revision}")
    _migrate(revision)

    metadata = _reflect()
    tables = {table.name: table for table in metadata.tables.values()}
    with db.engine.begin() as connection, _foreign_keys_off(connection):
        # Some migrations insert rows of their own (the roles); the backup's
        # rows replace them.
        for table in reversed(metadata.sorted_tables):
            if table.name != "alembic_version":
                connection.execute(table.delete())

    env_values = {}
    target_storage = storage_dir()
    storage_cleared = False
    with _open_backup_tar(path, passphrase) as archive:
        for member in archive:
            name = member.name
            data = archive.extractfile(member).read() if member.isfile() else b""
            if name.startswith("database/") and name.endswith(".jsonl"):
                table_name = name[len("database/"):-len(".jsonl")]
                if table_name == "alembic_version":
                    continue
                table = tables.get(table_name)
                if table is None:
                    raise BackupError(f"The backup holds a table this version does not have: {table_name}.")
                count = _insert_rows(table, data)
                report("database", f"{table_name}: {count} rows")
            elif name.startswith("files/"):
                if not storage_cleared:
                    report("files", "Restoring uploaded files")
                    _clear_storage(target_storage)
                    storage_cleared = True
                _write_storage_file(target_storage, name[len("files/"):], data)
            elif name == "env.json":
                env_values = json.loads(data)
    if not storage_cleared:
        _clear_storage(target_storage)

    old_root, new_root = manifest.get("storage_root"), str(target_storage)
    if old_root and old_root != new_root:
        report("files", "Pointing the database at this server's storage directory")
        _move_storage_paths(old_root, new_root)

    report("database", "Bringing the database up to this version")
    _migrate("head")
    return {"manifest": manifest, "contents": checked["contents"], "env": env_values}


def _insert_rows(table, data):
    lines = data.splitlines()
    if not lines:
        return 0
    columns = json.loads(lines[0])["columns"]
    unknown = [name for name in columns if name not in table.columns]
    if unknown:
        raise BackupError(f"The backup's {table.name} table has columns this version does not: {', '.join(unknown)}.")
    rows = [dict(zip(columns, (_decode(value) for value in json.loads(line)))) for line in lines[1:]]
    with db.engine.begin() as connection, _foreign_keys_off(connection):
        for start in range(0, len(rows), 500):
            connection.execute(table.insert(), rows[start:start + 500])
    return len(rows)


def _clear_storage(root):
    """Empty the storage directory -- except the backups, if they live there."""
    if not root.is_dir():
        root.mkdir(parents=True, exist_ok=True)
        return
    keep = backup_dir().resolve()
    for child in root.iterdir():
        if child.resolve() == keep or keep in child.resolve().parents:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _write_storage_file(root, relative, data):
    target = (root / relative).resolve()
    if root.resolve() not in target.parents:
        raise BackupError(f"The backup holds a file outside the storage directory: {relative}.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _move_storage_paths(old_root, new_root):
    metadata = _reflect()
    with db.engine.begin() as connection:
        for table_name, columns in STORAGE_PATH_COLUMNS.items():
            table = metadata.tables.get(table_name)
            if table is None:
                continue
            for column_name in columns:
                column = table.c[column_name]
                rows = connection.execute(
                    select(table.primary_key.columns.values()[0], column).where(column.like(f"{old_root}%"))
                ).all()
                for key, value in rows:
                    connection.execute(
                        table.update()
                        .where(table.primary_key.columns.values()[0] == key)
                        .values({column_name: new_root + value[len(old_root):]})
                    )


def apply_env_values(path, values):
    """Set the carried keys in an .env file, leaving every other line as it is."""
    path = Path(path)
    lines = path.read_text().splitlines() if path.is_file() else []
    remaining = {key: value for key, value in values.items() if key in CARRIED_ENV_KEYS}
    written = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            written.append(f"{key}={_dotenv_quote(remaining.pop(key))}")
        else:
            written.append(line)
    written.extend(f"{key}={_dotenv_quote(value)}" for key, value in remaining.items())
    path.write_text("\n".join(written) + "\n")


def _dotenv_quote(value):
    """Quote a value so that the installer (bash ``source``) and the portal
    (python-dotenv) both read back exactly the same text.

    Single quotes keep everything literal for both. A value that itself holds a
    single quote goes in double quotes, where both agree on ``\\`` and ``\"``
    but not on ``$`` or a backtick -- such a value is refused rather than
    written in a way one of them would misread.
    """
    value = str(value)
    if "'" not in value:
        return f"'{value}'"
    if "$" in value or "`" in value:
        raise BackupError(
            "A restored value holds both a quote and a $ or backtick, which .env cannot hold "
            "safely. Set it on the settings page instead."
        )
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---- Kept backups ------------------------------------------------------------------


def list_backups():
    root = backup_dir()
    if not root.is_dir():
        return []
    entries = []
    for path in sorted(root.glob("*.jabackup"), reverse=True):
        stat = path.stat()
        entries.append({
            "name": path.name,
            "size": stat.st_size,
            "size_display": human_size(stat.st_size),
            "modified": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc),
        })
    return entries


def backup_path(name):
    """The kept backup of that name, or None. Never a path outside the backup directory."""
    if not name or "/" in name or "\\" in name or not name.endswith(".jabackup"):
        return None
    path = backup_dir() / name
    return path if path.is_file() else None


def new_backup_path():
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return backup_dir() / f"portal-backup-{stamp}.jabackup"


def prune_backups(keep):
    for entry in list_backups()[keep:]:
        (backup_dir() / entry["name"]).unlink(missing_ok=True)


# ---- Progress, for the page that shows it -------------------------------------------

BACKUP_STEPS = (
    ("database", "Copying the database"),
    ("files", "Adding uploaded files"),
    ("configuration", "Adding configuration"),
    ("verify", "Checking the finished backup"),
)
_LOG_LINES = 60


def status_path():
    return backup_dir() / "status.json"


def _write_status(data):
    path = status_path()
    _private_dir(path.parent)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=1, default=str))
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _process_alive(pid):
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def read_status():
    """The last backup run as the page shows it, or None if there has not been one."""
    path = status_path()
    if not path.is_file():
        return None
    try:
        status = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if status.get("state") == "running" and status.get("pid") and not _process_alive(status["pid"]):
        status["state"] = "failed"
        status["error"] = "The backup stopped unexpectedly (the server may have restarted). Start it again."
    return status


def start_status(requested_by):
    """Record a backup as started, before the process doing it exists."""
    _write_status({
        "state": "running",
        "requested_by": requested_by,
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "steps": [{"key": key, "label": label, "state": "pending"} for key, label in BACKUP_STEPS],
        "log": [],
    })


def record_pid(pid):
    status = read_status() or {}
    status["pid"] = pid
    _write_status(status)


class StatusRecorder:
    """Keeps status.json up to date while a backup runs."""

    def __init__(self, requested_by):
        existing = read_status()
        if not existing or existing.get("state") != "running":
            start_status(requested_by)
        self.status = read_status()
        self.status["pid"] = os.getpid()
        _write_status(self.status)

    def __call__(self, step, message):
        reached = False
        for entry in self.status["steps"]:
            if entry["key"] == step:
                entry["state"] = "running"
                reached = True
            elif not reached:
                entry["state"] = "done"
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S")
        self.status["log"] = (self.status["log"] + [f"{stamp}  {message}"])[-_LOG_LINES:]
        _write_status(self.status)

    def finish(self, result):
        for entry in self.status["steps"]:
            entry["state"] = "done"
        self.status.update(state="completed", result=result,
                           finished_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
        _write_status(self.status)

    def fail(self, message):
        for entry in self.status["steps"]:
            if entry["state"] == "running":
                entry["state"] = "failed"
        self.status.update(state="failed", error=message,
                           finished_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
        _write_status(self.status)


def describe_backup_page():
    """Everything the Backup & Restore card shows, from one place."""
    from . import background_jobs, resume_checks

    paused = background_jobs.pause_state()
    resumed = background_jobs.resumed_state()
    return {
        "status": read_status(),
        "backups": list_backups(),
        "paused": paused,
        # While paused: whether each timer is firing, before anyone resumes.
        "jobs": [
            {"job": job, "state": state, "detail": detail}
            for job in background_jobs.JOBS
            for state, detail in [background_jobs.job_status(job)]
        ] if paused else [],
        "checklist": resume_checks.checklist(run_next=False) if resumed and not paused else None,
        "min_passphrase_length": MIN_PASSPHRASE_LENGTH,
        "kept": BACKUPS_KEPT,
    }


def human_size(size):
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
