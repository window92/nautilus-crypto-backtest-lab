from __future__ import annotations

import copy
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from crypto_lab.checker import CheckerOutcome
from crypto_lab.checker import check_evidence_directory
from crypto_lab.sealing import OfficialSealOutcome
from crypto_lab.sealing import verify_official_seal
from crypto_lab.status import FailureCode
from crypto_lab.status import ordered_funding_failure_codes
from tests.adversarial.test_r2_official_sealing import OfficialSealingAdversarialTests
from tests.unit.test_instrument_representation_funding_checker_repair import (
    FundingCheckerRepairTests,
)
from tests.unit.test_instrument_representation_funding_checker_repair import funding_case


ROOT = Path(__file__).resolve().parents[2]
M3_REPLAY = next(
    (
        ROOT
        / "evidence/audit/adversarial-remediation-002/qualification-retry-015/runs/perpetual-replay"
    ).iterdir()
)


class PreciseFundingFailureCodeTests(FundingCheckerRepairTests):
    def _codes(self, case):
        valid, failures, _detail = self.validate(case)
        self.assertFalse(valid)
        return failures

    def test_each_funding_defect_returns_the_exact_code(self) -> None:
        missing = funding_case()
        missing[1][0]["native_adjustments"] = []
        missing[1][0]["account_events_at_boundary"] = []
        missing[1][0]["account_balances_after_boundary"] = copy.deepcopy(
            missing[1][0]["account_balances_before_boundary"],
        )
        missing[2].clear()
        self.assertEqual(self._codes(missing)[0], FailureCode.FUNDING_MISSING.value)

        duplicate = funding_case()
        duplicate[1][0]["native_adjustments"].append(
            copy.deepcopy(duplicate[1][0]["native_adjustments"][0]),
        )
        self.assertEqual(self._codes(duplicate)[0], FailureCode.FUNDING_DOUBLE_COUNT.value)

        unexpected = funding_case(signed_qty=None)
        unexpected[1][0]["native_adjustments"] = [{
            "adjustment_type": "FUNDING",
            "instrument_id": "BTCUSDT-PERP.BINANCE",
            "ts_event": unexpected[1][0]["boundary_ns"],
            "pnl_change": "-5.00000000 USDT",
            "reason": "funding_settlement:ineligible",
        }]
        unexpected[2].append({
            "adjustment_type": "FUNDING",
            "instrument_id": "BTCUSDT-PERP.BINANCE",
            "ts_event": str(unexpected[1][0]["boundary_ns"]),
            "pnl_change": "-5.00000000 USDT",
            "reason": "funding_settlement:ineligible",
        })
        self.assertEqual(
            self._codes(unexpected)[0],
            FailureCode.FUNDING_UNEXPECTED_SETTLEMENT.value,
        )

        wrong_sign = funding_case()
        expected = Decimal(wrong_sign[2][0]["pnl_change"].split()[0])
        flipped = f"{-expected} USDT"
        wrong_sign[1][0]["native_adjustments"][0]["pnl_change"] = flipped
        wrong_sign[2][0]["pnl_change"] = flipped
        self.assertEqual(self._codes(wrong_sign)[0], FailureCode.FUNDING_SIGN_INVALID.value)

        wrong_rate = funding_case()
        wrong_rate[1][0]["runtime_updates_at_boundary"][0]["rate"] = "0.0002"
        self.assertEqual(self._codes(wrong_rate)[0], FailureCode.FUNDING_RATE_INVALID.value)

        wrong_mark = funding_case()
        wrong_mark[1][0]["native_mark_price"]["value"] = "49999.00000000"
        self.assertEqual(self._codes(wrong_mark)[0], FailureCode.FUNDING_MARK_INVALID.value)

        wrong_position = funding_case()
        wrong_position[1][0]["open_positions"][0]["signed_qty"] = "2"
        self.assertEqual(
            self._codes(wrong_position)[0],
            FailureCode.FUNDING_POSITION_INVALID.value,
        )

        wrong_boundary = funding_case()
        wrong_boundary[1][0]["boundary_ns"] = wrong_boundary[1][0]["boundary_ns"] + 1
        self.assertEqual(
            self._codes(wrong_boundary)[0],
            FailureCode.FUNDING_BOUNDARY_INVALID.value,
        )

        wrong_currency = funding_case()
        wrong_currency[1][0]["native_adjustments"][0]["pnl_change"] = (
            wrong_currency[2][0]["pnl_change"].replace("USDT", "BTC")
        )
        self.assertEqual(
            self._codes(wrong_currency)[0],
            FailureCode.FUNDING_CURRENCY_INVALID.value,
        )

        wrong_amount = funding_case()
        wrong_amount[1][0]["native_adjustments"][0]["pnl_change"] = "-1.00000000 USDT"
        wrong_amount[2][0]["pnl_change"] = "-1.00000000 USDT"
        self.assertEqual(
            self._codes(wrong_amount)[0],
            FailureCode.FUNDING_AMOUNT_INVALID.value,
        )

        wrong_delta = funding_case()
        wrong_delta[1][0]["account_balances_after_boundary"][0]["total"] = "1000.00000000 USDT"
        self.assertEqual(
            self._codes(wrong_delta)[0],
            FailureCode.FUNDING_ACCOUNT_DELTA_INVALID.value,
        )

    def test_multi_error_order_is_the_ssot_priority(self) -> None:
        case = funding_case()
        case[1][0]["native_adjustments"].append(
            copy.deepcopy(case[1][0]["native_adjustments"][0]),
        )
        case[1][0]["runtime_updates_at_boundary"][0]["rate"] = "0.0002"
        codes = self._codes(case)
        self.assertEqual(codes, ordered_funding_failure_codes(codes))
        self.assertLess(
            codes.index(FailureCode.FUNDING_DOUBLE_COUNT.value),
            codes.index(FailureCode.FUNDING_RATE_INVALID.value),
        )

    def _copy_replay(self, destination: Path) -> Path:
        shutil.copytree(M3_REPLAY, destination, symlinks=False)
        return destination

    def test_tampered_replay_fails_checker_and_official_seal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = self._copy_replay(Path(temporary) / "replay")
            funding = copied / "funding.csv"
            rows = funding.read_text(encoding="utf-8").splitlines()
            funding.write_text("\n".join([*rows, rows[1]]) + "\n", encoding="utf-8")
            report = check_evidence_directory(
                copied,
                repository_root=ROOT,
                official_source_required=False,
                source_revision_current_head_required=False,
            )
            self.assertEqual(report.outcome, CheckerOutcome.CHECK_FAIL)
            self.assertIn(FailureCode.FUNDING_DOUBLE_COUNT.value, report.failure_codes)
            seal = verify_official_seal(
                copied,
                repository_root=ROOT,
                source_revision_current_head_required=False,
            )
            self.assertIn(
                seal.outcome,
                {
                    OfficialSealOutcome.OFFICIAL_SEAL_FAIL,
                    OfficialSealOutcome.OFFICIAL_SEAL_BLOCKED,
                },
            )
            self.assertNotEqual(seal.outcome, OfficialSealOutcome.OFFICIAL_SEAL_PASS)
            self.assertIn(FailureCode.OFFICIAL_SEAL_FAILURE.value, seal.failure_codes)

    def test_funding_component_failure_cannot_receive_official_seal_pass(self) -> None:
        fixture = OfficialSealingAdversarialTests()
        fixture.setUp()
        try:
            fixture.component["outcome"] = "COMPONENT_CHECK_FAIL"
            fixture.component["failure_codes"] = [FailureCode.FUNDING_MISSING.value]
            fixture._write("component_validation.json", fixture.component)
            for name in ("evidence_manifest.json", "status.json", "official_seal.json"):
                (fixture.run_dir / name).unlink()
            fixture._seal()
            report = fixture._verify()
            self.assertEqual(report.outcome, OfficialSealOutcome.OFFICIAL_SEAL_FAIL)
            self.assertIn(FailureCode.OFFICIAL_SEAL_FAILURE.value, report.failure_codes)
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
