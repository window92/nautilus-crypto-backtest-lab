from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from nautilus_trader.model import FundingRateUpdate
from nautilus_trader.model import MarkPriceUpdate
from nautilus_trader.model import Price

from crypto_lab.checker import CheckerOutcome
from crypto_lab.checker import check_evidence_directory
from crypto_lab.config import MarketProfile
from crypto_lab.hashing import sha256_file
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.runner import run_lab
from crypto_lab.status import RunState
from tests.m1_helpers import PERP_ID
from tests.m1_helpers import SPOT_ID
from tests.m1_helpers import a4_bars
from tests.m1_helpers import intent
from tests.m1_helpers import make_bars
from tests.m1_helpers import make_request
from tests.m1_helpers import plan


class M1EvidenceContractTests(unittest.TestCase):
    def test_downstream_can_read_stable_run_result_and_bundle_without_strategy_edge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_lab(
                make_request(
                    Path(temporary),
                    run_id="m1-downstream-no-edge",
                    profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
                    data=a4_bars(SPOT_ID),
                    plan=plan({}),
                    scoring_start_ns=0,
                    scoring_end_ns=180_000_000_000,
                ),
            )
            self.assertEqual(result.state, RunState.COMPLETED)
            self.assertEqual(result.checker_outcome, CheckerOutcome.CHECK_PASS)
            required = {
                "lab_run_config.json",
                "lab_run_config.sha256",
                "runtime.lock.json",
                "runtime_identity.json",
                "source_revision.json",
                "dataset_release.json",
                "strategy_spec.json",
                "orders.csv",
                "fills.csv",
                "positions.csv",
                "account.csv",
                "native_fills.jsonl",
                "nautilus_result.json",
                "checker.json",
                "status.json",
            }
            self.assertEqual({path.name for path in result.evidence_dir.iterdir()}, required)
            status = json.loads((result.evidence_dir / "status.json").read_text())
            checker = json.loads((result.evidence_dir / "checker.json").read_text())
            self.assertEqual(status["state"], "COMPLETED")
            self.assertEqual(checker["outcome"], "CHECK_PASS")
            self.assertEqual(result.to_builtins()["config_sha256"], result.config_sha256)

            before = {
                path.name: sha256_file(path)
                for path in result.evidence_dir.iterdir()
                if path.is_file()
            }
            repeated = check_evidence_directory(result.evidence_dir)
            after = {
                path.name: sha256_file(path)
                for path in result.evidence_dir.iterdir()
                if path.is_file()
            }
            self.assertEqual(repeated.outcome, CheckerOutcome.CHECK_PASS)
            self.assertEqual(before, after)

    @staticmethod
    def _funding_data() -> tuple[object, ...]:
        bars = make_bars(
            PERP_ID,
            (
                (60_000_000_000, "50.00", "51.00", "49.00", "50.00"),
                (120_000_000_000, "99.99", "100.99", "98.99", "99.99"),
                (180_000_000_000, "110.00", "111.00", "109.00", "110.00"),
                (240_000_000_000, "120.00", "121.00", "119.00", "120.00"),
                (300_000_000_000, "121.00", "122.00", "120.00", "121.00"),
            ),
        )
        return (
            bars[0],
            bars[1],
            MarkPriceUpdate(
                PERP_ID,
                Price.from_str("100.00"),
                150_000_000_000,
                150_000_000_000,
            ),
            FundingRateUpdate(
                PERP_ID,
                Decimal("0.01"),
                160_000_000_000,
                160_000_000_000,
                interval=480,
                next_funding_ns=180_000_000_000,
            ),
            FundingRateUpdate(
                PERP_ID,
                Decimal("0.01"),
                170_000_000_000,
                170_000_000_000,
                interval=480,
                next_funding_ns=180_000_000_000,
            ),
            bars[2],
            bars[3],
            bars[4],
        )

    def _run_funding(self, root: Path, run_id: str):
        return run_lab(
            make_request(
                root,
                run_id=run_id,
                profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
                data=self._funding_data(),
                plan=plan({60_000_000_000: (intent("BUY", "2", "G09-evidence"),)}),
                scoring_start_ns=0,
                scoring_end_ns=300_000_000_000,
                expected_funding_settlements=(
                    {
                        "boundary_ns": 180_000_000_000,
                        "pnl_change": "-2.00000000 USDT",
                    },
                ),
            ),
        )

    def test_g09_checker_requires_one_native_funding_settlement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._run_funding(Path(temporary), "g09-evidence-checker")
            self.assertEqual(result.state, RunState.COMPLETED)
            self.assertEqual(result.checker_outcome, CheckerOutcome.CHECK_PASS)
            self.assertEqual(len(result.funding_events), 1)
            self.assertEqual(result.funding_events[0]["pnl_change"], "-2.00000000 USDT")
            checker = json.loads((result.evidence_dir / "checker.json").read_text())
            funding_check = next(
                item for item in checker["checks"] if item["name"] == "native_funding_exactly_once"
            )
            self.assertTrue(funding_check["pass"])
            self.assertEqual(funding_check["actual_settlements"], 1)

    def test_checker_blocks_tampered_dataset_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_lab(
                make_request(
                    Path(temporary),
                    run_id="m1-checker-dataset-tamper",
                    profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
                    data=a4_bars(SPOT_ID),
                    plan=plan({}),
                    scoring_start_ns=0,
                    scoring_end_ns=180_000_000_000,
                ),
            )
            path = result.evidence_dir / "dataset_release.json"
            dataset = json.loads(path.read_text())
            dataset["instrument_id"] = "ETHUSDT.BINANCE"
            path.write_bytes(canonical_json_bytes(dataset) + b"\n")
            report = check_evidence_directory(result.evidence_dir)
            self.assertEqual(report.outcome, CheckerOutcome.CHECK_BLOCKED)
            self.assertIn("DATA_HASH_MISMATCH", report.failure_codes)
            self.assertIn("EVIDENCE_INCOMPLETE", report.failure_codes)

    def test_checker_detects_native_fill_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_lab(
                make_request(
                    Path(temporary),
                    run_id="m1-checker-fill-tamper",
                    profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
                    data=a4_bars(SPOT_ID),
                    plan=plan({60_000_000_000: (intent("BUY", "1", "G05-negative"),)}),
                    scoring_start_ns=0,
                    scoring_end_ns=180_000_000_000,
                ),
            )
            path = result.evidence_dir / "native_fills.jsonl"
            fill = json.loads(path.read_text())
            fill["last_px"] = "999.99"
            path.write_bytes(canonical_json_bytes(fill) + b"\n")
            report = check_evidence_directory(result.evidence_dir)
            self.assertEqual(report.outcome, CheckerOutcome.CHECK_FAIL)
            self.assertIn("FILL_MUTATION_DETECTED", report.failure_codes)

    def test_g06_funding_replay_ignores_only_native_instance_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self._run_funding(root, "g06-funding-a")
            second = self._run_funding(root, "g06-funding-b")
            self.assertEqual(first.config_sha256, second.config_sha256)
            self.assertEqual(first.semantic_digest, second.semantic_digest)


if __name__ == "__main__":
    unittest.main()
