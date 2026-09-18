/* Asks for confirmation before submitting a form that declares data-confirm.
 *
 * In a static file rather than an inline <script> because the deployed
 * Content-Security-Policy allows script-src 'self' only; an inline handler is
 * silently blocked in the browser while still working in local render tests.
 *
 * This is a convenience, not a control: the server re-checks permissions on
 * every one of these actions regardless of what the browser did.
 */
document.addEventListener('DOMContentLoaded', function () {
    var forms = [].slice.call(document.querySelectorAll('form[data-confirm]'));
    forms.forEach(function (form) {
        form.addEventListener('submit', function (event) {
            var message = form.getAttribute('data-confirm');
            if (message && !window.confirm(message)) {
                event.preventDefault();
            }
        });
    });
});
