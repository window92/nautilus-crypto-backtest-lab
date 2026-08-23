from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from crypto_lab.data_provenance import AggTrade
from crypto_lab.data_provenance import AggTradeSource
from crypto_lab.data_provenance import CoverageDisposition
from crypto_lab.data_provenance import HttpRawStore
from crypto_lab.data_provenance import KlineObservation
from crypto_lab.data_provenance import KlineSource
from crypto_lab.data_provenance import ProvenanceError
from crypto_lab.data_provenance import derive_spot_kline
from crypto_lab.data_provenance import parse_aggtrade_rest_page
from crypto_lab.data_provenance import parse_kline_rest_page
from crypto_lab.data_provenance import prove_no_trade_interval
from crypto_lab.data_provenance import reconcile_spot_minute
from crypto_lab.data_provenance import reconcile_required_mark_roles
from crypto_lab.data_provenance import reconcile_three_way_exact
from crypto_lab.data_provenance import validate_aggtrade_sequence
from crypto_lab.data_provenance import validate_official_url
from crypto_lab.data_provenance import verify_checksum


ROOT = Path(__file__).resolve().parents[2]
MINUTE = 60_000
SOURCE_A = "a" * 64
SOURCE_B = "b" * 64
SOURCE_C = "c" * 64


def trade(
    aggregate_id: int,
    *,
    source: AggTradeSource = AggTradeSource.SPOT_DAILY,
    source_sha: str = SOURCE_A,
    price: str = "1.0",
    quantity: str = "1.0",
    first_trade_id: int | None = None,
    last_trade_id: int | None = None,
    timestamp_ms: int | None = None,
    buyer_is_maker: bool = False,
) -> AggTrade:
    first = aggregate_id if first_trade_id is None else first_trade_id
    last = first if last_trade_id is None else last_trade_id
    return AggTrade(
        source_kind=source,
        source_sha256=source_sha,
        row_number=aggregate_id + 1,
        symbol="BTCUSDT",
        aggregate_trade_id=aggregate_id,
        price_text=price,
        quantity_text=quantity,
        first_trade_id=first,
        last_trade_id=last,
        timestamp_ms=aggregate_id + 1 if timestamp_ms is None else timestamp_ms,
        buyer_is_maker=buyer_is_maker,
        best_price_match=True,
    )


def kline(
    source: KlineSource,
    *,
    source_sha: str = SOURCE_A,
    open_time_ms: int = 0,
    close: str = "0.30",
    invalid: tuple[str, ...] = (),
) -> KlineObservation:
    return KlineObservation(
        source_kind=source,
        source_sha256=source_sha,
        row_number=1,
        symbol="BTCUSDT",
        interval="1m",
        open_time_ms=open_time_ms,
        open_text="0.10",
        high_text="0.30",
        low_text="0.10",
        close_text=close,
        base_volume_text="0.60",
        close_time_ms=open_time_ms + MINUTE - 1,
        quote_volume_text="0.14",
        trade_count=5,
        taker_buy_base_text="0.20",
        taker_buy_quote_text="0.02",
        ignore_text="0",
        invalid_reasons=invalid,
    )


def known_events(
    *,
    source: AggTradeSource = AggTradeSource.SPOT_DAILY,
    source_sha: str = SOURCE_A,
) -> tuple[AggTrade, ...]:
    return (
        trade(
            10,
            source=source,
            source_sha=source_sha,
            price="0.10",
            quantity="0.20",
            first_trade_id=100,
            last_trade_id=101,
            timestamp_ms=1_000,
            buyer_is_maker=False,
        ),
        trade(
            11,
            source=source,
            source_sha=source_sha,
            price="0.30",
            quantity="0.40",
            first_trade_id=102,
            last_trade_id=104,
            timestamp_ms=2_000,
            buyer_is_maker=True,
        ),
    )


class DataProvenanceContractTests(unittest.TestCase):
    def test_required_daily_mark_role_cannot_be_replaced_by_rest_monthly_consensus(self) -> None:
        accepted, reason = reconcile_required_mark_roles(
            rest_monthly_valid=True,
            daily_archive_available=False,
            daily_row_present=False,
            daily_row_valid=False,
        )
        self.assertFalse(accepted)
        self.assertEqual(reason, "SOURCE_INCOMPLETE_REQUIRED_DAILY_MARK_ROLE")

    def test_candidate_002_and_adopted_root_bytes_are_identical(self) -> None:
        candidate_dir = ROOT / "evidence/repair/data-provenance-duckdb-001/ssot-candidate-002"
        root_bytes = (ROOT / "SSOT.md").read_bytes()
        candidate_bytes = (candidate_dir / "SSOT.data-provenance-candidate.md").read_bytes()
        manifest = json.loads((candidate_dir / "manifest.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256(root_bytes).hexdigest()
        self.assertEqual(root_bytes, candidate_bytes)
        self.assertEqual(digest, "f51971ed7a09b172c82ff5965f2899d2a302dd71a2af60eb7c920133567b4354")
        self.assertEqual(manifest["candidate_ssot_sha256"], digest)

    def test_three_way_official_kline_agreement(self) -> None:
        rest = kline(KlineSource.SPOT_REST, source_sha=SOURCE_A)
        daily = kline(KlineSource.SPOT_DAILY, source_sha=SOURCE_B)
        monthly = kline(KlineSource.SPOT_MONTHLY, source_sha=SOURCE_C)
        self.assertEqual(
            reconcile_three_way_exact(rest=rest, daily=daily, monthly=monthly),
            (True, "REST_DAILY_MONTHLY_EXACT_AGREEMENT"),
        )

    def test_stale_monthly_is_superseded_only_with_trade_consensus(self) -> None:
        rest = kline(KlineSource.SPOT_REST, source_sha=SOURCE_A)
        daily = kline(KlineSource.SPOT_DAILY, source_sha=SOURCE_B)
        monthly = kline(KlineSource.SPOT_MONTHLY, source_sha=SOURCE_C, close="0.29")
        derived = derive_spot_kline(known_events(), minute_start_ms=0)
        decision = reconcile_spot_minute(
            minute_start_ms=0,
            rest=rest,
            daily=daily,
            monthly=monthly,
            derived=derived,
            no_trade_proof=None,
            independent_trade_derivation_count=2,
        )
        self.assertEqual(decision.disposition, CoverageDisposition.REAL_OFFICIAL_BAR)
        self.assertEqual(decision.reason, "MONTHLY_CONFLICT_SUPERSEDED_BY_THREE_WAY_OFFICIAL_CONSENSUS")
        self.assertEqual(decision.conflicts, ("SOURCE_CONFLICT_SUPERSEDED_OBSERVATION",))

    def test_monthly_conflict_without_event_arbitration_blocks(self) -> None:
        decision = reconcile_spot_minute(
            minute_start_ms=0,
            rest=kline(KlineSource.SPOT_REST, source_sha=SOURCE_A),
            daily=kline(KlineSource.SPOT_DAILY, source_sha=SOURCE_B),
            monthly=kline(KlineSource.SPOT_MONTHLY, source_sha=SOURCE_C, close="0.29"),
            derived=None,
            no_trade_proof=None,
        )
        self.assertEqual(decision.disposition, CoverageDisposition.SOURCE_CONFLICT)
        self.assertTrue(decision.blocking)

    def test_known_aggtrades_derive_exact_decimal_kline(self) -> None:
        result = derive_spot_kline(known_events(), minute_start_ms=0)
        self.assertEqual(result.open_text, "0.10")
        self.assertEqual(result.high_text, "0.30")
        self.assertEqual(result.low_text, "0.10")
        self.assertEqual(result.close_text, "0.30")
        self.assertEqual(Decimal(result.base_volume_text), Decimal("0.60"))
        self.assertEqual(Decimal(result.quote_volume_text), Decimal("0.14"))
        self.assertEqual(result.trade_count, 5)
        self.assertEqual(Decimal(result.taker_buy_base_text), Decimal("0.20"))
        self.assertEqual(Decimal(result.taker_buy_quote_text), Decimal("0.02"))
        self.assertEqual(result.close_time_ms, 59_999)

    def test_trade_order_duplicate_overlap_and_gap_fail_closed(self) -> None:
        base = known_events()
        cases = (
            (base[::-1], "strictly ordered"),
            ((base[0], replace(base[1], aggregate_trade_id=10)), "duplicate aggregate trade ID"),
            ((base[0], replace(base[1], first_trade_id=101)), "overlap"),
            ((base[0], replace(base[1], first_trade_id=103, last_trade_id=105)), "underlying trade-ID gap"),
        )
        for events, message in cases:
            with self.subTest(message=message), self.assertRaises(ProvenanceError) as raised:
                validate_aggtrade_sequence(events, require_contiguous=True)
            self.assertEqual(raised.exception.code, "SOURCE_INCOMPLETE")
            self.assertIn(message, str(raised.exception))

    def test_verified_no_trade_positive_case_has_no_price(self) -> None:
        before = trade(7, first_trade_id=70, last_trade_id=72, timestamp_ms=1)
        after = trade(8, first_trade_id=73, last_trade_id=75, timestamp_ms=120_000)
        proof = prove_no_trade_interval(
            start_ms=MINUTE,
            end_ms=2 * MINUTE,
            before=before,
            after=after,
            events_inside=(),
            rest_kline_present=False,
            daily_kline_present=False,
            archives_complete=True,
            official_sources=(SOURCE_A,),
        )
        decision = reconcile_spot_minute(
            minute_start_ms=MINUTE,
            rest=None,
            daily=None,
            monthly=None,
            derived=None,
            no_trade_proof=proof,
        )
        self.assertEqual(decision.disposition, CoverageDisposition.VERIFIED_NO_TRADE_INTERVAL)
        self.assertIsNone(decision.canonical_identity)
        self.assertFalse(decision.blocking)

    def test_no_trade_rejects_aggregate_or_underlying_id_gap(self) -> None:
        before = trade(7, first_trade_id=70, last_trade_id=72, timestamp_ms=1)
        cases = (
            trade(9, first_trade_id=73, last_trade_id=75, timestamp_ms=120_000),
            trade(8, first_trade_id=74, last_trade_id=75, timestamp_ms=120_000),
        )
        for after in cases:
            with self.assertRaises(ProvenanceError) as raised:
                prove_no_trade_interval(
                    start_ms=MINUTE,
                    end_ms=2 * MINUTE,
                    before=before,
                    after=after,
                    events_inside=(),
                    rest_kline_present=False,
                    daily_kline_present=False,
                    archives_complete=True,
                    official_sources=(SOURCE_A,),
                )
            self.assertEqual(raised.exception.code, "SOURCE_INCOMPLETE")

    def test_monthly_only_impossible_row_blocks_without_complete_proof(self) -> None:
        decision = reconcile_spot_minute(
            minute_start_ms=0,
            rest=None,
            daily=None,
            monthly=kline(KlineSource.SPOT_MONTHLY),
            derived=None,
            no_trade_proof=None,
        )
        self.assertEqual(decision.disposition, CoverageDisposition.SOURCE_CONFLICT)
        self.assertTrue(decision.blocking)

    def test_aggtrade_rest_pagination_gap_is_rejected(self) -> None:
        payload = json.dumps(
            [
                {"a": 1, "p": "1", "q": "1", "f": 1, "l": 1, "T": 1, "m": False, "M": True},
                {"a": 3, "p": "1", "q": "1", "f": 2, "l": 2, "T": 2, "m": False, "M": True},
            ],
        ).encode()
        with self.assertRaises(ProvenanceError) as raised:
            parse_aggtrade_rest_page(payload, source_sha256=SOURCE_A)
        self.assertEqual(raised.exception.code, "SOURCE_INCOMPLETE")

    def test_malformed_http_payload_is_rejected_after_preservation(self) -> None:
        class Response:
            status = 200
            headers = {"content-type": "application/json"}

            def __init__(self) -> None:
                self._read = False

            def getcode(self) -> int:
                return self.status

            def read(self, _size: int) -> bytes:
                if self._read:
                    return b""
                self._read = True
                return b"{malformed"

            def close(self) -> None:
                return None

        with tempfile.TemporaryDirectory() as temporary:
            store = HttpRawStore(Path(temporary))
            with patch("crypto_lab.data_provenance.urllib.request.urlopen", return_value=Response()):
                observation = store.capture(
                    "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&startTime=0&endTime=59999&limit=1000",
                    source_role="SPOT_REST_KLINES",
                    pagination_position="page:0",
                )
            self.assertEqual(store.read(observation.raw_object_sha256), b"{malformed")
            with self.assertRaises(ProvenanceError):
                parse_kline_rest_page(
                    store.read(observation.raw_object_sha256),
                    source_kind=KlineSource.SPOT_REST,
                    source_sha256=observation.raw_object_sha256,
                )

    def test_publisher_checksum_mismatch_preserves_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "official.zip"
            payload.write_bytes(b"official")
            with self.assertRaises(ProvenanceError) as raised:
                verify_checksum(payload, f"{'0' * 64}  official.zip\n".encode(), exact_filename="official.zip")
            self.assertEqual(raised.exception.code, "DATA_HASH_MISMATCH")

    def test_unofficial_and_cross_venue_urls_are_rejected(self) -> None:
        for url in (
            "https://www.kaggle.com/datasets/example",
            "https://api.exchange.example/klines",
        ):
            with self.assertRaises(ProvenanceError):
                validate_official_url(url)


if __name__ == "__main__":
    unittest.main()
