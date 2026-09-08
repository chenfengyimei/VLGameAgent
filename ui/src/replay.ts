(() => {
  interface ReplayEvent {
    sequence: number;
    timestamp_ns: number;
    elapsed_ns: number;
    kind: string;
    reference_id: string;
    payload: unknown;
  }

  const data = document.querySelector<HTMLScriptElement>("#uga-replay-data");
  const slider = document.querySelector<HTMLInputElement>("#time");
  const video = document.querySelector<HTMLVideoElement>("#video");
  const details = document.querySelector<HTMLElement>("#details");
  const clock = document.querySelector<HTMLOutputElement>("#clock");
  if (!data || !slider || !video || !details || !clock) return;
  const events = JSON.parse(data.textContent ?? "[]") as ReplayEvent[];

  function update(): void {
    const timestamp = Number(slider?.value ?? 0);
    if (video) video.currentTime = timestamp / 1e9;
    if (clock) clock.value = `${(timestamp / 1e9).toFixed(3)} s`;
    const nearby = events.filter(
      (event) => Math.abs(Number(event.elapsed_ns) - timestamp) <= 100_000_000,
    );
    if (details) details.textContent = JSON.stringify(nearby, null, 2);
  }

  slider.addEventListener("input", update);
  update();
})();
