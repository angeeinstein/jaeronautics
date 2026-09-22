#!/usr/bin/env bash
# Moving the database, in both directions, with the data following it.
#
# Not part of the pytest suite: this needs a live MariaDB, because the whole
# point is whether real dumps of a real schema survive a real round trip.
# Charset in particular cannot be checked any other way -- the member table is
# full of German names and addresses, and a mismatched collation corrupts them
# silently, at the one moment nobody is looking.
#
#     sudo apt-get install -y mariadb-server
#     sudo tests/test_install_db_migration.sh
#
# It creates and drops its own databases (mig_source_test, mig_dest_test) and
# leaves the rest of the server alone.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

# install.sh runs main at the bottom; strip it so the functions can be sourced.
sed 's/^main "\$@"$/: # disabled for tests/' "${REPO_ROOT}/install.sh" > "${WORK}/lib.sh"
# shellcheck disable=SC1091
source "${WORK}/lib.sh"
trap - ERR EXIT
set +e

BACKUP_DIR="${WORK}/backups"; mkdir -p "${BACKUP_DIR}"
ENV_FILE="${WORK}/fake.env"
PASS=0; FAIL=0

check() {
    if [[ "$2" == "$3" ]]; then printf '  PASS  %s\n' "$1"; PASS=$((PASS+1))
    else printf '  FAIL  %s (got %s, want %s)\n' "$1" "$2" "$3"; FAIL=$((FAIL+1)); fi
}

# A host that is not spelled "localhost", so the external code path is taken
# even though the server answering is this one.
grep -q ' db-external$' /etc/hosts || echo "127.0.0.1 db-external" >> /etc/hosts

mariadb -e "
DROP DATABASE IF EXISTS mig_source_test; DROP DATABASE IF EXISTS mig_dest_test;
CREATE DATABASE mig_source_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE mig_dest_test   CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'migtest'@'localhost' IDENTIFIED BY 'migpass';
GRANT ALL ON mig_source_test.* TO 'migtest'@'localhost';
GRANT ALL ON mig_dest_test.*   TO 'migtest'@'localhost'; FLUSH PRIVILEGES;" || exit 1

# German text, because that is what this database actually holds.
#
# --default-character-set=utf8mb4 is not decoration. Without it the client
# announces latin1, the server dutifully re-encodes these UTF-8 bytes, and the
# fixture is double-encoded before the migration has even started -- which is
# exactly how this goes wrong on a real box, and why the dump and the import
# both pass the same flag.
mariadb --default-character-set=utf8mb4 mig_source_test -e "
CREATE TABLE member (id INT PRIMARY KEY, first_name VARCHAR(80), city VARCHAR(80))
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
INSERT INTO member VALUES (1,'Jürgen','Österreich'),(2,'Maße','Graz');
CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY);
INSERT INTO alembic_version VALUES ('f3a17c92de48');"

echo "external -> local"
SOURCE_DB_HOST="db-external"; SOURCE_DB_PORT=3306; SOURCE_DB_NAME="mig_source_test"
SOURCE_DB_USER="migtest";     SOURCE_DB_PASSWORD="migpass"
DB_HOST="127.0.0.1"; DB_PORT=3306; DB_NAME="mig_dest_test"; DB_USER="root"; DB_PASSWORD=""
MIGRATION_OVERWRITE_DEST=0
dump_source_database    >/dev/null 2>&1; check "dump taken"   "$?" "0"
import_migrated_database >/dev/null 2>&1; check "data loaded"  "$?" "0"
check "umlauts survived" \
    "$(mariadb --default-character-set=utf8mb4 -N -B mig_dest_test -e 'SELECT first_name FROM member WHERE id=1')" \
    "Jürgen"
# Byte-level, because a client that displays it correctly can still be
# compensating for a mistake made on the way in.
check "bytes are UTF-8" \
    "$(mariadb -N -B mig_dest_test -e 'SELECT HEX(first_name) FROM member WHERE id=1')" \
    "$(printf 'Jürgen' | od -An -tx1 | tr -d ' \n' | tr '[:lower:]' '[:upper:]')"
check "alembic revision carried" \
    "$(mariadb -N -B mig_dest_test -e 'SELECT version_num FROM alembic_version')" "f3a17c92de48"
GOOD_DUMP="${MIGRATION_DUMP_FILE}"

echo "local -> external (the move onto a managed server)"
mariadb -e "DROP DATABASE mig_dest_test; CREATE DATABASE mig_dest_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
SOURCE_DB_HOST="127.0.0.1"; SOURCE_DB_NAME="mig_source_test"; SOURCE_DB_USER="root"; SOURCE_DB_PASSWORD=""
DB_HOST="db-external"; DB_NAME="mig_dest_test"; DB_USER="migtest"; DB_PASSWORD="migpass"
dump_source_database    >/dev/null 2>&1; check "dump taken"  "$?" "0"
# An empty destination is the normal case here and must not read as unreachable.
import_migrated_database >/dev/null 2>&1; check "empty destination accepted" "$?" "0"

echo "refusals"
head -c 200 "${GOOD_DUMP}" > "${WORK}/truncated.sql"
verify_sql_dump "${WORK}/truncated.sql" >/dev/null 2>&1; check "dump cut short refused" "$?" "1"
: > "${WORK}/empty.sql"
verify_sql_dump "${WORK}/empty.sql" >/dev/null 2>&1; check "empty dump refused" "$?" "1"
verify_sql_dump "${GOOD_DUMP}" >/dev/null 2>&1;      check "complete dump accepted" "$?" "0"

SOURCE_DB_HOST="db-external"; SOURCE_DB_NAME="no_such_database"; SOURCE_DB_USER="migtest"; SOURCE_DB_PASSWORD="migpass"
( dump_source_database ) >/dev/null 2>&1; check "missing source refused" "$?" "1"
SOURCE_DB_NAME="mig_source_test"; SOURCE_DB_PASSWORD="wrong"
( dump_source_database ) >/dev/null 2>&1; check "wrong password refused" "$?" "1"

MIGRATION_SOURCE_COUNTS=$'member\t2\nphantom_table\t99'
DB_HOST="127.0.0.1"; DB_NAME="mig_dest_test"; DB_USER="root"; DB_PASSWORD=""
MIGRATION_DUMP_FILE="${GOOD_DUMP}"
( verify_migrated_database ) >/dev/null 2>&1; check "missing rows detected" "$?" "1"

echo "credentials"
set_db_conn_args "db-external" 3306 migtest migpass
CREDS="${MIGRATION_TEMP_FILES[-1]}"
check "credential file is 0600" "$(stat -c %a "${CREDS}")" "600"
check "password not in argv"    "$(printf '%s ' "${DB_CONN_ARGS[@]}" | grep -c migpass)" "0"
cleanup_migration_temp_files
check "credential file removed" "$([[ -e "${CREDS}" ]] && echo present || echo gone)" "gone"

mariadb -e "DROP DATABASE IF EXISTS mig_source_test; DROP DATABASE IF EXISTS mig_dest_test;
            DROP USER IF EXISTS 'migtest'@'localhost';"

printf '\npassed=%d failed=%d\n' "${PASS}" "${FAIL}"
[[ "${FAIL}" -eq 0 ]]
