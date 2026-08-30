#!/usr/bin/env python3
"""Freeze six new Development-only workflows against repaired qualification."""

from __future__ import annotations

import argparse
import json
from datetime import UTC
from datetime import datetime
from pathlib import Path

from crypto_lab.config import MarketProfile
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.m3 import QualifiedProfileRegistry
from crypto_lab.paths import validate_safe_component
from crypto_lab.research import PartitionRole
from scripts.prepare_owner_strategy_research_001 import RESEARCH_FAMILY
from scripts.prepare_owner_strategy_research_001 import build_protocol


ROOT = Path(__file__).resolve().parents[1]
AUDIT_ID = "COMPREHENSIVE_AUDIT_REMEDIATION_001"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-at-utc", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--epoch", required=True)
    parser.add_argument("--research-family-id", required=True)
    parser.add_argument("--qualification-registry", type=Path, required=True)
    arguments = parser.parse_args()
    epoch = validate_safe_component(arguments.epoch, field="epoch")
    research_family_id = validate_safe_component(
        arguments.research_family_id,
        field="research_family_id",
    )
    frozen = datetime.fromisoformat(arguments.frozen_at_utc.replace("Z", "+00:00"))
    if frozen.tzinfo is None or frozen.utcoffset() != UTC.utcoffset(frozen):
        raise ValueError("frozen-at-utc must be explicit UTC")
    output = arguments.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"fresh workflow output required: {output}")
    qualification_candidate = (
        arguments.qualification_registry
        if arguments.qualification_registry.is_absolute()
        else ROOT / arguments.qualification_registry
    )
    if qualification_candidate.is_symlink():
        raise ValueError("qualification registry must not be a symlink")
    qualification_registry = qualification_candidate.resolve(strict=True)
    try:
        qualification_registry.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError("qualification registry must be inside the repository") from exc
    if not qualification_registry.is_file():
        raise FileNotFoundError("repaired qualification registry is required before workflow freeze")
    registry = QualifiedProfileRegistry.from_json_bytes(qualification_registry.read_bytes())
    active_runtime = sha256_file(ROOT / "runtime.lock.json")
    if any(record.runtime_lock_sha256 != active_runtime for record in registry.records):
        raise RuntimeError("repaired qualification registry does not bind the active Runtime Lock")
    output.mkdir(parents=True)

    workflows = []
    protocols = []
    for profile in (
        MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
    ):
        protocol, profile_workflows = build_protocol(
            profile,
            frozen_at_utc=frozen,
            epoch=epoch,
            research_family_id=research_family_id,
            qualified_profile_registry_path=qualification_registry,
        )
        if (
            protocol.strategy_family != RESEARCH_FAMILY
            or protocol.development_interval.start_inclusive
            != profile_workflows[0].scoring_start
            or protocol.development_interval.end_exclusive
            != profile_workflows[0].scoring_end_exclusive
        ):
            raise RuntimeError("repaired protocol changed the locked strategy/window contract")
        protocols.append(protocol)
        workflows.extend(profile_workflows)

    for workflow in workflows:
        if workflow.partition_role is not PartitionRole.DEVELOPMENT:
            raise RuntimeError("remediation workflow must use exposed Development data only")
        (output / f"{workflow.trial_id}.json").write_bytes(
            workflow.to_json_bytes() + b"\n",
        )
    manifest = {
        "schema": "comprehensive-audit-remediation-workflows-v1",
        "audit_id": AUDIT_ID,
        "frozen_at_utc": frozen.isoformat().replace("+00:00", "Z"),
        "epoch": epoch,
        "research_family_id": research_family_id,
        "qualified_profile_registry": qualification_registry.relative_to(ROOT).as_posix(),
        "qualified_profile_registry_sha256": sha256_file(qualification_registry),
        "runtime_lock_sha256": active_runtime,
        "protocol_ids": [protocol.protocol_id for protocol in protocols],
        "workflow_files": sorted(f"{workflow.trial_id}.json" for workflow in workflows),
        "workflow_count": len(workflows),
        "profile_count": len(protocols),
        "partition_role": PartitionRole.DEVELOPMENT.value,
        "final_holdout_used": False,
        "profitability_claim_authorized": False,
        "optimization_performed": False,
    }
    if len(workflows) != 6 or len(protocols) != 2:
        raise RuntimeError("exactly six workflows and two profile protocols are required")
    manifest["manifest_identity"] = canonical_sha256(manifest)
    (output / "manifest.json").write_bytes(canonical_json_bytes(manifest) + b"\n")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
