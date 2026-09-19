// Shows the year group only when "student" is chosen, on every form that asks.
//
// The server decides this too -- YearGroupRequirement refuses a student with no
// year group and clears the box for everyone else -- so this is convenience,
// not enforcement. Without JavaScript the field simply stays visible and the
// server still gets it right.
(function () {
    "use strict";

    function wire(container) {
        var field = container.querySelector("[data-year-group-field]");
        var radios = container.querySelectorAll('input[name="member_kind"]');
        if (!field || !radios.length) {
            return;
        }

        var input = field.querySelector("input");

        function apply() {
            var isStudent = false;
            radios.forEach(function (radio) {
                if (radio.checked && radio.value === "student") {
                    isStudent = true;
                }
            });

            field.hidden = !isStudent;
            if (!input) {
                return;
            }
            if (isStudent) {
                // Restores the browser-side check a student used to get from
                // DataRequired, which no longer renders the attribute.
                input.setAttribute("required", "required");
            } else {
                input.removeAttribute("required");
                // A hidden box still submits. Clearing it keeps a value typed
                // before the choice was switched from arriving at the server.
                input.value = "";
            }
        }

        radios.forEach(function (radio) {
            radio.addEventListener("change", apply);
        });
        apply();
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("[data-member-kind]").forEach(wire);
    });
})();
