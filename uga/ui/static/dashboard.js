"use strict";
(() => {
    const csrf = new URLSearchParams(location.hash.slice(1)).get("uga-token");
    const buttons = document.querySelectorAll("[data-command]");
    function display(value) {
        return value === null || value === undefined ? "—" : String(value);
    }
    function update(state) {
        for (const [key, value] of Object.entries(state)) {
            if (key === "frame_preview_data_url")
                continue;
            const target = document.querySelector(`[data-field="${key}"]`);
            if (target)
                target.textContent = display(value);
        }
        const image = document.querySelector("#live-frame");
        const missing = document.querySelector("#preview-missing");
        if (image && state.frame_preview_data_url) {
            image.src = state.frame_preview_data_url;
            image.hidden = false;
            if (missing)
                missing.hidden = true;
        }
        else {
            if (image)
                image.hidden = true;
            if (missing)
                missing.hidden = false;
        }
    }
    async function refresh() {
        const response = await fetch("/api/state", { cache: "no-store" });
        if (!response.ok)
            throw new Error(`state request failed: ${response.status}`);
        update((await response.json()));
    }
    async function command(name) {
        if (!csrf)
            throw new Error("dashboard CSRF token is missing");
        const response = await fetch(`/api/commands/${encodeURIComponent(name)}`, {
            method: "POST",
            headers: { "X-UGA-CSRF": csrf },
        });
        if (!response.ok)
            throw new Error(await response.text());
        await refresh();
    }
    for (const button of buttons) {
        button.addEventListener("click", () => {
            const name = button.dataset.command;
            if (!name)
                return;
            button.disabled = true;
            void command(name)
                .catch((error) => window.alert(String(error)))
                .finally(() => {
                button.disabled = false;
            });
        });
    }
    if (csrf) {
        window.setInterval(() => void refresh().catch(() => undefined), 1000);
    }
})();
