"""Lightweight run dashboard: an HTTP server exposing the live decision
journal to a browser page (standard library only, no new dependencies).

GET /            -> self-contained Chinese dashboard page (auto-refresh)
GET /api/events  -> journal snapshot as JSON
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from uga.policy.decision_journal import DecisionJournal

_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>VLGameAgent 运行面板</title>
<meta http-equiv="refresh" content="3">
<style>
 body {{ font-family: "Microsoft YaHei", sans-serif; margin: 16px;
        background:#10141c; color:#dde3ee; }}
 h1 {{ font-size: 20px; }} h2 {{ font-size: 16px; margin: 18px 0 8px; }}
 .cards {{ display:flex; gap:12px; flex-wrap:wrap; }}
 .card {{ background:#1a2030; border-radius:8px; padding:10px 16px; min-width:130px; }}
 .card .v {{ font-size:24px; font-weight:600; color:#7cc4ff; }}
 .card .k {{ font-size:12px; color:#8a93a8; }}
 table {{ border-collapse: collapse; width:100%; font-size:13px; }}
 th, td {{ border:1px solid #2a3245; padding:5px 8px; text-align:left; }}
 th {{ background:#1a2030; color:#9fb0cc; }}
 tr.kind-decision td:first-child {{ color:#7cc4ff; }}
 tr.kind-queued td:first-child {{ color:#9fe6a0; }}
 tr.kind-static_hold td:first-child {{ color:#d8c97c; }}
 tr.kind-stale_discard td:first-child {{ color:#ff9d7c; }}
 tr.kind-failure td:first-child, tr.kind-rate_limited td:first-child {{ color:#ff7c7c; }}
 .muted {{ color:#8a93a8; }}
 code {{ background:#1a2030; padding:1px 5px; border-radius:4px; }}
</style></head><body>
<h1>VLGameAgent 运行面板 <span class="muted" style="font-size:12px">(每 3 秒自动刷新)</span></h1>
<div class="cards">{cards}</div>
<h2>决策时间线（最新在前，最多 120 条）</h2>
<table><tr><th>类型</th><th>时间</th><th>耗时</th><th>动作</th><th>说明</th><th>任务</th><th>步骤</th><th>图片</th><th>回复摘要</th></tr>
{rows}
</table>
<h2>原始事件 (JSON)</h2>
<pre style="font-size:11px; color:#9fb0cc; white-space:pre-wrap;">{raw}</pre>
</body></html>"""

_KIND_LABELS = {
    "decision": "推理决策",
    "queued": "队列回放",
    "action": "执行动作",
    "static_hold": "静帧保持",
    "stale_discard": "过期丢弃",
    "failure": "推理失败",
    "rate_limited": "限流退避",
}


def _esc(value: object) -> str:
    import html

    if value is None:
        return '<span class="muted">-</span>'
    return html.escape(str(value))


def render_page(snapshot: dict[str, Any]) -> str:
    stats = snapshot.get("stats", {})
    events = snapshot.get("events", [])
    cards = [
        ("总事件", stats.get("total", 0)),
        ("推理决策", stats.get("decisions", 0)),
        ("静帧保持", stats.get("static_holds", 0)),
        ("队列回放", stats.get("queued_replays", 0)),
    ]
    if "latency_avg_s" in stats:
        avg = stats["latency_avg_s"]
        p50 = stats.get("latency_p50_s", 0)
        peak = stats.get("latency_max_s", 0)
        cards.append(("平均推理耗时", f"{avg:.1f}s"))
        cards.append(("P50 / 最大", f"{p50:.1f} / {peak:.1f}s"))
    cards_html = "".join(
        f'<div class="card"><div class="v">{value}</div><div class="k">{key}</div></div>'
        for key, value in cards
    )
    rows = []
    for event in reversed(events):
        kind = event.get("kind", "?")
        latency = event.get("latency_s")
        latency_text = f"{latency:.1f}s" if isinstance(latency, (int, float)) else "-"
        images = event.get("images")
        head = event.get("reply_head")
        rows.append(
            "<tr class='kind-{kind}'><td>{label}</td><td>{clock}</td><td>{latency}</td>"
            "<td>{action}</td><td>{detail}</td><td>{quest}</td><td>{step}</td>"
            "<td>{images}</td><td>{head}</td></tr>".format(
                kind=kind,
                label=_KIND_LABELS.get(kind, kind),
                clock=_esc(event.get("wall_clock")),
                latency=latency_text,
                action=_esc(event.get("action")),
                detail=_esc(event.get("detail")),
                quest=_esc(event.get("quest")),
                step=_esc(event.get("quest_step")),
                images=images if images is not None else "-",
                head=f"<code>{_esc(head)}</code>" if head else "-",
            )
        )
    return _PAGE.format(
        cards=cards_html,
        rows="".join(rows) or '<tr><td colspan="9" class="muted">暂无事件</td></tr>',
        raw=_esc(json.dumps(stats, ensure_ascii=False, indent=2)),
    )


class DecisionDashboard:
    """Background HTTP server over a DecisionJournal; stop() shuts it down."""

    def __init__(self, journal: DecisionJournal, port: int) -> None:
        if not 0 < port <= 65535:
            raise ValueError("dashboard port must be within [1, 65535]")
        journal_ref = journal
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib naming
                if self.headers.get("Host", "").casefold() not in allowed_hosts:
                    self.send_response(421)
                    body = b"misdirected request"
                    self._finish_headers(body)
                    self.wfile.write(body)
                    return
                if self.path == "/api/events":
                    body = json.dumps(journal_ref.snapshot(), ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                elif self.path == "/":
                    body = render_page(journal_ref.snapshot()).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                else:
                    self.send_response(404)
                    body = b"not found"
                self._finish_headers(body)
                self.wfile.write(body)

            def _finish_headers(self, body: bytes) -> None:
                self.send_header("Cache-Control", "no-store")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; style-src 'unsafe-inline'",
                )
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass  # keep the run console clean

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="uga-dashboard", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
