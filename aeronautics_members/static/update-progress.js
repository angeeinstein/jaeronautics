/* Live progress for a running update.
 *
 * The update restarts the very server serving this page, so the page cannot be
 * kept open on a socket. It polls the admin status endpoint instead, tolerates
 * the gap where the application is restarting, and reloads once the update has
 * finished so the new version's page is shown.
 *
 * In a static file rather than an inline <script> because the deployed CSP
 * allows script-src 'self' only.
 */
document.addEventListener('DOMContentLoaded', function () {
    var panel = document.getElementById('update-progress');
    if (!panel) {
        return;
    }

    var statusUrl = panel.getAttribute('data-status-url');
    var logNode = document.getElementById('update-progress-log');
    var stateNode = document.getElementById('update-progress-state');
    var POLL_MS = 2000;
    var sawRunning = false;

    function setState(text) {
        if (stateNode) {
            stateNode.textContent = text;
        }
    }

    var barNode = document.getElementById('update-progress-bar');
    var stepNode = document.getElementById('update-progress-step');

    function renderProgress(progress) {
        if (!progress) {
            return;
        }
        if (stepNode && progress.current_step) {
            stepNode.textContent = progress.current_step;
        }
        if (!barNode) {
            return;
        }
        if (typeof progress.percent === 'number') {
            // A real percentage: switch from the moving stripes to a bar that
            // means something. Setting .style is CSSOM, which style-src allows.
            barNode.classList.remove('w-100', 'progress-bar-animated');
            barNode.style.width = progress.percent + '%';
            barNode.textContent = progress.percent + '%';
            barNode.parentNode.setAttribute('aria-valuenow', String(progress.percent));
        } else if (progress.steps_done) {
            barNode.textContent = 'Step ' + progress.steps_done;
        }
    }

    function render(data) {
        renderProgress(data.progress);
        if (logNode && data.last_run && data.last_run.log_tail) {
            logNode.textContent = data.last_run.log_tail;
            // Keep the newest output in view.
            logNode.scrollTop = logNode.scrollHeight;
        }
        if (data.in_progress) {
            sawRunning = true;
            setState(panel.getAttribute('data-label-running') || 'Update running...');
            return true;
        }
        if (sawRunning) {
            // Finished during this visit: show the completed page, and the new
            // version number along with it.
            setState(panel.getAttribute('data-label-finished') || 'Finished. Reloading...');
            window.setTimeout(function () { window.location.reload(); }, 1500);
            return false;
        }
        return false;
    }

    function poll() {
        fetch(statusUrl, { credentials: 'same-origin', cache: 'no-store' })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error('status ' + response.status);
                }
                return response.json();
            })
            .then(function (data) {
                if (render(data)) {
                    window.setTimeout(poll, POLL_MS);
                }
            })
            .catch(function () {
                // Expected while the application restarts mid-update; keep trying.
                setState(panel.getAttribute('data-label-restarting') || 'Server restarting...');
                window.setTimeout(poll, POLL_MS);
            });
    }

    poll();
});
