from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from crypto_lab.config import ConfigError
from crypto_lab.config import MarketProfile
from crypto_lab.data import DataContractError
from crypto_lab.data import DatasetRelease
from crypto_lab.data import SourceRole
from crypto_lab.data import build_dataset_release
from crypto_lab.data import build_nautilus_catalog
from crypto_lab.data import parse_funding_csv
from crypto_lab.data import parse_spot_instrument_metadata
from crypto_lab.data import parse_usdm_instrument_metadata
from crypto_lab.data import prove_funding_schedule
from crypto_lab.data import validate_funding_schedule
from crypto_lab.data import verify_catalog_identity
from crypto_lab.hashing import canonical_sha256
from crypto_lab.status import FailureCode
from tests.m2_helpers import FIXTURES
from tests.m2_helpers import ZERO_FEE_BASIS
from tests.m2_helpers import binding
from tests.m2_helpers import perp_bindings
from tests.m2_helpers import perp_execution_bars
from tests.m2_helpers import perp_mark_bars
from tests.m2_helpers import perp_metadata
from tests.m2_helpers import perp_metadata_payload
from tests.m2_helpers import perp_range
from tests.m2_helpers import spot_bars
from tests.m2_helpers import spot_bindings
from tests.m2_helpers import spot_metadata
from tests.m2_helpers import spot_metadata_payload
from tests.m2_helpers import spot_range


CREATED = datetime(2026, 8, 22, 3, 0, tzinfo=UTC)


def funding_events():
    return parse_funding_csv(
        FIXTURES.joinpath("usdm-funding.csv").read_bytes(),
        instrument_id="BTCUSDT-PERP.BINANCE",
    )


def build_spot(root: Path, *, created: datetime = CREATED) -> DatasetRelease:
    metadata = spot_metadata()
    bars = spot_bars()
    catalog = build_nautilus_catalog(
        root,
        metadata=metadata,
        execution_bars=bars,
    )
    return build_dataset_release(
        market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        instrument_id="BTCUSDT.BINANCE",
        source_objects=spot_bindings(),
        normalized_time_range=spot_range(),
        instrument_metadata=metadata,
        execution_bars=bars,
        catalog_identity=catalog.catalog_identity,
        created_at_utc=created,
    )


def build_perp(root: Path, *, created: datetime = CREATED) -> DatasetRelease:
    metadata = perp_metadata()
    execution = perp_execution_bars()
    marks = perp_mark_bars()
    events = funding_events()
    schedule = prove_funding_schedule(
        events,
        source_object_sha256="7" * 64,
        time_range=perp_range(),
    )
    catalog = build_nautilus_catalog(
        root,
        metadata=metadata,
        execution_bars=execution,
        mark_bars=marks,
        funding_events=events,
    )
    return build_dataset_release(
        market_profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        instrument_id="BTCUSDT-PERP.BINANCE",
        source_objects=perp_bindings(),
        normalized_time_range=perp_range(),
        instrument_metadata=metadata,
        execution_bars=execution,
        mark_bars=marks,
        funding_events=events,
        funding_schedule=schedule,
        catalog_identity=catalog.catalog_identity,
        created_at_utc=created,
    )


class FundingContractTests(unittest.TestCase):
    def test_schedule_uses_official_explicit_interval_and_validates(self) -> None:
        events = funding_events()
        schedule = prove_funding_schedule(
            events,
            source_object_sha256="7" * 64,
            time_range=perp_range(),
        )
        self.assertEqual(schedule.proof_basis, "OFFICIAL_BINANCE_FUNDING_ARCHIVE_EXPLICIT_INTERVAL_ROWS")
        self.assertEqual(schedule.expected_events[0].funding_interval_hours, 8)
        self.assertEqual(schedule.expected_events[0].calc_time_ns, 1_735_689_600_015_000_000)
        self.assertEqual(len(validate_funding_schedule(events, schedule)), 64)

    def test_removed_required_event_is_funding_missing(self) -> None:
        events = funding_events()
        schedule = prove_funding_schedule(
            events,
            source_object_sha256="7" * 64,
            time_range=perp_range(),
        )
        with self.assertRaises(DataContractError) as raised:
            validate_funding_schedule(events[1:], schedule)
        self.assertEqual(raised.exception.code, FailureCode.FUNDING_MISSING.value)

    def test_unproven_schedule_is_funding_ambiguous(self) -> None:
        with self.assertRaises(DataContractError) as raised:
            validate_funding_schedule(funding_events(), None)
        self.assertEqual(raised.exception.code, FailureCode.FUNDING_AMBIGUOUS.value)

    def test_conflicting_funding_duplicate_blocks(self) -> None:
        lines = FIXTURES.joinpath("usdm-funding.csv").read_text().splitlines()
        duplicate = lines[1].split(",")
        duplicate[2] = "0.00020000"
        payload = ("\n".join([*lines[:2], ",".join(duplicate)]) + "\n").encode()
        with self.assertRaises(DataContractError) as raised:
            parse_funding_csv(payload, instrument_id="BTCUSDT-PERP.BINANCE")
        self.assertEqual(raised.exception.code, FailureCode.FUNDING_AMBIGUOUS.value)


class MetadataTests(unittest.TestCase):
    def test_metadata_identity_observation_and_current_not_historical(self) -> None:
        metadata = spot_metadata()
        self.assertEqual(metadata.price_precision, 2)
        self.assertEqual(metadata.size_precision, 5)
        self.assertFalse(metadata.historical_exact)
        self.assertIn("EXACT_HISTORICAL_VENUE_RULES_UNAVAILABLE", metadata.limitations)
        self.assertEqual(metadata.observed_at_utc.tzinfo, UTC)
        self.assertEqual(len(metadata.instrument_metadata_identity), 64)
        self.assertEqual(metadata.maker_fee_rate, Decimal("0"))
        self.assertEqual(metadata.fee_rate_basis, ZERO_FEE_BASIS)

    def test_material_metadata_change_changes_identity_but_observation_time_does_not(self) -> None:
        first = parse_spot_instrument_metadata(
            spot_metadata_payload(1_700_000_000_000),
            source_object_sha256="1" * 64,
            maker_fee_rate=Decimal("0"),
            taker_fee_rate=Decimal("0"),
            fee_rate_basis=ZERO_FEE_BASIS,
        )
        second = parse_spot_instrument_metadata(
            spot_metadata_payload(1_800_000_000_000),
            source_object_sha256="1" * 64,
            maker_fee_rate=Decimal("0"),
            taker_fee_rate=Decimal("0"),
            fee_rate_basis=ZERO_FEE_BASIS,
        )
        self.assertEqual(first.instrument_metadata_identity, second.instrument_metadata_identity)
        changed = parse_spot_instrument_metadata(
            spot_metadata_payload(1_700_000_000_000),
            source_object_sha256="1" * 64,
            maker_fee_rate=Decimal("0.001"),
            taker_fee_rate=Decimal("0.001"),
            fee_rate_basis="EXPLICIT_TEST_ASSUMPTION",
        )
        self.assertNotEqual(first.instrument_metadata_identity, changed.instrument_metadata_identity)

    def test_invalid_or_incomplete_metadata_blocks(self) -> None:
        payload = json.loads(perp_metadata_payload())
        payload["symbols"][0]["marginAsset"] = "BTC"
        with self.assertRaises(DataContractError) as raised:
            parse_usdm_instrument_metadata(
                json.dumps(payload).encode(),
                source_object_sha256="2" * 64,
                maker_fee_rate=Decimal("0"),
                taker_fee_rate=Decimal("0"),
                fee_rate_basis=ZERO_FEE_BASIS,
            )
        self.assertEqual(raised.exception.code, FailureCode.INSTRUMENT_METADATA_INVALID.value)


class DatasetReleaseTests(unittest.TestCase):
    def test_canonical_release_identity_round_trip_and_created_at_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = build_spot(root / "catalog-a")
            parsed = DatasetRelease.from_json_bytes(first.to_json_bytes())
            self.assertEqual(parsed, first)
            later = first.with_created_at(datetime(2027, 1, 1, tzinfo=UTC))
            self.assertEqual(later.dataset_release_id, first.dataset_release_id)
            self.assertEqual(later.material_payload(), first.material_payload())
            different_physical_path = build_spot(
                root / "catalog-at-an-unrelated-physical-path",
                created=datetime(2027, 1, 1, tzinfo=UTC),
            )
            self.assertEqual(different_physical_path.catalog_identity, first.catalog_identity)
            self.assertEqual(different_physical_path.dataset_release_id, first.dataset_release_id)

    def test_material_source_change_changes_release_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = build_spot(Path(temporary) / "catalog-a")
            changed_bindings = list(spot_bindings())
            changed_bindings[0] = replace(changed_bindings[0], sha256="9" * 64)
            changed = build_dataset_release(
                market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
                instrument_id="BTCUSDT.BINANCE",
                source_objects=changed_bindings,
                normalized_time_range=spot_range(),
                instrument_metadata=spot_metadata(),
                execution_bars=spot_bars(),
                catalog_identity=first.catalog_identity,
                created_at_utc=CREATED,
            )
            self.assertNotEqual(first.dataset_release_id, changed.dataset_release_id)
            timestamp_material = first.material_payload()
            timestamp_material["timestamp_rules_identity"] = "f" * 64
            changed_timestamp_rule = replace(
                first,
                timestamp_rules_identity="f" * 64,
                dataset_release_id=canonical_sha256(timestamp_material),
            )
            self.assertNotEqual(first.dataset_release_id, changed_timestamp_rule.dataset_release_id)
            catalog_material = first.material_payload()
            catalog_material["catalog_identity"] = "e" * 64
            changed_catalog = replace(
                first,
                catalog_identity="e" * 64,
                dataset_release_id=canonical_sha256(catalog_material),
            )
            self.assertNotEqual(first.dataset_release_id, changed_catalog.dataset_release_id)

    def test_release_schema_rejects_unknown_missing_and_duplicate_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release = build_spot(Path(temporary) / "catalog")
            base = release.to_json_bytes().decode()
            with self.assertRaises(ConfigError):
                DatasetRelease.from_json_bytes((base[:-1] + ',"unknown":1}').encode())
            with self.assertRaises(ConfigError):
                DatasetRelease.from_json_bytes(base.replace('"schema_version":1,', "").encode())
            with self.assertRaises(ConfigError):
                DatasetRelease.from_json_bytes((base[:-1] + ',"schema_version":1}').encode())

    def test_spot_forbids_mark_and_funding_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release = build_spot(Path(temporary) / "catalog")
            with self.assertRaises(DataContractError) as raised:
                build_dataset_release(
                    market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
                    instrument_id="BTCUSDT.BINANCE",
                    source_objects=(*spot_bindings(), binding(SourceRole.USDM_PERPETUAL_MARK_1M, "6")),
                    normalized_time_range=spot_range(),
                    instrument_metadata=spot_metadata(),
                    execution_bars=spot_bars(),
                    mark_bars=perp_mark_bars(),
                    catalog_identity=release.catalog_identity,
                    created_at_utc=CREATED,
                )
            self.assertEqual(raised.exception.code, FailureCode.DATA_ROLE_MISMATCH.value)

    def test_perpetual_requires_mark_and_funding_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            catalog = build_nautilus_catalog(
                Path(temporary) / "catalog",
                metadata=perp_metadata(),
                execution_bars=perp_execution_bars(),
            )
            with self.assertRaises(DataContractError) as raised:
                build_dataset_release(
                    market_profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
                    instrument_id="BTCUSDT-PERP.BINANCE",
                    source_objects=perp_bindings(),
                    normalized_time_range=perp_range(),
                    instrument_metadata=perp_metadata(),
                    execution_bars=perp_execution_bars(),
                    catalog_identity=catalog.catalog_identity,
                    created_at_utc=CREATED,
                )
            self.assertEqual(raised.exception.code, FailureCode.MARK_ROLE_INVALID.value)

    def test_perpetual_mark_grid_and_release_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release = build_perp(Path(temporary) / "catalog")
            self.assertEqual(release.completeness_result.status, "PASS")
            self.assertEqual(len(release.completeness_result.role_results), 2)
            self.assertNotEqual(release.mark_data_identity, "NOT_APPLICABLE")
            self.assertNotEqual(release.funding_data_identity, "NOT_APPLICABLE")

    def test_catalog_rebuild_semantic_stability_and_mutation_staleness(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = perp_metadata()
            events = funding_events()
            first = build_nautilus_catalog(
                root / "catalog-a",
                metadata=metadata,
                execution_bars=perp_execution_bars(),
                mark_bars=perp_mark_bars(),
                funding_events=events,
            )
            second = build_nautilus_catalog(
                root / "different-physical-path",
                metadata=metadata,
                execution_bars=perp_execution_bars(),
                mark_bars=perp_mark_bars(),
                funding_events=events,
            )
            self.assertEqual(first.catalog_identity, second.catalog_identity)
            self.assertEqual(first.semantic_inventory, second.semantic_inventory)
            release = build_perp(root / "catalog-release")
            verify_catalog_identity(release, first.semantic_inventory)
            mutation = json.loads(json.dumps(first.semantic_inventory))
            mutation["execution_bars"][0]["close"] = "1.0"
            with self.assertRaises(DataContractError) as raised:
                verify_catalog_identity(release, mutation)
            self.assertEqual(raised.exception.code, FailureCode.DATASET_RELEASE_STALE.value)


if __name__ == "__main__":
    unittest.main()
