"""Repository policy checks that keep CI and release inputs immutable.

These checks fail closed when a workflow or release script reintroduces a
mutable supply-chain input: floating action refs, persisted checkout
credentials, floating toolchain versions, unhashed PyPI installs, ad-hoc npm
resolution, unlocked Cargo operations, or isolated PEP 517 builds that would
download an unpinned build backend.
"""

from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
CI_PATH = WORKFLOWS_DIR / "ci.yml"
LOCK_PATH = REPO_ROOT / "requirements-lock.txt"
VISION_LOCK_PATH = REPO_ROOT / "requirements-vision-lock.txt"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
SETUP_SCRIPT_PATH = REPO_ROOT / "scripts" / "setup_windows.ps1"
BUILD_SCRIPT_PATH = REPO_ROOT / "scripts" / "build_release.ps1"
CARGO_LOCK_PATH = REPO_ROOT / "native" / "Cargo.lock"

COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
EXACT_PATCH_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
EXACT_REQUIREMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*==[0-9][0-9A-Za-z.+-]*$")
LOCK_REQUIREMENT_LINE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*==")
TOOLCHAIN_SETTING_BY_ACTION = {
    "actions/setup-python": "python-version",
    "actions/setup-node": "node-version",
    "dtolnay/rust-toolchain": "toolchain",
}
NPM_AD_HOC_COMMAND = re.compile(r"(?i)\bnpm (install|update|add|link)\b")
CARGO_LOCKED_OPERATION = re.compile(r"(?i)\bcargo (clippy|test|build)\b")


def _workflows() -> list[tuple[Path, dict[str, Any]]]:
    loaded: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(document, dict):
            loaded.append((path, document))
    return loaded


def _steps(document: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for job in document.get("jobs", {}).values():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps", []):
            if isinstance(step, dict):
                steps.append(step)
    return steps


def _run_strings(document: dict[str, Any]) -> list[str]:
    return [str(step["run"]) for step in _steps(document) if isinstance(step.get("run"), str)]


def _pip_install_arguments(text: str) -> list[str]:
    matches = re.finditer(r"(?im)pip install(?P<args>[^`\r\n;]+)", text)
    return [match.group("args").strip() for match in matches]


def _script_command_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


class WorkflowPolicyTests(unittest.TestCase):
    def test_workflows_use_least_privilege_permissions(self) -> None:
        for path, document in _workflows():
            with self.subTest(workflow=path.name):
                self.assertEqual(document.get("permissions"), {"contents": "read"})

    def test_action_refs_are_immutable_commit_shas(self) -> None:
        for path, document in _workflows():
            for step in _steps(document):
                uses = step.get("uses")
                if not isinstance(uses, str):
                    continue
                with self.subTest(workflow=path.name, uses=uses):
                    action, separator, ref = uses.partition("@")
                    self.assertTrue(action and separator, "uses must name an action")
                    self.assertIsNotNone(COMMIT_SHA.fullmatch(ref))

    def test_checkout_does_not_persist_credentials(self) -> None:
        for path, document in _workflows():
            for step in _steps(document):
                uses = step.get("uses")
                if isinstance(uses, str) and uses.startswith("actions/checkout@"):
                    with self.subTest(workflow=path.name):
                        settings = step.get("with")
                        self.assertIsInstance(settings, dict)
                        self.assertIs(settings.get("persist-credentials"), False)

    def test_toolchain_versions_are_exact_patch_pins(self) -> None:
        for path, document in _workflows():
            for step in _steps(document):
                uses = step.get("uses")
                if not isinstance(uses, str):
                    continue
                action = uses.partition("@")[0]
                setting = TOOLCHAIN_SETTING_BY_ACTION.get(action)
                if setting is None:
                    continue
                with self.subTest(workflow=path.name, action=action):
                    settings = step.get("with")
                    self.assertIsInstance(settings, dict)
                    value = settings.get(setting)
                    self.assertIsInstance(value, str)
                    assert value is not None
                    self.assertIsNotNone(EXACT_PATCH_VERSION.fullmatch(value))

    def test_ci_installs_the_hash_lock_and_project_without_resolution(self) -> None:
        document = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
        assert isinstance(document, dict)
        runs = _run_strings(document)
        self.assertTrue(
            any("--require-hashes" in run and "-r requirements-lock.txt" in run for run in runs),
            "CI must install the committed hash lock before anything else",
        )
        self.assertTrue(
            any(
                "--no-deps" in run and "--no-build-isolation" in run and "-e ." in run
                for run in runs
            ),
            "CI must install the project without dependency resolution",
        )

    def test_ci_builds_python_artifacts_without_isolation(self) -> None:
        document = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
        assert isinstance(document, dict)
        build_runs = [run for run in _run_strings(document) if re.search(r"-m build\b", run)]
        self.assertTrue(build_runs, "CI must build Python artifacts")
        for run in build_runs:
            with self.subTest(run=run):
                self.assertIn("--no-isolation", run)


class InstallHygienePolicyTests(unittest.TestCase):
    def test_python_installs_are_hash_locked_or_dependency_free(self) -> None:
        sources: list[tuple[str, str]] = []
        for path, document in _workflows():
            sources.extend((path.name, run) for run in _run_strings(document))
        sources.append(("setup_windows.ps1", SETUP_SCRIPT_PATH.read_text(encoding="utf-8")))
        sources.append(("build_release.ps1", BUILD_SCRIPT_PATH.read_text(encoding="utf-8")))
        saw_locked_install = False
        for origin, text in sources:
            for args in _pip_install_arguments(text):
                with self.subTest(origin=origin, args=args):
                    self.assertNotIn("--upgrade", args)
                    self.assertTrue(
                        "--require-hashes" in args or "--no-deps" in args,
                        "PyPI installs must be hash-locked or dependency-free",
                    )
                    saw_locked_install = saw_locked_install or "--require-hashes" in args
        self.assertTrue(saw_locked_install, "no hash-locked dependency install was found")

    def test_npm_never_resolves_packages_ad_hoc(self) -> None:
        saw_clean_install = False
        for path, document in _workflows():
            for run in _run_strings(document):
                with self.subTest(workflow=path.name, run=run):
                    self.assertIsNone(NPM_AD_HOC_COMMAND.search(run))
                saw_clean_install = saw_clean_install or run.startswith("npm ci")
        for label, path in (
            ("setup_windows.ps1", SETUP_SCRIPT_PATH),
            ("build_release.ps1", BUILD_SCRIPT_PATH),
        ):
            for line in _script_command_lines(path):
                stripped = line.strip()
                if not (stripped.startswith("npm ") or stripped.startswith("& npm ")):
                    continue
                with self.subTest(script=label, line=stripped):
                    self.assertIsNone(NPM_AD_HOC_COMMAND.search(stripped))
                saw_clean_install = saw_clean_install or "npm ci" in stripped
        self.assertTrue(saw_clean_install, "no npm clean install was found")

    def test_cargo_operations_use_the_committed_lock(self) -> None:
        for path, document in _workflows():
            for run in _run_strings(document):
                if CARGO_LOCKED_OPERATION.search(run):
                    with self.subTest(workflow=path.name, run=run):
                        self.assertIn("--locked", run)
        for label, path in (
            ("setup_windows.ps1", SETUP_SCRIPT_PATH),
            ("build_release.ps1", BUILD_SCRIPT_PATH),
        ):
            for line in _script_command_lines(path):
                if not re.match(r"(?i)\s*cargo\b", line):
                    continue
                with self.subTest(script=label, line=line.strip()):
                    self.assertIn("--locked", line)

    def test_clean_wheel_environment_survives_until_bundle_launcher_smoke(self) -> None:
        script = BUILD_SCRIPT_PATH.read_text(encoding="utf-8")
        launcher = script.index("-ManifestSha256 $manifestDigest")
        evidence = script.index("--launcher-smoke-passed")
        cleanup = script.index("Remove-Item -LiteralPath $resolvedSmoke")
        self.assertLess(launcher, evidence)
        self.assertLess(evidence, cleanup)


class DependencyLockTests(unittest.TestCase):
    def test_cargo_lock_is_committed(self) -> None:
        self.assertTrue(CARGO_LOCK_PATH.is_file(), "Cargo.lock must be committed")

    def test_lock_pins_every_entry_with_hashes(self) -> None:
        self.assertTrue(LOCK_PATH.is_file(), "requirements-lock.txt must be committed")
        lines = LOCK_PATH.read_text(encoding="utf-8").splitlines()
        requirement_lines = [line for line in lines if LOCK_REQUIREMENT_LINE.match(line)]
        self.assertTrue(requirement_lines, "lock must contain pinned requirements")
        hash_count = sum(line.count("--hash=sha256:") for line in lines)
        self.assertGreaterEqual(hash_count, len(requirement_lines))
        for line in requirement_lines:
            with self.subTest(line=line):
                self.assertTrue(line.endswith(" \\"), "requirement must continue onto hashes")

    def test_lock_covers_exact_pyproject_requirements(self) -> None:
        project = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
        requirements = list(project["project"]["dependencies"])
        requirements += project["project"]["optional-dependencies"]["dev"]
        self.assertTrue(requirements)
        lock_text = LOCK_PATH.read_text(encoding="utf-8")
        for requirement in requirements:
            with self.subTest(requirement=requirement):
                self.assertIsNotNone(
                    EXACT_REQUIREMENT.fullmatch(requirement),
                    "pyproject requirements must be exact pins",
                )
                name, _, version = requirement.partition("==")
                pattern = rf"(?mi)^{re.escape(name)}=={re.escape(version)}\s"
                self.assertIsNotNone(re.search(pattern, lock_text))

    def test_vision_extras_have_a_separate_exact_hash_lock(self) -> None:
        project = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
        requirements = project["project"]["optional-dependencies"]["vision"]
        lock_text = VISION_LOCK_PATH.read_text(encoding="utf-8")
        for requirement in requirements:
            with self.subTest(requirement=requirement):
                self.assertIsNotNone(EXACT_REQUIREMENT.fullmatch(requirement))
                name, _, version = requirement.partition("==")
                normalized = re.escape(name).replace("\\-", "[-_]")
                pattern = rf"(?mi)^{normalized}=={re.escape(version)}\s"
                self.assertIsNotNone(re.search(pattern, lock_text))
        requirement_lines = [
            line for line in lock_text.splitlines() if LOCK_REQUIREMENT_LINE.match(line)
        ]
        hash_count = lock_text.count("--hash=sha256:")
        self.assertGreaterEqual(hash_count, len(requirement_lines))

    def test_build_backend_is_pinned_and_covered_by_the_lock(self) -> None:
        project = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
        requires = project["build-system"]["requires"]
        dev_requirements = project["project"]["optional-dependencies"]["dev"]
        self.assertTrue(requires)
        for requirement in requires:
            with self.subTest(requirement=requirement):
                self.assertIsNotNone(
                    EXACT_REQUIREMENT.fullmatch(requirement),
                    "build backend must be an exact pin for no-isolation builds",
                )
                self.assertIn(requirement, dev_requirements)


class ReleaseScriptPolicyTests(unittest.TestCase):
    def test_setup_script_installs_locked_dependencies(self) -> None:
        text = SETUP_SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn("--require-hashes", text)
        self.assertIn("requirements-lock.txt", text)
        self.assertIn("--no-deps --no-build-isolation", text)
        self.assertNotIn("pip install --upgrade pip", text)

    def test_release_build_script_uses_locked_inputs(self) -> None:
        text = BUILD_SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn("--require-hashes", text)
        self.assertIn("requirements-lock.txt", text)
        self.assertIn("--no-deps --no-build-isolation", text)
        self.assertIn("-m build --no-isolation", text)
        self.assertIn("cargo build --workspace --release --locked", text)
        self.assertIn("npm ci --include=dev", text)
        self.assertNotIn("pip install --upgrade pip", text)


if __name__ == "__main__":
    unittest.main()
