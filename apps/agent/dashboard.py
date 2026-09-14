"""Lightweight run dashboard: an HTTP server exposing the live decision
journal to a browser page (standard library only, no new dependencies).

GET /            -> self-contained Chinese dashboard page (auto-refresh)
GET /api/events  -> journal snapshot as JSON
"""

from __future__ import annotations

import html
import json
import threading
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from uga.policy.decision_journal import DecisionJournal

DashboardStatusProvider = Callable[[], Mapping[str, object]]
DashboardPreviewProvider = Callable[[], tuple[bytes, str] | None]


_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>VLGameAgent 运行面板</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="2">
<style>
 :root {{ color-scheme:dark; --bg:#080d16; --panel:#111a29; --line:#24324a;
          --text:#e7edf7; --muted:#91a0b8; --blue:#62b8ff; --green:#66d89b;
          --amber:#f1c86b; --red:#ff7e87; }}
 * {{ box-sizing:border-box; }}
 body {{ font-family:"Microsoft YaHei",system-ui,sans-serif; margin:0;
         background:radial-gradient(circle at top right,#13223b 0,var(--bg) 42%);
         color:var(--text); }}
 main {{ width:min(1480px,calc(100% - 28px)); margin:0 auto; padding:22px 0 40px; }}
 header {{ display:flex; justify-content:space-between; gap:18px; align-items:center;
           margin-bottom:16px; }}
 h1 {{ font-size:22px; margin:0; }} h2 {{ font-size:16px; margin:0 0 10px; }}
 .muted {{ color:var(--muted); }}
 .status {{ border:1px solid var(--line); border-radius:999px; padding:7px 12px;
            font-size:13px; font-weight:700; white-space:nowrap; }}
 .status-running {{ color:var(--blue); }} .status-succeeded {{ color:var(--green); }}
 .status-blocked,.status-failed {{ color:var(--red); }}
 .layout {{ display:grid; grid-template-columns:minmax(320px,1.15fr) minmax(320px,.85fr);
            gap:14px; align-items:start; }}
 .panel {{ background:color-mix(in srgb,var(--panel) 94%,transparent); border:1px solid var(--line);
           border-radius:14px; padding:14px; box-shadow:0 14px 45px #0005; }}
 .preview {{ aspect-ratio:16/9; display:grid; place-items:center; overflow:hidden;
             background:#050810; border-radius:10px; border:1px solid #1b2940; }}
 .preview img {{ width:100%; height:100%; object-fit:contain; }}
 .goal {{ font-size:15px; line-height:1.65; white-space:pre-wrap; word-break:break-word; }}
 .runtime-meta {{ display:grid; grid-template-columns:max-content 1fr; gap:7px 12px;
                  margin:12px 0 0; padding-top:12px; border-top:1px solid var(--line);
                  font-size:12px; }}
 .runtime-meta dt {{ color:var(--muted); }} .runtime-meta dd {{ margin:0; word-break:break-all; }}
 .cards {{ display:grid; grid-template-columns:repeat(4,minmax(125px,1fr)); gap:10px;
           margin:14px 0; }}
 .card {{ background:#121e31; border:1px solid #223451; border-radius:10px; padding:10px 12px;
          min-width:0; }}
 .card .v {{ font-size:19px; font-weight:700; color:var(--blue); overflow:hidden;
             text-overflow:ellipsis; white-space:nowrap; }}
 .card .k {{ font-size:11px; color:var(--muted); margin-top:3px; }}
 .table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:10px; }}
 table {{ border-collapse:collapse; width:100%; min-width:920px; font-size:12px; }}
 th,td {{ border-bottom:1px solid var(--line); padding:7px 8px; text-align:left;
          vertical-align:top; }}
 th {{ position:sticky; top:0; background:#162238; color:#aebbd0; }}
 tr:last-child td {{ border-bottom:0; }}
 tr.kind-decision td:first-child {{ color:#7cc4ff; }}
 tr.kind-queued td:first-child {{ color:#9fe6a0; }}
 tr.kind-static_hold td:first-child {{ color:#d8c97c; }}
 tr.kind-stale_discard td:first-child {{ color:#ff9d7c; }}
 tr.kind-failure td:first-child, tr.kind-rate_limited td:first-child {{ color:#ff7c7c; }}
 code {{ background:#17243a; padding:1px 5px; border-radius:4px; }}
 details {{ margin-top:14px; }} pre {{ font-size:11px; color:#aab7cb; white-space:pre-wrap;
                                     overflow-wrap:anywhere; }}
 @media (max-width:900px) {{ .layout {{ grid-template-columns:1fr; }}
                             .cards {{ grid-template-columns:repeat(2,1fr); }} }}
</style></head><body><main>
<header><div><h1>VLGameAgent 闭环运行面板</h1><div class="muted">每 2 秒刷新 · 本机只读</div></div>
<div class="status status-{status_class}">{status_label}</div></header>
<div class="layout"><section class="panel"><h2>实时画面</h2>{preview}</section>
<section class="panel"><h2>当前目标</h2><div class="goal">{goal}</div>
<div class="cards">{primary_cards}</div>{runtime_meta}</section></div>
<div class="cards">{health_cards}</div>
<section class="panel"><h2>决策时间线 <span class="muted">（最新在前，最多 120 条）</span></h2>
<div class="table-wrap"><table><tr><th>类型</th><th>时间</th><th>耗时</th>
<th>动作</th><th>监督说明</th><th>步骤</th><th>图像</th><th>模型回复摘要</th></tr>
{rows}</table></div></section>
<details class="panel"><summary>诊断原始数据</summary><pre>{raw}</pre></details>
</main></body></html>"""

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
    if value is None:
        return '<span class="muted">-</span>'
    return html.escape(str(value))


def _clip(value: object, limit: int = 180) -> object:
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _format_number(value: object, suffix: str = "") -> str:
    if isinstance(value, float):
        return f"{value:.1f}{suffix}"
    if isinstance(value, int):
        return f"{value}{suffix}"
    return "—"


def _card(label: str, value: object) -> str:
    return (
        f'<div class="card"><div class="v">{_esc(value)}</div>'
        f'<div class="k">{html.escape(label)}</div></div>'
    )


def _termination_label(value: object) -> str:
    labels = {
        "goal_confirmed": "证据已确认",
        "timeout": "运行超时",
        "runtime_error": "运行异常",
        "user_stop": "用户停止",
        "recovery_exhausted": "恢复次数耗尽",
    }
    return labels.get(str(value), "—" if value is None else str(value))


def render_page(snapshot: dict[str, Any], *, preview_available: bool = False) -> str:
    stats = snapshot.get("stats", {})
    events = snapshot.get("events", [])
    runtime = snapshot.get("runtime", {})
    if not isinstance(stats, dict):
        stats = {}
    if not isinstance(events, list):
        events = []
    if not isinstance(runtime, dict):
        runtime = {}
    status = str(runtime.get("status", "starting")).casefold()
    status_class = status if status in {"running", "succeeded", "blocked", "failed"} else "running"
    status_label = {
        "running": "运行中",
        "succeeded": "目标已确认",
        "blocked": "已安全阻断",
        "failed": "运行失败",
    }.get(status, "正在启动")
    primary_cards = "".join(
        (
            _card("最新决策", runtime.get("current_action", "等待首个决策")),
            _card("目标置信度", _format_number(runtime.get("goal_confidence"))),
            _card("证据置信度", _format_number(runtime.get("goal_evidence_confidence"))),
            _card("终止原因", _termination_label(runtime.get("termination_reason"))),
        )
    )
    runtime_meta = "<dl class=\"runtime-meta\">" + "".join(
        (
            f"<dt>模型</dt><dd>{_esc(runtime.get('model'))}</dd>",
            f"<dt>视觉策略</dt><dd>{_esc(runtime.get('vision_mode'))}</dd>",
            f"<dt>OCR</dt><dd>{'已启用' if runtime.get('ocr_active') else '未启用'}</dd>",
            f"<dt>最新帧源</dt><dd>{_esc(runtime.get('frame_source'))}</dd>",
            "<dt>最新帧年龄</dt><dd>"
            f"{_esc(_format_number(runtime.get('frame_age_ms'), ' ms'))}</dd>",
        )
    ) + "</dl>"
    health_cards = "".join(
        (
            _card("已采集帧", runtime.get("capture_frames", 0)),
            _card("采集间隔 P95", _format_number(runtime.get("capture_gap_p95_ms"), " ms")),
            _card("采集间隔最大", _format_number(runtime.get("capture_gap_max_ms"), " ms")),
            _card("过期推理丢弃", runtime.get("stale_results_discarded", 0)),
            _card("逻辑动作", runtime.get("logical_actions_issued", 0)),
            _card("物理事件", runtime.get("executed_actions", 0)),
            _card("恢复次数", runtime.get("recovery_count", 0)),
            _card("推理 P50 / 最大", (
                f"{float(stats.get('latency_p50_s', 0)):.1f} / "
                f"{float(stats.get('latency_max_s', 0)):.1f} s"
            )),
        )
    )
    preview = (
        '<div class="preview"><img src="/api/frame" alt="MuMu 实时画面"></div>'
        if preview_available
        else '<div class="preview muted">等待首帧…</div>'
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
            "<td>{action}</td><td>{detail}</td><td>{step}</td>"
            "<td>{images}</td><td>{head}</td></tr>".format(
                kind=kind,
                label=_KIND_LABELS.get(kind, kind),
                clock=_esc(event.get("wall_clock")),
                latency=latency_text,
                action=_esc(event.get("action")),
                detail=_esc(_clip(event.get("detail"))),
                step=_esc(_clip(event.get("quest_step"))),
                images=images if images is not None else "-",
                head=f"<code>{_esc(_clip(head))}</code>" if head else "-",
            )
        )
    return _PAGE.format(
        status_class=status_class,
        status_label=status_label,
        preview=preview,
        goal=_esc(runtime.get("goal", "等待运行时连接")),
        primary_cards=primary_cards,
        runtime_meta=runtime_meta,
        health_cards=health_cards,
        rows="".join(rows) or '<tr><td colspan="8" class="muted">暂无事件</td></tr>',
        raw=_esc(json.dumps(snapshot, ensure_ascii=False, indent=2)),
    )


class DecisionDashboard:
    """Background HTTP server over a DecisionJournal; stop() shuts it down."""

    def __init__(
        self,
        journal: DecisionJournal,
        port: int,
        *,
        status_provider: DashboardStatusProvider | None = None,
        preview_provider: DashboardPreviewProvider | None = None,
    ) -> None:
        if not 0 < port <= 65535:
            raise ValueError("dashboard port must be within [1, 65535]")
        journal_ref = journal
        status_provider_ref = status_provider
        preview_provider_ref = preview_provider
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

        class Handler(BaseHTTPRequestHandler):
            def _snapshot(self) -> dict[str, Any]:
                snapshot = journal_ref.snapshot()
                if status_provider_ref is not None:
                    try:
                        snapshot["runtime"] = dict(status_provider_ref())
                    except Exception as exc:
                        snapshot["runtime"] = {
                            "status": "failed",
                            "recent_failure": f"dashboard status unavailable: {exc}",
                        }
                return snapshot

            def do_GET(self) -> None:  # noqa: N802 - stdlib naming
                if self.headers.get("Host", "").casefold() not in allowed_hosts:
                    self.send_response(421)
                    body = b"misdirected request"
                    self._finish_headers(body)
                    self.wfile.write(body)
                    return
                path = urlsplit(self.path).path
                if path == "/api/events":
                    body = json.dumps(self._snapshot(), ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                elif path == "/api/frame":
                    try:
                        preview = (
                            None if preview_provider_ref is None else preview_provider_ref()
                        )
                    except Exception:
                        preview = None
                    if preview is None:
                        self.send_response(503)
                        body = b"frame unavailable"
                        self.send_header("Content-Type", "text/plain; charset=utf-8")
                    else:
                        body, content_type = preview
                        self.send_response(200)
                        self.send_header("Content-Type", content_type)
                elif path == "/":
                    body = render_page(
                        self._snapshot(), preview_available=preview_provider_ref is not None
                    ).encode("utf-8")
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
                    "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'",
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
        if self._thread.is_alive():
            self._server.shutdown()
            self._thread.join(timeout=2.0)
        self._server.server_close()
