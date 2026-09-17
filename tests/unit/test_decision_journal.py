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


class JournalDiskTests(unittest.TestCase):
    def test_disk_rotation_is_bounded(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decisions.jsonl"
            journal = DecisionJournal(path, max_file_bytes=1024)
            for _ in range(40):
                journal.record(_record())
            journal.close()
            self.assertIsNone(journal.snapshot()["diagnostic_file_error"])
            self.assertEqual(len(list(Path(directory).iterdir())), 2)
            self.assertTrue(all(p.stat().st_size <= 1024 for p in Path(directory).iterdir()))

    def test_slow_optional_disk_never_blocks_durable_sink_or_snapshot(self) -> None:
        import threading
        from pathlib import Path
        from unittest.mock import patch

        release, entered = threading.Event(), threading.Event()
        received = []
        journal = DecisionJournal(Path("unused.jsonl"), sink=received.append)

        def stalled(encoded: bytes) -> None:
            entered.set()
            release.wait(2)

        try:
            with patch.object(journal, "_append_disk", side_effect=stalled):
                journal.record(_record())
                self.assertTrue(entered.wait(1))
                journal.record(_record())
                self.assertEqual(len(received), 2)
                self.assertEqual(journal.snapshot()["stats"]["total"], 2)
                journal.close(0.01)
                self.assertIsNotNone(journal.snapshot()["diagnostic_file_error"])
        finally:
            release.set()
