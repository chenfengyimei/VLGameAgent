from __future__ import annotations

import html
from dataclasses import asdict, dataclass
from enum import StrEnum

from uga.control.lease import ControlMode, ControlOwner
from uga.ui import load_ui_script


class DashboardCommand(StrEnum):
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"
    TAKE_CONTROL = "take_control"
    RELEASE_CONTROL = "release_control"
    EMERGENCY_RELEASE = "emergency_release"


@dataclass(frozen=True, slots=True)
class DashboardState:
    frame_id: str | None
    frame_preview_data_url: str | None
    goal: str
    subgoal: str | None
    mode: ControlMode
    lease_owner: ControlOwner | None
    current_skill: str | None
    current_action: str | None
    policy_confidence: float | None
    reasoning_gate: bool
    capture_fps: float
    policy_hz: float
    end_to_end_latency_ms: float
    gpu_vram_mb: int | None
    queue_drops: int
    expired_actions: int
    recent_failure: str | None

    def to_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["mode"] = self.mode.value
        payload["lease_owner"] = None if self.lease_owner is None else self.lease_owner.name
        return payload


def render_dashboard(
    state: DashboardState, *, live: bool = False, csrf_token: str | None = None
) -> str:
    if live and csrf_token is None:
        raise ValueError("live dashboard rendering requires a CSRF token")
    values = {
        "frame_id": ("Frame", state.frame_id),
        "goal": ("Goal", state.goal),
        "subgoal": ("Subgoal", state.subgoal),
        "mode": ("Mode", state.mode.value),
        "lease_owner": (
            "Lease owner",
            None if state.lease_owner is None else state.lease_owner.name,
        ),
        "current_skill": ("Skill", state.current_skill),
        "current_action": ("Action", state.current_action),
        "policy_confidence": ("Policy confidence", state.policy_confidence),
        "reasoning_gate": ("Reasoning gate", state.reasoning_gate),
        "capture_fps": ("Capture FPS", state.capture_fps),
        "policy_hz": ("Policy Hz", state.policy_hz),
        "end_to_end_latency_ms": (
            "End-to-end latency ms",
            state.end_to_end_latency_ms,
        ),
        "gpu_vram_mb": ("GPU VRAM MB", state.gpu_vram_mb),
        "queue_drops": ("Queue drops", state.queue_drops),
        "expired_actions": ("Expired actions", state.expired_actions),
        "recent_failure": ("Recent failure", state.recent_failure),
    }
    preview_source = (
        "" if state.frame_preview_data_url is None else html.escape(state.frame_preview_data_url)
    )
    preview = (
        f'<p id="preview-missing"{("" if not preview_source else " hidden")}>No live frame</p>'
        f'<img id="live-frame" alt="Live frame" src="{preview_source}"'
        f"{(' hidden' if not preview_source else '')}>"
    )
    cards = "".join(
        f'<dt>{html.escape(label)}</dt><dd data-field="{key}">{html.escape(str(value))}</dd>'
        for key, (label, value) in values.items()
    )
    buttons = "".join(
        f'<button data-command="{command.value}">{command.value.replace("_", " ")}</button>'
        for command in DashboardCommand
    )
    live_script = ""
    live_meta = ""
    if live:
        live_meta = f'<meta name="uga-csrf" content="{html.escape(str(csrf_token))}">'
        live_script = f"<script>{load_ui_script('dashboard.js')}</script>"
    return (
        f"<!doctype html><html><meta charset=utf-8>{live_meta}<title>UGA Dashboard</title>"
        "<style>body{font-family:system-ui;margin:2rem;background:#111;color:#eee}"
        "dl{display:grid;grid-template-columns:max-content 1fr;gap:.5rem 1rem}"
        "button{margin:.25rem;padding:.5rem}</style><h1>UGA Dashboard</h1>"
        f"{preview}<dl>{cards}</dl><nav>{buttons}</nav>{live_script}</html>"
    )
