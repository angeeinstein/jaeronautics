// The account list: a whole row opens the account, and on a narrow screen the
// right edge fades while the table has more to show sideways.
//
// Both are wired to the document rather than to the rows, because the live
// filter swaps the results in and out without a page load.
(function () {
    // A click anywhere on a row goes where its address link goes. Clicks on
    // the link itself, or on anything else that does something, are left to
    // it -- and so is selecting text, which ends in a click too.
    function rowTarget(event) {
        var row = event.target.closest && event.target.closest('tr[data-href]');
        if (!row || event.target.closest('a, button, input, select, textarea, label')) {
            return null;
        }
        var selection = window.getSelection ? String(window.getSelection()) : '';
        return selection ? null : row.getAttribute('data-href');
    }

    document.addEventListener('click', function (event) {
        var href = rowTarget(event);
        if (!href) {
            return;
        }
        if (event.ctrlKey || event.metaKey || event.shiftKey) {
            window.open(href, '_blank');
        } else {
            window.location.href = href;
        }
    });

    // Middle click opens a new tab, as it would on a link.
    document.addEventListener('auxclick', function (event) {
        var href = event.button === 1 ? rowTarget(event) : null;
        if (href) {
            window.open(href, '_blank');
        }
    });

    function updateFade(wrap) {
        var scroller = wrap.querySelector('.table-responsive');
        if (!scroller) {
            return;
        }
        var more = scroller.scrollLeft + scroller.clientWidth < scroller.scrollWidth - 2;
        wrap.classList.toggle('has-more-right', more);
    }

    function updateAll() {
        document.querySelectorAll('[data-scroll-fade]').forEach(updateFade);
    }

    // Scroll events do not bubble, but they can be caught on the way down.
    document.addEventListener('scroll', function (event) {
        var wrap = event.target.closest && event.target.closest('[data-scroll-fade]');
        if (wrap) {
            updateFade(wrap);
        }
    }, true);
    window.addEventListener('resize', updateAll);
    document.addEventListener('DOMContentLoaded', updateAll);
    new MutationObserver(updateAll).observe(document.documentElement, { childList: true, subtree: true });
    updateAll();
})();
