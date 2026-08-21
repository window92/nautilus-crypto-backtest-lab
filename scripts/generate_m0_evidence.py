#!/usr/bin/env python3
"""Generate deterministic M0 qualification evidence from the installed real artifact."""

from __future__ import annotations

import json
import os
from pathlib import Path

from crypto_lab.config import LabRunConfig
from crypto_lab.config import RuntimeLock
from crypto_lab.config import SourceRevision
from crypto_lab.hashing import sha256_file
from crypto_lab.qualification import qualify_latency_contract
from crypto_lab.qualification import qualify_runtime_identity


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_RELATIVE = os.environ.get("M0_EVIDENCE_RELATIVE", "evidence/m0")
EVIDENCE = ROOT / EVIDENCE_RELATIVE


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    lock_path = ROOT / "runtime.lock.json"
    dependency_lock_path = ROOT / "requirements.lock.txt"
    fixture_path = ROOT / "tests/golden/fixtures/spot-lab-run-config.json"
    source_revision_path = ROOT / "tests/golden/fixtures/source-revision.json"
    lock = RuntimeLock.from_json_bytes(lock_path.read_bytes())
    config = LabRunConfig.from_json_bytes(fixture_path.read_bytes())
    source_revision = SourceRevision.from_json_bytes(source_revision_path.read_bytes())
    runtime_lock_sha256 = sha256_file(lock_path)
    if config.runtime_lock_sha256 != runtime_lock_sha256:
        raise RuntimeError(
            "CONFIG_HASH_MISMATCH: downstream fixture does not bind the exact runtime.lock.json bytes",
        )

    runtime = qualify_runtime_identity(
        lock,
        dependency_lock_path=dependency_lock_path,
    )
    latency = qualify_latency_contract(config)
    downstream = {
        "status": "PASS",
        "fixture": "tests/golden/fixtures/spot-lab-run-config.json",
        "parsed_without_defaults": True,
        "runtime_lock_sha256": runtime_lock_sha256,
        "config_sha256": config.config_sha256,
        "market_profile": config.market_profile.value,
        "price_protection_points": config.nautilus_venue_config.price_protection_points,
        "configured_fee_model": None,
        "effective_fee_model": config.nautilus_venue_config.effective_fee_model_path,
        "portfolio_use_mark_prices": config.nautilus_engine_config.portfolio.use_mark_prices,
        "no_market_data_loaded": True,
        "no_strategy_executed": True,
    }
    source_revision_contract = {
        "status": "PASS",
        "qualification_scope": "M0_CONTRACT_SHAPE_ONLY_NO_RUN_EXECUTION",
        "fixture": "tests/golden/fixtures/source-revision.json",
        "parsed_without_defaults": True,
        "separate_from_runtime_lock": True,
        "fields": list(source_revision.to_builtins()),
        "source_revision": source_revision.to_builtins(),
        "no_official_run_executed": True,
        "no_m1_runner_implemented": True,
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    _write_json(EVIDENCE / "runtime-qualification.json", runtime)
    _write_json(EVIDENCE / "latency-qualification.json", latency)
    _write_json(EVIDENCE / "downstream-contract.json", downstream)
    _write_json(EVIDENCE / "source-revision-contract.json", source_revision_contract)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
