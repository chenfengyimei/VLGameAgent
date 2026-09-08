"use strict";
(() => {
    const query = document.querySelector("#sample-query");
    const override = document.querySelector("#override-filter");
    const rows = document.querySelectorAll("tbody tr");
    if (!query || !override)
        return;
    function filter() {
        const wanted = query?.value.trim().toLocaleLowerCase() ?? "";
        const overrideValue = override?.value ?? "all";
        for (const row of rows) {
            const matchesText = (row.dataset.search ?? "").includes(wanted);
            const matchesOverride = overrideValue === "all" || row.dataset.override === overrideValue;
            row.hidden = !(matchesText && matchesOverride);
        }
    }
    query.addEventListener("input", filter);
    override.addEventListener("change", filter);
})();
