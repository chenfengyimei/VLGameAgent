from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "qualification"
    / "run_vlm_mumu_matrix.ps1"
)


def test_mumu_qualification_bounds_every_adb_process() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "function Invoke-BoundedProcess" in source
    assert "$process.WaitForExit($TimeoutMilliseconds)" in source
    assert "$process.Kill($true)" in source
    assert "& $Adb" not in source
    assert "& $AdbExecutable" not in source


def test_mumu_qualification_preserves_bounded_page_recovery() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "$startAttempts -ge 3" in source
    assert "$hierarchyAttempt -le 5" in source
    assert '-TimeoutMilliseconds 15000' in source
    assert 'Write-Warning "bounded Settings start failed' in source
