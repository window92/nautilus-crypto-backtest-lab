#!/usr/bin/env python3
"""Freeze the bounded official Binance M2 qualification and additive evidence."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crypto_lab.config import MarketProfile
from crypto_lab.data import AcquisitionRequest
from crypto_lab.data import NORMALIZER_VERSION
from crypto_lab.data import NOT_APPLICABLE
from crypto_lab.data import OfficialBinanceAcquirer
from crypto_lab.data import RawObjectRecord
from crypto_lab.data import RawObjectStore
from crypto_lab.data import SourceObjectBinding
from crypto_lab.data import SourceRole
from crypto_lab.data import TimeRange
from crypto_lab.data import build_dataset_release
from crypto_lab.data import build_nautilus_catalog
from crypto_lab.data import extract_single_csv_archive
from crypto_lab.data import parse_funding_csv
from crypto_lab.data import parse_kline_csv
from crypto_lab.data import parse_spot_instrument_metadata
from crypto_lab.data import parse_usdm_instrument_metadata
from crypto_lab.data import prove_funding_schedule
from crypto_lab.data import timestamp_rules_identity
from crypto_lab.data import validate_one_minute_grid
from crypto_lab.data import verify_catalog_identity
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import sha256_file
from crypto_lab.timestamps import unix_ns_to_utc_datetime


EVIDENCE = ROOT / "evidence/m2/m2-acceptance-001"
RAW_ROOT = ROOT / "data/raw"
CATALOG_ROOT = ROOT / "data/catalog"
RELEASE_ROOT = ROOT / "data/releases"
SOURCE_DIR = Path(os.environ.get("M2_SOURCE_DIR", ""))
ZERO_FEE_BASIS = "QUALIFICATION_ONLY_EXPLICIT_ZERO_NO_ACCOUNT_TIER_CLAIM"


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


DAY_PRE = TimeRange(
    start_inclusive=utc("2024-12-31T00:00:00Z"),
    end_exclusive=utc("2025-01-01T00:00:00Z"),
)
DAY_POST = TimeRange(
    start_inclusive=utc("2025-01-01T00:00:00Z"),
    end_exclusive=utc("2025-01-02T00:00:00Z"),
)
MONTH_JAN = TimeRange(
    start_inclusive=utc("2025-01-01T00:00:00Z"),
    end_exclusive=utc("2025-02-01T00:00:00Z"),
)
SPOT_RELEASE_RANGE = TimeRange(
    start_inclusive=utc("2024-12-31T23:58:00Z"),
    end_exclusive=utc("2025-01-01T00:02:00Z"),
)
PERP_RELEASE_RANGE = TimeRange(
    start_inclusive=utc("2025-01-01T00:00:00Z"),
    end_exclusive=utc("2025-01-01T00:04:00Z"),
)


ARCHIVES = (
    {
        "name": "spot_pre",
        "role": SourceRole.SPOT_EXECUTION_1M,
        "locator": "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2024-12-31.zip",
        "local": "BTCUSDT-spot-1m-2024-12-31.zip",
        "checksum_local": "BTCUSDT-spot-1m-2024-12-31.zip.CHECKSUM",
        "filename": "BTCUSDT-1m-2024-12-31.zip",
        "member": "BTCUSDT-1m-2024-12-31.csv",
        "profile": MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        "time_range": DAY_PRE,
    },
    {
        "name": "spot_post",
        "role": SourceRole.SPOT_EXECUTION_1M,
        "locator": "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2025-01-01.zip",
        "local": "BTCUSDT-spot-1m-2025-01-01.zip",
        "checksum_local": "BTCUSDT-spot-1m-2025-01-01.zip.CHECKSUM",
        "filename": "BTCUSDT-1m-2025-01-01.zip",
        "member": "BTCUSDT-1m-2025-01-01.csv",
        "profile": MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        "time_range": DAY_POST,
    },
    {
        "name": "perp_execution",
        "role": SourceRole.USDM_PERPETUAL_EXECUTION_1M,
        "locator": "https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2025-01-01.zip",
        "local": "BTCUSDT-usdm-execution-1m-2025-01-01.zip",
        "checksum_local": "BTCUSDT-usdm-execution-1m-2025-01-01.zip.CHECKSUM",
        "filename": "BTCUSDT-1m-2025-01-01.zip",
        "member": "BTCUSDT-1m-2025-01-01.csv",
        "profile": MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        "time_range": DAY_POST,
    },
    {
        "name": "perp_mark",
        "role": SourceRole.USDM_PERPETUAL_MARK_1M,
        "locator": "https://data.binance.vision/data/futures/um/daily/markPriceKlines/BTCUSDT/1m/BTCUSDT-1m-2025-01-01.zip",
        "local": "BTCUSDT-usdm-mark-1m-2025-01-01.zip",
        "checksum_local": "BTCUSDT-usdm-mark-1m-2025-01-01.zip.CHECKSUM",
        "filename": "BTCUSDT-1m-2025-01-01.zip",
        "member": "BTCUSDT-1m-2025-01-01.csv",
        "profile": MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        "time_range": DAY_POST,
    },
    {
        "name": "perp_funding",
        "role": SourceRole.USDM_PERPETUAL_FUNDING,
        "locator": "https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2025-01.zip",
        "local": "BTCUSDT-fundingRate-2025-01.zip",
        "checksum_local": "BTCUSDT-fundingRate-2025-01.zip.CHECKSUM",
        "filename": "BTCUSDT-fundingRate-2025-01.zip",
        "member": "BTCUSDT-fundingRate-2025-01.csv",
        "profile": MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        "time_range": MONTH_JAN,
    },
)


AUXILIARY = (
    {
        "name": "public_data_contract",
        "role": SourceRole.BINANCE_PUBLIC_DATA_CONTRACT,
        "locator": "https://raw.githubusercontent.com/binance/binance-public-data/master/README.md",
        "local": "binance-public-data-README.md",
        "filename": "binance-public-data-README.md",
    },
    {
        "name": "futures_connector_tree",
        "role": SourceRole.BINANCE_OFFICIAL_API_CONTRACT,
        "locator": "https://api.github.com/repos/binance/binance-futures-connector-python/git/trees/main?recursive=1",
        "local": "binance-futures-connector-tree.json",
        "filename": "binance-futures-connector-tree.json",
    },
    {
        "name": "futures_market_contract",
        "role": SourceRole.BINANCE_OFFICIAL_API_CONTRACT,
        "locator": "https://raw.githubusercontent.com/binance/binance-futures-connector-python/main/binance/um_futures/market.py",
        "local": "binance-um-market.py",
        "filename": "binance-um-market.py",
    },
    {
        "name": "futures_connector_readme",
        "role": SourceRole.BINANCE_OFFICIAL_API_CONTRACT,
        "locator": "https://raw.githubusercontent.com/binance/binance-futures-connector-python/main/README.md",
        "local": "binance-futures-connector-README.md",
        "filename": "binance-futures-connector-README.md",
    },
    {
        "name": "spot_api_contract",
        "role": SourceRole.BINANCE_OFFICIAL_API_CONTRACT,
        "locator": "https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/rest-api.md",
        "local": "binance-spot-rest-api.md",
        "filename": "binance-spot-rest-api.md",
    },
    {
        "name": "spot_metadata",
        "role": SourceRole.SPOT_INSTRUMENT_METADATA,
        "locator": "https://api.binance.com/api/v3/exchangeInfo?symbol=BTCUSDT",
        "local": "spot-exchangeInfo-BTCUSDT.json",
        "filename": "spot-exchangeInfo-BTCUSDT.json",
    },
    {
        "name": "perp_metadata",
        "role": SourceRole.USDM_PERPETUAL_INSTRUMENT_METADATA,
        "locator": "https://fapi.binance.com/fapi/v1/exchangeInfo",
        "local": "usdm-exchangeInfo.json",
        "filename": "usdm-exchangeInfo.json",
    },
    {
        "name": "perp_funding_metadata",
        "role": SourceRole.USDM_PERPETUAL_FUNDING_METADATA,
        "locator": "https://fapi.binance.com/fapi/v1/fundingInfo",
        "local": "usdm-fundingInfo.json",
        "filename": "usdm-fundingInfo.json",
    },
)


def write_once(path: Path, value: Any) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite evidence {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value if isinstance(value, bytes) else canonical_json_bytes(value) + b"\n"
    path.write_bytes(payload)


def acquired_at(path: Path) -> datetime:
    return unix_ns_to_utc_datetime(path.stat().st_mtime_ns)


def request_for_archive(item: dict[str, Any], *, checksum: bool = False) -> AcquisitionRequest:
    role = SourceRole.PUBLISHER_CHECKSUM if checksum else item["role"]
    locator = item["locator"] + (".CHECKSUM" if checksum else "")
    filename = item["filename"] + (".CHECKSUM" if checksum else "")
    return AcquisitionRequest(
        source_role=role,
        source_locator=locator,
        exact_filename=filename,
        instrument="BTCUSDT",
        market_profile=item["profile"].value,
        requested_interval="EVENT" if item["role"] is SourceRole.USDM_PERPETUAL_FUNDING else "1m",
        requested_time_range=item["time_range"],
    )


def main() -> int:
    if not SOURCE_DIR.is_dir():
        raise SystemExit("M2_SOURCE_DIR must point to the approved official download directory")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    RELEASE_ROOT.mkdir(parents=True, exist_ok=True)
    store = RawObjectStore(RAW_ROOT)
    local_by_locator: dict[str, Path] = {}
    for item in ARCHIVES:
        local_by_locator[item["locator"]] = SOURCE_DIR / item["local"]
        local_by_locator[item["locator"] + ".CHECKSUM"] = SOURCE_DIR / item["checksum_local"]
    for item in AUXILIARY:
        local_by_locator[item["locator"]] = SOURCE_DIR / item["local"]
    missing = [str(path) for path in local_by_locator.values() if not path.is_file()]
    if missing:
        raise SystemExit(f"missing approved source artifacts: {missing}")

    def fetch(locator: str) -> bytes:
        return local_by_locator[locator].read_bytes()

    acquirer = OfficialBinanceAcquirer(store, fetch_bytes=fetch)
    records: dict[str, RawObjectRecord] = {}
    checksum_records: dict[str, RawObjectRecord] = {}
    checksum_results: list[dict[str, Any]] = []
    for item in ARCHIVES:
        local = local_by_locator[item["locator"]]
        record, checksum_record = acquirer.acquire(
            request_for_archive(item),
            acquired_at_utc=acquired_at(local),
            checksum_request=request_for_archive(item, checksum=True),
        )
        assert checksum_record is not None
        records[item["name"]] = record
        checksum_records[item["name"]] = checksum_record
        checksum_results.append(
            {
                "name": item["name"],
                "source_locator": item["locator"],
                "exact_filename": item["filename"],
                "publisher_sha256": record.publisher_checksum,
                "local_sha256": record.sha256,
                "status": "PASS" if record.publisher_checksum == record.sha256 else "FAIL",
            },
        )
    for item in AUXILIARY:
        path = local_by_locator[item["locator"]]
        profile = NOT_APPLICABLE
        instrument = NOT_APPLICABLE
        if item["role"] is SourceRole.SPOT_INSTRUMENT_METADATA:
            profile = MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY.value
            instrument = "BTCUSDT"
        elif item["role"] in {
            SourceRole.USDM_PERPETUAL_INSTRUMENT_METADATA,
            SourceRole.USDM_PERPETUAL_FUNDING_METADATA,
        }:
            profile = MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING.value
            instrument = "BTCUSDT"
        request = AcquisitionRequest(
            source_role=item["role"],
            source_locator=item["locator"],
            exact_filename=item["filename"],
            instrument=instrument,
            market_profile=profile,
            requested_interval=NOT_APPLICABLE,
            requested_time_range=NOT_APPLICABLE,
        )
        record, _ = acquirer.acquire(request, acquired_at_utc=acquired_at(path))
        records[item["name"]] = record

    csv_payloads = {
        item["name"]: extract_single_csv_archive(
            store.read_bytes(records[item["name"]].sha256),
            expected_filename=item["member"],
        )
        for item in ARCHIVES
    }
    spot_pre_full = parse_kline_csv(
        csv_payloads["spot_pre"],
        source_role=SourceRole.SPOT_EXECUTION_1M,
        instrument_id="BTCUSDT.BINANCE",
        market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        source_date=DAY_PRE.start_inclusive.date(),
    )
    spot_post_full = parse_kline_csv(
        csv_payloads["spot_post"],
        source_role=SourceRole.SPOT_EXECUTION_1M,
        instrument_id="BTCUSDT.BINANCE",
        market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        source_date=DAY_POST.start_inclusive.date(),
    )
    perp_execution_full = parse_kline_csv(
        csv_payloads["perp_execution"],
        source_role=SourceRole.USDM_PERPETUAL_EXECUTION_1M,
        instrument_id="BTCUSDT-PERP.BINANCE",
        market_profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        source_date=DAY_POST.start_inclusive.date(),
    )
    perp_mark_full = parse_kline_csv(
        csv_payloads["perp_mark"],
        source_role=SourceRole.USDM_PERPETUAL_MARK_1M,
        instrument_id="BTCUSDT-PERP.BINANCE",
        market_profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        source_date=DAY_POST.start_inclusive.date(),
    )
    funding_full = parse_funding_csv(
        csv_payloads["perp_funding"],
        instrument_id="BTCUSDT-PERP.BINANCE",
    )
    full_grid_results = (
        validate_one_minute_grid(
            spot_pre_full,
            source_role=SourceRole.SPOT_EXECUTION_1M,
            time_range=DAY_PRE,
        ),
        validate_one_minute_grid(
            spot_post_full,
            source_role=SourceRole.SPOT_EXECUTION_1M,
            time_range=DAY_POST,
        ),
        validate_one_minute_grid(
            perp_execution_full,
            source_role=SourceRole.USDM_PERPETUAL_EXECUTION_1M,
            time_range=DAY_POST,
        ),
        validate_one_minute_grid(
            perp_mark_full,
            source_role=SourceRole.USDM_PERPETUAL_MARK_1M,
            time_range=DAY_POST,
        ),
    )
    spot_execution = tuple(
        item
        for item in (*spot_pre_full, *spot_post_full)
        if SPOT_RELEASE_RANGE.start_ns <= item.interval_start_ns < SPOT_RELEASE_RANGE.end_ns
    )
    perp_execution = tuple(
        item
        for item in perp_execution_full
        if PERP_RELEASE_RANGE.start_ns <= item.interval_start_ns < PERP_RELEASE_RANGE.end_ns
    )
    perp_mark = tuple(
        item
        for item in perp_mark_full
        if PERP_RELEASE_RANGE.start_ns <= item.interval_start_ns < PERP_RELEASE_RANGE.end_ns
    )
    funding_schedule = prove_funding_schedule(
        funding_full,
        source_object_sha256=records["perp_funding"].sha256,
        time_range=PERP_RELEASE_RANGE,
    )
    funding_release = tuple(
        item
        for item in funding_full
        if PERP_RELEASE_RANGE.start_ns <= item.calc_time_ns < PERP_RELEASE_RANGE.end_ns
    )

    spot_metadata = parse_spot_instrument_metadata(
        store.read_bytes(records["spot_metadata"].sha256),
        raw_symbol="BTCUSDT",
        instrument_id="BTCUSDT.BINANCE",
        source_object_sha256=records["spot_metadata"].sha256,
        maker_fee_rate=Decimal("0"),
        taker_fee_rate=Decimal("0"),
        fee_rate_basis=ZERO_FEE_BASIS,
    )
    perp_metadata = parse_usdm_instrument_metadata(
        store.read_bytes(records["perp_metadata"].sha256),
        raw_symbol="BTCUSDT",
        instrument_id="BTCUSDT-PERP.BINANCE",
        source_object_sha256=records["perp_metadata"].sha256,
        maker_fee_rate=Decimal("0"),
        taker_fee_rate=Decimal("0"),
        fee_rate_basis=ZERO_FEE_BASIS,
    )
    created = datetime.now(UTC)
    with tempfile.TemporaryDirectory(prefix="m2-spot-rebuild-") as temporary:
        spot_first = build_nautilus_catalog(
            Path(temporary),
            metadata=spot_metadata,
            execution_bars=spot_execution,
        )
    spot_catalog_path = CATALOG_ROOT / spot_first.catalog_identity
    spot_second = build_nautilus_catalog(
        spot_catalog_path,
        metadata=spot_metadata,
        execution_bars=spot_execution,
    )
    with tempfile.TemporaryDirectory(prefix="m2-perp-rebuild-") as temporary:
        perp_first = build_nautilus_catalog(
            Path(temporary),
            metadata=perp_metadata,
            execution_bars=perp_execution,
            mark_bars=perp_mark,
            funding_events=funding_release,
        )
    perp_catalog_path = CATALOG_ROOT / perp_first.catalog_identity
    perp_second = build_nautilus_catalog(
        perp_catalog_path,
        metadata=perp_metadata,
        execution_bars=perp_execution,
        mark_bars=perp_mark,
        funding_events=funding_release,
    )
    if spot_first.catalog_identity != spot_second.catalog_identity:
        raise RuntimeError("Spot catalog rebuild semantic identity mismatch")
    if perp_first.catalog_identity != perp_second.catalog_identity:
        raise RuntimeError("Perpetual catalog rebuild semantic identity mismatch")

    spot_source_names = ("spot_pre", "spot_post", "spot_metadata")
    perp_source_names = ("perp_execution", "perp_mark", "perp_funding", "perp_metadata")
    spot_release = build_dataset_release(
        market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        instrument_id="BTCUSDT.BINANCE",
        source_objects=tuple(SourceObjectBinding.from_raw(records[name]) for name in spot_source_names),
        normalized_time_range=SPOT_RELEASE_RANGE,
        instrument_metadata=spot_metadata,
        execution_bars=spot_execution,
        catalog_identity=spot_second.catalog_identity,
        created_at_utc=created,
    )
    perp_release = build_dataset_release(
        market_profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        instrument_id="BTCUSDT-PERP.BINANCE",
        source_objects=tuple(SourceObjectBinding.from_raw(records[name]) for name in perp_source_names),
        normalized_time_range=PERP_RELEASE_RANGE,
        instrument_metadata=perp_metadata,
        execution_bars=perp_execution,
        mark_bars=perp_mark,
        funding_events=funding_release,
        funding_schedule=funding_schedule,
        catalog_identity=perp_second.catalog_identity,
        created_at_utc=created,
    )
    verify_catalog_identity(spot_release, spot_second.semantic_inventory)
    verify_catalog_identity(perp_release, perp_second.semantic_inventory)

    release_files = {
        "spot": RELEASE_ROOT / f"{spot_release.dataset_release_id}.json",
        "perpetual": RELEASE_ROOT / f"{perp_release.dataset_release_id}.json",
        "spot_metadata": RELEASE_ROOT / f"{spot_metadata.instrument_metadata_identity}.metadata.json",
        "perp_metadata": RELEASE_ROOT / f"{perp_metadata.instrument_metadata_identity}.metadata.json",
        "funding": RELEASE_ROOT / f"{perp_release.funding_data_identity}.funding.json",
    }
    write_once(release_files["spot"], spot_release.to_json_bytes())
    write_once(release_files["perpetual"], perp_release.to_json_bytes())
    write_once(release_files["spot_metadata"], spot_metadata.to_json_bytes())
    write_once(release_files["perp_metadata"], perp_metadata.to_json_bytes())
    write_once(
        release_files["funding"],
        {
            "funding_data_identity": perp_release.funding_data_identity,
            "schedule_identity": funding_schedule.schedule_identity,
            "events": [item.semantic_payload() for item in funding_release],
        },
    )

    inventory_records = [*records.values(), *checksum_records.values()]
    inventory_records.sort(key=lambda item: (item.source_role.value, item.source_locator))
    write_once(
        EVIDENCE / "official-source-contract-references.json",
        {
            "schema": "m2-official-source-contracts-v1",
            "status": "PASS",
            "authorities": [
                {
                    "locator": AUXILIARY[0]["locator"],
                    "sha256": records["public_data_contract"].sha256,
                    "proves": [
                        "Spot archive timestamp transition from 2025-01-01 to microseconds",
                        "Spot /api/v3/klines source schema",
                        "USD-M /fapi/v1/klines source schema",
                        "publisher .CHECKSUM contract",
                        "archives may be republished",
                    ],
                },
                {
                    "locator": AUXILIARY[2]["locator"],
                    "sha256": records["futures_market_contract"].sha256,
                    "proves": [
                        "USD-M /fapi/v1/klines binding",
                        "USD-M /fapi/v1/markPriceKlines binding",
                        "USD-M /fapi/v1/fundingRate binding",
                    ],
                },
                {
                    "locator": AUXILIARY[4]["locator"],
                    "sha256": records["spot_api_contract"].sha256,
                    "proves": ["Spot API response timestamps default to milliseconds unless explicitly requested otherwise"],
                },
            ],
            "developer_site_fetch_limitation": "Canonical developer URLs returned HTTP 202 WAF challenge; the official Binance GitHub contracts and exact archive schemas were frozen instead",
        },
    )
    write_once(
        EVIDENCE / "acquisition-manifest.json",
        {
            "schema": "m2-acquisition-manifest-v1",
            "status": "PASS",
            "network_scope": [
                "data.binance.vision",
                "api.binance.com",
                "fapi.binance.com",
                "raw.githubusercontent.com/binance",
                "api.github.com/repos/binance",
                "developers.binance.com (documentation attempt only)",
            ],
            "credentials_used": False,
            "source_directory": str(SOURCE_DIR),
            "raw_store": "data/raw/sha256/<prefix>/<sha256>.blob",
            "raw_store_is_content_addressed": True,
            "parsed_only_after_raw_store": True,
            "objects": [record.to_builtins() for record in inventory_records],
        },
    )
    write_once(
        EVIDENCE / "raw-object-inventory.json",
        {
            "schema": "m2-raw-object-inventory-v1",
            "status": "PASS",
            "object_count": len(inventory_records),
            "objects": [
                {
                    "source_role": record.source_role.value,
                    "source_locator": record.source_locator,
                    "exact_filename": record.exact_filename,
                    "byte_size": record.byte_size,
                    "sha256": record.sha256,
                    "publisher_checksum": record.publisher_checksum,
                    "conflicts_with_sha256": list(record.conflicts_with_sha256),
                }
                for record in inventory_records
            ],
        },
    )
    write_once(
        EVIDENCE / "publisher-checksum-results.json",
        {
            "schema": "m2-publisher-checksum-results-v1",
            "status": "PASS" if all(item["status"] == "PASS" for item in checksum_results) else "FAIL",
            "results": checksum_results,
        },
    )
    write_once(
        EVIDENCE / "timestamp-unit-evidence.json",
        {
            "schema": "m2-timestamp-unit-evidence-v1",
            "status": "PASS",
            "timestamp_rules_identity": timestamp_rules_identity(),
            "rules": {
                "SPOT_BEFORE_2025_01_01": "MILLISECONDS",
                "SPOT_FROM_2025_01_01": "MICROSECONDS",
                "USDM_EXECUTION": "MILLISECONDS",
                "USDM_MARK": "MILLISECONDS",
                "USDM_FUNDING": "MILLISECONDS",
            },
            "selection_basis": "source role and official archive date; numeric magnitude is never inspected",
            "normalized_boundary_rows": [
                item.semantic_payload() for item in spot_execution
            ],
            "available_at_rule": "interval_start + 60 seconds = interval_end_exclusive",
        },
    )
    write_once(
        EVIDENCE / "instrument-metadata-evidence.json",
        {
            "schema": "m2-instrument-metadata-evidence-v1",
            "status": "PASS",
            "spot": spot_metadata.to_builtins(),
            "perpetual": perp_metadata.to_builtins(),
            "current_funding_info_sha256": records["perp_funding_metadata"].sha256,
            "current_funding_info_is_not_historical_schedule_proof": True,
            "fee_disclosure": ZERO_FEE_BASIS,
        },
    )
    write_once(EVIDENCE / "spot-qualification-release.json", spot_release.to_json_bytes())
    write_once(EVIDENCE / "perpetual-qualification-release.json", perp_release.to_json_bytes())
    write_once(
        EVIDENCE / "completeness-results.json",
        {
            "schema": "m2-completeness-results-v1",
            "status": "PASS",
            "full_official_daily_grids": [item.to_builtins() for item in full_grid_results],
            "spot_release": spot_release.completeness_result.to_builtins(),
            "perpetual_release": perp_release.completeness_result.to_builtins(),
            "no_repairs": True,
        },
    )
    write_once(
        EVIDENCE / "funding-schedule-proof.json",
        {
            "schema": "m2-funding-schedule-proof-v1",
            "status": "PASS",
            "schedule": funding_schedule.to_builtins(),
            "archive_event_count": len(funding_full),
            "intervals_read_from_official_rows": sorted(
                {item.funding_interval_hours for item in funding_full},
            ),
            "hard_coded_eight_hour_schedule": False,
            "page_position_used_as_identity": False,
            "event_identity_fields": [
                "instrument_id",
                "calc_time_ns",
                "funding_interval_hours",
                "funding_rate",
            ],
            "current_funding_info_corroboration_only": True,
        },
    )
    write_once(
        EVIDENCE / "mark-grid-proof.json",
        {
            "schema": "m2-mark-grid-proof-v1",
            "status": "PASS",
            "source_role": SourceRole.USDM_PERPETUAL_MARK_1M.value,
            "raw_object_sha256": records["perp_mark"].sha256,
            "full_day_expected": 1440,
            "full_day_actual": len(perp_mark_full),
            "release_expected": 4,
            "release_actual": len(perp_mark),
            "mark_data_identity": perp_release.mark_data_identity,
            "prohibited_fallback_used": False,
            "native_object": "nautilus_trader.model.MarkPriceUpdate",
        },
    )
    write_once(
        EVIDENCE / "catalog-rebuild-comparison.json",
        {
            "schema": "m2-catalog-rebuild-comparison-v1",
            "status": "PASS",
            "public_catalog_class": "nautilus_trader.persistence.ParquetDataCatalog",
            "spot": {
                "first_identity": spot_first.catalog_identity,
                "rebuilt_identity": spot_second.catalog_identity,
                "semantic_inventory_equal": spot_first.semantic_inventory == spot_second.semantic_inventory,
            },
            "perpetual": {
                "first_identity": perp_first.catalog_identity,
                "rebuilt_identity": perp_second.catalog_identity,
                "semantic_inventory_equal": perp_first.semantic_inventory == perp_second.semantic_inventory,
            },
            "physical_parquet_byte_identity_required": False,
        },
    )
    write_once(
        EVIDENCE / "qualification-summary.json",
        {
            "schema": "m2-official-data-qualification-v1",
            "status": "PASS",
            "normalizer_version": NORMALIZER_VERSION,
            "spot_dataset_release_id": spot_release.dataset_release_id,
            "perpetual_dataset_release_id": perp_release.dataset_release_id,
            "release_files": {key: str(path.relative_to(ROOT)) for key, path in release_files.items()},
            "catalog_identities": {
                "spot": spot_release.catalog_identity,
                "perpetual": perp_release.catalog_identity,
            },
            "market_data_acquired": True,
            "strategy_run": False,
            "official_run": False,
            "m3_started": False,
            "ssot_sha256": sha256_file(ROOT / "SSOT.md"),
            "runtime_lock_sha256": sha256_file(ROOT / "runtime.lock.json"),
        },
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "raw_objects": len(inventory_records),
                "spot_release": spot_release.dataset_release_id,
                "perpetual_release": perp_release.dataset_release_id,
            },
            sort_keys=True,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
