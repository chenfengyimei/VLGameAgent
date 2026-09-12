from __future__ import annotations

import unittest

from uga.policy.decision_journal import DecisionJournal, DecisionRecord


def _record() -> DecisionRecord:
    return DecisionRecord(
        timestamp=123.0,
        kind="decision",
        latency_s=0.25,
        action=None,
        detail="one step",
        quest=None,
        quest_step=None,
        images=1,
        reply_head='{"action":"wait"}',
    )


class DecisionJournalTests(unittest.TestCase):
    def test_attached_sink_receives_each_record(self) -> None:
        received: list[DecisionRecord] = []
        journal = DecisionJournal(sink=received.append)

        record = _record()
        journal.record(record)

        self.assertEqual(received, [record])
        self.assertEqual(journal.snapshot()["stats"]["total"], 1)

    def test_sink_can_be_attached_after_construction(self) -> None:
        received: list[DecisionRecord] = []
        journal = DecisionJournal()
        journal.set_sink(received.append)

        journal.record(_record())

        self.assertEqual(len(received), 1)

    def test_durable_sink_failure_is_not_silenced(self) -> None:
        def fail(record: DecisionRecord) -> None:
            del record
            raise RuntimeError("durable sink failed")

        journal = DecisionJournal(sink=fail)

        with self.assertRaisesRegex(RuntimeError, "durable sink failed"):
            journal.record(_record())


if __name__ == "__main__":
    unittest.main()
