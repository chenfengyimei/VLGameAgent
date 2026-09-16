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
from uga.agent.recovery_budget import RecoveryBudget
from uga.agent.session_state import GameSessionState
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
from uga.core.run_context import RunContext
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
from uga.safety.shutdown import SafetyShutdown, ShutdownCause
from uga.safety.watchdog import RuntimeWatchdog, RuntimeWatchdogMonitor
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
    continuous = bool(getattr(args, "continuous", False))
    vlm_temporal_frames = int(getattr(args, "vlm_temporal_frames", 3))
    vlm_image_width = int(getattr(args, "vlm_image_width", 1280))
    vlm_target_crops = int(getattr(args, "vlm_target_crops", 2))
    capture_hz = float(getattr(args, "capture_hz", 10.0))
    vlm_compact_output = bool(getattr(args, "vlm_compact_output", False))
    vlm_ocr_task_fallback = bool(getattr(args, "vlm_ocr_task_fallback", False))
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
    if (
        not math.isfinite(args.watchdog_timeout_seconds)
        or args.watchdog_timeout_seconds <= 0
    ):
        raise SystemExit("--watchdog-timeout-seconds must be positive")
    if not math.isfinite(args.tap_delay) or args.tap_delay < 0:
        raise SystemExit("--tap-delay must be >= 0")
    if not math.isfinite(args.tap_interval_seconds) or args.tap_interval_seconds < 0:
        raise SystemExit("--tap-interval-seconds must be >= 0 (0 = tap once)")
    if not math.isfinite(args.observation_hz) or args.observation_hz <= 0:
        raise SystemExit("--observation-hz must be positive")
    if not math.isfinite(capture_hz) or not 0.5 <= capture_hz <= 60.0:
        raise SystemExit("--capture-hz must be within [0.5, 60]")
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
    if args.policy == "vlm" and not 1 <= vlm_temporal_frames <= 3:
        raise SystemExit("--vlm-temporal-frames must be within [1, 3]")
    if args.policy == "vlm" and not 320 <= vlm_image_width <= 1280:
        raise SystemExit("--vlm-image-width must be within [320, 1280]")
    if args.policy == "vlm" and not 0 <= vlm_target_crops <= 2:
        raise SystemExit("--vlm-target-crops must be within [0, 2]")
    if continuous and args.policy != "vlm":
        raise SystemExit("--continuous requires --policy vlm")
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
            json_object_mode=args.vlm_json_object,
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
    # Inputs stay disabled until the emergency hotkey and watchdog are armed;
    # arming is an explicit step below, never a constructor default.
    enabled = AgentEnableState(False)
    executor = InputExecutor(
        clock,
        SendInputBackend(),
        FocusGuard(windows, Win32IntegrityProvider(), leases, enabled),
        leases,
    )
    scheduler = ActionScheduler(clock, executor, leases)
    arbiter = ActionArbiter(clock, leases)
    # F05/F06: the run-level recovery budget is owned here so supervisor
    # rebuilds in continuous mode can never reset the accounting.
    recovery_budget = RecoveryBudget(args.max_recoveries)
    # Latched fail-safe neutralization: disable inputs, revoke leases, flush
    # the scheduler queue, and release held keys — idempotent, first cause
    # wins. Every stop path trips this BEFORE notifying the async loop.
    safety = SafetyShutdown(clock, leases, scheduler, executor, enabled)
    run_context = RunContext(safety)
    watchdog = RuntimeWatchdog(
        clock,
        safety,
        int(args.watchdog_timeout_seconds * 1_000_000_000),
    )
    watchdog_monitor = RuntimeWatchdogMonitor(watchdog)

    client = target.client_screen_rect
    tap_x = round(client.left + client.width * args.tap_x_fraction)
    tap_y = round(client.top + client.height * args.tap_y_fraction)
    frames = FrameRingBuffer()
    sampler_backend: GDIFallbackCaptureBackend | None = None
    dashboard: DecisionDashboard | None = None
    journal: DecisionJournal | None = None
    grounded_planner: GroundedVlmPlanner | None = None
    ocr_active = False
    game_session: GameSessionState | None = None
    back_hotspot: tuple[float, float] | None = None
    close_hotspot: tuple[float, float] | None = None
    promote_hotspot: tuple[float, float] | None = None
    for exit_name in ("ui_back", "ui_close", "ui_promote"):
        binding = profile.binding(exit_name)
        if binding is None:
            continue
        if binding.kind != BindingKind.NORMALIZED_HOTSPOT or binding.hotspot is None:
            raise SystemExit(f"profile {exit_name} binding must be a normalized_hotspot")
        if binding.confirmed:
            if exit_name == "ui_back":
                back_hotspot = binding.hotspot
            elif exit_name == "ui_close":
                close_hotspot = binding.hotspot
            else:
                promote_hotspot = binding.hotspot
    available_keys = frozenset(
        binding.action
        for binding in profile.controls
        if binding.kind == BindingKind.VIRTUAL_KEY
        and binding.confirmed
        and isinstance(binding.code, int)
    )

    if args.policy == "vlm":
        try:
            sampler_backend = GDIFallbackCaptureBackend(windows)
            sampler_backend.start(target.identity)
            journal = DecisionJournal()
            game_session = GameSessionState()
            # The tracked quest survives agent restarts: the loop is often
            # relaunched while the game sits on a feature page whose OCR never
            # shows the main-quest tracker.
            # F15: the persistence namespace is bound to the game profile so
            # two profiles never cross-wire their quest memory.
            game_session.set_persistence(
                Path(__file__).resolve().parents[2]
                / "runs"
                / "live-agent"
                / "session_state.json",
                profile_id=profile.game_id,
            )
            assert vision_client is not None
            grounded_planner = GroundedVlmPlanner(
                vision_client,
                structured_output=True,
                max_image_width=vlm_image_width,
                max_temporal_frames=vlm_temporal_frames,
                max_target_crops=vlm_target_crops,
                compact_output=vlm_compact_output,
                prefer_ocr_task_panel=vlm_ocr_task_fallback,
                journal=journal,
                required_goal_evidence=goal_evidence,
                preferred_action_target=goal_action_target,
                back_hotspot=back_hotspot,
                close_hotspot=close_hotspot,
                promote_hotspot=promote_hotspot,
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
        if not continuous:
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

        def make_closed_loop() -> ClosedLoopSupervisor:
            return ClosedLoopSupervisor(
                clock,
                profile.perception,
                verifier=outcome_verifier,
                max_recoveries=args.max_recoveries,
                recovery_budget=recovery_budget,
                task_graph=task_graph,
                task_node_id=task_node_id if task_graph is not None else None,
                goal_evidence=goal_evidence,
                goal_action_target=goal_action_target,
                back_hotspot=back_hotspot,
                close_hotspot=close_hotspot,
                promote_hotspot=promote_hotspot,
                available_keys=available_keys or None,
                session=game_session,
                journal=journal,
                on_exit_executed=(
                    (lambda: grounded_planner.refresh_task_panel_cooldown())
                    if grounded_planner is not None
                    else None
                ),
            )

        closed_loop = make_closed_loop()
        gui_controller = GuiActionController(arbiter, scheduler, recorder)
        if recorder is not None and task_graph is not None:
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
        fallback_after_s=max(0.25, 1.5 / capture_hz),
        fallback_hz=min(capture_hz, 4.0),
        primary_hz=capture_hz,
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
        continuous_grounded=continuous,
        closed_loop_factory=(make_closed_loop if continuous else None),
        run_context=run_context,
        control_heartbeat=watchdog.heartbeat,
        recovery_budget=recovery_budget,
    )

    if journal is not None and args.dashboard_port > 0:
        def dashboard_status() -> dict[str, object]:
            capture_stats = capture_source.stats()
            scheduler_stats = scheduler.stats()
            diagnostics = loop.closed_loop_diagnostics or {}
            latest = frames.latest()
            events = [
                row
                for row in journal.snapshot().get("events", [])
                if isinstance(row, dict)
            ]
            latest_action = next(
                (row.get("action") for row in reversed(events) if row.get("action")),
                None,
            )
            physical = next(
                (
                    row
                    for row in reversed(events)
                    if row.get("kind") in {"action_submitted", "action_effect"}
                ),
                None,
            )
            session = diagnostics.get("session")
            sessionEnvelope = dict(session) if isinstance(session, dict) else {}
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
                "last_physical_action": (
                    None
                    if physical is None
                    else {
                        "time": physical.get("wall_clock"),
                        "action": physical.get("action"),
                        "detail": physical.get("detail"),
                        "kind": physical.get("kind"),
                        "quest_step": physical.get("quest_step"),
                    }
                ),
                "session_state": sessionEnvelope,
                "back_recovery_streak": diagnostics.get("back_recovery_streak", 0),
                "back_hotspot": diagnostics.get("back_hotspot"),
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
                "continuous_mode": diagnostics.get("continuous_mode", False),
                "continuous_cycle_count": diagnostics.get(
                    "continuous_cycle_count", 1
                ),
                "planner_failure_count": diagnostics.get("planner_failure_count", 0),
                "last_planner_error": diagnostics.get("last_planner_error"),
                "last_supervision_disposition": diagnostics.get(
                    "last_supervision_disposition"
                ),
                "last_supervision_reason": diagnostics.get("last_supervision_reason"),
                "last_planner_input_frame_id": diagnostics.get(
                    "last_planner_input_frame_id"
                ),
                "last_planner_input_age_ms": (
                    None
                    if not isinstance(
                        planner_input_age := diagnostics.get(
                            "last_planner_input_age_ns"
                        ),
                        int,
                    )
                    or isinstance(planner_input_age, bool)
                    else float(planner_input_age) / 1_000_000
                ),
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

    def request_stop(
        cause: ShutdownCause = ShutdownCause.NORMAL_STOP, user: bool = False
    ) -> None:
        if user:
            stop_state["user"] = True
        # Input de-authorization comes FIRST and is synchronous: disable
        # inputs, revoke leases, flush the scheduler queue, release held
        # keys. Printing and the async-loop notification happen only after
        # the latch has completed neutralization.
        trip = safety.trip(cause)
        run_context.cancel()
        print(f"stop requested ({trip.cause.value}) — shutting down", flush=True)
        with contextlib.suppress(RuntimeError):
            loop_ref.call_soon_threadsafe(stop.set)

    for name in ("SIGINT", "SIGTERM"):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop_ref.add_signal_handler(
                getattr(signal, name), request_stop, ShutdownCause.NORMAL_STOP, True
            )

    def _on_sigint(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        request_stop(ShutdownCause.NORMAL_STOP, True)

    previous_handler = None
    if os.name == "nt":
        previous_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, _on_sigint)

    hotkey = Win32EmergencyHotkey(
        lambda: request_stop(ShutdownCause.EMERGENCY_HOTKEY, True)
    )
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

    # The emergency latch and watchdog enforcement are live before any
    # physical input is authorized; only then are inputs armed.
    watchdog_monitor.start()
    enabled.set(True)
    print(
        f"[safety] emergency hotkey + {args.watchdog_timeout_seconds:.0f}s watchdog "
        "armed; physical inputs enabled",
        flush=True,
    )

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
                request_stop(ShutdownCause.NORMAL_STOP)
                break
            trip = safety.tripped
            if trip is not None and trip.cause in (
                ShutdownCause.WATCHDOG_TIMEOUT,
                ShutdownCause.RUNTIME_FAILURE,
            ):
                # Watchdog-initiated trip: neutralization already happened
                # inside trip(); stop the loop and surface it as a failure.
                stop.set()
                break
            await asyncio.sleep(0.2)
    except (KeyboardInterrupt, asyncio.CancelledError):
        request_stop(ShutdownCause.NORMAL_STOP, True)
    except BaseException as exc:
        run_error = exc
    finally:
        stop.set()
        # Neutralize BEFORE waiting for the in-flight step: a stop must
        # never depend on the model, the capture backend, or task teardown.
        if safety.tripped is None:
            safety.trip(
                ShutdownCause.RUNTIME_FAILURE
                if run_error is not None
                else ShutdownCause.NORMAL_STOP
            )
        run_context.cancel()
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

        cleanup(watchdog_monitor.close)
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
    trip = safety.tripped
    if (
        trip is not None
        and run_error is None
        and trip.cause in (ShutdownCause.WATCHDOG_TIMEOUT, ShutdownCause.RUNTIME_FAILURE)
    ):
        run_error = RuntimeError(f"safety shutdown tripped: {trip.cause.value}")
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
                "safety_discarded_results": _diagnostic_integer(
                    diagnostics, "safety_discarded_results"
                ),
                "max_consecutive_same_ineffective_action": _diagnostic_integer(
                    diagnostics, "max_consecutive_same_ineffective_action"
                ),
                "recovery_count": _diagnostic_integer(diagnostics, "recovery_count"),
                "recovery_budget_consumed": _diagnostic_integer(
                    diagnostics, "recovery_budget_consumed"
                ),
                "recovery_budget_limit": _diagnostic_integer(
                    diagnostics, "recovery_budget_limit"
                ),
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
    parser.add_argument(
        "--continuous",
        action="store_true",
        help=(
            "keep one VLM process alive by retrying transient planner failures and "
            "starting a fresh closed-loop cycle after blocked or completed states"
        ),
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
        "--capture-hz",
        type=float,
        default=10.0,
        help="maximum continuous capture frequency",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=0,
        help="serve the read-only VLM decision dashboard on loopback (0 = disabled)",
    )
    parser.add_argument(
        "--watchdog-timeout-seconds",
        type=float,
        default=60.0,
        help=(
            "control-plane liveness timeout; a scheduler stall beyond it trips the "
            "latched safety shutdown"
        ),
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
        "--vlm-temporal-frames",
        type=int,
        choices=range(1, 4),
        default=3,
        metavar="{1,2,3}",
        help="number of recent overview frames sent per decision",
    )
    parser.add_argument(
        "--vlm-image-width",
        type=int,
        default=1280,
        help="maximum overview image width sent to the vision model",
    )
    parser.add_argument(
        "--vlm-target-crops",
        type=int,
        choices=range(0, 3),
        default=2,
        metavar="{0,1,2}",
        help="additional OCR target crops attached after overview images",
    )
    parser.add_argument(
        "--vlm-compact-output",
        action="store_true",
        help="request only the minimal action fields for small local models",
    )
    parser.add_argument(
        "--vlm-ocr-task-fallback",
        action="store_true",
        help="replace WAIT or unrelated actions with a high-confidence OCR task-panel click",
    )
    parser.add_argument(
        "--vlm-no-thinking",
        action="store_true",
        help="ask thinking-style models (GLM-4.xV) to answer without a reasoning pass",
    )
    parser.add_argument(
        "--vlm-json-object",
        action="store_true",
        help="use provider JSON-object mode instead of a JSON Schema response format",
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
