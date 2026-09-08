"use strict";
(() => {
    const data = document.querySelector("#uga-replay-data");
    const slider = document.querySelector("#time");
    const video = document.querySelector("#video");
    const details = document.querySelector("#details");
    const clock = document.querySelector("#clock");
    if (!data || !slider || !video || !details || !clock)
        return;
    const events = JSON.parse(data.textContent ?? "[]");
    function update() {
        const timestamp = Number(slider?.value ?? 0);
        if (video)
            video.currentTime = timestamp / 1e9;
        if (clock)
            clock.value = `${(timestamp / 1e9).toFixed(3)} s`;
        const nearby = events.filter((event) => Math.abs(Number(event.elapsed_ns) - timestamp) <= 100_000_000);
        if (details)
            details.textContent = JSON.stringify(nearby, null, 2);
    }
    slider.addEventListener("input", update);
    update();
})();
