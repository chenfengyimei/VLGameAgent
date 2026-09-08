from __future__ import annotations

import html
import json
import os
from pathlib import Path

from uga.recording.replay import ReplayEngine
from uga.ui import load_ui_script


def write_replay_debugger(episode_path: str | Path, output: str | Path) -> Path:
    replay = ReplayEngine(episode_path)
    destination = Path(output).resolve()
    video_path = replay.path / "video.mp4"
    video_reference = os.path.relpath(video_path, destination.parent).replace("\\", "/")
    events = [
        {
            "sequence": row["sequence"],
            "timestamp_ns": row["timestamp_ns"],
            "elapsed_ns": row["elapsed_ns"],
            "kind": row["kind"],
            "reference_id": row["reference_id"],
            "payload": json.loads(str(row["payload_json"])),
        }
        for row in replay.timeline
    ]
    encoded = json.dumps(events, ensure_ascii=False).replace("<", "\\u003c")
    maximum = max((int(event["elapsed_ns"]) for event in events), default=0)
    document = f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><title>UGA Replay Debugger</title>
<style>
body{{font-family:system-ui;margin:1.5rem;background:#10131a;color:#e8eef8}}
main{{display:grid;grid-template-columns:minmax(320px,2fr) minmax(320px,1fr);gap:1rem}}
video,pre{{width:100%;background:#000}} pre{{white-space:pre-wrap;max-height:70vh;overflow:auto}}
input{{width:100%}}
</style>
<h1>UGA Replay: {html.escape(str(replay.metadata.get("episode_id")))}</h1>
<input id="time" type="range" min="0" max="{maximum}" value="0" step="1000000">
<output id="clock">0.000 s</output>
<main><video id="video" controls src="{video_reference}"></video><pre id="details"></pre></main>
<script id="uga-replay-data" type="application/json">{encoded}</script>
<script>{load_ui_script("replay.js")}</script></html>"""
    destination.write_text(document, encoding="utf-8")
    return destination
