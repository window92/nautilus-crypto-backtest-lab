#!/usr/bin/env python3
"""Create a complete, non-self-referential inventory of phase evidence."""

from __future__ import annotations

from collections import Counter
import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


REPO = Path("/home/builder/projects/nautilus-crypto-backtest-lab")
PHASE_REL = Path("evidence/repair/binance-origin-archive-recovery-001")
PHASE = REPO / PHASE_REL
OUTPUT = PHASE / "evidence-inventory.json"
EXPECTED_HEAD = "f379a411bfd45ee566fd99d72ea402776ed48a85"
EXPECTED_SSOT = "f51971ed7a09b172c82ff5965f2899d2a302dd71a2af60eb7c920133567b4354"
EXPECTED_DB = "932e97c446c713e8525f43b8111aced2e914b9579eba10823df7c6b0b51887b6"


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True, text=True).stdout.strip()


def classification(relative: Path) -> str:
    if relative.parts[0] == "raw":
        return "RAW_PUBLIC_ACQUISITION_EVIDENCE"
    if relative.parts[0] == "validator":
        return "EVIDENCE_ONLY_VALIDATOR"
    if relative.parts[0] == "ssot-candidate-003":
        return "SSOT_CANDIDATE_003"
    if relative.parts[0] == "owner-report":
        return "OWNER_REPORT"
    return "QUALIFICATION_RESULT"


def main() -> int:
    required = [
        "baseline-attestation.json",
        "provider-source-references.json",
        "provider-coverage-qualification.json",
        "control-window-comparison.json",
        "target-mark-gap-status.json",
        "spot-no-trade-status.json",
        "daily-404-reconciliation.json",
        "failed-attempts.jsonl",
        "owner-report/README.md",
        "ssot-candidate-003/SSOT.candidate-003.md",
        "ssot-candidate-003/SSOT.candidate-003.sha256",
        "ssot-candidate-003/SSOT.candidate-003.diff",
        "ssot-candidate-003/candidate-manifest.json",
        "ssot-candidate-003/semantic-change-summary.json",
        "ssot-candidate-003/round-trip-verification.json",
    ]
    missing = [relative for relative in required if not (PHASE / relative).is_file()]
    if missing:
        raise RuntimeError(f"missing required phase files: {missing}")

    records: list[dict[str, Any]] = []
    json_failures: list[str] = []
    text_failures: list[str] = []
    for path in sorted(item for item in PHASE.rglob("*") if item.is_file() and item != OUTPUT):
        raw = path.read_bytes()
        relative = path.relative_to(PHASE)
        record: dict[str, Any] = {
            "path": (PHASE_REL / relative).as_posix(),
            "phase_relative_path": relative.as_posix(),
            "classification": classification(relative),
            "size_bytes": len(raw),
            "sha256": sha256(raw),
        }
        if path.suffix in {".json", ".jsonl", ".md", ".py", ".diff", ".sha256", ".txt"}:
            try:
                text = raw.decode("utf-8")
                record["utf8_valid"] = True
                record["lf_only"] = b"\r" not in raw
                record["line_count"] = len(text.splitlines())
                if b"\r" in raw:
                    text_failures.append(f"non_lf:{relative}")
            except UnicodeDecodeError:
                record["utf8_valid"] = False
                text_failures.append(f"invalid_utf8:{relative}")
        if path.suffix == ".json":
            try:
                json.loads(raw)
                record["json_valid"] = True
            except Exception as exc:  # validation evidence retains type only
                record["json_valid"] = False
                json_failures.append(f"{relative}:{type(exc).__name__}")
        elif path.suffix == ".jsonl":
            try:
                for line in raw.splitlines():
                    json.loads(line)
                record["jsonl_valid"] = True
            except Exception as exc:
                record["jsonl_valid"] = False
                json_failures.append(f"{relative}:{type(exc).__name__}")
        records.append(record)

    observation_failures: list[str] = []
    status_codes: Counter[int] = Counter()
    observation_count = 0
    for observation_path in sorted(PHASE.rglob("*.observation.json")):
        observation_count += 1
        payload = json.loads(observation_path.read_text(encoding="utf-8"))
        status_codes[payload["status_code"]] += 1
        body = REPO / payload["body_path"]
        headers = REPO / payload["headers_path"]
        if not body.is_file() or not headers.is_file():
            observation_failures.append(f"missing_binding:{observation_path.relative_to(PHASE)}")
            continue
        raw = body.read_bytes()
        if len(raw) != payload["body_size_bytes"] or sha256(raw) != payload["body_sha256"]:
            observation_failures.append(f"body_identity:{observation_path.relative_to(PHASE)}")
        if payload.get("credentials_used") is not False:
            observation_failures.append(f"credential_flag:{observation_path.relative_to(PHASE)}")
        if payload.get("parsed_before_body_saved") is not False:
            observation_failures.append(f"raw_before_parse:{observation_path.relative_to(PHASE)}")

    ssot_raw = (REPO / "SSOT.md").read_bytes()
    database_path = REPO / "data/duckdb/binance-btcusdt-owner-smoke-001.duckdb"
    head = git("rev-parse", "HEAD")
    origin_main = git("rev-parse", "origin/main")
    tracked_diff = git("diff", "--name-only")
    status = git("status", "--short")
    status_lines = status.splitlines() if status else []
    phase_only_worktree = bool(status_lines) and all(
        line.startswith("?? evidence/repair/binance-origin-archive-recovery-001/") for line in status_lines
    )

    failures = [*missing, *json_failures, *text_failures, *observation_failures]
    integrity = {
        "head_unchanged": head == EXPECTED_HEAD,
        "origin_main_unchanged": origin_main == EXPECTED_HEAD,
        "root_ssot_unchanged": sha256(ssot_raw) == EXPECTED_SSOT,
        "duckdb_unchanged": sha256_file(database_path) == EXPECTED_DB,
        "tracked_diff_empty": tracked_diff == "",
        "phase_only_untracked_worktree": phase_only_worktree,
    }
    if not all(integrity.values()):
        failures.append(f"integrity:{integrity}")

    payload = {
        "schema": "binance-origin-archive-recovery-evidence-inventory-v1",
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "inventory_excludes_itself": True,
        "inventory_self_path": (PHASE_REL / OUTPUT.name).as_posix(),
        "file_count_excluding_inventory": len(records),
        "total_size_bytes_excluding_inventory": sum(record["size_bytes"] for record in records),
        "classification_counts": dict(sorted(Counter(record["classification"] for record in records).items())),
        "http_observation_count": observation_count,
        "http_status_code_counts": {str(code): count for code, count in sorted(status_codes.items())},
        "raw_observation_binding_failures": observation_failures,
        "json_validation_failures": json_failures,
        "text_validation_failures": text_failures,
        "required_files": required,
        "missing_required_files": missing,
        "integrity": integrity,
        "credentials_or_signed_private_urls_committed": False,
        "commit_or_push_performed": False,
        "files": records,
        "status": "PASS" if not failures else "FAIL",
    }
    OUTPUT.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "file_count_excluding_inventory": len(records),
                "observations": observation_count,
                "status": payload["status"],
            },
            sort_keys=True,
        )
    )
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
