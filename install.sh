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
STATE_DIR="/etc/jaeronautics"
STATE_FILE="${STATE_DIR}/install.conf"
BOOTSTRAP_DIR="/tmp/${APP_NAME}-installer-bootstrap"

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
NONINTERACTIVE="${BOOTSTRAP_NONINTERACTIVE:-0}"

PACKAGE_MANAGER=""
DB_SERVICE_NAME=""
NGINX_CONF_PATH=""
NGINX_ENABLED_PATH=""
ENV_FILE=""
SERVICE_FILE=""
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
  --enable-ssl
  --disable-ssl
  --ssl-email EMAIL
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
                remote="$(git -C "${current}" config --get remote.origin.url || true)"
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
            printf '%s\n' ca-certificates curl git nginx mariadb-client mariadb-server openssl python3 python3-pip python3-venv
            ;;
        dnf|yum)
            printf '%s\n' ca-certificates curl git nginx mariadb-server openssl python3 python3-pip
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

backup_local_repo_changes() {
    local target_dir="$1"

    if [[ ! -d "${target_dir}/.git" ]]; then
        return
    fi

    if [[ -n "$(git -C "${target_dir}" status --porcelain)" ]]; then
        local patch_file="${target_dir}/.installer-local-changes-$(date +%Y%m%d%H%M%S).patch"
        git -C "${target_dir}" diff > "${patch_file}" || true
        warn "Local git changes were detected and backed up to ${patch_file}."
    fi
}

sync_repo_to_dir() {
    local target_dir="$1"
    local repo_url="$2"
    local branch="$3"

    mkdir -p "$(dirname "${target_dir}")"

    if [[ -d "${target_dir}/.git" ]]; then
        backup_local_repo_changes "${target_dir}"
        info "Updating repository in ${target_dir}"
        git -C "${target_dir}" remote set-url origin "${repo_url}" || true
        retry 3 git -C "${target_dir}" fetch --prune origin
        git -C "${target_dir}" checkout -B "${branch}" "origin/${branch}"
        git -C "${target_dir}" reset --hard "origin/${branch}"
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
        existing_remote="$(git -C "${INSTALL_DIR}" config --get remote.origin.url || true)"
    fi
    if [[ -f "${STATE_FILE}" || ( -n "${existing_remote}" && "$(normalize_repo_url "${existing_remote}")" == "$(normalize_repo_url "${REPO_URL}")" ) ]]; then
        bootstrap_target="${INSTALL_DIR}"
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

    step "Installing Python dependencies"
    run_as_app_user "${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip wheel
    run_as_app_user "${INSTALL_DIR}/.venv/bin/pip" install --upgrade -r "${INSTALL_DIR}/requirements.txt"
}

collect_configuration() {
    source_existing_env
    local reconfigure_all="0"

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

    if [[ "${DB_HOST:-127.0.0.1}" == "127.0.0.1" || "${DB_HOST:-localhost}" == "localhost" ]]; then
        USE_LOCAL_DB="1"
    else
        USE_LOCAL_DB="0"
    fi

    local domain_default="${DOMAIN:-$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo "_")}"
    if [[ "${reconfigure_all}" == "1" || -z "${DOMAIN:-}" ]]; then
        prompt_value DOMAIN "Domain for nginx (use _ for a catch-all server)" "${domain_default}" 0 1
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

    if [[ "${MAIL_ACCOUNTS_JSON}" == "{}" && "${NONINTERACTIVE}" != "1" ]]; then
        warn "MAIL_ACCOUNTS_JSON is currently empty. The site will run, but SMTP-based features will need configuration later."
    fi

    if [[ "${reconfigure_all}" == "1" || -z "${ENABLE_SSL}" ]]; then
        if [[ "${DOMAIN}" == "_" ]]; then
            ENABLE_SSL="0"
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
    env PYTHONPATH="${INSTALL_DIR}" run_as_app_user "${INSTALL_DIR}/.venv/bin/flask" --app aeronautics_members.app:create_app db-init
}

render_service_file() {
    step "Writing systemd service"
    local unit_after="After=network.target"
    local unit_requires=""

    if [[ "${USE_LOCAL_DB}" == "1" ]]; then
        unit_after="After=network.target ${DB_SERVICE_NAME}.service"
        unit_requires="Requires=${DB_SERVICE_NAME}.service"
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
    nginx -t
    systemctl reload nginx
}

verify_installation() {
    step "Verifying deployment"
    if [[ "${USE_LOCAL_DB}" == "1" ]]; then
        systemctl is-active --quiet "${DB_SERVICE_NAME}"
    fi
    systemctl is-active --quiet nginx
    systemctl is-active --quiet "${SERVICE_NAME}"
    curl -fsS "http://127.0.0.1:${APP_PORT}/" >/dev/null
    success "The application is responding on 127.0.0.1:${APP_PORT}"
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

    ensure_systemd_service nginx
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
    render_service_file
    obtain_ssl_certificate
    render_nginx_config
    reload_services
    configure_firewall
    write_state_file
    verify_installation
}

uninstall_everything() {
    step "Uninstalling ${APP_NAME}"

    if [[ -f "${ENV_FILE}" ]]; then
        source_existing_env
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

    if [[ "${ENABLE_SSL:-0}" == "1" && -n "${DOMAIN:-}" && "${DOMAIN}" != "_" && command_exists certbot ]]; then
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
