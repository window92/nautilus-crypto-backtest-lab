#!/usr/bin/env python3
"""Run and record the fail-closed comprehensive remediation acceptance gates."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import time
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file


ROOT = Path(__file__).resolve().parents[1]
PROJECT_PYTHON = ROOT / ".venv/bin/python"
DATA_PYTHON = ROOT / ".data-venv/bin/python"
TARGETED_MODULES = (
    "tests.unit.test_audit_qualification_validator",
    "tests.unit.test_comprehensive_audit_regressions",
    "tests.unit.test_engine_data_window",
    "tests.unit.test_historical_contracts",
    "tests.unit.test_instrument_representation_funding_checker_repair",
    "tests.unit.test_m4_multiprocess_locking",
    "tests.unit.test_profile_authority",
    "tests.unit.test_result_status",
    "tests.unit.test_runtime_installed_files",
    "tests.unit.test_spot_cash_reconciliation",
    "tests.unit.test_timestamps",
)
EXPLICIT_REGRESSION_MODULES = {
    "RUNTIME_TAMPER_NEGATIVE": ("tests.unit.test_runtime_installed_files",),
    "SPOT_RECONCILIATION": ("tests.unit.test_spot_cash_reconciliation",),
    "SCORING_BOUNDARIES": ("tests.unit.test_engine_data_window",),
    "FUNDING_EXACT_BINDING": (
        "tests.unit.test_instrument_representation_funding_checker_repair.FundingCheckerRepairTests",
    ),
    "MULTIPROCESS_JOURNAL_HOLDOUT": ("tests.unit.test_m4_multiprocess_locking",),
}


def _environment(*, data_tool: bool = False, pycache: Path) -> dict[str, str]:
    pythonpath = [str(ROOT / "src"), str(ROOT)]
    if data_tool:
        pythonpath.append(str(ROOT / ".venv/lib/python3.12/site-packages"))
    return {
        **os.environ,
        "TZ": "UTC",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": str(pycache),
        "PYTHONPATH": os.pathsep.join(pythonpath),
    }


def _test_counts(output: str) -> dict[str, int | None]:
    matches = re.findall(r"Ran ([0-9]+) tests? in ", output)
    reverse_matches = re.findall(r'"execution_occurrences":\s*([0-9]+)', output)
    skipped = re.findall(r"skipped=([0-9]+)", output)
    reverse_skipped = re.findall(r'"skipped":\s*([0-9]+)', output)
    failure_sections = len(re.findall(r"^FAIL:", output, flags=re.MULTILINE))
    error_sections = len(re.findall(r"^ERROR:", output, flags=re.MULTILINE))
    return {
        "tests_run": (
            int(matches[-1])
            if matches
            else int(reverse_matches[-1])
            if reverse_matches
            else None
        ),
        "failures": failure_sections,
        "errors": error_sections,
        "skipped": (
            sum(int(item) for item in skipped)
            if skipped
            else int(reverse_skipped[-1])
            if reverse_skipped
            else 0
        ),
    }


def _run(
    *,
    ordinal: int,
    label: str,
    command: tuple[str, ...],
    log_root: Path,
    pycache: Path,
    data_tool: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=_environment(data_tool=data_tool, pycache=pycache),
        capture_output=True,
        text=True,
        check=False,
    )
    duration = time.monotonic() - started
    combined = completed.stdout + completed.stderr
    log_path = log_root / f"{ordinal:02d}-{label.lower().replace('_', '-')}.log"
    log_payload = (
        f"$ {shlex.join(command)}\n"
        f"exit_code={completed.returncode}\n"
        f"duration_seconds={duration:.6f}\n\n"
        f"{combined}"
    ).encode("utf-8")
    log_path.write_bytes(log_payload)
    counts = _test_counts(combined)
    passed = completed.returncode == 0 and counts["skipped"] == 0
    return {
        "ordinal": ordinal,
        "label": label,
        "status": "PASS" if passed else "FAIL",
        "command": list(command),
        "command_shell_display": shlex.join(command),
        "exit_code": completed.returncode,
        "duration_seconds": round(duration, 6),
        **counts,
        "log_path": log_path.relative_to(log_root.parent).as_posix(),
        "log_sha256": sha256_file(log_path),
    }


def _unittest_command(*modules: str) -> tuple[str, ...]:
    if modules:
        return (str(PROJECT_PYTHON), "-m", "unittest", "-v", *modules)
    return (
        str(PROJECT_PYTHON),
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-t",
        ".",
        "-v",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    output = arguments.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"fresh acceptance output required: {output}")
    output.mkdir(parents=True)
    logs = output / "logs"
    logs.mkdir()
    pycache = Path("/tmp/nautilus-audit-remediation-acceptance-pyc")
    started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    phases: list[dict[str, Any]] = []

    commands: list[tuple[str, tuple[str, ...], bool]] = [
        ("FULL_TEST_DISCOVERY", _unittest_command(), False),
        ("INDEPENDENT_FRESH_PROCESS", _unittest_command(), False),
        (
            "REVERSE_ORDER",
            (
                str(PROJECT_PYTHON),
                str(ROOT / "scripts/run_reverse_test_order.py"),
                "--output-dir",
                str(output / "reverse-order"),
            ),
            False,
        ),
        ("TARGETED_REGRESSIONS", _unittest_command(*TARGETED_MODULES), False),
    ]
    commands.extend(
        (label, _unittest_command(*modules), False)
        for label, modules in EXPLICIT_REGRESSION_MODULES.items()
    )
    commands.extend(
        (
            (
                "RUNTIME_INSTALLED_PAYLOAD_PREFLIGHT",
                (
                    str(PROJECT_PYTHON),
                    "-c",
                    (
                        "from pathlib import Path; "
                        "from crypto_lab.config import RuntimeLock; "
                        "from crypto_lab.runtime import verify_runtime_lock; "
                        "r=Path.cwd(); "
                        "print(verify_runtime_lock(RuntimeLock.from_json_bytes("
                        "(r/'runtime.lock.json').read_bytes()), "
                        "dependency_lock_path=r/'requirements.lock.txt'))"
                    ),
                ),
                False,
            ),
            (
                "HISTORICAL_EVIDENCE_VALIDATORS",
                (
                    str(PROJECT_PYTHON),
                    str(ROOT / "scripts/run_historical_evidence_acceptance.py"),
                    "--output",
                    str(output / "historical-evidence.json"),
                ),
                False,
            ),
            (
                "REPAIRED_PROFILE_QUALIFICATION",
                (
                    str(PROJECT_PYTHON),
                    str(ROOT / "scripts/validate_audit_qualification.py"),
                ),
                False,
            ),
            (
                "DATA_PROVENANCE_VALIDATION",
                (
                    str(DATA_PYTHON),
                    str(ROOT / "scripts/validate_data_provenance_evidence.py"),
                ),
                True,
            ),
            (
                "HISTORICAL_RESULT_STATUS",
                (
                    str(PROJECT_PYTHON),
                    "-c",
                    (
                        "from pathlib import Path; "
                        "from crypto_lab.result_status import load_historical_result_registry; "
                        "root=Path('evidence/audit/comprehensive-remediation-001'); "
                        "historical=load_historical_result_registry(root/'historical-result-status.json'); "
                        "superseded=load_historical_result_registry(root/'runtime-proof-supersession-status.json'); "
                        "assert len(historical.records)==28; assert len(superseded.records)==12; "
                        "print(len(historical.records), len(superseded.records))"
                    ),
                ),
                False,
            ),
            (
                "COMPILEALL",
                (
                    str(PROJECT_PYTHON),
                    "-m",
                    "compileall",
                    "-q",
                    "src",
                    "scripts",
                    "tests",
                ),
                False,
            ),
            ("PROJECT_PIP_CHECK", (str(PROJECT_PYTHON), "-m", "pip", "check"), False),
            ("DATA_PIP_CHECK", (str(DATA_PYTHON), "-m", "pip", "check"), True),
            ("GIT_DIFF_CHECK", ("git", "diff", "--check"), False),
        ),
    )

    for ordinal, (label, command, data_tool) in enumerate(commands, start=1):
        phase = _run(
            ordinal=ordinal,
            label=label,
            command=command,
            log_root=logs,
            pycache=pycache,
            data_tool=data_tool,
        )
        phases.append(phase)
        print(json.dumps({key: phase[key] for key in ("label", "status", "exit_code", "tests_run")}, sort_keys=True), flush=True)

    finished_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    passed = all(phase["status"] == "PASS" for phase in phases)
    full_counts = [
        phase["tests_run"]
        for phase in phases
        if phase["label"] in {"FULL_TEST_DISCOVERY", "INDEPENDENT_FRESH_PROCESS", "REVERSE_ORDER"}
    ]
    if len(set(full_counts)) != 1 or full_counts[0] in {None, 0}:
        passed = False
    result = {
        "schema": "comprehensive-audit-remediation-acceptance-v1",
        "audit_id": "COMPREHENSIVE_AUDIT_REMEDIATION_001",
        "status": "PASS" if passed else "FAIL",
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "source_commit": subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "ssot_sha256": sha256_file(ROOT / "SSOT.md"),
        "runtime_lock_sha256": sha256_file(ROOT / "runtime.lock.json"),
        "dependency_lock_sha256": sha256_file(ROOT / "requirements.lock.txt"),
        "phase_count": len(phases),
        "passed_phase_count": sum(phase["status"] == "PASS" for phase in phases),
        "failed_phase_count": sum(phase["status"] != "PASS" for phase in phases),
        "full_run_test_counts": full_counts,
        "phases": phases,
        "final_holdout_used": False,
        "profitability_claim_authorized": False,
        "network_used": False,
    }
    result["acceptance_identity"] = canonical_sha256(result)
    (output / "acceptance.json").write_bytes(canonical_json_bytes(result) + b"\n")
    print(canonical_json_bytes({
        "status": result["status"],
        "phase_count": result["phase_count"],
        "passed_phase_count": result["passed_phase_count"],
        "failed_phase_count": result["failed_phase_count"],
        "full_run_test_counts": result["full_run_test_counts"],
        "acceptance_identity": result["acceptance_identity"],
    }).decode("utf-8"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
