#!/usr/bin/env python3
"""Freeze additive official USD-M timestamp endpoint probes for M2 evidence."""

from __future__ import annotations

import csv
import io
import json
import os
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crypto_lab.config import MarketProfile
from crypto_lab.data import AcquisitionRequest
from crypto_lab.data import OfficialBinanceAcquirer
from crypto_lab.data import RawObjectStore
from crypto_lab.data import SourceRole
from crypto_lab.data import TimeRange
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.timestamps import unix_ns_to_utc_datetime


SOURCE_DIR = Path(os.environ.get("M2_SOURCE_DIR", ""))
EVIDENCE = ROOT / "evidence/m2/m2-acceptance-001"
RAW_ROOT = ROOT / "data/raw"
TIME_RANGE = TimeRange(
    start_inclusive=datetime(2025, 1, 1, tzinfo=UTC),
    end_exclusive=datetime(2025, 1, 1, 0, 1, tzinfo=UTC),
)
PROBES = (
    {
        "name": "execution",
        "role": SourceRole.USDM_EXECUTION_TIMESTAMP_PROBE,
        "locator": "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1m&startTime=1735689600000&endTime=1735689659999&limit=1",
        "local": "usdm-kline-time-probe.json",
    },
    {
        "name": "mark",
        "role": SourceRole.USDM_MARK_TIMESTAMP_PROBE,
        "locator": "https://fapi.binance.com/fapi/v1/markPriceKlines?symbol=BTCUSDT&interval=1m&startTime=1735689600000&endTime=1735689659999&limit=1",
        "local": "usdm-mark-time-probe.json",
    },
    {
        "name": "funding",
        "role": SourceRole.USDM_FUNDING_TIMESTAMP_PROBE,
        "locator": "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&startTime=1735689600000&endTime=1735689601000&limit=10",
        "local": "usdm-funding-time-probe.json",
    },
)


def main() -> int:
    if not SOURCE_DIR.is_dir():
        raise SystemExit("M2_SOURCE_DIR is required")
    files = {item["locator"]: SOURCE_DIR / item["local"] for item in PROBES}
    if any(not path.is_file() for path in files.values()):
        raise SystemExit("a required endpoint probe is missing")
    store = RawObjectStore(RAW_ROOT)
    acquirer = OfficialBinanceAcquirer(store, fetch_bytes=lambda locator: files[locator].read_bytes())
    records = {}
    responses = {}
    for item in PROBES:
        path = files[item["locator"]]
        request = AcquisitionRequest(
            source_role=item["role"],
            source_locator=item["locator"],
            exact_filename=item["local"],
            instrument="BTCUSDT",
            market_profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING.value,
            requested_interval="1m" if item["name"] != "funding" else "EVENT",
            requested_time_range=TIME_RANGE,
        )
        record, _ = acquirer.acquire(
            request,
            acquired_at_utc=unix_ns_to_utc_datetime(path.stat().st_mtime_ns),
        )
        records[item["name"]] = record
        responses[item["name"]] = json.loads(store.read_bytes(record.sha256))

    fixture_root = ROOT / "tests/golden/fixtures/m2"
    execution_csv = list(csv.reader(io.StringIO((fixture_root / "usdm-execution.csv").read_text())))[1]
    mark_csv = list(csv.reader(io.StringIO((fixture_root / "usdm-mark.csv").read_text())))[1]
    funding_csv = list(csv.reader(io.StringIO((fixture_root / "usdm-funding.csv").read_text())))[1]
    execution_match = responses["execution"] == [[
        int(execution_csv[0]),
        *execution_csv[1:6],
        int(execution_csv[6]),
        execution_csv[7],
        int(execution_csv[8]),
        *execution_csv[9:12],
    ]]
    mark_match = responses["mark"] == [[
        int(mark_csv[0]),
        *mark_csv[1:6],
        int(mark_csv[6]),
        mark_csv[7],
        int(mark_csv[8]),
        *mark_csv[9:12],
    ]]
    funding_response = responses["funding"]
    funding_match = (
        isinstance(funding_response, list)
        and len(funding_response) == 1
        and funding_response[0].get("symbol") == "BTCUSDT"
        and funding_response[0].get("fundingTime") == int(funding_csv[0])
        and funding_response[0].get("fundingRate") == funding_csv[2]
    )
    if not (execution_match and mark_match and funding_match):
        raise RuntimeError("official endpoint timestamp probe does not match frozen archive rows")

    addendum = {
        "schema": "m2-raw-object-inventory-addendum-v1",
        "status": "PASS",
        "reason": "additive official endpoint probes strengthen USD-M timestamp-unit evidence",
        "object_count": len(records),
        "objects": [records[name].to_builtins() for name in sorted(records)],
    }
    proof = {
        "schema": "m2-usdm-timestamp-endpoint-probes-v1",
        "status": "PASS",
        "requested_boundary": {
            "startTime": 1_735_689_600_000,
            "endTime_execution_mark": 1_735_689_659_999,
            "endTime_funding": 1_735_689_601_000,
            "unit": "MILLISECONDS",
            "utc_start": "2025-01-01T00:00:00Z",
        },
        "execution_archive_row_exact_match": execution_match,
        "mark_archive_row_exact_match": mark_match,
        "funding_archive_event_exact_match": funding_match,
        "objects": {
            name: {
                "source_locator": record.source_locator,
                "sha256": record.sha256,
                "byte_size": record.byte_size,
            }
            for name, record in records.items()
        },
        "digit_length_inference_used": False,
    }
    for path, value in (
        (EVIDENCE / "raw-object-inventory-addendum-001.json", addendum),
        (EVIDENCE / "timestamp-endpoint-probes.json", proof),
    ):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite evidence {path}")
        path.write_bytes(canonical_json_bytes(value) + b"\n")
    print(json.dumps({"status": "PASS", "objects": len(records)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
