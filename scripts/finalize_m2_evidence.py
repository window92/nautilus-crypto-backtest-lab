#!/usr/bin/env python3
"""Write one additive M2 integrity/finalization epoch after all gates pass."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import sha256_file
from scripts.validate_m2_evidence import EVIDENCE
from scripts.validate_m2_evidence import validate


def run(command: list[str], *, env: dict[str, str] | None = None) -> dict[str, object]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "command": " ".join(command),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "status": "PASS" if completed.returncode == 0 else "FAIL",
    }


def write_once(path: Path, value: object) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def main() -> int:
    validation = validate()
    compile_result = run([str(ROOT / ".venv/bin/python"), "-m", "compileall", "-q", "src", "scripts", "tests"])
    pip_result = run([str(ROOT / ".venv/bin/python"), "-m", "pip", "check"])
    diff_check = run(["git", "diff", "--check"])
    checks = {
        "compileall": compile_result,
        "pip_check": pip_result,
        "git_diff_check": diff_check,
    }
    if validation["status"] != "PASS" or any(item["status"] != "PASS" for item in checks.values()):
        raise SystemExit("M2 finalization gate failed")
    write_once(EVIDENCE / "evidence-validation.json", validation)
    write_once(
        EVIDENCE / "compile-and-static-checks.json",
        {"schema": "m2-compile-static-checks-v1", "status": "PASS", "checks": checks},
    )

    status = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    paths = sorted(line[3:] for line in status)
    forbidden_changes = [
        path
        for path in paths
        if path in {"SSOT.md", "runtime.lock.json", "requirements.lock.txt"}
        or path.startswith("evidence/m0/")
        or path.startswith("evidence/m1/")
        or "/m3/" in f"/{path}/"
    ]
    ignored_raw = subprocess.run(
        ["git", "status", "--porcelain=v1", "--ignored", "data/raw", "data/catalog"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    ending = {
        "schema": "m2-ending-integrity-v1",
        "status": "PASS" if not forbidden_changes else "FAIL",
        "captured_at_utc": datetime.now(UTC),
        "head_before_m2_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip(),
        "origin_main_before_m2_commit": subprocess.run(
            ["git", "rev-parse", "origin/main"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip(),
        "ssot_sha256": sha256_file(ROOT / "SSOT.md"),
        "runtime_lock_sha256": sha256_file(ROOT / "runtime.lock.json"),
        "dependency_lock_sha256": sha256_file(ROOT / "requirements.lock.txt"),
        "changed_paths": paths,
        "forbidden_changes": forbidden_changes,
        "local_raw_and_catalog_objects_ignored": bool(ignored_raw),
        "m3_started": False,
        "strategy_run": False,
        "official_run": False,
        "commit_pending": True,
        "push_pending": True,
    }
    if ending["status"] != "PASS":
        raise SystemExit(f"forbidden M2 changes: {forbidden_changes}")
    write_once(EVIDENCE / "ending-integrity.json", ending)

    evidence_inventory = [
        {
            "path": str(path.relative_to(ROOT)),
            "sha256": sha256_file(path),
            "byte_size": path.stat().st_size,
        }
        for path in sorted(EVIDENCE.iterdir())
        if path.is_file() and path.name != "final-acceptance-manifest.json"
    ]
    tests = json.loads((EVIDENCE / "test-results-final.json").read_text())
    qualification = json.loads((EVIDENCE / "qualification-summary.json").read_text())
    manifest = {
        "schema": "m2-final-acceptance-manifest-v1",
        "status": "PASS",
        "phase": "M2",
        "tests": {
            "tests_run": tests["tests_run"],
            "passed": tests["passed"],
            "failures": tests["failures"],
            "errors": tests["errors"],
            "skipped": tests["skipped"],
            "phase_totals": tests["phase_totals"],
        },
        "raw_object_count": 21,
        "spot_dataset_release_id": qualification["spot_dataset_release_id"],
        "perpetual_dataset_release_id": qualification["perpetual_dataset_release_id"],
        "evidence_inventory": evidence_inventory,
        "failed_attempts_preserved": True,
        "m3_started": False,
        "strategy_run": False,
        "official_run": False,
        "intended_commit_message": "feat(m2): add frozen Binance dataset pipeline",
    }
    write_once(EVIDENCE / "final-acceptance-manifest.json", manifest)
    print(json.dumps({"status": "PASS", "evidence_files": len(evidence_inventory)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
