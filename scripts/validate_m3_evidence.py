#!/usr/bin/env python3
"""Read-only validation of the additive M3 acceptance evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.historical_contracts import validate_validator_contract
from crypto_lab.m3 import QualificationDownstreamBundle
from crypto_lab.m3 import QualifiedProfileRegistry


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / "evidence/m3/m3-acceptance-001"


def validate(evidence: Path = DEFAULT_EVIDENCE) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    historical_contract = validate_validator_contract(
        Path(__file__).name,
        repository_root=ROOT,
    )
    checks["historical_contract_snapshot"] = historical_contract.acceptable
    summary = json.loads((evidence / "acceptance-summary.json").read_text(encoding="utf-8"))
    checks["both_profiles_qualified"] = summary["profiles"] == {
        "spot": "QUALIFIED",
        "perpetual": "QUALIFIED",
    }
    checks["qualification_only"] = (
        summary["run_purpose"] == "QUALIFICATION"
        and summary["official_run"] is False
        and summary["research_run"] is False
        and summary["profitability_claim"] is False
        and summary["m4_started"] is False
    )
    registry = QualifiedProfileRegistry.from_json_bytes(
        (evidence / "qualified-profile-registry.json").read_bytes(),
    )
    checks["registry_has_exact_profiles"] = len(registry.records) == 2
    for record in registry.records:
        bundle_path = evidence / "downstream" / f"{record.profile_id.value}.json"
        bundle = QualificationDownstreamBundle.from_json_bytes(bundle_path.read_bytes())
        checks[f"downstream_{record.profile_id.value}"] = (
            bundle.profile_record == record
            and bundle.run_result["state"] == "COMPLETED"
            and bundle.run_result["checker_outcome"] == "CHECK_PASS"
            and bundle.mechanical_integrity.state.value == "PASS"
        )
    for name in ("spot-primary", "spot-replay", "perpetual-primary", "perpetual-replay"):
        attempt = json.loads(
            (evidence / "attempt-summaries" / f"{name}.json").read_text(encoding="utf-8"),
        )
        directory = evidence / attempt["evidence_dir"]
        manifest = json.loads((directory / "evidence_manifest.json").read_text(encoding="utf-8"))
        valid = canonical_sha256(manifest["entries"]) == manifest["inventory_content_sha256"]
        for item in manifest["entries"]:
            path = directory / item["path"]
            valid = (
                valid
                and path.is_file()
                and sha256_file(path) == item["sha256"]
                and path.stat().st_size == item["byte_size"]
            )
        checks[f"run_manifest_{name}"] = valid
    replay = json.loads((evidence / "deterministic-replay.json").read_text(encoding="utf-8"))
    checks["fresh_process_replay"] = all(
        item["result"] == "PASS"
        and item["fresh_processes"] is True
        and item["semantic_digest_primary"] == item["semantic_digest_replay"]
        for item in replay.values()
    )
    controls = json.loads((evidence / "negative-controls.json").read_text(encoding="utf-8"))
    checks["negative_controls_complete"] = set(controls) == {
        "SPOT_SHORT",
        "PERP_DIRECT_CROSS_ZERO",
        "PERP_CONCURRENT_ORDER",
        "PERP_ABOVE_MARKET_MAX",
        "PERP_POST_BOUNDARY_OPEN",
        "PROHIBITED_MARK_FALLBACK",
        "NETWORK_ATTEMPT",
        "DUPLICATE_FUNDING_SETTLEMENT",
    }
    checks["duplicate_funding_checker_control"] = (
        controls["DUPLICATE_FUNDING_SETTLEMENT"]["checker"]["outcome"] == "CHECK_FAIL"
        and "FUNDING_DOUBLE_COUNT"
        in controls["DUPLICATE_FUNDING_SETTLEMENT"]["checker"]["failure_codes"]
    )
    qualification_manifest = json.loads(
        (evidence / "qualification-manifest.json").read_text(encoding="utf-8"),
    )
    manifest_ok = canonical_sha256(qualification_manifest["entries"]) == qualification_manifest[
        "content_sha256"
    ]
    for item in qualification_manifest["entries"]:
        path = evidence / item["path"]
        manifest_ok = manifest_ok and path.is_file() and sha256_file(path) == item["sha256"]
    checks["qualification_manifest"] = manifest_ok
    return {
        "schema": "m3-evidence-validation-v1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "historical_contract": historical_contract.to_builtins(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate(args.evidence)
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.write_text(payload, encoding="utf-8")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
