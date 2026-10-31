#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="jaeronautics"
DEFAULT_REPO_URL="https://github.com/angeeinstein/jaeronautics.git"
DEFAULT_BRANCH="main"
DEFAULT_INSTALL_DIR="/var/www/jaeronautics"
DEFAULT_SERVICE_NAME="jaeronautics"
DEFAULT_APP_USER="jaeronautics"
DEFAULT_APP_GROUP="jaeronautics"
DEFAULT_APP_PORT="8000"
DEFAULT_DB_NAME="jaeronautics"
DEFAULT_DB_USER="jaeronautics"
DEFAULT_LANGUAGES="en"
DEFAULT_RATELIMIT_STORAGE_URI="redis://127.0.0.1:6379/0"
STATE_DIR="/etc/jaeronautics"
STATE_FILE="${STATE_DIR}/install.conf"
BACKUP_DIR="/var/backups/${APP_NAME}"
BOOTSTRAP_DIR="/tmp/${APP_NAME}-installer-bootstrap"
RUNTIME_BACKUP_KEEP="${RUNTIME_BACKUP_KEEP:-3}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SCRIPT_PATH="${SCRIPT_DIR}/$(basename "${BASH_SOURCE[0]}")"
ORIGINAL_ARGS=("$@")

MODE="${BOOTSTRAP_MODE:-}"
REPO_URL="${BOOTSTRAP_REPO_URL:-}"
BRANCH="${BOOTSTRAP_BRANCH:-$DEFAULT_BRANCH}"
INSTALL_DIR="${BOOTSTRAP_INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
SERVICE_NAME="${BOOTSTRAP_SERVICE_NAME:-$DEFAULT_SERVICE_NAME}"
APP_USER="${BOOTSTRAP_APP_USER:-$DEFAULT_APP_USER}"
APP_GROUP="${BOOTSTRAP_APP_GROUP:-$DEFAULT_APP_GROUP}"
APP_PORT="${BOOTSTRAP_APP_PORT:-$DEFAULT_APP_PORT}"
DOMAIN="${BOOTSTRAP_DOMAIN:-}"
ENABLE_SSL="${BOOTSTRAP_ENABLE_SSL:-}"
SSL_EMAIL="${BOOTSTRAP_SSL_EMAIL:-}"
USE_CLOUDFLARE_TUNNEL="${BOOTSTRAP_USE_CLOUDFLARE_TUNNEL:-0}"
CLOUDFLARE_ORIGIN_HOST="${BOOTSTRAP_CLOUDFLARE_ORIGIN_HOST:-}"
ADMIN_EMAIL="${BOOTSTRAP_ADMIN_EMAIL:-}"
ADMIN_PASSWORD="${BOOTSTRAP_ADMIN_PASSWORD:-}"
# The revision that was running before the update. Carried across the
# re-exec: installers before 2026-10 moved the checkout in the bootstrap, so by
# then HEAD was already the new revision.
PRE_UPDATE_REVISION="${BOOTSTRAP_PRE_UPDATE_REVISION:-}"
# The revision the bootstrap fetched -- the one CI is asked about, and the one
# the live checkout is moved to, once the background jobs are paused.
TARGET_REVISION="${BOOTSTRAP_TARGET_REVISION:-}"
NONINTERACTIVE="${BOOTSTRAP_NONINTERACTIVE:-0}"
# --restore FILE: a backup made on the Backup & Restore page, and optionally a
# file holding its passphrase (for a run without a terminal).
RESTORE_FILE=""
RESTORE_PASSPHRASE_FILE=""
# Set while restoring onto a new server: the backup brings its own accounts,
# so asking for a first admin that the restore then deletes would be a trap.
SKIP_ADMIN_ACCOUNT="0"

PACKAGE_MANAGER=""
DB_SERVICE_NAME=""
REDIS_SERVICE_NAME=""
NGINX_CONF_PATH=""
NGINX_ENABLED_PATH=""
ENV_FILE=""
SERVICE_FILE=""
BILLING_RECONCILE_SERVICE_FILE=""
BILLING_RECONCILE_TIMER_FILE=""
NOTIFICATIONS_SERVICE_FILE=""
NOTIFICATIONS_TIMER_FILE=""
CLEANUP_LOGS_SERVICE_FILE=""
CLEANUP_LOGS_TIMER_FILE=""
FORUM_DRIFT_SERVICE_FILE=""
FORUM_DRIFT_TIMER_FILE=""
EXTERNAL_WORK_SERVICE_FILE=""
EXTERNAL_WORK_TIMER_FILE=""
AUTOMATIC_UPDATES_SERVICE_FILE=""
AUTOMATIC_UPDATES_TIMER_FILE=""
UNATTENDED_UPGRADES_FILE=""
RESTART_IF_NEEDED_SERVICE_FILE=""
RESTART_IF_NEEDED_TIMER_FILE=""
APT_UPGRADE_TIMER_DROPIN=""
APT_UPGRADE_SERVICE_DROPIN=""
UPDATE_RUNNER_SERVICE_FILE=""
UPDATE_RUNNER_TIMER_FILE=""
UPDATE_RUNNER_PATH_FILE=""
UPDATE_RUNNER_SCRIPT="/usr/local/lib/jaeronautics/update-runner.sh"
# Where the revision running before the last update is recorded, so there is
# something concrete to go back to.
ROLLBACK_FILE="${STATE_DIR}/rollback.conf"
SKIP_DB_BACKUP="${SKIP_DB_BACKUP:-0}"
LAST_DB_BACKUP_FILE=""
UPDATE_STATE_DIR="/var/lib/jaeronautics/updates"
# The update runner's own files (lock, status, log): root's, read by the app.
UPDATE_RUNNER_DIR="/var/lib/jaeronautics/update-runner"
# The night's work, one thing at a time (docs/automatic-updates-plan.md, "The
# night"): each nightly job, the update and Ubuntu's own updates hold this lock
# while they run, and one that finds it held waits. A lock of the kernel's, on
# an open file: it goes with the process however that ends -- finished,
# crashed, killed, the power gone --, so nothing is ever left locked. In the
# runner's folder (root's; the portal's group may read it), so root never opens
# a file the portal could have put there.
NIGHT_LOCK="${UPDATE_RUNNER_DIR}/night.lock"
# How long one waits for the one before it, at most; then it gives up for the night.
NIGHT_WAIT_SECONDS=3600
UPDATE_COMMAND_PATH="/usr/local/bin/update"
PACKAGE_CACHE_UPDATED=0
INSTALLATION_EXISTS=0
USE_LOCAL_DB="1"

# Set when the database is moving and the data is to follow it. The SOURCE_
# values are the configuration as it was before this run, captured before the
# prompts overwrite it, because by the time the new values are known the old
# ones are the only way back to the data.
MIGRATE_DB_DATA="0"
MIGRATION_DUMP_FILE=""
MIGRATION_SOURCE_COUNTS=""
MIGRATION_OVERWRITE_DEST="0"
MIGRATION_TEMP_FILES=()
DB_CONN_ARGS=()
SOURCE_DB_HOST=""
SOURCE_DB_PORT=""
SOURCE_DB_NAME=""
SOURCE_DB_USER=""
SOURCE_DB_PASSWORD=""

if [[ -t 1 ]]; then
    COLOR_RED=$'\033[0;31m'
    COLOR_GREEN=$'\033[0;32m'
    COLOR_YELLOW=$'\033[1;33m'
    COLOR_BLUE=$'\033[0;34m'
    COLOR_MAGENTA=$'\033[0;35m'
    COLOR_CYAN=$'\033[0;36m'
    COLOR_BOLD=$'\033[1m'
    COLOR_RESET=$'\033[0m'
else
    COLOR_RED=""
    COLOR_GREEN=""
    COLOR_YELLOW=""
    COLOR_BLUE=""
    COLOR_MAGENTA=""
    COLOR_CYAN=""
    COLOR_BOLD=""
    COLOR_RESET=""
fi

log() {
    local level="$1"
    local color="$2"
    shift 2
    printf '%b[%s]%b %s\n' "${color}" "${level}" "${COLOR_RESET}" "$*"
}

step() { log "STEP" "${COLOR_BLUE}${COLOR_BOLD}" "$*"; }
info() { log "INFO" "${COLOR_CYAN}" "$*"; }
success() { log "OK" "${COLOR_GREEN}" "$*"; }
warn() { log "WARN" "${COLOR_YELLOW}" "$*"; }
error() { log "ERR" "${COLOR_RED}" "$*"; }

die() {
    error "$*"
    exit 1
}

require_safe_sql_identifier() {
    # DB_NAME and DB_USER are interpolated straight into CREATE DATABASE, CREATE
    # USER and GRANT. Quoting them correctly for MySQL is fiddly and easy to get
    # subtly wrong, and a database called anything but a plain identifier is a
    # poor idea regardless -- so reject those names up front instead. MySQL
    # limits a user name to 32 characters and a database name to 64.
    local label="$1" value="$2" max_length="$3"
    if [[ ! "${value}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
        die "${label} must contain only letters, digits and underscores and start with a letter or underscore. Got: ${value}"
    fi
    if (( ${#value} > max_length )); then
        die "${label} must be at most ${max_length} characters. Got ${#value}."
    fi
}

diag_section() {
    printf '\n----- %s -----\n' "$*"
}

collect_diagnostics() {
    # Gather what someone would look up by hand after a failed install. This is
    # printed into the installer's own output, so it reaches the admin page's
    # update log too -- which is the whole point: an administrator without shell
    # access should be able to see *why* it failed, not just that it did.
    command -v systemctl >/dev/null 2>&1 || return 0

    local units=()
    [[ -n "${SERVICE_NAME:-}" ]] && units+=("${SERVICE_NAME}")
    if [[ -n "${SERVICE_NAME:-}" ]]; then
        units+=(
            "${SERVICE_NAME}-external-work.timer"
            "${SERVICE_NAME}-update-runner.path"
            "${SERVICE_NAME}-notifications.timer"
            "${SERVICE_NAME}-billing-reconcile.timer"
            "${SERVICE_NAME}-forum-drift.timer"
            "${SERVICE_NAME}-automatic-updates.timer"
        )
    fi
    [[ "${USE_LOCAL_DB:-0}" == "1" && -n "${DB_SERVICE_NAME:-}" ]] && units+=("${DB_SERVICE_NAME}")
    units+=("nginx")

    diag_section "Unit states"
    local unit
    for unit in "${units[@]}"; do
        printf '%-46s %s\n' "${unit}" "$(systemctl is-active "${unit}" 2>&1 || true)"
    done

    diag_section "Units in a failed state"
    systemctl --failed --no-pager --no-legend 2>/dev/null || true

    if [[ -n "${SERVICE_NAME:-}" ]]; then
        diag_section "Last 60 log lines for ${SERVICE_NAME}"
        journalctl -u "${SERVICE_NAME}" -n 60 --no-pager 2>/dev/null || true
    fi

    if [[ "${USE_LOCAL_DB:-0}" == "1" && -n "${DB_SERVICE_NAME:-}" ]]; then
        diag_section "Last 20 log lines for ${DB_SERVICE_NAME}"
        journalctl -u "${DB_SERVICE_NAME}" -n 20 --no-pager 2>/dev/null || true
    fi

    diag_section "nginx configuration test"
    nginx -t 2>&1 || true
    if [[ -r /var/log/nginx/error.log ]]; then
        diag_section "Last 20 lines of the nginx error log"
        tail -n 20 /var/log/nginx/error.log 2>/dev/null || true
    fi

    if [[ -n "${APP_PORT:-}" ]]; then
        diag_section "Health endpoint, direct to the application"
        curl -sS -m 10 -o - -w '\nHTTP %{http_code}\n' "http://127.0.0.1:${APP_PORT}/__health" 2>&1 || true
    fi
    if [[ -n "${DOMAIN:-}" ]]; then
        diag_section "Health endpoint, through nginx"
        curl -sS -m 10 -k -o - -w '\nHTTP %{http_code}\n' -H "Host: ${DOMAIN}" \
            "http://127.0.0.1/__health" 2>&1 || true
    fi

    # Out of disk is a common and easily missed cause of an install failing
    # halfway through.
    diag_section "Disk space"
    df -h "${INSTALL_DIR:-/}" / 2>/dev/null || true

    printf '\n'
}

on_error() {
    # Capture the failing status before anything else runs and overwrites it.
    local failed_status=$?
    local line="$1"
    local command="$2"
    error "Installer failed at line ${line}: ${command}"
    if [[ "${UNDO_STATE:-0}" == "2" ]]; then
        # Putting the previous version back failed: the portal stays stopped,
        # behind the maintenance page, rather than run half updated.
        error "This happened while undoing an update whose database migration had failed."
        error "The portal is stopped and visitors see the maintenance page. The backup taken"
        error "before the migration is ${UNDO_DUMP_FILE:-missing}; see docs/update-safety.md."
    elif [[ "${UNDO_STATE:-0}" == "1" ]]; then
        # Stopped for the migration, which went through; a later step failed.
        # The new code fits the migrated database, so it is started again.
        warn "Starting the portal again: its database migration had gone through."
        systemctl start "${SERVICE_NAME}" 2>/dev/null || true
    fi
    # Stopped between the new code arriving and the database step: the
    # previous version back, then its jobs.
    undo_code_after_early_failure
    # Never leave billing, notifications and forum syncs switched off because
    # an update stopped halfway.
    resume_background_jobs_after_failure || true
    warn "Collecting diagnostics for the failure above."
    collect_diagnostics
    error "Installer failed. The diagnostics above show the state at the time of failure."
    # Exit non-zero explicitly. Without this the handler's own last command --
    # which succeeds -- became the script's status, so a failed install exited 0.
    # The update runner reads that status, so the admin page showed a green
    # "Completed" for a deploy that had just printed "Installer failed".
    exit "$(( failed_status == 0 ? 1 : failed_status ))"
}
trap 'on_error "${LINENO}" "${BASH_COMMAND}"' ERR
# However the run ends: the database credential files and the build's
# temporary swap go, and background jobs paused for an update that stopped
# (die() exits without passing on_error) start again.
on_exit() {
    local status=$?
    cleanup_migration_temp_files
    remove_build_swap || true
    if (( status != 0 )); then
        # die() comes here without on_error. Not "|| true": that would keep
        # the undo from stopping at its first failure.
        undo_code_after_early_failure
        resume_background_jobs_after_failure || true
    fi
}
trap 'on_exit' EXIT

# The jobs the timers run, each a oneshot service of its own.
BACKGROUND_JOBS=(billing-reconcile notifications cleanup-logs forum-drift external-work automatic-updates)
BACKGROUND_JOBS_PAUSED=0
# How long an update waits for a job that is already running to finish.
BACKGROUND_JOB_WAIT_SECONDS=180

background_job_running() {
    # A oneshot service is "activating" while it runs.
    local state
    state="$(systemctl show -p ActiveState --value "$1" 2>/dev/null || true)"
    [[ "${state}" == "activating" || "${state}" == "active" || "${state}" == "deactivating" ]]
}

# The jobs that queue behind the night lock.
NIGHT_JOBS=(billing-reconcile forum-drift cleanup-logs automatic-updates)

night_job() {
    local job
    for job in "${NIGHT_JOBS[@]}"; do
        [[ "${job}" == "$1" ]] && return 0
    done
    return 1
}

# There before the night's jobs first run: they open it to read only (the
# portal's services see the file system read-only), so cannot create it.
# An existing one is kept, never replaced: a lock belongs to the file, and
# whoever holds or waits for it -- this very update, run by the update
# runner -- would hold the old one while the next job locked a new one.
ensure_night_lock() {
    if [[ -L "${NIGHT_LOCK}" || ( -e "${NIGHT_LOCK}" && ! -f "${NIGHT_LOCK}" ) ]]; then
        rm -rf -- "${NIGHT_LOCK}"
    fi
    if [[ ! -f "${NIGHT_LOCK}" ]]; then
        install -m 0640 -o root -g "${APP_GROUP}" /dev/null "${NIGHT_LOCK}"
    else
        chown root:"${APP_GROUP}" "${NIGHT_LOCK}"
        chmod 0640 "${NIGHT_LOCK}"
    fi
}

# An update started here, in a shell, is a step of the night as much as one
# the update runner starts: the 04:00 restart must not cut it in half, nor
# Ubuntu's updates run beside it. So it takes the night lock too -- waiting,
# like the runner, for whatever holds it -- unless the runner holds it for it
# already. The lock goes with this process however it ends.
hold_night_lock() {
    [[ "${NIGHT_LOCK_HELD:-0}" == "1" ]] && return 0
    [[ -f "${NIGHT_LOCK}" && ! -L "${NIGHT_LOCK}" ]] || return 0
    exec 8<"${NIGHT_LOCK}"
    if ! flock -n 8; then
        info "Waiting for the night's other work to finish first (up to $(( NIGHT_WAIT_SECONDS / 60 )) minutes)"
        flock -w "${NIGHT_WAIT_SECONDS}" 8 \
            || die "Still waiting after ${NIGHT_WAIT_SECONDS} seconds for the night's other work; nothing was changed. Try again later."
    fi
    export NIGHT_LOCK_HELD=1
}

pause_background_jobs() {
    # Between the new code arriving on disk and the restart, a job started by
    # its timer would run that code against packages and a database not yet
    # updated -- and these jobs talk to Discourse, send emails and change
    # memberships. So the timers stop for the update, a job already running is
    # let finish, and reload_services starts them all again at the end.
    step "Pausing background jobs for the update"
    local job unit waited
    for job in "${BACKGROUND_JOBS[@]}"; do
        systemctl stop "${SERVICE_NAME}-${job}.timer" 2>/dev/null || true
    done
    BACKGROUND_JOBS_PAUSED=1
    for job in "${BACKGROUND_JOBS[@]}"; do
        unit="${SERVICE_NAME}-${job}.service"
        if [[ "${NIGHT_LOCK_HELD:-0}" == "1" ]] && night_job "${job}"; then
            # Queued behind this update, not at work: it runs after it, on the new code.
            continue
        fi
        waited=0
        while background_job_running "${unit}"; do
            if (( waited >= BACKGROUND_JOB_WAIT_SECONDS )); then
                warn "${unit} is still running after ${BACKGROUND_JOB_WAIT_SECONDS}s; updating anyway."
                break
            fi
            if (( waited == 0 )); then
                info "Waiting for ${unit} to finish"
            fi
            sleep 2
            waited=$(( waited + 2 ))
        done
    done
}

resume_background_jobs_after_failure() {
    [[ "${BACKGROUND_JOBS_PAUSED:-0}" == "1" ]] || return 0
    if [[ "${KEEP_JOBS_PAUSED:-0}" == "1" ]]; then
        warn "The background jobs stay paused: the code on disk is half updated (above)."
        return 0
    fi
    warn "Starting the background jobs again after the failed update."
    local job
    for job in "${BACKGROUND_JOBS[@]}"; do
        systemctl start "${SERVICE_NAME}-${job}.timer" 2>/dev/null || true
    done
    BACKGROUND_JOBS_PAUSED=0
}

# Set by record_rollback_point, cleared at the database step: in between, the
# new code may be on disk without its packages or front end, and an update
# that stops there puts the previous version back rather than leaving the
# jobs -- and the portal at its next restart -- to run on it.
ROLLBACK_POINT_REVISION=""
KEEP_JOBS_PAUSED=0

undo_code_after_early_failure() {
    [[ -n "${ROLLBACK_POINT_REVISION}" && "${UNDO_STATE:-0}" == "0" ]] || return 0
    local revision="${ROLLBACK_POINT_REVISION}" head status
    ROLLBACK_POINT_REVISION=""
    head="$(git_in_dir "${INSTALL_DIR}" rev-parse HEAD 2>/dev/null || true)"
    [[ -n "${head}" && "${head}" != "${revision}" ]] || return 0
    warn "The update stopped before its database step, with the new code already on disk."
    warn "Putting the previous version (${revision:0:8}) back."
    # In a subshell of its own, stopping at its first failure, and without
    # this handler's trap: whatever happens there must not end this one.
    set +e
    ( trap - ERR; set -e; undo_update_code )
    status=$?
    set -e
    if (( status == 0 )); then
        # It started the portal and the timers again on the previous code.
        BACKGROUND_JOBS_PAUSED=0
        success "The previous version runs again."
    else
        KEEP_JOBS_PAUSED=1
        error "The previous version could not be put back. The portal keeps running the code it"
        error "started with until it restarts; update again, or roll back from the Updates page."
    fi
}

usage() {
    cat <<'EOF'
Usage: install.sh [options]

Options:
  --mode install|update|repair|uninstall
  --repo-url URL
  --branch BRANCH
  --install-dir PATH
  --domain DOMAIN
  --origin-host HOST
  --enable-ssl
  --disable-ssl
  --ssl-email EMAIL
  --admin-email EMAIL
  --admin-password PASSWORD
  --restore FILE            Restore a backup (from the Backup & Restore page).
                            Installs first if this server has no installation.
  --passphrase-file FILE    Read the backup's passphrase from FILE instead of asking.
  --build-locally           Build the front end on this server instead of
                            using CI's build (needs about 2 GB of RAM and swap;
                            temporary swap is added if there is less).
  --yes, --non-interactive
  -h, --help
EOF
}

has_tty() {
    # Opened, not only checked: /dev/tty is readable and writable by its
    # permissions everywhere, but without a controlling terminal -- the admin
    # page's update runner, a systemd unit -- opening it fails ("No such device
    # or address"), and a prompt written there ended the whole update.
    ( : < /dev/tty > /dev/tty ) 2>/dev/null
}

tty_print() {
    if has_tty; then
        printf '%b' "$*" > /dev/tty
    else
        printf '%b' "$*" >&2
    fi
}

# Give up on a stalled mirror rather than hanging forever; apt has no default
# timeout at all.
APT_NETWORK_OPTS=(
    -o Acquire::http::Timeout=30
    -o Acquire::https::Timeout=30
    -o Acquire::Retries=2
    -o DPkg::Lock::Timeout=120
    # One request per connection. Pipelined requests through a flaky mirror or
    # proxy are what turned downloads into "Ign:" lines and then a retry that
    # never returned (seen on jaero-test, 2026-09-27).
    -o Acquire::http::Pipeline-Depth=0
)

retry() {
    local attempts="$1"
    shift
    local try=1
    until "$@"; do
        if (( try >= attempts )); then
            return 1
        fi
        warn "Command failed. Retrying (${try}/${attempts})..."
        sleep $(( try * 2 ))
        try=$(( try + 1 ))
    done
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

detect_reachable_ipv4() {
    local detected=""

    if command_exists ip; then
        detected="$(ip route get 1.1.1.1 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i=="src") {print $(i+1); exit}}')"
    fi

    if [[ -z "${detected}" ]] && command_exists hostname; then
        detected="$(hostname -I 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i !~ /^127\./ && $i ~ /^([0-9]{1,3}\.){3}[0-9]{1,3}$/) {print $i; exit}}')"
    fi

    if [[ -z "${detected}" ]] && command_exists ip; then
        detected="$(ip -4 addr show scope global up 2>/dev/null | awk '/inet / {sub(/\/.*/, "", $2); if ($2 !~ /^127\./) {print $2; exit}}')"
    fi

    printf '%s' "${detected}"
}

require_root() {
    if [[ "${EUID}" -eq 0 ]]; then
        return
    fi

    if command_exists sudo && [[ -f "${SCRIPT_PATH}" ]]; then
        exec sudo -E bash "${SCRIPT_PATH}" "${ORIGINAL_ARGS[@]}"
    fi

    die "Please run this installer as root or via sudo."
}

detect_repo_url() {
    if [[ -n "${REPO_URL}" ]]; then
        printf '%s\n' "${REPO_URL}"
        return
    fi

    if command_exists git; then
        local current="${SCRIPT_DIR}"
        while [[ "${current}" != "/" ]]; do
            if [[ -d "${current}/.git" ]]; then
                local remote
                remote="$(git_in_dir "${current}" config --get remote.origin.url || true)"
                if [[ -n "${remote}" ]]; then
                    printf '%s\n' "${remote}"
                    return
                fi
            fi
            current="$(dirname "${current}")"
        done
    fi

    printf '%s\n' "${DEFAULT_REPO_URL}"
}

normalize_repo_url() {
    local url="$1"
    url="${url%.git}"
    printf '%s\n' "${url}"
}

git_in_dir() {
    local target_dir="$1"
    shift
    git config --global --add safe.directory "${target_dir}" >/dev/null 2>&1 || true
    git -c safe.directory="${target_dir}" -C "${target_dir}" "$@"
}

load_state() {
    if [[ -f "${STATE_FILE}" ]]; then
        # shellcheck disable=SC1090
        source "${STATE_FILE}"
    fi
}

parse_args() {
    while (($#)); do
        case "$1" in
            --rollback)
                MODE="rollback"
                shift
                ;;
            --skip-db-backup)
                SKIP_DB_BACKUP=1
                shift
                ;;
            --mode)
                MODE="${2:-}"
                shift 2
                ;;
            --repo-url)
                REPO_URL="${2:-}"
                shift 2
                ;;
            --branch)
                BRANCH="${2:-}"
                shift 2
                ;;
            --install-dir)
                INSTALL_DIR="${2:-}"
                shift 2
                ;;
            --domain)
                DOMAIN="${2:-}"
                shift 2
                ;;
            --origin-host)
                CLOUDFLARE_ORIGIN_HOST="${2:-}"
                shift 2
                ;;
            --enable-ssl)
                ENABLE_SSL="1"
                shift
                ;;
            --disable-ssl)
                ENABLE_SSL="0"
                shift
                ;;
            --ssl-email)
                SSL_EMAIL="${2:-}"
                shift 2
                ;;
            --admin-email)
                ADMIN_EMAIL="${2:-}"
                shift 2
                ;;
            --admin-password)
                ADMIN_PASSWORD="${2:-}"
                shift 2
                ;;
            --restore)
                MODE="restore"
                [[ -n "${2:-}" ]] || { error "--restore needs the backup file"; exit 2; }
                RESTORE_FILE="$(realpath -m -- "$2")"
                shift 2
                ;;
            --passphrase-file)
                [[ -n "${2:-}" ]] || { error "--passphrase-file needs a file"; exit 2; }
                RESTORE_PASSPHRASE_FILE="$(realpath -m -- "$2")"
                shift 2
                ;;
            --yes|--non-interactive)
                NONINTERACTIVE="1"
                shift
                ;;
            --build-locally)
                BUILD_LOCALLY="1"
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                # A mistyped argument is a usage error, not a failed install:
                # going through die() here tripped the ERR trap and buried the
                # one line that matters under a page of service diagnostics.
                error "Unknown argument: $1"
                usage >&2
                exit 2
                ;;
        esac
    done
}

resolve_paths() {
    ENV_FILE="${INSTALL_DIR}/.env"
    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
    BILLING_RECONCILE_SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}-billing-reconcile.service"
    BILLING_RECONCILE_TIMER_FILE="/etc/systemd/system/${SERVICE_NAME}-billing-reconcile.timer"
    NOTIFICATIONS_SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}-notifications.service"
    NOTIFICATIONS_TIMER_FILE="/etc/systemd/system/${SERVICE_NAME}-notifications.timer"
    CLEANUP_LOGS_SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}-cleanup-logs.service"
    CLEANUP_LOGS_TIMER_FILE="/etc/systemd/system/${SERVICE_NAME}-cleanup-logs.timer"
    FORUM_DRIFT_SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}-forum-drift.service"
    FORUM_DRIFT_TIMER_FILE="/etc/systemd/system/${SERVICE_NAME}-forum-drift.timer"
    EXTERNAL_WORK_SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}-external-work.service"
    EXTERNAL_WORK_TIMER_FILE="/etc/systemd/system/${SERVICE_NAME}-external-work.timer"
    AUTOMATIC_UPDATES_SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}-automatic-updates.service"
    AUTOMATIC_UPDATES_TIMER_FILE="/etc/systemd/system/${SERVICE_NAME}-automatic-updates.timer"
    UNATTENDED_UPGRADES_FILE="/etc/apt/apt.conf.d/52${SERVICE_NAME}-unattended-upgrades"
    RESTART_IF_NEEDED_SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}-restart-if-needed.service"
    RESTART_IF_NEEDED_TIMER_FILE="/etc/systemd/system/${SERVICE_NAME}-restart-if-needed.timer"
    APT_UPGRADE_TIMER_DROPIN="/etc/systemd/system/apt-daily-upgrade.timer.d/50-${SERVICE_NAME}.conf"
    APT_UPGRADE_SERVICE_DROPIN="/etc/systemd/system/apt-daily-upgrade.service.d/50-${SERVICE_NAME}.conf"
    UPDATE_RUNNER_SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}-update-runner.service"
    UPDATE_RUNNER_TIMER_FILE="/etc/systemd/system/${SERVICE_NAME}-update-runner.timer"
    UPDATE_RUNNER_PATH_FILE="/etc/systemd/system/${SERVICE_NAME}-update-runner.path"

    if [[ -d /etc/nginx/sites-available && -d /etc/nginx/sites-enabled ]]; then
        NGINX_CONF_PATH="/etc/nginx/sites-available/${SERVICE_NAME}.conf"
        NGINX_ENABLED_PATH="/etc/nginx/sites-enabled/${SERVICE_NAME}.conf"
    else
        NGINX_CONF_PATH="/etc/nginx/conf.d/${SERVICE_NAME}.conf"
        NGINX_ENABLED_PATH=""
    fi
}

detect_package_manager() {
    if command_exists apt-get; then
        PACKAGE_MANAGER="apt"
    elif command_exists dnf; then
        PACKAGE_MANAGER="dnf"
    elif command_exists yum; then
        PACKAGE_MANAGER="yum"
    else
        die "Unsupported Linux distribution. Expected apt, dnf, or yum."
    fi
}

update_package_index_once() {
    if [[ "${PACKAGE_CACHE_UPDATED}" == "1" ]]; then
        return
    fi

    case "${PACKAGE_MANAGER}" in
        apt)
            export DEBIAN_FRONTEND=noninteractive
            # Bound the network waits. Without these apt will sit on an
            # unresponsive mirror indefinitely, and because the update runs
            # unattended behind the admin button there is nobody to notice --
            # the update simply never finishes and the button stays disabled.
            retry 3 apt-get "${APT_NETWORK_OPTS[@]}" update
            ;;
        dnf)
            retry 3 dnf makecache
            ;;
        yum)
            retry 3 yum makecache
            ;;
    esac

    PACKAGE_CACHE_UPDATED=1
}

packages_missing() {
    # The ones not installed yet, one per line.
    local pkg
    for pkg in "$@"; do
        case "${PACKAGE_MANAGER}" in
            apt)
                dpkg-query -W -f='${Status}' "${pkg}" 2>/dev/null | grep -q 'install ok installed' \
                    || printf '%s\n' "${pkg}"
                ;;
            dnf|yum)
                rpm -q "${pkg}" >/dev/null 2>&1 || printf '%s\n' "${pkg}"
                ;;
            *)
                printf '%s\n' "${pkg}"
                ;;
        esac
    done
}

install_packages() {
    # An update of the portal needs nothing from the distribution's mirrors
    # when every package is already there. Asking anyway re-downloaded the whole
    # package index (about 35 MB -- it is deleted after every run) on every
    # update, so a portal update failed whenever the Ubuntu mirror had a bad
    # moment. System updates are unattended-upgrades' job; "--mode repair"
    # still refreshes these packages on purpose.
    if [[ "${MODE:-}" == "update" ]]; then
        local missing=()
        mapfile -t missing < <(packages_missing "$@")
        if (( ${#missing[@]} == 0 )); then
            info "System packages already installed: $*"
            return
        fi
        set -- "${missing[@]}"
    fi

    update_package_index_once

    case "${PACKAGE_MANAGER}" in
        apt)
            retry 3 apt-get "${APT_NETWORK_OPTS[@]}" install -y "$@"
            ;;
        dnf)
            retry 3 dnf install -y "$@"
            ;;
        yum)
            retry 3 yum install -y "$@"
            ;;
    esac
}

cleanup_package_caches() {
    local app_home=""

    case "${PACKAGE_MANAGER:-}" in
        apt)
            # The downloaded packages only. The package lists stay: without
            # them Ubuntu's security updates find nothing to install until
            # its next refresh.
            apt-get clean || true
            ;;
        dnf)
            dnf clean all || true
            ;;
        yum)
            yum clean all || true
            ;;
    esac

    rm -rf /root/.cache/pip 2>/dev/null || true
    if [[ -n "${APP_USER:-}" ]] && id -u "${APP_USER}" >/dev/null 2>&1; then
        app_home="$(getent passwd "${APP_USER}" | cut -d: -f6)"
        if [[ -n "${app_home}" ]]; then
            rm -rf "${app_home}/.cache/pip" 2>/dev/null || true
        fi
    fi
}

bootstrap_packages() {
    case "${PACKAGE_MANAGER}" in
        apt|dnf|yum)
            printf '%s\n' ca-certificates git
            ;;
    esac
}

base_packages() {
    case "${PACKAGE_MANAGER}" in
        apt)
            # mariadb-client always: the data has to be readable whether the
            # database is on this box or elsewhere. mariadb-server only once
            # the local/external answer is known, from install_or_update.
            # Pango (libpango, libpangoft2): WeasyPrint lays out the legal texts' PDFs with it;
            # HarfBuzz-Subset (4.1 or later, Ubuntu 24.04 has 8.3) trims the fonts it embeds.
            printf '%s\n' ca-certificates curl git nginx mariadb-client openssl python3 python3-pip python3-venv redis-server \
                libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0
            ;;
        dnf|yum)
            printf '%s\n' ca-certificates curl git nginx mariadb openssl python3 python3-pip redis pango
            ;;
    esac
}

certbot_packages() {
    printf '%s\n' certbot python3-certbot-nginx
}

sql_escape() {
    printf '%s' "${1//\'/\'\'}"
}

dotenv_quote() {
    local value="${1//\'/\'\"\'\"\'}"
    printf "'%s'" "${value}"
}

generate_secret() {
    openssl rand -hex 32
}

is_placeholder() {
    case "$1" in
        ""|change-me|sk_test_change_me|pk_test_change_me|price_change_me|whsec_change_me|*change_me*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

prompt_value() {
    local __var_name="$1"
    local prompt_text="$2"
    local default_value="${3:-}"
    local is_secret="${4:-0}"
    local required="${5:-0}"
    local answer=""

    if [[ "${NONINTERACTIVE}" == "1" && -n "${default_value}" ]]; then
        printf -v "${__var_name}" '%s' "${default_value}"
        return
    fi

    if [[ "${NONINTERACTIVE}" == "1" && "${required}" == "1" && -z "${default_value}" ]]; then
        die "Missing required value for: ${prompt_text}"
    fi

    while true; do
        if has_tty; then
            if [[ -n "${default_value}" ]]; then
                tty_print "${COLOR_MAGENTA}?${COLOR_RESET} ${prompt_text} [${default_value}]: "
            else
                tty_print "${COLOR_MAGENTA}?${COLOR_RESET} ${prompt_text}: "
            fi

            if [[ "${is_secret}" == "1" ]]; then
                IFS= read -r -s answer < /dev/tty
                tty_print "\n"
            else
                IFS= read -r answer < /dev/tty
            fi
        else
            answer="${default_value}"
        fi

        if [[ -z "${answer}" ]]; then
            answer="${default_value}"
        fi

        if [[ "${required}" == "1" && -z "${answer}" ]]; then
            warn "A value is required."
            continue
        fi

        printf -v "${__var_name}" '%s' "${answer}"
        return
    done
}

prompt_yes_no() {
    local __var_name="$1"
    local prompt_text="$2"
    local default_value="${3:-1}"
    local default_hint="Y/n"
    local answer=""

    if [[ "${default_value}" == "0" ]]; then
        default_hint="y/N"
    fi

    if [[ "${NONINTERACTIVE}" == "1" ]]; then
        printf -v "${__var_name}" '%s' "${default_value}"
        return
    fi

    while true; do
        if has_tty; then
            tty_print "${COLOR_MAGENTA}?${COLOR_RESET} ${prompt_text} [${default_hint}]: "
            IFS= read -r answer < /dev/tty
        else
            answer=""
        fi

        if [[ -z "${answer}" ]]; then
            printf -v "${__var_name}" '%s' "${default_value}"
            return
        fi

        case "${answer}" in
            y|Y|yes|YES)
                printf -v "${__var_name}" '%s' "1"
                return
                ;;
            n|N|no|NO)
                printf -v "${__var_name}" '%s' "0"
                return
                ;;
            *)
                warn "Please answer yes or no."
                ;;
        esac
    done
}

validate_json_object() {
    local json_input="${1:-{}}"
    JSON_INPUT="${json_input}" python3 - <<'PY'
import ast
import json
import os
import sys

raw = os.environ.get("JSON_INPUT", "{}")


def parse_object(value):
    text = (value or "{}").strip()
    candidates = [text]
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        candidates.append(text[1:-1])

    for candidate in candidates:
        try:
            data = json.loads(candidate or "{}")
            if isinstance(data, str):
                data = json.loads(data)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    try:
        data = ast.literal_eval(text)
        if isinstance(data, str):
            data = json.loads(data)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    return None


if parse_object(raw) is None:
    sys.exit(1)
PY
}

normalize_json_object() {
    local json_input="${1:-{}}"
    JSON_INPUT="${json_input}" python3 - <<'PY'
import ast
import json
import os
import sys

raw = os.environ.get("JSON_INPUT", "{}")


def parse_object(value):
    text = (value or "{}").strip()
    candidates = [text]
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        candidates.append(text[1:-1])

    for candidate in candidates:
        try:
            data = json.loads(candidate or "{}")
            if isinstance(data, str):
                data = json.loads(data)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    try:
        data = ast.literal_eval(text)
        if isinstance(data, str):
            data = json.loads(data)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    return {}


print(json.dumps(parse_object(raw), separators=(",", ":")))
PY
}

mail_accounts_count() {
    local json_input="${1:-{}}"
    JSON_INPUT="${json_input}" python3 - <<'PY'
import ast
import json
import os
import sys

raw = os.environ.get("JSON_INPUT", "{}")


def parse_object(value):
    text = (value or "{}").strip()
    candidates = [text]
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        candidates.append(text[1:-1])

    for candidate in candidates:
        try:
            data = json.loads(candidate or "{}")
            if isinstance(data, str):
                data = json.loads(data)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    try:
        data = ast.literal_eval(text)
        if isinstance(data, str):
            data = json.loads(data)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    return {}


data = parse_object(raw)

print(len(data))
PY
}

collect_mail_accounts() {
    local tmp_file
    local add_more="1"
    local account_name=""
    local account_host=""
    local account_port=""
    local account_user=""
    local account_password=""
    local account_starttls=""
    local added_accounts="0"

    tmp_file="$(mktemp)"

    tty_print "\n${COLOR_BOLD}SMTP account setup${COLOR_RESET}\n"
    tty_print "Add one or more sender accounts. Common names are 'office', 'it', or 'noreply'.\n\n"

    while [[ "${add_more}" == "1" ]]; do
        prompt_value account_name "Mail account key" "" 0 1
        prompt_value account_host "SMTP host for ${account_name}" "" 0 1
        prompt_value account_port "SMTP port for ${account_name}" "587" 0 1
        prompt_value account_user "SMTP username/email for ${account_name}" "" 0 1
        prompt_value account_password "SMTP password for ${account_name}" "" 1 1

        if [[ "${account_port}" == "587" ]]; then
            account_starttls="1"
            prompt_yes_no account_starttls "Use STARTTLS for ${account_name}?" "1"
        else
            prompt_yes_no account_starttls "Use STARTTLS for ${account_name}?" "0"
        fi

        printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$(printf '%s' "${account_name}" | base64 | tr -d '\n')" \
            "$(printf '%s' "${account_host}" | base64 | tr -d '\n')" \
            "$(printf '%s' "${account_port}" | base64 | tr -d '\n')" \
            "$(printf '%s' "${account_user}" | base64 | tr -d '\n')" \
            "$(printf '%s' "${account_password}" | base64 | tr -d '\n')" \
            "$(printf '%s' "${account_starttls}" | base64 | tr -d '\n')" >> "${tmp_file}"

        added_accounts=$((added_accounts + 1))
        prompt_yes_no add_more "Add another SMTP account?" "0"
    done

    MAIL_ACCOUNTS_JSON="$(python3 - "${tmp_file}" <<'PY'
import base64
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
accounts = {}
for line in path.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue

    encoded = line.split("	")
    if len(encoded) != 6:
        raise ValueError(f"Unexpected SMTP account line format: {line!r}")

    name, host, port, user, password, starttls = [
        base64.b64decode(value.encode("ascii")).decode("utf-8") for value in encoded
    ]

    accounts[name] = {
        "host": host,
        "port": int(port),
        "user": user,
        "pass": password,
    }
    if starttls == "1":
        accounts[name]["starttls"] = True

print(json.dumps(accounts, separators=(",", ":")))
PY
)"

    rm -f "${tmp_file}"
    MAIL_ACCOUNTS_JSON="$(normalize_json_object "${MAIL_ACCOUNTS_JSON}")"

    if [[ "${added_accounts}" -gt 0 ]] && [[ "$(mail_accounts_count "${MAIL_ACCOUNTS_JSON}")" == "0" ]]; then
        die "Failed to save SMTP sender accounts. MAIL_ACCOUNTS_JSON stayed empty after collection."
    fi

    info "Saved $(mail_accounts_count "${MAIL_ACCOUNTS_JSON}") SMTP sender account(s)."
}

choose_existing_install_action() {
    local choice=""
    tty_print "\n${COLOR_BOLD}Existing installation detected.${COLOR_RESET}\n"
    tty_print "1) Update existing installation\n"
    tty_print "2) Repair or reconfigure existing installation\n"
    tty_print "3) Uninstall everything created by this installer\n"
    tty_print "4) Cancel\n"

    while true; do
        if [[ "${NONINTERACTIVE}" == "1" ]]; then
            MODE="update"
            return
        fi

        tty_print "${COLOR_MAGENTA}?${COLOR_RESET} Choose an option [1-4]: "
        IFS= read -r choice < /dev/tty
        case "${choice}" in
            1) MODE="update"; return ;;
            2) MODE="repair"; return ;;
            3) MODE="uninstall"; return ;;
            # Not a failure: going through die() here tripped the ERR trap, so
            # choosing "cancel" printed "Installer failed", a stack line and a
            # page of diagnostics for what was a deliberate answer.
            4) info "Cancelled."; exit 0 ;;
            *) warn "Please choose 1, 2, 3, or 4." ;;
        esac
    done
}

prune_old_runtime_backups() {
    local backup_file

    if [[ ! -d "${BACKUP_DIR}" ]]; then
        return
    fi

    while IFS= read -r backup_file; do
        rm -f "${backup_file}" 2>/dev/null || true
    done < <(find "${BACKUP_DIR}" -maxdepth 1 -type f -name "${APP_NAME}-*" -printf '%T@ %p\n' 2>/dev/null | sort -nr | awk -v keep="${RUNTIME_BACKUP_KEEP:-3}" 'NR > keep { sub(/^[^ ]+ /, ""); print }')
}

cleanup_legacy_repo_backups() {
    local target_dir="$1"

    if [[ ! -d "${target_dir}" ]]; then
        return
    fi

    find "${target_dir}" -maxdepth 1 -type f \( -name '.installer-local-changes-*' -o -name '.installer-untracked-files-*' \) -delete 2>/dev/null || true
}

db_dump_client() {
    if command_exists mariadb-dump; then
        printf '%s\n' mariadb-dump
    elif command_exists mysqldump; then
        printf '%s\n' mysqldump
    else
        return 1
    fi
}

backup_runtime_state() {
    local backup_stamp
    local dump_client
    local dump_file

    mkdir -p "${BACKUP_DIR}"
    chmod 700 "${BACKUP_DIR}" 2>/dev/null || true
    prune_old_runtime_backups
    cleanup_legacy_repo_backups "${INSTALL_DIR}"

    backup_stamp="$(date +%Y%m%d%H%M%S)"

    if [[ -f "${ENV_FILE}" ]]; then
        cp -a "${ENV_FILE}" "${BACKUP_DIR}/${APP_NAME}-env-${backup_stamp}.env"
        info "Backed up environment file to ${BACKUP_DIR}/${APP_NAME}-env-${backup_stamp}.env"
    fi

    if [[ -f "${STATE_FILE}" ]]; then
        cp -a "${STATE_FILE}" "${BACKUP_DIR}/${APP_NAME}-state-${backup_stamp}.conf"
        info "Backed up installer state to ${BACKUP_DIR}/${APP_NAME}-state-${backup_stamp}.conf"
    fi

    if [[ -f "${SERVICE_FILE}" ]]; then
        cp -a "${SERVICE_FILE}" "${BACKUP_DIR}/${APP_NAME}-service-${backup_stamp}.service"
    fi

    if [[ -f "${NGINX_CONF_PATH}" ]]; then
        cp -a "${NGINX_CONF_PATH}" "${BACKUP_DIR}/${APP_NAME}-nginx-${backup_stamp}.conf"
    fi

    # Whether the database is on this box or elsewhere. The guard here used to
    # require a local one, which meant the installations most in need of a
    # backup -- the ones whose database this script cannot snapshot along with
    # the filesystem -- were the ones silently running without it.
    if [[ -n "${DB_NAME:-}" ]]; then
        # This dump is the only way back from a migration, because a rollback
        # restores code and deliberately leaves the schema alone. Failing to take
        # it is therefore a reason to stop, not a warning to scroll past.
        if dump_client="$(db_dump_client 2>/dev/null)"; then
            dump_file="${BACKUP_DIR}/${APP_NAME}-db-${backup_stamp}.sql.gz"
            set_db_conn_args "${DB_HOST:-127.0.0.1}" "${DB_PORT:-3306}" "${DB_USER:-}" "${DB_PASSWORD:-}"
            if "${dump_client}" "${DB_CONN_ARGS[@]}" --single-transaction --quick \
               --skip-lock-tables --default-character-set=utf8mb4 "${DB_NAME}" | gzip -c > "${dump_file}"; then
                info "Backed up MariaDB database to ${dump_file}"
                LAST_DB_BACKUP_FILE="${dump_file}"
            else
                rm -f "${dump_file}" 2>/dev/null || true
                if [[ "${SKIP_DB_BACKUP:-0}" == "1" ]]; then
                    warn "Database backup failed for ${DB_NAME}, continuing because --skip-db-backup was given."
                else
                    die "Database backup failed for ${DB_NAME}. Refusing to continue: without it there is no way back from a schema change. Fix the problem, or re-run with --skip-db-backup if you accept that risk."
                fi
            fi
        elif [[ "${SKIP_DB_BACKUP:-0}" == "1" ]]; then
            warn "No mysqldump/mariadb-dump found, continuing because --skip-db-backup was given."
        else
            die "Could not find mysqldump or mariadb-dump, so no database backup can be taken. Install one, or re-run with --skip-db-backup if you accept that risk."
        fi
    fi

    # Staged avatar uploads are real data the database does not hold: they are
    # files awaiting an administrator's approval, and a restore without them
    # loses those submissions silently.
    if [[ -d "${INSTALL_DIR}/storage" ]] && command_exists tar; then
        local storage_archive="${BACKUP_DIR}/${APP_NAME}-storage-${backup_stamp}.tar.gz"
        if tar -czf "${storage_archive}" -C "${INSTALL_DIR}" storage 2>/dev/null; then
            info "Backed up uploaded files to ${storage_archive}"
        else
            rm -f "${storage_archive}" 2>/dev/null || true
            warn "Could not back up ${INSTALL_DIR}/storage."
        fi
    fi

    prune_old_runtime_backups
}

warn_local_repo_changes() {
    local target_dir="$1"

    if [[ ! -d "${target_dir}/.git" ]]; then
        return
    fi

    cleanup_legacy_repo_backups "${target_dir}"

    if [[ -n "$(git_in_dir "${target_dir}" status --porcelain)" ]]; then
        warn "Local git changes were detected in ${target_dir}. Repo changes are no longer auto-backed up by the installer and will be discarded by update. Runtime configuration and the local database are backed up separately."
    fi
}

record_rollback_point() {
    # Called before the checkout moves, so this records what is running *now*.
    local revision alembic_revision
    # PRE_UPDATE_REVISION is what was running before the self-update replaced
    # the checkout. Without it this reads HEAD, which by now is the revision
    # being installed -- so the rollback point named the version we are moving
    # *to*, and rolling back could only ever report "already running that".
    revision="${PRE_UPDATE_REVISION}"
    if [[ -z "${revision}" ]]; then
        revision="$(git_in_dir "${INSTALL_DIR}" rev-parse HEAD 2>/dev/null || printf '')"
    fi
    [[ -n "${revision}" ]] || return 0

    alembic_revision="$(current_schema_revision)"

    mkdir -p "${STATE_DIR}"
    cat > "${ROLLBACK_FILE}" <<EOF
# Written by install.sh before an update. "rollback --rollback" returns here.
ROLLBACK_REVISION="${revision}"
ROLLBACK_BRANCH="${BRANCH:-}"
ROLLBACK_SCHEMA_REVISION="${alembic_revision}"
ROLLBACK_DB_BACKUP="${LAST_DB_BACKUP_FILE:-}"
ROLLBACK_RECORDED_AT="$(date --iso-8601=seconds)"
EOF
    # Readable by the application group, not just root: the admin page shows
    # the rollback point, and the web process runs unprivileged. At 0600 it got
    # PermissionError and -- because a missing file is the normal state before
    # the first update -- silently rendered no rollback panel at all. The file
    # holds a revision, a schema revision and a backup path; no secrets.
    chown "root:${APP_GROUP}" "${ROLLBACK_FILE}" 2>/dev/null || true
    chmod 640 "${ROLLBACK_FILE}"
    info "Recorded rollback point at ${revision:0:8}."
    # Until the database step, a failure puts this code back
    # (undo_code_after_early_failure).
    ROLLBACK_POINT_REVISION="${revision}"
}

current_schema_revision() {
    # Read straight from the database rather than through the application, which
    # may be the very thing that is broken.
    local client
    if ! client="$(db_client 2>/dev/null)"; then
        printf ''
        return 0
    fi
    "${client}" --protocol=socket -u root -N -B -e \
        "SELECT version_num FROM \`${DB_NAME}\`.alembic_version LIMIT 1;" 2>/dev/null || printf ''
}

roll_back_installation() {
    [[ -f "${ROLLBACK_FILE}" ]] || die "No rollback point has been recorded yet, so there is nothing to go back to."
    # shellcheck disable=SC1090
    source "${ROLLBACK_FILE}"
    [[ -n "${ROLLBACK_REVISION:-}" ]] || die "The rollback point in ${ROLLBACK_FILE} has no revision recorded."

    local current_revision
    current_revision="$(git_in_dir "${INSTALL_DIR}" rev-parse HEAD 2>/dev/null || printf 'unknown')"
    if [[ "${current_revision}" == "${ROLLBACK_REVISION}" ]]; then
        success "Already running ${ROLLBACK_REVISION:0:8}; nothing to roll back."
        return 0
    fi

    step "Rolling back to ${ROLLBACK_REVISION:0:8}"

    # The unit file names the database and cache services it must start after.
    # install_or_update detects them; this path did not, so the rollback wrote
    # "After=network.target .service" and "Requires=.service" and systemd
    # dropped the ordering -- the app would then be free to start before
    # MariaDB on the next reboot.
    detect_redis_service_name
    if [[ "${USE_LOCAL_DB}" == "1" ]]; then
        detect_db_service_name
    fi
    info "Recorded at ${ROLLBACK_RECORDED_AT:-unknown}, currently running ${current_revision:0:8}."

    local schema_now
    schema_now="$(current_schema_revision)"
    if [[ -n "${ROLLBACK_SCHEMA_REVISION:-}" && -n "${schema_now}" && "${schema_now}" != "${ROLLBACK_SCHEMA_REVISION}" ]]; then
        # Deliberately not undone. Every migration here only adds tables and
        # columns, which older code ignores, so leaving the schema forward is
        # safe. Undoing it would mean dropping tables -- destroying everything
        # written since the update, which is worse than the fault being escaped.
        warn "The database schema moved from ${ROLLBACK_SCHEMA_REVISION} to ${schema_now} during that update."
        warn "The rollback restores the code only and leaves the schema as it is."
        if [[ -n "${ROLLBACK_DB_BACKUP:-}" && -f "${ROLLBACK_DB_BACKUP}" ]]; then
            warn "To put the data back as well, restore ${ROLLBACK_DB_BACKUP} by hand afterwards."
        fi
    fi

    git_in_dir "${INSTALL_DIR}" fetch --prune origin || true
    git_in_dir "${INSTALL_DIR}" checkout --detach "${ROLLBACK_REVISION}" \
        || die "Could not check out ${ROLLBACK_REVISION}. The repository may have been rewritten."

    # Reinstall from the rolled-back revision so dependencies, unit files and the
    # nginx configuration all match the code that is about to run.
    ensure_writable_directories
    ensure_virtualenv
    install_frontend
    render_service_file
    render_billing_reconcile_timer_files
    render_notifications_timer_files
    render_cleanup_logs_timer_files
    render_forum_drift_timer_files
    render_external_work_timer_files
    render_automatic_updates_timer_files
    render_update_runner
    render_update_command
    render_nginx_config
    systemctl daemon-reload
    reload_services
    verify_installation

    success "Rolled back to ${ROLLBACK_REVISION:0:8}."
}

sync_repo_to_dir() {
    local target_dir="$1"
    local repo_url="$2"
    local branch="$3"

    mkdir -p "$(dirname "${target_dir}")"

    if [[ -d "${target_dir}/.git" ]]; then
        warn_local_repo_changes "${target_dir}"
        info "Updating repository in ${target_dir}"
        git_in_dir "${target_dir}" remote set-url origin "${repo_url}" || true
        # Ensure the requested branch is fetched even if the original clone was a
        # single-branch (shallow) clone whose refspec only tracks its own branch.
        # Without this, switching to a different branch fails because
        # origin/<branch> is never fetched.
        git_in_dir "${target_dir}" config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*" || true
        retry 3 git_in_dir "${target_dir}" fetch --prune origin "+refs/heads/${branch}:refs/remotes/origin/${branch}"
        git_in_dir "${target_dir}" checkout -B "${branch}" "origin/${branch}"
        git_in_dir "${target_dir}" reset --hard "origin/${branch}"
        return
    fi

    if [[ -e "${target_dir}" && ! -d "${target_dir}" ]]; then
        local backup_file="${target_dir}.backup.$(date +%Y%m%d%H%M%S)"
        warn "Moving unexpected file ${target_dir} to ${backup_file}"
        mv "${target_dir}" "${backup_file}"
    elif [[ -d "${target_dir}" ]] && [[ -n "$(find "${target_dir}" -mindepth 1 -maxdepth 1 2>/dev/null | head -n 1)" ]]; then
        local backup_dir="${target_dir}.backup.$(date +%Y%m%d%H%M%S)"
        warn "Moving existing unmanaged directory ${target_dir} to ${backup_dir}"
        mv "${target_dir}" "${backup_dir}"
    fi

    rm -rf "${target_dir}"
    retry 3 git clone --branch "${branch}" --depth 1 "${repo_url}" "${target_dir}"
}

bootstrap_self_update() {
    if [[ "${SELF_REEXEC_MARKER:-0}" == "1" ]]; then
        return
    fi

    detect_package_manager
    mapfile -t bootstrap_pkg_list < <(bootstrap_packages)
    install_packages "${bootstrap_pkg_list[@]}"

    # Always a copy of its own, never the live checkout: the running portal
    # and its background jobs use the code in INSTALL_DIR, and moving it here
    # -- before the wait for CI, and before the jobs are paused -- had them
    # run the new code on the old packages and database, and left it there
    # when CI failed. ensure_repo_present moves INSTALL_DIR, after the pause.
    local bootstrap_target="${BOOTSTRAP_DIR}"
    rm -rf "${BOOTSTRAP_DIR}" 2>/dev/null || true

    if [[ -z "${PRE_UPDATE_REVISION}" && -d "${INSTALL_DIR}/.git" ]]; then
        PRE_UPDATE_REVISION="$(git_in_dir "${INSTALL_DIR}" rev-parse HEAD 2>/dev/null || printf '')"
    fi

    sync_repo_to_dir "${bootstrap_target}" "${REPO_URL}" "${BRANCH}"
    chmod +x "${bootstrap_target}/install.sh"
    TARGET_REVISION="$(git_in_dir "${bootstrap_target}" rev-parse HEAD 2>/dev/null || printf '')"

    step "Re-running installer from the latest repository copy"
    local status=0
    env \
        SELF_REEXEC_MARKER=1 \
        BOOTSTRAP_MODE="${MODE}" \
        BOOTSTRAP_REPO_URL="${REPO_URL}" \
        BOOTSTRAP_BRANCH="${BRANCH}" \
        BOOTSTRAP_INSTALL_DIR="${INSTALL_DIR}" \
        BOOTSTRAP_SERVICE_NAME="${SERVICE_NAME}" \
        BOOTSTRAP_APP_USER="${APP_USER}" \
        BOOTSTRAP_APP_GROUP="${APP_GROUP}" \
        BOOTSTRAP_APP_PORT="${APP_PORT}" \
        BOOTSTRAP_DOMAIN="${DOMAIN}" \
        BOOTSTRAP_ENABLE_SSL="${ENABLE_SSL}" \
        BOOTSTRAP_SSL_EMAIL="${SSL_EMAIL}" \
        BOOTSTRAP_USE_CLOUDFLARE_TUNNEL="${USE_CLOUDFLARE_TUNNEL}" \
        BOOTSTRAP_CLOUDFLARE_ORIGIN_HOST="${CLOUDFLARE_ORIGIN_HOST}" \
        BOOTSTRAP_ADMIN_EMAIL="${ADMIN_EMAIL}" \
        BOOTSTRAP_ADMIN_PASSWORD="${ADMIN_PASSWORD}" \
        BOOTSTRAP_NONINTERACTIVE="${NONINTERACTIVE}" \
        BOOTSTRAP_PRE_UPDATE_REVISION="${PRE_UPDATE_REVISION}" \
        BOOTSTRAP_TARGET_REVISION="${TARGET_REVISION}" \
        bash "${bootstrap_target}/install.sh" "${ORIGINAL_ARGS[@]}" || status=$?
    # An update that undid itself has said all there is to say; any other
    # failure gets its diagnostics here, as before.
    if (( status != 0 && status != UPDATE_UNDONE_STATUS )); then
        (exit "${status}")
    fi
    exit "${status}"
}

detect_db_service_name() {
    local candidate
    for candidate in mariadb mysql mysqld; do
        if systemctl list-unit-files "${candidate}.service" >/dev/null 2>&1; then
            DB_SERVICE_NAME="${candidate}"
            return
        fi
    done
    DB_SERVICE_NAME="mariadb"
}

detect_redis_service_name() {
    local candidate
    for candidate in redis-server redis; do
        if systemctl list-unit-files "${candidate}.service" >/dev/null 2>&1; then
            REDIS_SERVICE_NAME="${candidate}"
            return
        fi
    done
    REDIS_SERVICE_NAME="redis-server"
}

ensure_systemd_service() {
    local service_name="$1"
    systemctl enable --now "${service_name}"
}

ensure_app_user() {
    if ! getent group "${APP_GROUP}" >/dev/null 2>&1; then
        groupadd --system "${APP_GROUP}"
    fi

    if ! id -u "${APP_USER}" >/dev/null 2>&1; then
        useradd --system --gid "${APP_GROUP}" --home "${INSTALL_DIR}" --shell /usr/sbin/nologin "${APP_USER}"
    fi
}

run_as_app_user() {
    if command -v runuser >/dev/null 2>&1; then
        runuser -u "${APP_USER}" -- "$@"
    else
        sudo -u "${APP_USER}" "$@"
    fi
}

source_existing_env() {
    if [[ -f "${ENV_FILE}" ]]; then
        # shellcheck disable=SC1090
        set -a
        source "${ENV_FILE}"
        set +a
    fi
}

detect_existing_installation() {
    resolve_paths
    INSTALLATION_EXISTS=0

    if [[ -d "${INSTALL_DIR}" || -f "${SERVICE_FILE}" || -f "${STATE_FILE}" || -f "${NGINX_CONF_PATH}" || -L "${NGINX_ENABLED_PATH:-/dev/null}" ]]; then
        INSTALLATION_EXISTS=1
    fi
}

ensure_repo_present() {
    sync_repo_to_dir "${INSTALL_DIR}" "${REPO_URL}" "${BRANCH}"
    # The revision CI was checked for, even if the branch has moved on since.
    if [[ -n "${TARGET_REVISION}" \
          && "$(git_in_dir "${INSTALL_DIR}" rev-parse HEAD 2>/dev/null || true)" != "${TARGET_REVISION}" ]]; then
        info "The branch has moved on since this update began; installing ${TARGET_REVISION:0:8}, the version checked."
        retry 3 git_in_dir "${INSTALL_DIR}" fetch origin "${TARGET_REVISION}"
        git_in_dir "${INSTALL_DIR}" reset --hard "${TARGET_REVISION}"
    fi
    ensure_writable_directories
    chown -R "${APP_USER}:${APP_GROUP}" "${INSTALL_DIR}"
}

# The one directory the application writes inside its own tree, and the only
# one git cannot bring: it is ignored, so a fresh clone has no storage/ at all.
# Every unit grants it with ReadWritePaths, and systemd will not start a
# service whose ReadWritePaths names something that does not exist -- so on a
# genuinely fresh install the service failed at once with
#
#   Failed to set up mount namespacing: /var/www/jaeronautics/storage:
#   No such file or directory
#   status=226/NAMESPACE
#
# which never showed up in testing, because every install until then was an
# update over a tree where the running application had already made it.
ensure_writable_directories() {
    install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0750 "${INSTALL_DIR}/storage"
    install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0750 \
        "${INSTALL_DIR}/storage/forum_avatar_staging"
}

ensure_virtualenv() {
    if [[ ! -x "${INSTALL_DIR}/.venv/bin/python3" ]]; then
        step "Creating Python virtual environment"
        python3 -m venv "${INSTALL_DIR}/.venv"
    fi

    chown -R "${APP_USER}:${APP_GROUP}" "${INSTALL_DIR}/.venv"

    step "Installing Python dependencies"
    run_as_app_user "${INSTALL_DIR}/.venv/bin/pip" install --no-cache-dir --upgrade pip wheel

    # From the lock: it pins the transitive dependencies too and checks every
    # download against a recorded hash, so an update installs the same code
    # that was tested rather than whatever the index happens to serve today.
    # The lock is requirements.txt, compiled from requirements.in (the layout
    # Dependabot keeps current); a checkout from before that -- a rollback to
    # it -- has it as requirements.lock, compiled from requirements.txt.
    # USE_DEPENDENCY_LOCK=0 is the escape hatch if a locked version ever fails
    # to build on a newer Python -- but reach for it knowing the install is
    # then unpinned.
    local lock_file="" loose_file=""
    if [[ -f "${INSTALL_DIR}/requirements.in" ]]; then
        lock_file="${INSTALL_DIR}/requirements.txt"
        loose_file="${INSTALL_DIR}/requirements.in"
    elif [[ -f "${INSTALL_DIR}/requirements.lock" ]]; then
        lock_file="${INSTALL_DIR}/requirements.lock"
        loose_file="${INSTALL_DIR}/requirements.txt"
    else
        loose_file="${INSTALL_DIR}/requirements.txt"
    fi
    if [[ -n "${lock_file}" && "${USE_DEPENDENCY_LOCK:-1}" != "0" ]]; then
        if ! run_as_app_user "${INSTALL_DIR}/.venv/bin/pip" install --no-cache-dir \
            --require-hashes -r "${lock_file}"; then
            error "If a pinned version cannot be built here, regenerate the lock (docs/maintenance.md,"
            error "\"Dependencies\"); as a temporary measure, re-run with USE_DEPENDENCY_LOCK=0 to install"
            error "from ${loose_file##*/} without pinned transitive versions or hashes."
            die "Installing the locked dependencies failed."
        fi
    else
        warn "Installing from ${loose_file##*/}: transitive versions are not pinned."
        run_as_app_user "${INSTALL_DIR}/.venv/bin/pip" install --no-cache-dir --upgrade -r "${loose_file}"
    fi
}

# The front end (frontend/) is built by CI, not here: each step of the build
# needs 650-870 MB of memory, more than a server with 1 GB has free beside
# the portal and MariaDB -- and when memory runs out, the kernel may stop the
# database instead of the build. So CI builds it once its tests have passed
# (the publish-frontend job in .github/workflows/ci.yml) and attaches it to
# the "frontend-builds" pre-release on GitHub, one file per version of
# frontend/: named by the folder's git tree hash, so a commit that changes
# only Python reuses the build before it. The installer downloads the one
# for the revision it installs, waiting for CI if it is still running.
#
# Built here instead with --build-locally, or when there is nothing to
# download (not a GitHub repository, GitHub out of reach, an old build
# cleaned up): with temporary swap if memory is short, and at the lowest
# priority, the first thing stopped if memory runs out.
FRONTEND_RELEASE="frontend-builds"
FRONTEND_CI_WORKFLOW=".github/workflows/ci.yml"
FRONTEND_CI_WAIT_MINUTES="${FRONTEND_CI_WAIT_MINUTES:-25}"
FRONTEND_CACHE_DIR="/var/cache/${APP_NAME}/frontend"
# Earlier builds kept here, so a rollback finds its own.
FRONTEND_CACHE_KEEP=3
BUILD_LOCALLY="${BUILD_LOCALLY:-0}"
FRONTEND_PREPARED=0
FRONTEND_TREE=""
FRONTEND_ARCHIVE=""
# Memory (RAM and swap) the build is given room for; measured peak 865 MB.
BUILD_MEMORY_NEEDED_MB=2048
BUILD_SWAP_FILE="/${APP_NAME}-build.swap"
BUILD_SWAP_ACTIVE=0

# Node.js, only to build here: the current long-term-support release, from
# NodeSource's package repository, as Ubuntu's own is too old for the build tools.
NODE_MAJOR=24

node_major() {
    command_exists node || return 0
    node --version 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/'
}

ensure_nodejs() {
    [[ -n "${PACKAGE_MANAGER:-}" ]] || detect_package_manager
    local have
    have="$(node_major)"
    if [[ -n "${have}" ]] && (( have >= NODE_MAJOR )); then
        info "Node.js $(node --version) is installed."
        return 0
    fi

    step "Installing Node.js ${NODE_MAJOR} (to build the front end)"
    case "${PACKAGE_MANAGER}" in
        apt)
            install_packages ca-certificates curl gnupg
            install -d -m 0755 /etc/apt/keyrings
            local key
            key="$(mktemp)"
            retry 3 curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key -o "${key}"
            gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg "${key}"
            rm -f "${key}"
            chmod 0644 /etc/apt/keyrings/nodesource.gpg
            printf 'deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_%s.x nodistro main\n' \
                "${NODE_MAJOR}" > /etc/apt/sources.list.d/nodesource.list
            # The new repository has to be read before its package can be found.
            PACKAGE_CACHE_UPDATED=0
            update_package_index_once
            retry 3 apt-get "${APT_NETWORK_OPTS[@]}" install -y nodejs
            ;;
        dnf|yum)
            install_packages nodejs npm
            ;;
    esac

    have="$(node_major)"
    if [[ -z "${have}" ]] || (( have < NODE_MAJOR )); then
        die "Node.js ${NODE_MAJOR} or newer is needed to build the front end; found ${have:-none}."
    fi
    success "Node.js $(node --version) installed."
}

# owner/repo of a repository on GitHub, or nothing.
github_repo_slug() {
    local url
    url="$(normalize_repo_url "${REPO_URL:-$(detect_repo_url)}")"
    case "${url}" in
        https://github.com/*) url="${url#https://github.com/}" ;;
        git@github.com:*) url="${url#git@github.com:}" ;;
        *) return 0 ;;
    esac
    if [[ "${url}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
        printf '%s\n' "${url}"
    fi
}

# Whether CI passed for a commit: success, failure:<why>, running, none (no
# run yet) or unknown (GitHub could not be asked). Unauthenticated: the
# repository is public, and GitHub allows 60 such questions an hour. A run
# for the commit itself: started by its push, or -- a Dependabot update CI
# merged -- by CI on the branch afterwards; not a pull request's, which tests
# it merged into another branch.
ci_state() {
    local slug="$1" sha="$2" answer
    if ! answer="$(curl -fsS --max-time 20 -H 'Accept: application/vnd.github+json' \
        "https://api.github.com/repos/${slug}/actions/runs?head_sha=${sha}&per_page=50" 2>/dev/null)"; then
        printf 'unknown\n'
        return 0
    fi
    printf '%s' "${answer}" | python3 -c '
import json, sys
try:
    runs = json.load(sys.stdin).get("workflow_runs") or []
except ValueError:
    print("unknown")
    raise SystemExit
runs = [run for run in runs if (run.get("path") or "").split("@")[0] == sys.argv[1]
        and run.get("event") in ("push", "workflow_dispatch")]
if not runs:
    print("none")
else:
    run = max(runs, key=lambda run: (run.get("run_number") or 0, run.get("run_attempt") or 0))
    if run.get("status") != "completed":
        print("running")
    elif run.get("conclusion") == "success":
        print("success")
    else:
        print("failure:" + str(run.get("conclusion")))
' "${FRONTEND_CI_WORKFLOW}" 2>/dev/null || printf 'unknown\n'
}

# Returns once CI has passed for the commit, or cannot be asked; stops the
# run if it failed or does not finish in time. Nothing is stopped yet when
# this runs for an update (prepare_frontend), so the portal is untouched.
wait_for_ci() {
    local slug="$1" sha="$2" state waited=0
    local limit=$(( FRONTEND_CI_WAIT_MINUTES * 60 ))
    local runs="https://github.com/${slug}/actions"
    while :; do
        state="$(ci_state "${slug}" "${sha}")"
        case "${state}" in
            success)
                info "CI passed for ${sha:0:8}."
                return 0
                ;;
            failure:*)
                die "CI did not pass for ${sha:0:8} (${state#failure:}; ${runs}), so this version is not installed. Fix it and update again -- or, to install it anyway, run the update with --build-locally."
                ;;
            unknown)
                warn "Could not ask GitHub whether CI passed for ${sha:0:8}; going on."
                return 0
                ;;
            none)
                # A push only just made has no run for a moment.
                if (( waited >= 180 )); then
                    warn "CI has not run for ${sha:0:8} (${runs}); going on without its result."
                    return 0
                fi
                ;;
            running)
                if (( waited >= limit )); then
                    die "CI has not finished for ${sha:0:8} after ${FRONTEND_CI_WAIT_MINUTES} minutes (${runs}). Run the update again once it has -- or with --build-locally to build here."
                fi
                ;;
        esac
        if (( waited == 0 )); then
            info "CI is still at work on ${sha:0:8} (${runs}); waiting for it, up to ${FRONTEND_CI_WAIT_MINUTES} minutes."
        elif (( waited % 300 == 0 )); then
            info "Still waiting for CI ($(( waited / 60 )) minutes so far)."
        fi
        # Once a minute: GitHub answers 60 questions an hour without an account.
        sleep 60
        waited=$(( waited + 60 ))
    done
}

# Gets the front end for the revision being installed ready:
# FRONTEND_ARCHIVE is CI's build, downloaded -- or empty, and it is built
# here. Run for an update before anything is stopped, so waiting for CI, or
# CI having failed, costs the running portal nothing.
prepare_frontend() {
    FRONTEND_PREPARED=1
    FRONTEND_ARCHIVE=""
    # The version being installed: the bootstrap's copy, as INSTALL_DIR is
    # only moved to it later.
    local checkout="${INSTALL_DIR}"
    if [[ -n "${TARGET_REVISION}" && -d "${SCRIPT_DIR}/.git" ]]; then
        checkout="${SCRIPT_DIR}"
    fi
    FRONTEND_TREE="$(git_in_dir "${checkout}" rev-parse HEAD:frontend 2>/dev/null || true)"
    [[ -f "${checkout}/frontend/package.json" && -n "${FRONTEND_TREE}" ]] || return 0
    if [[ "${BUILD_LOCALLY}" == "1" ]]; then
        info "The front end is built on this server, as asked (--build-locally)."
        return 0
    fi

    local slug sha
    slug="$(github_repo_slug)"
    sha="$(git_in_dir "${checkout}" rev-parse HEAD 2>/dev/null || true)"
    if [[ -z "${slug}" || -z "${sha}" ]]; then
        warn "This repository is not on GitHub, so there is no build from CI to fetch; the front end is built on this server."
        return 0
    fi

    # The step's name stays the same from update to update, so the admin page
    # can list it ahead as one still to come; the revision goes on its own line.
    step "Fetching the front end built by CI"
    info "Built from ${sha:0:8}."
    wait_for_ci "${slug}" "${sha}"

    local name="frontend-${FRONTEND_TREE}.tar.gz"
    install -d -m 0755 "${FRONTEND_CACHE_DIR}"
    if [[ -s "${FRONTEND_CACHE_DIR}/${name}" ]]; then
        info "This version of the front end is here already, from an earlier update."
        FRONTEND_ARCHIVE="${FRONTEND_CACHE_DIR}/${name}"
        return 0
    fi
    local url="https://github.com/${slug}/releases/download/${FRONTEND_RELEASE}/${name}"
    local part="${FRONTEND_CACHE_DIR}/${name}.part"
    if retry 3 curl -fsSL --max-time 300 -o "${part}" "${url}" && tar -tzf "${part}" >/dev/null 2>&1; then
        mv -f "${part}" "${FRONTEND_CACHE_DIR}/${name}"
        FRONTEND_ARCHIVE="${FRONTEND_CACHE_DIR}/${name}"
        success "Downloaded the front end ($(du -h "${FRONTEND_ARCHIVE}" | cut -f1))."
    else
        rm -f "${part}"
        warn "CI's build of this front end could not be downloaded (${url}); it is built on this server instead."
    fi
}

# Puts the front end of the revision in INSTALL_DIR in place: CI's build,
# or one made here.
install_frontend() {
    if [[ ! -f "${INSTALL_DIR}/frontend/package.json" ]]; then
        info "This revision has no front end to build."
        return 0
    fi
    # Prepared before the update began -- unless the checkout has moved on since.
    if [[ "${FRONTEND_PREPARED}" != "1" \
        || "$(git_in_dir "${INSTALL_DIR}" rev-parse HEAD:frontend 2>/dev/null || true)" != "${FRONTEND_TREE}" ]]; then
        prepare_frontend
    fi
    local next="${INSTALL_DIR}/aeronautics_members/static/app.next"
    if [[ -n "${FRONTEND_ARCHIVE}" ]]; then
        step "Putting the front end in place"
        rm -rf "${next}"
        install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0755 "${next}"
        # Unpacked as the portal's user: nothing in it is owned by root.
        run_as_app_user tar -xzf "${FRONTEND_ARCHIVE}" -C "${next}" --no-same-owner \
            || die "Unpacking the front end (${FRONTEND_ARCHIVE}) failed."
        [[ -f "${next}/index.html" ]] || die "The front end from CI has no index.html."
        [[ "$(cat "${next}/.build-tree" 2>/dev/null || true)" == "${FRONTEND_TREE}" ]] \
            || die "The front end from CI was built from other sources than this revision's."
    else
        build_frontend_here "${next}"
    fi
    swap_in_frontend "${next}"
    # The newest few, for a rollback.
    if [[ -d "${FRONTEND_CACHE_DIR}" ]]; then
        find "${FRONTEND_CACHE_DIR}" -maxdepth 1 -name 'frontend-*.tar.gz' -printf '%T@ %p\n' \
            | sort -rn | tail -n +$(( FRONTEND_CACHE_KEEP + 1 )) | cut -d' ' -f2- | xargs -r rm -f
    fi
}

# The build gives way to the portal: the lowest CPU and disk priority, and
# the first thing the kernel stops if memory runs out -- not MariaDB or
# gunicorn.
run_build_step() {
    local priority=(nice -n 19)
    if command_exists ionice; then
        priority+=(ionice -c 2 -n 7)
    fi
    run_as_app_user env HOME="${INSTALL_DIR}" "${priority[@]}" \
        bash -c 'echo 1000 > /proc/self/oom_score_adj 2>/dev/null || true; exec "$@"' build-step "$@"
}

# Temporary swap, when RAM and swap together leave less free than the build
# needs. Taken away again by remove_build_swap, also when the run fails.
add_build_swap() {
    local available size disk_free
    available="$(awk '/^(MemAvailable|SwapFree):/ { sum += $2 } END { print int(sum / 1024) }' /proc/meminfo)"
    if (( available >= BUILD_MEMORY_NEEDED_MB )); then
        info "${available} MB of memory free (RAM and swap): enough for the build."
        return 0
    fi
    # What is missing, rounded up to 512 MB, and 512 MB to spare.
    size=$(( (BUILD_MEMORY_NEEDED_MB - available + 511) / 512 * 512 + 512 ))
    disk_free="$(df --output=avail -m / | tail -n 1 | tr -d ' ')"
    if (( disk_free < size + 1024 )); then
        warn "Only ${available} MB of memory free, and too little disk space (${disk_free} MB) for temporary swap; building anyway."
        return 0
    fi
    info "Only ${available} MB of memory free (RAM and swap): adding ${size} MB of temporary swap for the build."
    rm -f "${BUILD_SWAP_FILE}"
    local made=0
    # fallocate is instant; a file system that will not swap to one gets a written-out file instead.
    if fallocate -l "${size}M" "${BUILD_SWAP_FILE}" 2>/dev/null; then
        chmod 600 "${BUILD_SWAP_FILE}"
        mkswap "${BUILD_SWAP_FILE}" >/dev/null 2>&1 && swapon "${BUILD_SWAP_FILE}" 2>/dev/null && made=1
    fi
    if (( ! made )); then
        rm -f "${BUILD_SWAP_FILE}"
        if dd if=/dev/zero of="${BUILD_SWAP_FILE}" bs=1M count="${size}" status=none 2>/dev/null; then
            chmod 600 "${BUILD_SWAP_FILE}"
            mkswap "${BUILD_SWAP_FILE}" >/dev/null 2>&1 && swapon "${BUILD_SWAP_FILE}" 2>/dev/null && made=1
        fi
    fi
    if (( made )); then
        BUILD_SWAP_ACTIVE=1
    else
        rm -f "${BUILD_SWAP_FILE}"
        warn "Could not add temporary swap; building anyway."
    fi
}

remove_build_swap() {
    (( ${BUILD_SWAP_ACTIVE:-0} )) || return 0
    if swapoff "${BUILD_SWAP_FILE}" 2>/dev/null; then
        rm -f "${BUILD_SWAP_FILE}"
        BUILD_SWAP_ACTIVE=0
        info "Temporary swap removed."
    else
        warn "Could not remove the temporary swap ${BUILD_SWAP_FILE}; remove it later with: swapoff ${BUILD_SWAP_FILE} && rm ${BUILD_SWAP_FILE}"
    fi
}

# The fallback: the build made here, into NEXT.
build_frontend_here() {
    local next="$1"
    local frontend="${INSTALL_DIR}/frontend"
    local cache="/var/cache/${APP_NAME}/npm"

    ensure_nodejs
    step "Building the front end on this server"
    add_build_swap
    install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0750 "${cache}"
    rm -rf "${next}"
    if ! run_build_step npm --prefix "${frontend}" ci --no-audit --no-fund --cache "${cache}"; then
        remove_build_swap
        die "Installing the front end's packages failed."
    fi
    if ! run_build_step npm --prefix "${frontend}" run build -- --outDir "${next}" --emptyOutDir; then
        remove_build_swap
        die "Building the front end failed."
    fi
    remove_build_swap
    [[ -f "${next}/index.html" ]] || die "The front-end build produced no index.html."
    success "Front end built."
}

# Swapped in at the end, so the portal never serves a half-written front end.
# The previous one's files stay one more update: a page opened before it
# still loads the scripts it was built with.
swap_in_frontend() {
    local next="$1"
    local static="${INSTALL_DIR}/aeronautics_members/static"
    local live="${static}/app"

    # This build's own files, so the next one carries over these and not
    # everything ever built.
    (cd "${next}/assets" && ls -1) > "${next}/.build-files"
    if [[ -f "${live}/.build-files" ]]; then
        local name
        while IFS= read -r name; do
            if [[ -f "${live}/assets/${name}" && ! -e "${next}/assets/${name}" ]]; then
                cp -p "${live}/assets/${name}" "${next}/assets/${name}"
            fi
        done < "${live}/.build-files"
    fi

    chown -R "${APP_USER}:${APP_GROUP}" "${next}"
    rm -rf "${static}/app.previous"
    if [[ -d "${live}" ]]; then
        mv "${live}" "${static}/app.previous"
    fi
    mv "${next}" "${live}"
    rm -rf "${static}/app.previous"
    success "Front end in place."
}

collect_configuration() {
    source_existing_env
    local reconfigure_all="0"
    local detected_origin_host=""

    if [[ "${MODE}" == "install" || "${MODE}" == "repair" ]]; then
        reconfigure_all="1"
    fi

    if [[ -z "${SECRET_KEY:-}" ]] || is_placeholder "${SECRET_KEY:-}"; then
        SECRET_KEY="$(generate_secret)"
        info "Generated a new Flask SECRET_KEY."
    fi

    LANGUAGES="${LANGUAGES:-$DEFAULT_LANGUAGES}"
    DB_PORT="${DB_PORT:-3306}"
    DB_NAME="${DB_NAME:-$DEFAULT_DB_NAME}"
    DB_USER="${DB_USER:-$DEFAULT_DB_USER}"
    MAIL_ACCOUNTS_JSON="${MAIL_ACCOUNTS_JSON:-{}}"
    if validate_json_object "${MAIL_ACCOUNTS_JSON}"; then
        MAIL_ACCOUNTS_JSON="$(normalize_json_object "${MAIL_ACCOUNTS_JSON}")"
    else
        MAIL_ACCOUNTS_JSON="{}"
    fi
    RATELIMIT_STORAGE_URI="${RATELIMIT_STORAGE_URI:-$DEFAULT_RATELIMIT_STORAGE_URI}"

    if [[ "${DB_HOST:-127.0.0.1}" == "127.0.0.1" || "${DB_HOST:-localhost}" == "localhost" ]]; then
        USE_LOCAL_DB="1"
    else
        USE_LOCAL_DB="0"
    fi

    local domain_default="${DOMAIN:-$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo "_")}"
    if [[ "${reconfigure_all}" == "1" || -z "${DOMAIN:-}" ]]; then
        prompt_value DOMAIN "Domain for nginx (use _ for a catch-all server)" "${domain_default}" 0 1
    fi

    if [[ "${reconfigure_all}" == "1" || -z "${USE_CLOUDFLARE_TUNNEL:-}" ]]; then
        prompt_yes_no USE_CLOUDFLARE_TUNNEL "Will this site be exposed through a Cloudflare Tunnel?" "${USE_CLOUDFLARE_TUNNEL:-0}"
    fi
    if [[ "${USE_CLOUDFLARE_TUNNEL}" == "1" && "${DOMAIN}" == "_" ]]; then
        prompt_value DOMAIN "Public hostname for the Cloudflare Tunnel" "" 0 1
    fi
    if [[ "${USE_CLOUDFLARE_TUNNEL}" == "1" ]]; then
        detected_origin_host="${CLOUDFLARE_ORIGIN_HOST:-$(detect_reachable_ipv4)}"
        if [[ -z "${detected_origin_host}" ]]; then
            detected_origin_host="127.0.0.1"
        fi
        if [[ "${reconfigure_all}" == "1" || -z "${CLOUDFLARE_ORIGIN_HOST:-}" ]]; then
            prompt_value CLOUDFLARE_ORIGIN_HOST "Reachable internal origin host/IP for the Cloudflare Tunnel" "${detected_origin_host}" 0 1
        fi
    else
        CLOUDFLARE_ORIGIN_HOST=""
    fi

    # Snapshot the database this installation is using right now, before the
    # prompts below overwrite it. If the answers move the database, these are
    # the only remaining directions to where the data actually lives.
    SOURCE_DB_HOST="${DB_HOST:-}"
    SOURCE_DB_PORT="${DB_PORT:-3306}"
    SOURCE_DB_NAME="${DB_NAME:-}"
    SOURCE_DB_USER="${DB_USER:-}"
    SOURCE_DB_PASSWORD="${DB_PASSWORD:-}"

    if [[ "${reconfigure_all}" == "1" || -z "${DB_HOST:-}" ]]; then
        prompt_yes_no USE_LOCAL_DB "Use a locally managed MariaDB database?" "${USE_LOCAL_DB}"
    fi

    if [[ "${USE_LOCAL_DB}" == "1" ]]; then
        DB_HOST="127.0.0.1"
        if [[ "${reconfigure_all}" == "1" || -z "${DB_NAME:-}" ]]; then
            prompt_value DB_NAME "Local MariaDB database name" "${DB_NAME}" 0 1
        fi
        if [[ "${reconfigure_all}" == "1" || -z "${DB_USER:-}" ]]; then
            prompt_value DB_USER "Local MariaDB database user" "${DB_USER}" 0 1
        fi
        if [[ -z "${DB_PASSWORD:-}" ]] || is_placeholder "${DB_PASSWORD:-}"; then
            DB_PASSWORD="$(generate_secret)"
            info "Generated a local MariaDB password."
        fi
    else
        if [[ "${reconfigure_all}" == "1" || -z "${DB_HOST:-}" ]]; then
            prompt_value DB_HOST "Database host" "${DB_HOST:-}" 0 1
        fi
        if [[ "${reconfigure_all}" == "1" || -z "${DB_PORT:-}" ]]; then
            prompt_value DB_PORT "Database port" "${DB_PORT:-3306}" 0 1
        fi
        if [[ "${reconfigure_all}" == "1" || -z "${DB_NAME:-}" ]]; then
            prompt_value DB_NAME "Database name" "${DB_NAME}" 0 1
        fi
        if [[ "${reconfigure_all}" == "1" || -z "${DB_USER:-}" ]]; then
            prompt_value DB_USER "Database user" "${DB_USER}" 0 1
        fi
        if [[ "${reconfigure_all}" == "1" || -z "${DB_PASSWORD:-}" ]]; then
            prompt_value DB_PASSWORD "Database password" "${DB_PASSWORD:-}" 1 1
        fi
    fi

    offer_database_migration

    if is_placeholder "${STRIPE_SECRET_KEY:-}"; then
        STRIPE_SECRET_KEY=""
    fi
    if is_placeholder "${STRIPE_PUBLISHABLE_KEY:-}"; then
        STRIPE_PUBLISHABLE_KEY=""
    fi
    if is_placeholder "${STRIPE_PRICE_ID:-}"; then
        STRIPE_PRICE_ID=""
    fi
    if is_placeholder "${STRIPE_WEBHOOK_SECRET:-}"; then
        STRIPE_WEBHOOK_SECRET=""
    fi

    if [[ "${reconfigure_all}" == "1" || -z "${STRIPE_SECRET_KEY:-}" ]]; then
        prompt_value STRIPE_SECRET_KEY "Stripe secret key" "${STRIPE_SECRET_KEY:-}" 1 1
    fi
    if [[ "${reconfigure_all}" == "1" || -z "${STRIPE_PUBLISHABLE_KEY:-}" ]]; then
        prompt_value STRIPE_PUBLISHABLE_KEY "Stripe publishable key" "${STRIPE_PUBLISHABLE_KEY:-}" 0 1
    fi
    if [[ "${reconfigure_all}" == "1" || -z "${STRIPE_PRICE_ID:-}" ]]; then
        prompt_value STRIPE_PRICE_ID "Stripe price ID" "${STRIPE_PRICE_ID:-}" 0 1
    fi
    if [[ "${reconfigure_all}" == "1" || -z "${STRIPE_WEBHOOK_SECRET:-}" ]]; then
        prompt_value STRIPE_WEBHOOK_SECRET "Stripe webhook secret" "${STRIPE_WEBHOOK_SECRET:-}" 1 1
    fi

    if [[ "${reconfigure_all}" == "1" || -z "${ENABLE_SSL}" ]]; then
        if [[ "${DOMAIN}" == "_" ]]; then
            ENABLE_SSL="0"
        elif [[ "${USE_CLOUDFLARE_TUNNEL}" == "1" ]]; then
            prompt_yes_no ENABLE_SSL "Also configure origin HTTPS with Let's Encrypt? This is usually not necessary behind Cloudflare Tunnel." "0"
        else
            prompt_yes_no ENABLE_SSL "Configure HTTPS with Let's Encrypt?" "0"
        fi
    fi

    if [[ "${ENABLE_SSL}" == "1" && ( "${reconfigure_all}" == "1" || -z "${SSL_EMAIL:-}" ) ]]; then
        prompt_value SSL_EMAIL "Let's Encrypt email address" "${SSL_EMAIL:-}" 0 1
    fi
}

write_env_file() {
    step "Writing application environment file"
    local public_scheme="http"
    local public_base_url=""

    if [[ -n "${DOMAIN:-}" && "${DOMAIN}" != "_" ]]; then
        if [[ "${ENABLE_SSL}" == "1" || "${USE_CLOUDFLARE_TUNNEL}" == "1" ]]; then
            public_scheme="https"
        fi
        public_base_url="${public_scheme}://${DOMAIN}"

        if [[ "${public_scheme}" == "http" ]]; then
            warn "Public deployment configured over plain HTTP (no Let's Encrypt, no Cloudflare Tunnel)."
            warn "The app requires HTTPS for its session cookie (SESSION_COOKIE_SECURE), so login will NOT work over http://${DOMAIN}."
            warn "Enable Let's Encrypt or put the site behind a Cloudflare Tunnel (which terminates TLS) before using it."
        fi
    fi

    cat > "${ENV_FILE}" <<EOF
SECRET_KEY=$(dotenv_quote "${SECRET_KEY}")
LANGUAGES=$(dotenv_quote "${LANGUAGES}")
DB_HOST=$(dotenv_quote "${DB_HOST}")
DB_NAME=$(dotenv_quote "${DB_NAME}")
DB_USER=$(dotenv_quote "${DB_USER}")
DB_PASSWORD=$(dotenv_quote "${DB_PASSWORD}")
DB_PORT=$(dotenv_quote "${DB_PORT}")
STRIPE_SECRET_KEY=$(dotenv_quote "${STRIPE_SECRET_KEY}")
STRIPE_PUBLISHABLE_KEY=$(dotenv_quote "${STRIPE_PUBLISHABLE_KEY}")
STRIPE_PRICE_ID=$(dotenv_quote "${STRIPE_PRICE_ID}")
STRIPE_WEBHOOK_SECRET=$(dotenv_quote "${STRIPE_WEBHOOK_SECRET}")
PUBLIC_BASE_URL=$(dotenv_quote "${public_base_url}")
RATELIMIT_STORAGE_URI=$(dotenv_quote "${RATELIMIT_STORAGE_URI}")
MAIL_ACCOUNTS_JSON=$(dotenv_quote "${MAIL_ACCOUNTS_JSON}")
UPDATE_STATE_DIR=$(dotenv_quote "${UPDATE_STATE_DIR}")
UPDATE_RUNNER_DIR=$(dotenv_quote "${UPDATE_RUNNER_DIR}")
EOF

    chown root:"${APP_GROUP}" "${ENV_FILE}"
    chmod 640 "${ENV_FILE}"
}

db_client() {
    if command_exists mariadb; then
        printf '%s\n' mariadb
    elif command_exists mysql; then
        printf '%s\n' mysql
    else
        die "Neither mariadb nor mysql CLI is installed."
    fi
}

run_db_sql() {
    local sql="$1"
    local client
    client="$(db_client)"
    "${client}" --protocol=socket -u root -e "${sql}"
}

ensure_database() {
    if [[ "${USE_LOCAL_DB}" != "1" ]]; then
        info "Skipping local MariaDB provisioning because an external database was selected."
        return
    fi

    step "Configuring local MariaDB database"
    # The password is escaped because it is a string literal and may legitimately
    # contain anything; the names are identifiers and are validated instead.
    require_safe_sql_identifier "The database name" "${DB_NAME}" 64
    require_safe_sql_identifier "The database user" "${DB_USER}" 32
    local password_sql
    password_sql="$(sql_escape "${DB_PASSWORD}")"
    run_db_sql "
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${password_sql}';
CREATE USER IF NOT EXISTS '${DB_USER}'@'127.0.0.1' IDENTIFIED BY '${password_sql}';
ALTER USER '${DB_USER}'@'localhost' IDENTIFIED BY '${password_sql}';
ALTER USER '${DB_USER}'@'127.0.0.1' IDENTIFIED BY '${password_sql}';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'127.0.0.1';
FLUSH PRIVILEGES;"
}

# ---------------------------------------------------------------------------
# Carrying the data across when the database moves
#
# Switching between a locally managed MariaDB and an external server is a
# configuration change, but the data does not follow by itself -- so without
# this the app comes back up against an empty database and looks, convincingly,
# like every member has vanished. The move works in both directions: out to a
# managed server when this outgrows one box, and back in when a single box is
# easier to snapshot.
#
# The ordering is the safety property. The dump is taken and verified while the
# old configuration is still the live one, so anything that fails leaves a
# working installation pointed at the database that still holds the data.
# ---------------------------------------------------------------------------

# Has the database actually moved, and if so, does the data follow?
#
# Only asked when there was a database before and it is not the one now
# configured -- on a first install there is nothing to carry, and answering a
# question about moving data that does not exist yet is just confusing.
offer_database_migration() {
    MIGRATE_DB_DATA="0"

    [[ -n "${SOURCE_DB_HOST}" && -n "${SOURCE_DB_NAME}" ]] || return 0
    if [[ "${SOURCE_DB_HOST}" == "${DB_HOST}" && "${SOURCE_DB_NAME}" == "${DB_NAME}" \
          && "${SOURCE_DB_PORT}" == "${DB_PORT}" ]]; then
        return 0
    fi

    local moving_to="an external server at ${DB_HOST}"
    db_host_is_local "${DB_HOST}" && moving_to="a locally managed MariaDB"

    tty_print "\n${COLOR_BOLD}The database is moving.${COLOR_RESET}\n"
    tty_print "  from  ${SOURCE_DB_NAME} on ${SOURCE_DB_HOST}\n"
    tty_print "  to    ${DB_NAME} on ${moving_to}\n"
    tty_print "Without copying the data across, the site comes back up empty.\n"
    prompt_yes_no MIGRATE_DB_DATA "Copy the existing data to the new database?" "1"

    if [[ "${MIGRATE_DB_DATA}" != "1" ]]; then
        warn "Starting with an empty database. The old one at ${SOURCE_DB_HOST} is left as it is."
    fi
}

db_host_is_local() {
    case "${1:-}" in
        ""|127.0.0.1|localhost|::1) return 0 ;;
        *) return 1 ;;
    esac
}

# Connection arguments for a database, returned in DB_CONN_ARGS.
#
# A password is never passed as an argument: ps(1) shows every argument of every
# process to every user on the box, so it goes into a 0600 defaults file that is
# removed when the installer exits. --defaults-extra-file has to come first or
# the client ignores it.
set_db_conn_args() {
    local host="$1" port="$2" user="$3" password="$4"
    DB_CONN_ARGS=()

    if db_host_is_local "${host}"; then
        # The local server is administered over the unix socket as root, the
        # same way ensure_database provisions it.
        DB_CONN_ARGS=(--protocol=socket -u root)
        return
    fi

    local creds
    creds="$(mktemp "${TMPDIR:-/tmp}/jaeronautics-db.XXXXXX.cnf")"
    chmod 600 "${creds}"
    printf '[client]\nuser=%s\npassword=%s\n' "${user}" "${password}" > "${creds}"
    MIGRATION_TEMP_FILES+=("${creds}")
    DB_CONN_ARGS=(--defaults-extra-file="${creds}" -h "${host}" -P "${port:-3306}" --protocol=TCP)
}

cleanup_migration_temp_files() {
    local file=""
    for file in "${MIGRATION_TEMP_FILES[@]:-}"; do
        [[ -n "${file}" ]] && rm -f "${file}"
    done
    MIGRATION_TEMP_FILES=()
}

# table<TAB>rowcount for every base table, which is what the import is checked
# against. Comparing row counts rather than just "did mysql exit 0" is the
# difference between knowing the data arrived and hoping it did.
db_table_row_counts() {
    local database="$1"
    local client table
    client="$(db_client)"
    while IFS= read -r table; do
        [[ -z "${table}" ]] && continue
        printf '%s\t%s\n' "${table}" "$("${client}" "${DB_CONN_ARGS[@]}" -N -B \
            -e "SELECT COUNT(*) FROM \`${database}\`.\`${table}\`" 2>/dev/null || printf 'ERR')"
    done < <("${client}" "${DB_CONN_ARGS[@]}" -N -B -e "
        SELECT table_name FROM information_schema.tables
         WHERE table_schema = '$(sql_escape "${database}")'
           AND table_type = 'BASE TABLE'
         ORDER BY table_name" 2>/dev/null)
}

# A dump that was cut off part way -- a full disk, a dropped connection, a
# server that went away -- looks exactly like a good one until you restore it.
# mysqldump writes its trailer last, so the trailer is the proof it finished.
verify_sql_dump() {
    local file="$1"
    local tables=0

    [[ -s "${file}" ]] || { error "The dump file is empty or missing: ${file}"; return 1; }
    if ! tail -5 "${file}" | grep -q "Dump completed"; then
        error "The dump has no completion marker, so it was cut short: ${file}"
        return 1
    fi
    tables="$(grep -c "^CREATE TABLE" "${file}" || true)"
    if [[ "${tables}" -lt 1 ]]; then
        error "The dump contains no tables, so it is not a usable copy: ${file}"
        return 1
    fi
    info "Dump verified: ${tables} tables, $(du -h "${file}" | cut -f1)."
    return 0
}

dump_source_database() {
    step "Copying the existing data out of ${SOURCE_DB_NAME} on ${SOURCE_DB_HOST}"

    local dump_client
    dump_client="$(db_dump_client)" || die "Neither mariadb-dump nor mysqldump is installed, so the data cannot be carried across."

    mkdir -p "${BACKUP_DIR}"
    MIGRATION_DUMP_FILE="${BACKUP_DIR}/${APP_NAME}-migration-$(date +%Y%m%d%H%M%S).sql"

    set_db_conn_args "${SOURCE_DB_HOST}" "${SOURCE_DB_PORT}" "${SOURCE_DB_USER}" "${SOURCE_DB_PASSWORD}"
    MIGRATION_SOURCE_COUNTS="$(db_table_row_counts "${SOURCE_DB_NAME}")"
    if [[ -z "${MIGRATION_SOURCE_COUNTS}" ]]; then
        die "Could not read ${SOURCE_DB_NAME} on ${SOURCE_DB_HOST}. Check the host, user and password before retrying; nothing has been changed."
    fi

    # --single-transaction so the other databases on a shared server keep
    # working; --routines and --triggers because a schema is not only tables.
    if ! "${dump_client}" "${DB_CONN_ARGS[@]}" --single-transaction --quick \
         --skip-lock-tables --routines --triggers --default-character-set=utf8mb4 \
         "${SOURCE_DB_NAME}" > "${MIGRATION_DUMP_FILE}" 2>"${MIGRATION_DUMP_FILE}.err"; then
        error "Reading the existing database failed:"
        sed 's/^/    /' "${MIGRATION_DUMP_FILE}.err" >&2 || true
        rm -f "${MIGRATION_DUMP_FILE}" "${MIGRATION_DUMP_FILE}.err"
        die "Nothing has been changed; the installation still points at ${SOURCE_DB_HOST}."
    fi
    rm -f "${MIGRATION_DUMP_FILE}.err"
    chmod 600 "${MIGRATION_DUMP_FILE}"

    verify_sql_dump "${MIGRATION_DUMP_FILE}" \
        || die "Nothing has been changed; the installation still points at ${SOURCE_DB_HOST}."
    success "Saved a verified copy to ${MIGRATION_DUMP_FILE}"
}

import_migrated_database() {
    step "Loading the data into ${DB_NAME} on ${DB_HOST}"

    local client
    client="$(db_client)"
    set_db_conn_args "${DB_HOST}" "${DB_PORT}" "${DB_USER}" "${DB_PASSWORD}"

    # An external destination is not created by this installer, so it has to
    # exist already -- and it has to be empty, or loading into it would write
    # over whatever is there.
    if ! db_host_is_local "${DB_HOST}"; then
        # Reachability and emptiness are separate questions. An empty
        # destination is the normal case for a move onto a managed server, and
        # reading "no tables" as "no database" would refuse every one of them.
        if ! "${client}" "${DB_CONN_ARGS[@]}" "${DB_NAME}" -N -B -e "SELECT 1" >/dev/null 2>&1; then
            die "Cannot reach database ${DB_NAME} on ${DB_HOST} as ${DB_USER}. Create it (CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci), grant access, and check the host, port and password. Nothing has been changed."
        fi
        local existing table_count
        existing="$(db_table_row_counts "${DB_NAME}")"
        table_count="$(printf '%s' "${existing}" | grep -c . || true)"
        if [[ "${table_count}" -gt 0 && "${MIGRATION_OVERWRITE_DEST}" != "1" ]]; then
            warn "${DB_NAME} on ${DB_HOST} already contains ${table_count} tables."
            prompt_yes_no MIGRATION_OVERWRITE_DEST "Overwrite what is already in ${DB_NAME} on ${DB_HOST}?" "0"
            [[ "${MIGRATION_OVERWRITE_DEST}" == "1" ]] \
                || die "Left the destination alone. The copied data is at ${MIGRATION_DUMP_FILE}; nothing else has been changed."
        fi
    fi

    if ! "${client}" "${DB_CONN_ARGS[@]}" --default-character-set=utf8mb4 \
         "${DB_NAME}" < "${MIGRATION_DUMP_FILE}" 2>"${MIGRATION_DUMP_FILE}.err"; then
        error "Loading the data failed:"
        sed 's/^/    /' "${MIGRATION_DUMP_FILE}.err" >&2 || true
        rm -f "${MIGRATION_DUMP_FILE}.err"
        die "The copy is still at ${MIGRATION_DUMP_FILE} and ${SOURCE_DB_NAME} on ${SOURCE_DB_HOST} is untouched, so nothing has been lost."
    fi
    rm -f "${MIGRATION_DUMP_FILE}.err"

    verify_migrated_database
}

# Every table, every row count, both sides. Anything less can miss a table that
# failed to load, and a member list that is quietly short by one is worse than
# one that is obviously empty.
verify_migrated_database() {
    local destination_counts differences
    set_db_conn_args "${DB_HOST}" "${DB_PORT}" "${DB_USER}" "${DB_PASSWORD}"
    destination_counts="$(db_table_row_counts "${DB_NAME}")"

    differences="$(diff <(printf '%s\n' "${MIGRATION_SOURCE_COUNTS}") \
                        <(printf '%s\n' "${destination_counts}") || true)"
    if [[ -n "${differences}" ]]; then
        error "What arrived does not match what was read. Differences (< source, > destination):"
        printf '%s\n' "${differences}" | sed 's/^/    /' >&2
        die "The copy is still at ${MIGRATION_DUMP_FILE} and ${SOURCE_DB_NAME} on ${SOURCE_DB_HOST} is untouched. Put ${ENV_FILE} back to the old database, or investigate, before using this installation."
    fi

    success "Verified: $(printf '%s' "${destination_counts}" | grep -c .) tables, row counts identical on both sides."
    info "The old database at ${SOURCE_DB_HOST} has not been touched. Keep it until you are satisfied, then remove it yourself."
}

initialize_database_schema() {
    step "Initializing database schema"
    run_as_app_user env PYTHONPATH="${INSTALL_DIR}" "${INSTALL_DIR}/.venv/bin/flask" --app aeronautics_members.app:create_app db-init
}

# --- An update whose migration fails undoes itself (docs/update-safety.md) ---
#
# MariaDB commits each table change at once, so a migration that fails part
# way leaves its earlier steps done: once that left the live server with new
# code on disk, the old app running and a half-migrated database. So an update
# that brings a migration stops the portal (nginx shows its maintenance page),
# dumps the database, then migrates -- and if that fails, loads the dump back,
# returns to the rollback point's code and starts it again. An update without
# a migration runs as before, with no downtime beyond the restart.
UNDO_DUMP_FILE=""
UNDO_TABLE_COUNTS=""
# The exit status of an update that failed and undid itself: failed, but
# explained, so the installer that started it adds no diagnostics.
UPDATE_UNDONE_STATUS=3
# 1 from the stop until the app runs again; 2 while the undo itself runs.
UNDO_STATE=0

# Whether the checked-out code brings migrations the database has not had:
# yes, no, or unknown -- which is treated like yes.
schema_migration_pending() {
    local answer
    answer="$(run_as_app_user env PYTHONPATH="${INSTALL_DIR}" "${INSTALL_DIR}/.venv/bin/flask" \
        --app aeronautics_members.app:create_app db-pending 2>/dev/null | tail -n 1 || true)"
    case "${answer}" in
        yes|no) printf '%s\n' "${answer}" ;;
        *) printf 'unknown\n' ;;
    esac
}

# A dump taken with the portal stopped, so nothing written after it is lost
# if it has to be loaded back; and its tables' row counts, to check that.
dump_database_before_migration() {
    step "Backing up the database before the migration"
    local dump_client
    if ! dump_client="$(db_dump_client)"; then
        error "Neither mariadb-dump nor mysqldump is installed."
        return 1
    fi
    mkdir -p "${BACKUP_DIR}"
    chmod 700 "${BACKUP_DIR}" 2>/dev/null || true
    UNDO_DUMP_FILE="${BACKUP_DIR}/${APP_NAME}-db-before-migration-$(date +%Y%m%d%H%M%S).sql"
    set_db_conn_args "${DB_HOST}" "${DB_PORT}" "${DB_USER}" "${DB_PASSWORD}"
    if ! "${dump_client}" "${DB_CONN_ARGS[@]}" --single-transaction --quick --skip-lock-tables \
         --routines --triggers --default-character-set=utf8mb4 "${DB_NAME}" > "${UNDO_DUMP_FILE}"; then
        rm -f "${UNDO_DUMP_FILE}"
        return 1
    fi
    chmod 600 "${UNDO_DUMP_FILE}"
    verify_sql_dump "${UNDO_DUMP_FILE}" || return 1
    UNDO_TABLE_COUNTS="$(db_table_row_counts "${DB_NAME}")"
    [[ -n "${UNDO_TABLE_COUNTS}" ]]
}

# Every table dropped -- a half-run migration leaves tables the dump knows
# nothing of -- then the dump loaded, and checked table by table.
restore_database_before_migration() {
    step "Putting the database back as it was before the migration"
    local client table counts drops=""
    client="$(db_client)"
    set_db_conn_args "${DB_HOST}" "${DB_PORT}" "${DB_USER}" "${DB_PASSWORD}"
    while IFS= read -r table; do
        [[ -n "${table}" ]] && drops+="DROP TABLE IF EXISTS \`${table}\`;"
    done < <("${client}" "${DB_CONN_ARGS[@]}" -N -B -e "
        SELECT table_name FROM information_schema.tables
         WHERE table_schema = '$(sql_escape "${DB_NAME}")' AND table_type = 'BASE TABLE'")
    "${client}" "${DB_CONN_ARGS[@]}" "${DB_NAME}" -e "SET FOREIGN_KEY_CHECKS=0; ${drops} SET FOREIGN_KEY_CHECKS=1;"
    "${client}" "${DB_CONN_ARGS[@]}" --default-character-set=utf8mb4 "${DB_NAME}" < "${UNDO_DUMP_FILE}"
    counts="$(db_table_row_counts "${DB_NAME}")"
    if [[ "${counts}" != "${UNDO_TABLE_COUNTS}" ]]; then
        error "The database does not match the backup after loading it back. Differences (< backup, > now):"
        diff <(printf '%s\n' "${UNDO_TABLE_COUNTS}") <(printf '%s\n' "${counts}") | sed 's/^/    /' >&2 || true
        return 1
    fi
    success "Database back as before the update: $(printf '%s' "${counts}" | grep -c .) tables, row counts identical."
}

# The rollback point's code, its packages and front end, started again. If the
# previous version is this very code -- an update run again after one that
# failed -- there is nothing to go back to, and it is only started again.
undo_update_code() {
    roll_back_installation
    if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
        reload_services
    fi
    UNDO_STATE=0
}

migrate_database_for_update() {
    # The packages and the front end are in; from here a failure is handled
    # by the steps below and on_error's UNDO_STATE, not by putting code back.
    ROLLBACK_POINT_REVISION=""
    local pending
    pending="$(schema_migration_pending)"
    if [[ "${pending}" == "no" ]]; then
        initialize_database_schema
        return 0
    fi
    if [[ "${pending}" == "unknown" ]]; then
        warn "Could not tell whether this update changes the database; treating it as one that does."
    fi

    step "Stopping the portal for the database migration"
    info "Visitors see the maintenance page until the portal starts again, usually within a minute."
    UNDO_STATE=1
    systemctl stop "${SERVICE_NAME}"
    # The jobs as well, not only their timers: one still running after the
    # pause's wait would write past the dump -- lost, or in the way, if it is
    # loaded back. systemctl stop returns once each has ended; a job cut short
    # is taken up again by its next run, as after any restart.
    local job
    for job in "${BACKGROUND_JOBS[@]}"; do
        systemctl stop "${SERVICE_NAME}-${job}.service" 2>/dev/null || true
    done

    # Both in an if, so a failure comes here rather than to on_error.
    if ! dump_database_before_migration; then
        UNDO_STATE=2
        error "The database could not be backed up, so nothing was migrated. Putting the previous version back."
        undo_update_code
        update_undone "The update was undone before migrating: the database could not be backed up first (above). The previous version runs again; nothing in the database was changed."
    fi
    if initialize_database_schema; then
        return 0
    fi

    UNDO_STATE=2
    error "The database migration failed (above). Putting the database and the previous version back."
    restore_database_before_migration
    undo_update_code
    update_undone "The update was undone: its database migration failed, so the database is as it was before the update and the previous version runs again. Fix the migration and update again. The backup taken before the migration: ${UNDO_DUMP_FILE}"
}

update_undone() {
    error "$*"
    exit "${UPDATE_UNDONE_STATUS}"
}

count_admin_accounts() {
    run_as_app_user env PYTHONPATH="${INSTALL_DIR}" "${INSTALL_DIR}/.venv/bin/python" -c "from sqlalchemy import func; from aeronautics_members.app import create_app; from aeronautics_members.db_models import Role, User, db; app=create_app(); ctx=app.app_context(); ctx.push(); print(db.session.scalar(db.select(func.count()).select_from(User).where(User.roles.any(Role.slug.in_(('admin', 'superadmin'))))) or 0); ctx.pop()"
}

ensure_admin_account() {
    local existing_admin_count="0"
    local create_admin_now="0"

    if [[ "${SKIP_ADMIN_ACCOUNT}" == "1" ]]; then
        info "Not creating an admin account: the backup being restored brings its own."
        return
    fi

    existing_admin_count="$(count_admin_accounts 2>/dev/null || printf '0')"
    if [[ ! "${existing_admin_count}" =~ ^[0-9]+$ ]]; then
        existing_admin_count="0"
    fi

    if [[ -n "${ADMIN_EMAIL:-}" ]]; then
        create_admin_now="1"
    elif [[ "${existing_admin_count}" -eq 0 ]]; then
        # Only a new installation offers one: an update never waits on a question.
        if [[ "${NONINTERACTIVE}" == "1" || "${MODE}" == "update" ]]; then
            warn "No admin account exists yet. After install, create one manually with: flask --app aeronautics_members.app:create_app create-admin <email>"
            return
        fi
        prompt_yes_no create_admin_now "Create the initial admin account now?" "1"
    else
        info "Found ${existing_admin_count} existing admin account(s)."
        return
    fi

    if [[ "${create_admin_now}" != "1" ]]; then
        if [[ "${existing_admin_count}" -eq 0 ]]; then
            warn "No admin account was created during install. Create one manually before using the admin UI."
        fi
        return
    fi

    prompt_value ADMIN_EMAIL "Admin email address" "${ADMIN_EMAIL:-}" 0 1

    step "Creating admin account"
    if [[ -n "${ADMIN_PASSWORD:-}" ]]; then
        run_as_app_user env PYTHONPATH="${INSTALL_DIR}" "${INSTALL_DIR}/.venv/bin/flask" --app aeronautics_members.app:create_app create-admin "${ADMIN_EMAIL}" --password "${ADMIN_PASSWORD}"
        ADMIN_PASSWORD=""
    else
        run_as_app_user env PYTHONPATH="${INSTALL_DIR}" "${INSTALL_DIR}/.venv/bin/flask" --app aeronautics_members.app:create_app create-admin "${ADMIN_EMAIL}"
    fi
    success "Admin account is ready for ${ADMIN_EMAIL}"
}

render_service_file() {
    step "Writing systemd service"
    local unit_after="After=network.target"
    local unit_requires=""

    if [[ "${USE_LOCAL_DB}" == "1" ]]; then
        unit_after="After=network.target ${DB_SERVICE_NAME}.service"
        unit_requires="Requires=${DB_SERVICE_NAME}.service"
    fi

    if [[ -n "${REDIS_SERVICE_NAME}" ]]; then
        unit_after="${unit_after} ${REDIS_SERVICE_NAME}.service"
        if [[ -n "${unit_requires}" ]]; then
            unit_requires="${unit_requires}
Requires=${REDIS_SERVICE_NAME}.service"
        else
            unit_requires="Requires=${REDIS_SERVICE_NAME}.service"
        fi
    fi

    cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=Joanneum Aeronautics membership service
${unit_after}
${unit_requires}

[Service]
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
# Threads, so a page waiting on Stripe or the forum holds one thread rather
# than a whole worker: three workers of one request each meant three slow
# forum calls stalled the entire site. Twelve requests at once now, and the
# outside calls themselves time out well inside the 60 seconds.
ExecStart=${INSTALL_DIR}/.venv/bin/gunicorn --workers 3 --threads 4 --timeout 60 --graceful-timeout 30 --keep-alive 75 --bind 127.0.0.1:${APP_PORT} wsgi:application
Restart=always
RestartSec=5
TimeoutStartSec=60
PrivateTmp=true
NoNewPrivileges=true
# Reduce what a compromised process can reach: the filesystem is read-only apart
# from the paths granted below, and the usual escalation surfaces are closed.
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictRealtime=true
LockPersonality=true
# ProtectSystem=strict makes everything read-only, so grant back the two places
# the application legitimately writes: staged forum avatars await approval in
# storage, and the update button leaves its request in the state directory.
ReadWritePaths=${INSTALL_DIR}/storage ${UPDATE_STATE_DIR}

[Install]
WantedBy=multi-user.target
EOF

    chmod 644 "${SERVICE_FILE}"
}

# When the night's steps run, in the portal's time zone (MEMBERSHIP_TIMEZONE,
# Vienna by default) rather than the server's clock, which is usually UTC.
# From 03:15: between 02:00 and 03:00 the clock changes in spring and autumn --
# that hour is missing one night and there twice another --, and 03:15 is
# where an attendance day ends, which the first step closes (services/attendance.py).
night_at() {
    printf '*-*-* %s %s' "$1" "$(night_timezone)"
}

# The portal's time zone, if the system knows it -- a timer naming one it does
# not know would not start at all --, else the server's own clock.
night_timezone() {
    local zone="${MEMBERSHIP_TIMEZONE:-Europe/Vienna}"
    if [[ "${zone}" =~ ^[A-Za-z0-9_+/-]+$ && -f "/usr/share/zoneinfo/${zone}" ]]; then
        printf '%s' "${zone}"
    else
        printf 'UTC'
    fi
}

# A nightly job's command, queued behind the night lock; its own time limit
# counts once it has the lock.
night_queued() {
    local work_seconds="$1"
    shift
    printf '/usr/bin/flock --wait %s %s /usr/bin/timeout %s %s' \
        "${NIGHT_WAIT_SECONDS}" "${NIGHT_LOCK}" "${work_seconds}" "$*"
}

render_billing_reconcile_timer_files() {
    step "Writing billing reconciliation timer"
    local unit_after="After=network.target"
    local unit_requires=""

    if [[ "${USE_LOCAL_DB}" == "1" ]]; then
        unit_after="After=network.target ${DB_SERVICE_NAME}.service"
        unit_requires="Requires=${DB_SERVICE_NAME}.service"
    fi

    cat > "${BILLING_RECONCILE_SERVICE_FILE}" <<EOF
[Unit]
Description=Joanneum Aeronautics billing reconciliation
${unit_after}
${unit_requires}

[Service]
Type=oneshot
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONPATH=${INSTALL_DIR}
ExecStart=$(night_queued 180 "${INSTALL_DIR}/.venv/bin/flask --app aeronautics_members.app:create_app reconcile-billing --lookahead-days 3")
TimeoutStartSec=$(( NIGHT_WAIT_SECONDS + 240 ))
PrivateTmp=true
NoNewPrivileges=true
# Reduce what a compromised process can reach: the filesystem is read-only apart
# from the paths granted below, and the usual escalation surfaces are closed.
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictRealtime=true
LockPersonality=true
# ProtectSystem=strict makes everything read-only, so grant back the two places
# the application legitimately writes: staged forum avatars await approval in
# storage, and the update button leaves its request in the state directory.
ReadWritePaths=${INSTALL_DIR}/storage ${UPDATE_STATE_DIR}
EOF

    cat > "${BILLING_RECONCILE_TIMER_FILE}" <<EOF
[Unit]
Description=Daily Joanneum Aeronautics billing reconciliation

[Timer]
OnCalendar=$(night_at 03:15)
Persistent=true
Unit=${SERVICE_NAME}-billing-reconcile.service

[Install]
WantedBy=timers.target
EOF

    chmod 644 "${BILLING_RECONCILE_SERVICE_FILE}" "${BILLING_RECONCILE_TIMER_FILE}"
}

cert_paths_exist() {
    [[ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" && -f "/etc/letsencrypt/live/${DOMAIN}/privkey.pem" ]]
}

render_notifications_timer_files() {
    step "Writing notification delivery timer"
    local unit_after="After=network.target"
    local unit_requires=""

    if [[ "${USE_LOCAL_DB}" == "1" ]]; then
        unit_after="After=network.target ${DB_SERVICE_NAME}.service"
        unit_requires="Requires=${DB_SERVICE_NAME}.service"
    fi

    cat > "${NOTIFICATIONS_SERVICE_FILE}" <<EOF
[Unit]
Description=Joanneum Aeronautics notification delivery
${unit_after}
${unit_requires}

[Service]
Type=oneshot
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONPATH=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/.venv/bin/flask --app aeronautics_members.app:create_app deliver-notifications
TimeoutStartSec=180
PrivateTmp=true
NoNewPrivileges=true
# Reduce what a compromised process can reach: the filesystem is read-only apart
# from the paths granted below, and the usual escalation surfaces are closed.
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictRealtime=true
LockPersonality=true
# ProtectSystem=strict makes everything read-only, so grant back the two places
# the application legitimately writes: staged forum avatars await approval in
# storage, and the update button leaves its request in the state directory.
ReadWritePaths=${INSTALL_DIR}/storage ${UPDATE_STATE_DIR}
EOF

    cat > "${NOTIFICATIONS_TIMER_FILE}" <<EOF
[Unit]
Description=Joanneum Aeronautics notification delivery timer

# Every two minutes: review emails wait a minute to collect what arrives
# together, and this is what sends them. A run with nothing due does nothing.
[Timer]
OnBootSec=2min
OnActiveSec=2min
OnUnitActiveSec=2min
AccuracySec=30s
Persistent=true
Unit=${SERVICE_NAME}-notifications.service

[Install]
WantedBy=timers.target
EOF

    chmod 644 "${NOTIFICATIONS_SERVICE_FILE}" "${NOTIFICATIONS_TIMER_FILE}"
}

render_cleanup_logs_timer_files() {
    step "Writing log-retention cleanup timer"
    local unit_after="After=network.target"
    local unit_requires=""

    if [[ "${USE_LOCAL_DB}" == "1" ]]; then
        unit_after="After=network.target ${DB_SERVICE_NAME}.service"
        unit_requires="Requires=${DB_SERVICE_NAME}.service"
    fi

    cat > "${CLEANUP_LOGS_SERVICE_FILE}" <<EOF
[Unit]
Description=Joanneum Aeronautics log retention cleanup
${unit_after}
${unit_requires}

[Service]
Type=oneshot
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONPATH=${INSTALL_DIR}
ExecStart=$(night_queued 300 "${INSTALL_DIR}/.venv/bin/flask --app aeronautics_members.app:create_app cleanup-logs")
TimeoutStartSec=$(( NIGHT_WAIT_SECONDS + 360 ))
PrivateTmp=true
NoNewPrivileges=true
# Reduce what a compromised process can reach: the filesystem is read-only apart
# from the paths granted below, and the usual escalation surfaces are closed.
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictRealtime=true
LockPersonality=true
# ProtectSystem=strict makes everything read-only, so grant back the two places
# the application legitimately writes: staged forum avatars await approval in
# storage, and the update button leaves its request in the state directory.
ReadWritePaths=${INSTALL_DIR}/storage ${UPDATE_STATE_DIR}
EOF

    cat > "${CLEANUP_LOGS_TIMER_FILE}" <<EOF
[Unit]
Description=Joanneum Aeronautics log retention cleanup timer

[Timer]
OnCalendar=*-*-01 03:25:00 $(night_timezone)
Persistent=true
Unit=${SERVICE_NAME}-cleanup-logs.service

[Install]
WantedBy=timers.target
EOF

    chmod 644 "${CLEANUP_LOGS_SERVICE_FILE}" "${CLEANUP_LOGS_TIMER_FILE}"
}

render_forum_drift_timer_files() {
    step "Writing forum membership drift timer"
    local unit_after="After=network.target"
    local unit_requires=""
    if [[ "${USE_LOCAL_DB}" == "1" && -n "${DB_SERVICE_NAME}" ]]; then
        unit_after="After=network.target ${DB_SERVICE_NAME}.service"
        unit_requires="Requires=${DB_SERVICE_NAME}.service"
    fi

    # Everything else that ends a membership is an event somebody causes, and
    # each of those syncs the forum where it happens. A membership ending
    # because its last day has passed is not an event: nobody does anything, so
    # nothing tells the forum, and the person goes on reading the archive until
    # somebody happens to touch their record. This is what asks.
    cat > "${FORUM_DRIFT_SERVICE_FILE}" <<EOF
[Unit]
Description=Joanneum Aeronautics forum membership drift sync
${unit_after}
${unit_requires}

[Service]
Type=oneshot
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONPATH=${INSTALL_DIR}
ExecStart=$(night_queued 900 "${INSTALL_DIR}/.venv/bin/flask --app aeronautics_members.app:create_app sync-forum-members --only-changed")
TimeoutStartSec=$(( NIGHT_WAIT_SECONDS + 960 ))
PrivateTmp=true
NoNewPrivileges=true
# Reduce what a compromised process can reach: the filesystem is read-only apart
# from the paths granted below, and the usual escalation surfaces are closed.
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictRealtime=true
LockPersonality=true
ReadWritePaths=${INSTALL_DIR}/storage ${UPDATE_STATE_DIR}
EOF

    # After the billing reconciliation -- and while that still runs, waiting
    # for it (the night lock) --, so that a membership Stripe has just been
    # found to have lapsed is already recorded here before this asks what the
    # forum should be told.
    cat > "${FORUM_DRIFT_TIMER_FILE}" <<EOF
[Unit]
Description=Joanneum Aeronautics forum membership drift timer

[Timer]
OnCalendar=$(night_at 03:20)
Persistent=true
Unit=${SERVICE_NAME}-forum-drift.service

[Install]
WantedBy=timers.target
EOF

    chmod 644 "${FORUM_DRIFT_SERVICE_FILE}" "${FORUM_DRIFT_TIMER_FILE}"
}

render_external_work_timer_files() {
    step "Writing external work (forum sync) worker timer"
    local unit_after="After=network.target"
    local unit_requires=""

    if [[ "${USE_LOCAL_DB}" == "1" ]]; then
        unit_after="After=network.target ${DB_SERVICE_NAME}.service"
        unit_requires="Requires=${DB_SERVICE_NAME}.service"
    fi

    cat > "${EXTERNAL_WORK_SERVICE_FILE}" <<EOF
[Unit]
Description=Joanneum Aeronautics external work worker (Discourse sync)
${unit_after}
${unit_requires}

[Service]
Type=oneshot
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONPATH=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/.venv/bin/flask --app aeronautics_members.app:create_app process-external-work
TimeoutStartSec=600
PrivateTmp=true
NoNewPrivileges=true
# Reduce what a compromised process can reach: the filesystem is read-only apart
# from the paths granted below, and the usual escalation surfaces are closed.
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictRealtime=true
LockPersonality=true
# ProtectSystem=strict makes everything read-only, so grant back the two places
# the application legitimately writes: staged forum avatars await approval in
# storage, and the update button leaves its request in the state directory.
ReadWritePaths=${INSTALL_DIR}/storage ${UPDATE_STATE_DIR}
EOF

    cat > "${EXTERNAL_WORK_TIMER_FILE}" <<EOF
[Unit]
Description=Joanneum Aeronautics external work worker timer

[Timer]
# Queued forum syncs should land quickly, so members do not wait for access.
# OnActiveSec is what starts it on a machine that is already up: OnBootSec has
# long passed there, and OnUnitActiveSec counts from a run that a freshly
# installed service has never had -- so without it the timer sat "elapsed",
# with no next trigger, for a day after a reinstall. The same holds for the
# notification and update-runner timers.
OnBootSec=2min
OnActiveSec=2min
OnUnitActiveSec=2min
AccuracySec=30s
Persistent=true
Unit=${SERVICE_NAME}-external-work.service

[Install]
WantedBy=timers.target
EOF

    chmod 644 "${EXTERNAL_WORK_SERVICE_FILE}" "${EXTERNAL_WORK_TIMER_FILE}"
}

# Updates installed by themselves at night (docs/automatic-updates-plan.md,
# part 2). The job only asks, the way the Updates page's button does -- it
# writes the request the root runner below acts on --, and only when the
# switch on that page is on and CI passed for the newest version.
render_automatic_updates_timer_files() {
    step "Writing automatic updates timer"
    local unit_after="After=network-online.target"
    local unit_requires=""

    if [[ "${USE_LOCAL_DB}" == "1" ]]; then
        unit_after="After=network-online.target ${DB_SERVICE_NAME}.service"
        unit_requires="Requires=${DB_SERVICE_NAME}.service"
    fi

    cat > "${AUTOMATIC_UPDATES_SERVICE_FILE}" <<EOF
[Unit]
Description=Joanneum Aeronautics automatic updates (asks for the newest version)
${unit_after}
Wants=network-online.target
${unit_requires}

[Service]
Type=oneshot
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONPATH=${INSTALL_DIR}
ExecStart=$(night_queued 180 "${INSTALL_DIR}/.venv/bin/flask --app aeronautics_members.app:create_app automatic-updates run")
TimeoutStartSec=$(( NIGHT_WAIT_SECONDS + 240 ))
PrivateTmp=true
NoNewPrivileges=true
# Reduce what a compromised process can reach: the filesystem is read-only apart
# from the paths granted below, and the usual escalation surfaces are closed.
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictRealtime=true
LockPersonality=true
# ProtectSystem=strict makes everything read-only, so grant back the two places
# the application legitimately writes: staged forum avatars await approval in
# storage, and the update request goes into the state directory.
ReadWritePaths=${INSTALL_DIR}/storage ${UPDATE_STATE_DIR}
EOF

    # Not Persistent: a night missed -- the server was off -- is not made up
    # in the day, while people use the portal. The next night comes soon.
    cat > "${AUTOMATIC_UPDATES_TIMER_FILE}" <<EOF
[Unit]
Description=Joanneum Aeronautics automatic updates timer

[Timer]
OnCalendar=$(night_at 03:30)
Persistent=false
Unit=${SERVICE_NAME}-automatic-updates.service

[Install]
WantedBy=timers.target
EOF

    chmod 644 "${AUTOMATIC_UPDATES_SERVICE_FILE}" "${AUTOMATIC_UPDATES_TIMER_FILE}"
}

# A new installation installs updates by itself; one that runs already keeps
# them off until an administrator switches them on (Settings > Updates).
switch_on_automatic_updates() {
    step "Switching on automatic updates (a new installation)"
    run_as_app_user env PYTHONPATH="${INSTALL_DIR}" "${INSTALL_DIR}/.venv/bin/flask" \
        --app aeronautics_members.app:create_app automatic-updates on \
        || warn "Could not switch automatic updates on; do it under Settings > Updates."
}

# Ubuntu's own security updates (docs/automatic-updates-plan.md, part 3):
# unattended-upgrades installs them; here it is made sure it is there and
# switched on, and they become a step of the night: at 03:40, queued behind
# the night lock (Ubuntu's own time is a random one in the morning), and at
# 04:00 the server restarts if one of them needs that -- a new kernel, say.
# The restart is the portal's own step, not unattended-upgrades', so that it
# too waits for whatever still runs: never in the middle of an update.
# A file of the portal's own, after Ubuntu's (20auto-upgrades,
# 50unattended-upgrades), so what is set there stays and only this is added.
configure_unattended_upgrades() {
    if [[ "${PACKAGE_MANAGER}" != "apt" ]]; then
        info "Automatic system updates are set up for apt only; on this system, set them up yourself (dnf-automatic)."
        return 0
    fi
    step "Switching on automatic security updates of the system"
    install_packages unattended-upgrades
    cat > "${UNATTENDED_UPGRADES_FILE}" <<EOF
// Written by the portal's installer (docs/automatic-updates-plan.md, part 3);
// removed again by its uninstall. Ubuntu's security updates every day, and a
// restart at 04:00 when one needs it.
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
// The restart is the portal's (${SERVICE_NAME}-restart-if-needed): after the
// night's other steps, never in the middle of one.
Unattended-Upgrade::Automatic-Reboot "false";
EOF
    chmod 644 "${UNATTENDED_UPGRADES_FILE}"

    install -d -m 0755 "$(dirname "${APT_UPGRADE_TIMER_DROPIN}")" "$(dirname "${APT_UPGRADE_SERVICE_DROPIN}")"
    cat > "${APT_UPGRADE_TIMER_DROPIN}" <<EOF
# Written by the portal's installer: Ubuntu's updates as a step of the night.
[Timer]
OnCalendar=
OnCalendar=$(night_at 03:40)
RandomizedDelaySec=0
EOF
    cat > "${APT_UPGRADE_SERVICE_DROPIN}" <<EOF
# Written by the portal's installer: queued behind the night lock.
[Service]
ExecStart=
# The package lists fetched first, here: Ubuntu's own refresh (apt-daily) runs
# at a random time and at most once a day, so the lists could be a day old.
ExecStart=/usr/bin/flock --wait ${NIGHT_WAIT_SECONDS} ${NIGHT_LOCK} /bin/sh -c '/usr/bin/apt-get -q update || true; exec /usr/lib/apt/apt.systemd.daily install'
EOF

    cat > "${RESTART_IF_NEEDED_SERVICE_FILE}" <<EOF
[Unit]
Description=Joanneum Aeronautics: restart the server if an update needs it

[Service]
Type=oneshot
# Root: it restarts the machine. Only when Ubuntu says an update needs that,
# and only once nothing else of the night runs.
ExecStart=/usr/bin/flock --wait ${NIGHT_WAIT_SECONDS} ${NIGHT_LOCK} /bin/sh -c 'if [ -f /run/reboot-required ]; then echo "An update needs a restart: restarting."; systemctl reboot; else echo "No restart needed."; fi'
TimeoutStartSec=$(( NIGHT_WAIT_SECONDS + 120 ))
EOF
    cat > "${RESTART_IF_NEEDED_TIMER_FILE}" <<EOF
[Unit]
Description=Joanneum Aeronautics: restart the server if an update needs it (timer)

[Timer]
OnCalendar=$(night_at 04:00)
Persistent=false
Unit=${SERVICE_NAME}-restart-if-needed.service

[Install]
WantedBy=timers.target
EOF
    chmod 644 "${APT_UPGRADE_TIMER_DROPIN}" "${APT_UPGRADE_SERVICE_DROPIN}" \
        "${RESTART_IF_NEEDED_SERVICE_FILE}" "${RESTART_IF_NEEDED_TIMER_FILE}"
    systemctl daemon-reload
    systemctl enable --now unattended-upgrades 2>/dev/null || true
    systemctl enable "${SERVICE_NAME}-restart-if-needed.timer"
    systemctl restart "${SERVICE_NAME}-restart-if-needed.timer" apt-daily-upgrade.timer 2>/dev/null || true
}

render_update_runner() {
    step "Installing admin-page update runner"

    # The web application runs unprivileged and must never be able to run the
    # update itself: a sudoers rule for it would turn any compromise of the web
    # process into root in one step. Instead the app writes a request into this
    # directory, and the root-owned runner below acts on it. The request carries
    # no instructions -- what gets deployed comes from this installer's own
    # state -- so the unprivileged side can ask for an update but cannot choose
    # what an update is.
    install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0750 "${UPDATE_STATE_DIR}"
    # What the runner itself writes goes here, where only root may write: in
    # the app's folder, the app could put a symbolic link where the runner's
    # log goes, and root would write through it into any file. The app's
    # group reads it, for the progress on the Updates page.
    if [[ -L "${UPDATE_RUNNER_DIR}" || ( -e "${UPDATE_RUNNER_DIR}" && ! -d "${UPDATE_RUNNER_DIR}" ) ]]; then
        rm -f -- "${UPDATE_RUNNER_DIR}"
    fi
    install -d -o root -g "${APP_GROUP}" -m 0750 "${UPDATE_RUNNER_DIR}"
    ensure_night_lock
    install -d -m 0755 "$(dirname "${UPDATE_RUNNER_SCRIPT}")"

    # Write via a temporary file and rename: bash reads a script incrementally,
    # so truncating this file while an update is running would corrupt the very
    # process performing it.
    local runner_tmp="${UPDATE_RUNNER_SCRIPT}.tmp"
    if [[ -f "${INSTALL_DIR}/deploy/update-runner.sh" ]]; then
        cp -f "${INSTALL_DIR}/deploy/update-runner.sh" "${runner_tmp}"
    else
        warn "deploy/update-runner.sh not found; the admin update button will be unavailable."
        return
    fi
    chmod 750 "${runner_tmp}"
    mv -f "${runner_tmp}" "${UPDATE_RUNNER_SCRIPT}"

    cat > "${UPDATE_RUNNER_SERVICE_FILE}" <<EOF
[Unit]
Description=Joanneum Aeronautics admin-requested update runner
After=network.target

[Service]
Type=oneshot
# Runs as root by design: installing an update needs systemd, nginx and
# packages. It acts only on a request file written by the web application.
Environment=UPDATE_STATE_DIR=${UPDATE_STATE_DIR}
Environment=UPDATE_RUNNER_DIR=${UPDATE_RUNNER_DIR}
Environment=UPDATE_COMMAND=${UPDATE_COMMAND_PATH}
Environment=INSTALL_DIR=${INSTALL_DIR}
Environment=LOG_GROUP=${APP_GROUP}
Environment=NIGHT_LOCK=${NIGHT_LOCK}
Environment=NIGHT_WAIT_SECONDS=${NIGHT_WAIT_SECONDS}
ExecStart=${UPDATE_RUNNER_SCRIPT}
# Above the runner's own limit (UPDATE_TIMEOUT, 45 minutes, plus a minute for
# the update to stop): the runner is what records a hung update as failed. At
# 1800 systemd killed it first, mid-run, and the page showed "running" for good.
# Plus the wait for the night's jobs (NIGHT_WAIT_SECONDS), which it queues behind.
TimeoutStartSec=$(( 3300 + NIGHT_WAIT_SECONDS ))
EOF

    # A path unit starts the runner the moment the request file appears, so an
    # administrator does not wait out a polling interval.
    cat > "${UPDATE_RUNNER_PATH_FILE}" <<EOF
[Unit]
Description=Joanneum Aeronautics update request watcher (immediate)

[Path]
PathExists=${UPDATE_STATE_DIR}/request.json
Unit=${SERVICE_NAME}-update-runner.service

[Install]
WantedBy=paths.target
EOF

    # The timer stays as a safety net: it recovers a request written while the
    # path unit was not running, which the path unit alone would never notice.
    cat > "${UPDATE_RUNNER_TIMER_FILE}" <<EOF
[Unit]
Description=Joanneum Aeronautics update request watcher (fallback poll)

[Timer]
OnBootSec=2min
OnActiveSec=2min
OnUnitActiveSec=5min
AccuracySec=30s
Unit=${SERVICE_NAME}-update-runner.service

[Install]
WantedBy=timers.target
EOF

    chmod 644 "${UPDATE_RUNNER_SERVICE_FILE}" "${UPDATE_RUNNER_TIMER_FILE}" "${UPDATE_RUNNER_PATH_FILE}"
}

render_update_command() {
    step "Installing 'update' convenience command"

    # Do not clobber a pre-existing 'update' binary that we did not create.
    if [[ -e "${UPDATE_COMMAND_PATH}" ]] && ! grep -q "jaeronautics-update-command" "${UPDATE_COMMAND_PATH}" 2>/dev/null; then
        warn "Skipping 'update' command: ${UPDATE_COMMAND_PATH} already exists and was not created by this installer."
        return
    fi

    cat > "${UPDATE_COMMAND_PATH}" <<EOF
#!/usr/bin/env bash
# jaeronautics-update-command
# Convenience command generated by ${APP_NAME} install.sh.
# Type 'update' and press Enter to update the app (re-runs the installer in update mode).
set -euo pipefail
INSTALLER="${INSTALL_DIR}/install.sh"
if [[ ! -f "\${INSTALLER}" ]]; then
    echo "Installer not found at \${INSTALLER}. Is ${APP_NAME} still installed?" >&2
    exit 1
fi
# "update --rollback" (or "rollback") returns to the revision that was running
# before the last update.
MODE_ARGS=(--mode update)
if [[ "\${1:-}" == "--rollback" || "\$(basename "\$0")" == "rollback" ]]; then
    MODE_ARGS=(--rollback)
    [[ "\${1:-}" == "--rollback" ]] && shift
fi
if [[ \${EUID} -ne 0 ]]; then
    exec sudo bash "\${INSTALLER}" "\${MODE_ARGS[@]}" "\$@"
fi
exec bash "\${INSTALLER}" "\${MODE_ARGS[@]}" "\$@"
EOF

    chmod 755 "${UPDATE_COMMAND_PATH}"
    # A 'rollback' alias, so going back is as easy to remember as going forward.
    ln -sf "${UPDATE_COMMAND_PATH}" "$(dirname "${UPDATE_COMMAND_PATH}")/rollback"
    success "Run 'update' any time to update ${APP_NAME}, or 'rollback' to return to the previous version."
}

# Only one nginx configuration may serve the portal. nginx reads conf.d/ before
# sites-enabled/, so an old conf.d/<service>.conf from an earlier installation
# answers for the domain while every update writes the real one to
# sites-available/ -- where nginx ignores it. That happened on the live site:
# the old file set a fixed Content-Security-Policy, two policies arrived, and
# the stricter one blocked the app's styles. A file of ours in the other place
# is moved into the backup folder; any other file naming the domain is only
# reported, since it is not ours to move.
retire_other_nginx_configs() {
    local nginx_dir="${NGINX_DIR:-/etc/nginx}"
    local ours="${NGINX_CONF_PATH}"
    local enabled="${NGINX_ENABLED_PATH:-}"
    local candidate stamp moved_to
    local -a others=()

    if [[ "${ours}" == */sites-available/* ]]; then
        others=("${nginx_dir}/conf.d/${SERVICE_NAME}.conf")
    else
        others=("${nginx_dir}/sites-enabled/${SERVICE_NAME}.conf" "${nginx_dir}/sites-available/${SERVICE_NAME}.conf")
    fi

    stamp="$(date +%Y%m%d%H%M%S)"
    for candidate in "${others[@]}"; do
        [[ -e "${candidate}" || -L "${candidate}" ]] || continue
        [[ "${candidate}" == "${ours}" || "${candidate}" == "${enabled}" ]] && continue
        mkdir -p "${BACKUP_DIR}"
        moved_to="${BACKUP_DIR}/${APP_NAME}-nginx-other-${stamp}-$(basename "$(dirname "${candidate}")")-$(basename "${candidate}")"
        mv "${candidate}" "${moved_to}"
        warn "A second nginx configuration for the portal was in ${candidate}; moved to ${moved_to}. Only ${ours} serves it now."
    done

    [[ -n "${DOMAIN:-}" ]] || return 0
    local file
    for file in "${nginx_dir}"/conf.d/*.conf "${nginx_dir}"/sites-enabled/*; do
        [[ -f "${file}" ]] || continue
        [[ "${file}" == "${ours}" || "${file}" == "${enabled}" ]] && continue
        [[ "$(readlink -f "${file}")" == "$(readlink -f "${ours}" 2>/dev/null || echo "${ours}")" ]] && continue
        if grep -Eq "server_name[^;]*[[:space:]]${DOMAIN//./\\.}([[:space:]]|;)" "${file}"; then
            warn "${file} also serves ${DOMAIN}. nginx may answer from it instead of the portal's own configuration; remove it unless it is meant to be there."
        fi
    done
}

render_nginx_config() {
    step "Writing nginx configuration"
    retire_other_nginx_configs
    local static_dir="${INSTALL_DIR}/aeronautics_members/static"
    # One connection to gunicorn per request used to be opened and closed. On
    # the Azure portal VM that filled the kernel's connection-tracking table
    # (7,168 entries on 1 GiB) after half a minute of an intake-sized load, and
    # from then on new connections were dropped without a word: every request
    # hung, the CPU sat idle, nothing was logged but "nf_conntrack: table full"
    # in dmesg. Kept-alive connections are a handful, reused. nginx closes an
    # idle one after 60 s, before gunicorn's --keep-alive 75 would, so nginx
    # never sends a request down a connection gunicorn is closing.
    local upstream_name="${SERVICE_NAME//[^A-Za-z0-9_]/_}_app"
    local upstream_block
    upstream_block="upstream ${upstream_name} {
    server 127.0.0.1:${APP_PORT};
    keepalive 32;
    keepalive_timeout 60s;
}"
    # The devices ask every 2 seconds whether anything changed; "nothing" (304)
    # is not logged -- 43,000 lines a day per device that say nothing. A sign
    # of life that brought a change, an error, every other request: logged as
    # before. As on the device's page in the portal (docs/devices-plan.md).
    local log_flag="${SERVICE_NAME//[^A-Za-z0-9_]/_}_logged"
    local log_block
    log_block="map \"\$request_method \$uri \$status\" \$${log_flag} {
    \"GET /api/v1/device 304\" 0;
    default 1;
}"
    # No Content-Security-Policy here: the portal sets it on every answer itself,
    # with a fresh nonce each time (aeronautics_members/content_security.py). A
    # second, fixed policy from nginx would apply as well and block what it allows.
    mkdir -p "$(dirname "${NGINX_CONF_PATH}")"

    if [[ "${ENABLE_SSL}" == "1" ]] && cert_paths_exist; then
        cat > "${NGINX_CONF_PATH}" <<EOF
${upstream_block}

${log_block}

server {
    listen 80;
    server_name ${DOMAIN};
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name ${DOMAIN};

    ssl_certificate /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;
    ssl_session_timeout 1d;
    ssl_session_cache shared:SSL:10m;
    ssl_protocols TLSv1.2 TLSv1.3;

    # Tell browsers to use HTTPS for this host from now on. Sent from the
    # port-80 block too, because TLS terminates at the Cloudflare Tunnel and the
    # browser still receives this over HTTPS; a browser ignores it on plain HTTP.
    # No "preload" -- that is hard to reverse and belongs to a deliberate choice.
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    real_ip_header CF-Connecting-IP;
    real_ip_recursive on;
    set_real_ip_from 127.0.0.1;
    set_real_ip_from ::1;
    client_max_body_size 20m;
    access_log /var/log/nginx/access.log combined if=\$${log_flag};

    # While the portal itself does not answer -- restarting during an update,
    # or down -- nginx shows a page of its own instead of "Bad gateway". As
    # 503, so browsers and Stripe know to come back; a request to the portal
    # that it answers itself, even with an error, is passed on untouched.
    error_page 502 503 504 =503 /maintenance.html;
    location = /maintenance.html {
        root ${static_dir};
        internal;
    }

    location /static/ {
        alias ${static_dir}/;
        expires 30d;
        access_log off;
    }

    location / {
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        # Kept-alive connections to gunicorn (see the upstream above).
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_pass http://${upstream_name};
        proxy_read_timeout 120s;
    }
}
EOF
    else
        cat > "${NGINX_CONF_PATH}" <<EOF
${upstream_block}

${log_block}

server {
    listen 80;
    server_name ${DOMAIN};

    # Tell browsers to use HTTPS for this host from now on. Sent from the
    # port-80 block too, because TLS terminates at the Cloudflare Tunnel and the
    # browser still receives this over HTTPS; a browser ignores it on plain HTTP.
    # No "preload" -- that is hard to reverse and belongs to a deliberate choice.
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    real_ip_header CF-Connecting-IP;
    real_ip_recursive on;
    set_real_ip_from 127.0.0.1;
    set_real_ip_from ::1;
    client_max_body_size 20m;
    access_log /var/log/nginx/access.log combined if=\$${log_flag};

    # While the portal itself does not answer -- restarting during an update,
    # or down -- nginx shows a page of its own instead of "Bad gateway". As
    # 503, so browsers and Stripe know to come back; a request to the portal
    # that it answers itself, even with an error, is passed on untouched.
    error_page 502 503 504 =503 /maintenance.html;
    location = /maintenance.html {
        root ${static_dir};
        internal;
    }

    location /static/ {
        alias ${static_dir}/;
        expires 30d;
        access_log off;
    }

    location / {
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        # Kept-alive connections to gunicorn (see the upstream above).
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_pass http://${upstream_name};
        proxy_read_timeout 120s;
    }
}
EOF
    fi

    chmod 644 "${NGINX_CONF_PATH}"

    if [[ -n "${NGINX_ENABLED_PATH}" ]]; then
        ln -sfn "${NGINX_CONF_PATH}" "${NGINX_ENABLED_PATH}"
        if [[ -L /etc/nginx/sites-enabled/default ]]; then
            rm -f /etc/nginx/sites-enabled/default
        fi
    fi
}

write_cloudflare_tunnel_files() {
    if [[ "${USE_CLOUDFLARE_TUNNEL}" != "1" ]]; then
        return
    fi

    step "Writing Cloudflare Tunnel guidance files"
    local cloudflare_dir="${INSTALL_DIR}/cloudflare"
    local origin_scheme="http"
    local origin_port="80"
    mkdir -p "${cloudflare_dir}"

    if [[ "${ENABLE_SSL}" == "1" ]]; then
        origin_scheme="https"
        origin_port="443"
    fi

    cat > "${cloudflare_dir}/cloudflared-config.yml.example" <<EOF
tunnel: REPLACE_WITH_YOUR_TUNNEL_ID
credentials-file: /etc/cloudflared/REPLACE_WITH_YOUR_TUNNEL_ID.json

ingress:
  - hostname: ${DOMAIN}
    service: ${origin_scheme}://${CLOUDFLARE_ORIGIN_HOST}
EOF

    if [[ "${ENABLE_SSL}" == "1" ]]; then
        cat >> "${cloudflare_dir}/cloudflared-config.yml.example" <<'EOF'
    originRequest:
      noTLSVerify: true
EOF
    fi

    cat >> "${cloudflare_dir}/cloudflared-config.yml.example" <<EOF
  - service: http_status:404
EOF

    cat > "${cloudflare_dir}/README.txt" <<EOF
Cloudflare Tunnel setup for ${APP_NAME}

Recommended public hostname:
  ${DOMAIN}

Recommended Cloudflare origin:
  ${origin_scheme}://${CLOUDFLARE_ORIGIN_HOST}

Origin host/IP and port:
  ${CLOUDFLARE_ORIGIN_HOST}:${origin_port} (nginx)

Application upstream:
  127.0.0.1:${APP_PORT} (gunicorn)

Health endpoints:
  Direct app: http://127.0.0.1:${APP_PORT}/__health
  Through nginx: ${origin_scheme}://${DOMAIN}/__health
  Public URL: https://${DOMAIN}/__health

Suggested next steps:
  1. Install cloudflared on the server.
  2. Run: cloudflared tunnel login
  3. Run: cloudflared tunnel create ${APP_NAME}
  4. Copy cloudflared-config.yml.example to /etc/cloudflared/config.yml
  5. Replace the tunnel ID and credentials path in that config.
  6. In Cloudflare DNS, route ${DOMAIN} to the tunnel.
  7. Start the tunnel:
       cloudflared service install
       systemctl enable --now cloudflared
EOF

    if [[ "${ENABLE_SSL}" == "1" ]]; then
        cat >> "${cloudflare_dir}/README.txt" <<'EOF'

Origin HTTPS note:
  This install uses nginx on port 443 and redirects port 80 to HTTPS.
  The example cloudflared config therefore includes `noTLSVerify: true`
  for the local origin. If you keep origin HTTPS enabled, leave that in place
  unless you replace it with your own validated local certificate strategy.
EOF
    fi

    chown -R "${APP_USER}:${APP_GROUP}" "${cloudflare_dir}"
}

print_stripe_webhook_help() {
    local webhook_scheme="https"
    local webhook_url=""

    if [[ -z "${DOMAIN:-}" || "${DOMAIN}" == "_" ]]; then
        webhook_url="http://SERVER_IP_OR_HOST/stripe-webhook"
    else
        if [[ "${ENABLE_SSL}" != "1" && "${USE_CLOUDFLARE_TUNNEL}" != "1" ]]; then
            webhook_scheme="http"
        fi
        webhook_url="${webhook_scheme}://${DOMAIN}/stripe-webhook"
    fi

    printf '
%b%s%b
' "${COLOR_BOLD}" "Stripe Webhook" "${COLOR_RESET}"
    printf '  Endpoint URL: %s
' "${webhook_url}"
    printf '  Expected response: 200 Success
'
    printf '  Note: A 405 Method Not Allowed from Stripe usually means the webhook URL is wrong.

'
}

print_cloudflare_tunnel_help() {
    if [[ "${USE_CLOUDFLARE_TUNNEL}" != "1" ]]; then
        return
    fi

    local origin_scheme="http"
    local origin_port="80"
    local local_public_url="http://${DOMAIN}/__health"
    if [[ "${ENABLE_SSL}" == "1" ]]; then
        origin_scheme="https"
        origin_port="443"
        local_public_url="https://${DOMAIN}/__health"
    fi

    printf '\n%b%s%b\n' "${COLOR_BOLD}" "Cloudflare Tunnel" "${COLOR_RESET}"
    printf '  Public hostname: %s\n' "${DOMAIN}"
    printf '  Recommended origin: %s://%s\n' "${origin_scheme}" "${CLOUDFLARE_ORIGIN_HOST}"
    printf '  Origin host/IP and port: %s:%s (nginx)\n' "${CLOUDFLARE_ORIGIN_HOST}" "${origin_port}"
    printf '  App upstream: 127.0.0.1:%s (gunicorn)\n' "${APP_PORT}"
    printf '  Direct app health: http://127.0.0.1:%s/__health\n' "${APP_PORT}"
    printf '  Local nginx health: %s\n' "${local_public_url}"
    printf '  Public health target: https://%s/__health\n' "${DOMAIN}"
    printf '  Example config: %s\n' "${INSTALL_DIR}/cloudflare/cloudflared-config.yml.example"
    printf '  Notes: %s\n' "${INSTALL_DIR}/cloudflare/README.txt"
    printf '\n'
}

check_health_endpoint() {
    local url="$1"
    shift || true
    local response=""

    if ! response="$(curl -fsS -L --max-time 20 "$@" "${url}")"; then
        # Retry without -f so the body and status of the failure are visible;
        # "it returned 502" is far more useful than "the check failed".
        warn "Health check failed for ${url}; response was:"
        curl -sS -L --max-time 20 -o - -w '\nHTTP %{http_code}\n' "$@" "${url}" 2>&1 || true
        return 1
    fi

    python3 - "${response}" <<'PY'
import json
import sys

data = json.loads(sys.argv[1])
if data.get("status") != "ok":
    raise SystemExit(1)
PY
}

configure_selinux() {
    if command_exists getenforce && [[ "$(getenforce)" == "Enforcing" ]] && command_exists setsebool; then
        setsebool -P httpd_can_network_connect 1 || true
    fi
}

configure_firewall() {
    if command_exists ufw && ufw status 2>/dev/null | grep -q "Status: active"; then
        ufw allow 'Nginx Full' || true
    elif command_exists firewall-cmd && firewall-cmd --state >/dev/null 2>&1; then
        firewall-cmd --permanent --add-service=http || true
        firewall-cmd --permanent --add-service=https || true
        firewall-cmd --reload || true
    fi
}

obtain_ssl_certificate() {
    if [[ "${ENABLE_SSL}" != "1" ]]; then
        return
    fi

    if [[ "${DOMAIN}" == "_" ]]; then
        warn "SSL cannot be enabled with a catch-all domain. Continuing with HTTP only."
        ENABLE_SSL="0"
        return
    fi

    mapfile -t certbot_pkg_list < <(certbot_packages)
    install_packages "${certbot_pkg_list[@]}"
    systemctl enable --now nginx

    if cert_paths_exist; then
        info "An existing certificate for ${DOMAIN} was found."
        return
    fi

    step "Requesting Let's Encrypt certificate"
    if ! certbot certonly --nginx --non-interactive --agree-tos --email "${SSL_EMAIL}" -d "${DOMAIN}" --keep-until-expiring; then
        warn "Automatic HTTPS setup failed. Leaving the deployment on HTTP."
        ENABLE_SSL="0"
    fi
}

configure_connection_tracking() {
    # Only where the kernel tracks connections at all (a firewall loaded the
    # module). Its default size follows memory -- 7,168 entries on a 1 GiB VM,
    # which an intake evening can fill -- so it is set here, loaded early at
    # boot so the setting has something to apply to, and applied now.
    if [[ ! -e /proc/sys/net/netfilter/nf_conntrack_max ]]; then
        return
    fi
    step "Sizing the connection-tracking table"
    # At least 65,536; a server sized larger already keeps its own.
    local size current
    size=65536
    current="$(cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null || printf '0')"
    if [[ "${current}" =~ ^[0-9]+$ ]] && (( current > size )); then
        size="${current}"
    fi
    printf '%s\n' "nf_conntrack" > "/etc/modules-load.d/${SERVICE_NAME}-conntrack.conf"
    cat > "/etc/sysctl.d/60-${SERVICE_NAME}-conntrack.conf" <<EOF
# Written by the ${APP_NAME} installer. See configure_connection_tracking.
net.netfilter.nf_conntrack_max = ${size}
net.netfilter.nf_conntrack_tcp_timeout_time_wait = 30
EOF
    sysctl -p "/etc/sysctl.d/60-${SERVICE_NAME}-conntrack.conf" >/dev/null \
        || warn "Could not size the connection-tracking table; see /etc/sysctl.d/60-${SERVICE_NAME}-conntrack.conf"
}

reload_services() {
    step "Reloading system services"
    systemctl daemon-reload
    # enable --now starts a stopped unit but does NOT restart an already-running
    # one, so on an update gunicorn would keep executing the previous code while
    # serving the newly deployed templates. Enable, then restart, to guarantee
    # the app runs the freshly deployed code.
    systemctl enable "${SERVICE_NAME}"
    systemctl restart "${SERVICE_NAME}"
    # Running again: no longer stopped for a migration (migrate_database_for_update).
    if [[ "${UNDO_STATE:-0}" == "1" ]]; then
        UNDO_STATE=0
    fi
    # Timers restarted for the same reason, and one more: a timer stuck
    # "elapsed" with no next trigger stays stuck under enable --now, because it
    # counts as running. Restarting re-arms it from its new definition.
    local timer
    for timer in billing-reconcile notifications cleanup-logs forum-drift \
                 external-work automatic-updates update-runner; do
        systemctl enable "${SERVICE_NAME}-${timer}.timer"
        systemctl restart "${SERVICE_NAME}-${timer}.timer"
    done
    BACKGROUND_JOBS_PAUSED=0
    systemctl enable --now "${SERVICE_NAME}-update-runner.path"
    nginx -t
    systemctl reload nginx
}

verify_installation() {
    step "Verifying deployment"
    if [[ "${USE_LOCAL_DB}" == "1" ]]; then
        systemctl is-active --quiet "${DB_SERVICE_NAME}"
    fi
    if [[ -n "${REDIS_SERVICE_NAME}" ]]; then
        systemctl is-active --quiet "${REDIS_SERVICE_NAME}"
    fi
    systemctl is-active --quiet nginx
    systemctl is-active --quiet "${SERVICE_NAME}"
    systemctl is-active --quiet "${SERVICE_NAME}-billing-reconcile.timer"
    systemctl is-active --quiet "${SERVICE_NAME}-notifications.timer"
    systemctl is-active --quiet "${SERVICE_NAME}-cleanup-logs.timer"
    systemctl is-active --quiet "${SERVICE_NAME}-forum-drift.timer"
    systemctl is-active --quiet "${SERVICE_NAME}-external-work.timer"
    systemctl is-active --quiet "${SERVICE_NAME}-automatic-updates.timer"
    systemctl is-active --quiet "${SERVICE_NAME}-update-runner.timer"
    check_health_endpoint "http://127.0.0.1:${APP_PORT}/__health"
    success "The application is responding on 127.0.0.1:${APP_PORT}"

    if [[ "${USE_CLOUDFLARE_TUNNEL}" == "1" && -n "${CLOUDFLARE_ORIGIN_HOST:-}" ]]; then
        if [[ "${ENABLE_SSL}" == "1" ]]; then
            check_health_endpoint "https://${CLOUDFLARE_ORIGIN_HOST}/__health" -k -H "Host: ${DOMAIN}"
        else
            check_health_endpoint "http://${CLOUDFLARE_ORIGIN_HOST}/__health" -H "Host: ${DOMAIN}"
        fi
        success "The selected Cloudflare origin address ${CLOUDFLARE_ORIGIN_HOST} is reachable"
    fi

    if [[ -n "${DOMAIN:-}" && "${DOMAIN}" != "_" ]]; then
        if [[ "${ENABLE_SSL}" == "1" ]]; then
            check_health_endpoint "https://${DOMAIN}/__health" --resolve "${DOMAIN}:443:127.0.0.1"
        else
            check_health_endpoint "http://${DOMAIN}/__health" --resolve "${DOMAIN}:80:127.0.0.1"
        fi
    else
        check_health_endpoint "http://127.0.0.1/__health"
    fi
    success "nginx is proxying /__health correctly"
}

verify_cloudflare_tunnel() {
    if [[ "${USE_CLOUDFLARE_TUNNEL}" != "1" ]]; then
        return
    fi

    local origin_scheme="http"
    local origin_port="80"
    local public_health_url=""
    local wait_for_tunnel="1"
    local retry_test="1"
    local change_url="0"
    local readiness_input=""

    if [[ "${ENABLE_SSL}" == "1" ]]; then
        origin_scheme="https"
        origin_port="443"
    fi

    if [[ -n "${DOMAIN:-}" && "${DOMAIN}" != "_" ]]; then
        public_health_url="https://${DOMAIN}/__health"
    fi

    print_cloudflare_tunnel_help

    info "Checking the public Cloudflare Tunnel URL automatically: ${public_health_url}"
    if check_health_endpoint "${public_health_url}"; then
        success "The public Cloudflare Tunnel URL is already reaching the server successfully."
        return
    fi

    warn "The public Cloudflare Tunnel URL is not healthy yet. DNS propagation, tunnel startup, or origin settings may still be in progress."

    if [[ "${NONINTERACTIVE}" == "1" ]]; then
        info "Skipping interactive Cloudflare Tunnel verification in non-interactive mode."
        return
    fi

    prompt_yes_no wait_for_tunnel "Wait for your Cloudflare Tunnel and test the public URL now?" "1"
    if [[ "${wait_for_tunnel}" != "1" ]]; then
        return
    fi

    prompt_value public_health_url "Public health-check URL to test" "${public_health_url}" 0 1

    while true; do
        printf '\n%b%s%b\n' "${COLOR_BOLD}" "Cloudflare Tunnel Test" "${COLOR_RESET}"
        printf '  Configure cloudflared to point at %s://%s (port %s).\n' "${origin_scheme}" "${CLOUDFLARE_ORIGIN_HOST}" "${origin_port}"
        printf '  The app itself listens on 127.0.0.1:%s.\n' "${APP_PORT}"
        printf '  When the tunnel is up, the installer will test: %s\n' "${public_health_url}"
        if has_tty; then
            tty_print "${COLOR_MAGENTA}?${COLOR_RESET} Press Enter when the tunnel is ready, or type skip to continue without testing: "
            IFS= read -r readiness_input < /dev/tty
            if [[ "${readiness_input}" == "skip" ]]; then
                warn "Skipping Cloudflare Tunnel verification."
                return
            fi
        fi

        if check_health_endpoint "${public_health_url}"; then
            success "The public Cloudflare Tunnel URL reached the server successfully."
            return
        fi

        warn "The public URL did not return a healthy response yet. DNS propagation, tunnel startup, or origin settings may still be in progress."
        prompt_yes_no retry_test "Try the Cloudflare Tunnel check again?" "1"
        if [[ "${retry_test}" != "1" ]]; then
            return
        fi
        prompt_yes_no change_url "Change the public health-check URL before retrying?" "0"
        if [[ "${change_url}" == "1" ]]; then
            prompt_value public_health_url "Public health-check URL to test" "${public_health_url}" 0 1
        fi
    done
}

write_state_file() {
    mkdir -p "${STATE_DIR}"
    cat > "${STATE_FILE}" <<EOF
REPO_URL=$(dotenv_quote "${REPO_URL}")
BRANCH=$(dotenv_quote "${BRANCH}")
INSTALL_DIR=$(dotenv_quote "${INSTALL_DIR}")
SERVICE_NAME=$(dotenv_quote "${SERVICE_NAME}")
APP_USER=$(dotenv_quote "${APP_USER}")
APP_GROUP=$(dotenv_quote "${APP_GROUP}")
APP_PORT=$(dotenv_quote "${APP_PORT}")
DOMAIN=$(dotenv_quote "${DOMAIN}")
ENABLE_SSL=$(dotenv_quote "${ENABLE_SSL}")
USE_CLOUDFLARE_TUNNEL=$(dotenv_quote "${USE_CLOUDFLARE_TUNNEL}")
CLOUDFLARE_ORIGIN_HOST=$(dotenv_quote "${CLOUDFLARE_ORIGIN_HOST}")
SSL_EMAIL=$(dotenv_quote "${SSL_EMAIL}")
DB_NAME=$(dotenv_quote "${DB_NAME}")
DB_USER=$(dotenv_quote "${DB_USER}")
EOF
    chmod 600 "${STATE_FILE}"
}

install_or_update() {
    detect_package_manager
    mapfile -t base_pkg_list < <(base_packages)
    install_packages "${base_pkg_list[@]}"

    source_existing_env
    if [[ "${INSTALLATION_EXISTS}" == "1" ]]; then
        # Before anything is backed up or stopped: waiting for CI's build of the
        # front end, or CI having failed, leaves the running portal untouched.
        prepare_frontend
        backup_runtime_state
        # Must run before ensure_repo_present moves the checkout, or it would
        # record the revision being installed rather than the one being replaced.
        record_rollback_point
        # Before the new code arrives; reload_services starts them again.
        pause_background_jobs
    fi

    detect_redis_service_name
    ensure_systemd_service nginx
    ensure_systemd_service "${REDIS_SERVICE_NAME}"
    ensure_app_user
    configure_selinux

    ensure_repo_present
    collect_configuration

    if [[ "${USE_LOCAL_DB}" == "1" ]]; then
        # Deferred until the answer is known: an installation pointed at an
        # external server has no use for a local database daemon.
        install_packages mariadb-server
        detect_db_service_name
        ensure_systemd_service "${DB_SERVICE_NAME}"
    else
        DB_SERVICE_NAME=""
    fi

    # Before write_env_file on purpose. Everything up to here is reversible by
    # doing nothing, so a database that cannot be read or a dump that cannot be
    # verified stops the run with the installation still pointing at the
    # database that holds the data.
    if [[ "${MIGRATE_DB_DATA}" == "1" ]]; then
        dump_source_database
    fi

    write_env_file
    ensure_virtualenv
    install_frontend
    ensure_database
    if [[ "${MIGRATE_DB_DATA}" == "1" ]]; then
        import_migrated_database
    fi
    if [[ "${INSTALLATION_EXISTS}" == "1" && "${MIGRATE_DB_DATA}" != "1" ]]; then
        # An update: one that brings a migration undoes itself if it fails.
        migrate_database_for_update
    else
        initialize_database_schema
        # A database moved from elsewhere keeps its own setting; a backup
        # restored after this brings its own too.
        if [[ "${MIGRATE_DB_DATA}" != "1" ]]; then
            switch_on_automatic_updates
        fi
    fi
    ensure_admin_account
    render_service_file
    render_billing_reconcile_timer_files
    render_notifications_timer_files
    render_cleanup_logs_timer_files
    render_forum_drift_timer_files
    render_external_work_timer_files
    render_automatic_updates_timer_files
    render_update_runner
    render_update_command
    configure_unattended_upgrades
    obtain_ssl_certificate
    render_nginx_config
    write_cloudflare_tunnel_files
    configure_connection_tracking
    reload_services
    configure_firewall
    write_state_file
    verify_installation
    verify_cloudflare_tunnel
    cleanup_package_caches
}

app_units() {
    # The portal and everything that runs on its behalf, for stopping and
    # starting around a restore. The update runner stays: it is root's.
    printf '%s\n' "${SERVICE_NAME}"
    local timer
    for timer in billing-reconcile notifications cleanup-logs forum-drift external-work; do
        printf '%s\n' "${SERVICE_NAME}-${timer}.timer"
    done
}

restore_from_backup() {
    [[ -f "${RESTORE_FILE}" && -r "${RESTORE_FILE}" ]] || die "Cannot read the backup file ${RESTORE_FILE}."

    if [[ "${INSTALLATION_EXISTS}" != "1" ]]; then
        step "No installation here yet: installing first, then restoring the backup"
        SKIP_ADMIN_ACCOUNT="1"
        MODE="install"
        print_summary
        install_or_update
        MODE="restore"
    else
        source_existing_env
    fi

    local passphrase=""
    if [[ -n "${RESTORE_PASSPHRASE_FILE}" ]]; then
        [[ -r "${RESTORE_PASSPHRASE_FILE}" ]] || die "Cannot read the passphrase file ${RESTORE_PASSPHRASE_FILE}."
        IFS= read -r passphrase < "${RESTORE_PASSPHRASE_FILE}" || true
    else
        has_tty || die "No terminal to ask for the backup passphrase. Pass --passphrase-file FILE."
        prompt_value passphrase "Passphrase of the backup" "" 1 1
    fi

    # The portal's own user does the restore, reading a copy it owns: the
    # original may sit somewhere only root can read.
    local staging
    staging="$(mktemp -d "/var/tmp/${APP_NAME}-restore.XXXXXX")"
    chmod 700 "${staging}"
    cp -- "${RESTORE_FILE}" "${staging}/backup.jabackup"
    chown -R "${APP_USER}:${APP_GROUP}" "${staging}"
    chmod 600 "${staging}/backup.jabackup"

    step "Stopping the portal while it is restored"
    local unit
    while IFS= read -r unit; do
        systemctl stop "${unit}" 2>/dev/null || true
    done < <(app_units)

    step "Restoring ${RESTORE_FILE}"
    if ! printf '%s\n' "${passphrase}" | run_as_app_user env PYTHONPATH="${INSTALL_DIR}" \
            "${INSTALL_DIR}/.venv/bin/flask" --app aeronautics_members.app:create_app \
            restore-backup "${staging}/backup.jabackup" --passphrase-stdin --yes \
            --env-out "${staging}/env.json"; then
        passphrase=""
        rm -rf -- "${staging}"
        while IFS= read -r unit; do
            systemctl start "${unit}" 2>/dev/null || true
        done < <(app_units)
        die "The restore did not complete; see above. If it stopped before emptying the database, nothing was changed."
    fi
    passphrase=""

    # SECRET_KEY, the Stripe keys and the mail accounts from the backup. .env is
    # root's, so root writes them -- with the portal's own function, so the
    # quoting is the one both bash and the portal read back the same way.
    if [[ -s "${staging}/env.json" ]]; then
        step "Restoring the secret key and credentials in ${ENV_FILE}"
        env PYTHONPATH="${INSTALL_DIR}" "${INSTALL_DIR}/.venv/bin/python" - "${ENV_FILE}" "${staging}/env.json" <<'PY'
import json
import sys

from aeronautics_members.services.backup import apply_env_values

with open(sys.argv[2], encoding="utf-8") as handle:
    apply_env_values(sys.argv[1], json.load(handle))
PY
        chown root:"${APP_GROUP}" "${ENV_FILE}"
        chmod 640 "${ENV_FILE}"
    fi
    rm -rf -- "${staging}"

    step "Starting the portal again"
    systemctl daemon-reload
    while IFS= read -r unit; do
        systemctl start "${unit}"
    done < <(app_units)

    success "Restored. Background jobs are paused: sign in with an account from the backup, then review and resume them under Settings > Maintenance > Backup & Restore."
}

uninstall_everything() {
    step "Uninstalling ${APP_NAME}"

    if [[ -f "${ENV_FILE}" ]]; then
        source_existing_env
    fi

    if [[ -f "${BILLING_RECONCILE_TIMER_FILE}" ]]; then
        systemctl disable --now "${SERVICE_NAME}-billing-reconcile.timer" || true
        rm -f "${BILLING_RECONCILE_TIMER_FILE}"
    fi
    if [[ -f "${BILLING_RECONCILE_SERVICE_FILE}" ]]; then
        rm -f "${BILLING_RECONCILE_SERVICE_FILE}"
    fi
    if [[ -f "${NOTIFICATIONS_TIMER_FILE}" ]]; then
        systemctl disable --now "${SERVICE_NAME}-notifications.timer" || true
        rm -f "${NOTIFICATIONS_TIMER_FILE}"
    fi
    if [[ -f "${NOTIFICATIONS_SERVICE_FILE}" ]]; then
        rm -f "${NOTIFICATIONS_SERVICE_FILE}"
    fi
    if [[ -f "${CLEANUP_LOGS_TIMER_FILE}" ]]; then
        systemctl disable --now "${SERVICE_NAME}-cleanup-logs.timer" || true
        rm -f "${CLEANUP_LOGS_TIMER_FILE}"
    fi
    if [[ -f "${CLEANUP_LOGS_SERVICE_FILE}" ]]; then
        rm -f "${CLEANUP_LOGS_SERVICE_FILE}"
    fi
    if [[ -f "${FORUM_DRIFT_TIMER_FILE}" ]]; then
        systemctl disable --now "${SERVICE_NAME}-forum-drift.timer" || true
        rm -f "${FORUM_DRIFT_TIMER_FILE}"
    fi
    if [[ -f "${FORUM_DRIFT_SERVICE_FILE}" ]]; then
        rm -f "${FORUM_DRIFT_SERVICE_FILE}"
    fi
    if [[ -f "${EXTERNAL_WORK_TIMER_FILE}" ]]; then
        systemctl disable --now "${SERVICE_NAME}-external-work.timer" || true
        rm -f "${EXTERNAL_WORK_TIMER_FILE}"
    fi
    if [[ -f "${EXTERNAL_WORK_SERVICE_FILE}" ]]; then
        rm -f "${EXTERNAL_WORK_SERVICE_FILE}"
    fi
    if [[ -f "${AUTOMATIC_UPDATES_TIMER_FILE}" ]]; then
        systemctl disable --now "${SERVICE_NAME}-automatic-updates.timer" || true
        rm -f "${AUTOMATIC_UPDATES_TIMER_FILE}"
    fi
    if [[ -f "${AUTOMATIC_UPDATES_SERVICE_FILE}" ]]; then
        rm -f "${AUTOMATIC_UPDATES_SERVICE_FILE}"
    fi
    # The system keeps its own security updates, at its own times again; only
    # what the portal set goes with it.
    rm -f "${UNATTENDED_UPGRADES_FILE}" "${APT_UPGRADE_TIMER_DROPIN}" "${APT_UPGRADE_SERVICE_DROPIN}"
    if [[ -f "${RESTART_IF_NEEDED_TIMER_FILE}" ]]; then
        systemctl disable --now "${SERVICE_NAME}-restart-if-needed.timer" || true
        rm -f "${RESTART_IF_NEEDED_TIMER_FILE}"
    fi
    rm -f "${RESTART_IF_NEEDED_SERVICE_FILE}"
    if [[ -f "${UPDATE_RUNNER_PATH_FILE}" ]]; then
        systemctl disable --now "${SERVICE_NAME}-update-runner.path" || true
        rm -f "${UPDATE_RUNNER_PATH_FILE}"
    fi
    if [[ -f "${UPDATE_RUNNER_TIMER_FILE}" ]]; then
        systemctl disable --now "${SERVICE_NAME}-update-runner.timer" || true
        rm -f "${UPDATE_RUNNER_TIMER_FILE}"
    fi
    if [[ -f "${UPDATE_RUNNER_SERVICE_FILE}" ]]; then
        rm -f "${UPDATE_RUNNER_SERVICE_FILE}"
    fi
    rm -f "${UPDATE_RUNNER_SCRIPT}"
    rm -rf "${UPDATE_STATE_DIR}" "${UPDATE_RUNNER_DIR}"
    if [[ -f "${SERVICE_FILE}" ]]; then
        systemctl disable --now "${SERVICE_NAME}" || true
        rm -f "${SERVICE_FILE}"
        systemctl daemon-reload
    fi

    if [[ -f "${UPDATE_COMMAND_PATH}" ]] && grep -q "jaeronautics-update-command" "${UPDATE_COMMAND_PATH}" 2>/dev/null; then
        rm -f "${UPDATE_COMMAND_PATH}"
    fi
    # The connection-tracking size (configure_connection_tracking): the values
    # set stay until the next reboot, which then uses the system's own.
    rm -f "/etc/modules-load.d/${SERVICE_NAME}-conntrack.conf" "/etc/sysctl.d/60-${SERVICE_NAME}-conntrack.conf"

    if [[ -n "${NGINX_ENABLED_PATH}" ]]; then
        rm -f "${NGINX_ENABLED_PATH}"
    fi
    rm -f "${NGINX_CONF_PATH}"
    if command_exists nginx; then
        nginx -t && systemctl reload nginx || true
    fi

    if [[ "${ENABLE_SSL:-0}" == "1" && -n "${DOMAIN:-}" && "${DOMAIN}" != "_" ]] && command_exists certbot; then
        certbot delete --cert-name "${DOMAIN}" --non-interactive || true
    fi

    if [[ "${DB_HOST:-127.0.0.1}" == "127.0.0.1" || "${DB_HOST:-localhost}" == "localhost" ]]; then
        detect_db_service_name
        ensure_systemd_service "${DB_SERVICE_NAME}"
        run_db_sql "
DROP DATABASE IF EXISTS \`${DB_NAME:-$DEFAULT_DB_NAME}\`;
DROP USER IF EXISTS '${DB_USER:-$DEFAULT_DB_USER}'@'localhost';
DROP USER IF EXISTS '${DB_USER:-$DEFAULT_DB_USER}'@'127.0.0.1';
FLUSH PRIVILEGES;" || true
    fi

    rm -rf "${INSTALL_DIR}"
    rm -f "${STATE_FILE}"
    rmdir "${STATE_DIR}" 2>/dev/null || true

    if id -u "${APP_USER}" >/dev/null 2>&1; then
        userdel "${APP_USER}" 2>/dev/null || true
    fi
    if getent group "${APP_GROUP}" >/dev/null 2>&1; then
        groupdel "${APP_GROUP}" 2>/dev/null || true
    fi

    success "Uninstall completed. Packages such as nginx, MariaDB, Python, and git were left installed on the system."
}

cleanup_bootstrap_dir() {
    if [[ "${SCRIPT_DIR}" == "${BOOTSTRAP_DIR}" && "${INSTALL_DIR}" != "${BOOTSTRAP_DIR}" ]]; then
        rm -rf "${BOOTSTRAP_DIR}"
    fi
}

print_summary() {
    printf '\n%b%s%b\n' "${COLOR_BOLD}" "Deployment summary" "${COLOR_RESET}"
    printf '  Mode:         %s\n' "${MODE}"
    printf '  Repo:         %s\n' "${REPO_URL}"
    printf '  Branch:       %s\n' "${BRANCH}"
    printf '  Install dir:  %s\n' "${INSTALL_DIR}"
    printf '  Domain:       %s\n' "${DOMAIN}"
    printf '  Service:      %s\n' "${SERVICE_NAME}"
    printf '  HTTPS:        %s\n' "$( [[ "${ENABLE_SSL}" == "1" ]] && printf yes || printf no )"
    printf '  Cloudflare:   %s\n' "$( [[ "${USE_CLOUDFLARE_TUNNEL}" == "1" ]] && printf yes || printf no )"
    if [[ -n "${CLOUDFLARE_ORIGIN_HOST:-}" ]]; then
        printf '  Origin host:  %s\n' "${CLOUDFLARE_ORIGIN_HOST}"
    fi
    printf '\n'
}

main() {
    require_root
    load_state
    parse_args "$@"

    REPO_URL="$(detect_repo_url)"
    resolve_paths
    bootstrap_self_update
    resolve_paths
    detect_existing_installation

    if [[ -z "${MODE}" ]]; then
        if [[ "${INSTALLATION_EXISTS}" == "1" ]]; then
            choose_existing_install_action
        else
            MODE="install"
        fi
    fi

    if [[ "${MODE}" != "uninstall" ]]; then
        hold_night_lock
    fi

    case "${MODE}" in
        install|update|repair)
            print_summary
            install_or_update
            cleanup_bootstrap_dir
            print_stripe_webhook_help
            print_cloudflare_tunnel_help
            success "Installation finished successfully."
            ;;
        rollback)
            roll_back_installation
            cleanup_bootstrap_dir
            ;;
        restore)
            restore_from_backup
            cleanup_bootstrap_dir
            ;;
        uninstall)
            print_summary
            uninstall_everything
            cleanup_bootstrap_dir
            ;;
        *)
            die "Invalid mode '${MODE}'. Expected install, update, repair, rollback, or uninstall."
            ;;
    esac
}

main "$@"






