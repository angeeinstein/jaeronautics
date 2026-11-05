#!/usr/bin/env bash
# A server's night for the system itself (docs/automatic-updates-plan.md,
# parts 3 and 5): Ubuntu's security updates -- and cloudflared, from
# Cloudflare's own repository -- at 03:40, a restart at 04:00 only when one
# of them needs it. One after the other, behind one lock (flock, the
# kernel's: it goes with the process however that ends), which other nightly
# work on the server can hold too -- the portal's jobs and its update do.
#
#   server-night.sh install --name NAME [--timezone ZONE] [--lock FILE] [--wait SECONDS]
#   server-night.sh remove --name NAME [--lock FILE]
#
# and, from the timers it sets up: server-night.sh updates | restart-if-needed.
#
# The portal's installer runs it for the portal's server (install.sh,
# configure_unattended_upgrades). Any other server -- the forum's -- runs it
# once by hand, with the command System health shows; running it again
# brings that server up to date with it. It keeps a copy of itself where only
# root may write, for the timers: they run it as root every night.

set -Eeuo pipefail

SELF="${BASH_SOURCE[0]}"

NAME=""
ZONE="UTC"
LOCK=""
WAIT_SECONDS=3600

APT_CONF_DIR="/etc/apt/apt.conf.d"
SYSTEMD_DIR="/etc/systemd/system"
LIB_DIR="/usr/local/lib"
STATE_DIR="/var/lib"
ZONEINFO="/usr/share/zoneinfo"
REBOOT_REQUIRED="/run/reboot-required"
APT_DAILY="/usr/lib/apt/apt.systemd.daily"

say() { printf '%s\n' "$*"; }
fail() { printf 'server-night: %s\n' "$*" >&2; exit 1; }

paths() {
    CONF_FILE="${APT_CONF_DIR}/52${NAME}-unattended-upgrades"
    TIMER_DROPIN="${SYSTEMD_DIR}/apt-daily-upgrade.timer.d/50-${NAME}.conf"
    SERVICE_DROPIN="${SYSTEMD_DIR}/apt-daily-upgrade.service.d/50-${NAME}.conf"
    RESTART_SERVICE="${SYSTEMD_DIR}/${NAME}-restart-if-needed.service"
    RESTART_TIMER="${SYSTEMD_DIR}/${NAME}-restart-if-needed.timer"
    COPY="${LIB_DIR}/${NAME}/server-night.sh"
    DEFAULT_LOCK="${STATE_DIR}/${NAME}/night.lock"
    LOCK="${LOCK:-${DEFAULT_LOCK}}"
}

# When, in the given time zone if the system knows it -- a timer naming one it
# does not know would not start at all --, else on the server's clock.
at() {
    local zone="${ZONE}"
    if [[ ! "${zone}" =~ ^[A-Za-z0-9_+/-]+$ || ! -f "${ZONEINFO}/${zone}" ]]; then
        zone="UTC"
    fi
    printf '*-*-* %s %s' "$1" "${zone}"
}

ensure_unattended_upgrades() {
    dpkg-query -W -f='${Status}' unattended-upgrades 2>/dev/null | grep -q "install ok installed" && return 0
    say "Installing unattended-upgrades"
    apt-get update -q
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q unattended-upgrades
}

install_night() {
    ensure_unattended_upgrades

    # The timers' copy: written beside and renamed, as bash reads a script
    # while it runs it.
    install -d -m 0755 "$(dirname "${COPY}")"
    install -m 0755 "${SELF}" "${COPY}.tmp"
    mv -f "${COPY}.tmp" "${COPY}"

    # Kept if there: whoever holds or waits for the lock holds that file, and
    # a new one would be a new lock (install.sh, ensure_night_lock).
    if [[ ! -f "${LOCK}" ]]; then
        install -d -m 0755 "$(dirname "${LOCK}")"
        install -m 0600 /dev/null "${LOCK}"
    fi

    # After Ubuntu's own files (20auto-upgrades, 50unattended-upgrades), so
    # what is set there stays and only this is added.
    install -d -m 0755 "${APT_CONF_DIR}"
    cat > "${CONF_FILE}" <<EOF
// Written by server-night.sh (docs/automatic-updates-plan.md); removed by its
// "remove". Ubuntu's security updates every night, cloudflared with them, and
// a restart when one needs it -- ${NAME}-restart-if-needed, after the night's
// other steps, never in the middle of one.
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
Unattended-Upgrade::Automatic-Reboot "false";
// Cloudflare's repository, matched by its address: Ubuntu's updates do not
// take packages from other repositories unless they are named.
Unattended-Upgrade::Origins-Pattern {
        "site=pkg.cloudflare.com";
};
EOF

    install -d -m 0755 "$(dirname "${TIMER_DROPIN}")" "$(dirname "${SERVICE_DROPIN}")"
    cat > "${TIMER_DROPIN}" <<EOF
# Written by server-night.sh: Ubuntu's updates as a step of the night.
[Timer]
OnCalendar=
OnCalendar=$(at 03:40)
RandomizedDelaySec=0
EOF
    cat > "${SERVICE_DROPIN}" <<EOF
# Written by server-night.sh: queued behind the night lock.
[Service]
ExecStart=
ExecStart=/usr/bin/flock --wait ${WAIT_SECONDS} ${LOCK} ${COPY} updates
EOF

    cat > "${RESTART_SERVICE}" <<EOF
[Unit]
Description=Restart the server if an update needs it (server-night.sh)

[Service]
Type=oneshot
# Root: it restarts the machine. Only when Ubuntu says an update needs that,
# and only once nothing else of the night runs.
ExecStart=/usr/bin/flock --wait ${WAIT_SECONDS} ${LOCK} ${COPY} restart-if-needed
TimeoutStartSec=$(( WAIT_SECONDS + 120 ))
EOF
    cat > "${RESTART_TIMER}" <<EOF
[Unit]
Description=Restart the server if an update needs it (server-night.sh, timer)

[Timer]
OnCalendar=$(at 04:00)
Persistent=false
Unit=${NAME}-restart-if-needed.service

[Install]
WantedBy=timers.target
EOF
    chmod 644 "${CONF_FILE}" "${TIMER_DROPIN}" "${SERVICE_DROPIN}" "${RESTART_SERVICE}" "${RESTART_TIMER}"

    systemctl daemon-reload
    systemctl enable --now unattended-upgrades 2>/dev/null || true
    systemctl enable "${NAME}-restart-if-needed.timer"
    systemctl restart "${NAME}-restart-if-needed.timer" apt-daily-upgrade.timer 2>/dev/null || true
    say "The night is set up: security updates and cloudflared at $(at 03:40 | cut -d' ' -f2-), a restart at 04:00 when one is needed."
}

remove_night() {
    systemctl disable --now "${NAME}-restart-if-needed.timer" 2>/dev/null || true
    rm -f "${CONF_FILE}" "${TIMER_DROPIN}" "${SERVICE_DROPIN}" "${RESTART_SERVICE}" "${RESTART_TIMER}" "${COPY}"
    rmdir "$(dirname "${COPY}")" 2>/dev/null || true
    # Only a lock of its own: one passed in belongs to whoever else uses it.
    if [[ "${LOCK}" == "${DEFAULT_LOCK}" ]]; then
        rm -f "${LOCK}"
        rmdir "$(dirname "${LOCK}")" 2>/dev/null || true
    fi
    systemctl daemon-reload
    # Ubuntu's own times again.
    systemctl restart apt-daily-upgrade.timer 2>/dev/null || true
    say "Removed: the system installs its security updates at its own times again, and nothing restarts it."
}

# A program the update replaced keeps running the old one until restarted: its
# file on disk is gone ("(deleted)"). Only cloudflared, which its package
# leaves to whoever runs it; Ubuntu's own services restart themselves.
restart_replaced() {
    local program="$1" pid exe unit
    local -A units=()
    for pid in $(pgrep -x "${program}" || true); do
        exe="$(readlink "/proc/${pid}/exe" 2>/dev/null)" || continue
        [[ "${exe}" == *" (deleted)" ]] || continue
        unit="$(ps -o unit= -p "${pid}" 2>/dev/null | tr -d ' ')"
        [[ "${unit}" == *.service ]] && units["${unit}"]=1
    done
    for unit in "${!units[@]}"; do
        say "${program} was updated: restarting ${unit}."
        systemctl restart "${unit}" || say "Could not restart ${unit}."
    done
}

updates() {
    local status=0
    # The package lists fetched first, here: Ubuntu's own refresh (apt-daily)
    # runs at a random time and at most once a day.
    apt-get -q update || true
    "${APT_DAILY}" install || status=$?
    restart_replaced cloudflared
    return "${status}"
}

restart_if_needed() {
    if [[ -f "${REBOOT_REQUIRED}" ]]; then
        say "An update needs a restart: restarting."
        systemctl reboot
    else
        say "No restart needed."
    fi
}

usage() {
    sed -n '/^#   server-night/,/^# and,/p' "${SELF}" | sed 's/^# \{0,3\}//'
    exit 2
}

main() {
    local command="${1:-}"
    [[ -n "${command}" ]] || usage
    shift
    while (( $# )); do
        case "$1" in
            --name) NAME="${2:-}"; shift 2 ;;
            --timezone) ZONE="${2:-}"; shift 2 ;;
            --lock) LOCK="${2:-}"; shift 2 ;;
            --wait) WAIT_SECONDS="${2:-}"; shift 2 ;;
            *) usage ;;
        esac
    done

    case "${command}" in
        updates) updates; return ;;
        restart-if-needed) restart_if_needed; return ;;
        install|remove) ;;
        *) usage ;;
    esac

    [[ "${EUID}" -eq 0 ]] || fail "run it as root (sudo)."
    [[ "${NAME}" =~ ^[a-z0-9][a-z0-9-]{0,40}$ ]] || fail "--name: lower-case letters, digits and dashes."
    [[ "${WAIT_SECONDS}" =~ ^[0-9]+$ ]] || fail "--wait: seconds."
    [[ -z "${LOCK}" || "${LOCK}" == /* ]] || fail "--lock: a full path."
    command -v apt-get >/dev/null || fail "this is for Ubuntu and Debian (apt) only."
    paths
    if [[ "${command}" == "install" ]]; then
        install_night
    else
        remove_night
    fi
}

main "$@"
