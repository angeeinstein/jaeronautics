#!/usr/bin/env bash
# The connection variables below look unused to shellcheck: the functions that
# read them live in install.sh, which is sourced at runtime rather than parsed.
# shellcheck disable=SC2034
# An update whose migration fails undoes itself (docs/update-safety.md).
#
# Not part of the pytest suite: this needs a live MariaDB, because the point is
# what MariaDB does with a migration that stops part way -- it keeps the steps
# before -- and whether the database comes back exactly as it was. systemd, the
# code rollback and the migration itself are stand-ins; the dump, the restore
# and the checks are install.sh's own.
#
#     sudo apt-get install -y mariadb-server
#     sudo tests/test_install_update_undo.sh
#
# It creates and drops its own database (undo_test) and leaves the rest of the
# server alone.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"

# install.sh runs main at the bottom; strip it so the functions can be sourced.
sed 's/^main "\$@"$/: # disabled for tests/' "${REPO_ROOT}/install.sh" > "${WORK}/lib.sh"
# shellcheck disable=SC1091
source "${WORK}/lib.sh"
trap - ERR EXIT
trap 'rm -rf "${WORK}"; mariadb -e "DROP DATABASE IF EXISTS undo_test"' EXIT
set +e

BACKUP_DIR="${WORK}/backups"
SERVICE_NAME="undo-test"
DB_HOST="127.0.0.1"; DB_PORT=3306; DB_NAME="undo_test"; DB_USER="unused"; DB_PASSWORD="unused"
PASS=0; FAIL=0

check() {
    if [[ "$2" == "$3" ]]; then printf '  PASS  %s\n' "$1"; PASS=$((PASS+1))
    else printf '  FAIL  %s (got %s, want %s)\n' "$1" "$2" "$3"; FAIL=$((FAIL+1)); fi
}

# What the stand-ins were asked to do, one line each.
CALLS="${WORK}/calls"
systemctl() { echo "systemctl $*" >> "${CALLS}"; [[ "$1" != "is-active" ]]; }
roll_back_installation() { echo "rollback" >> "${CALLS}"; }
reload_services() { echo "reload" >> "${CALLS}"; }
called() { grep -c "^$1" "${CALLS}" 2>/dev/null || true; }

fresh_database() {
    : > "${CALLS}"
    rm -rf "${BACKUP_DIR}"
    mariadb -e "DROP DATABASE IF EXISTS undo_test;
        CREATE DATABASE undo_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" || exit 1
    mariadb --default-character-set=utf8mb4 undo_test -e "
        CREATE TABLE users (id INT PRIMARY KEY, name VARCHAR(80), city VARCHAR(80));
        INSERT INTO users VALUES (1,'Jürgen','Graz'),(2,'Maße','Wien');
        CREATE TABLE payments (id INT PRIMARY KEY, user_id INT, cents INT, team_bears_fee BOOL,
            FOREIGN KEY (user_id) REFERENCES users(id));
        INSERT INTO payments VALUES (1,1,2500,0),(2,2,1000,1);
        CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY);
        INSERT INTO alembic_version VALUES ('before');" || exit 1
}

# The whole database as text: every table's definition and rows.
snapshot() {
    local table
    for table in $(mariadb -N -B undo_test -e "SHOW TABLES"); do
        mariadb -N -B undo_test -e "SHOW CREATE TABLE \`${table}\`"
        mariadb -N -B --default-character-set=utf8mb4 undo_test -e "SELECT * FROM \`${table}\`"
    done
}

# What the failed live update did: steps that went through, then one that did not.
half_migration() {
    mariadb undo_test -e "
        CREATE TABLE stripe_bookings (id INT PRIMARY KEY, amount INT);
        INSERT INTO stripe_bookings VALUES (1, 500);
        ALTER TABLE payments DROP COLUMN team_bears_fee;
        UPDATE users SET city = 'changed';
        DELETE FROM setting WHERE key = 'x';" 2>/dev/null
}

echo "an update without a migration"
fresh_database
schema_migration_pending() { echo no; }
initialize_database_schema() { echo "migrate" >> "${CALLS}"; }
( migrate_database_for_update ) >/dev/null 2>&1
check "it migrates"                "$(called migrate)" "1"
check "the portal is not stopped"  "$(called 'systemctl stop')" "0"
check "no backup is taken"         "$(ls "${BACKUP_DIR}" 2>/dev/null | wc -l)" "0"

echo "an update whose migration goes through"
fresh_database
schema_migration_pending() { echo yes; }
initialize_database_schema() { echo "migrate" >> "${CALLS}"; mariadb undo_test -e "UPDATE alembic_version SET version_num='after'"; }
( migrate_database_for_update ) >/dev/null 2>&1
check "it ends well"               "$?" "0"
check "the portal was stopped"     "$(called 'systemctl stop undo-test$')" "1"
check "and its jobs, not only their timers" "$(called 'systemctl stop undo-test-.*\.service$')" "${#BACKGROUND_JOBS[@]}"
check "a backup was taken first"   "$(ls "${BACKUP_DIR}"/*-db-before-migration-*.sql 2>/dev/null | wc -l)" "1"
check "the backup is 0600"         "$(stat -c %a "${BACKUP_DIR}"/*-db-before-migration-*.sql)" "600"
check "nothing is rolled back"     "$(called rollback)" "0"
check "the schema moved on"        "$(mariadb -N -B undo_test -e 'SELECT version_num FROM alembic_version')" "after"

echo "an update whose migration stops part way"
fresh_database
before="$(snapshot)"
schema_migration_pending() { echo yes; }
initialize_database_schema() { echo "migrate" >> "${CALLS}"; half_migration; }
output="$( ( migrate_database_for_update ) 2>&1 )"
status=$?
check "the update counts as failed"   "$(( status != 0 ))" "1"
check "it says it was undone"          "$(grep -c 'The update was undone' <<<"${output}")" "1"
check "the half-made table is gone"    "$(mariadb -N -B undo_test -e "SHOW TABLES LIKE 'stripe_bookings'" | wc -l)" "0"
check "the dropped column is back"     "$(mariadb -N -B undo_test -e "SHOW COLUMNS FROM payments LIKE 'team_bears_fee'" | wc -l)" "1"
check "the database is exactly as before" "$( [[ "$(snapshot)" == "${before}" ]] && echo same || echo differs)" "same"
check "the previous code is put back"  "$(called rollback)" "1"
check "and started"                    "$(called reload)" "1"

echo "an update whose backup cannot be taken"
fresh_database
before="$(snapshot)"
schema_migration_pending() { echo unknown; }
initialize_database_schema() { echo "migrate" >> "${CALLS}"; }
db_dump_client() { return 1; }
output="$( ( migrate_database_for_update ) 2>&1 )"
status=$?
check "the update counts as failed"   "$(( status != 0 ))" "1"
check "nothing is migrated"            "$(called migrate)" "0"
check "the previous code is put back"  "$(called rollback)" "1"
check "it says so"                     "$(grep -c 'undone before migrating' <<<"${output}")" "1"
check "the database is untouched"      "$( [[ "$(snapshot)" == "${before}" ]] && echo same || echo differs)" "same"

printf '\n%d passed, %d failed\n' "${PASS}" "${FAIL}"
(( FAIL == 0 ))
