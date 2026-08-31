from __future__ import annotations

import csv
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from nautilus_trader.backtest import BacktestEngine

from crypto_lab.checker import CheckerOutcome
from crypto_lab.checker import check_evidence_directory
from crypto_lab.config import MarketProfile
from crypto_lab.hashing import canonical_sha256
from crypto_lab.nautilus_config import add_venue_from_config
from crypto_lab.nautilus_config import to_nautilus_engine_config
from crypto_lab.status import FailureCode
from crypto_lab.strategies.base import GuardedCausalStrategy
from crypto_lab.strategies.base import OrderIntent
from tests.adversarial.test_r2_causality_boundaries import _small_scored_run
from tests.m1_helpers import SPOT_ID
from tests.m1_helpers import make_bars
from tests.m1_helpers import make_instrument
from tests.m1_helpers import make_request
from tests.m1_helpers import plan


MINUTE_NS = 60_000_000_000


class _FutureLowLevelDecisionStrategy(GuardedCausalStrategy):
    def __init__(self) -> None:
        super().__init__()
        self.rejection: str | None = None

    def on_bar(self, bar) -> None:
        super().on_bar(bar)
        if self.rejection is not None:
            return
        try:
            self._submit_guarded(
                OrderIntent("BUY", "1", "MARKET", "FUTURE_LOW_LEVEL_API"),
                bar,
                decision_timestamp_ns=int(bar.ts_init) + 1,
            )
        except ValueError as exc:
            self.rejection = str(exc)


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(
        json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        return list(reader.fieldnames or ()), list(reader)


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class ExecutionChainAdversarialTests(unittest.TestCase):
    def _baseline(self, root: Path, suffix: str):
        result = _small_scored_run(
            root,
            MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            suffix=suffix,
        )
        self.assertEqual(result.checker_outcome, CheckerOutcome.CHECK_PASS)
        return result

    def _check(self, evidence_dir: Path):
        return check_evidence_directory(
            evidence_dir,
            official_source_required=False,
            source_revision_current_head_required=False,
        )

    def _assert_chain_failure(self, report) -> None:
        self.assertEqual(report.outcome, CheckerOutcome.CHECK_FAIL)
        self.assertIn(
            FailureCode.CAUSAL_EXECUTION_UNRESOLVED.value,
            report.failure_codes,
        )
        chain = next(
            item
            for item in report.checks
            if item["name"] == "intent_native_order_fill_execution_chain"
        )
        self.assertFalse(chain["pass"])
        self.assertTrue(chain["errors"])

    def test_deleted_order_row_cannot_leave_a_submitted_intent_off_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._baseline(Path(temporary), "delete-order")
            path = result.evidence_dir / "orders.csv"
            fields, _rows = _read_csv(path)
            _write_csv(path, fields, [])
            self._assert_chain_failure(self._check(result.evidence_dir))

    def test_rehashed_deleted_native_lifecycle_cannot_pass_semantic_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._baseline(Path(temporary), "delete-native")
            path = result.evidence_dir / "nautilus_result.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["native_order_events"] = []
            payload["semantic_sequence"]["orders"] = []
            payload["semantic_digest"] = canonical_sha256(payload["semantic_sequence"])
            _write_json(path, payload)
            report = self._check(result.evidence_dir)
            self._assert_chain_failure(report)
            chain = next(
                item
                for item in report.checks
                if item["name"] == "intent_native_order_fill_execution_chain"
            )
            self.assertNotIn("SEMANTIC_DIGEST_MISMATCH", chain["errors"])
            self.assertIn("INTENT_ORDER_NATIVE_ID_SET_MISMATCH", chain["errors"])

    def test_order_quantity_side_status_and_instrument_are_native_bound(self) -> None:
        for field, value in (
            ("quantity", "9"),
            ("side", "SELL"),
            ("status", "CANCELED"),
            ("instrument_id", "ETHUSDT.BINANCE"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                result = self._baseline(Path(temporary), f"order-{field}")
                path = result.evidence_dir / "orders.csv"
                fields, rows = _read_csv(path)
                rows[0][field] = value
                _write_csv(path, fields, rows)
                self._assert_chain_failure(self._check(result.evidence_dir))

    def test_future_decision_and_effective_insert_tamper_fail_lookahead_contract(self) -> None:
        for field in ("decision_timestamp_ns", "effective_insert_at_ns"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                result = self._baseline(Path(temporary), f"future-{field}")
                path = result.evidence_dir / "nautilus_result.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                submitted = payload["strategy_observations"]["submitted_intents"][0]
                submitted[field] = int(submitted[field]) + 1
                _write_json(path, payload)
                report = self._check(result.evidence_dir)
                self.assertEqual(report.outcome, CheckerOutcome.CHECK_FAIL)
                self.assertIn(FailureCode.LOOKAHEAD_DETECTED.value, report.failure_codes)
                eligibility = next(
                    item
                    for item in report.checks
                    if item["name"] == "submitted_signal_bar_eligibility"
                )
                self.assertFalse(eligibility["pass"])

    def test_native_backtest_totals_cannot_be_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._baseline(Path(temporary), "native-totals")
            path = result.evidence_dir / "nautilus_result.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["backtest_result"]["total_orders"] = 0
            _write_json(path, payload)
            report = self._check(result.evidence_dir)
            self._assert_chain_failure(report)
            chain = next(
                item
                for item in report.checks
                if item["name"] == "intent_native_order_fill_execution_chain"
            )
            self.assertIn("NATIVE_BACKTEST_TOTALS_MISMATCH", chain["errors"])

    def test_duplicate_submitted_identifier_is_not_collapsed_by_checker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._baseline(Path(temporary), "duplicate-intent")
            path = result.evidence_dir / "nautilus_result.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["strategy_observations"]["submitted_intents"].append(
                dict(payload["strategy_observations"]["submitted_intents"][0]),
            )
            _write_json(path, payload)
            report = self._check(result.evidence_dir)
            self._assert_chain_failure(report)
            chain = next(
                item
                for item in report.checks
                if item["name"] == "intent_native_order_fill_execution_chain"
            )
            self.assertIn("SUBMITTED_INTENT_ID_CARDINALITY_MISMATCH", chain["errors"])

    def test_low_level_strategy_api_rejects_future_decision_before_order_factory(self) -> None:
        bars = make_bars(
            SPOT_ID,
            (
                (MINUTE_NS, "100.00", "101.00", "99.00", "100.00"),
                (2 * MINUTE_NS, "100.00", "101.00", "99.00", "100.00"),
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            request = make_request(
                Path(temporary),
                run_id="r2-future-low-level-decision",
                profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
                data=bars,
                plan=plan({}),
                scoring_start_ns=0,
                scoring_end_ns=2 * MINUTE_NS,
            )
            engine = BacktestEngine(
                to_nautilus_engine_config(
                    request.lab_run_config.nautilus_engine_config,
                ),
            )
            add_venue_from_config(
                engine,
                request.lab_run_config.nautilus_venue_config,
            )
            instrument = make_instrument(
                MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            )
            engine.add_instrument(instrument)
            strategy = _FutureLowLevelDecisionStrategy()
            strategy.configure(
                instrument_id=instrument.id,
                bar_type=bars[0].bar_type,
                execution_bar_type=bars[0].bar_type,
                profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
                plan=plan({}),
                scoring_start_ns=0,
                scoring_end_exclusive_ns=2 * MINUTE_NS,
                effective_insert_latency_ns=MINUTE_NS,
                size_precision=instrument.size_precision,
                min_quantity=None,
                max_quantity=None,
                size_increment=instrument.size_increment.as_decimal(),
                initial_capital_amount=Decimal("1000"),
                initial_capital_currency="USDT",
            )
            engine.add_strategy(strategy)
            engine.add_data(list(bars))
            engine.run(start=0)
            orders = engine.cache.orders(instrument_id=instrument.id)
            engine.dispose()
        self.assertEqual(
            strategy.rejection,
            "decision timestamp is in the future of the engine clock",
        )
        self.assertEqual(orders, [])
        self.assertEqual(strategy.observations["submitted_intents"], [])


if __name__ == "__main__":
    unittest.main()
