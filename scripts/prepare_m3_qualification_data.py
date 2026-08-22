#!/usr/bin/env python3
"""Build additive M3 fee-qualified releases from the frozen M2 raw objects.

The script only uses the public M2 data boundary.  It never acquires data and
never mutates an existing content-addressed release, metadata object, catalog,
or raw object.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from crypto_lab.config import MarketProfile
from crypto_lab.data import FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY
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
from crypto_lab.data import verify_catalog_identity
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from nautilus_trader.persistence import ParquetDataCatalog


RAW_ROOT = ROOT / "data/raw"
CATALOG_ROOT = ROOT / "data/catalog"
RELEASE_ROOT = ROOT / "data/releases"
ACQUISITION_MANIFEST = ROOT / "evidence/m2/m2-acceptance-001/acquisition-manifest.json"
BINDINGS_PATH = ROOT / "configs/m3/release-bindings.json"
FEE_RATE = Decimal("0.001")
FEE_BASIS = "SSOT_APPENDIX_A_M3_QUALIFICATION_ESTIMATED_FEE"
CREATED_AT = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


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
PERPETUAL_RANGE = TimeRange(
    start_inclusive=utc("2025-01-01T07:56:00Z"),
    end_exclusive=utc("2025-01-01T08:04:00Z"),
)


def preserve_exact(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise FileExistsError(f"content-addressed collision at {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def load_records() -> tuple[RawObjectRecord, ...]:
    raw = json.loads(ACQUISITION_MANIFEST.read_text(encoding="utf-8"))
    return tuple(
        RawObjectRecord.from_json_bytes(canonical_json_bytes(item))
        for item in raw["objects"]
    )


def one(records: tuple[RawObjectRecord, ...], role: SourceRole) -> RawObjectRecord:
    matches = [record for record in records if record.source_role is role]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {role.value}, received {len(matches)}")
    return matches[0]


def archive(store: RawObjectStore, record: RawObjectRecord, member: str) -> bytes:
    return extract_single_csv_archive(
        store.read_bytes(record.sha256),
        expected_filename=member,
    )


def sliced(items: tuple[Any, ...], time_range: TimeRange, time_field: str) -> tuple[Any, ...]:
    return tuple(
        item
        for item in items
        if time_range.start_ns <= getattr(item, time_field) < time_range.end_ns
    )


def build_catalog_twice(name: str, **kwargs: Any) -> CatalogBuildResult:
    with tempfile.TemporaryDirectory(prefix=f"m3-{name}-a-") as first_dir:
        first = build_nautilus_catalog(Path(first_dir), **kwargs)
    with tempfile.TemporaryDirectory(prefix=f"m3-{name}-b-") as second_dir:
        second = build_nautilus_catalog(Path(second_dir), **kwargs)
    if first.semantic_inventory != second.semantic_inventory:
        raise RuntimeError(f"{name} catalog semantic replay diverged")
    if first.catalog_identity != second.catalog_identity:
        raise RuntimeError(f"{name} catalog identity replay diverged")

    target = CATALOG_ROOT / first.catalog_identity
    if not target.exists():
        persisted = build_nautilus_catalog(target, **kwargs)
    else:
        catalog = ParquetDataCatalog(str(target))
        inventory = catalog_semantic_inventory(
            catalog,
            instrument_id=str(first.instrument.id),
            bar_type=str(first.execution_bars[0].bar_type),
            funding_updates=first.funding_updates,
        )
        persisted = CatalogBuildResult(
            catalog_identity=canonical_sha256(inventory),
            semantic_inventory=inventory,
            instrument=catalog.instruments([str(first.instrument.id)])[0],
            execution_bars=tuple(catalog.query_bars([str(first.execution_bars[0].bar_type)])),
            mark_updates=tuple(catalog.query_mark_price_updates([str(first.instrument.id)])),
            funding_updates=first.funding_updates,
        )
    if persisted.catalog_identity != first.catalog_identity:
        raise RuntimeError(f"{name} persisted catalog identity mismatch")
    return persisted


def persist_release(
    release: DatasetRelease,
    metadata: Any,
    *,
    funding_schedule: Any | None = None,
    funding_events: tuple[Any, ...] = (),
    native_binding: str | None = None,
) -> None:
    preserve_exact(
        RELEASE_ROOT / f"{metadata.instrument_metadata_identity}.metadata.json",
        metadata.to_json_bytes() + b"\n",
    )
    preserve_exact(
        RELEASE_ROOT / f"{release.dataset_release_id}.json",
        release.to_json_bytes() + b"\n",
    )
    if funding_schedule is not None:
        material = {
            "schedule_identity": funding_schedule.schedule_identity,
            "events": [event.semantic_payload() for event in funding_events],
        }
        if native_binding is not None:
            material["native_binding"] = native_binding
        if canonical_sha256(material) != release.funding_data_identity:
            raise RuntimeError("funding manifest material identity mismatch")
        preserve_exact(
            RELEASE_ROOT / f"{release.funding_data_identity}.funding.json",
            canonical_json_bytes(
                {"funding_data_identity": release.funding_data_identity, **material},
            )
            + b"\n",
        )


def main() -> int:
    records = load_records()
    store = RawObjectStore(RAW_ROOT)
    by_role = {role: tuple(r for r in records if r.source_role is role) for role in SourceRole}
    spot_pre, spot_post = sorted(
        by_role[SourceRole.SPOT_EXECUTION_1M],
        key=lambda record: record.source_locator,
    )
    spot_meta_record = one(records, SourceRole.SPOT_INSTRUMENT_METADATA)
    perp_exec_record = one(records, SourceRole.USDM_PERPETUAL_EXECUTION_1M)
    perp_mark_record = one(records, SourceRole.USDM_PERPETUAL_MARK_1M)
    perp_funding_record = one(records, SourceRole.USDM_PERPETUAL_FUNDING)
    perp_meta_record = one(records, SourceRole.USDM_PERPETUAL_INSTRUMENT_METADATA)

    spot_pre_rows = parse_kline_csv(
        archive(store, spot_pre, "BTCUSDT-1m-2024-12-31.csv"),
        source_role=SourceRole.SPOT_EXECUTION_1M,
        instrument_id="BTCUSDT.BINANCE",
        market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        source_date=DAY_PRE.start_inclusive.date(),
    )
    spot_post_rows = parse_kline_csv(
        archive(store, spot_post, "BTCUSDT-1m-2025-01-01.csv"),
        source_role=SourceRole.SPOT_EXECUTION_1M,
        instrument_id="BTCUSDT.BINANCE",
        market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        source_date=DAY_POST.start_inclusive.date(),
    )
    perp_exec_rows = parse_kline_csv(
        archive(store, perp_exec_record, "BTCUSDT-1m-2025-01-01.csv"),
        source_role=SourceRole.USDM_PERPETUAL_EXECUTION_1M,
        instrument_id="BTCUSDT-PERP.BINANCE",
        market_profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        source_date=DAY_POST.start_inclusive.date(),
    )
    perp_mark_rows = parse_kline_csv(
        archive(store, perp_mark_record, "BTCUSDT-1m-2025-01-01.csv"),
        source_role=SourceRole.USDM_PERPETUAL_MARK_1M,
        instrument_id="BTCUSDT-PERP.BINANCE",
        market_profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        source_date=DAY_POST.start_inclusive.date(),
    )
    funding_rows = parse_funding_csv(
        archive(store, perp_funding_record, "BTCUSDT-fundingRate-2025-01.csv"),
        instrument_id="BTCUSDT-PERP.BINANCE",
    )

    spot_bars = sliced((*spot_pre_rows, *spot_post_rows), SPOT_RANGE, "interval_start_ns")
    perp_bars = sliced(perp_exec_rows, PERPETUAL_RANGE, "interval_start_ns")
    perp_marks = sliced(perp_mark_rows, PERPETUAL_RANGE, "interval_start_ns")
    perp_funding = sliced(funding_rows, PERPETUAL_RANGE, "calc_time_ns")
    if len(spot_bars) != 4 or len(perp_bars) != 8 or len(perp_marks) != 8:
        raise RuntimeError("frozen M3 window cardinality mismatch")
    if (
        len(perp_funding) != 1
        or perp_funding[0].calc_time_ns != 1_735_718_400_000_000_000
        or perp_funding[0].funding_rate != Decimal("0.00010000")
        or perp_funding[0].funding_interval_hours != 8
    ):
        raise RuntimeError("frozen M3 funding event mismatch")

    spot_metadata = parse_spot_instrument_metadata(
        store.read_bytes(spot_meta_record.sha256),
        raw_symbol="BTCUSDT",
        instrument_id="BTCUSDT.BINANCE",
        source_object_sha256=spot_meta_record.sha256,
        maker_fee_rate=FEE_RATE,
        taker_fee_rate=FEE_RATE,
        fee_rate_basis=FEE_BASIS,
    )
    perp_metadata = parse_usdm_instrument_metadata(
        store.read_bytes(perp_meta_record.sha256),
        raw_symbol="BTCUSDT",
        instrument_id="BTCUSDT-PERP.BINANCE",
        source_object_sha256=perp_meta_record.sha256,
        maker_fee_rate=FEE_RATE,
        taker_fee_rate=FEE_RATE,
        fee_rate_basis=FEE_BASIS,
    )
    schedule = prove_funding_schedule(
        funding_rows,
        source_object_sha256=perp_funding_record.sha256,
        time_range=PERPETUAL_RANGE,
    )

    spot_catalog = build_catalog_twice(
        "spot",
        metadata=spot_metadata,
        execution_bars=spot_bars,
    )
    perp_catalog = build_catalog_twice(
        "perpetual",
        metadata=perp_metadata,
        execution_bars=perp_bars,
        mark_bars=perp_marks,
        funding_events=perp_funding,
        funding_native_binding=FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY,
    )
    spot_release = build_dataset_release(
        market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        instrument_id="BTCUSDT.BINANCE",
        source_objects=tuple(
            SourceObjectBinding.from_raw(record)
            for record in (spot_pre, spot_post, spot_meta_record)
        ),
        normalized_time_range=SPOT_RANGE,
        instrument_metadata=spot_metadata,
        execution_bars=spot_bars,
        catalog_identity=spot_catalog.catalog_identity,
        created_at_utc=CREATED_AT,
    )
    perp_release = build_dataset_release(
        market_profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        instrument_id="BTCUSDT-PERP.BINANCE",
        source_objects=tuple(
            SourceObjectBinding.from_raw(record)
            for record in (
                perp_exec_record,
                perp_mark_record,
                perp_funding_record,
                perp_meta_record,
            )
        ),
        normalized_time_range=PERPETUAL_RANGE,
        instrument_metadata=perp_metadata,
        execution_bars=perp_bars,
        mark_bars=perp_marks,
        funding_events=perp_funding,
        funding_schedule=schedule,
        funding_native_binding=FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY,
        catalog_identity=perp_catalog.catalog_identity,
        created_at_utc=CREATED_AT,
    )
    verify_catalog_identity(spot_release, spot_catalog.semantic_inventory)
    verify_catalog_identity(perp_release, perp_catalog.semantic_inventory)
    persist_release(spot_release, spot_metadata)
    persist_release(
        perp_release,
        perp_metadata,
        funding_schedule=schedule,
        funding_events=perp_funding,
        native_binding=FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY,
    )

    bindings = {
        "schema": "m3-qualification-release-bindings-v1",
        "fee_assumption": {
            "maker_fee": FEE_RATE,
            "taker_fee": FEE_RATE,
            "claim_class": "ESTIMATED_FEE",
            "basis": FEE_BASIS,
        },
        "spot": {
            "base_dataset_release_id": "2e0bdefe2b664821c559e95d35a3462c8354606076e1ec81d0ce6272f89b9a44",
            "dataset_release_id": spot_release.dataset_release_id,
            "instrument_metadata_identity": spot_metadata.instrument_metadata_identity,
            "catalog_identity": spot_catalog.catalog_identity,
        },
        "perpetual": {
            "base_dataset_release_id": "749e654402021fafafe4a3269005c5ef1253c3743f04c35622726bca957a356b",
            "dataset_release_id": perp_release.dataset_release_id,
            "instrument_metadata_identity": perp_metadata.instrument_metadata_identity,
            "catalog_identity": perp_catalog.catalog_identity,
            "funding_data_identity": perp_release.funding_data_identity,
            "official_source_event_count": 1,
            "native_runtime_update_count": 2,
            "native_binding": FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY,
        },
        "raw_objects_changed": False,
        "network_used": False,
        "official_run": False,
        "research_run": False,
    }
    payload = canonical_json_bytes(bindings) + b"\n"
    if BINDINGS_PATH.exists() and BINDINGS_PATH.read_bytes() != payload:
        raise FileExistsError(f"refusing to overwrite different {BINDINGS_PATH}")
    BINDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    BINDINGS_PATH.write_bytes(payload)
    print(json.dumps(bindings, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
