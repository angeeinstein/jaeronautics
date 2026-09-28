// Filter forms that apply themselves: a dropdown as soon as it changes, the
// search box once typing has paused. Used on Accounts and Logs.
//
// The results are fetched and swapped in rather than the page being reloaded,
// so the search box keeps its focus and cursor while somebody is still typing.
// The address bar is updated to match, which keeps the result shareable and
// Back working. Without JavaScript the form is an ordinary GET form: Enter in
// the search box submits it, and a <noscript> Apply button covers the rest.
(function () {
    var SEARCH_DELAY_MS = 450;

    // Cloudflare's Email Address Obfuscation, if switched on for the zone,
    // hides every address in the HTML and decodes them with a script that
    // runs once, on page load. Results swapped in afterwards would stay as
    // "[email protected]", so they are decoded here the same way: the first
    // byte is the key, every following byte is XORed with it.
    function decodeProtectedEmails(root) {
        root.querySelectorAll('[data-cfemail]').forEach(function (element) {
            var hex = element.getAttribute('data-cfemail');
            var key = parseInt(hex.substr(0, 2), 16);
            var text = '';
            for (var i = 2; i < hex.length; i += 2) {
                text += String.fromCharCode(parseInt(hex.substr(i, 2), 16) ^ key);
            }
            try {
                text = decodeURIComponent(escape(text));
            } catch (error) {
                // Not UTF-8 encoded: keep the bytes as they are.
            }
            element.replaceWith(document.createTextNode(text));
        });
    }

    function setUp(form) {
        var results = document.querySelector(form.getAttribute('data-live-filter'));
        var status = form.querySelector('[data-live-filter-status]');
        if (!results) {
            return;
        }
        var timer = null;
        var inFlight = null;
        // Only what differs from the defaults goes in the address, so a
        // search for "anna" reads ?q=anna rather than listing every "all".
        function currentQuery() {
            var params = new URLSearchParams();
            new FormData(form).forEach(function (value, name) {
                if (value !== '' && value !== 'all') {
                    params.append(name, value);
                }
            });
            return params.toString();
        }

        var lastQuery = currentQuery();

        function showBusy(busy) {
            if (status) {
                status.hidden = !busy;
            }
            results.setAttribute('aria-busy', busy ? 'true' : 'false');
        }

        function apply() {
            window.clearTimeout(timer);
            var query = currentQuery();
            if (query === lastQuery) {
                return;
            }
            lastQuery = query;
            var url = form.getAttribute('action') + (query ? '?' + query : '');
            if (inFlight) {
                inFlight.abort();
            }
            inFlight = new AbortController();
            showBusy(true);
            fetch(url, {
                credentials: 'same-origin',
                headers: { 'Accept': 'text/html' },
                signal: inFlight.signal,
            }).then(function (response) {
                if (!response.ok || response.redirected) {
                    // Signed out, or refused: let the browser show why.
                    window.location.assign(url);
                    return null;
                }
                return response.text();
            }).then(function (html) {
                if (html === null) {
                    return;
                }
                var page = new DOMParser().parseFromString(html, 'text/html');
                var fresh = page.getElementById(results.id);
                if (!fresh) {
                    window.location.assign(url);
                    return;
                }
                decodeProtectedEmails(fresh);
                results.innerHTML = fresh.innerHTML;
                window.history.replaceState(null, '', url);
                showBusy(false);
            }).catch(function (error) {
                if (error.name === 'AbortError') {
                    return;
                }
                // Network trouble: fall back to loading the page itself.
                window.location.assign(url);
            });
        }

        form.addEventListener('change', function (event) {
            if (event.target.matches('select')) {
                apply();
            }
        });
        form.addEventListener('input', function (event) {
            if (event.target.matches('input[type="text"], input[type="search"]')) {
                window.clearTimeout(timer);
                timer = window.setTimeout(apply, SEARCH_DELAY_MS);
            }
        });
        // Enter in the search box: now, rather than after the pause.
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            apply();
        });
    }

    document.querySelectorAll('form[data-live-filter]').forEach(setUp);
})();
