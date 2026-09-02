#!/usr/bin/env python3
"""Run exactly one M3 qualification attempt in a fresh Python process."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path


def main() -> int:
    state = sys.modules.get("_crypto_lab_verified_bootstrap")
    attestation = getattr(state, "ATTESTATION", None)
    if (
        not isinstance(attestation, Mapping)
        or attestation.get("target") != "scripts/run_m3_child.py"
    ):
        raise RuntimeError(
            "RUNTIME_STARTUP_MISMATCH: M3 child requires isolated bootstrap",
        )

    # Product and Nautilus imports are deliberately after the stdlib-only
    # bootstrap state check.
    from crypto_lab.config import MarketProfile
    from crypto_lab.hashing import canonical_json_bytes
    from crypto_lab.git_identity import require_repository_root
    from crypto_lab.m3 import M3NegativeControl
    from crypto_lab.m3 import build_m3_request
    from crypto_lab.m3 import negative_qualification_inputs
    from crypto_lab.m3 import qualification_dataset_release
    from crypto_lab.m3 import qualification_strategy_inputs
    from crypto_lab.runner import LabRunRequest
    from crypto_lab.runner import QualificationControl
    from crypto_lab.runner import capture_source_revision
    from crypto_lab.runner import run_lab

    def profile_for(value: str) -> MarketProfile:
        return {
            "spot": MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            "perpetual": MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        }[value]

    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("spot", "perpetual"), required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--negative-control", choices=tuple(item.value for item in M3NegativeControl))
    parser.add_argument("--network-attempt", action="store_true")
    parser.add_argument("--invalid-mark-binding", action="store_true")
    args = parser.parse_args()
    product = attestation.get("product")
    if not isinstance(product, Mapping):
        raise RuntimeError("RUNTIME_STARTUP_MISMATCH: M3 child product authority is absent")
    repository = require_repository_root(
        args.repository,
        expected_repository_identity=product.get("repository_identity"),
        expected_git_commit=product.get("source_commit"),
        expected_git_tree=product.get("source_tree"),
    )
    if str(repository) != attestation.get("repository_root"):
        raise RuntimeError(
            "RUNTIME_STARTUP_MISMATCH: M3 child repository differs from bootstrap root",
        )

    profile = profile_for(args.profile)
    release = qualification_dataset_release(profile, repository_root=repository)
    inputs = (
        negative_qualification_inputs(M3NegativeControl(args.negative_control))
        if args.negative_control
        else qualification_strategy_inputs(profile)
    )
    control = (
        QualificationControl.NETWORK_ATTEMPT_NEGATIVE_CONTROL
        if args.network_attempt
        else QualificationControl.STANDARD
    )
    request = build_m3_request(
        release,
        source_revision=capture_source_revision(repository),
        evidence_root=args.evidence_root,
        repository_root=repository,
        run_id=args.run_id,
        strategy_inputs=inputs,
        qualification_control=control,
    )
    if args.invalid_mark_binding:
        altered_config = replace(request.lab_run_config, mark_binding="f" * 64)
        request = LabRunRequest(
            lab_run_config=altered_config,
            source_revision=request.source_revision,
            strategy_spec=request.strategy_spec,
            dataset_release=request.dataset_release,
            instrument=request.instrument,
            data=request.data,
            strategy_plan=request.strategy_plan,
            evidence_root=request.evidence_root,
            repository_root=request.repository_root,
            qualification_control=request.qualification_control,
        )
    result = run_lab(request)
    runtime_identity = json.loads(
        (result.evidence_dir / "runtime_identity.json").read_text(encoding="utf-8"),
    )
    startup_attestation = runtime_identity.get("startup_attestation")
    summary = {
        **result.to_builtins(),
        "orders_count": len(result.orders),
        "fills_count": len(result.fills),
        "positions_count": len(result.positions),
        "account_events_count": len(result.account_events),
        "funding_events_count": len(result.funding_events),
        "guard_failure_codes": [
            item["failure_code"]
            for item in result.strategy_observations.get("guard_failures", [])
        ],
        "funding_events": list(result.funding_events),
        "strategy_observations": result.strategy_observations,
        "runtime_startup_verified": bool(
            runtime_identity.get("startup_verified_before_product_import") is True
            and runtime_identity.get("startup_qualification_only") is True
            and isinstance(startup_attestation, dict)
            and startup_attestation.get("target") == "scripts/run_m3_child.py"
        ),
        "runtime_startup_target": (
            None
            if not isinstance(startup_attestation, dict)
            else startup_attestation.get("target")
        ),
        "runtime_startup_attestation_sha256": runtime_identity.get(
            "startup_attestation_sha256",
        ),
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_bytes(canonical_json_bytes(summary) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
