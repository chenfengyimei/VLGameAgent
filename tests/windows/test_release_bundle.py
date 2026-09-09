from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import winreg
from pathlib import Path

from uga.release.development import build_development_manifest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LAUNCHER = _PROJECT_ROOT / "scripts" / "run_bundle.ps1"


def _write_seeded_file(path: Path, size: int, seed: int) -> None:
    material = hashlib.sha256(bytes([seed])).digest()
    payload = (material * (size // len(material) + 1))[:size]
    path.write_bytes(payload)


@unittest.skipUnless(os.name == "nt", "Windows-only contract")
class ReleaseBundleLauncherTests(unittest.TestCase):
    """Launcher integrity, rollback, and removal behavior for release bundles.

    Every case runs the real launcher script against a synthetic bundle whose
    manifest was built by the production manifest builder; the configured
    Python is a stub that records agent launches and delegates manifest
    verification to the real interpreter, so a refusal can never be mistaken
    for a pass and a launch can only happen after every check succeeds.
    """

    _temp: Path
    _stub: Path
    _good: Path
    _good_anchor: str
    _good_dll_sha256: str

    @classmethod
    def setUpClass(cls) -> None:
        cls._temp = Path(tempfile.mkdtemp(prefix="uga-release-bundle-"))
        cls._stub = cls._temp / "python-stub.cmd"
        cls._stub.write_text(
            "@echo off\r\n"
            'if "%~2"=="apps.agent" (\r\n'
            "  if defined UGA_LAUNCH_MARKER (\r\n"
            '    > "%UGA_LAUNCH_MARKER%.env" echo DLL=%UGA_NATIVE_CAPTURE_DLL%\r\n'
            '    >> "%UGA_LAUNCH_MARKER%.env" echo SHA=%UGA_NATIVE_CAPTURE_SHA256%\r\n'
            '    type nul > "%UGA_LAUNCH_MARKER%"\r\n'
            "    exit /b 0\r\n"
            "  )\r\n"
            ")\r\n"
            f'"{sys.executable}" %*\r\n'
            "exit /b %ERRORLEVEL%\r\n",
            encoding="ascii",
        )
        cls._good = cls._build_bundle("good")
        cls._good_anchor = hashlib.sha256(
            (cls._good / "release-manifest.json").read_bytes()
        ).hexdigest()
        cls._good_dll_sha256 = hashlib.sha256(
            (cls._good / "native" / "uga_capture.dll").read_bytes()
        ).hexdigest()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._temp, ignore_errors=True)

    @classmethod
    def _build_bundle(cls, name: str) -> Path:
        root = cls._temp / name
        (root / "native").mkdir(parents=True)
        _write_seeded_file(root / "uga_test_bundle-0.1.0-py3-none-any.whl", 4096, 1)
        _write_seeded_file(root / "uga_test_bundle-0.1.0.tar.gz", 8192, 2)
        _write_seeded_file(root / "native" / "uga_capture.dll", 2048, 3)
        (root / "third-party-inventory.json").write_text(
            '{"schema": "uga.third-party-inventory"}\n', encoding="utf-8"
        )
        shutil.copyfile(_LAUNCHER, root / "run_uga.ps1")
        manifest = build_development_manifest(root, source_revision="workspace-unversioned")
        manifest.write(root / "release-manifest.json")
        return root

    def _copy_bundle(self, name: str) -> Path:
        bundle = shutil.copytree(self._good, self._temp / name)
        self.addCleanup(shutil.rmtree, bundle, ignore_errors=True)
        return bundle

    def _launch(
        self, bundle: Path, *, anchor: str | None = None
    ) -> tuple[int, str, str, bool]:
        marker = self._temp / f"marker-{bundle.name}-{abs(hash(anchor))}.launched"
        for stale in (marker, Path(f"{marker}.env")):
            stale.unlink(missing_ok=True)
        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(bundle / "run_uga.ps1"),
            "-Python",
            str(self._stub),
        ]
        if anchor is not None:
            command += ["-ManifestSha256", anchor]
        environment = os.environ.copy()
        environment["UGA_LAUNCH_MARKER"] = str(marker)
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=120,
        )
        return (
            completed.returncode,
            completed.stdout,
            completed.stderr,
            marker.is_file(),
        )

    def test_clean_bundle_launches_agent_with_pinned_native_library(self) -> None:
        code, _, _, launched = self._launch(self._good, anchor=self._good_anchor)
        self.assertEqual(code, 0)
        self.assertTrue(launched)
        env_text = (
            self._temp / f"marker-good-{abs(hash(self._good_anchor))}.launched.env"
        ).read_text(encoding="utf-8", errors="replace")
        self.assertIn(f"DLL={self._good / 'native' / 'uga_capture.dll'}", env_text)
        self.assertIn(f"SHA={self._good_dll_sha256}", env_text)

    def test_bundle_without_anchor_launches_in_degraded_mode(self) -> None:
        # Write-Warning does not surface on the captured streams, so the
        # contract checked here is that the missing anchor is tolerated: the
        # launcher still verifies the bundle and starts the agent.
        code, _, _, launched = self._launch(self._good)
        self.assertEqual(code, 0)
        self.assertTrue(launched)

    def test_missing_native_library_refuses_to_launch(self) -> None:
        bundle = self._copy_bundle("missing-dll")
        (bundle / "native" / "uga_capture.dll").unlink()
        code, _, _, launched = self._launch(bundle, anchor=self._good_anchor)
        self.assertNotEqual(code, 0)
        self.assertFalse(launched)

    def test_missing_manifest_refuses_to_launch(self) -> None:
        bundle = self._copy_bundle("missing-manifest")
        (bundle / "release-manifest.json").unlink()
        code, _, _, launched = self._launch(bundle, anchor=self._good_anchor)
        self.assertNotEqual(code, 0)
        self.assertFalse(launched)

    def test_invalid_anchor_format_refuses_to_launch(self) -> None:
        code, _, _, launched = self._launch(self._good, anchor="not-a-digest")
        self.assertNotEqual(code, 0)
        self.assertFalse(launched)

    def test_anchor_mismatch_refuses_to_launch(self) -> None:
        bundle = self._copy_bundle("anchor-mismatch")
        manifest_path = bundle / "release-manifest.json"
        tampered = manifest_path.read_text(encoding="utf-8").replace("0.1.0-dev", "9.9.9-dev")
        manifest_path.write_text(tampered, encoding="utf-8")
        code, _, stderr, launched = self._launch(bundle, anchor=self._good_anchor)
        self.assertNotEqual(code, 0)
        self.assertFalse(launched)
        self.assertIn("out-of-band anchor", stderr)

    def test_corrupted_artifact_refuses_even_with_valid_anchor(self) -> None:
        bundle = self._copy_bundle("corrupted-wheel")
        _write_seeded_file(bundle / "uga_test_bundle-0.1.0-py3-none-any.whl", 4096, 9)
        code, _, _, launched = self._launch(bundle, anchor=self._good_anchor)
        self.assertNotEqual(code, 0)
        self.assertFalse(launched)

    def test_corrupted_native_library_refuses_to_launch(self) -> None:
        bundle = self._copy_bundle("corrupted-dll")
        _write_seeded_file(bundle / "native" / "uga_capture.dll", 4096, 7)
        code, _, _, launched = self._launch(bundle, anchor=self._good_anchor)
        self.assertNotEqual(code, 0)
        self.assertFalse(launched)

    def test_failed_candidate_leaves_previous_bundle_runnable(self) -> None:
        candidate = self._copy_bundle("rollback-candidate")
        _write_seeded_file(candidate / "native" / "uga_capture.dll", 4096, 11)
        candidate_code, _, _, candidate_launched = self._launch(
            candidate, anchor=self._good_anchor
        )
        self.assertNotEqual(candidate_code, 0)
        self.assertFalse(candidate_launched)
        good_code, _, _, good_launched = self._launch(self._good, anchor=self._good_anchor)
        self.assertEqual(good_code, 0)
        self.assertTrue(good_launched)

    def test_removal_is_clean_and_leaves_no_persistent_environment(self) -> None:
        bundle = self._copy_bundle("removal")
        code, _, _, launched = self._launch(bundle, anchor=self._good_anchor)
        self.assertEqual(code, 0)
        self.assertTrue(launched)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            for name in ("UGA_NATIVE_CAPTURE_DLL", "UGA_NATIVE_CAPTURE_SHA256"):
                with self.subTest(variable=name):
                    try:
                        winreg.QueryValueEx(key, name)
                    except FileNotFoundError:
                        continue
                    self.fail(f"launcher left a persistent environment variable: {name}")
        shutil.rmtree(bundle)
        self.assertFalse(bundle.exists())


if __name__ == "__main__":
    unittest.main()
