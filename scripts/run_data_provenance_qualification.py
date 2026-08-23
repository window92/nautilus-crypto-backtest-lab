#!/usr/bin/env python3
"""Offline sparse-market qualification for the blocked provenance build."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.m1_qualification import qualify_sparse_real_bar_behavior


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_BAR_DISPOSITIONS = {"REAL_OFFICIAL_BAR", "DERIVED_FROM_OFFICIAL_TRADES"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    export_dir = arguments.export_dir if arguments.export_dir.is_absolute() else ROOT / arguments.export_dir
    output = arguments.output if arguments.output.is_absolute() else ROOT / arguments.output
    if output.exists():
        raise FileExistsError(f"refusing to overwrite qualification evidence: {output}")

    spot_bars = read_jsonl(export_dir / "spot-execution-bars.jsonl")
    coverage = read_jsonl(export_dir / "minute-coverage.jsonl")
    spot_no_trade = {
        int(item["open_time_ms"])
        for item in coverage
        if item["market_profile"] == "BINANCE_SPOT_CASH_LONG_ONLY"
        and item["disposition"] == "VERIFIED_NO_TRADE_INTERVAL"
    }
    exported_spot_times = {int(item["open_time_ms"]) for item in spot_bars}
    inventory = {
        "accepted_spot_bar_count": len(spot_bars),
        "exported_dispositions": sorted({item["disposition"] for item in spot_bars}),
        "forbidden_disposition_count": sum(
            item["disposition"] not in ALLOWED_BAR_DISPOSITIONS for item in spot_bars
        ),
        "verified_no_trade_minute_count": len(spot_no_trade),
        "verified_no_trade_export_overlap_count": len(spot_no_trade & exported_spot_times),
        "spot_export_sha256": hashlib.sha256(
            (export_dir / "spot-execution-bars.jsonl").read_bytes(),
        ).hexdigest(),
    }
    blocked_catalog_path = ROOT / "data/catalog/data-provenance-duckdb-001"
    parquet_payloads = sorted(export_dir.rglob("*.parquet"))
    sparse = qualify_sparse_real_bar_behavior()
    conditions = {
        "only_accepted_real_or_trade_derived_rows_exported": (
            inventory["forbidden_disposition_count"] == 0
        ),
        "verified_no_trade_never_exported_as_bar": (
            inventory["verified_no_trade_export_overlap_count"] == 0
        ),
        "sparse_nautilus_qualification_passed": sparse["status"] == "PASS",
        "parquet_catalog_not_built_after_blocking_gate": (
            not blocked_catalog_path.exists() and not parquet_payloads
        ),
    }
    result = {
        "schema": "data-provenance-nautilus-qualification-v1",
        "status": "PASS" if all(conditions.values()) else "FAIL",
        "conditions": conditions,
        "diagnostic_canonical_export_inventory": inventory,
        "sparse_nautilus_qualification": sparse,
        "parquet_catalog_status": "NOT_BUILT_DATASET_RELEASE_GATE_BLOCKED",
        "blocked_catalog_path": str(blocked_catalog_path.relative_to(ROOT)),
        "parquet_payload_count": len(parquet_payloads),
        "official_trial_started": False,
        "research_strategy_started": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_json_bytes(result) + b"\n")
    print(json.dumps({"output": str(output), "status": result["status"]}, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
