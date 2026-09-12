from __future__ import annotations

import re
import subprocess
from pathlib import Path

from uga.core.errors import ContractViolation

_FULL_GIT_REVISION = re.compile(r"[0-9a-f]{40}")


def is_traceable_source_revision(source_revision: str) -> bool:
    return _FULL_GIT_REVISION.fullmatch(source_revision) is not None


def validate_source_revision(source_revision: str) -> str:
    if not is_traceable_source_revision(source_revision):
        raise ContractViolation("qualification requires a full Git source revision")
    return source_revision


def require_clean_source_revision(project_root: str | Path) -> str:
    """Return HEAD only when the supplied project is a clean Git checkout."""
    root = Path(project_root).resolve()
    try:
        status = subprocess.run(
            ("git", "status", "--porcelain"),
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if status.stdout.strip():
            raise ContractViolation("qualification requires a clean committed source tree")
        revision = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContractViolation("qualification could not inspect the Git source revision") from exc
    return validate_source_revision(revision)
