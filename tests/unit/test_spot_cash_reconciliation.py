from __future__ import annotations

import csv
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from crypto_lab.checker import CheckerOutcome
from crypto_lab.checker import validate_spot_cash_reconciliation
from crypto_lab.config import MarketProfile
from crypto_lab.runner import run_lab
from crypto_lab.status import FailureCode
from tests.m1_helpers import SPOT_ID
from tests.m1_helpers import intent
from tests.m1_helpers import make_bars
from tests.m1_helpers import make_request
from tests.m1_helpers import plan


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


class SpotCashReconciliationTests(unittest.TestCase):
    def _run_partial(self):
        rows = (
            (60_000_000_000, "100.00", "101.00", "99.00", "100.00"),
            (120_000_000_000, "200.00", "201.00", "199.00", "200.00"),
            (180_000_000_000, "300.00", "301.00", "299.00", "300.00"),
        )
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        request = make_request(
            Path(temporary.name),
            run_id="spot-partial-fee-reconciliation",
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            data=make_bars(SPOT_ID, rows, volume="4"),
            plan=plan({60_000_000_000: (intent("BUY", "3", "partial"),)}),
            scoring_start_ns=0,
            scoring_end_ns=180_000_000_000,
            fee=Decimal("0.001"),
        )
        return run_lab(request)

    def test_partial_fills_fees_and_decimal_rounding_reconcile_exactly(self) -> None:
        result = self._run_partial()
        self.assertEqual(result.checker_outcome, CheckerOutcome.CHECK_PASS)
        self.assertEqual(
            [(row["last_qty"], row["commission"]) for row in result.fills],
            [("1", "0.20001000 USDT"), ("2", "0.40004000 USDT")],
        )
        report = json.loads(
            (result.evidence_dir / "checker.json").read_text(encoding="utf-8"),
        )
        reconciliation = next(
            row for row in report["checks"] if row["name"] == "spot_cash_reconciliation"
        )
        self.assertTrue(reconciliation["pass"])
        self.assertEqual(reconciliation["reconciled_fill_count"], 2)
        self.assertEqual(Decimal(reconciliation["expected_terminal_base"]), Decimal("3"))
        self.assertEqual(
            Decimal(reconciliation["expected_terminal_quote"]),
            Decimal("399.34995000"),
        )

    def test_adverse_next_bar_gap_unfunded_buy_is_rejected_pre_submission(self) -> None:
        rows = (
            (60_000_000_000, "100.00", "101.00", "99.00", "100.00"),
            (120_000_000_000, "300.00", "301.00", "299.00", "300.00"),
            (180_000_000_000, "300.00", "301.00", "299.00", "300.00"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = run_lab(
                make_request(
                    Path(temporary),
                    run_id="spot-adverse-gap-unfunded",
                    profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
                    data=make_bars(SPOT_ID, rows),
                    plan=plan({60_000_000_000: (intent("BUY", "4", "gap"),)}),
                    scoring_start_ns=0,
                    scoring_end_ns=180_000_000_000,
                    fee=Decimal("0.001"),
                ),
            )
        self.assertEqual(result.orders, ())
        self.assertEqual(result.fills, ())
        self.assertIn(FailureCode.SPOT_SHORT_OR_BORROW_DETECTED.value, result.failure_codes)
        guard = result.strategy_observations["guard_failures"][0]
        self.assertIn("maximum executable price", guard["detail"])

    def test_balance_or_commission_tamper_fails_independent_reconciliation(self) -> None:
        result = self._run_partial()
        fills = [dict(row) for row in result.fills]
        accounts = _read_csv(result.evidence_dir / "account.csv")
        positions = _read_csv(result.evidence_dir / "positions.csv")

        tampered_accounts = [dict(row) for row in accounts]
        terminal_quote = next(
            row
            for row in reversed(tampered_accounts)
            if row["currency"] == "USDT"
        )
        terminal_quote["total"] = "399.34995001"
        terminal_quote["free"] = "399.34995001"
        valid, detail = validate_spot_cash_reconciliation(
            fills=fills,
            account_rows=tampered_accounts,
            position_rows=positions,
            instrument_id="BTCUSDT.BINANCE",
            base_currency="BTC",
            quote_currency="USDT",
            initial_quote_balance=Decimal("1000"),
        )
        self.assertFalse(valid)
        self.assertIn("QUOTE_BALANCE_DELTA_MISMATCH", detail["errors"])

        tampered_fills = [dict(row) for row in fills]
        tampered_fills[0]["commission"] = "0.20000000 USDT"
        valid, detail = validate_spot_cash_reconciliation(
            fills=tampered_fills,
            account_rows=accounts,
            position_rows=positions,
            instrument_id="BTCUSDT.BINANCE",
            base_currency="BTC",
            quote_currency="USDT",
            initial_quote_balance=Decimal("1000"),
        )
        self.assertFalse(valid)
        self.assertIn("QUOTE_BALANCE_DELTA_MISMATCH", detail["errors"])

    def test_sell_exceeding_available_base_is_rejected_even_if_native_rows_exist(self) -> None:
        result = self._run_partial()
        fills = [dict(result.fills[0])]
        fills[0]["order_side"] = "SELL"
        valid, detail = validate_spot_cash_reconciliation(
            fills=fills,
            account_rows=_read_csv(result.evidence_dir / "account.csv")[:2],
            position_rows=_read_csv(result.evidence_dir / "positions.csv")[:1],
            instrument_id="BTCUSDT.BINANCE",
            base_currency="BTC",
            quote_currency="USDT",
            initial_quote_balance=Decimal("1000"),
        )
        self.assertFalse(valid)
        self.assertIn("SPOT_SELL_EXCEEDS_AVAILABLE_BASE", detail["errors"])

    def test_nonfinancial_account_snapshot_before_fill_is_allowed_but_bound_exactly(self) -> None:
        fill = {
            "instrument_id": "BTCUSDT.BINANCE",
            "last_qty": "0.00100",
            "last_px": "93610.94",
            "commission": "0.09361094 USDT",
            "currency": "USDT",
            "order_side": "BUY",
            "ts_event": "1735689660000000000",
        }
        common = {
            "account_type": "CASH",
            "account_id": "BINANCE-001",
            "locked": "0.00000000",
            "reported": "False",
        }
        accounts = [
            {
                **common,
                "event_index": "0",
                "ts_event": "1735689480000000000",
                "currency": "USDT",
                "total": "1000.00000000",
                "free": "1000.00000000",
            },
            {
                **common,
                "event_index": "1",
                "ts_event": fill["ts_event"],
                "currency": "USDT",
                "total": "1000.00000000",
                "free": "1000.00000000",
            },
            {
                **common,
                "event_index": "2",
                "ts_event": fill["ts_event"],
                "currency": "USDT",
                "total": "906.29544906",
                "free": "906.29544906",
            },
            {
                **common,
                "event_index": "2",
                "ts_event": fill["ts_event"],
                "currency": "BTC",
                "total": "0.00100000",
                "free": "0.00100000",
            },
        ]
        positions = [
            {
                "row_type": "PositionOpened",
                "instrument_id": "BTCUSDT.BINANCE",
                "signed_qty": "0.00100",
                "ts_event": fill["ts_event"],
            },
            {
                "row_type": "FINAL_NATIVE_POSITION",
                "instrument_id": "BTCUSDT.BINANCE",
                "side": "LONG",
                "quantity": "0.00100",
            },
        ]
        valid, detail = validate_spot_cash_reconciliation(
            fills=[fill],
            account_rows=accounts,
            position_rows=positions,
            instrument_id="BTCUSDT.BINANCE",
            base_currency="BTC",
            quote_currency="USDT",
            initial_quote_balance=Decimal("1000"),
        )
        self.assertTrue(valid, detail)
        self.assertEqual(detail["account_event_count"], 3)
        self.assertEqual(detail["reconciled_fill_count"], 1)


if __name__ == "__main__":
    unittest.main()
