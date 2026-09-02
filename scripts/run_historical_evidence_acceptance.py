#!/usr/bin/env python3
"""Execute every historical validator from its immutable v2 authority."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.git_identity import require_repository_root
from crypto_lab.historical_contracts import HistoricalAuthorityError
from crypto_lab.historical_contracts import load_historical_authority_manifest
from crypto_lab.historical_executor import execute_historical_validator


def _legacy_declared_validators(repository_root: Path) -> set[str]:
    value = json.loads(
        (repository_root / "contracts/historical-contract-snapshots.json").read_text(
            encoding="utf-8",
        ),
    )
    validators = value.get("validators") if isinstance(value, dict) else None
    if not isinstance(validators, dict) or not all(
        isinstance(name, str) and name for name in validators
    ):
        raise HistoricalAuthorityError(
            "EXECUTION_PLAN_MISMATCH",
            "legacy declared-validator inventory is malformed",
        )
    return set(validators)


def run_acceptance(
    *,
    repository_root: Path,
    authority_path: Path | None = None,
    bootstrap_path: Path | None = None,
) -> dict[str, Any]:
    """Run only pinned validator bytes; current-root validators are never called."""

    repository_root = require_repository_root(repository_root)
    authority_path = (
        repository_root / "contracts/historical-validator-authorities-v2.json"
        if authority_path is None
        else authority_path
    )
    bootstrap_path = (
        repository_root / "scripts/isolated_runtime_bootstrap.py"
        if bootstrap_path is None
        else bootstrap_path
    )
    manifest = load_historical_authority_manifest(authority_path)
    plan = tuple(manifest["execution_plan"])
    declared = _legacy_declared_validators(repository_root)
    if set(plan) != declared or len(plan) != 14:
        raise HistoricalAuthorityError(
            "EXECUTION_PLAN_MISMATCH",
            f"v2={sorted(plan)}, legacy={sorted(declared)}",
        )
    results: dict[str, Any] = {}
    for name in plan:
        authority = manifest["authorities"][name]
        profile = manifest["runtime_profiles"][authority.interpreter_profile]
        try:
            execution = execute_historical_validator(
                authority,
                repository_root=repository_root,
                runtime_profile=profile,
                bootstrap_path=bootstrap_path,
            )
        except HistoricalAuthorityError as exc:
            results[name] = {
                "validator_name": name,
                "authority_id": authority.authority_id,
                "bundle_identity": authority.bundle_identity,
                "failure_code": exc.code,
                "failure_reason": exc.reason,
                "detail": exc.detail,
                "pass": False,
                "output_contract_matched": False,
                "historical_evidence_accepted": False,
                "current_root_validator_executed": False,
            }
        else:
            results[name] = execution.to_builtins()
    matched = sum(bool(item["output_contract_matched"]) for item in results.values())
    accepted = sum(bool(item["historical_evidence_accepted"]) for item in results.values())
    return {
        "schema": "historical-evidence-acceptance-v2",
        "status": "PASS" if matched == len(plan) else "FAIL",
        "validator_count": len(plan),
        "passed": matched,
        "failed": len(plan) - matched,
        "authority_output_contracts_matched": matched,
        "authority_output_contract_mismatches": len(plan) - matched,
        "historical_evidence_accepted_count": accepted,
        "historical_evidence_rejected_count": len(plan) - accepted,
        "results": results,
        "legacy_v1_snapshot_is_execution_authority": False,
        "current_root_validator_executed": False,
        "historical_evidence_mutated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authority", type=Path)
    parser.add_argument("--bootstrap", type=Path)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    try:
        result = run_acceptance(
            authority_path=(
                None
                if arguments.authority is None
                else arguments.authority.resolve(strict=True)
            ),
            bootstrap_path=(
                None
                if arguments.bootstrap is None
                else arguments.bootstrap.resolve(strict=True)
            ),
            repository_root=arguments.repository,
        )
        exit_code = 0 if result["status"] == "PASS" else 1
    except (HistoricalAuthorityError, FileNotFoundError, ValueError) as exc:
        result = {
            "schema": "historical-evidence-acceptance-v2",
            "status": "FAIL",
            "validator_count": 0,
            "passed": 0,
            "failed": 0,
            "authority_output_contracts_matched": 0,
            "authority_output_contract_mismatches": 0,
            "historical_evidence_accepted_count": 0,
            "historical_evidence_rejected_count": 0,
            "failure_code": getattr(
                exc,
                "code",
                "HISTORICAL_VALIDATOR_IDENTITY_MISMATCH",
            ),
            "failure_reason": getattr(exc, "reason", type(exc).__name__),
            "detail": str(exc),
            "legacy_v1_snapshot_is_execution_authority": False,
            "current_root_validator_executed": False,
            "historical_evidence_mutated": False,
        }
        exit_code = 1
    payload = canonical_json_bytes(result) + b"\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_bytes(payload)
    print(payload.decode("utf-8"), end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
