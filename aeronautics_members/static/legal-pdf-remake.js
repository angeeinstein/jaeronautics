/* "Make all PDFs again" on Admin -> Legal Texts.
 *
 * Asks the server for one PDF at a time -- first to remove the kept ones,
 * then each version -- and ticks its line off with the answer: a spinner while
 * it is being made, a tick and its size when done, a cross and the reason when
 * not. One short request each, so none runs into a time limit, and a text that
 * cannot be laid out does not stop the others.
 *
 * In a static file because the CSP allows no inline script. Text from the
 * server is set with textContent only.
 */
(function () {
    var form = document.querySelector('form[data-pdf-remake]');
    var list = document.getElementById('legal-pdfs-progress');
    if (!form || !list || !window.fetch || !window.FormData) {
        return;  // the form posts and the server makes them all in one go
    }
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
        span.className = state === 'running' ? 'spinner-border spinner-border-sm' : 'checklist-dot';
        return span;
    }

    function show(row, state, detail) {
        row.className = 'checklist-item checklist-' + state;
        var box = row.querySelector('.checklist-icon');
        box.textContent = '';
        box.appendChild(icon(state));
        row.querySelector('.checklist-detail').textContent = detail || '';
    }

    form.addEventListener('submit', function (event) {
        event.preventDefault();
        var button = form.querySelector('button[type="submit"]');
        if (button.disabled) {
            return;
        }
        button.disabled = true;
        list.hidden = false;
        var token = form.querySelector('input[name="csrf_token"]').value;
        var rows = Array.prototype.slice.call(list.querySelectorAll('[data-item]'));
        rows.forEach(function (row) { show(row, 'pending', ''); });

        function next(index) {
            if (index >= rows.length) {
                button.disabled = false;
                return;
            }
            var row = rows[index];
            show(row, 'running', '');
            var body = new FormData();
            body.append('csrf_token', token);
            body.append('item', row.getAttribute('data-item'));
            fetch(form.action, {
                method: 'POST', body: body, credentials: 'same-origin', cache: 'no-store',
                headers: { 'Accept': 'application/json' }
            }).then(function (response) {
                return response.json();
            }).then(function (answer) {
                show(row, answer.state === 'ok' ? 'ok' : 'failed', answer.detail);
            }).catch(function () {
                show(row, 'failed', list.getAttribute('data-failed-label'));
            }).then(function () {
                next(index + 1);
            });
        }
        next(0);
    });
})();
