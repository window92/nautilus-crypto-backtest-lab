from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from crypto_lab import runner
from crypto_lab.checker import check_evidence_directory
from crypto_lab.data import DataContractError
from crypto_lab.data import FundingEvent
from crypto_lab.data import to_nautilus_funding_updates
from crypto_lab.data import to_nautilus_mark_updates
from crypto_lab.owner import _official_child_command
from crypto_lab.status import FailureCode
from tests.m2_helpers import perp_mark_bars
from tests.unit.test_instrument_representation_funding_checker_repair import repaired_perp


ROOT = Path(__file__).resolve().parents[2]


class AuditRegressionTests(unittest.TestCase):
    def test_owner_child_uses_single_cli_module_identity(self) -> None:
        workflow = ROOT / "research/workflows/owner-smoke-002-spot-sma20-development.json"
        command = _official_child_command(ROOT, workflow)
        self.assertEqual(command[1], str(ROOT / "scripts/run_owner_workflow.py"))
        self.assertNotIn("-m", command)
        self.assertNotIn("crypto_lab.owner", command)

    def test_f009_failure_code_vocabulary_exactly_matches_ssot_section_15(self) -> None:
        expected = {
            "RUNTIME_LOCK_MISMATCH",
            "RUNTIME_WHEEL_HASH_MISMATCH",
            "UNSUPPORTED_RUNTIME",
            "UNSUPPORTED_MARKET_PROFILE",
            "UNSUPPORTED_V1_ORDER_TYPE",
            "CONFIG_INVALID",
            "CONFIG_HASH_MISMATCH",
            "NETWORK_DURING_OFFICIAL_RUN",
            "DATA_SOURCE_INVALID",
            "DATA_HASH_MISMATCH",
            "DATA_TIMESTAMP_INVALID",
            "DATA_GAP",
            "DATA_DUPLICATE_CONFLICT",
            "DATA_ROLE_MISMATCH",
            "DATASET_RELEASE_STALE",
            "IRRECOVERABLE_OFFICIAL_MARK_DELIVERY_GAP",
            "DATA_WINDOW_QUALITY_EXHAUSTED",
            "INSTRUMENT_METADATA_INVALID",
            "TIMEFRAME_AGGREGATION_UNRESOLVED",
            "CAUSAL_EXECUTION_UNRESOLVED",
            "LOOKAHEAD_DETECTED",
            "SAME_BAR_EXECUTION_DETECTED",
            "FILL_MUTATION_DETECTED",
            "SPOT_SHORT_OR_BORROW_DETECTED",
            "PERP_PROFILE_INVALID",
            "CROSS_ZERO_ORDER_REJECTED",
            "CONCURRENT_STRATEGY_ORDER_REJECTED",
            "FEE_MISSING",
            "FEE_DOUBLE_COUNT",
            "FUNDING_MISSING",
            "FUNDING_AMBIGUOUS",
            "FUNDING_DOUBLE_COUNT",
            "MARK_ROLE_INVALID",
            "DETERMINISM_FAILURE",
            "DETERMINISTIC_REBUILD_MISMATCH",
            "CHECKER_FAILURE",
            "CHECKER_BLOCKED",
            "TRIAL_HISTORY_INCOMPLETE",
            "RESEARCH_PROTOCOL_INVALID",
            "PARTITION_LEAKAGE",
            "HOLDOUT_ALREADY_CONSUMED",
            "HOLDOUT_HISTORY_VIOLATION",
            "MULTIPLE_TESTING_UNDECLARED",
            "CLAIM_INELIGIBLE",
            "DOWNSTREAM_CONTRACT_FAILURE",
            "DEFECT_ROOT_UNRESOLVED",
            "RETRY_LIMIT_REACHED",
            "EVIDENCE_INCOMPLETE",
        }
        self.assertEqual({item.value for item in FailureCode}, expected)

    def test_f007_utc_conversion_uses_exact_integer_arithmetic(self) -> None:
        value = datetime(2021, 8, 24, 12, 34, 56, 1, tzinfo=UTC)
        expected = 1_629_808_496_000_001_000
        self.assertEqual(runner._timestamp_ns(value), expected)
        # The historical float path is off by exactly the reported 24 ns.
        self.assertEqual(int(value.timestamp() * 1_000_000_000) - expected, 24)

    def test_f005_mark_converter_rejects_cross_instrument_relabel(self) -> None:
        wrong = replace(perp_mark_bars()[0], instrument_id="ETHUSDT-PERP.BINANCE")
        with self.assertRaises(DataContractError) as raised:
            to_nautilus_mark_updates((wrong,), repaired_perp())
        self.assertEqual(raised.exception.code, FailureCode.MARK_ROLE_INVALID.value)

    def test_f005_funding_converter_rejects_cross_instrument_relabel(self) -> None:
        event = FundingEvent(
            instrument_id="ETHUSDT-PERP.BINANCE",
            calc_time_ns=1_610_000_000_003_000_000,
            funding_interval_hours=8,
            funding_rate=Decimal("0.0001"),
            source_row_number=1,
            source_row_sha256="5" * 64,
            event_key="6" * 64,
        )
        with self.assertRaises(DataContractError) as raised:
            to_nautilus_funding_updates((event,), repaired_perp())
        self.assertEqual(raised.exception.code, FailureCode.DATA_ROLE_MISMATCH.value)

    def test_f001_historical_impossible_spot_benchmark_is_financially_rejected(self) -> None:
        report = check_evidence_directory(
            ROOT / "runs/owner-strategy-research-001-spot-benchmark-run-ef60cf17606c",
            repository_root=ROOT,
            source_revision_current_head_required=False,
        )
        reconciliation = [
            item for item in report.checks if item["name"] == "spot_cash_reconciliation"
        ]
        historical_shallow_check = [
            item for item in report.checks if item["name"] == "spot_cash_no_short_or_borrow"
        ]
        self.assertEqual(len(reconciliation), 1)
        self.assertFalse(reconciliation[0]["pass"])
        self.assertTrue(historical_shallow_check[0]["pass"])
        self.assertIn(FailureCode.SPOT_SHORT_OR_BORROW_DETECTED.value, report.failure_codes)

    def test_f004_every_real_perpetual_run_gets_exact_funding_binding_check(self) -> None:
        report = check_evidence_directory(
            ROOT / "runs/owner-strategy-research-001-perpetual-candidate-a-run-7c03f28261fe",
            repository_root=ROOT,
            source_revision_current_head_required=False,
        )
        exact = [
            item for item in report.checks if item["name"] == "official_funding_exact_binding"
        ]
        self.assertEqual(len(exact), 1)
        self.assertEqual(exact[0]["source_event_count"], 636)
        self.assertEqual(exact[0]["applicable_open_position_boundaries"], 542)
        self.assertFalse(exact[0]["pass"])


if __name__ == "__main__":
    unittest.main()
