/* Keeps the settings page's active tab in sync with the URL fragment, so a
 * reload or a shared link reopens the same tab.
 *
 * Kept in a static file rather than an inline <script> because the deployed
 * Content-Security-Policy allows script-src 'self' only: an inline block is
 * silently blocked by the browser behind nginx, while still working in local
 * template-render tests.
 */
document.addEventListener('DOMContentLoaded', function () {
    var tabTriggerList = [].slice.call(
        document.querySelectorAll('#settings-tab button[data-bs-toggle="pill"]')
    );
    if (!tabTriggerList.length) {
        return;
    }

    var currentHash = window.location.hash;
    var matchedTab = currentHash
        ? document.querySelector('#settings-tab button[data-bs-target="' + currentHash + '"]')
        : null;
    // A link to something inside a tab (#backup-restore) opens the tab it
    // sits in and scrolls to it, rather than leaving it hidden behind General.
    var inner = null;
    if (!matchedTab && currentHash) {
        try {
            inner = document.querySelector(currentHash);
        } catch (error) {
            inner = null;
        }
        var pane = inner ? inner.closest('.tab-pane') : null;
        matchedTab = pane
            ? document.querySelector('#settings-tab button[data-bs-target="#' + pane.id + '"]')
            : null;
    }
    if (matchedTab && window.bootstrap && window.bootstrap.Tab) {
        window.bootstrap.Tab.getOrCreateInstance(matchedTab).show();
        if (inner) {
            inner.scrollIntoView();
        }
    }

    tabTriggerList.forEach(function (tabTrigger) {
        tabTrigger.addEventListener('shown.bs.tab', function (event) {
            var target = event.target.getAttribute('data-bs-target');
            if (target) {
                history.replaceState(null, '', target);
            }
        });
    });
});
