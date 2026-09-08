from __future__ import annotations

import hmac
import ipaddress
import json
import secrets
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from uga.core.errors import ContractViolation
from uga.dashboard.controller import DashboardCommandRouter
from uga.dashboard.state import DashboardCommand, DashboardState, render_dashboard

DashboardStateProvider = Callable[[], DashboardState]


class DashboardHttpServer(ThreadingHTTPServer):
    state_provider: DashboardStateProvider
    command_router: DashboardCommandRouter
    csrf_token: str

    def __init__(
        self,
        address: tuple[str, int],
        state_provider: DashboardStateProvider,
        command_router: DashboardCommandRouter,
        csrf_token: str,
    ) -> None:
        self.state_provider = state_provider
        self.command_router = command_router
        self.csrf_token = csrf_token
        super().__init__(address, DashboardRequestHandler)


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server: DashboardHttpServer

    def do_GET(self) -> None:
        if not self._loopback_client():
            self._respond(HTTPStatus.FORBIDDEN, b"loopback clients only", "text/plain")
            return
        if self.path == "/":
            document = render_dashboard(
                self.server.state_provider(), live=True, csrf_token=self.server.csrf_token
            ).encode("utf-8")
            self._respond(HTTPStatus.OK, document, "text/html; charset=utf-8")
            return
        if self.path == "/api/state":
            payload = json.dumps(self.server.state_provider().to_payload(), sort_keys=True).encode(
                "utf-8"
            )
            self._respond(HTTPStatus.OK, payload, "application/json")
            return
        self._respond(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

    def do_POST(self) -> None:
        if not self._loopback_client():
            self._respond(HTTPStatus.FORBIDDEN, b"loopback clients only", "text/plain")
            return
        supplied = self.headers.get("X-UGA-CSRF", "")
        if not hmac.compare_digest(supplied, self.server.csrf_token):
            self._respond(HTTPStatus.FORBIDDEN, b"invalid CSRF token", "text/plain")
            return
        prefix = "/api/commands/"
        if not self.path.startswith(prefix):
            self._respond(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            return
        try:
            command = DashboardCommand(self.path.removeprefix(prefix))
            self.server.command_router.execute(command)
        except ValueError:
            self._respond(HTTPStatus.NOT_FOUND, b"unknown command", "text/plain")
            return
        except RuntimeError as error:
            self._respond(HTTPStatus.CONFLICT, str(error).encode("utf-8"), "text/plain")
            return
        self._respond(HTTPStatus.NO_CONTENT, b"", "text/plain")

    def _loopback_client(self) -> bool:
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return False

    def _respond(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'"
        )
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def create_dashboard_server(
    state_provider: DashboardStateProvider,
    command_router: DashboardCommandRouter,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    csrf_token: str | None = None,
) -> DashboardHttpServer:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise ContractViolation("dashboard host must be a loopback IP address") from error
    if not address.is_loopback or not 0 <= port <= 65535:
        raise ContractViolation("dashboard must bind to a valid loopback address and port")
    return DashboardHttpServer(
        (host, port), state_provider, command_router, csrf_token or secrets.token_urlsafe(32)
    )
