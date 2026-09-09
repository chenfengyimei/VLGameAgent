from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from pathlib import Path

from uga.core.artifact_limits import (
    DEFAULT_ARTIFACT_LIMITS,
    ArtifactResourceLimits,
    ensure_file_size,
)
from uga.core.errors import ContractViolation
from uga.recording.replay import ReplayEngine


class QualityStatus(StrEnum):
    ACCEPTED = "accepted"
    REVIEW = "review"
    REJECTED = "rejected"


class FindingSeverity(IntEnum):
    INFO = 0
    WARNING = 1
    ERROR = 2


@dataclass(frozen=True, slots=True)
class QualityFinding:
    code: str
    severity: FindingSeverity
    detail: str


@dataclass(frozen=True, slots=True)
class ValidationReport:
    episode_id: str
    status: QualityStatus
    quality_score: float
    findings: tuple[QualityFinding, ...]
    frame_count: int
    action_count: int


class DatasetValidator:
    def __init__(
        self,
        *,
        max_capture_gap_ns: int = 500_000_000,
        max_input_gap_ns: int = 500_000_000,
        max_mouse_delta: int = 5000,
        limits: ArtifactResourceLimits = DEFAULT_ARTIFACT_LIMITS,
    ) -> None:
        if max_capture_gap_ns <= 0 or max_input_gap_ns <= 0 or max_mouse_delta <= 0:
            raise ContractViolation("dataset validation thresholds must be positive")
        self._max_capture_gap_ns = max_capture_gap_ns
        self._max_input_gap_ns = max_input_gap_ns
        self._max_mouse_delta = max_mouse_delta
        self._limits = limits

    def validate(self, episode_path: str | Path) -> ValidationReport:
        path = Path(episode_path)
        try:
            replay = ReplayEngine(path, limits=self._limits)
        except (FileNotFoundError, ContractViolation, OSError, ValueError) as exc:
            return ValidationReport(
                path.name,
                QualityStatus.REJECTED,
                0.0,
                (QualityFinding("episode_invalid", FindingSeverity.ERROR, str(exc)),),
                0,
                0,
            )
        findings: list[QualityFinding] = []
        frames = [row for row in replay.timeline if row["kind"] == "frame"]
        self._check_clock_regressions(replay.timeline, findings)
        self._check_capture_gaps(frames, findings)
        self._check_resolution(frames, findings)
        self._check_actions(replay.actions, findings)
        self._check_input_capture(replay.metadata, replay.actions, findings)
        self._check_events(replay.timeline, findings)
        self._check_video(path / "video.mp4", len(frames), findings)
        severity = max((finding.severity for finding in findings), default=FindingSeverity.INFO)
        status = (
            QualityStatus.REJECTED
            if severity >= FindingSeverity.ERROR
            else QualityStatus.REVIEW
            if severity >= FindingSeverity.WARNING
            else QualityStatus.ACCEPTED
        )
        penalty = sum(25 if item.severity == FindingSeverity.ERROR else 8 for item in findings)
        return ValidationReport(
            str(replay.metadata.get("episode_id", path.name)),
            status,
            max(0.0, 100.0 - penalty),
            tuple(findings),
            len(frames),
            len(replay.actions),
        )

    @staticmethod
    def _check_clock_regressions(
        timeline: list[dict[str, object]], findings: list[QualityFinding]
    ) -> None:
        """Check producer-local order because persisted rows are timestamp-sorted."""
        previous_by_kind: dict[str, int] = {}
        for row in sorted(timeline, key=lambda item: int(str(item["sequence"]))):
            kind = str(row["kind"])
            timestamp = int(str(row["timestamp_ns"]))
            previous = previous_by_kind.get(kind)
            if previous is not None and timestamp < previous:
                findings.append(
                    QualityFinding(
                        "clock_regression",
                        FindingSeverity.ERROR,
                        f"{kind} timestamp regressed from {previous}ns to {timestamp}ns",
                    )
                )
                return
            previous_by_kind[kind] = timestamp

    def _check_capture_gaps(
        self, frames: list[dict[str, object]], findings: list[QualityFinding]
    ) -> None:
        timestamps = [int(str(row["timestamp_ns"])) for row in frames]
        for previous, current in zip(timestamps, timestamps[1:], strict=False):
            gap = current - previous
            if gap > self._max_capture_gap_ns:
                findings.append(
                    QualityFinding(
                        "capture_gap",
                        FindingSeverity.WARNING,
                        f"capture gap {gap}ns exceeds {self._max_capture_gap_ns}ns",
                    )
                )

    @staticmethod
    def _check_resolution(frames: list[dict[str, object]], findings: list[QualityFinding]) -> None:
        sizes: set[tuple[int, int]] = set()
        for row in frames:
            payload = json.loads(str(row["payload_json"]))
            data = payload.get("data", {})
            if isinstance(data, dict) and "width" in data and "height" in data:
                sizes.add((int(data["width"]), int(data["height"])))
        if len(sizes) > 1:
            findings.append(
                QualityFinding(
                    "resolution_change",
                    FindingSeverity.WARNING,
                    f"episode contains {len(sizes)} frame sizes",
                )
            )

    def _check_actions(
        self, actions: list[dict[str, object]], findings: list[QualityFinding]
    ) -> None:
        for action in actions:
            if str(action.get("action_type")) != "RelativeMouseAction":
                continue
            payload = json.loads(str(action["payload_json"]))
            if (
                max(abs(int(payload.get("dx", 0))), abs(int(payload.get("dy", 0))))
                > self._max_mouse_delta
            ):
                findings.append(
                    QualityFinding(
                        "large_mouse_spike",
                        FindingSeverity.WARNING,
                        f"action {action.get('action_id')} exceeds mouse delta threshold",
                    )
                )

    def _check_input_capture(
        self,
        metadata: dict[str, object],
        actions: list[dict[str, object]],
        findings: list[QualityFinding],
    ) -> None:
        raw_inputs = [row for row in actions if row.get("category") == "raw_input"]
        ordered = sorted(raw_inputs, key=lambda row: int(str(row["timestamp_ns"])))
        for previous, current in zip(ordered, ordered[1:], strict=False):
            gap = int(str(current["timestamp_ns"])) - int(str(previous["timestamp_ns"]))
            if gap > self._max_input_gap_ns:
                findings.append(
                    QualityFinding(
                        "input_gap",
                        FindingSeverity.WARNING,
                        f"input gap {gap}ns exceeds {self._max_input_gap_ns}ns",
                    )
                )
                break

        if bool(metadata.get("human_controlled")) and not raw_inputs:
            findings.append(
                QualityFinding(
                    "missing_raw_input",
                    FindingSeverity.ERROR,
                    "human-controlled episode contains no raw input snapshots",
                )
            )
        if any(not row.get("input_state_json") for row in raw_inputs):
            findings.append(
                QualityFinding(
                    "missing_input_state",
                    FindingSeverity.ERROR,
                    "raw input event is missing its state snapshot",
                )
            )

    @staticmethod
    def _check_events(timeline: list[dict[str, object]], findings: list[QualityFinding]) -> None:
        event_text = " ".join(
            str(row.get("payload_json", "")).casefold()
            for row in timeline
            if row.get("kind") == "event"
        )
        checks = {
            "frame_dropped": ("dropped_frames", FindingSeverity.WARNING),
            "window_lost": ("window_lost", FindingSeverity.ERROR),
            "focus_lost": ("focus_lost", FindingSeverity.WARNING),
            "unexpected_alt_tab": ("unexpected_alt_tab", FindingSeverity.WARNING),
        }
        for token, (code, severity) in checks.items():
            if token in event_text:
                findings.append(QualityFinding(code, severity, f"timeline contains {token}"))

    def _check_video(
        self,
        path: Path,
        expected_frames: int,
        findings: list[QualityFinding],
    ) -> None:
        if not path.is_file():
            findings.append(
                QualityFinding("missing_video", FindingSeverity.ERROR, "video.mp4 is missing")
            )
            return
        try:
            ensure_file_size(path, self._limits.max_video_bytes, "Episode video")
            if expected_frames > self._limits.max_video_frames:
                raise ContractViolation("video exceeds the expected-frame resource limit")
            av = importlib.import_module("av")
            with av.open(str(path)) as container:
                stream = container.streams.video[0]
                self._check_video_dimensions(
                    int(stream.codec_context.width),
                    int(stream.codec_context.height),
                )
                declared_frames = int(stream.frames or 0)
                if declared_frames > self._limits.max_video_frames:
                    raise ContractViolation("video exceeds the declared-frame resource limit")
                decoded = 0
                for frame in container.decode(video=0):
                    decoded += 1
                    if decoded > self._limits.max_video_frames:
                        raise ContractViolation("video exceeds the decoded-frame resource limit")
                    self._check_video_dimensions(int(frame.width), int(frame.height))
                    if decoded > expected_frames:
                        break
            if decoded != expected_frames:
                findings.append(
                    QualityFinding(
                        "video_frame_mismatch",
                        FindingSeverity.ERROR,
                        f"decoded {decoded} video frames for {expected_frames} timeline frames",
                    )
                )
        except ContractViolation as exc:
            findings.append(QualityFinding("video_resource_limit", FindingSeverity.ERROR, str(exc)))
        except Exception as exc:
            findings.append(QualityFinding("corrupted_video", FindingSeverity.ERROR, str(exc)))

    def _check_video_dimensions(self, width: int, height: int) -> None:
        if (
            width < 1
            or height < 1
            or width > self._limits.max_video_dimension
            or height > self._limits.max_video_dimension
            or width * height > self._limits.max_video_pixels
        ):
            raise ContractViolation("video dimensions exceed the resource limit")
