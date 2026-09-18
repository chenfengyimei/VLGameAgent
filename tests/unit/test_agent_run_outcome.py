from __future__ import annotations

import unittest

from apps.agent.run import _episode_outcome, _process_exit_code
from uga.agent.closed_loop import TerminalStatus
from uga.recording.schema import EpisodeResult


class AgentRunOutcomeTests(unittest.TestCase):
    def test_incomplete_vlm_run_is_restartable_not_fatal_provider_exit(self) -> None:
        self.assertEqual(
            _process_exit_code(
                user_stopped=False,
                policy="vlm",
                terminal_status=TerminalStatus.RUNNING,
            ),
            1,
        )

    def test_confirmed_or_user_stopped_run_exits_cleanly(self) -> None:
        self.assertEqual(
            _process_exit_code(
                user_stopped=False,
                policy="vlm",
                terminal_status=TerminalStatus.SUCCEEDED,
            ),
            0,
        )
        self.assertEqual(
            _process_exit_code(
                user_stopped=True,
                policy="vlm",
                terminal_status=TerminalStatus.RUNNING,
            ),
            0,
        )

    def test_timeout_is_failure_not_implicit_success(self) -> None:
        result, reason = _episode_outcome(
            user_stopped=False,
            run_error=None,
            terminal_status=TerminalStatus.RUNNING,
            terminal_reason=None,
            duration_expired=True,
        )

        self.assertEqual(result, EpisodeResult.FAILURE)
        self.assertEqual(reason, "timeout")

    def test_only_confirmed_goal_is_success(self) -> None:
        result, reason = _episode_outcome(
            user_stopped=False,
            run_error=None,
            terminal_status=TerminalStatus.SUCCEEDED,
            terminal_reason="goal_confirmed",
            duration_expired=False,
        )

        self.assertEqual(result, EpisodeResult.SUCCESS)
        self.assertEqual(reason, "goal_confirmed")

    def test_user_stop_takes_precedence_over_runtime_status(self) -> None:
        result, reason = _episode_outcome(
            user_stopped=True,
            run_error=None,
            terminal_status=TerminalStatus.SUCCEEDED,
            terminal_reason="goal_confirmed",
            duration_expired=False,
        )

        self.assertEqual(result, EpisodeResult.ABORTED)
        self.assertEqual(reason, "user_stopped")


if __name__ == "__main__":
    unittest.main()
