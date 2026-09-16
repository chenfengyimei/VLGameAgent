from __future__ import annotations

import argparse
from pathlib import Path

from uga.control.lease import ControlMode
from uga.dashboard.controller import DashboardCommandRouter
from uga.dashboard.server import create_dashboard_server
from uga.dashboard.state import DashboardState, render_dashboard


class DisconnectedOperatorControl:
    def _unavailable(self) -> None:
        raise RuntimeError("runtime not connected")

    start = pause = resume = stop = take_control = release_control = emergency_release = (
        _unavailable
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Render or serve the UGA runtime dashboard")
    parser.add_argument("--output", type=Path, default=Path("dashboard.html"))
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    state = DashboardState(
        None,
        None,
        "runtime not connected",
        None,
        ControlMode.UNKNOWN,
        None,
        None,
        None,
        None,
        False,
        0.0,
        0.0,
        0.0,
        None,
        0,
        0,
        None,
        # D13: the standalone dashboard never connects to a runtime — render
        # the command buttons disabled so the UI cannot fake control success.
        runtime_connected=False,
    )
    if args.serve:
        server = create_dashboard_server(
            lambda: state,
            DashboardCommandRouter(DisconnectedOperatorControl()),
            host=args.host,
            port=args.port,
        )
        bound_host = server.server_address[0]
        if isinstance(bound_host, bytes):
            bound_host = bound_host.decode("ascii")
        # The token travels out-of-band through the URL fragment; fragments are
        # never sent back to the server and the page must not embed them.
        print(f"http://{bound_host}:{server.server_address[1]}/#uga-token={server.csrf_token}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    else:
        args.output.write_text(render_dashboard(state), encoding="utf-8")
        print(args.output.resolve())


if __name__ == "__main__":
    main()
