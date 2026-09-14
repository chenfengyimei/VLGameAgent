from __future__ import annotations

import http.client
import json
import socket
import unittest

from apps.agent.dashboard import DecisionDashboard
from uga.policy.decision_journal import DecisionJournal


def _available_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class DecisionDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.port = _available_port()
        self.dashboard = DecisionDashboard(
            DecisionJournal(),
            self.port,
            status_provider=lambda: {
                "status": "running",
                "goal": "打开 <网络>",
                "capture_frames": 42,
            },
            preview_provider=lambda: (b"\x89PNG\r\n", "image/png"),
        )
        self.dashboard.start()
        self.addCleanup(self.dashboard.stop)

    def _request(self, host: str, path: str = "/api/events") -> http.client.HTTPResponse:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        self.addCleanup(connection.close)
        connection.putrequest("GET", path, skip_host=True)
        connection.putheader("Host", host)
        connection.endheaders()
        return connection.getresponse()

    def test_loopback_host_receives_security_headers(self) -> None:
        response = self._request(f"127.0.0.1:{self.port}")

        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.getheader("X-Frame-Options"), "DENY")

    def test_runtime_status_and_preview_are_exposed(self) -> None:
        response = self._request(f"127.0.0.1:{self.port}")
        payload = json.loads(response.read())
        self.assertEqual(payload["runtime"]["status"], "running")
        self.assertEqual(payload["runtime"]["capture_frames"], 42)

        frame_response = self._request(f"127.0.0.1:{self.port}", "/api/frame")
        self.assertEqual(frame_response.status, 200)
        self.assertEqual(frame_response.getheader("Content-Type"), "image/png")
        self.assertEqual(frame_response.read(), b"\x89PNG\r\n")

    def test_page_renders_closed_loop_overview_and_escapes_goal(self) -> None:
        response = self._request(f"127.0.0.1:{self.port}", "/")
        page = response.read().decode("utf-8")

        self.assertIn("闭环运行面板", page)
        self.assertIn("运行中", page)
        self.assertIn("打开 &lt;网络&gt;", page)
        self.assertIn('src="/api/frame"', page)
        self.assertNotIn("打开 <网络>", page)

    def test_untrusted_host_is_rejected(self) -> None:
        response = self._request(f"attacker.example:{self.port}")

        self.assertEqual(response.status, 421)
        self.assertEqual(response.read(), b"misdirected request")

    def test_invalid_port_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "within"):
            DecisionDashboard(DecisionJournal(), 65536)

    def test_stop_before_start_does_not_block(self) -> None:
        dashboard = DecisionDashboard(DecisionJournal(), _available_port())

        dashboard.stop()


if __name__ == "__main__":
    unittest.main()
