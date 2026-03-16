#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_URL="https://raw.githubusercontent.com/angeeinstein/jaeronautics/main/install.sh"
TMP_SCRIPT=""

create_tmp_script() {
    local candidate_dir=""

    for candidate_dir in /tmp /var/tmp /dev/shm; do
        if [[ -d "${candidate_dir}" && -w "${candidate_dir}" ]]; then
            if TMP_SCRIPT="$(mktemp "${candidate_dir}/jaeronautics-install.XXXXXX.sh" 2>/dev/null)"; then
                return 0
            fi
        fi
    done

    echo "[ERR] Could not create a temporary installer file. The filesystem may be full." >&2
    echo "[ERR] Free some space and retry. Useful checks: df -h, du -xh /var/www/jaeronautics | sort -h | tail" >&2
    exit 1
}

cleanup() {
    if [[ -n "${TMP_SCRIPT}" ]]; then
        rm -f "${TMP_SCRIPT}"
    fi
}
trap cleanup EXIT

download_installer() {
    echo "[INFO] Downloading jaeronautics installer..."

    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "${INSTALL_URL}" -o "${TMP_SCRIPT}"
        return
    fi

    if command -v wget >/dev/null 2>&1; then
        wget -qO "${TMP_SCRIPT}" "${INSTALL_URL}"
        return
    fi

    echo "Install curl or wget first, then re-run this command." >&2
    exit 1
}

run_installer() {
    chmod +x "${TMP_SCRIPT}"
    echo "[INFO] Starting jaeronautics installer..."

    if [[ "${EUID}" -eq 0 ]]; then
        exec bash "${TMP_SCRIPT}" "$@"
    fi

    if ! command -v sudo >/dev/null 2>&1; then
        echo "Please run as root or install sudo." >&2
        exit 1
    fi

    exec sudo -E bash "${TMP_SCRIPT}" "$@"
}

create_tmp_script
download_installer
run_installer "$@"
