"""Bounded live publication after input and frame production have stopped."""

from __future__ import annotations

import asyncio
from pathlib import Path

from uga.core.deadline import BoundedWorker, Deadline, DeadlineExceeded
from uga.recording.episode_writer import EpisodeWriter
from uga.recording.frame_queue import RecordingFailure
from uga.recording.schema import EpisodeResult
from uga.time.clock import UGATime


async def finalize_episode(
    writer: EpisodeWriter,
    result: EpisodeResult,
    end: UGATime,
    *,
    timeout_s: float = 15.0,
) -> Path:
    try:
        return await BoundedWorker().run(Deadline.after(timeout_s), writer.finalize, result, end)
    except (DeadlineExceeded, asyncio.CancelledError) as exc:
        # Never delete an already-published artifact to 'undo' a rename. If
        # publication hasn't started, late codec/disk work must keep staging.
        writer.forbid_publication()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise RecordingFailure("Episode finalization timed out; inspect retained staging") from exc
