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
DEFAULT_LANGUAGES="en,de"
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
NONINTERACTIVE="${BOOTSTRAP_NONINTERACTIVE:-0}"

PACKAGE_MANAGER=""
DB_SERVICE_NAME=""
REDIS_SERVICE_NAME=""
NGINX_CONF_PATH=""
NGINX_ENABLED_PATH=""
ENV_FILE=""
SERVICE_FILE=""
BILLING_RECONCILE_SERVICE_FILE=""
BILLING_RECONCILE_TIMER_FILE=""
PACKAGE_CACHE_UPDATED=0
INSTALLATION_EXISTS=0
USE_LOCAL_DB="1"

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

on_error() {
    local line="$1"
    local command="$2"
    error "Installer failed at line ${line}: ${command}"
    if [[ -n "${SERVICE_NAME:-}" ]] && command -v systemctl >/dev/null 2>&1; then
        warn "Recent ${SERVICE_NAME} service logs:"
        journalctl -u "${SERVICE_NAME}" -n 20 --no-pager 2>/dev/null || true
    fi
}
trap 'on_error "${LINENO}" "${BASH_COMMAND}"' ERR

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
  --yes, --non-interactive
  -h, --help
EOF
}

has_tty() {
    [[ -r /dev/tty && -w /dev/tty ]]
}

tty_print() {
    if has_tty; then
        printf '%b' "$*" > /dev/tty
    else
        printf '%b' "$*" >&2
    fi
}

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
            --yes|--non-interactive)
                NONINTERACTIVE="1"
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                die "Unknown argument: $1"
                ;;
        esac
    done
}

resolve_paths() {
    ENV_FILE="${INSTALL_DIR}/.env"
    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
    BILLING_RECONCILE_SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}-billing-reconcile.service"
    BILLING_RECONCILE_TIMER_FILE="/etc/systemd/system/${SERVICE_NAME}-billing-reconcile.timer"

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
            retry 3 apt-get update
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

install_packages() {
    update_package_index_once

    case "${PACKAGE_MANAGER}" in
        apt)
            retry 3 apt-get install -y "$@"
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
            apt-get clean || true
            rm -rf /var/lib/apt/lists/* 2>/dev/null || true
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
            printf '%s\n' ca-certificates curl git nginx mariadb-client mariadb-server openssl python3 python3-pip python3-venv redis-server
            ;;
        dnf|yum)
            printf '%s\n' ca-certificates curl git nginx mariadb-server openssl python3 python3-pip redis
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
            4) die "Cancelled." ;;
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

    if [[ "${USE_LOCAL_DB:-1}" == "1" && ( "${DB_HOST:-127.0.0.1}" == "127.0.0.1" || "${DB_HOST:-localhost}" == "localhost" ) && -n "${DB_NAME:-}" ]]; then
        if dump_client="$(db_dump_client 2>/dev/null)"; then
            dump_file="${BACKUP_DIR}/${APP_NAME}-db-${backup_stamp}.sql.gz"
            if "${dump_client}" --protocol=socket -u root --single-transaction --quick --skip-lock-tables "${DB_NAME}" | gzip -c > "${dump_file}"; then
                info "Backed up MariaDB database to ${dump_file}"
            else
                rm -f "${dump_file}" 2>/dev/null || true
                warn "Database backup failed for ${DB_NAME}. Continuing without a DB dump."
            fi
        else
            warn "Could not find mysqldump/mariadb-dump. Continuing without a DB dump backup."
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

sync_repo_to_dir() {
    local target_dir="$1"
    local repo_url="$2"
    local branch="$3"

    mkdir -p "$(dirname "${target_dir}")"

    if [[ -d "${target_dir}/.git" ]]; then
        warn_local_repo_changes "${target_dir}"
        info "Updating repository in ${target_dir}"
        git_in_dir "${target_dir}" remote set-url origin "${repo_url}" || true
        retry 3 git_in_dir "${target_dir}" fetch --prune origin
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

    local bootstrap_target="${BOOTSTRAP_DIR}"
    local existing_remote=""
    if [[ -d "${INSTALL_DIR}/.git" ]]; then
        existing_remote="$(git_in_dir "${INSTALL_DIR}" config --get remote.origin.url || true)"
    fi
    if [[ -f "${STATE_FILE}" || ( -n "${existing_remote}" && "$(normalize_repo_url "${existing_remote}")" == "$(normalize_repo_url "${REPO_URL}")" ) ]]; then
        bootstrap_target="${INSTALL_DIR}"
    fi

    if [[ "${bootstrap_target}" == "${BOOTSTRAP_DIR}" ]]; then
        rm -rf "${BOOTSTRAP_DIR}" 2>/dev/null || true
    fi

    sync_repo_to_dir "${bootstrap_target}" "${REPO_URL}" "${BRANCH}"
    chmod +x "${bootstrap_target}/install.sh"

    step "Re-running installer from the latest repository copy"
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
        bash "${bootstrap_target}/install.sh" "${ORIGINAL_ARGS[@]}"
    exit $?
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
    chown -R "${APP_USER}:${APP_GROUP}" "${INSTALL_DIR}"
}

ensure_virtualenv() {
    if [[ ! -x "${INSTALL_DIR}/.venv/bin/python3" ]]; then
        step "Creating Python virtual environment"
        python3 -m venv "${INSTALL_DIR}/.venv"
    fi

    chown -R "${APP_USER}:${APP_GROUP}" "${INSTALL_DIR}/.venv"

    step "Installing Python dependencies"
    run_as_app_user "${INSTALL_DIR}/.venv/bin/pip" install --no-cache-dir --upgrade pip wheel
    run_as_app_user "${INSTALL_DIR}/.venv/bin/pip" install --no-cache-dir --upgrade -r "${INSTALL_DIR}/requirements.txt"
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

initialize_database_schema() {
    step "Initializing database schema"
    run_as_app_user env PYTHONPATH="${INSTALL_DIR}" "${INSTALL_DIR}/.venv/bin/flask" --app aeronautics_members.app:create_app db-init
}

count_admin_accounts() {
    run_as_app_user env PYTHONPATH="${INSTALL_DIR}" "${INSTALL_DIR}/.venv/bin/python" -c "from sqlalchemy import func; from aeronautics_members.app import create_app; from aeronautics_members.db_models import Role, User, db; app=create_app(); ctx=app.app_context(); ctx.push(); print(db.session.scalar(db.select(func.count()).select_from(User).where(User.roles.any(Role.slug == 'admin'))) or 0); ctx.pop()"
}

ensure_admin_account() {
    local existing_admin_count="0"
    local create_admin_now="0"

    existing_admin_count="$(count_admin_accounts 2>/dev/null || printf '0')"
    if [[ ! "${existing_admin_count}" =~ ^[0-9]+$ ]]; then
        existing_admin_count="0"
    fi

    if [[ -n "${ADMIN_EMAIL:-}" ]]; then
        create_admin_now="1"
    elif [[ "${existing_admin_count}" -eq 0 ]]; then
        if [[ "${NONINTERACTIVE}" == "1" ]]; then
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
ExecStart=${INSTALL_DIR}/.venv/bin/gunicorn --workers 3 --bind 127.0.0.1:${APP_PORT} wsgi:application
Restart=always
RestartSec=5
TimeoutStartSec=60
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

    chmod 644 "${SERVICE_FILE}"
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
ExecStart=${INSTALL_DIR}/.venv/bin/flask --app aeronautics_members.app:create_app reconcile-billing --lookahead-days 3
TimeoutStartSec=180
PrivateTmp=true
NoNewPrivileges=true
EOF

    cat > "${BILLING_RECONCILE_TIMER_FILE}" <<EOF
[Unit]
Description=Daily Joanneum Aeronautics billing reconciliation

[Timer]
OnCalendar=*-*-* 03:15:00
RandomizedDelaySec=10m
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

render_nginx_config() {
    step "Writing nginx configuration"
    local static_dir="${INSTALL_DIR}/aeronautics_members/static"
    mkdir -p "$(dirname "${NGINX_CONF_PATH}")"

    if [[ "${ENABLE_SSL}" == "1" ]] && cert_paths_exist; then
        cat > "${NGINX_CONF_PATH}" <<EOF
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

    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self' https://js.stripe.com https://cdn.jsdelivr.net/npm/; style-src 'self' https://cdn.jsdelivr.net/npm/; frame-src https://js.stripe.com; img-src 'self' data:;" always;

    real_ip_header CF-Connecting-IP;
    real_ip_recursive on;
    set_real_ip_from 127.0.0.1;
    set_real_ip_from ::1;
    client_max_body_size 20m;

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
        proxy_pass http://127.0.0.1:${APP_PORT};
        proxy_read_timeout 120s;
    }
}
EOF
    else
        cat > "${NGINX_CONF_PATH}" <<EOF
server {
    listen 80;
    server_name ${DOMAIN};

    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self' https://js.stripe.com https://cdn.jsdelivr.net/npm/; style-src 'self' https://cdn.jsdelivr.net/npm/; frame-src https://js.stripe.com; img-src 'self' data:;" always;

    real_ip_header CF-Connecting-IP;
    real_ip_recursive on;
    set_real_ip_from 127.0.0.1;
    set_real_ip_from ::1;
    client_max_body_size 20m;

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
        proxy_pass http://127.0.0.1:${APP_PORT};
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

reload_services() {
    step "Reloading system services"
    systemctl daemon-reload
    systemctl enable --now "${SERVICE_NAME}"
    systemctl enable --now "${SERVICE_NAME}-billing-reconcile.timer"
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
        backup_runtime_state
    fi

    detect_redis_service_name
    ensure_systemd_service nginx
    ensure_systemd_service "${REDIS_SERVICE_NAME}"
    ensure_app_user
    configure_selinux

    ensure_repo_present
    collect_configuration

    if [[ "${USE_LOCAL_DB}" == "1" ]]; then
        detect_db_service_name
        ensure_systemd_service "${DB_SERVICE_NAME}"
    else
        DB_SERVICE_NAME=""
    fi

    write_env_file
    ensure_virtualenv
    ensure_database
    initialize_database_schema
    ensure_admin_account
    render_service_file
    render_billing_reconcile_timer_files
    obtain_ssl_certificate
    render_nginx_config
    write_cloudflare_tunnel_files
    reload_services
    configure_firewall
    write_state_file
    verify_installation
    verify_cloudflare_tunnel
    cleanup_package_caches
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
    if [[ -f "${SERVICE_FILE}" ]]; then
        systemctl disable --now "${SERVICE_NAME}" || true
        rm -f "${SERVICE_FILE}"
        systemctl daemon-reload
    fi

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

    case "${MODE}" in
        install|update|repair)
            print_summary
            install_or_update
            cleanup_bootstrap_dir
            print_stripe_webhook_help
            print_cloudflare_tunnel_help
            success "Installation finished successfully."
            ;;
        uninstall)
            print_summary
            uninstall_everything
            cleanup_bootstrap_dir
            ;;
        *)
            die "Invalid mode '${MODE}'. Expected install, update, repair, or uninstall."
            ;;
    esac
}

main "$@"
