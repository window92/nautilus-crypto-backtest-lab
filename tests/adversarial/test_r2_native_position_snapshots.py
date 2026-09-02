from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from crypto_lab.checker import CheckerOutcome
from crypto_lab.config import MarketProfile
from crypto_lab.hashing import canonical_sha256
from crypto_lab.runner import run_lab
from tests.m1_helpers import PERP_ID
from tests.m1_helpers import complete_perpetual_roles
from tests.m1_helpers import intent
from tests.m1_helpers import lifecycle_bars
from tests.m1_helpers import make_bars
from tests.m1_helpers import make_request
from tests.m1_helpers import plan


class NativePositionSnapshotAdversarialTests(unittest.TestCase):
    def test_two_close_reopen_lifecycles_use_exact_detached_native_snapshots(self) -> None:
        bars = (
            *lifecycle_bars(),
            *make_bars(
                PERP_ID,
                ((660_000_000_000, "70.00", "71.00", "69.00", "70.00"),),
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = run_lab(
                make_request(
                    Path(temporary),
                    run_id="r2-two-native-close-lifecycles",
                    profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
                    data=complete_perpetual_roles(bars),
                    plan=plan(
                        {
                            60_000_000_000: (intent("BUY", "2", "open-long"),),
                            180_000_000_000: (intent("SELL", "1", "reduce-long"),),
                            300_000_000_000: (intent("SELL", "1", "close-long"),),
                            420_000_000_000: (intent("SELL", "1", "open-short"),),
                            540_000_000_000: (intent("BUY", "1", "close-short"),),
                        },
                    ),
                    scoring_start_ns=0,
                    scoring_end_ns=660_000_000_000,
                ),
            )
            payload = json.loads(
                (result.evidence_dir / "nautilus_result.json").read_text(
                    encoding="utf-8",
                ),
            )

        self.assertEqual(result.checker_outcome, CheckerOutcome.CHECK_PASS)
        self.assertEqual(
            [item["event_type"] for item in result.strategy_observations["position_sequence"]],
            [
                "PositionOpened",
                "PositionChanged",
                "PositionClosed",
                "PositionOpened",
                "PositionClosed",
            ],
        )
        snapshots = payload["native_closed_position_snapshots"]
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(
            [
                (
                    str(item["avg_px_open"]),
                    str(item["avg_px_close"]),
                    item["realized_pnl"],
                    int(item["ts_opened"]),
                    int(item["ts_closed"]),
                )
                for item in snapshots
            ],
            [
                ("100.0", "90.0", "-20.00000000 USDT", 120_000_000_000, 360_000_000_000),
                ("90.0", "80.0", "10.00000000 USDT", 480_000_000_000, 600_000_000_000),
            ],
        )
        completed = payload["semantic_sequence"]["native_completed_positions"]
        self.assertEqual(
            [
                (
                    item["source_kind"],
                    item["average_open_price"],
                    item["average_close_price"],
                    item["realized_pnl"],
                )
                for item in completed
            ],
            [
                ("DIRECT_POSITION_CLOSED_SNAPSHOT", "100.0", "90.0", "-20.00000000"),
                ("DIRECT_POSITION_CLOSED_SNAPSHOT", "90.0", "80.0", "10.00000000"),
            ],
        )
        self.assertEqual(
            len({canonical_sha256(item) for item in snapshots}),
            2,
        )


if __name__ == "__main__":
    unittest.main()
