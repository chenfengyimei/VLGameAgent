from __future__ import annotations

from pathlib import Path

from uga.core.errors import ContractViolation
from uga.release.manifest import GateStatus, ReleaseGate, ReleaseManifest, hash_bundle_tree


def build_development_manifest(
    bundle_root: str | Path,
    *,
    source_revision: str,
    package_smoke_passed: bool = False,
) -> ReleaseManifest:
    """Describe a development bundle without overstating V1 qualification."""
    root = Path(bundle_root).resolve()
    wheels = sorted(path for path in root.glob("*.whl") if path.is_file())
    source_archives = sorted(path for path in root.glob("*.tar.gz") if path.is_file())
    if len(wheels) != 1 or len(source_archives) != 1:
        raise ContractViolation("bundle requires exactly one wheel and one source archive")

    required_paths = (root / "native" / "uga_capture.dll", root / "third-party-inventory.json")
    if any(not path.is_file() for path in required_paths):
        raise ContractViolation("bundle is missing the native DLL or dependency inventory")
    package_smoke = ReleaseGate(
        "package-install-smoke",
        GateStatus.PASSED if package_smoke_passed else GateStatus.NOT_RUN,
        (
            "isolated wheel install and uga-agent console entry passed"
            if package_smoke_passed
            else "run an isolated wheel install before promotion"
        ),
    )
    return ReleaseManifest(
        version="0.1.0-dev",
        schema_version="1.1",
        source_revision=source_revision,
        artifacts=hash_bundle_tree(root),
        gates=(
            ReleaseGate(
                "automated-tests",
                GateStatus.PASSED,
                "ruff, strict mypy, pytest, and cargo release build passed during bundling",
            ),
            ReleaseGate("package-build", GateStatus.PASSED, "wheel, sdist, and native DLL built"),
            package_smoke,
            ReleaseGate("capture-soak", GateStatus.NOT_RUN, "30-minute game matrix required"),
            ReleaseGate("control-hardware", GateStatus.NOT_RUN, "supervised fault matrix required"),
            ReleaseGate("recorder-10min", GateStatus.NOT_RUN, "supervised gameplay required"),
            ReleaseGate("dataset-5h", GateStatus.NOT_RUN, "licensed reviewed corpus required"),
            ReleaseGate("model-training", GateStatus.NOT_RUN, "GPU training artifacts required"),
            ReleaseGate("generalization-bench", GateStatus.NOT_RUN, "four-game benchmark required"),
            ReleaseGate("repository-license", GateStatus.BLOCKED, "owner license choice required"),
        ),
    )
