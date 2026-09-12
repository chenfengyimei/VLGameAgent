"""Compose the realtime agent loop against a live window from a game profile.

The V1 composition entry: discovers the profile's target window, activates
it, starts the preferred capture backend, and runs the full production loop
(observation → mode routing → policy → lease arbitration → 30 Hz scheduling →
SendInput injection) with an optional Episode recording. The default policy is
the profile-driven scripted tap timeline, which suits pointer-driven targets
such as Android emulator games.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import os
import re
import signal
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from types import FrameType

from apps.agent.dashboard import DecisionDashboard
from uga.agent.mode_router import ModeRouter, RuleModeClassifier
from uga.capture.dxgi import DXGIDuplicationBackend
from uga.capture.fallback import GDIFallbackCaptureBackend
from uga.capture.registry import CaptureBackendRegistry
from uga.capture.ring_buffer import FrameRingBuffer, SequencedFrame
from uga.capture.windows_graphics_capture import WindowsGraphicsCaptureBackend
from uga.control.arbiter import ActionArbiter
from uga.control.executor import InputExecutor
from uga.control.lease import ControlMode
from uga.control.lease_manager import ControlLeaseManager
from uga.control.scheduler import ActionScheduler
from uga.control.windows_input import SendInputBackend
from uga.core.agent_loop import RealtimeAgentLoop
from uga.core.errors import BackendUnavailableError, CaptureTimeoutError, ContractViolation
from uga.core.events import EventBus
from uga.environment.generic import GenericEnvironment
from uga.environment.profile import GameProfile, load_game_profile
from uga.observation.buffer import TemporalObservationBuffer
from uga.observation.builder import ObservationBuilder, ObservationInputs
from uga.policy.chunk_controller import ActionChunkController
from uga.policy.decision_journal import DecisionJournal
from uga.policy.scripted_tap import ScriptedTapPolicy
from uga.policy.vlm_planner import (
    FrameHistorySampler,
    OpenAICompatibleVisionClient,
    VlmPlannerPolicy,
    build_android_quest_instruction,
    build_instruction,
)
from uga.recording.episode_writer import EpisodeWriter
from uga.recording.schema import EpisodeMetadata, EpisodeResult
from uga.recording.video import PyAvVideoRecorder
from uga.release.fixture_qualification import _activate
from uga.safety.emergency_stop import Win32EmergencyHotkey
from uga.safety.focus_guard import AgentEnableState, FocusGuard
from uga.time.clock import PerfCounterClock
from uga.windows.backend import Win32WindowBackend, WindowBackend, WindowSnapshot
from uga.windows.coordinates import Rect
from uga.windows.integrity import Win32IntegrityProvider

_TAP_FRACTION_DEFAULT = (0.5, 0.79)
_CAPTURE_STALL_BUDGET_S = 10.0


def _best_effort_cleanup(*operations: Callable[[], object]) -> None:
    for operation in operations:
        with contextlib.suppress(BaseException):
            operation()


def _capture_backend(
    profile: GameProfile, windows: Win32WindowBackend
) -> CaptureBackendRegistry:
    preference = profile.preferred_capture
    registry = CaptureBackendRegistry((preference,) if preference != "auto" else ())
    registry.register(WindowsGraphicsCaptureBackend(windows=windows))
    registry.register(DXGIDuplicationBackend(windows=windows))
    registry.register(GDIFallbackCaptureBackend(windows))
    return registry


def _find_target(
    windows: WindowBackend, profile: GameProfile
) -> WindowSnapshot:
    pattern = re.compile(profile.window_title_pattern or r".*")
    matches_by_hwnd: dict[int, WindowSnapshot] = {}
    for executable in profile.executables:
        for snapshot in windows.discover(executable_name=executable):
            if pattern.fullmatch(snapshot.title) is not None:
                matches_by_hwnd[snapshot.identity.hwnd] = snapshot
    matches = list(matches_by_hwnd.values())
    if len(matches) != 1:
        raise SystemExit(
            "expected exactly one trusted executable/window pair matching "
            f"executables={profile.executables!r} and title={profile.window_title_pattern!r};"
            f" found {len(matches)}"
        )
    return matches[0]


def _current_target_client_rect(
    windows: WindowBackend,
    target: WindowSnapshot,
    title_pattern: re.Pattern[str],
) -> Rect:
    try:
        current = windows.snapshot(target.identity.hwnd)
    except (OSError, ContractViolation) as exc:
        raise BackendUnavailableError("target window is no longer available") from exc
    if current.identity != target.identity:
        raise BackendUnavailableError("target window identity changed")
    if not current.is_visible or title_pattern.fullmatch(current.title) is None:
        raise BackendUnavailableError("target window no longer matches its profile")
    return current.client_screen_rect


async def _run(args: argparse.Namespace) -> int:
    if not math.isfinite(args.duration_seconds) or args.duration_seconds < 0:
        raise SystemExit("--duration-seconds must be >= 0 (0 = run until stopped)")
    if not math.isfinite(args.tap_delay) or args.tap_delay < 0:
        raise SystemExit("--tap-delay must be >= 0")
    if not math.isfinite(args.tap_interval_seconds) or args.tap_interval_seconds < 0:
        raise SystemExit("--tap-interval-seconds must be >= 0 (0 = tap once)")
    if not math.isfinite(args.observation_hz) or args.observation_hz <= 0:
        raise SystemExit("--observation-hz must be positive")
    if not 0 <= args.dashboard_port <= 65535:
        raise SystemExit("--dashboard-port must be within [0, 65535]")
    if args.policy == "vlm" and (
        not math.isfinite(args.vlm_decision_interval)
        or args.vlm_decision_interval <= 0
        or not math.isfinite(args.vlm_timeout_seconds)
        or args.vlm_timeout_seconds <= 0
    ):
        raise SystemExit("vision planner intervals and timeouts must be positive")
    if args.tap_interval_seconds and args.tap_interval_seconds <= args.tap_delay:
        raise SystemExit("--tap-interval-seconds must exceed --tap-delay")
    extra_body: dict[str, object] | None = None
    vision_client: OpenAICompatibleVisionClient | None = None
    if args.policy == "vlm":
        try:
            parsed_extra = json.loads(args.vlm_extra_body) if args.vlm_extra_body else None
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--vlm-extra-body is not valid JSON: {exc}") from exc
        if parsed_extra is not None and not isinstance(parsed_extra, dict):
            raise SystemExit("--vlm-extra-body must be a JSON object")
        extra_body = parsed_extra
        vision_client = OpenAICompatibleVisionClient(
            base_url=args.vlm_base_url,
            model=args.vlm_model,
            api_key=os.environ.get(args.vlm_api_key_env, ""),
            timeout_s=args.vlm_timeout_seconds,
            disable_thinking=args.vlm_no_thinking,
            extra_body=extra_body,
        )
    profile = load_game_profile(args.profile)
    # Enforce the environment safety manifest before activating, capturing, or
    # otherwise interacting with the target.
    environment = GenericEnvironment(profile)
    windows = Win32WindowBackend()
    target = _find_target(windows, profile)
    if not _activate(windows, target.identity.hwnd):
        raise SystemExit("target window could not become foreground")

    registry = _capture_backend(profile, windows)
    backend = registry.start_best(target.identity)
    # Frame-delivery backends only emit when the target presents content, and
    # the activation transition just performed can stall composition briefly;
    # prime the first frame within a bounded budget while no lease is held and
    # no input is scheduled. A genuine capture outage still fails loudly.
    primed = False
    deadline = time.monotonic() + 5.0
    try:
        while time.monotonic() < deadline:
            try:
                backend.capture()
                primed = True
                break
            except CaptureTimeoutError:
                continue
    except BaseException:
        _best_effort_cleanup(backend.stop)
        raise
    if not primed:
        backend.stop()
        raise SystemExit("target produced no capture frames within 5s of activation")
    clock = PerfCounterClock()
    events = EventBus(clock)
    leases = ControlLeaseManager(clock)
    enabled = AgentEnableState(True)
    executor = InputExecutor(
        clock,
        SendInputBackend(),
        FocusGuard(windows, Win32IntegrityProvider(), leases, enabled),
        leases,
    )
    scheduler = ActionScheduler(clock, executor, leases)
    arbiter = ActionArbiter(clock, leases)

    client = target.client_screen_rect
    tap_x = round(client.left + client.width * args.tap_x_fraction)
    tap_y = round(client.top + client.height * args.tap_y_fraction)
    title_pattern = re.compile(profile.window_title_pattern or r".*")

    def _current_client_rect() -> Rect:
        return _current_target_client_rect(windows, target, title_pattern)

    frames = FrameRingBuffer()
    sampler: FrameHistorySampler | None = None
    sampler_backend: GDIFallbackCaptureBackend | None = None
    dashboard: DecisionDashboard | None = None

    if args.policy == "vlm":
        try:
            # The vision planner blocks the observe loop for the whole model
            # round-trip; an independent GDI thread keeps screenshotting during
            # that gap so the next decision gets a chronological bundle.
            sampler_backend = GDIFallbackCaptureBackend(windows)
            sampler_backend.start(target.identity)
            sampler = FrameHistorySampler(
                capture=sampler_backend.capture,
                interval_s=1.0,
            )
            sampler.start()
            journal = DecisionJournal()
            if args.dashboard_port > 0:
                try:
                    dashboard = DecisionDashboard(journal, args.dashboard_port)
                    dashboard.start()
                    print(
                        f"[dashboard] live at http://127.0.0.1:{args.dashboard_port}",
                        flush=True,
                    )
                except OSError as exc:
                    if dashboard is not None:
                        _best_effort_cleanup(dashboard.stop)
                        dashboard = None
                    print(f"[dashboard] disabled: {exc}", flush=True)
            # The model may press semantic buttons; only ones with confirmed
            # bindings in this profile can actuate — the rest are skipped at the
            # policy so a press never produces an empty chunk (pipeline contract).
            available_buttons = frozenset(
                name
                for name in ("jump", "menu", "confirm", "back", "primary", "secondary")
                if (binding := profile.binding(name)) is not None and binding.confirmed
            )
            assert vision_client is not None
            instruction_builder = (
                build_android_quest_instruction
                if profile.planner_prompt_strategy == "android_quest"
                else build_instruction
            )
            policy: ScriptedTapPolicy | VlmPlannerPolicy = VlmPlannerPolicy(
                client=vision_client,
                frame_source=lambda: frames.snapshot()[-1].frame,
                client_rect=_current_client_rect,
                goal=args.goal,
                decision_interval_s=args.vlm_decision_interval,
                sampler=sampler,
                journal=journal,
                available_buttons=available_buttons,
                instruction_builder=instruction_builder,
            )
        except BaseException:
            _best_effort_cleanup(
                *(
                    value.stop
                    for value in (dashboard, sampler, sampler_backend)
                    if value is not None
                ),
                backend.stop,
            )
            raise
    else:
        policy = ScriptedTapPolicy(
            [(args.tap_delay, tap_x, tap_y)],
            repeat_interval_s=args.tap_interval_seconds or None,
        )

    builder = ObservationBuilder(
        clock,
        profile.game_id,
        ObservationInputs(goal=args.goal),
    )
    observations = TemporalObservationBuffer()
    router = ModeRouter(initial_mode=ControlMode.PLAY_3D, confirmation_frames=1)

    recorder: EpisodeWriter | None = None
    episode_id = f"{profile.game_id}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    if args.record:
        try:
            recorder = EpisodeWriter(
                args.record,
                EpisodeMetadata(
                    episode_id,
                    profile.game_id,
                    "1.1",
                    (round(client.width), round(client.height)),
                    backend.backend_id,
                    clock.now().value_ns,
                    args.goal,
                    EpisodeResult.IN_PROGRESS,
                    "0.1.0",
                    policy.policy_version,
                    False,
                ),
            )
            recorder.attach_video(PyAvVideoRecorder(recorder.video_path, fps=15))
        except BaseException:
            _best_effort_cleanup(
                *(value.abort for value in (recorder,) if value is not None),
                scheduler.neutralize,
                lambda: leases.revoke_all(notify=False),
                *(
                    value.stop
                    for value in (dashboard, sampler, sampler_backend)
                    if value is not None
                ),
                backend.stop,
            )
            raise

    controller = ActionChunkController(environment, arbiter, scheduler, recorder)

    class CaptureSource:
        def __init__(self) -> None:
            self.count = 0

        async def capture_once(self) -> SequencedFrame:
            # Present-driven backends only deliver frames while the target
            # renders; emulator menus, dialogs, and transitions can hold the
            # surface still for seconds. Wait through those gaps within a
            # bounded budget before failing closed — no observation means no
            # new policy output, so waiting cannot act blind.
            deadline = time.monotonic() + _CAPTURE_STALL_BUDGET_S
            while True:
                try:
                    frame = await asyncio.to_thread(backend.capture)
                    sequenced = frames.publish(frame)
                    self.count += 1
                    return sequenced
                except CaptureTimeoutError:
                    if time.monotonic() >= deadline:
                        raise

    capture_source = CaptureSource()
    loop = RealtimeAgentLoop(
        clock=clock,
        capture=capture_source,
        frames=frames,
        observation_builder=builder,
        observations=observations,
        environment=environment,
        mode_classifier=RuleModeClassifier(),
        mode_router=router,
        policy=policy,
        leases=leases,
        controller=controller,
        scheduler=scheduler,
        events=events,
        recorder=recorder,
    )

    stop = asyncio.Event()
    loop_ref = asyncio.get_running_loop()
    stop_state: dict[str, bool] = {"user": False}

    def request_stop() -> None:
        stop_state["user"] = True
        print("stop requested (Ctrl+C or Ctrl+Shift+F12) — shutting down", flush=True)
        with contextlib.suppress(RuntimeError):
            loop_ref.call_soon_threadsafe(stop.set)

    for name in ("SIGINT", "SIGTERM"):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop_ref.add_signal_handler(getattr(signal, name), request_stop)

    def _on_sigint(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        request_stop()

    previous_handler = None
    if os.name == "nt":
        previous_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, _on_sigint)

    hotkey = Win32EmergencyHotkey(request_stop)
    try:
        hotkey.start()
    except BaseException:
        _best_effort_cleanup(
            scheduler.neutralize,
            lambda: leases.revoke_all(notify=False),
            *(
                value.stop
                for value in (dashboard, sampler, sampler_backend)
                if value is not None
            ),
            backend.stop,
            hotkey.close,
        )
        if previous_handler is not None:
            signal.signal(signal.SIGINT, previous_handler)
        raise

    started = time.monotonic()
    if args.policy == "vlm":
        print(
            f"running: game={profile.game_id} target={target.title}"
            f" policy=vlm model={args.vlm_model} goal={args.goal}"
        )
    else:
        print(f"running: game={profile.game_id} target={target.title} tap=({tap_x}, {tap_y})")
    task = asyncio.create_task(
        loop.run(stop, observation_hz=args.observation_hz)
    )
    run_error: BaseException | None = None
    try:
        while not task.done():
            if 0 < args.duration_seconds <= time.monotonic() - started:
                break
            await asyncio.sleep(0.2)
    except (KeyboardInterrupt, asyncio.CancelledError):
        stop_state["user"] = True
    except BaseException as exc:
        run_error = exc
    finally:
        stop.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except BaseException as exc:
            if run_error is None:
                run_error = exc

        def cleanup(operation: Callable[[], object]) -> None:
            nonlocal run_error
            try:
                operation()
            except BaseException as exc:
                if run_error is None:
                    run_error = exc

        cleanup(scheduler.neutralize)
        cleanup(lambda: leases.revoke_all(notify=False))
        if sampler is not None:
            cleanup(sampler.stop)
        if sampler_backend is not None:
            cleanup(sampler_backend.stop)
        if dashboard is not None:
            cleanup(dashboard.stop)
        cleanup(backend.stop)
        cleanup(hotkey.close)
        if previous_handler is not None:
            cleanup(lambda: signal.signal(signal.SIGINT, previous_handler))
    if recorder is not None:
        stats = scheduler.stats()
        recorder.set_metrics(
            {
                "capture_frames": capture_source.count,
                "scheduled_actions": stats.scheduled,
                "executed_actions": stats.executed,
                "action_execution_ratio": (
                    stats.executed / stats.scheduled if stats.scheduled else 0.0
                ),
            }
        )
        result = (
            EpisodeResult.ABORTED
            if stop_state["user"]
            else EpisodeResult.FAILURE
            if run_error is not None
            else EpisodeResult.SUCCESS
        )
        episode_path = recorder.finalize(result, clock.now())
        print(f"episode: {episode_path}")
    if run_error is not None:
        raise run_error
    print(f"taps executed: {scheduler.stats().executed}")
    return 0


def _client_fraction(value: str) -> float:
    fraction = float(value)
    if not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0:
        raise argparse.ArgumentTypeError("client fraction must be within [0, 1]")
    return fraction


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the UGA agent against a live window")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--goal", default="Interact with the target")
    parser.add_argument(
        "--policy",
        choices=["scripted", "vlm"],
        default="scripted",
        help="decision source: a scripted tap timeline or a vision-language planner",
    )
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=30.0,
        help="0 = run until stopped (Ctrl+C or the Ctrl+Shift+F12 emergency hotkey)",
    )
    parser.add_argument("--tap-delay", type=float, default=2.0)
    parser.add_argument(
        "--tap-interval-seconds",
        type=float,
        default=0.0,
        help="repeat the tap timeline every N seconds (0 = tap once)",
    )
    parser.add_argument(
        "--tap-x-fraction",
        type=_client_fraction,
        default=_TAP_FRACTION_DEFAULT[0],
        help="tap point as a horizontal fraction of the client area",
    )
    parser.add_argument(
        "--tap-y-fraction",
        type=_client_fraction,
        default=_TAP_FRACTION_DEFAULT[1],
        help="tap point as a vertical fraction of the client area",
    )
    parser.add_argument("--observation-hz", type=float, default=2.0)
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=0,
        help="serve the read-only VLM decision dashboard on loopback (0 = disabled)",
    )
    parser.add_argument("--record", type=Path)
    parser.add_argument(
        "--vlm-base-url",
        default="http://127.0.0.1:1234/v1",
        help="OpenAI-compatible vision endpoint (LM Studio or any cloud vision API)",
    )
    parser.add_argument(
        "--vlm-model",
        default="gemma-3-4b-it",
        help="vision model name exposed at the endpoint",
    )
    parser.add_argument(
        "--vlm-api-key-env",
        default="UGA_VLM_API_KEY",
        help="environment variable that holds the vision API key (empty for local servers)",
    )
    parser.add_argument(
        "--vlm-decision-interval",
        type=float,
        default=6.0,
        help="seconds between vision planner decisions",
    )
    parser.add_argument(
        "--vlm-timeout-seconds",
        type=float,
        default=30.0,
        help="vision request timeout",
    )
    parser.add_argument(
        "--vlm-no-thinking",
        action="store_true",
        help="ask thinking-style models (GLM-4.xV) to answer without a reasoning pass",
    )
    parser.add_argument(
        "--vlm-extra-body",
        help="JSON object merged into the vision request body (e.g. "
        '\'{"enable_thinking": false}\' for DashScope Qwen3 models)',
    )
    return parser


def main(args: argparse.Namespace) -> int:
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main(build_parser().parse_args()))
