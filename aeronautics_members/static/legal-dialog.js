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

    document.addEventListener('click', function (event) {
        var link = event.target.closest('a[data-legal-dialog]');
        if (!link || typeof HTMLDialogElement === 'undefined' || !window.fetch) {
            return;
        }
        event.preventDefault();
        var url = link.getAttribute('href');
        fetch(url + (url.indexOf('?') === -1 ? '?' : '&') + 'part=body', { credentials: 'same-origin' })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error('HTTP ' + response.status);
                }
                return response.text();
            })
            .then(function (html) {
                var box = ensureDialog();
                box.querySelector('.legal-dialog-body').innerHTML = html;
                box.querySelector('[data-legal-open]').setAttribute('href', url);
                box.showModal();
                box.querySelector('.legal-dialog-body').scrollTop = 0;
                box.querySelector('.legal-dialog-body').focus();
            })
            .catch(function () {
                window.open(url, '_blank', 'noopener');
            });
    });
}());
