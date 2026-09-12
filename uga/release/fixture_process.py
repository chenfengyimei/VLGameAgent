from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


def launch_owned_python_gui(
    arguments: Sequence[str],
    *,
    cwd: str | Path,
) -> subprocess.Popen[bytes]:
    """Launch a Python GUI whose returned PID owns the eventual window.

    Windows virtual-environment launchers can remain as a wrapper while the
    base interpreter owns Tk windows. Qualification compares the window PID
    with ``Popen.pid`` as an ownership boundary, so launch the base interpreter
    directly and propagate the already-isolated import path explicitly.
    """
    root = Path(cwd).resolve()
    executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    pythonw = executable.with_name("pythonw.exe")
    if pythonw.is_file():
        executable = pythonw
    environment = os.environ.copy()
    import_paths = tuple(
        dict.fromkeys(
            (str(root), *(str(Path(entry).resolve()) for entry in sys.path if entry))
        )
    )
    environment["PYTHONPATH"] = os.pathsep.join(import_paths)
    return subprocess.Popen(
        (str(executable), *arguments),
        cwd=root,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
