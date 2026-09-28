/* Fills the "delete mail account" confirmation modal from the trigger button.
 *
 * Kept in a static file rather than an inline <script> because the deployed
 * Content-Security-Policy allows script-src 'self' only: an inline block is
 * silently blocked by the browser behind nginx, while still working in local
 * template-render tests.
 */
document.addEventListener('DOMContentLoaded', function () {
    var deleteModal = document.getElementById('deleteMailAccountModal');
    if (!deleteModal) {
        return;
    }

    deleteModal.addEventListener('show.bs.modal', function (event) {
        var trigger = event.relatedTarget;
        if (!trigger) {
            return;
        }

        var deleteUrl = trigger.getAttribute('data-delete-url') || '#';
        var accountKey = trigger.getAttribute('data-account-key') || '';
        var deleteForm = document.getElementById('delete-mail-account-form');
        var accountName = document.getElementById('delete-mail-account-name');

        if (deleteForm) {
            deleteForm.setAttribute('action', deleteUrl);
        }
        if (accountName) {
            accountName.textContent = accountKey;
        }
    });
});
