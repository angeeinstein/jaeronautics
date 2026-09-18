#!/usr/bin/env bash
#
# Privileged half of the admin-page update button.
#
# The web application runs unprivileged and cannot install an update. It writes
# a request file; this script -- run as root by a systemd timer -- notices the
# request and performs the update.
#
# The request deliberately carries no instructions. What gets deployed comes
# from the installer's own state (remote and branch), so the unprivileged side
# can ask for an update but cannot choose what "an update" means. That is the
# whole reason this is not simply a sudoers rule.
#
# Installed by install.sh; not intended to be run by hand.

set -Eeuo pipefail

STATE_DIR="${UPDATE_STATE_DIR:-/var/lib/jaeronautics/updates}"
REQUEST_FILE="${STATE_DIR}/request.json"
CLAIM_FILE="${STATE_DIR}/request.processing.json"
STATUS_FILE="${STATE_DIR}/status.json"
LOG_FILE="${STATE_DIR}/last-run.log"
UPDATE_COMMAND="${UPDATE_COMMAND:-/usr/local/bin/update}"
INSTALL_DIR="${INSTALL_DIR:-/var/www/jaeronautics}"
# Keep the tail short: it is rendered on a web page, and a full install log is
# both large and more likely to contain incidental detail.
LOG_TAIL_LINES="${LOG_TAIL_LINES:-40}"
# Group allowed to read the log, so the web application can show progress while
# the update is still running. Falls back to root-only if unset.
LOG_GROUP="${LOG_GROUP:-}"

json_escape() {
    # Escape a string for embedding in JSON without needing python or jq.
    local s=${1//\\/\\\\}
    s=${s//\"/\\\"}
    s=${s//$'\n'/\\n}
    s=${s//$'\r'/}
    s=${s//$'\t'/\\t}
    printf '%s' "${s}"
}

current_revision() {
    # This runs as root against a checkout owned by the application user, which
    # git refuses by default ("detected dubious ownership"). Reading the commit
    # id is harmless, so mark the path safe for this invocation only rather than
    # changing git's global configuration.
    git -c "safe.directory=${INSTALL_DIR}" -C "${INSTALL_DIR}" rev-parse HEAD 2>/dev/null || printf 'unknown'
}

read_status_number() {
    # Pull a numeric field out of the previous status file, or print 0.
    local value
    value="$(sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\([0-9]\{1,\}\).*/\1/p" "${STATUS_FILE}" 2>/dev/null | head -n1)"
    printf '%s' "${value:-0}"
}

count_steps() {
    grep -c '^\[STEP\]' "${LOG_FILE}" 2>/dev/null || printf '0'
}

write_status() {
    local state="$1" exit_code="$2" started_at="$3" finished_at="$4"
    local revision_before="$5" revision_after="$6" log_tail="$7"
    local steps_done="${8:-0}" steps_expected="${9:-0}"
    local requested_at="${REQUESTED_AT:-}" requested_by="${REQUESTED_BY:-null}"

    local tmp="${STATUS_FILE}.tmp"
    cat > "${tmp}" <<EOF
{
  "state": "$(json_escape "${state}")",
  "requested_at": "$(json_escape "${requested_at}")",
  "requested_by_user_id": ${requested_by:-null},
  "started_at": "$(json_escape "${started_at}")",
  "finished_at": "$(json_escape "${finished_at}")",
  "exit_code": ${exit_code},
  "revision_before": "$(json_escape "${revision_before}")",
  "revision_after": "$(json_escape "${revision_after}")",
  "steps_done": ${steps_done},
  "steps_expected": ${steps_expected},
  "log_tail": "$(json_escape "${log_tail}")"
}
EOF
    # World-readable so the unprivileged web process can show progress.
    chmod 644 "${tmp}"
    mv -f "${tmp}" "${STATUS_FILE}"
}

read_request_field() {
    # Minimal field read; the request file is written by us and is flat JSON.
    sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\{0,1\}\([^\",}]*\)\"\{0,1\}.*/\1/p" "${CLAIM_FILE}" | head -n1
}

main() {
    [[ -d "${STATE_DIR}" ]] || exit 0
    # Nothing requested: this is the normal case on almost every timer tick.
    [[ -f "${REQUEST_FILE}" ]] || exit 0

    # Claim atomically, so a slow update cannot be started twice if the timer
    # fires again while it is still going.
    if ! mv -n "${REQUEST_FILE}" "${CLAIM_FILE}" 2>/dev/null || [[ ! -f "${CLAIM_FILE}" ]]; then
        exit 0
    fi

    REQUESTED_AT="$(read_request_field requested_at)"
    REQUESTED_BY="$(read_request_field requested_by_user_id)"
    [[ "${REQUESTED_BY}" =~ ^[0-9]+$ ]] || REQUESTED_BY="null"

    local started_at revision_before expected_steps
    started_at="$(date --iso-8601=seconds)"
    revision_before="$(current_revision)"
    # How many steps the last successful update took. Using the real previous
    # run rather than a hardcoded guess keeps the bar honest when the number of
    # steps changes with the configuration (local database, TLS, and so on).
    expected_steps="$(read_status_number steps_expected)"
    write_status "running" 0 "${started_at}" "" "${revision_before}" "" "" 0 "${expected_steps}"

    # Create the log and make it readable before the update starts, so the admin
    # page can tail it live rather than only seeing output once everything is
    # over. stdbuf keeps the installer's output unbuffered for the same reason.
    : > "${LOG_FILE}"
    if [[ -n "${LOG_GROUP}" ]]; then
        chgrp "${LOG_GROUP}" "${LOG_FILE}" 2>/dev/null || true
        chmod 640 "${LOG_FILE}" 2>/dev/null || true
    else
        chmod 600 "${LOG_FILE}" 2>/dev/null || true
    fi

    local exit_code=0
    if ! stdbuf -oL -eL "${UPDATE_COMMAND}" >>"${LOG_FILE}" 2>&1; then
        exit_code=$?
    fi

    local finished_at revision_after log_tail state
    finished_at="$(date --iso-8601=seconds)"
    revision_after="$(current_revision)"
    log_tail="$(tail -n "${LOG_TAIL_LINES}" "${LOG_FILE}" 2>/dev/null || printf '')"
    if [[ ${exit_code} -eq 0 ]]; then
        state="completed"
    else
        state="failed"
    fi

    local steps_done
    steps_done="$(count_steps)"
    write_status "${state}" "${exit_code}" "${started_at}" "${finished_at}" \
        "${revision_before}" "${revision_after}" "${log_tail}" "${steps_done}" "${steps_done}"
    rm -f "${CLAIM_FILE}"
}

main "$@"
