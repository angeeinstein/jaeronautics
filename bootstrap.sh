#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_URL="https://raw.githubusercontent.com/angeeinstein/jaeronautics/main/install.sh"
TMP_SCRIPT="$(mktemp /tmp/jaeronautics-install.XXXXXX.sh)"

cleanup() {
    rm -f "${TMP_SCRIPT}"
}
trap cleanup EXIT

download_installer() {
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

    if [[ "${EUID}" -eq 0 ]]; then
        exec bash "${TMP_SCRIPT}" "$@"
    fi

    if ! command -v sudo >/dev/null 2>&1; then
        echo "Please run as root or install sudo." >&2
        exit 1
    fi

    exec sudo -E bash "${TMP_SCRIPT}" "$@"
}

download_installer
run_installer "$@"
