/* A second, deliberate click before a form does something that cannot be taken back.
 *
 * The first click does not submit: the button turns into its confirm version
 * ("Yes, record it"), with the explanation beneath it. It fills up for a moment
 * before it can be pressed, so a double click never confirms, and turns back
 * by itself after a while, or as soon as the form is edited.
 *
 * - form[data-confirm]: always asks.
 * - button[data-confirm]: asks only when that button submits the form, for a
 *   form with a harmless and a final button (approve / reject).
 * - form[data-confirm-if-changed="a,b"] with data-confirm-changed: asks only
 *   when one of those fields was changed, for a form whose ordinary save is
 *   harmless but a few of its fields are not (a fee).
 * - data-confirm-label, on the button or the form: the confirm button's text;
 *   otherwise the page's default from <body data-confirm-label>.
 *
 * A message may name a field as {name}; it is filled in with what was typed,
 * so the explanation shows the amount about to be recorded.
 *
 * In a static file rather than an inline <script> because the deployed
 * Content-Security-Policy allows script-src 'self' only.
 *
 * This is a convenience, not a control: the server re-checks permissions on
 * every one of these actions regardless of what the browser did.
 */
(function () {
    var ARMING_MS = 1000;
    var GIVE_UP_MS = 8000;

    function fill(message, form) {
        return message.replace(/\{([a-z_]+)\}/g, function (whole, name) {
            var field = form.elements[name];
            return field && 'value' in field ? field.value : whole;
        });
    }

    function changed(form, names) {
        return names.split(',').some(function (name) {
            var field = form.elements[name.trim()];
            if (!field) {
                return false;
            }
            if (field.tagName === 'SELECT') {
                return [].slice.call(field.options).some(function (option) {
                    return option.selected !== option.defaultSelected;
                });
            }
            if (field.type === 'checkbox' || field.type === 'radio') {
                return field.checked !== field.defaultChecked;
            }
            return field.value !== field.defaultValue;
        });
    }

    function messageFor(form, button) {
        var message = (button && button.getAttribute('data-confirm')) || form.getAttribute('data-confirm');
        var watched = form.getAttribute('data-confirm-if-changed');
        if (!message && watched && changed(form, watched)) {
            message = form.getAttribute('data-confirm-changed');
        }
        return message ? fill(message, form) : null;
    }

    function labelFor(form, button) {
        return (button && button.getAttribute('data-confirm-label'))
            || form.getAttribute('data-confirm-label')
            || document.body.getAttribute('data-confirm-label')
            || 'Confirm';
    }

    var armed = null;  // {form, button, label, hint, timers, ready}

    function disarm() {
        if (!armed) {
            return;
        }
        armed.timers.forEach(clearTimeout);
        armed.button.innerHTML = armed.label;
        armed.button.disabled = false;
        armed.button.classList.remove('confirm-arming', 'confirm-ready');
        if (armed.hint.parentNode) {
            armed.hint.parentNode.removeChild(armed.hint);
        }
        armed = null;
    }

    function arm(form, button, message) {
        disarm();
        var hint = document.createElement('div');
        hint.className = 'confirm-hint';
        hint.setAttribute('role', 'status');
        hint.textContent = message;
        armed = {form: form, button: button, label: button.innerHTML, hint: hint, timers: [], ready: false};
        button.textContent = labelFor(form, button);
        button.disabled = true;
        button.classList.add('confirm-arming');
        button.insertAdjacentElement('afterend', hint);
        var state = armed;
        state.timers.push(setTimeout(function () {
            state.ready = true;
            button.disabled = false;
            button.classList.remove('confirm-arming');
            button.classList.add('confirm-ready');
            button.focus();
        }, ARMING_MS));
        state.timers.push(setTimeout(function () {
            if (armed === state) {
                disarm();
            }
        }, GIVE_UP_MS));
    }

    document.addEventListener('DOMContentLoaded', function () {
        [].slice.call(document.querySelectorAll('form')).forEach(function (form) {
            form.addEventListener('submit', function (event) {
                var button = event.submitter
                    || form.querySelector('button[type="submit"], button:not([type]), input[type="submit"]');
                var message = button ? messageFor(form, button) : null;
                if (!message) {
                    return;
                }
                if (armed && armed.button === button && armed.ready) {
                    armed.timers.forEach(clearTimeout);
                    armed.hint.parentNode && armed.hint.parentNode.removeChild(armed.hint);
                    armed = null;
                    return;  // the second, deliberate click: on its way
                }
                event.preventDefault();
                if (!armed || armed.button !== button) {
                    arm(form, button, message);
                }
            });
            // What is confirmed is what was shown: editing the form asks again.
            form.addEventListener('input', function () {
                if (armed && armed.form === form) {
                    disarm();
                }
            });
        });
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                disarm();
            }
        });
    });
}());
