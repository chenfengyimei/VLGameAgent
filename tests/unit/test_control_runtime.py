from __future__ import annotations

import asyncio
import time
import unittest
from threading import Event, Thread
from unittest.mock import patch

from tests.helpers import identity
from uga.control.arbiter import ActionArbiter
from uga.control.executor import InputExecutor
from uga.control.gamepad_backend import OptionalGamepadBackend, RoutedInputBackend
from uga.control.input_backend import DryRunInputBackend
from uga.control.input_state import KeyboardStateMachine
from uga.control.lease import ControlMode, ControlOwner
from uga.control.lease_manager import ControlLeaseManager
from uga.control.lifetime import ActionLifetime
from uga.control.physical import GamepadAction, KeyboardAction, KeyEncoding
from uga.control.proposal import ActionProposal
from uga.control.scheduler import ActionScheduler
from uga.safety.emergency_stop import EmergencyStop
from uga.safety.focus_guard import AgentEnableState, FocusGuard, GuardReason
from uga.safety.shutdown import SafetyShutdown, ShutdownCause
from uga.safety.watchdog import RuntimeWatchdog, RuntimeWatchdogMonitor
from uga.time.clock import ManualClock, UGATime
from uga.windows.backend import WindowSnapshot
from uga.windows.coordinates import Rect
from uga.windows.integrity import IntegrityLevel
from uga.windows.window_identity import WindowIdentity


class FakeWindows:
    def __init__(self, target: WindowIdentity) -> None:
        self.target = target
        self.foreground: int | None = target.hwnd

    def discover(self, *, executable_name: str | None = None) -> tuple[WindowSnapshot, ...]:
        del executable_name
        return (self.snapshot(self.target.hwnd),)

    def snapshot(self, hwnd: int) -> WindowSnapshot:
        if hwnd != self.target.hwnd:
            raise LookupError(hwnd)
        return WindowSnapshot(
            identity=self.target,
            title="fixture",
            window_rect=Rect(0, 0, 100, 100),
            client_screen_rect=Rect(0, 0, 100, 100),
            dpi=96,
            is_visible=True,
            is_foreground=self.foreground == hwnd,
        )

    def foreground_hwnd(self) -> int | None:
        return self.foreground


class FakeIntegrity:
    def __init__(self) -> None:
        self.current = IntegrityLevel.MEDIUM
        self.target = IntegrityLevel.MEDIUM

    def current_process(self) -> IntegrityLevel:
        return self.current

    def process(self, pid: int) -> IntegrityLevel:
        del pid
        return self.target


class FakeGamepadDriver:
    def __init__(self) -> None:
        self.updates: list[dict[str, float | int]] = []
        self.neutral_count = 0

    def update(self, **state: float | int) -> None:
        self.updates.append(state)

    def neutral(self) -> None:
        self.neutral_count += 1


class FailingInputBackend(DryRunInputBackend):
    def submit(self, action: object) -> None:
        del action
        raise RuntimeError("injected backend failure")


class BlockingReleaseBackend(DryRunInputBackend):
    def __init__(self) -> None:
        super().__init__()
        self.release_started = Event()
        self.allow_release = Event()

    def release_all(self) -> None:
        self.release_started.set()
        if not self.allow_release.wait(timeout=1.0):
            raise TimeoutError("release was not allowed to complete")
        super().release_all()


class ExplodingClock(ManualClock):
    """Clock backend whose reads fail once armed; probes must not disarm the watchdog."""

    def __init__(self, start: int) -> None:
        super().__init__(start)
        self.armed = False

    def now(self) -> UGATime:
        if self.armed:
            raise RuntimeError("clock backend failed")
        return super().now()


class ControlRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = ManualClock(100)
        self.target = identity()
        self.windows = FakeWindows(self.target)
        self.integrity = FakeIntegrity()
        self.enabled = AgentEnableState(True)
        self.leases = ControlLeaseManager(self.clock)
        self.lease = self.leases.grant(
            ControlOwner.FAST_POLICY,
            ControlMode.PLAY_3D,
            1_000,
            confidence=0.9,
            reason="test",
        )
        self.backend = DryRunInputBackend()
        self.guard = FocusGuard(self.windows, self.integrity, self.leases, self.enabled)
        self.executor = InputExecutor(self.clock, self.backend, self.guard, self.leases)
        self.scheduler = ActionScheduler(self.clock, self.executor, self.leases)
        self.arbiter = ActionArbiter(self.clock, self.leases)

    def _proposal(self, actions: tuple[KeyboardAction, ...], end: int = 500) -> ActionProposal:
        lifetime = ActionLifetime(UGATime(100), UGATime(100), UGATime(end))
        return ActionProposal(
            "proposal",
            "test",
            self.lease.owner,
            self.lease.mode,
            self.lease.lease_id,
            self.lease.generation,
            lifetime,
            actions,
        )

    @staticmethod
    def _key(action_id: str, effective: int, expires: int) -> KeyboardAction:
        lifetime = ActionLifetime(UGATime(100), UGATime(effective), UGATime(expires))
        return KeyboardAction(action_id, lifetime, 0x11, True)

    def test_scheduler_executes_due_actions_in_order(self) -> None:
        actions = (self._key("now", 100, 300), self._key("later", 150, 300))
        decision = self.arbiter.decide(self._proposal(actions))
        self.assertEqual(self.scheduler.schedule(decision, self.target, self.lease), 2)
        self.assertEqual(self.scheduler.tick().executed, 1)
        self.clock.set(150)
        stats = self.scheduler.tick()
        self.assertEqual(stats.scheduled, 2)
        self.assertEqual(stats.executed, 2)
        self.assertEqual([action.action_id for action in self.backend.actions], ["now", "later"])

    def test_scheduler_never_backfills_expired_action(self) -> None:
        action = self._key("stale", 100, 110)
        self.scheduler.schedule(
            self.arbiter.decide(self._proposal((action,))), self.target, self.lease
        )
        self.clock.set(111)
        stats = self.scheduler.tick()
        self.assertEqual(stats.expired, 1)
        self.assertEqual(self.backend.actions, [])
        self.assertEqual(self.backend.release_count, 1)

    def test_lease_revoke_neutralizes_and_flushes(self) -> None:
        future = self._key("future", 200, 400)
        self.scheduler.schedule(
            self.arbiter.decide(self._proposal((future,))), self.target, self.lease
        )

        self.assertTrue(self.leases.revoke(self.lease.lease_id))

        self.assertEqual(self.scheduler.stats().queued, 0)
        self.assertEqual(self.backend.release_count, 1)

    def test_lease_replacement_neutralizes_previous_authority(self) -> None:
        self.leases.grant(
            ControlOwner.EMERGENCY,
            ControlMode.PLAY_3D,
            1_000,
            confidence=1.0,
            reason="replace",
        )

        self.assertEqual(self.backend.release_count, 1)

    def test_replacement_is_not_published_before_neutralization(self) -> None:
        clock = ManualClock(100)
        leases = ControlLeaseManager(clock)
        old_lease = leases.grant(
            ControlOwner.FAST_POLICY,
            ControlMode.PLAY_3D,
            1_000,
            confidence=0.9,
            reason="old",
        )
        backend = BlockingReleaseBackend()
        executor = InputExecutor(
            clock,
            backend,
            FocusGuard(self.windows, self.integrity, leases, self.enabled),
            leases,
        )
        ActionScheduler(clock, executor, leases)
        replacement: list[object] = []
        observed: list[object] = []
        grant_thread = Thread(
            target=lambda: replacement.append(
                leases.grant(
                    ControlOwner.EMERGENCY,
                    ControlMode.PLAY_3D,
                    1_000,
                    confidence=1.0,
                    reason="new",
                )
            )
        )
        grant_thread.start()
        self.assertTrue(backend.release_started.wait(timeout=1.0))
        current_thread = Thread(target=lambda: observed.append(leases.current()))
        current_thread.start()
        time.sleep(0.01)
        self.assertTrue(current_thread.is_alive())
        backend.allow_release.set()
        grant_thread.join(timeout=1.0)
        current_thread.join(timeout=1.0)

        self.assertFalse(grant_thread.is_alive())
        self.assertFalse(current_thread.is_alive())
        self.assertNotEqual(observed, [old_lease])
        self.assertEqual(observed, replacement)

    def test_passive_lease_expiry_neutralizes(self) -> None:
        self.clock.set(1_101)

        self.assertIsNone(self.leases.current())
        self.assertEqual(self.backend.release_count, 1)

    def test_scheduler_stop_neutralizes(self) -> None:
        stop = asyncio.Event()
        stop.set()

        asyncio.run(self.scheduler.run(stop))

        self.assertEqual(self.backend.release_count, 1)

    def test_backend_failure_neutralizes_and_flushes(self) -> None:
        leases = ControlLeaseManager(self.clock)
        lease = leases.grant(
            ControlOwner.FAST_POLICY,
            ControlMode.PLAY_3D,
            1_000,
            confidence=0.9,
            reason="failure",
        )
        backend = FailingInputBackend()
        executor = InputExecutor(
            self.clock,
            backend,
            FocusGuard(self.windows, self.integrity, leases, self.enabled),
            leases,
        )
        scheduler = ActionScheduler(self.clock, executor, leases)
        proposal = ActionProposal(
            "failing-proposal",
            "test",
            lease.owner,
            lease.mode,
            lease.lease_id,
            lease.generation,
            ActionLifetime(UGATime(100), UGATime(100), UGATime(500)),
            (self._key("failing", 100, 300), self._key("future", 200, 400)),
        )
        scheduler.schedule(ActionArbiter(self.clock, leases).decide(proposal), self.target, lease)

        with self.assertRaisesRegex(RuntimeError, "injected backend failure"):
            scheduler.tick()

        self.assertEqual(backend.release_count, 1)
        self.assertEqual(scheduler.stats().queued, 0)

    def test_focus_loss_releases_and_flushes_future_actions(self) -> None:
        actions = (self._key("now", 100, 300), self._key("future", 150, 300))
        self.scheduler.schedule(
            self.arbiter.decide(self._proposal(actions)), self.target, self.lease
        )
        self.windows.foreground = 999
        stats = self.scheduler.tick()
        self.assertEqual(stats.queued, 0)
        self.assertEqual(stats.flushed, 1)
        self.assertEqual(self.backend.release_count, 1)

    def test_focus_loss_counts_other_due_actions_as_flushed(self) -> None:
        actions = (self._key("first", 100, 300), self._key("second", 100, 300))
        self.scheduler.schedule(
            self.arbiter.decide(self._proposal(actions)), self.target, self.lease
        )
        self.windows.foreground = 999
        self.assertEqual(self.scheduler.tick().flushed, 1)

    def test_focus_guard_fails_closed_on_integrity_mismatch(self) -> None:
        self.integrity.target = IntegrityLevel.HIGH
        decision = self.guard.check(self.target, self.lease)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, GuardReason.INTEGRITY_INCOMPATIBLE)

    def test_watchdog_trip_revokes_flushes_releases_and_pauses(self) -> None:
        future = self._key("future", 200, 400)
        self.scheduler.schedule(
            self.arbiter.decide(self._proposal((future,))), self.target, self.lease
        )
        shutdown = SafetyShutdown(
            self.clock, self.leases, self.scheduler, self.executor, self.enabled
        )
        watchdog = RuntimeWatchdog(self.clock, shutdown, timeout_ns=20)
        self.clock.set(121)
        status = watchdog.check()
        self.assertEqual(status.trip.cause if status.trip else None, ShutdownCause.WATCHDOG_TIMEOUT)
        self.assertFalse(self.enabled.get())
        self.assertFalse(self.leases.validate(self.lease))
        self.assertEqual(self.scheduler.stats().queued, 0)
        self.assertEqual(self.backend.release_count, 1)
        self.assertFalse(watchdog.heartbeat())

    def test_emergency_stop_is_latched_and_idempotent(self) -> None:
        shutdown = SafetyShutdown(
            self.clock, self.leases, self.scheduler, self.executor, self.enabled
        )
        emergency = EmergencyStop(shutdown)
        first = emergency.trigger()
        second = emergency.trigger()
        self.assertIs(first, second)
        self.assertEqual(first.cause, ShutdownCause.EMERGENCY_HOTKEY)
        self.assertEqual(self.backend.release_count, 1)

    def test_watchdog_monitor_trips_without_main_loop_polling(self) -> None:
        shutdown = SafetyShutdown(
            self.clock, self.leases, self.scheduler, self.executor, self.enabled
        )
        watchdog = RuntimeWatchdog(self.clock, shutdown, timeout_ns=20)
        monitor = RuntimeWatchdogMonitor(watchdog, poll_interval_s=0.001)
        self.clock.set(121)
        monitor.start()
        try:
            for _ in range(100):
                if shutdown.tripped is not None:
                    break
                time.sleep(0.001)
        finally:
            monitor.close()
        self.assertIsNotNone(shutdown.tripped)
        self.assertEqual(self.backend.release_count, 1)

    def test_shutdown_trip_survives_clock_failure(self) -> None:
        clock = ExplodingClock(100)
        leases = ControlLeaseManager(clock)
        backend = DryRunInputBackend()
        enabled = AgentEnableState(True)
        guard = FocusGuard(self.windows, self.integrity, leases, enabled)
        executor = InputExecutor(clock, backend, guard, leases)
        scheduler = ActionScheduler(clock, executor, leases)
        shutdown = SafetyShutdown(clock, leases, scheduler, executor, enabled)
        lease = leases.grant(
            ControlOwner.FAST_POLICY,
            ControlMode.PLAY_3D,
            1_000,
            confidence=0.9,
            reason="test",
        )
        clock.armed = True
        trip = EmergencyStop(shutdown).trigger()
        self.assertIsNotNone(trip)
        self.assertFalse(enabled.get())
        self.assertEqual(backend.release_count, 1)
        self.assertFalse(leases.validate(lease, UGATime(101)))
        if trip is not None:
            self.assertTrue(
                any("clock unavailable" in error for error in trip.cleanup_errors)
            )

    def test_monitor_keeps_enforcement_alive_when_probe_raises(self) -> None:
        clock = ExplodingClock(100)
        leases = ControlLeaseManager(clock)
        backend = DryRunInputBackend()
        enabled = AgentEnableState(True)
        guard = FocusGuard(self.windows, self.integrity, leases, enabled)
        executor = InputExecutor(clock, backend, guard, leases)
        scheduler = ActionScheduler(clock, executor, leases)
        shutdown = SafetyShutdown(clock, leases, scheduler, executor, enabled)
        watchdog = RuntimeWatchdog(clock, shutdown, timeout_ns=20)
        monitor = RuntimeWatchdogMonitor(watchdog, poll_interval_s=0.001)
        clock.armed = True
        monitor.start()
        try:
            for _ in range(200):
                if shutdown.tripped is not None:
                    break
                time.sleep(0.001)
        finally:
            monitor.close()
        trip = shutdown.tripped
        self.assertIsNotNone(trip)
        if trip is not None:
            self.assertEqual(trip.cause, ShutdownCause.RUNTIME_FAILURE)
        self.assertFalse(enabled.get())
        self.assertEqual(backend.release_count, 1)

    def test_monitor_survives_untrippable_shutdown(self) -> None:
        clock = ExplodingClock(100)
        leases = ControlLeaseManager(clock)
        backend = DryRunInputBackend()
        enabled = AgentEnableState(True)
        guard = FocusGuard(self.windows, self.integrity, leases, enabled)
        executor = InputExecutor(clock, backend, guard, leases)
        scheduler = ActionScheduler(clock, executor, leases)
        shutdown = SafetyShutdown(clock, leases, scheduler, executor, enabled)
        watchdog = RuntimeWatchdog(clock, shutdown, timeout_ns=20)
        monitor = RuntimeWatchdogMonitor(watchdog, poll_interval_s=0.001)
        clock.armed = True
        with patch.object(SafetyShutdown, "trip", side_effect=RuntimeError("trip failed")):
            monitor.start()
            try:
                for _ in range(50):
                    time.sleep(0.002)
            finally:
                alive_before_close = monitor._thread.is_alive()
                monitor.close()
        self.assertTrue(alive_before_close, "monitor thread must outlive failing trips")


class StateAndPolicyTests(unittest.TestCase):
    def test_keyboard_state_machine_tracks_three_views(self) -> None:
        observed = {(KeyEncoding.SCAN_CODE, 0x11, False)}
        machine = KeyboardStateMachine(lambda key: key in observed)
        lifetime = ActionLifetime(UGATime(1), UGATime(1), UGATime(10))
        down = KeyboardAction("down", lifetime, 0x11, True)
        machine.desire(down)
        machine.submitted(down)
        snapshot = machine.snapshot()
        self.assertEqual(snapshot.desired, observed)
        self.assertEqual(snapshot.submitted, observed)
        self.assertEqual(snapshot.observed, observed)

    def test_optional_gamepad_routes_and_neutralizes(self) -> None:
        driver = FakeGamepadDriver()
        gamepad = OptionalGamepadBackend(driver)
        keyboard = DryRunInputBackend()
        routed = RoutedInputBackend(keyboard, gamepad)
        lifetime = ActionLifetime(UGATime(1), UGATime(1), UGATime(10))
        action = GamepadAction("pad", lifetime, left_x=0.5, buttons=1)
        routed.submit(action)
        routed.release_all()
        self.assertEqual(driver.updates[0]["left_x"], 0.5)
        self.assertEqual(driver.neutral_count, 1)
        self.assertEqual(keyboard.release_count, 1)


if __name__ == "__main__":
    unittest.main()
