(() => {
  interface DashboardState {
    frame_id: string | null;
    frame_preview_data_url: string | null;
    goal: string;
    subgoal: string | null;
    mode: string;
    lease_owner: string | null;
    current_skill: string | null;
    current_action: string | null;
    policy_confidence: number | null;
    reasoning_gate: boolean;
    capture_fps: number;
    policy_hz: number;
    end_to_end_latency_ms: number;
    gpu_vram_mb: number | null;
    queue_drops: number;
    expired_actions: number;
    recent_failure: string | null;
  }

  const csrf = document.querySelector<HTMLMetaElement>('meta[name="uga-csrf"]')?.content;
  const buttons = document.querySelectorAll<HTMLButtonElement>("[data-command]");

  function display(value: unknown): string {
    return value === null || value === undefined ? "—" : String(value);
  }

  function update(state: DashboardState): void {
    for (const [key, value] of Object.entries(state)) {
      if (key === "frame_preview_data_url") continue;
      const target = document.querySelector<HTMLElement>(`[data-field="${key}"]`);
      if (target) target.textContent = display(value);
    }
    const image = document.querySelector<HTMLImageElement>("#live-frame");
    const missing = document.querySelector<HTMLElement>("#preview-missing");
    if (image && state.frame_preview_data_url) {
      image.src = state.frame_preview_data_url;
      image.hidden = false;
      if (missing) missing.hidden = true;
    } else {
      if (image) image.hidden = true;
      if (missing) missing.hidden = false;
    }
  }

  async function refresh(): Promise<void> {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(`state request failed: ${response.status}`);
    update((await response.json()) as DashboardState);
  }

  async function command(name: string): Promise<void> {
    if (!csrf) throw new Error("dashboard CSRF token is missing");
    const response = await fetch(`/api/commands/${encodeURIComponent(name)}`, {
      method: "POST",
      headers: { "X-UGA-CSRF": csrf },
    });
    if (!response.ok) throw new Error(await response.text());
    await refresh();
  }

  for (const button of buttons) {
    button.addEventListener("click", () => {
      const name = button.dataset.command;
      if (!name) return;
      button.disabled = true;
      void command(name)
        .catch((error: unknown) => window.alert(String(error)))
        .finally(() => {
          button.disabled = false;
        });
    });
  }
  if (csrf) {
    window.setInterval(() => void refresh().catch(() => undefined), 1000);
  }
})();
