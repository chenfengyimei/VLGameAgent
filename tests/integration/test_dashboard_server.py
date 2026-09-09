from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from uga.control.lease import ControlMode
from uga.core.errors import ContractViolation
from uga.dashboard.controller import DashboardCommandRouter
from uga.dashboard.server import create_dashboard_server
from uga.dashboard.state import DashboardState


class RecordingOperatorControl:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def start(self) -> None:
        self.commands.append("start")

    def pause(self) -> None:
        self.commands.append("pause")

    def resume(self) -> None:
        self.commands.append("resume")

    def stop(self) -> None:
        self.commands.append("stop")

    def take_control(self) -> None:
        self.commands.append("take_control")

    def release_control(self) -> None:
        self.commands.append("release_control")

    def emergency_release(self) -> None:
        self.commands.append("emergency_release")


def dashboard_state() -> DashboardState:
    return DashboardState(
        "frame-1",
        None,
        "reach target",
        None,
        ControlMode.PLAY_3D,
        None,
        None,
        None,
        0.9,
        False,
        30.0,
        5.0,
        25.0,
        None,
        0,
        0,
        None,
    )


class DashboardServerTests(unittest.TestCase):
    def test_loopback_server_exposes_state_and_authenticated_commands(self) -> None:
        control = RecordingOperatorControl()
        token = "test-token-0123456789abcdef0123456789"
        server = create_dashboard_server(
            dashboard_state,
            DashboardCommandRouter(control),
            port=0,
            csrf_token=token,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urllib.request.urlopen(base, timeout=2) as response:
                document = response.read().decode("utf-8")
            self.assertNotIn(token, document)
            self.assertNotIn("uga-csrf", document)
            self.assertIn("/api/state", document)
            with urllib.request.urlopen(f"{base}/api/state", timeout=2) as response:
                payload = json.load(response)
            self.assertEqual(payload["mode"], "play_3d")

            unauthenticated = urllib.request.Request(
                f"{base}/api/commands/emergency_release", method="POST"
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(unauthenticated, timeout=2)
            self.assertEqual(raised.exception.code, 403)

            authenticated = urllib.request.Request(
                f"{base}/api/commands/emergency_release",
                method="POST",
                headers={
                    "X-UGA-CSRF": token,
                    "Origin": base,
                },
            )
            with urllib.request.urlopen(authenticated, timeout=2) as response:
                self.assertEqual(response.status, 204)
            self.assertEqual(control.commands, ["emergency_release"])

            wrong_host = urllib.request.Request(
                f"{base}/api/state",
                headers={"Host": "attacker.example"},
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(wrong_host, timeout=2)
            self.assertEqual(raised.exception.code, 403)

            cross_origin = urllib.request.Request(
                f"{base}/api/commands/emergency_release",
                method="POST",
                headers={
                    "X-UGA-CSRF": token,
                    "Origin": "https://evil.example",
                },
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(cross_origin, timeout=2)
            self.assertEqual(raised.exception.code, 403)
            self.assertEqual(control.commands, ["emergency_release"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_non_loopback_binding_is_rejected(self) -> None:
        with self.assertRaisesRegex(ContractViolation, "loopback"):
            create_dashboard_server(
                dashboard_state,
                DashboardCommandRouter(RecordingOperatorControl()),
                host="0.0.0.0",
            )
        with self.assertRaisesRegex(ContractViolation, "loopback"):
            create_dashboard_server(
                dashboard_state,
                DashboardCommandRouter(RecordingOperatorControl()),
                host="::1",
            )

    def test_weak_caller_supplied_csrf_token_is_rejected(self) -> None:
        with self.assertRaisesRegex(ContractViolation, "at least 32 characters"):
            create_dashboard_server(
                dashboard_state,
                DashboardCommandRouter(RecordingOperatorControl()),
                port=0,
                csrf_token="short",
            )


if __name__ == "__main__":
    unittest.main()
