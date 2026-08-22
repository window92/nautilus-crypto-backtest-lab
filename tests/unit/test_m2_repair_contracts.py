from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from nautilus_trader.model import Quantity

from crypto_lab.config import MarketProfile
from crypto_lab.data import AcquisitionRequest
from crypto_lab.data import DataContractError
from crypto_lab.data import OfficialBinanceAcquirer
from crypto_lab.data import RawObjectStore
from crypto_lab.data import SourceObjectBinding
from crypto_lab.data import SourceRole
from crypto_lab.data import build_dataset_release
from crypto_lab.data import build_nautilus_catalog
from crypto_lab.data import parse_kline_csv
from crypto_lab.data import parse_spot_instrument_metadata
from crypto_lab.data import parse_usdm_instrument_metadata
from crypto_lab.data import to_nautilus_instrument
from crypto_lab.runner import LabRunRequest
from crypto_lab.status import FailureCode
from tests.m1_helpers import a4_bars
from tests.m1_helpers import make_request
from tests.m1_helpers import plan
from tests.m2_helpers import ZERO_FEE_BASIS
from tests.m2_helpers import binding
from tests.m2_helpers import spot_bars
from tests.m2_helpers import spot_bindings
from tests.m2_helpers import spot_metadata
from tests.m2_helpers import spot_metadata_payload
from tests.m2_helpers import spot_range
from tests.unit.test_m2_release_contract import build_perp
from tests.unit.test_m2_release_contract import build_spot


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
SPOT_METADATA_SHA = "73aa2c2b43d172e5fb12db5de19c682344b1db3e8e05b02b7cba3f56b3b9315d"
PERP_METADATA_SHA = "5383b7e8ee3a3f3819282f101ea15fb66c9135526ecc97691aa9dd30054dc988"


def raw_blob(digest: str) -> bytes:
    return (ROOT / "data/raw/sha256" / digest[:2] / f"{digest}.blob").read_bytes()


def acquisition() -> AcquisitionRequest:
    return AcquisitionRequest(
        source_role=SourceRole.SPOT_EXECUTION_1M,
        source_locator=(
            "https://data.binance.vision/data/spot/daily/klines/"
            "BTCUSDT/1m/BTCUSDT-1m-2025-01-01.zip"
        ),
        exact_filename="BTCUSDT-1m-2025-01-01.zip",
        instrument="BTCUSDT",
        market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY.value,
        requested_interval="1m",
        requested_time_range=spot_range(),
    )


def checksum_acquisition() -> AcquisitionRequest:
    base = acquisition()
    return AcquisitionRequest(
        source_role=SourceRole.PUBLISHER_CHECKSUM,
        source_locator=base.source_locator + ".CHECKSUM",
        exact_filename=base.exact_filename + ".CHECKSUM",
        instrument=base.instrument,
        market_profile=base.market_profile,
        requested_interval=base.requested_interval,
        requested_time_range=base.requested_time_range,
    )


class F01DatasetReleaseBoundaryTests(unittest.TestCase):
    def test_lab_run_request_accepts_the_typed_release_without_dict_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = build_perp(root / "catalog")
            synthetic = make_request(
                root / "runs",
                run_id="f01-golden-boundary",
                profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
                data=a4_bars(__import__("tests.m1_helpers", fromlist=["PERP_ID"]).PERP_ID),
                plan=plan({}),
                scoring_start_ns=60_000_000_000,
                scoring_end_ns=180_000_000_000,
            )
            direct = replace(synthetic, dataset_release=release)
            self.assertIs(direct.dataset_release, release)
            self.assertIsInstance(direct, LabRunRequest)


class F02PreserveBeforeParseTests(unittest.TestCase):
    def test_malformed_checksum_preserves_both_responses_and_retry_history(self) -> None:
        archive = b"official-archive-response"
        malformed_checksum = b"not a publisher checksum\n"
        base = acquisition()
        checksum = checksum_acquisition()
        mapping = {
            base.source_locator: archive,
            checksum.source_locator: malformed_checksum,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = RawObjectStore(root)
            acquirer = OfficialBinanceAcquirer(store, fetch_bytes=mapping.__getitem__)
            errors: list[DataContractError] = []
            for acquired_at in (
                NOW,
                datetime(2026, 8, 22, 12, 1, tzinfo=UTC),
            ):
                with self.assertRaises(DataContractError) as raised:
                    acquirer.acquire(
                        base,
                        acquired_at_utc=acquired_at,
                        checksum_request=checksum,
                    )
                errors.append(raised.exception)
            self.assertTrue(all(item.code == FailureCode.DATA_HASH_MISMATCH.value for item in errors))
            archive_sha = hashlib.sha256(archive).hexdigest()
            checksum_sha = hashlib.sha256(malformed_checksum).hexdigest()
            self.assertEqual(store.read_bytes(archive_sha), archive)
            self.assertEqual(store.read_bytes(checksum_sha), malformed_checksum)
            observations = tuple(root.joinpath("observations").glob("*/*.json"))
            self.assertEqual(len(observations), 4)
            self.assertTrue(
                all(
                    item.evidence == {
                        "archive_sha256": archive_sha,
                        "checksum_sha256": checksum_sha,
                    }
                    for item in errors
                ),
            )


class F03StrictSourceBindingTests(unittest.TestCase):
    def test_conflict_provenance_survives_binding_and_blocks_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = RawObjectStore(root / "raw")
            first = store.store_bytes(b"first", request=acquisition(), acquired_at_utc=NOW)
            second = store.store_bytes(
                b"second",
                request=acquisition(),
                acquired_at_utc=datetime(2026, 8, 22, 12, 1, tzinfo=UTC),
            )
            conflicted = SourceObjectBinding.from_raw(second)
            self.assertEqual(conflicted.conflicts_with_sha256, (first.sha256,))
            valid = build_spot(root / "catalog")
            sources = [conflicted, spot_bindings()[1]]
            with self.assertRaises(DataContractError) as raised:
                build_dataset_release(
                    market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
                    instrument_id="BTCUSDT.BINANCE",
                    source_objects=sources,
                    normalized_time_range=spot_range(),
                    instrument_metadata=spot_metadata(),
                    execution_bars=spot_bars(),
                    catalog_identity=valid.catalog_identity,
                    created_at_utc=NOW,
                )
            self.assertEqual(raised.exception.code, FailureCode.DATA_DUPLICATE_CONFLICT.value)

    def test_target_release_rejects_mismatched_symbol_profile_interval_range_and_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            valid = build_spot(root / "catalog")
            execution = spot_bindings()[0]
            mutations = (
                (replace(execution, instrument="ETHUSDT"), FailureCode.DATA_ROLE_MISMATCH),
                (
                    replace(
                        execution,
                        market_profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING.value,
                    ),
                    FailureCode.DATA_ROLE_MISMATCH,
                ),
                (replace(execution, requested_interval="5m"), FailureCode.DATA_ROLE_MISMATCH),
                (
                    replace(
                        execution,
                        requested_time_range=__import__("crypto_lab.data", fromlist=["TimeRange"]).TimeRange(
                            start_inclusive=datetime(2025, 1, 2, tzinfo=UTC),
                            end_exclusive=datetime(2025, 1, 3, tzinfo=UTC),
                        ),
                    ),
                    FailureCode.DATA_SOURCE_INVALID,
                ),
                (
                    replace(execution, exact_filename="ETHUSDT-1m-2025-01-01.zip"),
                    FailureCode.DATA_SOURCE_INVALID,
                ),
            )
            for mutated, expected in mutations:
                with self.subTest(expected=expected.value, mutation=mutated.to_builtins()):
                    with self.assertRaises(DataContractError) as raised:
                        build_dataset_release(
                            market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
                            instrument_id="BTCUSDT.BINANCE",
                            source_objects=(mutated, spot_bindings()[1]),
                            normalized_time_range=spot_range(),
                            instrument_metadata=spot_metadata(),
                            execution_bars=spot_bars(),
                            catalog_identity=valid.catalog_identity,
                            created_at_utc=NOW,
                        )
                    self.assertEqual(raised.exception.code, expected.value)

            perpetual = build_perp(root / "perpetual-catalog")
            perpetual_execution = next(
                item
                for item in perpetual.source_objects
                if item.source_role is SourceRole.USDM_PERPETUAL_EXECUTION_1M
            )
            profile_crossings = (
                (valid, perpetual_execution),
                (perpetual, execution),
            )
            for target, incompatible in profile_crossings:
                with self.subTest(
                    target_profile=target.market_profile.value,
                    incompatible_role=incompatible.source_role.value,
                ), self.assertRaises(DataContractError) as raised:
                    replace(
                        target,
                        source_objects=tuple(
                            sorted(
                                (*target.source_objects, incompatible),
                                key=lambda item: (
                                    item.source_role.value,
                                    item.source_locator,
                                    item.sha256,
                                ),
                            ),
                        ),
                    )
                self.assertEqual(
                    raised.exception.code,
                    FailureCode.DATA_ROLE_MISMATCH.value,
                )

    def test_second_symbol_pipeline_has_no_btc_product_default(self) -> None:
        payload = json.loads(spot_metadata_payload())
        definition = payload["symbols"][0]
        definition["symbol"] = "ETHUSDT"
        definition["baseAsset"] = "ETH"
        metadata = parse_spot_instrument_metadata(
            json.dumps(payload, separators=(",", ":")).encode(),
            raw_symbol="ETHUSDT",
            instrument_id="ETHUSDT.BINANCE",
            source_object_sha256="e" * 64,
            maker_fee_rate=Decimal("0"),
            taker_fee_rate=Decimal("0"),
            fee_rate_basis=ZERO_FEE_BASIS,
        )
        bars = parse_kline_csv(
            ROOT.joinpath("tests/golden/fixtures/m2/spot-post-transition.csv").read_bytes(),
            source_role=SourceRole.SPOT_EXECUTION_1M,
            instrument_id="ETHUSDT.BINANCE",
            market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            source_date=datetime(2025, 1, 1, tzinfo=UTC).date(),
        )
        eth_range = __import__("crypto_lab.data", fromlist=["TimeRange"]).TimeRange(
            start_inclusive=datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
            end_exclusive=datetime(2025, 1, 1, 0, 2, tzinfo=UTC),
        )
        execution = replace(
            binding(SourceRole.SPOT_EXECUTION_1M, "3"),
            source_locator=(
                "https://data.binance.vision/data/spot/daily/klines/"
                "ETHUSDT/1m/ETHUSDT-1m-2025-01-01.zip"
            ),
            exact_filename="ETHUSDT-1m-2025-01-01.zip",
            instrument="ETHUSDT",
            requested_time_range=eth_range,
        )
        metadata_binding = replace(
            binding(SourceRole.SPOT_INSTRUMENT_METADATA, "4"),
            source_locator="https://api.binance.com/api/v3/exchangeInfo?symbol=ETHUSDT",
            exact_filename="spot-exchangeInfo-ETHUSDT.json",
            instrument="ETHUSDT",
        )
        with tempfile.TemporaryDirectory() as temporary:
            catalog = build_nautilus_catalog(
                Path(temporary) / "catalog",
                metadata=metadata,
                execution_bars=bars,
            )
            release = build_dataset_release(
                market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
                instrument_id="ETHUSDT.BINANCE",
                source_objects=(execution, metadata_binding),
                normalized_time_range=eth_range,
                instrument_metadata=metadata,
                execution_bars=bars,
                catalog_identity=catalog.catalog_identity,
                created_at_utc=NOW,
            )
        self.assertEqual(release.instrument_id, "ETHUSDT.BINANCE")


class F04MarketOrderMetadataTests(unittest.TestCase):
    def _spot(self):
        return parse_spot_instrument_metadata(
            raw_blob(SPOT_METADATA_SHA),
            raw_symbol="BTCUSDT",
            instrument_id="BTCUSDT.BINANCE",
            source_object_sha256=SPOT_METADATA_SHA,
            maker_fee_rate=Decimal("0"),
            taker_fee_rate=Decimal("0"),
            fee_rate_basis=ZERO_FEE_BASIS,
        )

    def _perp(self):
        return parse_usdm_instrument_metadata(
            raw_blob(PERP_METADATA_SHA),
            raw_symbol="BTCUSDT",
            instrument_id="BTCUSDT-PERP.BINANCE",
            source_object_sha256=PERP_METADATA_SHA,
            maker_fee_rate=Decimal("0"),
            taker_fee_rate=Decimal("0"),
            fee_rate_basis=ZERO_FEE_BASIS,
        )

    def test_frozen_filters_resolve_exact_market_intersections_and_native_limits(self) -> None:
        spot = self._spot()
        perp = self._perp()
        self.assertEqual(
            (
                spot.lot_size_min_quantity,
                spot.lot_size_max_quantity,
                spot.lot_size_step_size,
            ),
            (Decimal("0.00001000"), Decimal("9000.00000000"), Decimal("0.00001000")),
        )
        self.assertEqual(
            (
                spot.market_lot_size_min_quantity,
                spot.market_lot_size_max_quantity,
                spot.market_lot_size_step_size,
            ),
            (Decimal("0.00000000"), Decimal("107.65653775"), Decimal("0.00000000")),
        )
        self.assertEqual(
            (spot.min_quantity, spot.max_quantity, spot.size_increment),
            (Decimal("0.00001"), Decimal("107.65653"), Decimal("0.00001")),
        )
        self.assertEqual(
            (
                perp.lot_size_min_quantity,
                perp.lot_size_max_quantity,
                perp.lot_size_step_size,
            ),
            (Decimal("0.001"), Decimal("1000"), Decimal("0.001")),
        )
        self.assertEqual(
            (
                perp.market_lot_size_min_quantity,
                perp.market_lot_size_max_quantity,
                perp.market_lot_size_step_size,
            ),
            (Decimal("0.001"), Decimal("120"), Decimal("0.001")),
        )
        self.assertEqual(
            (perp.min_quantity, perp.max_quantity, perp.size_increment),
            (Decimal("0.001"), Decimal("120.000"), Decimal("0.001")),
        )
        spot_native = to_nautilus_instrument(spot)
        perp_native = to_nautilus_instrument(perp)
        self.assertEqual(str(spot_native.max_quantity), "107.65653")
        self.assertEqual(str(perp_native.max_quantity), "120.000")
        self.assertIn("MARKET_LOT_SIZE", json.dumps(spot_native.info, sort_keys=True))
        self.assertIn("LOT_SIZE", json.dumps(perp_native.info, sort_keys=True))

    def test_market_quantity_max_above_max_and_grid_are_checked_before_fill(self) -> None:
        from crypto_lab.data import validate_market_order_quantity

        spot_native = to_nautilus_instrument(self._spot())
        perp_native = to_nautilus_instrument(self._perp())
        validate_market_order_quantity(spot_native, Quantity.from_str("107.65653"))
        validate_market_order_quantity(perp_native, Quantity.from_str("120.000"))
        for instrument, value in (
            (spot_native, "107.65654"),
            (perp_native, "120.001"),
            (perp_native, "1.0000"),
        ):
            with self.subTest(instrument=str(instrument.id), value=value):
                with self.assertRaises(DataContractError) as raised:
                    validate_market_order_quantity(instrument, Quantity.from_str(value))
                self.assertEqual(raised.exception.code, FailureCode.INSTRUMENT_METADATA_INVALID.value)

    def test_quantity_precision_is_not_used_as_step_size(self) -> None:
        from crypto_lab.data import validate_market_order_quantity

        payload = json.loads(raw_blob(PERP_METADATA_SHA))
        definition = payload["symbols"][0]
        for item in definition["filters"]:
            if item["filterType"] == "LOT_SIZE":
                item.update(minQty="0.006", maxQty="1000", stepSize="0.002")
            elif item["filterType"] == "MARKET_LOT_SIZE":
                item.update(minQty="0.006", maxQty="120", stepSize="0.003")
        definition["quantityPrecision"] = 3
        metadata = parse_usdm_instrument_metadata(
            json.dumps(payload, separators=(",", ":")).encode(),
            raw_symbol="BTCUSDT",
            instrument_id="BTCUSDT-PERP.BINANCE",
            source_object_sha256="d" * 64,
            maker_fee_rate=Decimal("0"),
            taker_fee_rate=Decimal("0"),
            fee_rate_basis=ZERO_FEE_BASIS,
        )
        self.assertEqual(metadata.size_increment, Decimal("0.006"))
        native = to_nautilus_instrument(metadata)
        validate_market_order_quantity(native, Quantity.from_str("119.994"))
        with self.assertRaises(DataContractError):
            validate_market_order_quantity(native, Quantity.from_str("1.001"))


class F05AcceptanceCountTests(unittest.TestCase):
    def test_reconciliation_deduplicates_shared_g09_and_discloses_occurrences(self) -> None:
        from scripts import run_m2_acceptance

        reconcile = getattr(run_m2_acceptance, "reconcile_test_executions", None)
        self.assertTrue(callable(reconcile))
        g09 = (
            "tests.qualification.test_m1_native_funding."
            "M1NativeFundingQualificationTests.test_g09_native_long_and_short_sign_timing_once"
        )
        m0_method_named_for_downstream = (
            "tests.integration.test_m0_downstream_contract.M0DownstreamContractTests."
            "test_m1_can_parse_and_bind_m0_config_without_defaults"
        )
        rows = [
            {"phase": "M0_REGRESSION", "test_id": g09, "status": "PASS"},
            {"phase": "M1_REGRESSION", "test_id": g09, "status": "PASS"},
            {
                "phase": "M0_REGRESSION",
                "test_id": m0_method_named_for_downstream,
                "status": "PASS",
            },
            {"phase": "M2", "test_id": "tests.example.Case.test_unique", "status": "PASS"},
        ]
        result = reconcile(rows)
        self.assertEqual(result["unique_test_cases"], 3)
        self.assertEqual(result["test_execution_occurrences"], 4)
        self.assertEqual(result["repeated_execution_count"], 1)
        self.assertEqual(result["repeated_executions"][0]["test_id"], g09)
        self.assertEqual(result["repeated_executions"][0]["canonical_owner_phase"], "M1_REGRESSION")
        owners = {item["test_id"]: item["canonical_owner_phase"] for item in result["unique_tests"]}
        self.assertEqual(owners[m0_method_named_for_downstream], "M0_REGRESSION")


if __name__ == "__main__":
    unittest.main()
