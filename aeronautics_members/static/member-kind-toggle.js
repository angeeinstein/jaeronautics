// Shows the year group only for the member categories that are asked for one.
//
// Which those are is not decided here. The container carries
// data-year-group-categories, rendered from member_categories.py, so adding a
// category never means editing this file -- and this file can never quietly
// disagree with the server about the rule.
//
// The server applies it too: YearGroupRequirement refuses a student with no
// year group and clears the field for categories that are not asked. So this
// is convenience, not enforcement. Without JavaScript the field simply stays
// visible and the server still gets it right.
(function () {
    "use strict";

    function wire(container) {
        var field = container.querySelector("[data-year-group-field]");
        var chooser = container.querySelector('select[name="member_category"]');
        if (!field || !chooser) {
            return;
        }

        var shown = (container.getAttribute("data-year-group-categories") || "")
            .split(/\s+/)
            .filter(Boolean);
        var required = (container.getAttribute("data-year-group-required") || "")
            .split(/\s+/)
            .filter(Boolean);
        var input = field.querySelector("input");

        function apply() {
            var category = chooser.value;
            var isShown = shown.indexOf(category) !== -1;

            field.hidden = !isShown;
            if (!input) {
                return;
            }
            if (isShown && required.indexOf(category) !== -1) {
                // Restores the browser-side check a student used to get from
                // DataRequired, which no longer renders the attribute.
                input.setAttribute("required", "required");
            } else {
                input.removeAttribute("required");
            }
            if (!isShown) {
                // A hidden field still submits. Clearing it keeps a value typed
                // before the choice was switched from reaching the server.
                input.value = "";
            }
        }

        chooser.addEventListener("change", apply);
        apply();
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("[data-member-kind]").forEach(wire);
    });
})();
