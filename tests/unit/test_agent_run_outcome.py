from __future__ import annotations

import unittest

from apps.agent.run import _episode_outcome
from uga.agent.closed_loop import TerminalStatus
from uga.recording.schema import EpisodeResult


class AgentRunOutcomeTests(unittest.TestCase):
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
