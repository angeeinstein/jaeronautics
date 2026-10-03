/* The logo field of a team's settings.
 *
 * Remove and choosing a file only mark what saving will do: the preview shows
 * the logo as it will be, and a line says so. Remove clears a chosen file too;
 * pressed again (Undo) it brings back what was there.
 *
 * A static file because the Content-Security-Policy allows script-src 'self'.
 */
document.addEventListener('DOMContentLoaded', function () {
    [].slice.call(document.querySelectorAll('[data-team-logo-field]')).forEach(function (field) {
        if (field.hasAttribute('data-ready')) {
            return;
        }
        field.setAttribute('data-ready', '');
        var input = field.querySelector('input[type=file]');
        var removeButton = field.querySelector('[data-logo-remove]');
        var flag = field.querySelector('[data-logo-remove-flag]');
        var note = field.querySelector('[data-logo-note]');
        var preview = field.querySelector('[data-logo-preview]');
        var current = field.querySelector('[data-logo-current]');
        var empty = field.querySelector('[data-logo-empty]');
        var chosen = null;

        function show() {
            var removing = flag.value === 'on';
            var hasFile = input.files && input.files.length > 0;
            if (chosen) {
                chosen.remove();
                chosen = null;
            }
            if (hasFile) {
                chosen = document.createElement('img');
                chosen.className = 'team-logo team-logo-large';
                chosen.alt = '';
                chosen.src = URL.createObjectURL(input.files[0]);
                preview.appendChild(chosen);
            }
            if (current) {
                current.hidden = removing || hasFile;
            }
            empty.hidden = hasFile || (current && !removing);
            removeButton.hidden = !(current || hasFile);
            removeButton.textContent = removing && !hasFile
                ? removeButton.getAttribute('data-label-undo')
                : removeButton.getAttribute('data-label-remove');
            note.hidden = !(removing || hasFile);
            note.textContent = hasFile ? note.getAttribute('data-note-new') : note.getAttribute('data-note-removed');
        }

        removeButton.addEventListener('click', function () {
            var hasFile = input.files && input.files.length > 0;
            if (hasFile) {
                input.value = '';
                flag.value = current ? 'on' : '';
            } else {
                flag.value = flag.value === 'on' ? '' : 'on';
            }
            show();
        });
        input.addEventListener('change', function () {
            flag.value = '';
            show();
        });
    });
});
