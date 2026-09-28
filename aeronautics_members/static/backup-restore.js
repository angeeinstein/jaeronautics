/* Live updates for the Backup & Restore card.
 *
 * Two things poll here:
 *  - a backup being made: its steps and log, until it finishes, then the page
 *    reloads to list the new file;
 *  - the checklist after resuming background jobs: each poll runs the next
 *    outstanding check on the server, so every line turns from a spinner to
 *    its result as it really finishes.
 *
 * In a static file because the CSP allows no inline script. Text from the
 * server is set with textContent only.
 */
(function () {
    var SVG = 'http://www.w3.org/2000/svg';

    function icon(state) {
        if (state === 'ok' || state === 'failed') {
            var svg = document.createElementNS(SVG, 'svg');
            svg.setAttribute('class', state === 'ok' ? 'success-check' : 'checklist-cross');
            svg.setAttribute('width', '22');
            svg.setAttribute('height', '22');
            svg.setAttribute('viewBox', '0 0 26 26');
            svg.setAttribute('aria-hidden', 'true');
            var path = document.createElementNS(SVG, 'path');
            path.setAttribute('d', state === 'ok' ? 'M4 13l6.5 6.5L22 6' : 'M6 6l14 14M20 6L6 20');
            svg.appendChild(path);
            return svg;
        }
        var span = document.createElement('span');
        span.setAttribute('aria-hidden', 'true');
        span.className = (state === 'running' || state === 'waiting') ? 'spinner-border spinner-border-sm' : 'checklist-dot';
        return span;
    }

    function row(label, state, detail) {
        var li = document.createElement('li');
        li.className = 'checklist-item checklist-' + state;
        var iconBox = document.createElement('span');
        iconBox.className = 'checklist-icon';
        iconBox.appendChild(icon(state));
        var text = document.createElement('span');
        var labelNode = document.createElement('span');
        labelNode.className = 'checklist-label';
        labelNode.textContent = label;
        var detailNode = document.createElement('span');
        detailNode.className = 'checklist-detail';
        detailNode.textContent = detail || '';
        text.appendChild(labelNode);
        text.appendChild(detailNode);
        li.appendChild(iconBox);
        li.appendChild(text);
        return li;
    }

    // Replace a line only when its state changed, so a finished tick draws
    // itself once and does not restart on every poll.
    function render(list, items) {
        items.forEach(function (item, index) {
            var existing = list.children[index];
            var fresh = row(item.label, item.state, item.detail);
            if (!existing) {
                list.appendChild(fresh);
            } else if (existing.className !== fresh.className) {
                list.replaceChild(fresh, existing);
            } else {
                existing.querySelector('.checklist-detail').textContent = item.detail || '';
            }
        });
    }

    function getJson(url) {
        return fetch(url, { credentials: 'same-origin', cache: 'no-store' }).then(function (response) {
            if (!response.ok) {
                throw new Error('status ' + response.status);
            }
            return response.json();
        });
    }

    function watchChecklist() {
        var list = document.getElementById('resume-checklist');
        if (!list) {
            return;
        }
        var url = list.getAttribute('data-url');
        function poll() {
            getJson(url).then(function (data) {
                render(list, data.items);
                // Quickly while checks run; then slowly, for the timers.
                window.setTimeout(poll, data.done ? 30000 : 2500);
            }).catch(function () {
                window.setTimeout(poll, 5000);
            });
        }
        poll();
    }

    function watchBackup() {
        var panel = document.getElementById('backup-progress');
        if (!panel || panel.getAttribute('data-state') !== 'running') {
            return;
        }
        var url = panel.getAttribute('data-status-url');
        var list = panel.querySelector('.checklist');
        var log = document.getElementById('backup-progress-log');
        var states = { done: 'ok', running: 'running', failed: 'failed' };
        function poll() {
            getJson(url).then(function (data) {
                render(list, (data.steps || []).map(function (step) {
                    return { label: step.label, state: states[step.state] || 'pending', detail: '' };
                }));
                if (log) {
                    log.textContent = (data.log || []).join('\n');
                    log.scrollTop = log.scrollHeight;
                }
                if (data.state === 'running') {
                    window.setTimeout(poll, 1500);
                } else {
                    window.setTimeout(function () { window.location.reload(); }, 1200);
                }
            }).catch(function () {
                window.setTimeout(poll, 3000);
            });
        }
        poll();
    }

    document.addEventListener('DOMContentLoaded', function () {
        watchChecklist();
        watchBackup();
    });
})();
