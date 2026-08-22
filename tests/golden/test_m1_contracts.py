from __future__ import annotations

import copy
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from nautilus_trader.model import FundingRateUpdate

from crypto_lab.checker import CheckerOutcome
from crypto_lab.config import ConfigError
from crypto_lab.config import LabRunConfig
from crypto_lab.config import MarketProfile
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.runner import LabRunRequest
from crypto_lab.runner import QualificationControl
from crypto_lab.runner import RunResult
from crypto_lab.runner import run_lab
from crypto_lab.status import RunState
from tests.helpers import encode_config
from tests.helpers import load_spot_config_dict
from tests.m1_helpers import PERP_ID
from tests.m1_helpers import SPOT_ID
from tests.m1_helpers import a4_bars
from tests.m1_helpers import complete_perpetual_roles
from tests.m1_helpers import intent
from tests.m1_helpers import lifecycle_bars
from tests.m1_helpers import make_bars
from tests.m1_helpers import make_request
from tests.m1_helpers import plan


ROOT = Path(__file__).resolve().parents[2]
EXPECTATIONS = json.loads(
    (ROOT / "tests/golden/fixtures/m1-expectations.json").read_text(encoding="utf-8"),
)


class M1GoldenContractTests(unittest.TestCase):
    def _run(self, **kwargs) -> RunResult:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        request = make_request(Path(temporary.name), **kwargs)
        return run_lab(request)

    def test_m1_public_runner_contract_exists(self) -> None:
        self.assertTrue(callable(run_lab))
        self.assertIsNotNone(LabRunRequest)
        self.assertIsNotNone(RunResult)

    def test_g01_completed_bar_visibility_boundary(self) -> None:
        result = self._run(
            run_id="g01-completed-bar",
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            data=a4_bars(SPOT_ID),
            plan=plan({}),
            scoring_start_ns=0,
            scoring_end_ns=180_000_000_000,
        )
        first = result.strategy_observations["bars"][0]
        expected = EXPECTATIONS["G01"]
        self.assertEqual(first["ts_init"], expected["first_bar_available_at_ns"])
        self.assertEqual(first["callback_clock_ns"], first["ts_init"])
        self.assertFalse(expected["visible_before_available_at"])
        self.assertEqual(result.checker_outcome, CheckerOutcome.CHECK_PASS)

    def test_g02_no_same_bar_fill(self) -> None:
        result = self._run(
            run_id="g02-no-same-bar",
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            data=a4_bars(SPOT_ID),
            plan=plan({60_000_000_000: (intent("BUY", "1", "G02"),)}),
            scoring_start_ns=0,
            scoring_end_ns=180_000_000_000,
        )
        self.assertEqual(result.state, RunState.COMPLETED)
        self.assertEqual(len(result.fills), 1)
        fill = result.fills[0]
        signal = result.strategy_observations["submitted_intents"][0]
        self.assertGreater(fill["ts_event"], signal["signal_bar_available_at_ns"])
        fill_price = Decimal(fill["last_px"])
        self.assertFalse(Decimal("99.00") <= fill_price <= Decimal("101.00"))

    def test_g03_zero_latency_negative_control_fails_checker(self) -> None:
        result = self._run(
            run_id="g03-zero-latency",
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            data=a4_bars(SPOT_ID),
            plan=plan({60_000_000_000: (intent("BUY", "1", "G03"),)}),
            scoring_start_ns=0,
            scoring_end_ns=180_000_000_000,
            qualification_control=QualificationControl.ZERO_LATENCY_NEGATIVE_CONTROL,
        )
        expected = EXPECTATIONS["G03"]
        self.assertEqual(len(result.fills), 1)
        self.assertEqual(result.fills[0]["last_px"], expected["zero_latency_fill_price"])
        self.assertEqual(result.fills[0]["ts_event"], expected["zero_latency_fill_time_ns"])
        self.assertEqual(result.checker_outcome.value, expected["checker_outcome"])
        self.assertIn(expected["failure_code"], result.failure_codes)

    def test_g05_native_fill_bytes_are_immutable(self) -> None:
        result = self._run(
            run_id="g05-fill-immutability",
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            data=a4_bars(SPOT_ID),
            plan=plan({60_000_000_000: (intent("BUY", "1", "G05"),)}),
            scoring_start_ns=0,
            scoring_end_ns=180_000_000_000,
        )
        expected_bytes = canonical_json_bytes(result.fills[0]) + b"\n"
        actual_bytes = (result.evidence_dir / "native_fills.jsonl").read_bytes()
        self.assertEqual(actual_bytes, expected_bytes)
        checker = json.loads((result.evidence_dir / "checker.json").read_text())
        self.assertFalse(checker["mutated_run_evidence"])

    def test_g06_two_fresh_runs_have_identical_semantic_sequence(self) -> None:
        common = dict(
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            data=a4_bars(SPOT_ID),
            plan=plan({60_000_000_000: (intent("BUY", "1", "G06"),)}),
            scoring_start_ns=0,
            scoring_end_ns=180_000_000_000,
        )
        first = self._run(run_id="g06-replay-a", **common)
        second = self._run(run_id="g06-replay-b", **common)
        self.assertEqual(first.config_sha256, second.config_sha256)
        self.assertEqual(first.semantic_digest, second.semantic_digest)
        first_semantic = json.loads(
            (first.evidence_dir / "nautilus_result.json").read_text(),
        )["semantic_sequence"]
        second_semantic = json.loads(
            (second.evidence_dir / "nautilus_result.json").read_text(),
        )["semantic_sequence"]
        self.assertEqual(first_semantic, second_semantic)

    def test_g07_spot_oversell_is_blocked_before_submission(self) -> None:
        result = self._run(
            run_id="g07-spot-short",
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            data=a4_bars(SPOT_ID),
            plan=plan({60_000_000_000: (intent("SELL", "1", "G07"),)}),
            scoring_start_ns=0,
            scoring_end_ns=180_000_000_000,
        )
        expected = EXPECTATIONS["G07"]
        self.assertEqual(result.state, RunState.BLOCKED)
        self.assertIn(expected["oversell_failure_code"], result.failure_codes)
        self.assertEqual(result.orders, ())
        self.assertEqual(result.fills, ())

    def test_g08_perpetual_netting_lifecycle_and_guards(self) -> None:
        bars = lifecycle_bars()
        data = complete_perpetual_roles(bars)
        legal = self._run(
            run_id="g08-legal-netting",
            profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            data=data,
            plan=plan(
                {
                    60_000_000_000: (intent("BUY", "2", "open-long"),),
                    180_000_000_000: (intent("SELL", "1", "reduce"),),
                    300_000_000_000: (intent("SELL", "1", "close-flat"),),
                    420_000_000_000: (intent("SELL", "1", "reopen-short"),),
                },
            ),
            scoring_start_ns=0,
            scoring_end_ns=600_000_000_000,
        )
        self.assertEqual(legal.state, RunState.COMPLETED)
        sequence = [
            Decimal(item["signed_position"])
            for item in legal.strategy_observations["position_sequence"]
        ]
        self.assertEqual(sequence, [Decimal("2"), Decimal("1"), Decimal("0"), Decimal("-1")])
        self.assertEqual(
            [(fill["order_side"], fill["last_qty"], fill["last_px"]) for fill in legal.fills],
            [("BUY", "2", "100.00"), ("SELL", "1", "90.00"), ("SELL", "1", "90.00"), ("SELL", "1", "90.00")],
        )
        clearances = legal.strategy_observations["lifecycle_clearances"]
        self.assertEqual(
            [Decimal(item["signed_position"]) for item in clearances],
            [Decimal("2"), Decimal("1"), Decimal("0")],
        )
        self.assertTrue(all(item["terminal_status"] == "FILLED" for item in clearances))
        self.assertTrue(all(item["native_fill_events"] >= 1 for item in clearances))
        self.assertTrue(all(item["account_state_events"] >= 1 for item in clearances))
        terminal_total = Decimal(legal.account_events[-1]["balances"][0]["total"])
        self.assertEqual(terminal_total, Decimal("980.00000000"))

        cross = self._run(
            run_id="g08-cross-zero",
            profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            data=data,
            plan=plan(
                {
                    60_000_000_000: (intent("BUY", "2", "open-long"),),
                    180_000_000_000: (intent("SELL", "3", "illegal-cross"),),
                },
            ),
            scoring_start_ns=0,
            scoring_end_ns=600_000_000_000,
        )
        self.assertIn(EXPECTATIONS["G08"]["direct_cross_zero_failure_code"], cross.failure_codes)
        self.assertEqual(len([event for event in cross.orders if event["type"] == "OrderInitialized"]), 1)

        concurrent = self._run(
            run_id="g08-concurrent",
            profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            data=data,
            plan=plan(
                {
                    60_000_000_000: (
                        intent("BUY", "1", "first"),
                        intent("BUY", "1", "second"),
                    ),
                },
                attempt_all=True,
            ),
            scoring_start_ns=0,
            scoring_end_ns=600_000_000_000,
        )
        self.assertIn(EXPECTATIONS["G08"]["concurrent_failure_code"], concurrent.failure_codes)
        self.assertEqual(len([event for event in concurrent.orders if event["type"] == "OrderInitialized"]), 1)

        concurrent_nonflat = self._run(
            run_id="g08-concurrent-nonflat",
            profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            data=data,
            plan=plan(
                {
                    60_000_000_000: (intent("BUY", "2", "open-long"),),
                    180_000_000_000: (
                        intent("SELL", "2", "first-close"),
                        intent("SELL", "1", "second-before-terminal"),
                    ),
                },
                attempt_all=True,
            ),
            scoring_start_ns=0,
            scoring_end_ns=600_000_000_000,
        )
        self.assertIn(
            EXPECTATIONS["G08"]["concurrent_failure_code"],
            concurrent_nonflat.failure_codes,
        )
        initialized = [
            event
            for event in concurrent_nonflat.orders
            if event["type"] == "OrderInitialized"
        ]
        self.assertEqual(len(initialized), 2)
        guard = concurrent_nonflat.strategy_observations["guard_failures"][0]
        self.assertEqual(guard["intent"]["reason"], "second-before-terminal")

        conflict = self._run(
            run_id="g08-conflict-rule",
            profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            data=data,
            plan=plan(
                {
                    60_000_000_000: (
                        intent("BUY", "1", "first"),
                        intent("SELL", "1", "second"),
                    ),
                },
            ),
            scoring_start_ns=0,
            scoring_end_ns=600_000_000_000,
        )
        self.assertEqual(conflict.state, RunState.COMPLETED)
        self.assertEqual(len(conflict.strategy_observations["submitted_intents"]), 1)
        self.assertEqual(
            conflict.strategy_observations["suppressed_intents"][0]["reason_code"],
            "CONFLICT_RULE_REDUCED",
        )

    def test_g08_hedging_configuration_is_rejected(self) -> None:
        raw = copy.deepcopy(load_spot_config_dict())
        raw["nautilus_venue_config"]["oms_type"] = "HEDGING"
        with self.assertRaises(ConfigError):
            LabRunConfig.from_json_bytes(encode_config(raw))

    def test_g10_maker_taker_fee_is_applied_exactly_once(self) -> None:
        rows = (
            (60_000_000_000, "50.00", "51.00", "49.00", "50.00"),
            (120_000_000_000, "99.99", "100.99", "98.99", "99.99"),
            (180_000_000_000, "110.00", "111.00", "109.00", "110.00"),
        )
        result = self._run(
            run_id="g10-fee-once",
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            data=make_bars(SPOT_ID, rows),
            plan=plan({60_000_000_000: (intent("BUY", "2", "G10"),)}),
            scoring_start_ns=0,
            scoring_end_ns=180_000_000_000,
            fee=Decimal("0.001"),
        )
        expected = EXPECTATIONS["G10"]
        self.assertEqual(len(result.fills), expected["commission_count"])
        fill = result.fills[0]
        self.assertEqual(fill["last_px"], expected["fill_price"])
        self.assertEqual(fill["last_qty"], expected["fill_quantity"])
        self.assertEqual(fill["commission"], f"{expected['commission_usdt']} USDT")
        native = json.loads((result.evidence_dir / "nautilus_result.json").read_text())
        self.assertEqual(native["project_fee_postings"], 0)

    def test_g11_perpetual_mark_valuation_and_missing_mark_block(self) -> None:
        rows = (
            (60_000_000_000, "50.00", "51.00", "49.00", "50.00"),
            (120_000_000_000, "99.99", "100.99", "98.99", "99.99"),
            (180_000_000_000, "120.00", "121.00", "119.00", "120.00"),
        )
        bars = make_bars(PERP_ID, rows)
        positive = self._run(
            run_id="g11-mark-positive",
            profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            data=complete_perpetual_roles(bars, mark_value="80.00"),
            plan=plan({60_000_000_000: (intent("BUY", "2", "G11"),)}),
            scoring_start_ns=0,
            scoring_end_ns=180_000_000_000,
        )
        native = json.loads((positive.evidence_dir / "nautilus_result.json").read_text())
        self.assertEqual(native["terminal_portfolio"]["unrealized_pnl"], "-40.00000000 USDT")
        self.assertGreater(native["mark_price_count"], 0)

        funding = FundingRateUpdate(
            PERP_ID,
            Decimal("0"),
            60_000_000_000,
            60_000_000_000,
            interval=480,
            next_funding_ns=900_000_000_000,
        )
        missing = self._run(
            run_id="g11-mark-missing",
            profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            data=(bars[0], funding, *bars[1:]),
            plan=plan({60_000_000_000: (intent("BUY", "2", "G11-missing"),)}),
            scoring_start_ns=0,
            scoring_end_ns=180_000_000_000,
            mark_complete=False,
        )
        self.assertEqual(missing.state, RunState.BLOCKED)
        self.assertIn(EXPECTATIONS["G11"]["missing_mark_failure_code"], missing.failure_codes)
        self.assertEqual(missing.orders, ())

    def test_g13_warmup_bar_at_scoring_start_cannot_submit(self) -> None:
        result = self._run(
            run_id="g13-warmup-boundary",
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            data=a4_bars(SPOT_ID, 3),
            plan=plan({60_000_000_000: (intent("BUY", "1", "warmup"),)}),
            scoring_start_ns=60_000_000_000,
            scoring_end_ns=180_000_000_000,
        )
        expected = EXPECTATIONS["G13"]
        boundary = result.strategy_observations["scoring_boundary"]
        self.assertEqual(Decimal(boundary["signed_position"]), Decimal(expected["boundary_position"]))
        self.assertEqual(boundary["non_terminal_strategy_orders"], expected["boundary_non_terminal_orders"])
        self.assertEqual(Decimal(boundary["account_total"]), Decimal("1000.00"))
        self.assertEqual(boundary["currency"], "USDT")
        self.assertEqual(result.orders, ())
        self.assertEqual(result.fills, ())
        self.assertEqual(
            result.strategy_observations["suppressed_intents"][0]["reason_code"],
            "SIGNAL_BAR_NOT_SCORING_ELIGIBLE",
        )

    def test_g14_excluded_order_type_is_rejected_before_submission(self) -> None:
        result = self._run(
            run_id="g14-excluded-order",
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            data=a4_bars(SPOT_ID),
            plan=plan({60_000_000_000: (intent("BUY", "1", "G14", "LIMIT"),)}),
            scoring_start_ns=0,
            scoring_end_ns=180_000_000_000,
        )
        expected = EXPECTATIONS["G14"]
        self.assertIn(expected["failure_code"], result.failure_codes)
        self.assertEqual(len(result.orders), expected["native_submissions"])

    def test_g21_terminal_insert_boundary_suppresses_intent(self) -> None:
        result = self._run(
            run_id="g21-terminal-boundary",
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            data=a4_bars(SPOT_ID, 2),
            plan=plan({60_000_000_000: (intent("BUY", "1", "G21"),)}),
            scoring_start_ns=0,
            scoring_end_ns=120_000_000_000,
        )
        expected = EXPECTATIONS["G21"]
        self.assertEqual(len(result.orders), expected["arrival_equal_boundary_submissions"])
        self.assertEqual(len(result.fills), expected["fills_at_or_after_boundary"])
        self.assertEqual(result.state, RunState.COMPLETED)
        self.assertEqual(
            result.strategy_observations["suppressed_intents"][0]["reason_code"],
            "TERMINAL_INSERT_BOUNDARY",
        )

    def test_market_gtc_partial_fill_remainder_finishes_at_causal_arrival(self) -> None:
        rows = (
            (60_000_000_000, "100.00", "101.00", "99.00", "100.00"),
            (120_000_000_000, "200.00", "201.00", "199.00", "200.00"),
            (180_000_000_000, "300.00", "301.00", "299.00", "300.00"),
        )
        result = self._run(
            run_id="m1-partial-remainder",
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            data=make_bars(SPOT_ID, rows, volume="4"),
            plan=plan({60_000_000_000: (intent("BUY", "3", "partial"),)}),
            scoring_start_ns=0,
            scoring_end_ns=180_000_000_000,
        )
        self.assertEqual(result.state, RunState.COMPLETED)
        self.assertEqual([(fill["last_qty"], fill["ts_event"]) for fill in result.fills], [("1", 120_000_000_000), ("2", 120_000_000_000)])
        self.assertEqual(
            [event["type"] for event in result.orders],
            ["OrderInitialized", "OrderSubmitted", "OrderFilled", "OrderFilled"],
        )
        initialized = next(event for event in result.orders if event["type"] == "OrderInitialized")
        self.assertEqual(initialized["time_in_force"], "GTC")
        self.assertEqual(initialized["order_type"], "MARKET")
        self.assertEqual({event["ts_event"] for event in result.fills}, {120_000_000_000})
        order_row = (result.evidence_dir / "orders.csv").read_text()
        self.assertIn(",GTC,3,3,0,FILLED,", order_row)

    def test_terminal_shutdown_preserves_open_position_without_synthetic_close(self) -> None:
        result = self._run(
            run_id="m1-terminal-open-position",
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            data=a4_bars(SPOT_ID),
            plan=plan({60_000_000_000: (intent("BUY", "1", "terminal-open"),)}),
            scoring_start_ns=0,
            scoring_end_ns=180_000_000_000,
        )
        native = json.loads((result.evidence_dir / "nautilus_result.json").read_text())
        self.assertEqual(native["terminal_policy"], "MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE")
        self.assertTrue(native["terminal_position_open"])
        self.assertFalse(native["synthetic_terminal_close_order"])
        self.assertEqual(len([event for event in result.orders if event["type"] == "OrderInitialized"]), 1)

    def test_invalid_price_and_quantity_precision_fail_explicitly(self) -> None:
        invalid_rows = (
            (60_000_000_000, "100", "101", "99", "100"),
            (120_000_000_000, "200.00", "201.00", "199.00", "200.00"),
            (180_000_000_000, "300.00", "301.00", "299.00", "300.00"),
        )
        invalid_bar = self._run(
            run_id="m1-invalid-price-precision",
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            data=make_bars(SPOT_ID, invalid_rows),
            plan=plan({}),
            scoring_start_ns=0,
            scoring_end_ns=180_000_000_000,
        )
        self.assertIn(EXPECTATIONS["PRECISION_NEGATIVE"]["failure_code"], invalid_bar.failure_codes)
        self.assertEqual(invalid_bar.orders, ())

        invalid_qty = self._run(
            run_id="m1-invalid-quantity-precision",
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            data=a4_bars(SPOT_ID),
            plan=plan({60_000_000_000: (intent("BUY", "1.0", "bad-quantity"),)}),
            scoring_start_ns=0,
            scoring_end_ns=180_000_000_000,
        )
        self.assertIn(EXPECTATIONS["PRECISION_NEGATIVE"]["failure_code"], invalid_qty.failure_codes)
        self.assertEqual(invalid_qty.orders, ())

    def test_profile_files_are_exact_cash_and_netting_contracts(self) -> None:
        spot = json.loads((ROOT / "configs/profiles/spot_cash_v1.json").read_text())
        perp = json.loads((ROOT / "configs/profiles/usdm_perpetual_v1.json").read_text())
        self.assertEqual((spot["account_type"], spot["oms_type"]), ("CASH", "NETTING"))
        self.assertFalse(spot["allow_cash_borrowing"])
        self.assertFalse(spot["use_mark_prices"])
        self.assertEqual((perp["account_type"], perp["oms_type"]), ("MARGIN", "NETTING"))
        self.assertEqual(perp["default_leverage"], "1")
        self.assertTrue(perp["use_mark_prices"])


if __name__ == "__main__":
    unittest.main()
