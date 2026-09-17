from __future__ import annotations

import bisect
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from uga.core.artifact_limits import DEFAULT_ARTIFACT_LIMITS, ArtifactResourceLimits
from uga.core.errors import ContractViolation
from uga.recording.replay import ReplayEngine


class DatasetSplit(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"


class EpisodeQualification(StrEnum):
    QUALIFIED = "qualified"
    LEGACY = "legacy"
    UNQUALIFIED = "unqualified"


@dataclass(frozen=True, slots=True)
class AlignedSample:
    episode_id: str
    game_id: str
    observation_id: str
    observation_timestamp_ns: int
    action_id: str
    action_timestamp_ns: int
    action_delay_ns: int
    observation_json: str
    action_json: str
    action_source: str
    human_override: bool
    inference_observation_id: str
    inference_observation_timestamp_ns: int
    inference_observation_json: str
    execution_status: str
    action_layer: str = "physical"
    action_type: str = ""


@dataclass(frozen=True, slots=True)
class ProcessedEpisode:
    episode_id: str
    game_id: str
    duration_ns: int
    samples: tuple[AlignedSample, ...]
    qualification: EpisodeQualification = EpisodeQualification.LEGACY
    qualified_duration_ns: int = 0
    exclusion_counts: tuple[tuple[str, int], ...] = ()


class DatasetProcessor:
    """Aligns by monotonic timestamp/reference, never by array position."""

    def __init__(
        self,
        *,
        max_alignment_delay_ns: int = 1_000_000_000,
        limits: ArtifactResourceLimits = DEFAULT_ARTIFACT_LIMITS,
    ) -> None:
        if max_alignment_delay_ns < 0:
            raise ContractViolation("alignment delay cannot be negative")
        self._max_alignment_delay_ns = max_alignment_delay_ns
        self._limits = limits

    def process(self, episode_path: str | Path) -> ProcessedEpisode:
        replay = ReplayEngine(episode_path, limits=self._limits)
        observations = sorted(replay.observations, key=lambda row: int(row["timestamp_ns"]))
        observation_by_id = {str(row["observation_id"]): row for row in observations}
        times = [int(str(row["timestamp_ns"])) for row in observations]
        samples: list[AlignedSample] = []
        exclusions: dict[str, int] = {}
        # Keep mixed motor/GUI episodes. Physical children are evidence for a
        # logical label, never duplicate labels of their own.
        logical_ids = {
            str(action["action_id"]) for action in replay.actions
            if action.get("action_layer") == "canonical"
            or action.get("action_type") == "GuiAction"
        }
        children = {
            str(row["action_id"]) for row in replay.provenance
            if row.get("parent_action_id") in logical_ids
        }
        selected_actions = [
            action for action in replay.actions
            if str(action["action_id"]) in logical_ids
            or (action.get("action_layer") == "physical"
                and str(action["action_id"]) not in children)
        ]
        start = int(replay.metadata["start_monotonic_ns"])
        end = int(replay.metadata["end_monotonic_ns"])
        if not replay.has_execution_receipt_table:
            return ProcessedEpisode(
                str(replay.metadata["episode_id"]),
                str(replay.metadata["game_id"]),
                end - start,
                (),
                EpisodeQualification.LEGACY,
                0,
                (("legacy_missing_receipts", len(selected_actions)),),
            )
        qualified_intervals: list[tuple[int, int]] = []
        for action in selected_actions:
            action_id = str(action["action_id"])
            provenance = replay.provenance_for_action(action_id)
            if provenance is None:
                raise ContractViolation("processor encountered an action without provenance")
            if bool(provenance["human_override"]) or action.get("category") == "raw_input":
                self._exclude(exclusions, "human_override")
                continue
            receipts = self._receipts_for_training_action(replay, action, provenance)
            if not receipts:
                self._exclude(exclusions, "missing_receipt")
                continue
            if any(str(receipt["status"]) != "executed" for receipt in receipts):
                self._exclude(exclusions, "not_fully_executed")
                continue
            latest_receipt = max(receipts, key=lambda row: int(str(row["at_ns"])))
            execution_observation_id = latest_receipt.get("pre_action_observation_id")
            if execution_observation_id is None:
                self._exclude(exclusions, "missing_pre_action_observation")
                continue
            observation = observation_by_id[str(execution_observation_id)]
            # Train on information available BEFORE the first primitive,
            # never on the effect frame produced after that action.
            if any(
                r.get("pre_action_observation_id") != execution_observation_id for r in receipts
            ):
                self._exclude(exclusions, "inconsistent_pre_action_observation")
                continue
            action_time = min(int(str(r["at_ns"])) for r in receipts)
            capture_ns = latest_receipt.get("pre_action_capture_ns")
            if type(capture_ns) is not int:
                self._exclude(exclusions, "missing_pre_action_capture_time")
                continue
            if any(r.get("pre_action_capture_ns") != capture_ns for r in receipts):
                self._exclude(exclusions, "inconsistent_pre_action_capture_time")
                continue
            observation_time = capture_ns
            delay = action_time - observation_time
            if delay < 0 or delay > self._max_alignment_delay_ns:
                raise ContractViolation(
                    f"action {action['action_id']} has invalid observation delay {delay}ns"
                )
            inference_observation = self._resolve_observation(
                action, observations, observation_by_id, times
            )
            samples.append(
                AlignedSample(
                    str(replay.metadata["episode_id"]),
                    str(replay.metadata["game_id"]),
                    str(observation["observation_id"]),
                    observation_time,
                    action_id,
                    action_time,
                    delay,
                    str(observation["payload_json"]),
                    str(action["payload_json"]),
                    str(provenance["action_source"]),
                    bool(provenance["human_override"]),
                    str(inference_observation["observation_id"]),
                    int(str(inference_observation["timestamp_ns"])),
                    str(inference_observation["payload_json"]),
                    "executed",
                    str(action["action_layer"]),
                    str(action["action_type"]),
                )
            )
            # Measured active execution time, not action TTL or wall time.
            # Instantaneous point labels are valid but contribute zero duration.
            qualified_intervals.append(
                (max(start, action_time), min(end, int(str(latest_receipt["at_ns"]))))
            )
        qualified_duration = self._merged_duration(qualified_intervals)
        qualification = (
            EpisodeQualification.QUALIFIED
            if samples
            else EpisodeQualification.UNQUALIFIED
        )
        return ProcessedEpisode(
            str(replay.metadata["episode_id"]),
            str(replay.metadata["game_id"]),
            end - start,
            tuple(samples),
            qualification,
            qualified_duration,
            tuple(sorted(exclusions.items())),
        )

    @staticmethod
    def _receipts_for_training_action(
        replay: ReplayEngine,
        action: dict[str, object],
        provenance: dict[str, object],
    ) -> tuple[dict[str, object], ...]:
        action_id = str(action["action_id"])
        if action.get("action_layer") != "canonical" and action.get("action_type") != "GuiAction":
            proposal_id = provenance.get("proposal_id")
            if provenance.get("action_source") == "GUI_AGENT" and proposal_id is not None:
                children = {
                    str(row["action_id"]) for row in replay.provenance
                    if row.get("proposal_id") == proposal_id
                    and str(row["action_id"]) in {
                        str(item["action_id"]) for item in replay.actions
                        if item.get("action_layer") == "physical"
                    }
                }
                receipts = replay.receipts_for_proposal(str(proposal_id))
                if {str(row["action_id"]) for row in receipts} != children:
                    return ()
                return receipts
            return replay.receipts_for_action(action_id)
        child_ids = {
            str(row["action_id"])
            for row in replay.provenance
            if row.get("parent_action_id") == action_id
        }
        if not child_ids:
            return ()
        receipts = tuple(
            receipt
            for receipt in replay.execution_receipts
            if str(receipt.get("action_id")) in child_ids
        )
        if len(receipts) != len(child_ids):
            return ()
        proposal_id = provenance.get("proposal_id")
        if proposal_id is None or any(
            receipt.get("proposal_id") != proposal_id for receipt in receipts
        ):
            return ()
        return receipts

    @staticmethod
    def _exclude(counts: dict[str, int], reason: str) -> None:
        counts[reason] = counts.get(reason, 0) + 1

    @staticmethod
    def _merged_duration(intervals: list[tuple[int, int]]) -> int:
        if not intervals:
            return 0
        total = 0
        start, end = sorted(intervals)[0]
        for next_start, next_end in sorted(intervals)[1:]:
            if next_start <= end:
                end = max(end, next_end)
            else:
                total += max(0, end - start)
                start, end = next_start, next_end
        return total + max(0, end - start)

    @staticmethod
    def _resolve_observation(
        action: dict[str, object],
        observations: list[dict[str, object]],
        observation_by_id: dict[str, dict[str, object]],
        times: list[int],
    ) -> dict[str, object]:
        explicit = action.get("observation_id")
        if explicit is not None:
            return observation_by_id[str(explicit)]
        index = bisect.bisect_right(times, int(str(action["timestamp_ns"]))) - 1
        if index < 0:
            raise ContractViolation(f"action {action['action_id']} has no preceding observation")
        return observations[index]


class MultiGameDataset:
    def __init__(self, game_splits: dict[str, DatasetSplit]) -> None:
        if not game_splits:
            raise ContractViolation("multi-game dataset requires locked game splits")
        self._game_splits = dict(game_splits)
        self._episodes: dict[str, ProcessedEpisode] = {}

    def add(self, episode: ProcessedEpisode) -> DatasetSplit:
        if episode.episode_id in self._episodes:
            raise ContractViolation(f"duplicate episode: {episode.episode_id}")
        try:
            split = self._game_splits[episode.game_id]
        except KeyError as exc:
            raise ContractViolation(f"game split was not locked: {episode.game_id}") from exc
        self._episodes[episode.episode_id] = episode
        return split

    def samples(self, split: DatasetSplit) -> tuple[AlignedSample, ...]:
        return tuple(
            sample
            for episode in self._episodes.values()
            if self._game_splits[episode.game_id] == split
            for sample in episode.samples
        )

    def hours(self, split: DatasetSplit) -> float:
        total_ns = sum(
            episode.duration_ns
            for episode in self._episodes.values()
            if self._game_splits[episode.game_id] == split
        )
        return total_ns / 3_600_000_000_000


class LeakageSafeSplitRegistry:
    """Rejects episode, session, player, or game identity crossing splits."""

    def __init__(self) -> None:
        self._assignments: dict[str, dict[str, DatasetSplit]] = {
            "episode": {},
            "session": {},
            "player": {},
            "game": {},
        }

    def assign(
        self,
        *,
        episode_id: str,
        session_id: str,
        player_id: str,
        game_id: str,
        split: DatasetSplit,
    ) -> None:
        identities = {
            "episode": episode_id,
            "session": session_id,
            "player": player_id,
            "game": game_id,
        }
        if any(not value.strip() for value in identities.values()):
            raise ContractViolation("split identities cannot be blank")
        for kind, value in identities.items():
            previous = self._assignments[kind].get(value)
            if previous is not None and previous != split:
                raise ContractViolation(
                    f"dataset leakage: {kind} {value!r} crosses {previous} and {split}"
                )
        for kind, value in identities.items():
            self._assignments[kind][value] = split
