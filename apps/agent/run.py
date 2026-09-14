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
from uga.agent.closed_loop import ClosedLoopSupervisor, TerminalStatus
from uga.agent.mode_router import ModeRouter, RuleModeClassifier
from uga.agent.task_graph import RetryPolicy, TaskGraph, TaskNode, TaskStatus
from uga.capture.dxgi import DXGIDuplicationBackend
from uga.capture.fallback import GDIFallbackCaptureBackend
from uga.capture.hub import CaptureHub
from uga.capture.registry import CaptureBackendRegistry
from uga.capture.ring_buffer import FrameRingBuffer
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
from uga.environment.profile import BindingKind, GameProfile, load_game_profile
from uga.gui.controller import GuiActionController
from uga.observation.buffer import TemporalObservationBuffer
from uga.observation.builder import ObservationBuilder, ObservationInputs
from uga.perception.builder import PerceptionBuilder
from uga.perception.text import NullTextProvider, RapidOcrProvider, TextObservationProvider
from uga.policy.chunk_controller import ActionChunkController
from uga.policy.decision_journal import DecisionJournal, DecisionRecord
from uga.policy.grounded_vlm import GroundedOutcomeVerifier, GroundedVlmPlanner
from uga.policy.scripted_tap import ScriptedTapPolicy
from uga.policy.vlm_planner import OpenAICompatibleVisionClient, encode_frame_png
from uga.recording.episode_writer import EpisodeWriter
from uga.recording.schema import EpisodeMetadata, EpisodeResult
from uga.recording.video import PyAvVideoRecorder
from uga.release.fixture_qualification import _activate
from uga.release.revision import require_clean_source_revision
from uga.safety.emergency_stop import Win32EmergencyHotkey
from uga.safety.focus_guard import AgentEnableState, FocusGuard
from uga.time.clock import PerfCounterClock
from uga.windows.backend import Win32WindowBackend, WindowBackend, WindowSnapshot
from uga.windows.coordinates import Rect
from uga.windows.integrity import Win32IntegrityProvider

_TAP_FRACTION_DEFAULT = (0.5, 0.79)
_CAPTURE_STALL_BUDGET_S = 10.0


def _diagnostic_integer(values: dict[str, object], name: str) -> int:
    value = values.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _best_effort_cleanup(*operations: Callable[[], object]) -> None:
    for operation in operations:
        with contextlib.suppress(BaseException):
            operation()


def _persist_planner_decision(
    recorder: EpisodeWriter,
    clock: PerfCounterClock,
    record: DecisionRecord,
) -> None:
    recorder.record_planner(uuid.uuid4().hex, clock.now(), record.to_row())


def _episode_outcome(
    *,
    user_stopped: bool,
    run_error: BaseException | None,
    terminal_status: TerminalStatus,
    terminal_reason: str | None,
    duration_expired: bool,
) -> tuple[EpisodeResult, str]:
    """Resolve one unambiguous terminal result for the Episode contract."""
    if user_stopped:
        return EpisodeResult.ABORTED, "user_stopped"
    if run_error is not None:
        return EpisodeResult.FAILURE, "runtime_error"
    if terminal_status == TerminalStatus.SUCCEEDED:
        return EpisodeResult.SUCCESS, terminal_reason or "goal_confirmed"
    if terminal_status in {TerminalStatus.BLOCKED, TerminalStatus.FAILED}:
        return EpisodeResult.FAILURE, terminal_reason or terminal_status.value
    if duration_expired:
        return EpisodeResult.FAILURE, "timeout"
    return EpisodeResult.FAILURE, "loop_ended_without_goal_confirmation"


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
    qualification_root = getattr(args, "qualification_project_root", None)
    raw_goal_evidence = tuple(getattr(args, "goal_evidence", ()))
    if any(not value.strip() for value in raw_goal_evidence):
        raise SystemExit("--goal-evidence values cannot be blank")
    goal_evidence = tuple(dict.fromkeys(value.strip() for value in raw_goal_evidence))
    goal_action_target = getattr(args, "goal_action_target", None)
    if goal_action_target is not None:
        goal_action_target = goal_action_target.strip()
        if not goal_action_target:
            raise SystemExit("--goal-action-target cannot be blank")
    if qualification_root is not None and not args.record:
        raise SystemExit("--qualification-project-root requires --record")
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
        or not 64 <= args.vlm_max_output_tokens <= 16_384
    ):
        raise SystemExit("vision planner intervals and timeouts must be positive")
    if args.policy == "vlm" and not 0 <= args.max_recoveries <= 2:
        raise SystemExit("--max-recoveries must be within [0, 2]")
    if args.tap_interval_seconds and args.tap_interval_seconds <= args.tap_delay:
        raise SystemExit("--tap-interval-seconds must exceed --tap-delay")
    extra_body: dict[str, object] | None = None
    vision_client: OpenAICompatibleVisionClient | None = None
    outcome_verifier: GroundedOutcomeVerifier | None = None
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
            max_output_tokens=args.vlm_max_output_tokens,
            disable_thinking=args.vlm_no_thinking,
            extra_body=extra_body,
        )
        use_verifier = args.vision_mode == "hybrid" or (
            args.vision_mode == "auto" and bool(args.verifier_model)
        )
        if use_verifier:
            if not args.verifier_model:
                raise SystemExit("--vision-mode hybrid requires --verifier-model")
            verifier_client = OpenAICompatibleVisionClient(
                base_url=args.verifier_base_url or args.vlm_base_url,
                model=args.verifier_model,
                api_key=os.environ.get(args.verifier_api_key_env, ""),
                timeout_s=args.vlm_timeout_seconds,
                max_output_tokens=args.vlm_max_output_tokens,
            )
            outcome_verifier = GroundedOutcomeVerifier(verifier_client)
    profile = load_game_profile(args.profile)
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
    if not primed and args.policy != "vlm":
        backend.stop()
        raise SystemExit("target produced no capture frames within 5s of activation")
    if not primed:
        print(
            f"[capture] {backend.backend_id} produced no priming frame; "
            "starting the bounded GDI heartbeat before inference",
            flush=True,
        )
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
    frames = FrameRingBuffer()
    sampler_backend: GDIFallbackCaptureBackend | None = None
    dashboard: DecisionDashboard | None = None
    journal: DecisionJournal | None = None
    grounded_planner: GroundedVlmPlanner | None = None
    ocr_active = False

    if args.policy == "vlm":
        try:
            sampler_backend = GDIFallbackCaptureBackend(windows)
            sampler_backend.start(target.identity)
            journal = DecisionJournal()
            assert vision_client is not None
            grounded_planner = GroundedVlmPlanner(
                vision_client,
                structured_output=True,
                max_image_width=1280,
                journal=journal,
                required_goal_evidence=goal_evidence,
                preferred_action_target=goal_action_target,
            )
            policy: ScriptedTapPolicy | None = None
        except BaseException:
            _best_effort_cleanup(
                *(
                    value.stop
                    for value in (dashboard, sampler_backend)
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
    router = ModeRouter(
        initial_mode=ControlMode.GUI if args.policy == "vlm" else ControlMode.PLAY_3D,
        confirmation_frames=1,
    )

    recorder: EpisodeWriter | None = None
    episode_id = f"{profile.game_id}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    if args.record:
        try:
            source_revision = (
                require_clean_source_revision(qualification_root)
                if qualification_root is not None
                else None
            )
            policy_version = (
                grounded_planner.policy_version
                if grounded_planner is not None
                else (None if policy is None else policy.policy_version)
            )
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
                    policy_version,
                    False,
                    source_revision=source_revision,
                    source_tree_clean=True if source_revision is not None else None,
                    model_id=args.vlm_model if grounded_planner is not None else None,
                ),
            )
            recorder.attach_video(PyAvVideoRecorder(recorder.video_path, fps=15))
            if journal is not None:
                journal.set_sink(
                    lambda record: _persist_planner_decision(recorder, clock, record)
                )
        except BaseException:
            _best_effort_cleanup(
                *(value.abort for value in (recorder,) if value is not None),
                scheduler.neutralize,
                lambda: leases.revoke_all(notify=False),
                *(
                    value.stop
                    for value in (dashboard, sampler_backend)
                    if value is not None
                ),
                backend.stop,
            )
            raise

    controller = ActionChunkController(environment, arbiter, scheduler, recorder)
    perception_builder: PerceptionBuilder | None = None
    closed_loop: ClosedLoopSupervisor | None = None
    gui_controller: GuiActionController | None = None
    task_graph: TaskGraph | None = None
    task_node_id = "episode-goal"
    if args.policy == "vlm":
        text_provider: TextObservationProvider = NullTextProvider()
        if args.ocr == "auto" and profile.perception.ocr_enabled:
            try:
                text_provider = RapidOcrProvider()
                ocr_active = True
                print("[perception] RapidOCR enabled", flush=True)
            except BackendUnavailableError as exc:
                print(
                    f"[perception] OCR unavailable; continuing fail-safe: {exc}",
                    flush=True,
                )
        perception_builder = PerceptionBuilder(text_provider)
        timeout_ns = max(
            1,
            round(
                (args.duration_seconds if args.duration_seconds > 0 else 86_400.0)
                * 1_000_000_000
            ),
        )
        task_graph = TaskGraph(
            (
                TaskNode(
                    task_node_id,
                    args.goal,
                    None,
                    (),
                    TaskStatus.PENDING,
                    (),
                    None,
                    "goal verifier confirms success on two fresh frames",
                    "closed loop blocks, times out, or fails",
                    timeout_ns,
                    RetryPolicy(max_attempts=1),
                ),
            )
        )
        closed_loop = ClosedLoopSupervisor(
            clock,
            profile.perception,
            verifier=outcome_verifier,
            max_recoveries=args.max_recoveries,
            task_graph=task_graph,
            task_node_id=task_node_id,
            goal_evidence=goal_evidence,
            goal_action_target=goal_action_target,
        )
        gui_controller = GuiActionController(arbiter, scheduler, recorder)
        if recorder is not None:
            recorder.record_task(
                task_node_id,
                clock.now(),
                task_graph.get(task_node_id).to_envelope(),
            )

    def _resolve_key(name: str) -> tuple[int, ...] | None:
        binding = profile.binding(name)
        if (
            binding is None
            or not binding.confirmed
            or binding.kind != BindingKind.VIRTUAL_KEY
            or not isinstance(binding.code, int)
        ):
            return None
        return (binding.code,)

    capture_source = CaptureHub(
        primary=backend,
        fallback=sampler_backend,
        frames=frames,
        record_frame=None if recorder is None else recorder.record_frame,
        fallback_after_s=0.25,
        fallback_hz=4.0,
        consumer_timeout_s=_CAPTURE_STALL_BUDGET_S,
    )
    loop = RealtimeAgentLoop(
        clock=clock,
        capture=capture_source,
        frames=frames,
        observation_builder=builder,
        observations=observations,
        environment=environment,
        mode_classifier=RuleModeClassifier(profile.perception.mode_hints),
        mode_router=router,
        policy=policy,
        leases=leases,
        controller=controller,
        scheduler=scheduler,
        events=events,
        recorder=recorder,
        grounded_planner=grounded_planner,
        perception_builder=perception_builder,
        closed_loop=closed_loop,
        gui_controller=gui_controller,
        key_resolver=_resolve_key if grounded_planner is not None else None,
        grounded_decision_interval_s=(
            args.vlm_decision_interval if grounded_planner is not None else 0.0
        ),
    )

    if journal is not None and args.dashboard_port > 0:
        def dashboard_status() -> dict[str, object]:
            capture_stats = capture_source.stats()
            scheduler_stats = scheduler.stats()
            diagnostics = loop.closed_loop_diagnostics or {}
            latest = frames.latest()
            latest_action = next(
                (
                    row.get("action")
                    for row in reversed(journal.snapshot().get("events", []))
                    if isinstance(row, dict) and row.get("action")
                ),
                None,
            )
            return {
                "status": loop.terminal_status.value,
                "goal": args.goal,
                "model": args.vlm_model,
                "vision_mode": args.vision_mode,
                "ocr_active": ocr_active,
                "frame_id": None if latest is None else latest.frame.frame_id,
                "frame_source": (
                    None if latest is None else latest.frame.source_backend
                ),
                "frame_age_ms": (
                    None
                    if latest is None
                    else max(
                        0.0,
                        (clock.now().value_ns - latest.frame.capture_timestamp.value_ns)
                        / 1_000_000,
                    )
                ),
                "current_action": latest_action,
                "goal_confidence": loop.goal_confidence,
                "goal_evidence_confidence": diagnostics.get(
                    "goal_evidence_confidence"
                ),
                "termination_reason": loop.termination_reason,
                "capture_frames": capture_stats.accepted_frames,
                "capture_primary_frames": capture_stats.primary_frames,
                "capture_fallback_frames": capture_stats.fallback_frames,
                "capture_primary_errors": capture_stats.primary_errors,
                "capture_fallback_errors": capture_stats.fallback_errors,
                "capture_gap_p95_ms": capture_stats.p95_gap_ns / 1_000_000,
                "capture_gap_max_ms": capture_stats.max_gap_ns / 1_000_000,
                "stale_results_discarded": diagnostics.get(
                    "stale_results_discarded", 0
                ),
                "logical_actions_issued": diagnostics.get(
                    "logical_actions_issued", 0
                ),
                "executed_actions": scheduler_stats.executed,
                "recovery_count": diagnostics.get("recovery_count", 0),
                "recent_failure": diagnostics.get("last_loop_finding"),
            }

        def dashboard_preview() -> tuple[bytes, str] | None:
            latest = frames.latest()
            if latest is None:
                return None
            return encode_frame_png(latest.frame, max_width=960), "image/png"

        try:
            dashboard = DecisionDashboard(
                journal,
                args.dashboard_port,
                status_provider=dashboard_status,
                preview_provider=dashboard_preview,
            )
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
                for value in (dashboard, sampler_backend)
                if value is not None
            ),
            backend.stop,
            hotkey.close,
        )
        if previous_handler is not None:
            signal.signal(signal.SIGINT, previous_handler)
        raise

    started = time.monotonic()
    duration_expired = False
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
                duration_expired = True
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
        if sampler_backend is not None:
            cleanup(sampler_backend.stop)
        if dashboard is not None:
            cleanup(dashboard.stop)
        cleanup(backend.stop)
        cleanup(hotkey.close)
        if previous_handler is not None:
            cleanup(lambda: signal.signal(signal.SIGINT, previous_handler))
    if not stop_state["user"]:
        if run_error is not None:
            loop.fail_closed_loop("runtime_error")
        elif duration_expired:
            loop.fail_closed_loop("timeout")
    if recorder is not None:
        stats = scheduler.stats()
        diagnostics = loop.closed_loop_diagnostics or {}
        recorder.set_metrics(
            {
                "capture_frames": capture_source.stats().accepted_frames,
                "capture_primary_frames": capture_source.stats().primary_frames,
                "capture_fallback_frames": capture_source.stats().fallback_frames,
                "capture_primary_errors": capture_source.stats().primary_errors,
                "capture_fallback_errors": capture_source.stats().fallback_errors,
                "capture_consumer_skipped_frames": (
                    capture_source.stats().consumer_skipped_frames
                ),
                "capture_gap_p95_ns": capture_source.stats().p95_gap_ns,
                "capture_gap_max_ns": capture_source.stats().max_gap_ns,
                "scheduled_actions": stats.scheduled,
                "executed_actions": stats.executed,
                "action_execution_ratio": (
                    stats.executed / stats.scheduled if stats.scheduled else 0.0
                ),
                "logical_actions_issued": _diagnostic_integer(
                    diagnostics, "logical_actions_issued"
                ),
                "verified_effect_actions": _diagnostic_integer(
                    diagnostics, "verified_effect_actions"
                ),
                "ineffective_actions": _diagnostic_integer(
                    diagnostics, "ineffective_actions"
                ),
                "pending_action_at_termination": int(
                    bool(diagnostics.get("pending_action", False))
                ),
                "stale_results_discarded": _diagnostic_integer(
                    diagnostics, "stale_results_discarded"
                ),
                "max_consecutive_same_ineffective_action": _diagnostic_integer(
                    diagnostics, "max_consecutive_same_ineffective_action"
                ),
                "recovery_count": _diagnostic_integer(diagnostics, "recovery_count"),
            }
        )
        result, termination_reason = _episode_outcome(
            user_stopped=stop_state["user"],
            run_error=run_error,
            terminal_status=loop.terminal_status,
            terminal_reason=loop.termination_reason,
            duration_expired=duration_expired,
        )
        recorder.record_planner(
            f"terminal-{uuid.uuid4().hex[:12]}",
            clock.now(),
            {
                "kind": "terminal",
                "termination_reason": termination_reason,
                "goal_confidence": loop.goal_confidence,
                "closed_loop": loop.closed_loop_diagnostics,
            },
        )
        if task_graph is not None:
            recorder.record_task(
                f"{task_node_id}-terminal",
                clock.now(),
                task_graph.get(task_node_id).to_envelope(),
            )
        recorder.set_terminal_context(termination_reason, loop.goal_confidence)
        episode_path = recorder.finalize(result, clock.now())
        print(f"episode: {episode_path}")
    if run_error is not None:
        raise run_error
    print(f"physical actions executed: {scheduler.stats().executed}")
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
        "--goal-evidence",
        action="append",
        default=[],
        help=(
            "text that must be present in fresh OCR before DONE can be accepted; "
            "repeat for multiple required facts"
        ),
    )
    parser.add_argument(
        "--goal-action-target",
        help="preferred visible label for the next single-step navigation action",
    )
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
        default="qwen3-vl-4b-instruct",
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
        "--vlm-max-output-tokens",
        type=int,
        default=768,
        help="maximum generated tokens for one structured vision decision",
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
    parser.add_argument(
        "--vision-mode",
        choices=["auto", "local", "hybrid"],
        default="auto",
        help="local-first vision routing; hybrid uses the optional verifier",
    )
    parser.add_argument(
        "--ocr",
        choices=["auto", "off"],
        default="auto",
        help="enable profile-requested OCR when its locked extras are installed",
    )
    parser.add_argument("--max-recoveries", type=int, default=2)
    parser.add_argument("--verifier-base-url")
    parser.add_argument("--verifier-model")
    parser.add_argument("--verifier-api-key-env", default="UGA_VERIFIER_API_KEY")
    return parser


def main(args: argparse.Namespace) -> int:
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main(build_parser().parse_args()))
