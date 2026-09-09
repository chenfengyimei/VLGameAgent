from __future__ import annotations

import contextlib
import json
import math
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from uga.capture.base import CaptureBackend
from uga.capture.diagnostics import CaptureDiagnosticsAccumulator
from uga.capture.dxgi import DXGIDuplicationBackend
from uga.capture.fallback import GDIFallbackCaptureBackend
from uga.capture.frame import BufferKind, Frame, PixelFormat
from uga.capture.registry import CaptureBackendRegistry
from uga.capture.windows_graphics_capture import WindowsGraphicsCaptureBackend
from uga.control.arbiter import ActionArbiter
from uga.control.canonical import CanonicalAction
from uga.control.executor import InputExecutor
from uga.control.lease import ControlLease, ControlMode, ControlOwner
from uga.control.lease_manager import ControlLeaseManager
from uga.control.lifetime import ActionLifetime
from uga.control.physical import (
    AbsolutePointerAction,
    KeyboardAction,
    KeyEncoding,
    MouseButton,
    MouseButtonAction,
    PhysicalAction,
    RelativeMouseAction,
)
from uga.control.proposal import ActionProposal
from uga.control.scheduler import ActionScheduler
from uga.control.windows_input import SendInputBackend
from uga.core.errors import BackendUnavailableError, CaptureTimeoutError, ContractViolation
from uga.dataset.validator import DatasetValidator
from uga.environment.fixture_world import FixtureScenario, FixtureWorld, fixture_policy_features
from uga.recording.episode_writer import EpisodeWriter
from uga.recording.replay import ReplayEngine
from uga.recording.schema import ActionProvenance, EpisodeMetadata, EpisodeResult
from uga.recording.video import PyAvVideoRecorder
from uga.safety.emergency_stop import EmergencyStop, Win32EmergencyHotkey
from uga.safety.environment_policy import (
    EnvironmentClass,
    EnvironmentSafetyManifest,
    require_safe_environment,
)
from uga.safety.focus_guard import AgentEnableState, FocusGuard
from uga.safety.shutdown import SafetyShutdown, SafetyTrip, ShutdownCause
from uga.safety.watchdog import RuntimeWatchdog, RuntimeWatchdogMonitor
from uga.time.clock import PerfCounterClock, UGATime
from uga.windows.backend import Win32WindowBackend, WindowSnapshot
from uga.windows.coordinates import CoordinateSpace
from uga.windows.integrity import Win32IntegrityProvider
from uga.windows.window_identity import WindowIdentity


@dataclass(frozen=True, slots=True)
class FixtureVisualState:
    player_pixels: int
    player_center: tuple[float, float] | None
    target_pixels: int
    target_center: tuple[float, float] | None
    success_pixels: int

    @property
    def success_visible(self) -> bool:
        return self.success_pixels >= 20


def _matches_rgb(
    blue: int,
    green: int,
    red: int,
    expected: tuple[int, int, int],
    tolerance: int = 12,
) -> bool:
    expected_red, expected_green, expected_blue = expected
    return (
        abs(red - expected_red) <= tolerance
        and abs(green - expected_green) <= tolerance
        and abs(blue - expected_blue) <= tolerance
    )


def analyze_fixture_frame(frame: Frame) -> FixtureVisualState:
    """Read only developer-owned fixture colors from a captured CPU frame."""
    if frame.buffer_handle.kind != BufferKind.CPU_BYTES:
        raise ContractViolation("fixture analysis requires CPU-addressable pixels")
    if frame.pixel_format not in (PixelFormat.BGRA8, PixelFormat.RGBA8):
        raise ContractViolation("fixture analysis requires a four-channel pixel format")
    source = frame.buffer_handle.readonly_view().cast("B")
    colors = {
        "player": (56, 189, 248),
        "target": (34, 197, 94),
        "success": (250, 204, 21),
    }
    counts = {name: 0 for name in colors}
    x_sums = {name: 0 for name in colors}
    y_sums = {name: 0 for name in colors}
    rgba = frame.pixel_format == PixelFormat.RGBA8
    for y in range(frame.height):
        row = y * frame.stride_bytes
        for x in range(frame.width):
            offset = row + x * 4
            if rgba:
                red, green, blue = source[offset], source[offset + 1], source[offset + 2]
            else:
                blue, green, red = source[offset], source[offset + 1], source[offset + 2]
            for name, expected in colors.items():
                if _matches_rgb(blue, green, red, expected):
                    counts[name] += 1
                    x_sums[name] += x
                    y_sums[name] += y
                    break

    def center(name: str) -> tuple[float, float] | None:
        count = counts[name]
        return None if count == 0 else (x_sums[name] / count, y_sums[name] / count)

    return FixtureVisualState(
        counts["player"],
        center("player"),
        counts["target"],
        center("target"),
        counts["success"],
    )


def _visual_features(
    state: FixtureVisualState,
    width: int,
    height: int,
    scenario: FixtureScenario,
) -> tuple[float, ...]:
    if state.player_center is None or state.target_center is None:
        player = target = (0.0, 0.0)
    else:
        player, target = state.player_center, state.target_center
    return fixture_policy_features(
        player_x=player[0],
        player_y=player[1],
        target_x=target[0],
        target_y=target[1],
        width=width,
        height=height,
        success=state.success_visible,
        scenario=scenario,
    )


def _success_pixel_count(frame: Frame) -> int:
    if frame.buffer_handle.kind != BufferKind.CPU_BYTES:
        raise ContractViolation("fixture analysis requires CPU-addressable pixels")
    if frame.pixel_format == PixelFormat.BGRA8:
        color = (21, 204, 250)
    elif frame.pixel_format == PixelFormat.RGBA8:
        color = (250, 204, 21)
    else:
        raise ContractViolation("fixture analysis requires a four-channel pixel format")
    payload = frame.buffer_handle.readonly_view().tobytes()
    return sum(payload.count(bytes((*color, alpha))) for alpha in (0, 255))


def _lifetime(
    created: UGATime,
    effective_ns: int,
    *,
    ttl_ns: int = 1_000_000_000,
) -> ActionLifetime:
    return ActionLifetime(created, UGATime(effective_ns), UGATime(effective_ns + ttl_ns))


_CYCLE_NS = 5_000_000_000


def _build_fixture_cycle(
    created: UGATime,
    target: WindowSnapshot,
    cycle: int,
    base_ns: int,
    scenario: FixtureScenario,
) -> tuple[tuple[PhysicalAction, ...], int]:
    if cycle < 0 or created.value_ns > base_ns + 100_000_000:
        raise ContractViolation("fixture cycle must be scheduled before its first action")
    center_x = round((target.client_screen_rect.left + target.client_screen_rect.right) / 2)
    resume_y = round((target.client_screen_rect.top + target.client_screen_rect.bottom) / 2 - 20)

    def key(
        name: str,
        offset_ns: int,
        code: int,
        is_down: bool,
        encoding: KeyEncoding = KeyEncoding.SCAN_CODE,
    ) -> KeyboardAction:
        effective = base_ns + offset_ns
        return KeyboardAction(
            f"fixture-{cycle:04d}-{name}",
            _lifetime(created, effective),
            code,
            is_down,
            encoding,
        )

    actions: list[PhysicalAction] = [
        key("reset-down", 100_000_000, 19, True),
        key("reset-up", 150_000_000, 19, False),
        key("menu-down", 350_000_000, 0x1B, True, KeyEncoding.VIRTUAL_KEY),
        key("menu-up", 400_000_000, 0x1B, False, KeyEncoding.VIRTUAL_KEY),
        AbsolutePointerAction(
            f"fixture-{cycle:04d}-resume-move",
            _lifetime(created, base_ns + 550_000_000),
            center_x,
            resume_y,
            CoordinateSpace.PHYSICAL_SCREEN_PIXEL,
        ),
        MouseButtonAction(
            f"fixture-{cycle:04d}-resume-down",
            _lifetime(created, base_ns + 600_000_000),
            MouseButton.LEFT,
            True,
        ),
        MouseButtonAction(
            f"fixture-{cycle:04d}-resume-up",
            _lifetime(created, base_ns + 650_000_000),
            MouseButton.LEFT,
            False,
        ),
        RelativeMouseAction(
            f"fixture-{cycle:04d}-look",
            _lifetime(created, base_ns + 800_000_000),
            12,
            0,
        ),
    ]
    scan_codes = {"w": 17, "a": 30, "s": 31, "d": 32}
    world = FixtureWorld(scenario=scenario)
    movement_up_offset_ns = 1_000_000_000 + round(
        world.snapshot.distance_to_target / world.speed_pixels_per_second * 1_000_000_000
    )
    for movement_key in world.movement_keys:
        code = scan_codes[movement_key]
        actions.append(key(f"{movement_key}-down", 1_000_000_000, code, True))
        actions.append(key(f"{movement_key}-up", movement_up_offset_ns, code, False))
    actions.extend(
        (
            key("interact-down", 3_900_000_000, 18, True),
            key("interact-up", 3_950_000_000, 18, False),
        )
    )
    return tuple(actions), base_ns + 4_200_000_000


def _capture_registry(preference: str, windows: Win32WindowBackend) -> CaptureBackendRegistry:
    order = (
        (preference,)
        if preference != "auto"
        else ("windows_graphics_capture", "dxgi_duplication", "gdi_fallback")
    )
    registry = CaptureBackendRegistry(order)
    registry.register(WindowsGraphicsCaptureBackend(windows=windows))
    registry.register(DXGIDuplicationBackend(windows=windows))
    registry.register(GDIFallbackCaptureBackend(windows))
    return registry


def _activate(windows: Win32WindowBackend, hwnd: int, timeout_seconds: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if windows.foreground_hwnd() == hwnd:
            return True
        if windows.request_foreground(hwnd) and windows.foreground_hwnd() == hwnd:
            return True
        time.sleep(0.05)
    return False


@contextlib.contextmanager
def _owned_focus_sink(windows: Win32WindowBackend) -> Iterator[WindowSnapshot]:
    executable = Path(sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    if pythonw.is_file():
        executable = pythonw
    process = subprocess.Popen(
        [str(executable), "-m", "apps.example_game", "--focus-sink"],
        cwd=Path.cwd(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 8.0
        sink: WindowSnapshot | None = None
        while time.monotonic() < deadline:
            sink = next(
                (
                    item
                    for item in windows.discover()
                    if item.title == "UGA Focus Sink" and item.identity.pid == process.pid
                ),
                None,
            )
            if sink is not None:
                break
            if process.poll() is not None:
                break
            time.sleep(0.05)
        if sink is None:
            raise ContractViolation("developer-owned focus sink did not become ready")
        yield sink
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)


def _provenance(action: PhysicalAction, lease_id: str, observation_id: str) -> ActionProvenance:
    return ActionProvenance(
        action.action_id,
        "fixture-qualification",
        "fixture-script-v1",
        None,
        observation_id,
        "fixture-navigation",
        "fixture-complete-task",
        ControlMode.PLAY_3D.value,
        lease_id,
        1.0,
        False,
        action.lifetime,
    )


def _canonical_provenance(
    action: CanonicalAction, lease_id: str, observation_id: str
) -> ActionProvenance:
    return ActionProvenance(
        action.action_id,
        "fixture-qualification",
        "fixture-script-v1",
        None,
        observation_id,
        "fixture-navigation",
        "fixture-complete-task",
        ControlMode.PLAY_3D.value,
        lease_id,
        1.0,
        False,
        action.lifetime,
    )


def _exercise_focus_loss(
    windows: Win32WindowBackend,
    focus_sink: WindowSnapshot,
    target: WindowSnapshot,
    executor: InputExecutor,
    lease: ControlLease,
) -> dict[str, object]:
    try:
        moved = _activate(windows, focus_sink.identity.hwnd)
    except Exception as error:
        return {"exercised": False, "reason": str(error)}
    if not moved or windows.foreground_hwnd() == target.identity.hwnd:
        return {"exercised": False, "reason": "Windows denied the foreground transition"}
    now = PerfCounterClock().now()
    probe = KeyboardAction(
        f"focus-loss-probe-{uuid.uuid4().hex}",
        ActionLifetime(now, now, UGATime(now.value_ns + 1_000_000_000)),
        32,
        False,
    )
    result = executor.execute(probe, target.identity, lease)
    _activate(windows, target.identity.hwnd)
    return {
        "exercised": True,
        "executed": result.executed,
        "reason": str(result.reason),
        "passed": not result.executed and str(result.reason) == "target_not_foreground",
    }


def _exercise_emergency_hotkey(
    target: WindowSnapshot,
    executor: InputExecutor,
    lease: ControlLease,
    emergency: EmergencyStop,
) -> dict[str, object]:
    results: list[str] = []
    try:
        for name, code in (("control", 0x11), ("shift", 0x10), ("f12", 0x7B)):
            now = PerfCounterClock().now()
            action = KeyboardAction(
                f"emergency-{name}-{uuid.uuid4().hex}",
                ActionLifetime(now, now, UGATime(now.value_ns + 2_000_000_000)),
                code,
                True,
                KeyEncoding.VIRTUAL_KEY,
            )
            result = executor.execute(action, target.identity, lease)
            results.append(str(result.reason))
            time.sleep(0.03)
        deadline = time.monotonic() + 2.0
        while emergency.tripped is None and time.monotonic() < deadline:
            time.sleep(0.02)
        trip = emergency.tripped
        return {
            "registered": True,
            "input_results": results,
            "tripped": trip is not None,
            "cause": None if trip is None else trip.cause.value,
            "flushed_actions": None if trip is None else trip.flushed_actions,
            "cleanup_errors": [] if trip is None else list(trip.cleanup_errors),
            "passed": _emergency_hotkey_passed(results, trip),
        }
    finally:
        executor.release_all()


def _emergency_hotkey_passed(results: list[str], trip: SafetyTrip | None) -> bool:
    return (
        all(reason == "executed" for reason in results)
        and trip is not None
        and trip.cause == ShutdownCause.EMERGENCY_HOTKEY
        and not trip.cleanup_errors
    )


def _watchdog_timeout_passed(
    trip: SafetyTrip | None,
    enabled: AgentEnableState,
    lease_valid: bool,
) -> bool:
    return (
        trip is not None
        and trip.cause == ShutdownCause.WATCHDOG_TIMEOUT
        and not trip.cleanup_errors
        and not enabled.get()
        and not lease_valid
    )


def _exercise_watchdog_timeout(
    clock: PerfCounterClock,
    windows: Win32WindowBackend,
    keepalive: Callable[[], bool] | None,
) -> dict[str, object]:
    """Stall a dedicated supervision stack and verify the watchdog trips closed.

    The run's own watchdog stays live through ``keepalive``; a run that is
    already tripped (for example by the emergency-hotkey exercise) passes
    ``None`` because heartbeats would always report false there.
    """
    enabled = AgentEnableState(True)
    leases = ControlLeaseManager(clock)
    executor = InputExecutor(
        clock,
        SendInputBackend(),
        FocusGuard(windows, Win32IntegrityProvider(), leases, enabled),
        leases,
    )
    scheduler = ActionScheduler(clock, executor, leases)
    shutdown = SafetyShutdown(clock, leases, scheduler, executor, enabled)
    watchdog = RuntimeWatchdog(clock, shutdown, timeout_ns=5_000_000_000)
    monitor = RuntimeWatchdogMonitor(watchdog)
    lease = leases.grant(
        ControlOwner.FAST_POLICY,
        ControlMode.PLAY_3D,
        10_000_000_000,
        confidence=1.0,
        reason="fixture watchdog-timeout qualification",
    )
    if not watchdog.heartbeat():
        return {"exercised": False, "reason": "exercise watchdog could not start"}
    monitor.start()
    # Deliberately stop heartbeating: this is the stalled-supervision state
    # the live run must neutralize without any operator action.
    deadline = time.monotonic() + 8.0
    while shutdown.tripped is None and time.monotonic() < deadline:
        time.sleep(0.05)
        if keepalive is not None and not keepalive():
            monitor.close()
            return {
                "exercised": False,
                "reason": "run supervision tripped during the watchdog exercise",
            }
    trip = shutdown.tripped
    monitor.close()
    lease_valid = leases.validate(lease)
    return {
        "exercised": True,
        "tripped": trip is not None,
        "cause": None if trip is None else trip.cause.value,
        "flushed_actions": None if trip is None else trip.flushed_actions,
        "cleanup_errors": [] if trip is None else list(trip.cleanup_errors),
        "agent_disabled": not enabled.get(),
        "lease_revoked": not lease_valid,
        "passed": _watchdog_timeout_passed(trip, enabled, lease_valid),
    }


def _select_owned_fixture_target(
    windows: Win32WindowBackend,
    *,
    title_pattern: str,
    expected_pid: int,
    expected_identity: WindowIdentity | None,
) -> WindowSnapshot:
    if expected_pid <= 0:
        raise ContractViolation("fixture qualification requires a trusted positive process ID")
    pattern = re.compile(title_pattern)
    matches = tuple(
        item
        for item in windows.discover()
        if item.identity.pid == expected_pid and pattern.fullmatch(item.title)
    )
    if len(matches) != 1:
        raise ContractViolation(
            f"fixture qualification requires exactly one owned window; found {len(matches)}"
        )
    target = matches[0]
    if expected_identity is not None:
        expected_process_window = (
            expected_identity.hwnd,
            expected_identity.pid,
            expected_identity.executable_path_hash,
            expected_identity.process_start_time_100ns,
        )
        actual_process_window = (
            target.identity.hwnd,
            target.identity.pid,
            target.identity.executable_path_hash,
            target.identity.process_start_time_100ns,
        )
        if actual_process_window != expected_process_window:
            raise ContractViolation("fixture target identity changed after owned-process discovery")
    return target


def run_fixture_qualification(
    *,
    title_pattern: str,
    duration_seconds: float,
    target_fps: float,
    backend_preference: str,
    episode_root: Path,
    report_path: Path,
    allow_physical_input: bool,
    exercise_focus_loss: bool,
    exercise_emergency_hotkey: bool,
    exercise_watchdog_timeout: bool = False,
    fixture_scenario: str = FixtureScenario.EXPLORATION.value,
    expected_pid: int,
    expected_identity: WindowIdentity | None = None,
) -> Path:
    if not allow_physical_input:
        raise ContractViolation("fixture qualification requires --allow-physical-input")
    if duration_seconds < 4.5 or not math.isfinite(duration_seconds):
        raise ContractViolation("fixture qualification duration must be at least 4.5 seconds")
    if not 1 <= target_fps <= 120 or not math.isfinite(target_fps):
        raise ContractViolation("fixture qualification target FPS must be in [1, 120]")
    require_safe_environment(
        EnvironmentSafetyManifest(EnvironmentClass.DEVELOPER_OWNED, True, False, False)
    )
    scenario = FixtureScenario(fixture_scenario)
    fixture_world = FixtureWorld(scenario=scenario)
    windows = Win32WindowBackend()
    target = _select_owned_fixture_target(
        windows,
        title_pattern=title_pattern,
        expected_pid=expected_pid,
        expected_identity=expected_identity,
    )
    if not _activate(windows, target.identity.hwnd):
        raise ContractViolation("fixture window could not become foreground")

    registry = _capture_registry(backend_preference, windows)
    candidates = registry.candidates(target.identity)
    backend: CaptureBackend = registry.start_best(target.identity)
    clock = PerfCounterClock()
    started = clock.now()
    enabled = AgentEnableState(True)
    leases = ControlLeaseManager(clock)
    input_backend = SendInputBackend()
    guard = FocusGuard(windows, Win32IntegrityProvider(), leases, enabled)
    executor = InputExecutor(clock, input_backend, guard, leases)
    scheduler = ActionScheduler(clock, executor, leases)
    shutdown = SafetyShutdown(clock, leases, scheduler, executor, enabled)
    emergency = EmergencyStop(shutdown)
    emergency_listener = Win32EmergencyHotkey(emergency.trigger)
    watchdog = RuntimeWatchdog(clock, shutdown, timeout_ns=5_000_000_000)
    watchdog_monitor = RuntimeWatchdogMonitor(watchdog)
    episode_id = f"fixture-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    metadata = EpisodeMetadata(
        episode_id,
        fixture_world.game_id,
        "1.1",
        (round(target.client_screen_rect.width), round(target.client_screen_rect.height)),
        backend.backend_id,
        started.value_ns,
        "Navigate to the green target and interact",
        EpisodeResult.IN_PROGRESS,
        "0.1.0",
        "fixture-script-v1",
        False,
    )
    writer = EpisodeWriter(episode_root, metadata)
    writer.attach_video(PyAvVideoRecorder(writer.video_path, fps=round(target_fps)))
    diagnostics = CaptureDiagnosticsAccumulator(backend.backend_id)
    failed_backends: set[str] = set()
    capture_transitions: list[dict[str, object]] = []

    def capture_with_failover() -> Frame:
        nonlocal backend
        try:
            return backend.capture()
        except (BackendUnavailableError, CaptureTimeoutError) as error:
            failed_id = backend.backend_id
            failed_backends.add(failed_id)
            with contextlib.suppress(Exception):
                backend.stop()
            replacement = registry.start_best(
                target.identity,
                exclude=frozenset(failed_backends),
            )
            transition: dict[str, object] = {
                "from": failed_id,
                "to": replacement.backend_id,
                "error": str(error),
            }
            capture_transitions.append(transition)
            backend = replacement
            writer.record_event(
                f"capture-failover-{len(capture_transitions)}",
                clock.now(),
                transition,
            )
            return backend.capture()

    writer.record_event(
        "capture-selected",
        clock.now(),
        {"backend_id": backend.backend_id, "target_hwnd": target.identity.hwnd},
    )

    interval_ns = round(1_000_000_000 / target_fps)
    frame_count = 0
    initial_frame: Frame | None = None
    final_frame: Frame | None = None
    success_seen = False
    pending_checks: list[int] = []
    pending_observations: list[tuple[int, int, str, int]] = []
    episode_path: Path | None = None
    capture_started = clock.now()
    capture_ended = capture_started
    script_start_ns = capture_started.value_ns + 250_000_000
    cycle_count = max(1, math.floor((duration_seconds - 4.45) / 5.0) + 1)
    next_cycle = 0
    scheduled = 0
    canonical_recorded = 0
    accepted_proposals = 0
    observed_flushes = 0
    arbiter = ActionArbiter(clock, leases)
    scheduler_interval_ns = round(1_000_000_000 / ActionScheduler.DEFAULT_HZ)
    try:
        emergency_listener.start()
        watchdog_monitor.start()
        if not watchdog.heartbeat():
            raise ContractViolation("fixture safety supervision failed to start")
        capture_before = clock.now()
        first_frame = capture_with_failover()
        capture_after = clock.now()
        diagnostics.add(
            first_frame,
            (capture_after.value_ns - capture_before.value_ns) / 1_000_000,
        )
        writer.record_frame(first_frame)
        frame_count = 1
        initial_frame = first_frame
        final_frame = first_frame
        capture_started = capture_after
        capture_ended = capture_started
        script_start_ns = capture_started.value_ns + 250_000_000
        next_capture_ns = capture_started.value_ns + interval_ns
        next_scheduler_ns = capture_started.value_ns
        while True:
            if not watchdog.heartbeat():
                trip = shutdown.tripped
                cause = "unknown" if trip is None else trip.cause.value
                raise ContractViolation(f"fixture safety supervision tripped: {cause}")
            loop_before = clock.now()
            while next_cycle < cycle_count:
                base_ns = script_start_ns + next_cycle * _CYCLE_NS
                if loop_before.value_ns < base_ns - 250_000_000:
                    break
                if not _activate(windows, target.identity.hwnd):
                    raise ContractViolation("fixture focus could not be restored for next cycle")
                created = clock.now()
                actions, success_check = _build_fixture_cycle(
                    created,
                    target,
                    next_cycle,
                    base_ns,
                    scenario,
                )
                last_expiry = max(action.lifetime.expires_at.value_ns for action in actions)
                lease = leases.grant(
                    ControlOwner.FAST_POLICY,
                    ControlMode.PLAY_3D,
                    last_expiry - created.value_ns + 1_000_000_000,
                    confidence=1.0,
                    reason=f"developer-owned fixture cycle {next_cycle}",
                )
                observation_id = f"fixture-observation-{next_cycle:04d}"
                proposal = ActionProposal(
                    uuid.uuid4().hex,
                    "fixture-qualification",
                    lease.owner,
                    lease.mode,
                    lease.lease_id,
                    lease.generation,
                    ActionLifetime(
                        created,
                        actions[0].lifetime.effective_from,
                        UGATime(last_expiry),
                    ),
                    actions,
                    observation_id,
                    1.0,
                )
                decision = arbiter.decide(proposal)
                added = scheduler.schedule(decision, target.identity, lease)
                if not decision.accepted or added != len(actions):
                    raise ContractViolation("fixture cycle proposal was not fully scheduled")
                for action in actions:
                    writer.record_action(
                        action,
                        _provenance(action, lease.lease_id, observation_id),
                    )
                accepted_proposals += 1
                scheduled += added
                pending_checks.append(success_check)
                pending_observations.append(
                    (base_ns + 250_000_000, next_cycle, lease.lease_id, base_ns)
                )
                next_cycle += 1
            if loop_before.value_ns >= next_scheduler_ns:
                before_stats = scheduler.stats()
                after_stats = scheduler.tick()
                if after_stats.flushed > before_stats.flushed:
                    observed_flushes += after_stats.flushed - before_stats.flushed
                    writer.record_event(
                        f"guard-recovery-{observed_flushes}",
                        clock.now(),
                        {"flushed_actions": after_stats.flushed - before_stats.flushed},
                    )
                while next_scheduler_ns <= loop_before.value_ns:
                    next_scheduler_ns += scheduler_interval_ns

            capture_due = clock.now()
            if capture_due.value_ns >= next_capture_ns:
                capture_before = capture_due
                frame = capture_with_failover()
                capture_after = clock.now()
                diagnostics.add(
                    frame,
                    (capture_after.value_ns - capture_before.value_ns) / 1_000_000,
                )
                writer.record_frame(frame)
                frame_count += 1
                final_frame = frame
                while (
                    pending_observations
                    and frame.capture_timestamp.value_ns >= pending_observations[0][0]
                ):
                    _, observation_cycle, lease_id, observation_base_ns = pending_observations.pop(
                        0
                    )
                    visual = analyze_fixture_frame(frame)
                    observation_id = f"fixture-observation-{observation_cycle:04d}"
                    writer.record_observation(
                        observation_id,
                        frame.capture_timestamp,
                        {
                            "task": metadata.task,
                            "cycle": observation_cycle,
                            "source": "captured-screen",
                            "features": _visual_features(
                                visual,
                                frame.width,
                                frame.height,
                                scenario,
                            ),
                            "visual": asdict(visual),
                        },
                    )
                    move_x, move_y = fixture_world.canonical_movement
                    canonical = CanonicalAction(
                        f"fixture-{observation_cycle:04d}-canonical",
                        ActionLifetime(
                            frame.capture_timestamp,
                            UGATime(observation_base_ns + 1_000_000_000),
                            UGATime(observation_base_ns + 4_950_000_000),
                        ),
                        move_x=move_x,
                        move_y=move_y,
                        look_x=0.05,
                        interact=True,
                    )
                    writer.record_canonical_action(
                        canonical,
                        _canonical_provenance(canonical, lease_id, observation_id),
                    )
                    canonical_recorded += 1
                while pending_checks and frame.capture_timestamp.value_ns >= pending_checks[0]:
                    success_seen = success_seen or _success_pixel_count(frame) >= 20
                    pending_checks.pop(0)
                while next_capture_ns <= capture_after.value_ns:
                    next_capture_ns += interval_ns

            loop_after = clock.now()
            elapsed_seconds = (loop_after.value_ns - capture_started.value_ns) / 1_000_000_000
            if elapsed_seconds >= duration_seconds:
                capture_ended = loop_after
                assert final_frame is not None
                success_seen = success_seen or _success_pixel_count(final_frame) >= 20
                break
            next_due_ns = min(next_scheduler_ns, next_capture_ns)
            if next_cycle < cycle_count:
                next_cycle_schedule_ns = script_start_ns + next_cycle * _CYCLE_NS - 250_000_000
                next_due_ns = min(next_due_ns, next_cycle_schedule_ns)
            remaining_ns = next_due_ns - loop_after.value_ns
            if remaining_ns > 0:
                time.sleep(remaining_ns / 1_000_000_000)

        if exercise_focus_loss:
            if not watchdog.heartbeat():
                raise ContractViolation("fixture safety supervision tripped before focus test")
            focus_lease = leases.grant(
                ControlOwner.FAST_POLICY,
                ControlMode.PLAY_3D,
                5_000_000_000,
                confidence=1.0,
                reason="fixture focus-loss qualification",
            )
            with _owned_focus_sink(windows) as focus_sink:
                focus_report = _exercise_focus_loss(
                    windows,
                    focus_sink,
                    target,
                    executor,
                    focus_lease,
                )
        else:
            focus_report = {"exercised": False, "reason": "not requested"}
        if not _activate(windows, target.identity.hwnd):
            raise ContractViolation("fixture focus could not be restored before emergency test")
        if exercise_emergency_hotkey:
            if not watchdog.heartbeat():
                raise ContractViolation("fixture safety supervision tripped before emergency test")
            emergency_lease = leases.grant(
                ControlOwner.EMERGENCY,
                ControlMode.PLAY_3D,
                5_000_000_000,
                confidence=1.0,
                reason="fixture emergency-hotkey qualification",
            )
            emergency_report = _exercise_emergency_hotkey(
                target,
                executor,
                emergency_lease,
                emergency,
            )
        else:
            emergency_report = {"registered": False, "tripped": False, "reason": "not requested"}
        if exercise_watchdog_timeout:
            watchdog_report = _exercise_watchdog_timeout(
                clock,
                windows,
                None if shutdown.tripped is not None else watchdog.heartbeat,
            )
        else:
            watchdog_report = {"exercised": False, "reason": "not requested"}
        stats = scheduler.stats()
        guard_rejected = max(
            0,
            scheduled - stats.executed - stats.expired - stats.rejected - stats.flushed,
        )
        execution_ratio = stats.executed / scheduled if scheduled else 0.0
        control_passed = (
            next_cycle == cycle_count
            and canonical_recorded == cycle_count
            and stats.expired == 0
            and stats.rejected == 0
            and execution_ratio >= 0.95
        )
        ended = clock.now()
        diagnostic_report = diagnostics.summarize(
            elapsed_seconds=(capture_ended.value_ns - capture_started.value_ns) / 1_000_000_000
        )
        writer.record_event(
            "safety-checks-complete",
            ended,
            {"focus": focus_report, "emergency": emergency_report, "watchdog": watchdog_report},
        )
        writer.set_metrics(
            {
                "capture_frames": frame_count,
                "capture_effective_fps": diagnostic_report.effective_fps,
                "scheduled_actions": scheduled,
                "executed_actions": stats.executed,
                "guard_rejected_actions": guard_rejected,
                "action_execution_ratio": execution_ratio,
                "success_visible": int(success_seen),
            }
        )
        episode_result = (
            EpisodeResult.SUCCESS if success_seen and control_passed else EpisodeResult.FAILURE
        )
        episode_path = writer.finalize(episode_result, ended)
    except BaseException:
        with contextlib.suppress(Exception):
            writer.finalize(EpisodeResult.ABORTED, clock.now())
        raise
    finally:
        watchdog_monitor.close()
        emergency_listener.close()
        shutdown.trip(ShutdownCause.NORMAL_STOP)
        backend.stop()

    assert episode_path is not None
    assert initial_frame is not None and final_frame is not None
    initial_visual = analyze_fixture_frame(initial_frame)
    final_visual = analyze_fixture_frame(final_frame)
    success_seen = success_seen or final_visual.success_visible
    replay = ReplayEngine(episode_path).validation()
    quality = DatasetValidator().validate(episode_path)
    stats = scheduler.stats()
    focus_passed = not exercise_focus_loss or bool(focus_report.get("passed"))
    emergency_passed = not exercise_emergency_hotkey or bool(emergency_report.get("passed"))
    watchdog_passed = not exercise_watchdog_timeout or bool(watchdog_report.get("passed"))
    passed = (
        success_seen
        and control_passed
        and diagnostic_report.timestamp_regressions == 0
        and replay.action_count == scheduled + canonical_recorded
        and quality.status.value == "accepted"
        and focus_passed
        and emergency_passed
        and watchdog_passed
    )
    report = {
        "schema": "uga.fixture_qualification",
        "schema_version": "1.1",
        "passed": passed,
        "target": {
            "hwnd": target.identity.hwnd,
            "pid": target.identity.pid,
            "title": target.title,
            "dpi": target.dpi,
        },
        "capture_candidates": [
            {
                "backend_id": item.backend.backend_id,
                "available": item.probe.available,
                "score": item.probe.score,
                "reason": item.probe.reason,
            }
            for item in candidates
        ],
        "capture": asdict(diagnostic_report),
        "capture_runtime": {
            "initial_backend": diagnostic_report.backend_id,
            "final_backend": backend.backend_id,
            "transitions": capture_transitions,
        },
        "control": {
            "proposals_accepted": accepted_proposals,
            "cycles_planned": cycle_count,
            "cycles_scheduled": next_cycle,
            "scheduled": scheduled,
            "canonical_recorded": canonical_recorded,
            "guard_rejected": guard_rejected,
            "execution_ratio": execution_ratio,
            "observed_flushes": observed_flushes,
            "scheduler": asdict(stats),
            "focus_loss": focus_report,
            "emergency_hotkey": emergency_report,
            "watchdog_timeout": watchdog_report,
        },
        "visual": {
            "initial": None if initial_visual is None else asdict(initial_visual),
            "final": None if final_visual is None else asdict(final_visual),
            "success_seen": success_seen,
        },
        "recorder": {
            "episode_path": str(episode_path),
            "replay": asdict(replay),
            "quality": {
                "status": quality.status.value,
                "quality_score": quality.quality_score,
                "frame_count": quality.frame_count,
                "action_count": quality.action_count,
                "findings": [
                    {
                        "code": item.code,
                        "severity": int(item.severity),
                        "detail": item.detail,
                    }
                    for item in quality.findings
                ],
            },
        },
    }
    report_path = report_path.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not passed:
        raise ContractViolation(f"fixture qualification failed; inspect {report_path}")
    return report_path
