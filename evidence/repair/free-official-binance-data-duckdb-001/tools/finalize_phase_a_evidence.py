#!/usr/bin/env python3
"""Close Phase A with an integrity-bound content manifest; perform no adoption."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[4]
PHASE = ROOT / "evidence/repair/free-official-binance-data-duckdb-001"
FINAL = PHASE / "final-content-manifest.json"
CANDIDATE_DIR = PHASE / "ssot-candidate-004"
BASE_SHA = "f51971ed7a09b172c82ff5965f2899d2a302dd71a2af60eb7c920133567b4354"
RUNTIME_SHA = "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd"
DEPENDENCY_SHA = "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47"
DB_SHA = "932e97c446c713e8525f43b8111aced2e914b9579eba10823df7c6b0b51887b6"
DB_SIZE = 1_236_807_680
HEAD = "f379a411bfd45ee566fd99d72ea402776ed48a85"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True)
    return result.stdout


def validate_c003() -> dict[str, Any]:
    phase = ROOT / "evidence/repair/binance-origin-archive-recovery-001"
    inventory_path = phase / "evidence-inventory.json"
    if sha256_file(inventory_path) != "0bcf40dc3d51d44cf9e0f0619698d003de348c5c6789829f20cf95f25828aaf5":
        raise ValueError("Candidate 003 inventory identity changed")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    failures = []
    for entry in inventory["files"]:
        path = ROOT / entry["path"]
        if not path.is_file() or path.stat().st_size != entry["size_bytes"] or sha256_file(path) != entry["sha256"]:
            failures.append(entry["path"])
    if failures:
        raise ValueError(f"Candidate 003 historical bytes changed: {failures[:3]}")
    return {
        "inventory_sha256": sha256_file(inventory_path),
        "validated_file_count": len(inventory["files"]),
        "validation_failure_count": 0,
        "status": "UNCHANGED",
    }


def main() -> int:
    created_at = datetime.now(tz=UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    db = ROOT / "data/duckdb/binance-btcusdt-owner-smoke-001.duckdb"
    if sha256_file(ROOT / "SSOT.md") != BASE_SHA:
        raise ValueError("root SSOT changed")
    if sha256_file(ROOT / "runtime.lock.json") != RUNTIME_SHA:
        raise ValueError("Runtime Lock changed")
    if sha256_file(ROOT / "requirements.lock.txt") != DEPENDENCY_SHA:
        raise ValueError("Dependency Lock changed")
    if db.stat().st_size != DB_SIZE or sha256_file(db) != DB_SHA:
        raise ValueError("current DuckDB changed")
    if git("rev-parse", "HEAD").strip() != HEAD or git("rev-parse", "origin/main").strip() != HEAD:
        raise ValueError("Git identity changed")
    if git("diff", "--name-only") or git("diff", "--cached", "--name-only"):
        raise ValueError("tracked or staged changes exist")
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True, capture_output=True, text=True)

    manifest_path = CANDIDATE_DIR / "candidate-004-manifest.json"
    candidate_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    candidate = CANDIDATE_DIR / "SSOT.candidate-004.md"
    patch = CANDIDATE_DIR / "SSOT.candidate-004.diff"
    if sha256_file(candidate) != candidate_manifest["candidate_ssot_sha256"]:
        raise ValueError("Candidate 004 full-file identity mismatch")
    if sha256_file(patch) != candidate_manifest["diff_sha256"]:
        raise ValueError("Candidate 004 diff identity mismatch")
    if candidate_manifest["adoption_status"] != "PENDING_OWNER_BYTE_ADOPTION":
        raise ValueError("Candidate adoption state is not pending")

    required = {
        "baseline-attestation.json",
        "owner-decisions.json",
        "rejected-candidate-003.json",
        "official-source-contracts.json",
        "raw-object-inventory.json",
        "source-observations.json",
        "spot-conflict-reconciliation.json",
        "spot-trade-continuity.json",
        "verified-no-trade-intervals.json",
        "perpetual-mark-gap-disposition.json",
        "candidate-window-scan.json",
        "selected-window.json",
        "failed-attempts.jsonl",
        "owner-report/README.md",
        "ssot-candidate-004/SSOT.candidate-004.md",
        "ssot-candidate-004/SSOT.candidate-004.sha256",
        "ssot-candidate-004/SSOT.candidate-004.diff",
        "ssot-candidate-004/candidate-004-manifest.json",
        "ssot-candidate-004/semantic-audit.json",
        "ssot-candidate-004/forward-reverse-patch-verification.json",
        "ssot-candidate-004/owner-review-report.md",
    }
    actual = {
        path.relative_to(PHASE).as_posix()
        for path in PHASE.rglob("*")
        if path.is_file() and path != FINAL
    }
    missing = sorted(required - actual)
    if missing:
        raise ValueError(f"required evidence missing: {missing}")

    entries = []
    json_validation_failures = []
    utf8_or_lf_failures = []
    for relative in sorted(actual):
        path = PHASE / relative
        content = path.read_bytes()
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            utf8_or_lf_failures.append(relative)
            text = ""
        if b"\r" in content or (content and not content.endswith(b"\n")):
            utf8_or_lf_failures.append(relative)
        if path.suffix == ".json":
            try:
                json.loads(text)
            except (json.JSONDecodeError, UnicodeDecodeError):
                json_validation_failures.append(relative)
        if relative.endswith(".jsonl"):
            try:
                for line in text.splitlines():
                    json.loads(line)
            except json.JSONDecodeError:
                json_validation_failures.append(relative)
        entries.append(
            {
                "path": relative,
                "size_bytes": len(content),
                "sha256": sha256_bytes(content),
                "line_count": len(content.splitlines()),
            },
        )
    if json_validation_failures or utf8_or_lf_failures:
        raise ValueError(
            f"evidence validation failed: json={json_validation_failures}, text={utf8_or_lf_failures}",
        )

    evidence_text = "\n".join(
        (PHASE / relative).read_text(encoding="utf-8", errors="strict")
        for relative in sorted(actual)
        if not relative.startswith("tools/")
    ).lower()
    forbidden_secret_markers = ["\"authorization\":", "\"x-mbx-apikey\":", "bearer ", "x-amz-signature="]
    secret_hits = [marker for marker in forbidden_secret_markers if marker in evidence_text]
    if secret_hits:
        raise ValueError(f"secret marker found: {secret_hits}")

    new_raw = ROOT / "data/raw/free-official-binance-data-duckdb-001"
    content_manifest = {
        "schema": "free-official-binance-phase-a-final-content-manifest-v1",
        "created_at_utc": created_at,
        "epoch": "FREE_OFFICIAL_BINANCE_DATA_AND_DUCKDB_REPAIR_001",
        "phase_scope": "PHASE_A_OFFICIAL_SOURCE_QUALIFICATION_AND_SSOT_CANDIDATE_004_ONLY",
        "terminal_verdict": "SSOT_CANDIDATE_004_READY_FOR_OWNER_BYTE_ADOPTION",
        "inventory_excludes_itself": True,
        "inventory_self_path": "final-content-manifest.json",
        "file_count_excluding_inventory": len(entries),
        "total_size_bytes_excluding_inventory": sum(item["size_bytes"] for item in entries),
        "files": entries,
        "canonical_file_inventory_sha256": sha256_bytes(canonical_bytes(entries)),
        "required_files_missing": missing,
        "json_validation_failure_count": 0,
        "utf8_or_lf_failure_count": 0,
        "secret_marker_hits": [],
        "raw_local_artifacts": {
            "phase_a_acquisition_path": "data/raw/free-official-binance-data-duckdb-001/phase-a-acquisition.json",
            "phase_a_acquisition_file_sha256": sha256_file(new_raw / "phase-a-acquisition.json"),
            "phase_a_acquisition_identity": "6031d1f37a7e2687ba07988c6d2c9c74d241da368fd3baa4bfd5ffd31f1d8b40",
            "phase_a_analysis_path": "data/raw/free-official-binance-data-duckdb-001/phase-a-analysis.json",
            "phase_a_analysis_file_sha256": sha256_file(new_raw / "phase-a-analysis.json"),
            "phase_a_analysis_identity": "bf7c4d476702a6438e2940d85548943ca1b2b926f74ba64380e20bd0490c654d",
        },
        "candidate_004": {
            "candidate_sha256": candidate_manifest["candidate_ssot_sha256"],
            "diff_sha256": candidate_manifest["diff_sha256"],
            "manifest_sha256": sha256_file(manifest_path),
            "adoption_status": candidate_manifest["adoption_status"],
            "forward_apply_verified": True,
            "reverse_apply_verified": True,
            "fuzz_used": False,
            "offset_used": False,
        },
        "selected_window": candidate_manifest["selected_clean_window"],
        "integrity": {
            "root_ssot_sha256": BASE_SHA,
            "root_ssot_modified": False,
            "runtime_lock_sha256": RUNTIME_SHA,
            "dependency_lock_sha256": DEPENDENCY_SHA,
            "current_duckdb_sha256": DB_SHA,
            "current_duckdb_size_bytes": DB_SIZE,
            "current_duckdb_modified": False,
            "head": HEAD,
            "origin_main": HEAD,
            "tracked_diff_empty": True,
            "staged_diff_empty": True,
            "commit_performed": False,
            "push_performed": False,
            "dataset_release_created": False,
            "strategy_or_official_trial_run": False,
            "historical_candidate_003": validate_c003(),
        },
        "status": "PASS_PENDING_OWNER_BYTE_ADOPTION",
    }
    FINAL.write_text(
        json.dumps(content_manifest, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "status": content_manifest["status"],
                "candidate_sha256": candidate_manifest["candidate_ssot_sha256"],
                "diff_sha256": candidate_manifest["diff_sha256"],
                "file_count": len(entries) + 1,
                "inventory_sha256": content_manifest["canonical_file_inventory_sha256"],
            },
            sort_keys=True,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
