from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from pathlib import Path

from crypto_lab.config import ConfigError
from crypto_lab.config import MarketProfile
from crypto_lab.data import AcquisitionRequest
from crypto_lab.data import DataContractError
from crypto_lab.data import NOT_APPLICABLE
from crypto_lab.data import OfficialBinanceAcquirer
from crypto_lab.data import RawObjectRecord
from crypto_lab.data import RawObjectStore
from crypto_lab.data import SourceRole
from crypto_lab.data import TimestampUnit
from crypto_lab.data import parse_kline_csv
from crypto_lab.data import timestamp_unit_for
from crypto_lab.data import validate_one_minute_grid
from crypto_lab.data import verify_publisher_checksum
from crypto_lab.status import FailureCode
from tests.m2_helpers import FIXTURES
from tests.m2_helpers import perp_execution_bars
from tests.m2_helpers import perp_range
from tests.m2_helpers import spot_bars
from tests.m2_helpers import spot_range


NOW = datetime(2026, 8, 22, 2, 30, tzinfo=UTC)
SPOT_LOCATOR = (
    "https://data.binance.vision/data/spot/daily/klines/"
    "BTCUSDT/1m/BTCUSDT-1m-2025-01-01.zip"
)


def acquisition(role: SourceRole = SourceRole.SPOT_EXECUTION_1M) -> AcquisitionRequest:
    locator = SPOT_LOCATOR
    filename = "BTCUSDT-1m-2025-01-01.zip"
    if role is SourceRole.PUBLISHER_CHECKSUM:
        locator += ".CHECKSUM"
        filename += ".CHECKSUM"
    return AcquisitionRequest(
        source_role=role,
        source_locator=locator,
        exact_filename=filename,
        instrument="BTCUSDT",
        market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY.value,
        requested_interval="1m",
        requested_time_range=spot_range(),
    )


class RawObjectTests(unittest.TestCase):
    def test_raw_byte_hash_stability_and_read_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RawObjectStore(Path(temporary))
            record = store.store_bytes(b"exact bytes", request=acquisition(), acquired_at_utc=NOW)
            self.assertEqual(record.sha256, hashlib.sha256(b"exact bytes").hexdigest())
            self.assertEqual(store.read_bytes(record.sha256), b"exact bytes")
            again = store.store_bytes(b"exact bytes", request=acquisition(), acquired_at_utc=NOW)
            self.assertEqual(again.sha256, record.sha256)

    def test_publisher_checksum_verification_and_corruption_failure(self) -> None:
        payload = b"official archive bytes"
        digest = hashlib.sha256(payload).hexdigest()
        checksum = f"{digest}  BTCUSDT-1m-2025-01-01.zip\n".encode()
        self.assertEqual(
            verify_publisher_checksum(
                payload,
                checksum,
                exact_filename="BTCUSDT-1m-2025-01-01.zip",
            ),
            digest,
        )
        with self.assertRaises(DataContractError) as raised:
            verify_publisher_checksum(
                payload + b"corrupt",
                checksum,
                exact_filename="BTCUSDT-1m-2025-01-01.zip",
            )
        self.assertEqual(raised.exception.code, FailureCode.DATA_HASH_MISMATCH.value)

    def test_acquirer_stores_corrupted_download_before_failing_checksum(self) -> None:
        archive = b"corrupted-transfer"
        checksum = f"{'0' * 64}  BTCUSDT-1m-2025-01-01.zip\n".encode()
        mapping = {SPOT_LOCATOR: archive, SPOT_LOCATOR + ".CHECKSUM": checksum}
        with tempfile.TemporaryDirectory() as temporary:
            store = RawObjectStore(Path(temporary))
            acquirer = OfficialBinanceAcquirer(store, fetch_bytes=mapping.__getitem__)
            with self.assertRaises(DataContractError) as raised:
                acquirer.acquire(
                    acquisition(),
                    acquired_at_utc=NOW,
                    checksum_request=acquisition(SourceRole.PUBLISHER_CHECKSUM),
                )
            self.assertEqual(raised.exception.code, FailureCode.DATA_HASH_MISMATCH.value)
            digest = hashlib.sha256(archive).hexdigest()
            self.assertEqual(store.read_bytes(digest), archive)

    def test_locator_replay_with_different_bytes_preserves_both_and_links_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RawObjectStore(Path(temporary))
            first = store.store_bytes(b"publisher-v1", request=acquisition(), acquired_at_utc=NOW)
            second = store.store_bytes(
                b"publisher-v2",
                request=acquisition(),
                acquired_at_utc=datetime(2026, 8, 22, 3, 0, tzinfo=UTC),
            )
            self.assertNotEqual(first.sha256, second.sha256)
            self.assertEqual(second.conflicts_with_sha256, (first.sha256,))
            self.assertEqual(store.read_bytes(first.sha256), b"publisher-v1")
            self.assertEqual(store.read_bytes(second.sha256), b"publisher-v2")

    def test_raw_schema_rejects_unknown_missing_and_duplicate_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = RawObjectStore(Path(temporary)).store_bytes(
                b"x",
                request=acquisition(),
                acquired_at_utc=NOW,
            )
            base = record.to_json_bytes().decode()
            unknown = base[:-1] + ',"unknown":1}'
            with self.assertRaises(ConfigError):
                RawObjectRecord.from_json_bytes(unknown.encode())
            missing = base.replace('"byte_size":1,', "")
            with self.assertRaises(ConfigError):
                RawObjectRecord.from_json_bytes(missing.encode())
            duplicate = base[:-1] + ',"byte_size":1}'
            with self.assertRaises(ConfigError):
                RawObjectRecord.from_json_bytes(duplicate.encode())

    def test_prohibited_archive_role_substitution_is_rejected(self) -> None:
        prohibited = (
            "https://data.binance.vision/data/futures/um/daily/indexPriceKlines/"
            "BTCUSDT/1m/BTCUSDT-1m-2025-01-01.zip"
        )
        with self.assertRaises(ConfigError):
            AcquisitionRequest(
                source_role=SourceRole.USDM_PERPETUAL_MARK_1M,
                source_locator=prohibited,
                exact_filename="BTCUSDT-1m-2025-01-01.zip",
                instrument="BTCUSDT",
                market_profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING.value,
                requested_interval="1m",
                requested_time_range=perp_range(),
            )


class TimestampAndParsingTests(unittest.TestCase):
    def test_usdm_does_not_inherit_spot_transition(self) -> None:
        self.assertEqual(
            timestamp_unit_for(
                SourceRole.SPOT_EXECUTION_1M,
                source_date=datetime(2025, 1, 1, tzinfo=UTC).date(),
            ),
            TimestampUnit.MICROSECONDS,
        )
        self.assertEqual(
            timestamp_unit_for(
                SourceRole.USDM_PERPETUAL_EXECUTION_1M,
                source_date=datetime(2025, 1, 1, tzinfo=UTC).date(),
            ),
            TimestampUnit.MILLISECONDS,
        )
        self.assertEqual(perp_execution_bars()[0].interval_start_ns, 1_735_689_600_000_000_000)

    def test_one_minute_completeness_passes_and_missing_minute_blocks(self) -> None:
        bars = spot_bars()
        result = validate_one_minute_grid(
            bars,
            source_role=SourceRole.SPOT_EXECUTION_1M,
            time_range=spot_range(),
        )
        self.assertEqual(result.expected_count, 4)
        self.assertEqual(result.actual_count, 4)
        with self.assertRaises(DataContractError) as raised:
            validate_one_minute_grid(
                (*bars[:1], *bars[2:]),
                source_role=SourceRole.SPOT_EXECUTION_1M,
                time_range=spot_range(),
            )
        self.assertEqual(raised.exception.code, FailureCode.DATA_GAP.value)

    def _parse_mutated(self, lines: list[str]):
        return parse_kline_csv(
            ("\n".join(lines) + "\n").encode(),
            source_role=SourceRole.SPOT_EXECUTION_1M,
            instrument_id="BTCUSDT.BINANCE",
            market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            source_date=datetime(2024, 12, 31, tzinfo=UTC).date(),
        )

    def test_conflicting_duplicate_blocks(self) -> None:
        lines = FIXTURES.joinpath("spot-pre-transition.csv").read_text().splitlines()
        duplicate = lines[0].split(",")
        duplicate[4] = "93601.00000000"
        with self.assertRaises(DataContractError) as raised:
            self._parse_mutated([lines[0], ",".join(duplicate)])
        self.assertEqual(raised.exception.code, FailureCode.DATA_DUPLICATE_CONFLICT.value)

    def test_malformed_nonmonotonic_invalid_ohlc_and_negative_volume_block_without_repair(self) -> None:
        original = FIXTURES.joinpath("spot-pre-transition.csv").read_bytes()
        lines = original.decode().splitlines()
        cases: list[tuple[list[str], FailureCode]] = [
            ([lines[0].rsplit(",", 1)[0]], FailureCode.DATA_SOURCE_INVALID),
            ([lines[1], lines[0]], FailureCode.DATA_TIMESTAMP_INVALID),
        ]
        invalid_ohlc = lines[0].split(",")
        invalid_ohlc[2] = "93500.00000000"
        cases.append(([",".join(invalid_ohlc)], FailureCode.DATA_SOURCE_INVALID))
        negative_volume = lines[0].split(",")
        negative_volume[5] = "-1.00000000"
        cases.append(([",".join(negative_volume)], FailureCode.DATA_SOURCE_INVALID))
        for payload, code in cases:
            with self.subTest(code=code.value, payload=payload):
                with self.assertRaises(DataContractError) as raised:
                    self._parse_mutated(payload)
                self.assertEqual(raised.exception.code, code.value)
        self.assertEqual(FIXTURES.joinpath("spot-pre-transition.csv").read_bytes(), original)

    def test_source_role_mismatch_blocks(self) -> None:
        with self.assertRaises(DataContractError) as raised:
            parse_kline_csv(
                FIXTURES.joinpath("usdm-mark.csv").read_bytes(),
                source_role=SourceRole.USDM_PERPETUAL_MARK_1M,
                instrument_id="BTCUSDT-PERP.BINANCE",
                market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
                source_date=datetime(2025, 1, 1, tzinfo=UTC).date(),
            )
        self.assertEqual(raised.exception.code, FailureCode.DATA_ROLE_MISMATCH.value)


if __name__ == "__main__":
    unittest.main()
