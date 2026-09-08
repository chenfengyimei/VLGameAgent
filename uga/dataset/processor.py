from __future__ import annotations

import bisect
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from uga.core.errors import ContractViolation
from uga.recording.replay import ReplayEngine


class DatasetSplit(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"


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


@dataclass(frozen=True, slots=True)
class ProcessedEpisode:
    episode_id: str
    game_id: str
    duration_ns: int
    samples: tuple[AlignedSample, ...]


class DatasetProcessor:
    """Aligns by monotonic timestamp/reference, never by array position."""

    def __init__(self, *, max_alignment_delay_ns: int = 1_000_000_000) -> None:
        if max_alignment_delay_ns < 0:
            raise ContractViolation("alignment delay cannot be negative")
        self._max_alignment_delay_ns = max_alignment_delay_ns

    def process(self, episode_path: str | Path) -> ProcessedEpisode:
        replay = ReplayEngine(episode_path)
        observations = sorted(replay.observations, key=lambda row: int(row["timestamp_ns"]))
        observation_by_id = {str(row["observation_id"]): row for row in observations}
        times = [int(str(row["timestamp_ns"])) for row in observations]
        samples: list[AlignedSample] = []
        canonical_actions = [
            action for action in replay.actions if action.get("action_layer") == "canonical"
        ]
        selected_actions = canonical_actions or replay.actions
        for action in selected_actions:
            observation = self._resolve_observation(action, observations, observation_by_id, times)
            action_time = int(action["timestamp_ns"])
            observation_time = int(str(observation["timestamp_ns"]))
            delay = action_time - observation_time
            if delay < 0 or delay > self._max_alignment_delay_ns:
                raise ContractViolation(
                    f"action {action['action_id']} has invalid observation delay {delay}ns"
                )
            provenance = replay.provenance_for_action(str(action["action_id"]))
            if provenance is None:
                raise ContractViolation("processor encountered an action without provenance")
            samples.append(
                AlignedSample(
                    str(replay.metadata["episode_id"]),
                    str(replay.metadata["game_id"]),
                    str(observation["observation_id"]),
                    observation_time,
                    str(action["action_id"]),
                    action_time,
                    delay,
                    str(observation["payload_json"]),
                    str(action["payload_json"]),
                    str(provenance["action_source"]),
                    bool(provenance["human_override"]),
                )
            )
        start = int(replay.metadata["start_monotonic_ns"])
        end = int(replay.metadata["end_monotonic_ns"])
        return ProcessedEpisode(
            str(replay.metadata["episode_id"]),
            str(replay.metadata["game_id"]),
            end - start,
            tuple(samples),
        )

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
