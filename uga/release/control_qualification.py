from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from uga.control.executor import InputExecutor
from uga.control.lease import ControlMode, ControlOwner
from uga.control.lease_manager import ControlLeaseManager
from uga.control.lifetime import ActionLifetime
from uga.control.physical import KeyboardAction, KeyEncoding
from uga.control.windows_input import SendInputBackend
from uga.core.artifact_limits import (
    DEFAULT_ARTIFACT_LIMITS,
    parse_json_text,
    read_text_limited,
    sha256_file_limited,
)
from uga.core.errors import ContractViolation
from uga.release.fixture_qualification import _activate, _select_owned_fixture_target
from uga.release.revision import validate_source_revision
from uga.safety.focus_guard import AgentEnableState, FocusGuard, GuardReason
from uga.time.clock import PerfCounterClock, UGATime
from uga.windows.backend import Win32WindowBackend
from uga.windows.integrity import IntegrityLevel, Win32IntegrityProvider


def uipi_probe_passed(
    current_level: IntegrityLevel,
    target_level: IntegrityLevel,
    *,
    executed: bool,
    reason: str,
) -> bool:
    return (
        current_level not in {IntegrityLevel.UNKNOWN, IntegrityLevel.PROTECTED}
        and target_level not in {IntegrityLevel.UNKNOWN, IntegrityLevel.PROTECTED}
        and target_level > current_level
        and not executed
        and reason == GuardReason.INTEGRITY_INCOMPATIBLE.value
    )


def run_supervised_uipi_probe(
    *,
    title_pattern: str,
    expected_pid: int,
    report_path: str | Path,
    source_revision: str,
    allow_physical_input: bool,
) -> Path:
    validate_source_revision(source_revision)
    if not allow_physical_input:
        raise ContractViolation("UIPI probe requires --allow-physical-input")
    windows = Win32WindowBackend()
    target = _select_owned_fixture_target(
        windows,
        title_pattern=title_pattern,
        expected_pid=expected_pid,
        expected_identity=None,
    )
    if not _activate(windows, target.identity.hwnd):
        raise ContractViolation("UIPI probe target could not become foreground")
    integrity = Win32IntegrityProvider()
    current_level = integrity.current_process()
    target_level = integrity.process(target.identity.pid)
    executed = False
    reason = "target integrity is not higher than the qualification process"
    if target_level > current_level:
        clock = PerfCounterClock()
        enabled = AgentEnableState(True)
        leases = ControlLeaseManager(clock)
        backend = SendInputBackend()
        executor = InputExecutor(
            clock,
            backend,
            FocusGuard(windows, integrity, leases, enabled),
            leases,
        )
        lease = leases.grant(
            ControlOwner.EMERGENCY,
            ControlMode.GUI,
            2_000_000_000,
            confidence=1.0,
            reason="supervised UIPI rejection probe",
        )
        now = clock.now()
        # F24 key-up is intentionally side-effect-free even if a broken guard
        # lets it reach SendInput. A key-down is never emitted by this probe.
        probe = KeyboardAction(
            "uipi-f24-key-up-probe",
            ActionLifetime(now, now, UGATime(now.value_ns + 1_000_000_000)),
            0x87,
            False,
            KeyEncoding.VIRTUAL_KEY,
        )
        try:
            result = executor.execute(probe, target.identity, lease)
            executed = result.executed
            reason = str(result.reason)
        finally:
            executor.release_all()
            leases.revoke_all(notify=False)
            enabled.set(False)
    passed = uipi_probe_passed(
        current_level,
        target_level,
        executed=executed,
        reason=reason,
    )
    payload = {
        "schema": "uga.uipi_qualification",
        "schema_version": "1.1",
        "source_revision": source_revision,
        "passed": passed,
        "target": {
            "hwnd": target.identity.hwnd,
            "pid": target.identity.pid,
            "title": target.title,
        },
        "current_integrity": {"name": current_level.name, "value": int(current_level)},
        "target_integrity": {"name": target_level.name, "value": int(target_level)},
        "executed": executed,
        "reason": reason,
        "probe_action": "F24 key-up only",
    }
    destination = Path(report_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if not passed:
        raise ContractViolation(f"UIPI rejection probe did not pass; inspect {destination}")
    return destination


def build_control_qualification_report(
    *,
    fixture_report_path: str | Path,
    uipi_report_path: str | Path,
    output_path: str | Path,
    source_revision: str,
) -> Path:
    validate_source_revision(source_revision)
    fixture_path = Path(fixture_report_path).resolve()
    uipi_path = Path(uipi_report_path).resolve()
    destination = Path(output_path).resolve()
    evidence_root = destination.parent
    try:
        fixture_relative = fixture_path.relative_to(evidence_root).as_posix()
        uipi_relative = uipi_path.relative_to(evidence_root).as_posix()
    except ValueError as exc:
        raise ContractViolation(
            "control qualification inputs must be beneath the output evidence directory"
        ) from exc
    fixture = _read_report(fixture_path, "Fixture qualification report")
    uipi = _read_report(uipi_path, "UIPI qualification report")
    if (
        fixture.get("schema") != "uga.fixture_qualification"
        or fixture.get("schema_version") != "1.1"
        or fixture.get("source_revision") != source_revision
        or fixture.get("passed") is not True
    ):
        raise ContractViolation("control qualification requires a passed current Fixture report")
    if (
        uipi.get("schema") != "uga.uipi_qualification"
        or uipi.get("schema_version") != "1.1"
        or uipi.get("source_revision") != source_revision
        or uipi.get("passed") is not True
        or not _uipi_report_is_real_rejection(uipi)
    ):
        raise ContractViolation("control qualification requires a passed current UIPI report")
    fixture_control = fixture.get("control")
    if not isinstance(fixture_control, dict):
        raise ContractViolation("Fixture control report is missing")
    exercises: dict[str, Any] = {
        name: fixture_control.get(name)
        for name in (
            "focus_loss",
            "held_key_fault",
            "emergency_hotkey",
            "watchdog_timeout",
        )
    }
    exercises["uipi_mismatch"] = {
        "exercised": True,
        "passed": True,
        "mechanism": "win32_integrity_guard",
        "probe_report_sha256": sha256_file_limited(
            uipi_path,
            DEFAULT_ARTIFACT_LIMITS.max_document_bytes,
            "UIPI qualification report",
        ),
    }
    if any(
        not _fixture_exercise_passed(name, item)
        for name, item in exercises.items()
        if name != "uipi_mismatch"
    ):
        raise ContractViolation("control qualification has an incomplete Fixture exercise")
    payload = {
        "schema": "uga.control_qualification",
        "schema_version": "1.1",
        "source_revision": source_revision,
        "passed": True,
        "fixture_report": fixture_relative,
        "fixture_report_sha256": sha256_file_limited(
            fixture_path,
            DEFAULT_ARTIFACT_LIMITS.max_document_bytes,
            "Fixture qualification report",
        ),
        "uipi_report": uipi_relative,
        "uipi_report_sha256": exercises["uipi_mismatch"]["probe_report_sha256"],
        "exercises": exercises,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return destination


def _read_report(path: Path, label: str) -> dict[str, Any]:
    payload: Any = parse_json_text(
        read_text_limited(path, DEFAULT_ARTIFACT_LIMITS.max_document_bytes, label)
    )
    if not isinstance(payload, dict):
        raise ContractViolation(f"{label} must be an object")
    return payload


def _uipi_report_is_real_rejection(payload: dict[str, Any]) -> bool:
    current = payload.get("current_integrity")
    target = payload.get("target_integrity")
    if not isinstance(current, dict) or not isinstance(target, dict):
        return False
    current_value = current.get("value")
    target_value = target.get("value")
    valid_values = {
        int(level)
        for level in IntegrityLevel
        if level not in {IntegrityLevel.UNKNOWN, IntegrityLevel.PROTECTED}
    }
    return (
        isinstance(current_value, int)
        and not isinstance(current_value, bool)
        and current_value in valid_values
        and isinstance(target_value, int)
        and not isinstance(target_value, bool)
        and target_value in valid_values
        and target_value > current_value
        and payload.get("executed") is False
        and payload.get("reason") == GuardReason.INTEGRITY_INCOMPATIBLE.value
        and payload.get("probe_action") == "F24 key-up only"
    )


def _fixture_exercise_passed(name: str, payload: Any) -> bool:
    if not isinstance(payload, dict) or payload.get("passed") is not True:
        return False
    if name == "emergency_hotkey":
        return payload.get("registered") is True
    return payload.get("exercised") is True
