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
import os
import re
import signal
import time
import uuid
from pathlib import Path
from types import FrameType

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
from uga.core.errors import CaptureTimeoutError
from uga.core.events import EventBus
from uga.environment.generic import GenericEnvironment
from uga.environment.profile import GameProfile, load_game_profile
from uga.observation.buffer import TemporalObservationBuffer
from uga.observation.builder import ObservationBuilder, ObservationInputs
from uga.policy.chunk_controller import ActionChunkController
from uga.policy.scripted_tap import ScriptedTapPolicy
from uga.recording.episode_writer import EpisodeWriter
from uga.recording.schema import EpisodeMetadata, EpisodeResult
from uga.recording.video import PyAvVideoRecorder
from uga.release.fixture_qualification import _activate
from uga.safety.emergency_stop import Win32EmergencyHotkey
from uga.safety.focus_guard import AgentEnableState, FocusGuard
from uga.time.clock import PerfCounterClock
from uga.windows.backend import Win32WindowBackend, WindowSnapshot
from uga.windows.integrity import Win32IntegrityProvider

_TAP_FRACTION_DEFAULT = (0.5, 0.79)
_CAPTURE_STALL_BUDGET_S = 10.0


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
    windows: Win32WindowBackend, profile: GameProfile
) -> WindowSnapshot:
    pattern = re.compile(profile.window_title_pattern or r".*")
    matches = [
        snapshot
        for snapshot in windows.discover()
        if pattern.fullmatch(snapshot.title) is not None
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"expected exactly one window matching {profile.window_title_pattern!r};"
            f" found {len(matches)}"
        )
    return matches[0]


async def _run(args: argparse.Namespace) -> int:
    if args.duration_seconds < 0:
        raise SystemExit("--duration-seconds must be >= 0 (0 = run until stopped)")
    if args.tap_interval_seconds < 0:
        raise SystemExit("--tap-interval-seconds must be >= 0 (0 = tap once)")
    profile = load_game_profile(args.profile)
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
    while time.monotonic() < deadline:
        try:
            backend.capture()
            primed = True
            break
        except CaptureTimeoutError:
            continue
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
    environment = GenericEnvironment(profile)
    arbiter = ActionArbiter(clock, leases)

    client = target.client_screen_rect
    tap_x = round(client.left + client.width * args.tap_x_fraction)
    tap_y = round(client.top + client.height * args.tap_y_fraction)
    policy = ScriptedTapPolicy(
        [(args.tap_delay, tap_x, tap_y)],
        repeat_interval_s=args.tap_interval_seconds or None,
    )

    controller = ActionChunkController(environment, arbiter, scheduler)

    frames = FrameRingBuffer()
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

    class CaptureSource:
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
                    return frames.publish(frame)
                except CaptureTimeoutError:
                    if time.monotonic() >= deadline:
                        raise

    loop = RealtimeAgentLoop(
        clock=clock,
        capture=CaptureSource(),
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
    hotkey.start()

    started = time.monotonic()
    print(f"running: game={profile.game_id} target={target.title} tap=({tap_x}, {tap_y})")
    task = asyncio.create_task(
        loop.run(stop, observation_hz=args.observation_hz)
    )
    try:
        while not task.done():
            if 0 < args.duration_seconds <= time.monotonic() - started:
                break
            await asyncio.sleep(0.2)
    except (KeyboardInterrupt, asyncio.CancelledError):
        stop_state["user"] = True
    finally:
        stop.set()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        scheduler.neutralize()
        leases.revoke_all(notify=False)
        backend.stop()
        hotkey.close()
        if previous_handler is not None:
            signal.signal(signal.SIGINT, previous_handler)
    if recorder is not None:
        recorder.set_metrics(
            {
                "capture_frames": 0,
                "scheduled_actions": scheduler.stats().executed,
                "executed_actions": scheduler.stats().executed,
                "action_execution_ratio": 1.0,
            }
        )
        result = EpisodeResult.ABORTED if stop_state["user"] else EpisodeResult.SUCCESS
        episode_path = recorder.finalize(result, clock.now())
        print(f"episode: {episode_path}")
    print(f"taps executed: {scheduler.stats().executed}")
    return 0


def _client_fraction(value: str) -> float:
    fraction = float(value)
    if not 0.0 <= fraction <= 1.0:
        raise argparse.ArgumentTypeError("client fraction must be within [0, 1]")
    return fraction


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the UGA agent against a live window")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--goal", default="Interact with the target")
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
    parser.add_argument("--record", type=Path)
    return parser


def main(args: argparse.Namespace) -> int:
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main(build_parser().parse_args()))
