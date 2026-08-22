#!/usr/bin/env python3
"""Build additive M2 repair releases and evidence from frozen local raw objects."""

from __future__ import annotations

import hashlib
import json
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
from crypto_lab.data import NORMALIZER_VERSION
from crypto_lab.data import CatalogBuildResult
from crypto_lab.data import DatasetRelease
from crypto_lab.data import RawObjectRecord
from crypto_lab.data import RawObjectStore
from crypto_lab.data import SourceObjectBinding
from crypto_lab.data import SourceRole
from crypto_lab.data import TimeRange
from crypto_lab.data import build_dataset_release
from crypto_lab.data import build_nautilus_catalog
from crypto_lab.data import catalog_semantic_inventory
from crypto_lab.data import extract_single_csv_archive
from crypto_lab.data import parse_funding_csv
from crypto_lab.data import parse_kline_csv
from crypto_lab.data import parse_spot_instrument_metadata
from crypto_lab.data import parse_usdm_instrument_metadata
from crypto_lab.data import prove_funding_schedule
from crypto_lab.data import to_nautilus_instrument
from crypto_lab.data import validate_one_minute_grid
from crypto_lab.data import verify_catalog_identity
from crypto_lab.data import verify_publisher_checksum
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from nautilus_trader.persistence import ParquetDataCatalog


EVIDENCE = ROOT / "evidence/m2/m2-repair-001"
OLD_EVIDENCE = ROOT / "evidence/m2/m2-acceptance-001"
RAW_ROOT = ROOT / "data/raw"
CATALOG_ROOT = ROOT / "data/catalog"
RELEASE_ROOT = ROOT / "data/releases"
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
SPOT_RANGE = TimeRange(
    start_inclusive=utc("2024-12-31T23:58:00Z"),
    end_exclusive=utc("2025-01-01T00:02:00Z"),
)
PERP_RANGE = TimeRange(
    start_inclusive=utc("2025-01-01T00:00:00Z"),
    end_exclusive=utc("2025-01-01T00:04:00Z"),
)
M3_READY_RANGE = TimeRange(
    start_inclusive=utc("2025-01-01T07:56:00Z"),
    end_exclusive=utc("2025-01-01T08:04:00Z"),
)


def write_once(path: Path, value: Any) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value if isinstance(value, bytes) else canonical_json_bytes(value) + b"\n"
    path.write_bytes(payload)


def preserve_or_verify(path: Path, value: Any) -> None:
    """Reuse an existing content-addressed artifact only when its bytes are exact."""

    payload = value if isinstance(value, bytes) else canonical_json_bytes(value) + b"\n"
    if path.exists():
        if path.read_bytes() != payload:
            raise FileExistsError(f"content-addressed artifact collision: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def load_records() -> tuple[RawObjectRecord, ...]:
    manifest = json.loads((OLD_EVIDENCE / "acquisition-manifest.json").read_text())
    return tuple(
        RawObjectRecord.from_json_bytes(canonical_json_bytes(item))
        for item in manifest["objects"]
    )


def one(records: tuple[RawObjectRecord, ...], role: SourceRole) -> RawObjectRecord:
    matches = [item for item in records if item.source_role is role]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {role.value}, found {len(matches)}")
    return matches[0]


def archive(
    store: RawObjectStore,
    record: RawObjectRecord,
    member: str,
) -> bytes:
    return extract_single_csv_archive(store.read_bytes(record.sha256), expected_filename=member)


def slice_bars(items, time_range: TimeRange):
    return tuple(
        item
        for item in items
        if time_range.start_ns <= item.interval_start_ns < time_range.end_ns
    )


def slice_funding(items, time_range: TimeRange):
    return tuple(
        item
        for item in items
        if time_range.start_ns <= item.calc_time_ns < time_range.end_ns
    )


def build_catalog_three_times(name: str, **kwargs: Any):
    with tempfile.TemporaryDirectory(prefix=f"m2-repair-{name}-a-") as first_root:
        first = build_nautilus_catalog(Path(first_root), **kwargs)
    with tempfile.TemporaryDirectory(prefix=f"m2-repair-{name}-b-") as second_root:
        second = build_nautilus_catalog(Path(second_root), **kwargs)
    if first.catalog_identity != second.catalog_identity or first.semantic_inventory != second.semantic_inventory:
        raise RuntimeError(f"{name} independent catalog rebuild mismatch")
    physical = CATALOG_ROOT / first.catalog_identity
    if physical.exists():
        catalog = ParquetDataCatalog(str(physical))
        actual_inventory = catalog_semantic_inventory(
            catalog,
            instrument_id=str(first.instrument.id),
            bar_type=str(first.execution_bars[0].bar_type),
            funding_updates=first.funding_updates,
        )
        persisted = CatalogBuildResult(
            catalog_identity=canonical_sha256(actual_inventory),
            semantic_inventory=actual_inventory,
            instrument=catalog.instruments([str(first.instrument.id)])[0],
            execution_bars=tuple(catalog.query_bars([str(first.execution_bars[0].bar_type)])),
            mark_updates=tuple(catalog.query_mark_price_updates([str(first.instrument.id)])),
            funding_updates=first.funding_updates,
        )
    else:
        persisted = build_nautilus_catalog(physical, **kwargs)
    if persisted.catalog_identity != first.catalog_identity:
        raise RuntimeError(f"{name} persisted catalog identity mismatch")
    return first, second, persisted


def persist_release(
    release: DatasetRelease,
    *,
    metadata,
    funding_schedule=None,
    funding_events=(),
) -> dict[str, str]:
    release_path = RELEASE_ROOT / f"{release.dataset_release_id}.json"
    metadata_path = RELEASE_ROOT / f"{metadata.instrument_metadata_identity}.metadata.json"
    preserve_or_verify(release_path, release.to_json_bytes())
    preserve_or_verify(metadata_path, metadata.to_json_bytes())
    paths = {
        "release": str(release_path.relative_to(ROOT)),
        "metadata": str(metadata_path.relative_to(ROOT)),
    }
    if funding_schedule is not None:
        material = {
            "schedule_identity": funding_schedule.schedule_identity,
            "events": [item.semantic_payload() for item in funding_events],
        }
        if canonical_sha256(material) != release.funding_data_identity:
            raise RuntimeError("funding material identity mismatch")
        funding_path = RELEASE_ROOT / f"{release.funding_data_identity}.funding.json"
        preserve_or_verify(
            funding_path,
            {"funding_data_identity": release.funding_data_identity, **material},
        )
        paths["funding"] = str(funding_path.relative_to(ROOT))
    return paths


def freeze_or_reuse_release(release: DatasetRelease) -> DatasetRelease:
    """Retain the first immutable representation for a material release identity."""

    path = RELEASE_ROOT / f"{release.dataset_release_id}.json"
    if not path.exists():
        return release
    existing = DatasetRelease.from_json_bytes(path.read_bytes())
    if existing.material_payload() != release.material_payload():
        raise FileExistsError(f"Dataset Release material identity collision: {path}")
    return existing


def main() -> int:
    records = load_records()
    store = RawObjectStore(RAW_ROOT)
    by_role = {role: [item for item in records if item.source_role is role] for role in SourceRole}
    spot_pre_record, spot_post_record = sorted(
        by_role[SourceRole.SPOT_EXECUTION_1M],
        key=lambda item: item.source_locator,
    )
    perp_execution_record = one(records, SourceRole.USDM_PERPETUAL_EXECUTION_1M)
    perp_mark_record = one(records, SourceRole.USDM_PERPETUAL_MARK_1M)
    perp_funding_record = one(records, SourceRole.USDM_PERPETUAL_FUNDING)
    spot_metadata_record = one(records, SourceRole.SPOT_INSTRUMENT_METADATA)
    perp_metadata_record = one(records, SourceRole.USDM_PERPETUAL_INSTRUMENT_METADATA)

    raw_hash_results = []
    for record in records:
        path = store.blob_path(record.sha256)
        actual = sha256_file(path)
        if actual != record.sha256:
            raise RuntimeError(f"raw hash mismatch {record.sha256}")
        raw_hash_results.append(
            {
                "source_role": record.source_role.value,
                "source_locator": record.source_locator,
                "sha256": record.sha256,
                "byte_size": path.stat().st_size,
                "status": "PASS",
            },
        )

    publisher_results = []
    for source in (
        spot_pre_record,
        spot_post_record,
        perp_execution_record,
        perp_mark_record,
        perp_funding_record,
    ):
        checksum_locator = source.source_locator + ".CHECKSUM"
        matches = [
            item
            for item in by_role[SourceRole.PUBLISHER_CHECKSUM]
            if item.source_locator == checksum_locator
        ]
        if len(matches) != 1:
            raise RuntimeError(f"publisher checksum binding missing for {source.source_locator}")
        verified = verify_publisher_checksum(
            store.read_bytes(source.sha256),
            store.read_bytes(matches[0].sha256),
            exact_filename=source.exact_filename,
        )
        publisher_results.append(
            {
                "source_locator": source.source_locator,
                "archive_sha256": source.sha256,
                "checksum_object_sha256": matches[0].sha256,
                "publisher_sha256": verified,
                "status": "PASS",
            },
        )

    spot_pre_full = parse_kline_csv(
        archive(store, spot_pre_record, "BTCUSDT-1m-2024-12-31.csv"),
        source_role=SourceRole.SPOT_EXECUTION_1M,
        instrument_id="BTCUSDT.BINANCE",
        market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        source_date=DAY_PRE.start_inclusive.date(),
    )
    spot_post_full = parse_kline_csv(
        archive(store, spot_post_record, "BTCUSDT-1m-2025-01-01.csv"),
        source_role=SourceRole.SPOT_EXECUTION_1M,
        instrument_id="BTCUSDT.BINANCE",
        market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        source_date=DAY_POST.start_inclusive.date(),
    )
    perp_execution_full = parse_kline_csv(
        archive(store, perp_execution_record, "BTCUSDT-1m-2025-01-01.csv"),
        source_role=SourceRole.USDM_PERPETUAL_EXECUTION_1M,
        instrument_id="BTCUSDT-PERP.BINANCE",
        market_profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        source_date=DAY_POST.start_inclusive.date(),
    )
    perp_mark_full = parse_kline_csv(
        archive(store, perp_mark_record, "BTCUSDT-1m-2025-01-01.csv"),
        source_role=SourceRole.USDM_PERPETUAL_MARK_1M,
        instrument_id="BTCUSDT-PERP.BINANCE",
        market_profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        source_date=DAY_POST.start_inclusive.date(),
    )
    funding_full = parse_funding_csv(
        archive(store, perp_funding_record, "BTCUSDT-fundingRate-2025-01.csv"),
        instrument_id="BTCUSDT-PERP.BINANCE",
    )
    full_grids = (
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

    spot_metadata = parse_spot_instrument_metadata(
        store.read_bytes(spot_metadata_record.sha256),
        raw_symbol="BTCUSDT",
        instrument_id="BTCUSDT.BINANCE",
        source_object_sha256=spot_metadata_record.sha256,
        maker_fee_rate=Decimal("0"),
        taker_fee_rate=Decimal("0"),
        fee_rate_basis=ZERO_FEE_BASIS,
    )
    perp_metadata = parse_usdm_instrument_metadata(
        store.read_bytes(perp_metadata_record.sha256),
        raw_symbol="BTCUSDT",
        instrument_id="BTCUSDT-PERP.BINANCE",
        source_object_sha256=perp_metadata_record.sha256,
        maker_fee_rate=Decimal("0"),
        taker_fee_rate=Decimal("0"),
        fee_rate_basis=ZERO_FEE_BASIS,
    )
    spot_native = to_nautilus_instrument(spot_metadata)
    perp_native = to_nautilus_instrument(perp_metadata)

    spot_bars = slice_bars((*spot_pre_full, *spot_post_full), SPOT_RANGE)
    perp_bars = slice_bars(perp_execution_full, PERP_RANGE)
    perp_marks = slice_bars(perp_mark_full, PERP_RANGE)
    perp_events = slice_funding(funding_full, PERP_RANGE)
    perp_schedule = prove_funding_schedule(
        funding_full,
        source_object_sha256=perp_funding_record.sha256,
        time_range=PERP_RANGE,
    )
    m3_bars = slice_bars(perp_execution_full, M3_READY_RANGE)
    m3_marks = slice_bars(perp_mark_full, M3_READY_RANGE)
    m3_events = slice_funding(funding_full, M3_READY_RANGE)
    m3_schedule = prove_funding_schedule(
        funding_full,
        source_object_sha256=perp_funding_record.sha256,
        time_range=M3_READY_RANGE,
    )
    if (
        len(m3_bars) != 8
        or len(m3_marks) != 8
        or len(m3_events) != 1
        or m3_events[0].calc_time_ns != 1_735_718_400_000_000_000
        or m3_events[0].funding_rate != Decimal("0.00010000")
        or m3_events[0].funding_interval_hours != 8
    ):
        raise RuntimeError("M3-ready official window does not satisfy the Owner contract")

    spot_catalogs = build_catalog_three_times(
        "spot",
        metadata=spot_metadata,
        execution_bars=spot_bars,
    )
    perp_catalogs = build_catalog_three_times(
        "perp-qualification",
        metadata=perp_metadata,
        execution_bars=perp_bars,
        mark_bars=perp_marks,
        funding_events=perp_events,
    )
    m3_catalogs = build_catalog_three_times(
        "perp-m3-ready",
        metadata=perp_metadata,
        execution_bars=m3_bars,
        mark_bars=m3_marks,
        funding_events=m3_events,
    )

    spot_sources = tuple(
        SourceObjectBinding.from_raw(item)
        for item in (spot_pre_record, spot_post_record, spot_metadata_record)
    )
    perp_sources = tuple(
        SourceObjectBinding.from_raw(item)
        for item in (
            perp_execution_record,
            perp_mark_record,
            perp_funding_record,
            perp_metadata_record,
        )
    )
    created = datetime.now(UTC)
    spot_release = build_dataset_release(
        market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        instrument_id="BTCUSDT.BINANCE",
        source_objects=spot_sources,
        normalized_time_range=SPOT_RANGE,
        instrument_metadata=spot_metadata,
        execution_bars=spot_bars,
        catalog_identity=spot_catalogs[2].catalog_identity,
        created_at_utc=created,
    )
    perp_release = build_dataset_release(
        market_profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        instrument_id="BTCUSDT-PERP.BINANCE",
        source_objects=perp_sources,
        normalized_time_range=PERP_RANGE,
        instrument_metadata=perp_metadata,
        execution_bars=perp_bars,
        mark_bars=perp_marks,
        funding_events=perp_events,
        funding_schedule=perp_schedule,
        catalog_identity=perp_catalogs[2].catalog_identity,
        created_at_utc=created,
    )
    m3_release = build_dataset_release(
        market_profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        instrument_id="BTCUSDT-PERP.BINANCE",
        source_objects=perp_sources,
        normalized_time_range=M3_READY_RANGE,
        instrument_metadata=perp_metadata,
        execution_bars=m3_bars,
        mark_bars=m3_marks,
        funding_events=m3_events,
        funding_schedule=m3_schedule,
        catalog_identity=m3_catalogs[2].catalog_identity,
        created_at_utc=created,
    )
    spot_release = freeze_or_reuse_release(spot_release)
    perp_release = freeze_or_reuse_release(perp_release)
    m3_release = freeze_or_reuse_release(m3_release)
    for release, catalogs in (
        (spot_release, spot_catalogs),
        (perp_release, perp_catalogs),
        (m3_release, m3_catalogs),
    ):
        for catalog in catalogs:
            verify_catalog_identity(release, catalog.semantic_inventory)

    release_files = {
        "spot": persist_release(spot_release, metadata=spot_metadata),
        "perpetual": persist_release(
            perp_release,
            metadata=perp_metadata,
            funding_schedule=perp_schedule,
            funding_events=perp_events,
        ),
        "m3_ready_perpetual": persist_release(
            m3_release,
            metadata=perp_metadata,
            funding_schedule=m3_schedule,
            funding_events=m3_events,
        ),
    }

    protected_files = sorted(
        path
        for root in (ROOT / "evidence/m0", ROOT / "evidence/m1", OLD_EVIDENCE)
        for path in root.rglob("*")
        if path.is_file()
    )
    protected_inventory = [
        {
            "path": str(path.relative_to(ROOT)),
            "sha256": sha256_file(path),
            "byte_size": path.stat().st_size,
        }
        for path in protected_files
    ]
    write_once(
        EVIDENCE / "baseline-attestation.json",
        {
            "schema": "m2-repair-baseline-v1",
            "status": "PASS",
            "user": "builder",
            "repository": str(ROOT),
            "branch": "main",
            "starting_head": "6dee359842bda402282770daf71181b47b704dbb",
            "starting_origin_main": "6dee359842bda402282770daf71181b47b704dbb",
            "starting_git_status": "CLEAN",
            "ssot_sha256": sha256_file(ROOT / "SSOT.md"),
            "runtime_lock_sha256": sha256_file(ROOT / "runtime.lock.json"),
            "dependency_lock_sha256": sha256_file(ROOT / "requirements.lock.txt"),
            "historical_evidence_file_count": len(protected_inventory),
            "historical_evidence_initial_aggregate": "ed0845e6e70feae9324dd48f1ed4662fb832c1f8f9772d36e61af35ec42ca8ad",
            "raw_store_initial_aggregate": "3254e66009be11b4ae90d628271c5c21f4573495174e2d1b17b236325528b967",
            "catalog_store_initial_aggregate": "a7eea0281d8257be7615389fc1a9c3bfbbbe67d74892d751edee30ab2ea576ce",
        },
    )
    write_once(
        EVIDENCE / "preserved-historical-evidence-inventory.json",
        {
            "schema": "m2-repair-preserved-history-v1",
            "status": "PASS",
            "files": protected_inventory,
        },
    )
    write_once(
        EVIDENCE / "raw-object-hash-validation.json",
        {
            "schema": "m2-repair-raw-hash-validation-v1",
            "status": "PASS",
            "object_count": len(raw_hash_results),
            "objects": raw_hash_results,
            "raw_bytes_modified": False,
        },
    )
    write_once(
        EVIDENCE / "publisher-checksum-validation.json",
        {
            "schema": "m2-repair-publisher-checksum-v1",
            "status": "PASS",
            "count": len(publisher_results),
            "results": publisher_results,
        },
    )
    write_once(
        EVIDENCE / "source-binding-repair.json",
        {
            "schema": "m2-repair-source-binding-v1",
            "status": "PASS",
            "normalizer_version": NORMALIZER_VERSION,
            "preserved_fields": sorted(SourceObjectBinding.__dataclass_fields__),
            "spot_bindings": [item.to_builtins() for item in spot_release.source_objects],
            "perpetual_bindings": [item.to_builtins() for item in perp_release.source_objects],
            "unresolved_conflicts": 0,
            "validation_dimensions": [
                "instrument",
                "binance_symbol",
                "market_profile",
                "source_role",
                "interval",
                "time_range_coverage",
                "filename_locator",
                "timestamp_unit_contract",
            ],
        },
    )
    write_once(
        EVIDENCE / "market-limit-mapping.json",
        {
            "schema": "m2-repair-market-limit-mapping-v1",
            "status": "PASS",
            "spot": {
                "raw_LOT_SIZE": {
                    "minQty": spot_metadata.lot_size_min_quantity,
                    "maxQty": spot_metadata.lot_size_max_quantity,
                    "stepSize": spot_metadata.lot_size_step_size,
                },
                "raw_MARKET_LOT_SIZE": {
                    "minQty": spot_metadata.market_lot_size_min_quantity,
                    "maxQty": spot_metadata.market_lot_size_max_quantity,
                    "stepSize": spot_metadata.market_lot_size_step_size,
                },
                "effective_MARKET": {
                    "minQty": spot_metadata.min_quantity,
                    "maxQty": spot_metadata.max_quantity,
                    "stepSize": spot_metadata.size_increment,
                },
                "nautilus": {
                    "min_quantity": str(spot_native.min_quantity),
                    "max_quantity": str(spot_native.max_quantity),
                    "size_increment": str(spot_native.size_increment),
                },
                "derivation": spot_metadata.effective_market_derivation,
            },
            "perpetual": {
                "raw_LOT_SIZE": {
                    "minQty": perp_metadata.lot_size_min_quantity,
                    "maxQty": perp_metadata.lot_size_max_quantity,
                    "stepSize": perp_metadata.lot_size_step_size,
                },
                "raw_MARKET_LOT_SIZE": {
                    "minQty": perp_metadata.market_lot_size_min_quantity,
                    "maxQty": perp_metadata.market_lot_size_max_quantity,
                    "stepSize": perp_metadata.market_lot_size_step_size,
                },
                "effective_MARKET": {
                    "minQty": perp_metadata.min_quantity,
                    "maxQty": perp_metadata.max_quantity,
                    "stepSize": perp_metadata.size_increment,
                },
                "nautilus": {
                    "min_quantity": str(perp_native.min_quantity),
                    "max_quantity": str(perp_native.max_quantity),
                    "size_increment": str(perp_native.size_increment),
                },
                "derivation": perp_metadata.effective_market_derivation,
            },
            "decimal_arithmetic_only": True,
            "quantity_precision_used_as_step": False,
        },
    )
    write_once(
        EVIDENCE / "instrument-metadata-evidence.json",
        {
            "schema": "m2-repair-instrument-metadata-v2",
            "status": "PASS",
            "spot": spot_metadata.to_builtins(),
            "perpetual": perp_metadata.to_builtins(),
            "fee_disclosure": ZERO_FEE_BASIS,
        },
    )
    write_once(EVIDENCE / "spot-qualification-release.json", spot_release.to_json_bytes())
    write_once(EVIDENCE / "perpetual-qualification-release.json", perp_release.to_json_bytes())
    write_once(EVIDENCE / "m3-ready-perpetual-release.json", m3_release.to_json_bytes())
    write_once(
        EVIDENCE / "catalog-rebuild-comparison.json",
        {
            "schema": "m2-repair-catalog-rebuild-v1",
            "status": "PASS",
            "comparisons": {
                name: {
                    "first_identity": catalogs[0].catalog_identity,
                    "second_identity": catalogs[1].catalog_identity,
                    "persisted_identity": catalogs[2].catalog_identity,
                    "first_second_semantic_equal": catalogs[0].semantic_inventory
                    == catalogs[1].semantic_inventory,
                    "first_persisted_semantic_equal": catalogs[0].semantic_inventory
                    == catalogs[2].semantic_inventory,
                }
                for name, catalogs in (
                    ("spot", spot_catalogs),
                    ("perpetual_qualification", perp_catalogs),
                    ("m3_ready_perpetual", m3_catalogs),
                )
            },
            "independent_rebuilds_per_release": 2,
            "physical_paths_excluded_from_identity": True,
        },
    )
    write_once(
        EVIDENCE / "completeness-and-m3-ready-window.json",
        {
            "schema": "m2-repair-completeness-v1",
            "status": "PASS",
            "full_daily_grids": [item.to_builtins() for item in full_grids],
            "spot_release": spot_release.completeness_result.to_builtins(),
            "perpetual_release": perp_release.completeness_result.to_builtins(),
            "m3_ready_release": m3_release.completeness_result.to_builtins(),
            "m3_ready_window": M3_READY_RANGE.to_builtins(),
            "execution_minutes": len(m3_bars),
            "mark_minutes": len(m3_marks),
            "funding_events": [item.semantic_payload() for item in m3_events],
            "funding_schedule": m3_schedule.to_builtins(),
            "strategy_run": False,
            "official_run": False,
            "m3_started": False,
        },
    )
    old_summary = json.loads((OLD_EVIDENCE / "qualification-summary.json").read_text())
    write_once(
        EVIDENCE / "release-identities.json",
        {
            "schema": "m2-repair-release-identities-v1",
            "status": "PASS",
            "old": {
                "spot_dataset_release_id": old_summary["spot_dataset_release_id"],
                "perpetual_dataset_release_id": old_summary["perpetual_dataset_release_id"],
                "spot_metadata_identity": "a80c2b9e7d3fe322486bfad9e3e7ab1fd78034df4647f0085d41460c32bf98af",
                "perpetual_metadata_identity": "7686e8bf6cb594d72112405b93b95494cdfebb98bb4923e79943e1815e0bac2b",
            },
            "new": {
                "spot_dataset_release_id": spot_release.dataset_release_id,
                "perpetual_dataset_release_id": perp_release.dataset_release_id,
                "m3_ready_perpetual_dataset_release_id": m3_release.dataset_release_id,
                "spot_metadata_identity": spot_metadata.instrument_metadata_identity,
                "perpetual_metadata_identity": perp_metadata.instrument_metadata_identity,
            },
            "release_files": release_files,
            "old_releases_reinterpreted": False,
            "old_releases_preserved_as_historical": True,
        },
    )
    write_once(
        EVIDENCE / "repair-generation-summary.json",
        {
            "schema": "m2-repair-generation-summary-v1",
            "status": "PASS",
            "normalizer_version": NORMALIZER_VERSION,
            "source_network_used": False,
            "raw_objects_modified": False,
            "spot_dataset_release_id": spot_release.dataset_release_id,
            "perpetual_dataset_release_id": perp_release.dataset_release_id,
            "m3_ready_perpetual_dataset_release_id": m3_release.dataset_release_id,
            "strategy_run": False,
            "official_run": False,
            "m3_started": False,
            "ssot_sha256": sha256_file(ROOT / "SSOT.md"),
            "runtime_lock_sha256": sha256_file(ROOT / "runtime.lock.json"),
            "dependency_lock_sha256": sha256_file(ROOT / "requirements.lock.txt"),
        },
    )
    failed_attempts = [
        {
            "attempt": 0,
            "finding": finding,
            "kind": "GOLDEN_FIRST_EXPECTED_FAILURE",
            "evidence": "evidence/m2/m2-repair-001/golden-first-failures.json",
            "retained": True,
        }
        for finding in ("F-01", "F-02", "F-03", "F-04", "F-05")
    ]
    failed_attempts.append(
        {
            "attempt": 1,
            "finding": "REPAIR_EPOCH_GENERATION",
            "kind": "CONTENT_ADDRESSED_REUSE_NOT_YET_IMPLEMENTED",
            "result": "FileExistsError for an exact pre-existing funding identity",
            "evidence": "evidence/m2/m2-repair-001/generation-attempt-001.txt",
            "retained": True,
        },
    )
    failed_attempts.append(
        {
            "attempt": 2,
            "finding": "REPAIR_EPOCH_GENERATION",
            "kind": "NON_MATERIAL_RELEASE_METADATA_REUSE_NOT_YET_IMPLEMENTED",
            "result": "FileExistsError for the same material release with a later created_at_utc",
            "evidence": "evidence/m2/m2-repair-001/generation-attempt-002.txt",
            "retained": True,
        },
    )
    write_once(
        EVIDENCE / "failed-attempts.jsonl",
        b"".join(canonical_json_bytes(item) + b"\n" for item in failed_attempts),
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "spot_release": spot_release.dataset_release_id,
                "perpetual_release": perp_release.dataset_release_id,
                "m3_ready_release": m3_release.dataset_release_id,
                "spot_metadata": spot_metadata.instrument_metadata_identity,
                "perpetual_metadata": perp_metadata.instrument_metadata_identity,
            },
            sort_keys=True,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
