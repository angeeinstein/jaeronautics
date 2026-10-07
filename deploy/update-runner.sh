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
LOCK_FILE="${STATE_DIR}/runner.lock"
UPDATE_COMMAND="${UPDATE_COMMAND:-/usr/local/bin/update}"
ROLLBACK_FILE="${ROLLBACK_FILE:-/etc/jaeronautics/rollback.conf}"
INSTALL_DIR="${INSTALL_DIR:-/var/www/jaeronautics}"
# Keep the tail short: it is rendered on a web page, and a full install log is
# both large and more likely to contain incidental detail.
LOG_TAIL_LINES="${LOG_TAIL_LINES:-40}"
# An update that hangs -- an unresponsive package mirror, a held dpkg lock --
# would otherwise stay "running" forever, and the admin page refuses to start a
# new one while that is true. So the button would disable itself permanently
# and only a shell could clear it. Bound the run and record a failure instead.
UPDATE_TIMEOUT="${UPDATE_TIMEOUT:-2700}"
# Group allowed to read the log, so the web application can show progress while
# the update is still running. Falls back to root-only if unset.
LOG_GROUP="${LOG_GROUP:-}"

# Which boot this run belongs to. A "running" status from an earlier boot is a
# run the reboot ended, whatever the file still says.
BOOT_ID="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || printf '')"

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
    # grep -c prints "0" and exits 1 when nothing matches; "|| printf 0" then
    # added a second 0 on its own line, and the status file stopped being JSON.
    local count
    count="$(grep -c '^\[STEP\]' "${LOG_FILE}" 2>/dev/null)" || true
    printf '%s' "${count:-0}"
}

write_status() {
    local state="$1" exit_code="$2" started_at="$3" finished_at="$4"
    local revision_before="$5" revision_after="$6" log_tail="$7"
    local steps_done="${8:-0}" steps_expected="${9:-0}"
    local requested_at="${REQUESTED_AT:-}" requested_by="${REQUESTED_BY:-null}" action="${ACTION:-update}"

    local tmp="${STATUS_FILE}.tmp"
    cat > "${tmp}" <<EOF
{
  "state": "$(json_escape "${state}")",
  "action": "$(json_escape "${action}")",
  "requested_at": "$(json_escape "${requested_at}")",
  "requested_by_user_id": ${requested_by:-null},
  "started_at": "$(json_escape "${started_at}")",
  "finished_at": "$(json_escape "${finished_at}")",
  "exit_code": ${exit_code},
  "revision_before": "$(json_escape "${revision_before}")",
  "revision_after": "$(json_escape "${revision_after}")",
  "steps_done": ${steps_done},
  "steps_expected": ${steps_expected},
  "boot_id": "$(json_escape "${BOOT_ID}")",
  "runner_pid": $$,
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

read_status_text() {
    sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "${STATUS_FILE}" 2>/dev/null | head -n1
}

# Written when this run is stopped before it can finish on its own: systemd's
# time limit, a service stop, a reboot. Without it the status file keeps
# saying "running" forever -- the admin page then shows a frozen update and
# refuses to start another, and neither a reboot nor a successful update from
# the shell changes that file.
on_interrupt() {
    trap - TERM INT HUP
    local log_tail steps_done
    if [[ -n "${UPDATE_PID:-}" ]]; then
        # timeout passes this on to the update it runs.
        kill -TERM "${UPDATE_PID}" 2>/dev/null || true
        wait "${UPDATE_PID}" 2>/dev/null || true
    fi
    printf '\n[ERROR] The update was stopped before it finished (%s).\n' "$1" >>"${LOG_FILE}" 2>/dev/null || true
    printf '[ERROR] Run "update" from a shell to finish it and see where it stops.\n' >>"${LOG_FILE}" 2>/dev/null || true
    log_tail="$(tail -n "${LOG_TAIL_LINES}" "${LOG_FILE}" 2>/dev/null || printf '')"
    steps_done="$(count_steps)"
    write_status "failed" 143 "${STARTED_AT:-}" "$(date --iso-8601=seconds)" \
        "${REVISION_BEFORE:-}" "$(current_revision)" "${log_tail}" "${steps_done}" "${EXPECTED_STEPS:-0}"
    rm -f "${CLAIM_FILE}"
    exit 143
}

# A claim left by a run that died without its trap (SIGKILL, power loss):
# holding the lock proves no run is still going, so the claim is stale. Say
# so in the status the page reads, and clear the way for the next request.
recover_from_a_dead_run() {
    [[ -f "${CLAIM_FILE}" ]] || return 0
    if [[ "$(read_status_text state)" == "running" ]]; then
        local log_tail
        printf '\n[ERROR] This update was interrupted and did not finish.\n' >>"${LOG_FILE}" 2>/dev/null || true
        log_tail="$(tail -n "${LOG_TAIL_LINES}" "${LOG_FILE}" 2>/dev/null || printf '')"
        write_status "failed" 143 "$(read_status_text started_at)" "$(date --iso-8601=seconds)" \
            "$(read_status_text revision_before)" "$(current_revision)" "${log_tail}" "$(count_steps)" 0
    fi
    rm -f "${CLAIM_FILE}"
}

main() {
    [[ -d "${STATE_DIR}" ]] || exit 0

    # One run at a time, and a way to tell a live run from a dead one: the
    # lock goes with the process, however it ends.
    exec 9>"${LOCK_FILE}"
    flock -n 9 || exit 0
    recover_from_a_dead_run

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

    # The request may ask for a rollback instead of an update, but that is the
    # whole of its influence: which revision to return to comes from this side,
    # from the rollback point install.sh recorded. Anything unrecognised is
    # treated as an update rather than trusted.
    local command_args=()
    ACTION="update"
    if [[ "$(read_request_field action)" == "rollback" ]]; then
        ACTION="rollback"
        command_args=(--rollback)
    fi

    local started_at revision_before expected_steps
    started_at="$(date --iso-8601=seconds)"
    revision_before="$(current_revision)"
    STARTED_AT="${started_at}"
    REVISION_BEFORE="${revision_before}"
    # How many steps the last successful update took. Using the real previous
    # run rather than a hardcoded guess keeps the bar honest when the number of
    # steps changes with the configuration (local database, TLS, and so on).
    expected_steps="$(read_status_number steps_expected)"
    EXPECTED_STEPS="${expected_steps}"
    trap 'on_interrupt "stopped by a signal: SIGTERM -- a time limit, a service stop or a reboot"' TERM
    trap 'on_interrupt "stopped by a signal: SIGINT"' INT
    trap 'on_interrupt "stopped by a signal: SIGHUP"' HUP
    # Create the log and make it readable before the update starts, so the admin
    # page can follow it live rather than only seeing output once everything is
    # over. stdbuf keeps the installer's output unbuffered for the same reason.
    # Emptied before the status says "running", so the page never reads the
    # previous run's steps as this one's.
    : > "${LOG_FILE}"
    if [[ -n "${LOG_GROUP}" ]]; then
        chgrp "${LOG_GROUP}" "${LOG_FILE}" 2>/dev/null || true
        chmod 640 "${LOG_FILE}" 2>/dev/null || true
    else
        chmod 600 "${LOG_FILE}" 2>/dev/null || true
    fi
    write_status "running" 0 "${started_at}" "" "${revision_before}" "" "" 0 "${expected_steps}"


    # In the background and waited for, rather than in the foreground: bash
    # holds a trapped signal until a foreground command ends, so a stop request
    # would wait out the whole update. `wait` returns at once instead, and
    # on_interrupt stops the update itself.
    local exit_code=0
    stdbuf -oL -eL timeout --signal=TERM --kill-after=60 "${UPDATE_TIMEOUT}" \
        "${UPDATE_COMMAND}" "${command_args[@]+"${command_args[@]}"}" >>"${LOG_FILE}" 2>&1 &
    UPDATE_PID=$!
    if wait "${UPDATE_PID}"; then
        exit_code=0
    else
        exit_code=$?
    fi
    if [[ ${exit_code} -eq 124 ]]; then
        printf '\n[ERROR] The update was stopped after %s seconds without finishing.\n' \
            "${UPDATE_TIMEOUT}" >>"${LOG_FILE}"
        printf '[ERROR] It is usually a package mirror that stopped responding, or a held dpkg lock.\n' \
            >>"${LOG_FILE}"
        printf '[ERROR] Run "update" from a shell to see where it stops.\n' >>"${LOG_FILE}"
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
    trap - TERM INT HUP
    write_status "${state}" "${exit_code}" "${started_at}" "${finished_at}" \
        "${revision_before}" "${revision_after}" "${log_tail}" "${steps_done}" "${steps_done}"
    rm -f "${CLAIM_FILE}"
}

main "$@"
