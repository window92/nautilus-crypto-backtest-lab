from __future__ import annotations

import csv
import json
import unittest
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "evidence/m3/m3-acceptance-001"


def directory(name: str) -> Path:
    summary = json.loads((EVIDENCE / "attempt-summaries" / f"{name}.json").read_text())
    return EVIDENCE / summary["evidence_dir"]


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


class M3RealGoldenValues(unittest.TestCase):
    def test_spot_fill_is_on_later_real_bar_and_native_fee_matches_hand_calculation(self) -> None:
        fill = rows(directory("spot-primary") / "fills.csv")[0]
        self.assertEqual(fill["last_px"], "93610.94")
        self.assertEqual(fill["last_qty"], "0.00100")
        self.assertEqual(fill["ts_event"], "1735689660000000000")
        self.assertEqual(fill["commission"], "0.09361094 USDT")

    def test_perpetual_fill_lifecycle_matches_frozen_real_data_expectation(self) -> None:
        fills = rows(directory("perpetual-primary") / "fills.csv")
        self.assertEqual(
            [(item["order_side"], item["last_qty"], item["last_px"], item["ts_event"]) for item in fills],
            [
                ("BUY", "0.004", "93695.1", "1735718280000000000"),
                ("SELL", "0.001", "93654.3", "1735718340000000000"),
                ("SELL", "0.003", "93675.4", "1735718520000000000"),
                ("SELL", "0.001", "93675.5", "1735718580000000000"),
            ],
        )

    def test_perpetual_funding_cash_effect_is_independent_decimal_expectation(self) -> None:
        quantity = Decimal("0.003")
        multiplier = Decimal("1")
        mark = Decimal("93661.08732624")
        rate = Decimal("0.00010000")
        expected = (-quantity * multiplier * mark * rate).quantize(Decimal("0.00000001"))
        self.assertEqual(expected, Decimal("-0.02809833"))
        funding = rows(directory("perpetual-primary") / "funding.csv")
        self.assertEqual(len(funding), 1)
        self.assertEqual(Decimal(funding[0]["pnl_change"].split()[0]), expected)


if __name__ == "__main__":
    unittest.main()
