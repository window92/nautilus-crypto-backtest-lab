from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from crypto_lab.checker import CheckerOutcome
from crypto_lab.checker import check_evidence_directory
from crypto_lab.config import MarketProfile
from crypto_lab.hashing import canonical_sha256
from crypto_lab.runner import _semantic_position_sequence
from crypto_lab.status import FailureCode
from tests.adversarial.test_r2_causality_boundaries import ROOT
from tests.adversarial.test_r2_causality_boundaries import _small_scored_run


def _rows(first_id: str, second_id: str) -> list[dict[str, object]]:
    return [
        {
            "row_type": "FINAL_NATIVE_POSITION",
            "ts_event": 40,
            "instrument_id": "BTCUSDT.BINANCE",
            "position_id": second_id,
            "signed_qty": "-1",
            "quantity": "1",
            "avg_px_open": "103.00",
            "realized_pnl": "2.00 USDT",
        },
        {
            "row_type": "PositionOpened",
            "ts_event": 10,
            "instrument_id": "BTCUSDT.BINANCE",
            "position_id": first_id,
            "signed_qty": "1",
            "quantity": "1",
            "avg_px_open": "100.00",
            "realized_pnl": "-0.10 USDT",
        },
        {
            "row_type": "PositionClosed",
            "ts_event": 20,
            "instrument_id": "BTCUSDT.BINANCE",
            "position_id": first_id,
            "signed_qty": "0",
            "quantity": "0",
            "avg_px_open": "100.00",
            "realized_pnl": "2.00 USDT",
        },
        {
            "row_type": "PositionOpened",
            "ts_event": 40,
            "instrument_id": "BTCUSDT.BINANCE",
            "position_id": second_id,
            "signed_qty": "-1",
            "quantity": "1",
            "avg_px_open": "103.00",
            "realized_pnl": "-0.10 USDT",
        },
    ]


class SemanticPositionReplayTests(unittest.TestCase):
    def test_process_local_ids_are_normalized_without_mutating_native_rows(self) -> None:
        primary = _rows("POSITION-DataActor-111", "POSITION-DataActor-222")
        replay = _rows("POSITION-DataActor-999", "POSITION-DataActor-888")
        primary_before = deepcopy(primary)
        replay_before = deepcopy(replay)

        primary_semantic = _semantic_position_sequence(primary)
        replay_semantic = _semantic_position_sequence(replay)

        self.assertEqual(primary_semantic, replay_semantic)
        self.assertEqual(canonical_sha256(primary_semantic), canonical_sha256(replay_semantic))
        self.assertEqual(primary, primary_before)
        self.assertEqual(replay, replay_before)
        self.assertEqual(
            [row["position_id"] for row in primary_semantic],
            [
                "POSITION_OCCURRENCE_000001",
                "POSITION_OCCURRENCE_000002",
                "POSITION_OCCURRENCE_000002",
                "POSITION_OCCURRENCE_000001",
            ],
        )

    def test_changed_identity_relationship_or_financial_value_remains_semantic(self) -> None:
        baseline = _semantic_position_sequence(_rows("P-A", "P-B"))
        changed_relationship_rows = _rows("P-A", "P-B")
        changed_relationship_rows[2]["position_id"] = "P-C"
        changed_value_rows = _rows("P-A", "P-B")
        changed_value_rows[3]["signed_qty"] = "-9"

        self.assertNotEqual(
            baseline,
            _semantic_position_sequence(changed_relationship_rows),
        )
        self.assertNotEqual(baseline, _semantic_position_sequence(changed_value_rows))

    def test_checker_rejects_rehashed_forged_semantic_position_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = _small_scored_run(
                Path(temporary),
                MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
                suffix="semantic-position-forgery",
            )
            self.assertEqual(result.checker_outcome, CheckerOutcome.COMPONENT_CHECK_PASS)
            path = result.evidence_dir / "nautilus_result.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["semantic_sequence"]["positions"][0]["position_id"] = (
                "FORGED_SEMANTIC_POSITION"
            )
            payload["semantic_digest"] = canonical_sha256(payload["semantic_sequence"])
            path.write_text(
                json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n",
                encoding="utf-8",
            )

            report = check_evidence_directory(
                result.evidence_dir,
                repository_root=ROOT,
                official_source_required=False,
                source_revision_current_head_required=False,
            )
            self.assertEqual(report.outcome, CheckerOutcome.COMPONENT_CHECK_FAIL)
            self.assertIn(
                FailureCode.CAUSAL_EXECUTION_UNRESOLVED.value,
                report.failure_codes,
            )
            chain = next(
                item
                for item in report.checks
                if item["name"] == "intent_native_order_fill_execution_chain"
            )
            self.assertIn(
                "SEMANTIC_NATIVE_POSITION_PROJECTION_MISMATCH",
                chain["errors"],
            )


if __name__ == "__main__":
    unittest.main()
