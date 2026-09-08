from __future__ import annotations

import os
import unittest

from uga.windows.backend import Win32WindowBackend


@unittest.skipUnless(os.name == "nt", "Windows-only contract")
class Win32WindowBackendTests(unittest.TestCase):
    def test_discovery_is_safe_on_an_empty_desktop(self) -> None:
        snapshots = Win32WindowBackend().discover()
        self.assertIsInstance(snapshots, tuple)


if __name__ == "__main__":
    unittest.main()
