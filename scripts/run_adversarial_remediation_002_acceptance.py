#!/usr/bin/env python3
"""Run the complete offline R2 acceptance matrix and record exact exit codes.

The script is orchestration only.  It cannot start an Owner workflow, consume
a Final Holdout, contact an exchange, authorize profitability, or publish a
result.  All output is required to be a fresh directory below ``/tmp``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT_PYTHON = ROOT / ".venv/bin/python"
DATA_PYTHON = ROOT / ".data-venv/bin/python"
EXPECTED_EPOCH = "adversarial-remediation-002"
R2_MODULES = (
    "tests.adversarial.test_r2_causality_boundaries",
    "tests.adversarial.test_r2_execution_chain",
    "tests.adversarial.test_r2_full_raw_inventory",
    "tests.adversarial.test_r2_historical_authority_builder",
    "tests.adversarial.test_r2_historical_result_status",
    "tests.adversarial.test_r2_historical_result_status_builder",
    "tests.adversarial.test_r2_historical_validator_identity",
    "tests.adversarial.test_r2_locking_durability",
    "tests.adversarial.test_r2_material_valuation_grid",
    "tests.adversarial.test_r2_native_daily_snapshot_order",
    "tests.adversarial.test_r2_native_position_snapshots",
    "tests.adversarial.test_r2_official_active_resolution",
    "tests.adversarial.test_r2_official_metrics",
    "tests.adversarial.test_r2_official_sealing",
    "tests.adversarial.test_r2_perpetual_reconciliation",
    "tests.adversarial.test_r2_repository_authority_supersession",
    "tests.adversarial.test_r2_runtime_bootstrap",
    "tests.adversarial.test_r2_semantic_position_replay",
    "tests.adversarial.test_r2_spot_affordability_mutation",
    "tests.unit.test_r2_acceptance_validator",
    "tests.unit.test_r2_failure_code_vocabulary",
    "tests.unit.test_r2_official_rebuild_plan",
)
MUTATION_MODULES = (
    "tests.adversarial.test_r2_causality_boundaries",
    "tests.adversarial.test_r2_execution_chain",
    "tests.adversarial.test_r2_full_raw_inventory",
    "tests.adversarial.test_r2_historical_authority_builder",
    "tests.adversarial.test_r2_historical_validator_identity",
    "tests.adversarial.test_r2_native_daily_snapshot_order",
    "tests.adversarial.test_r2_native_position_snapshots",
    "tests.adversarial.test_r2_official_active_resolution",
    "tests.adversarial.test_r2_official_sealing",
    "tests.adversarial.test_r2_perpetual_reconciliation",
    "tests.adversarial.test_r2_repository_authority_supersession",
    "tests.adversarial.test_r2_runtime_bootstrap",
    "tests.adversarial.test_r2_semantic_position_replay",
    "tests.adversarial.test_r2_spot_affordability_mutation",
    "tests.unit.test_r2_acceptance_validator",
    "tests.unit.test_r2_failure_code_vocabulary",
)
LEGACY_REGRESSION_MODULES = (
    "tests.unit.test_comprehensive_audit_regressions",
    "tests.unit.test_engine_data_window",
    "tests.unit.test_historical_contracts",
    "tests.unit.test_instrument_representation_funding_checker_repair",
    "tests.unit.test_m3_contracts",
    "tests.unit.test_m4_multiprocess_locking",
    "tests.unit.test_profile_authority",
    "tests.unit.test_result_status",
    "tests.unit.test_runtime_installed_files",
    "tests.unit.test_spot_cash_reconciliation",
    "tests.unit.test_timestamps",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fresh_output(path: Path) -> Path:
    lexical = Path(os.path.abspath(path))
    if lexical.exists() or lexical.is_symlink():
        raise FileExistsError(f"fresh acceptance output required: {lexical}")
    try:
        lexical.relative_to(ROOT)
    except ValueError:
        pass
    else:
        raise ValueError("acceptance output must remain outside the repository")
    parent = lexical.parent.resolve(strict=True)
    if Path("/tmp") not in (parent, *parent.parents):
        raise ValueError("acceptance output must be below /tmp")
    if parent != lexical.parent:
        raise ValueError("acceptance output parent must not traverse a symlink")
    return lexical


def _regular_input(path: Path, *, label: str) -> Path:
    lexical = Path(os.path.abspath(path))
    cursor = Path(lexical.anchor)
    for component in lexical.parts[1:]:
        cursor /= component
        if cursor.is_symlink():
            raise ValueError(f"{label} must not traverse a symlink")
    resolved = lexical.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"{label} must be a regular file")
    return resolved


def _plan_epoch(path: Path) -> str:
    """Return the exact R2 plan epoch without silently selecting a default."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("R2 execution plan is not readable strict JSON") from exc
    epoch = payload.get("epoch") if isinstance(payload, dict) else None
    if (
        not isinstance(epoch, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", epoch) is None
        or (epoch != EXPECTED_EPOCH and not epoch.startswith(f"{EXPECTED_EPOCH}-"))
    ):
        raise ValueError("R2 execution plan has no in-scope explicit epoch")
    return epoch


def _environment(*, pycache: Path, data_tool: bool = False) -> dict[str, str]:
    pythonpath = [str(ROOT / "src"), str(ROOT)]
    if data_tool:
        pythonpath.append(str(ROOT / ".venv/lib/python3.12/site-packages"))
    isolated_home = pycache.parent / "home"
    isolated_home.mkdir(mode=0o700, exist_ok=True)
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(isolated_home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPYCACHEPREFIX": str(pycache),
        "PYTHONPATH": os.pathsep.join(pythonpath),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }


def _test_counts(output: str) -> dict[str, int | None]:
    matches = re.findall(r"Ran ([0-9]+) tests? in ", output)
    reverse = re.findall(r'"execution_occurrences":\s*([0-9]+)', output)
    skipped = re.findall(r"skipped=([0-9]+)", output)
    reverse_skipped = re.findall(r'"skipped":\s*([0-9]+)', output)
    return {
        "tests_run": (
            int(matches[-1])
            if matches
            else int(reverse[-1])
            if reverse
            else None
        ),
        "failures": len(re.findall(r"^FAIL:", output, flags=re.MULTILINE)),
        "errors": len(re.findall(r"^ERROR:", output, flags=re.MULTILINE)),
        "skipped": (
            sum(int(item) for item in skipped)
            if skipped
            else int(reverse_skipped[-1])
            if reverse_skipped
            else 0
        ),
    }


def _write_log(path: Path, *, command: list[str], returncode: int, duration: float, text: str) -> None:
    header = (
        f"$ {shlex.join(command)}\n"
        f"exit_code={returncode}\n"
        f"duration_seconds={duration:.6f}\n"
    )
    path.write_text(header if not text else f"{header}\n{text}", encoding="utf-8")


def _run(
    *,
    ordinal: int,
    label: str,
    command: tuple[str, ...],
    logs: Path,
    pycache: Path,
    data_tool: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=_environment(pycache=pycache, data_tool=data_tool),
        check=False,
        capture_output=True,
        text=True,
    )
    duration = time.monotonic() - started
    combined = completed.stdout + completed.stderr
    counts = _test_counts(combined)
    passed = completed.returncode == 0 and counts["skipped"] == 0
    log = logs / f"{ordinal:02d}-{label.lower().replace('_', '-')}.log"
    _write_log(
        log,
        command=list(command),
        returncode=completed.returncode,
        duration=duration,
        text=combined,
    )
    return {
        "ordinal": ordinal,
        "label": label,
        "status": "PASS" if passed else "FAIL",
        "command": list(command),
        "command_shell_display": shlex.join(command),
        "exit_code": completed.returncode,
        "duration_seconds": round(duration, 6),
        **counts,
        "log_path": log.relative_to(logs.parent).as_posix(),
        "log_sha256": _sha256(log),
    }


def _unittest(*modules: str) -> tuple[str, ...]:
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


def _database_semantic_gate(database: Path, plan: Path) -> tuple[str, ...]:
    code = """
import hashlib
import json
import sys
from pathlib import Path
from scripts.validate_free_official_binance_rebuild import configure_database
from scripts.validate_free_official_binance_rebuild import database_gate
from scripts.validate_free_official_binance_rebuild import resolve_and_compare_catalogs

database = Path(sys.argv[1]).resolve(strict=True)
plan = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
repository = Path.cwd().resolve(strict=True)
bindings = plan["dataset_releases"]
declared = {}
for profile, binding in bindings.items():
    path = (repository / binding["path"]).resolve(strict=True)
    path.relative_to(repository)
    payload = path.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == binding["sha256"]
    declared[profile] = json.loads(payload)
connection = configure_database(database)
try:
    rows = connection.execute(
        "SELECT market_profile, semantic_release_json FROM dataset_releases ORDER BY market_profile"
    ).fetchall()
finally:
    connection.close()
database_releases = {str(profile): json.loads(str(payload)) for profile, payload in rows}
assert database_releases == declared
readonly_gate = database_gate(database)
catalogs = resolve_and_compare_catalogs({"releases": database_releases}, database)
assert set(catalogs) == set(database_releases)
assert all(item.get("status") == "PASS" for item in catalogs.values())
print(json.dumps({
    "status": "PASS",
    "dataset_release_ids": sorted(
        item["dataset_release_id"] for item in database_releases.values()
    ),
    "full_raw_inventory_results": readonly_gate["full_raw_inventory_results"],
    "catalogs": catalogs,
}, sort_keys=True))
""".strip()
    return (str(DATA_PYTHON), "-c", code, str(database), str(plan))


def _qualification_evidence_directory(plan: Path) -> Path:
    payload = json.loads(plan.read_text(encoding="utf-8"))
    binding = payload.get("qualification_registry")
    if not isinstance(binding, dict) or not isinstance(binding.get("path"), str):
        raise ValueError("R2 plan lacks a qualification registry binding")
    registry = (ROOT / binding["path"]).resolve(strict=True)
    registry.relative_to(ROOT)
    if registry.name != "qualified-profile-registry.json" or not registry.is_file():
        raise ValueError("R2 qualification binding is not a regular registry file")
    return registry.parent


def _fresh_wheel_phase(
    *,
    ordinal: int,
    logs: Path,
    nautilus_wheel: Path,
    project_wheel: Path,
) -> dict[str, Any]:
    started = time.monotonic()
    outputs: list[str] = []
    returncode = 0
    commands: list[list[str]] = []
    with tempfile.TemporaryDirectory(prefix="crypto-lab-r2-fresh-wheel-", dir="/tmp") as temporary:
        root = Path(temporary)
        fresh = root / "venv"
        commands = [
            ["/usr/bin/python3.12", "-m", "venv", "--copies", str(fresh)],
            [
                str(fresh / "bin/python"),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                str(nautilus_wheel),
                str(project_wheel),
            ],
            [str(fresh / "bin/python"), "-m", "pip", "check"],
            [
                str(fresh / "bin/python"),
                "-I",
                "-P",
                "-c",
                (
                    "from pathlib import Path; import crypto_lab, importlib.metadata as m; "
                    "from crypto_lab.config import RuntimeLock; "
                    "from crypto_lab.runtime import verify_runtime_lock; "
                    f"r=Path({str(ROOT)!r}); "
                    "o=Path(crypto_lab.__file__).resolve(); "
                    f"assert o.is_relative_to(Path({str(fresh)!r}).resolve()); "
                    "assert m.version('nautilus-crypto-backtest-lab') == '1.0.1.dev0'; "
                    "print(verify_runtime_lock(RuntimeLock.from_json_bytes("
                    "(r/'runtime.lock.json').read_bytes()), "
                    "dependency_lock_path=r/'requirements.lock.txt')['installed_files_verified'])"
                ),
            ],
            [
                str(fresh / "bin/python"),
                "-m",
                "unittest",
                "-v",
                "tests.adversarial.test_r2_causality_boundaries",
                "tests.adversarial.test_r2_official_sealing",
                "tests.adversarial.test_r2_perpetual_reconciliation",
                "tests.adversarial.test_r2_spot_affordability_mutation",
            ],
        ]
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(root / "home"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
        (root / "home").mkdir()
        for command in commands:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            outputs.append(
                f"$ {shlex.join(command)}\nexit_code={completed.returncode}\n"
                + completed.stdout
                + completed.stderr,
            )
            if completed.returncode != 0:
                returncode = completed.returncode
                break
    duration = time.monotonic() - started
    combined = "\n".join(outputs)
    counts = _test_counts(combined)
    passed = returncode == 0 and counts["skipped"] == 0
    log = logs / f"{ordinal:02d}-fresh-locked-wheel-environment.log"
    _write_log(
        log,
        command=["FRESH_LOCKED_WHEEL_ENVIRONMENT", *map(shlex.join, commands)],
        returncode=returncode,
        duration=duration,
        text=combined,
    )
    return {
        "ordinal": ordinal,
        "label": "FRESH_LOCKED_WHEEL_ENVIRONMENT",
        "status": "PASS" if passed else "FAIL",
        "command": commands,
        "exit_code": returncode,
        "duration_seconds": round(duration, 6),
        **counts,
        "log_path": log.relative_to(logs.parent).as_posix(),
        "log_sha256": _sha256(log),
        "temporary_environment_removed": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--data-database", type=Path, required=True)
    parser.add_argument("--nautilus-wheel", type=Path, required=True)
    parser.add_argument("--project-wheel", type=Path, required=True)
    arguments = parser.parse_args(argv)

    output = _fresh_output(arguments.output_dir)
    plan = _regular_input(arguments.plan, label="R2 execution plan")
    database = _regular_input(
        arguments.data_database
        if arguments.data_database.is_absolute()
        else ROOT / arguments.data_database,
        label="DuckDB database",
    )
    nautilus_wheel = _regular_input(arguments.nautilus_wheel, label="Nautilus Wheel")
    project_wheel = _regular_input(arguments.project_wheel, label="project Wheel")
    plan_epoch = _plan_epoch(plan)
    runtime = json.loads((ROOT / "runtime.lock.json").read_text(encoding="utf-8"))
    if (
        nautilus_wheel.name != runtime["nautilus_wheel_filename"]
        or _sha256(nautilus_wheel) != runtime["nautilus_wheel_sha256"]
        or project_wheel.suffix != ".whl"
    ):
        raise ValueError("locked Nautilus or project Wheel identity differs")
    qualification_evidence = _qualification_evidence_directory(plan)

    output.mkdir(mode=0o700)
    logs = output / "logs"
    logs.mkdir()
    pycache = output / "pycache"
    started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    phases: list[dict[str, Any]] = []
    commands: list[tuple[str, tuple[str, ...], bool]] = [
        ("FULL_TEST_DISCOVERY", _unittest(), False),
        ("INDEPENDENT_FRESH_PROCESS_DISCOVERY", _unittest(), False),
        (
            "REVERSE_TEST_ORDER",
            (
                str(PROJECT_PYTHON),
                str(ROOT / "scripts/run_reverse_test_order.py"),
                "--output-dir",
                str(output / "reverse-order"),
            ),
            False,
        ),
        ("R2_TARGETED_REGRESSIONS", _unittest(*R2_MODULES), False),
        ("R2_MUTATION_NEGATIVE_CONTROLS", _unittest(*MUTATION_MODULES), False),
        ("LEGACY_CONTRACT_REGRESSIONS", _unittest(*LEGACY_REGRESSION_MODULES), False),
        (
            "RUNTIME_INSTALLED_PAYLOAD_VERIFIER",
            (
                str(PROJECT_PYTHON),
                "-c",
                (
                    "from pathlib import Path; "
                    "from crypto_lab.config import RuntimeLock; "
                    "from crypto_lab.runtime import verify_runtime_lock; "
                    "r=Path.cwd(); print(verify_runtime_lock(RuntimeLock.from_json_bytes("
                    "(r/'runtime.lock.json').read_bytes()), "
                    "dependency_lock_path=r/'requirements.lock.txt')['installed_files_verified'])"
                ),
            ),
            False,
        ),
        (
            "RUNTIME_STARTUP_INJECTION_NEGATIVES",
            _unittest("tests.adversarial.test_r2_runtime_bootstrap"),
            False,
        ),
        (
            "HISTORICAL_EXECUTABLE_VALIDATORS",
            (
                str(PROJECT_PYTHON),
                str(ROOT / "scripts/run_historical_evidence_acceptance.py"),
                "--output",
                str(output / "historical-evidence.json"),
            ),
            False,
        ),
        (
            "CURRENT_M3_QUALIFICATION_VALIDATION",
            (
                str(PROJECT_PYTHON),
                str(ROOT / "scripts/validate_m3_evidence.py"),
                "--evidence",
                str(qualification_evidence),
            ),
            False,
        ),
        (
            "JOURNAL_HOLDOUT_MULTIPROCESS_DURABILITY",
            _unittest(
                "tests.unit.test_m4_multiprocess_locking",
                "tests.adversarial.test_r2_locking_durability",
            ),
            False,
        ),
        (
            "RAW_OBJECT_AND_PUBLISHER_CHECKSUM_VALIDATION",
            (
                str(DATA_PYTHON),
                str(ROOT / "scripts/validate_free_official_raw_objects.py"),
                "--database",
                str(database),
            ),
            True,
        ),
        (
            "DATASET_RELEASE_DATABASE_CATALOG_SEMANTIC_IDENTITY",
            _database_semantic_gate(database, plan),
            True,
        ),
        (
            "R2_SIX_RUNS_AND_REPLAYS",
            (
                str(PROJECT_PYTHON),
                str(ROOT / "scripts/validate_adversarial_remediation_002_runs.py"),
                "--plan",
                str(plan),
                "--epoch",
                plan_epoch,
                "--output",
                str(output / "r2-runs-validation.json"),
            ),
            False,
        ),
        (
            "COMPILEALL",
            (str(PROJECT_PYTHON), "-m", "compileall", "-q", "src", "scripts", "tests"),
            False,
        ),
        ("PROJECT_PIP_CHECK", (str(PROJECT_PYTHON), "-m", "pip", "check"), False),
        ("DATA_PIP_CHECK", (str(DATA_PYTHON), "-m", "pip", "check"), True),
        ("GIT_DIFF_CHECK", ("git", "diff", "--check"), False),
        (
            "GIT_WORKTREE_CLEAN",
            (
                str(PROJECT_PYTHON),
                "-c",
                (
                    "import subprocess; p=subprocess.run(('git','status','--porcelain=v1',"
                    "'--untracked-files=all'),check=True,capture_output=True); "
                    "assert p.stdout == b'', p.stdout.decode()"
                ),
            ),
            False,
        ),
    ]
    for ordinal, (label, command, data_tool) in enumerate(commands, start=1):
        phase = _run(
            ordinal=ordinal,
            label=label,
            command=command,
            logs=logs,
            pycache=pycache,
            data_tool=data_tool,
        )
        phases.append(phase)
        print(
            json.dumps(
                {key: phase[key] for key in ("label", "status", "exit_code", "tests_run")},
                sort_keys=True,
            ),
            flush=True,
        )
    fresh = _fresh_wheel_phase(
        ordinal=len(phases) + 1,
        logs=logs,
        nautilus_wheel=nautilus_wheel,
        project_wheel=project_wheel,
    )
    phases.append(fresh)
    print(
        json.dumps(
            {key: fresh[key] for key in ("label", "status", "exit_code", "tests_run")},
            sort_keys=True,
        ),
        flush=True,
    )

    full_counts = [
        phase["tests_run"]
        for phase in phases
        if phase["label"]
        in {"FULL_TEST_DISCOVERY", "INDEPENDENT_FRESH_PROCESS_DISCOVERY", "REVERSE_TEST_ORDER"}
    ]
    passed = bool(
        all(phase["status"] == "PASS" for phase in phases)
        and len(full_counts) == 3
        and len(set(full_counts)) == 1
        and full_counts[0] not in {None, 0}
    )
    shutil.rmtree(pycache, ignore_errors=True)
    shutil.rmtree(output / "home", ignore_errors=True)
    supporting_artifacts = [
        {
            "path": path.relative_to(output).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(item for item in output.rglob("*") if item.is_file())
        if path.name != "acceptance.json"
    ]
    result = {
        "schema": "adversarial-remediation-002-acceptance-v1",
        "epoch": plan_epoch,
        "status": "PASS" if passed else "FAIL",
        "started_at_utc": started_at,
        "finished_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_commit": subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "plan_sha256": _sha256(plan),
        "data_database_path": str(database),
        "data_database_sha256": _sha256(database),
        "nautilus_wheel_filename": nautilus_wheel.name,
        "nautilus_wheel_sha256": _sha256(nautilus_wheel),
        "project_wheel_filename": project_wheel.name,
        "project_wheel_sha256": _sha256(project_wheel),
        "ssot_sha256": _sha256(ROOT / "SSOT.md"),
        "runtime_lock_sha256": _sha256(ROOT / "runtime.lock.json"),
        "dependency_lock_sha256": _sha256(ROOT / "requirements.lock.txt"),
        "phase_count": len(phases),
        "passed_phase_count": sum(phase["status"] == "PASS" for phase in phases),
        "failed_phase_count": sum(phase["status"] != "PASS" for phase in phases),
        "full_run_test_counts": full_counts,
        "phases": phases,
        "supporting_artifact_count": len(supporting_artifacts),
        "supporting_artifacts": supporting_artifacts,
        "final_holdout_used": False,
        "live_trading_used": False,
        "profitability_claim_authorized": False,
        "network_used": False,
        "isolated_subprocess_home_removed": True,
        "pycache_removed": True,
    }
    result["acceptance_identity"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()
    (output / "acceptance.json").write_text(
        json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "phase_count": result["phase_count"],
                "passed_phase_count": result["passed_phase_count"],
                "failed_phase_count": result["failed_phase_count"],
                "full_run_test_counts": result["full_run_test_counts"],
                "acceptance_identity": result["acceptance_identity"],
            },
            sort_keys=True,
        ),
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
