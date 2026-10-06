/* Links to a legal text's PDF (marked data-legal-file): make it first, then open it.
 *
 * Making a PDF takes a few seconds the first time. Followed straight away,
 * the link opened an empty tab that only filled once it was done -- or, on a
 * phone, seemed to do nothing. So the link asks the server to make the PDF
 * (?prepare=1) and says so meanwhile, with a spinner, and opens it once it is
 * there. The address it opens carries the PDF's hash, so a browser or phone
 * holding an earlier copy cannot show that one instead.
 *
 * A link meant for a new tab opens one when the PDF is ready; where the
 * browser does not allow that so long after the click (Safari), the PDF opens
 * in this tab instead. Without this script the links work as plain links.
 */
(function () {
    if (!window.fetch || !window.URL) {
        return;
    }
    var body = document.body;
    var busyLabel = body.getAttribute('data-pdf-busy-label') || 'Making the PDF…';
    var failedLabel = body.getAttribute('data-pdf-failed-label') || 'The PDF could not be made just now.';

    function setBusy(link, busy) {
        if (busy) {
            link.setAttribute('aria-busy', 'true');
            link.setAttribute('data-idle-html', link.innerHTML);
            var spinner = document.createElement('span');
            spinner.className = 'spinner-border spinner-border-sm me-1';
            spinner.setAttribute('aria-hidden', 'true');
            link.textContent = '';
            link.appendChild(spinner);
            link.appendChild(document.createTextNode(busyLabel));
        } else if (link.hasAttribute('data-idle-html')) {
            link.innerHTML = link.getAttribute('data-idle-html');
            link.removeAttribute('data-idle-html');
            link.removeAttribute('aria-busy');
        }
    }

    function sayFailed(link, message) {
        var note = link.nextElementSibling;
        if (!note || !note.hasAttribute('data-pdf-note')) {
            note = document.createElement('span');
            note.setAttribute('data-pdf-note', '');
            note.className = 'text-danger small ms-2';
            note.setAttribute('role', 'alert');
            link.parentNode.insertBefore(note, link.nextSibling);
        }
        note.textContent = message || failedLabel;
    }

    function open(url, newTab) {
        if (newTab) {
            var tab = window.open(url, '_blank');
            if (tab) {
                tab.opener = null;
                return;
            }
        }
        window.location.assign(url);
    }

    document.addEventListener('click', function (event) {
        var link = event.target.closest ? event.target.closest('a[data-legal-file]') : null;
        if (!link || event.defaultPrevented || event.button !== 0
            || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
            return;
        }
        event.preventDefault();
        if (link.getAttribute('aria-busy') === 'true') {
            return;
        }
        var note = link.nextElementSibling;
        if (note && note.hasAttribute('data-pdf-note')) {
            note.remove();
        }
        var address = new URL(link.href, window.location.href);
        address.searchParams.set('prepare', '1');
        setBusy(link, true);
        fetch(address.toString(), {
            credentials: 'same-origin', cache: 'no-store', headers: { 'Accept': 'application/json' }
        }).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (answer) {
                if (!response.ok || !answer.url) {
                    throw new Error(answer.error || '');
                }
                return answer.url;
            });
        }).then(function (url) {
            setBusy(link, false);
            open(url, link.target === '_blank');
        }).catch(function (error) {
            setBusy(link, false);
            sayFailed(link, error && error.message);
        });
    });

    // Back from the PDF, a page restored from the browser's cache still shows
    // the spinner it was left with.
    window.addEventListener('pageshow', function () {
        document.querySelectorAll('a[data-legal-file][aria-busy="true"]').forEach(function (link) {
            setBusy(link, false);
        });
    });
})();
