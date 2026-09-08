from __future__ import annotations

import html
from pathlib import Path

from uga.dataset.processor import AlignedSample, ProcessedEpisode
from uga.ui import load_ui_script


def _render_sample(sample: AlignedSample) -> str:
    search = " ".join((sample.observation_id, sample.action_id, sample.action_source)).casefold()
    override = "yes" if sample.human_override else "no"
    return (
        f'<tr data-search="{html.escape(search)}" data-override="{override}">'
        f"<td>{html.escape(sample.observation_id)}</td>"
        f"<td>{html.escape(sample.action_id)}</td>"
        f"<td>{sample.action_delay_ns}</td>"
        f"<td>{html.escape(sample.action_source)}</td>"
        f"<td>{override}</td>"
        "</tr>"
    )


def write_episode_viewer(episode: ProcessedEpisode, output: str | Path) -> Path:
    rows = "\n".join(_render_sample(sample) for sample in episode.samples)
    document = f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><title>UGA Episode {html.escape(episode.episode_id)}</title>
<style>
body{{font-family:system-ui;margin:2rem}}
table{{border-collapse:collapse}}
td,th{{border:1px solid #aaa;padding:.4rem}}
</style>
<h1>{html.escape(episode.episode_id)}</h1><p>Game: {html.escape(episode.game_id)}</p>
<label>Filter <input id="sample-query" type="search"></label>
<label>Override <select id="override-filter"><option value="all">all</option>
<option value="yes">yes</option><option value="no">no</option></select></label>
<table><thead><tr>
<th>Observation</th><th>Action</th><th>Delay ns</th>
<th>Source</th><th>Override</th>
</tr></thead>
<tbody>{rows}</tbody></table><script>{load_ui_script("dataset-viewer.js")}</script></html>"""
    path = Path(output)
    path.write_text(document, encoding="utf-8")
    return path
