/* Reading a legal text without leaving the form one is filling in.
 *
 * Links marked data-legal-dialog open the text in a window over the page,
 * fetched from the text's own page (?part=body). Without JavaScript -- or if
 * the fetch fails -- the link simply opens the page in a new tab, as its
 * target="_blank" says. Either way nothing typed into the form is lost.
 *
 * A static file because the Content-Security-Policy allows script-src 'self'.
 */
(function () {
    var dialog = null;

    function ensureDialog() {
        if (dialog) {
            return dialog;
        }
        dialog = document.createElement('dialog');
        dialog.className = 'legal-dialog';
        dialog.innerHTML =
            '<div class="legal-dialog-bar">' +
            '<a class="small" target="_blank" rel="noopener" data-legal-open>' + (document.body.getAttribute('data-legal-open-label') || 'Open in a new tab') + '</a>' +
            '<button type="button" class="btn btn-primary btn-sm" data-legal-close>' + (document.body.getAttribute('data-legal-close-label') || 'Close') + '</button>' +
            '</div><div class="legal-dialog-body" tabindex="0"></div>';
        dialog.querySelector('[data-legal-close]').addEventListener('click', function () { dialog.close(); });
        dialog.addEventListener('click', function (event) {
            if (event.target === dialog) {
                dialog.close();  // a click on the dimmed backdrop
            }
        });
        document.body.appendChild(dialog);
        return dialog;
    }

    function show(url) {
        return fetch(url + (url.indexOf('?') === -1 ? '?' : '&') + 'part=body', { credentials: 'same-origin' })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error('HTTP ' + response.status);
                }
                return response.text();
            })
            .then(function (html) {
                var box = ensureDialog();
                var body = box.querySelector('.legal-dialog-body');
                body.innerHTML = html;
                box.querySelector('[data-legal-open]').setAttribute('href', url);
                if (!box.open) {
                    box.showModal();
                }
                body.scrollTop = 0;
                body.focus();
            })
            .catch(function () {
                window.open(url, '_blank', 'noopener');
            });
    }

    document.addEventListener('click', function (event) {
        if (typeof HTMLDialogElement === 'undefined' || !window.fetch) {
            return;
        }
        var link = event.target.closest('a[data-legal-dialog]');
        if (link) {
            event.preventDefault();
            show(link.getAttribute('href'));
            return;
        }
        // A link inside the text (the German version, another text) must not
        // take the page away from the form either: another legal text -- or a
        // team's rules -- loads in the window, anything else, the PDF among
        // them, opens in a new tab.
        var inner = event.target.closest('.legal-dialog-body a[href]');
        if (inner) {
            var href = inner.getAttribute('href');
            if (href.charAt(0) === '#') {
                return;
            }
            event.preventDefault();
            var legalText = href.indexOf('/legal/') === 0 || /^\/teams\/[^/]+\/rules(\/|\?|$)/.test(href);
            if (legalText && !inner.hasAttribute('data-legal-file')) {
                show(href);
            } else {
                window.open(inner.href, '_blank', 'noopener');
            }
        }
    });
}());
