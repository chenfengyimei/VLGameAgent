from __future__ import annotations

import unittest

from uga.windows.window_identity import WindowIdentityTracker, executable_path_hash


class WindowIdentityTests(unittest.TestCase):
    def test_path_hash_is_case_and_separator_stable(self) -> None:
        self.assertEqual(
            executable_path_hash("C:/Games/Demo.EXE"),
            executable_path_hash(r"c:\games\demo.exe"),
        )

    def test_generation_changes_when_hwnd_is_recycled(self) -> None:
        tracker = WindowIdentityTracker()
        first = tracker.identify(
            hwnd=50, pid=10, executable_path=r"C:\game.exe", process_start_time_100ns=1
        )
        same = tracker.identify(
            hwnd=50, pid=10, executable_path=r"c:\GAME.exe", process_start_time_100ns=1
        )
        recycled = tracker.identify(
            hwnd=50, pid=11, executable_path=r"C:\game.exe", process_start_time_100ns=2
        )
        self.assertEqual(first, same)
        self.assertEqual(recycled.window_generation, first.window_generation + 1)


if __name__ == "__main__":
    unittest.main()
