#!/usr/bin/env python3
"""Generate an additive REVOKED/INVALIDATED registry for audited historical Runs."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from crypto_lab.checker import check_evidence_directory
from crypto_lab.config import LabRunConfig
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_PRIMARY_RUNS = (
    "owner-smoke-002-perpetual-run-11882dd8dabb",
    "owner-smoke-002-replacement-001-perpetual-run-1959c892b218",
    "owner-smoke-002-replacement-001-spot-run-a754e2c26324",
    "owner-smoke-002-replacement-001-spot-run-retry-001-abbedb975f37",
    "owner-smoke-002-replacement-001-spot-run-retry-002-b25302d138b2",
    "owner-smoke-002-spot-run-48d3cf2e05a0",
    "owner-smoke-002-spot-run-retry-001-8a09aee98d9f",
    "owner-strategy-research-001-perpetual-benchmark-run-4d2108bc43f7",
    "owner-strategy-research-001-perpetual-candidate-a-run-7c03f28261fe",
    "owner-strategy-research-001-perpetual-candidate-b-run-d61049dfda6b",
    "owner-strategy-research-001-spot-benchmark-run-ef60cf17606c",
    "owner-strategy-research-001-spot-candidate-a-run-f1e2c8bc7b40",
    "owner-strategy-research-001-spot-candidate-a-run-retry-001-f1e2c8bc7b40",
    "owner-strategy-research-001-spot-candidate-b-run-91f36cf4151c",
)
SUPERSEDED_REMEDIATION_PRIMARY_RUNS = (
    "comprehensive-audit-remediation-001-perpetual-benchmark-run-29fd33c29504",
    "comprehensive-audit-remediation-001-perpetual-candidate-a-run-f76adf0cf931",
    "comprehensive-audit-remediation-001-perpetual-candidate-b-run-bf60298911ac",
    "comprehensive-audit-remediation-001-spot-benchmark-run-28567cfbf8de",
    "comprehensive-audit-remediation-001-spot-candidate-a-run-ccaf8bd16c10",
    "comprehensive-audit-remediation-001-spot-candidate-b-run-3e1f8986c6d6",
)


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _replay_for(run_name: str) -> Path:
    matches = sorted((ROOT / "runs/replays").glob(f"*/{run_name}"))
    if len(matches) != 1:
        raise RuntimeError(f"expected one historical replay for {run_name}, found {len(matches)}")
    return matches[0]


def _record(
    run_dir: Path,
    *,
    finding_ids: list[str] | None = None,
) -> dict[str, Any]:
    report = check_evidence_directory(
        run_dir,
        repository_root=ROOT,
        source_revision_current_head_required=False,
    )
    config = LabRunConfig.from_json_bytes((run_dir / "lab_run_config.json").read_bytes())
    spot = config.market_profile.value == "BINANCE_SPOT_CASH_LONG_ONLY"
    active_findings = finding_ids or ["F-002", "F-003", "F-001" if spot else "F-004"]
    return {
        "path": run_dir.relative_to(ROOT).as_posix(),
        "market_profile": config.market_profile.value,
        "historical_run_status": "REVOKED",
        "financial_result_status": "INVALIDATED",
        "finding_ids": active_findings,
        "current_checker_outcome": report.outcome.value,
        "current_failure_codes": list(report.failure_codes),
        "historical_bytes_preserved": True,
        "evidence_hashes": {
            name: sha256_file(run_dir / name)
            for name in ("checker.json", "status.json", "evidence_manifest.json")
        },
    }


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recorded-at-utc", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--scope",
        choices=("audited-baseline", "runtime-proof-supersession"),
        default="audited-baseline",
    )
    arguments = parser.parse_args()
    recorded = datetime.fromisoformat(arguments.recorded_at_utc.replace("Z", "+00:00"))
    if recorded.tzinfo is None or recorded.utcoffset() != UTC.utcoffset(recorded):
        raise ValueError("recorded-at-utc must be explicit UTC")
    records: list[dict[str, Any]] = []
    run_names = (
        HISTORICAL_PRIMARY_RUNS
        if arguments.scope == "audited-baseline"
        else SUPERSEDED_REMEDIATION_PRIMARY_RUNS
    )
    findings = None if arguments.scope == "audited-baseline" else ["F-003"]
    for run_name in run_names:
        records.append(_record(ROOT / "runs" / run_name, finding_ids=findings))
        records.append(_record(_replay_for(run_name), finding_ids=findings))
    records.sort(key=lambda item: item["path"])
    if len(records) != 2 * len(run_names):
        raise RuntimeError("historical primary/replay inventory is incomplete")
    policy = (
        "Original evidence bytes remain immutable; this additive registry is the current "
        "authority for financial-result trust status."
        if arguments.scope == "audited-baseline"
        else (
            "The first remediation result generation remains immutable but is superseded: "
            "runtime verification executed fail-closed yet its positive installed-file identity "
            "was not persisted inside each Run. New content-addressed Runs are required."
        )
    )
    manifest = {
        "schema": "audit-historical-result-status-v1",
        "audit_id": "COMPREHENSIVE_AUDIT_REMEDIATION_001",
        "audited_baseline_commit": "890b9d41cc05ff091f41c82409d196c91b86d452",
        "source_commit": _git_head(),
        "recorded_at_utc": recorded.isoformat().replace("+00:00", "Z"),
        "historical_policy": policy,
        "final_holdout_authorized": False,
        "profitability_claim_authorized": False,
        "record_count": len(records),
        "records": records,
    }
    manifest["records_identity"] = canonical_sha256(records)
    payload = canonical_json_bytes(manifest) + b"\n"
    output = arguments.output if arguments.output.is_absolute() else ROOT / arguments.output
    if output.exists() and output.read_bytes() != payload:
        raise RuntimeError(f"immutable invalidation manifest collision: {output}")
    if not output.exists():
        _write_atomic(output, payload)
    print(payload.decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
