// Forms that hand over to a page that takes a moment -- Stripe's payment page,
// its billing portal -- are marked with data-busy-text. Each is sent once, and
// the button says that something is happening.
//
// Without this a second click while Stripe was being asked for the page sent
// the signup again, and its answer -- "an account with this email address
// already exists" -- could land in place of the payment page.
(function () {
    function submitButtons(form) {
        return form.querySelectorAll('button[type="submit"], input[type="submit"]');
    }

    function showBusy(form) {
        var text = form.getAttribute('data-busy-text');
        submitButtons(form).forEach(function (button) {
            button.disabled = true;
            if (button.tagName === 'INPUT') {
                button.setAttribute('data-idle-value', button.value);
                button.value = text;
                return;
            }
            button.setAttribute('data-idle-html', button.innerHTML);
            var spinner = document.createElement('span');
            spinner.className = 'spinner-border spinner-border-sm me-2';
            spinner.setAttribute('aria-hidden', 'true');
            button.textContent = '';
            button.appendChild(spinner);
            button.appendChild(document.createTextNode(text));
        });
    }

    function showIdle(form) {
        form.removeAttribute('data-submitting');
        submitButtons(form).forEach(function (button) {
            button.disabled = false;
            if (button.hasAttribute('data-idle-value')) {
                button.value = button.getAttribute('data-idle-value');
            } else if (button.hasAttribute('data-idle-html')) {
                button.innerHTML = button.getAttribute('data-idle-html');
            }
        });
    }

    document.addEventListener('submit', function (event) {
        var form = event.target;
        if (!form.matches || !form.matches('form[data-busy-text]')) {
            return;
        }
        if (form.hasAttribute('data-submitting')) {
            event.preventDefault();
            return;
        }
        form.setAttribute('data-submitting', '');
        // After the browser has taken the submission: a button disabled first
        // would be left out of what is sent.
        window.setTimeout(function () { showBusy(form); }, 0);
    });

    // Back from Stripe with the browser's back button, the page can come from
    // its cache exactly as it was left -- button disabled, still "busy".
    window.addEventListener('pageshow', function (event) {
        if (event.persisted) {
            document.querySelectorAll('form[data-busy-text]').forEach(showIdle);
        }
    });
})();
