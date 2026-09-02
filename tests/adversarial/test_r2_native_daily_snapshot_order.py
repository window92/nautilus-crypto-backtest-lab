from __future__ import annotations

import copy
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from nautilus_trader.model import FundingRateUpdate
from nautilus_trader.model import MarkPriceUpdate
from nautilus_trader.model import Price

from crypto_lab.checker import _validate_official_daily_portfolio_snapshots
from crypto_lab.checker import NATIVE_RESEARCH_FAMILIES
from crypto_lab.config import MarketProfile
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.m1_qualification import PERP_ID
from crypto_lab.m1_qualification import _FundingBarStrategy
from crypto_lab.m1_qualification import _bar
from crypto_lab.m1_qualification import _engine
from crypto_lab.runner import DAY_NS
from crypto_lab.runner import ONE_MINUTE_NS
from crypto_lab.runner import OFFICIAL_DAILY_METRIC_FAMILIES
from crypto_lab.runner import _native_portfolio_snapshots
from crypto_lab.runner import _run_real_data_with_native_boundary_checkpoints
from tests.m1_helpers import make_request
from tests.m1_helpers import SPOT_ID
from tests.m1_helpers import make_bars
from tests.m1_helpers import plan


def _snapshot(
    index: int,
    timestamp: int,
    *,
    account_type: str,
    totals: list[dict[str, str]],
    realized: str = "0",
    unrealized: str = "0",
) -> dict[str, object]:
    return {
        "snapshot_index": index,
        "account_id": "BINANCE-001",
        "account_type": account_type,
        "base_currency": None,
        "base_currency_equity": None,
        "total_equity": totals,
        "realized_pnls": (
            []
            if Decimal(realized) == 0
            else [{"amount": realized, "currency": "USDT"}]
        ),
        "unrealized_pnls": (
            []
            if Decimal(unrealized) == 0
            else [{"amount": unrealized, "currency": "USDT"}]
        ),
        "is_stale": False,
        "stale_instruments": [],
        "stale_currencies": [],
        "unpriced_instruments": [],
        "ts_event": timestamp,
        "ts_init": timestamp,
    }


def _capture(timestamps: tuple[int, ...], row_count: int) -> dict[str, object]:
    return {
        "schema": "native-post-event-portfolio-snapshot-capture-v1",
        "status": "PASS",
        "public_api": "nautilus_trader.portfolio.Portfolio.build_snapshot",
        "capture_phase": "AFTER_ALL_SAME_TIMESTAMP_MARK_FUNDING_BAR_EVENTS",
        "automatic_snapshot_count": row_count,
        "explicit_post_event_snapshot_count": len(timestamps),
        "canonical_snapshot_count": row_count,
        "superseded_pre_event_snapshot_count": 1,
        "explicit_post_event_timestamps_ns": list(timestamps),
        "financial_state_mutated_by_project": False,
    }


class NativeSnapshotEventOrderTests(unittest.TestCase):
    def test_runner_and_independent_checker_close_the_same_research_family_scope(self) -> None:
        self.assertEqual(
            OFFICIAL_DAILY_METRIC_FAMILIES,
            NATIVE_RESEARCH_FAMILIES,
        )

    def test_native_post_event_snapshot_includes_same_timestamp_funding(self) -> None:
        scoring_start = DAY_NS
        funding_boundary = 2 * DAY_NS
        scoring_end = 3 * DAY_NS
        engine = _engine()
        strategy = _FundingBarStrategy()
        strategy.signal_at_ns = scoring_start
        engine.add_strategy(strategy)
        data = (
            MarkPriceUpdate(
                PERP_ID,
                Price.from_str("100.00"),
                scoring_start,
                scoring_start,
            ),
            _bar(scoring_start, "50.00"),
            _bar(scoring_start + ONE_MINUTE_NS, "99.99"),
            MarkPriceUpdate(
                PERP_ID,
                Price.from_str("100.00"),
                funding_boundary - 30_000_000_000,
                funding_boundary - 30_000_000_000,
            ),
            MarkPriceUpdate(
                PERP_ID,
                Price.from_str("100.00"),
                funding_boundary,
                funding_boundary,
            ),
            FundingRateUpdate(
                PERP_ID,
                Decimal("0.01"),
                funding_boundary,
                funding_boundary,
                interval=3,
                next_funding_ns=None,
            ),
            FundingRateUpdate(
                PERP_ID,
                Decimal("0.01"),
                funding_boundary,
                funding_boundary,
                interval=3,
                next_funding_ns=None,
            ),
            _bar(funding_boundary, "100.00"),
            MarkPriceUpdate(
                PERP_ID,
                Price.from_str("100.00"),
                scoring_end,
                scoring_end,
            ),
            _bar(scoring_end, "100.00"),
        )
        try:
            funding, checkpoints, native_snapshots = (
                _run_real_data_with_native_boundary_checkpoints(
                    engine,
                    data=data,
                    instrument_id=PERP_ID,
                    start_ns=scoring_start,
                    scoring_start_ns=scoring_start,
                    end_ns=scoring_end,
                    capture_daily_portfolio=True,
                )
            )
            account = engine.cache.account_for_venue(PERP_ID.venue)
            pre_event_rows, _ = _native_portfolio_snapshots(engine, account)
            post_event_rows, capture = _native_portfolio_snapshots(
                engine,
                account,
                explicit_post_event_snapshots=native_snapshots,
            )
        finally:
            engine.dispose()

        pre_event = next(
            row for row in pre_event_rows if row["ts_event"] == funding_boundary
        )
        post_event = next(
            row for row in post_event_rows if row["ts_event"] == funding_boundary
        )
        self.assertEqual(pre_event["total_equity"][0]["amount"], "1000.00000000")
        self.assertEqual(post_event["total_equity"][0]["amount"], "998.00000000")
        self.assertEqual(post_event["realized_pnls"][0]["amount"], "-2.00000000")
        self.assertEqual([item["pnl_change"] for item in funding], ["-2.00000000 USDT"])
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(capture["superseded_pre_event_snapshot_count"], 1)
        self.assertFalse(capture["financial_state_mutated_by_project"])


class DailySnapshotReconciliationTests(unittest.TestCase):
    def _request(self, root: Path, profile: MarketProfile):
        instrument_id = (
            SPOT_ID
            if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
            else PERP_ID
        )
        return make_request(
            root,
            run_id=f"r2-daily-snapshot-{profile.name.lower()}",
            profile=profile,
            data=make_bars(
                instrument_id,
                ((DAY_NS, "100.00", "101.00", "99.00", "100.00"),),
            ),
            plan=plan({}),
            scoring_start_ns=DAY_NS,
            scoring_end_ns=3 * DAY_NS,
        )

    @staticmethod
    def _write_rows(run_dir: Path, rows: list[dict[str, object]]) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "native_portfolio_snapshots.jsonl").write_bytes(
            b"".join(canonical_json_bytes(row) + b"\n" for row in rows),
        )

    def test_pre_funding_daily_snapshot_tamper_fails_financial_reconciliation(self) -> None:
        timestamps = (DAY_NS, 2 * DAY_NS, 3 * DAY_NS)
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            config = self._request(
                run_dir,
                MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            ).lab_run_config
            initial = config.initial_capital.amount
            rows = [
                _snapshot(
                    0,
                    timestamps[0],
                    account_type="MARGIN",
                    totals=[{"amount": str(initial), "currency": "USDT"}],
                ),
                _snapshot(
                    1,
                    timestamps[1],
                    account_type="MARGIN",
                    totals=[{"amount": str(initial - 2), "currency": "USDT"}],
                    realized="-2",
                ),
                _snapshot(
                    2,
                    timestamps[2],
                    account_type="MARGIN",
                    totals=[{"amount": str(initial - 2), "currency": "USDT"}],
                    realized="-2",
                ),
            ]
            result = {
                "native_daily_portfolio_snapshot_capture": _capture(timestamps, 3),
                "dataset_contract": {
                    "instrument": {
                        "instrument_id": config.instrument_id,
                        "settlement_currency": "USDT",
                        "settlement_currency_precision": 8,
                        "size_increment": "1",
                        "price_precision": 2,
                        "size_precision": 0,
                        "multiplier": "1",
                    },
                },
            }
            observations = {
                "mark_price_updates": [
                    {
                        "instrument_id": config.instrument_id,
                        "value": "100.00",
                        "ts_event": timestamp,
                        "ts_init": timestamp,
                    }
                    for timestamp in timestamps
                ],
            }
            fills = [
                {
                    "fill_index": "0",
                    "event_id": "fill-1",
                    "client_order_id": "order-1",
                    "venue_order_id": "venue-order-1",
                    "trade_id": "trade-1",
                    "position_id": "position-1",
                    "account_id": "BINANCE-001",
                    "instrument_id": config.instrument_id,
                    "order_side": "BUY",
                    "order_type": "MARKET",
                    "last_qty": "2",
                    "last_px": "100",
                    "commission": "0E-8 USDT",
                    "currency": "USDT",
                    "liquidity_side": "TAKER",
                    "ts_event": str(DAY_NS + ONE_MINUTE_NS),
                    "ts_init": str(DAY_NS + ONE_MINUTE_NS),
                },
            ]
            funding = [
                {
                    "adjustment_type": "FUNDING",
                    "instrument_id": config.instrument_id,
                    "pnl_change": "-2.00000000 USDT",
                    "quantity_change": "0",
                    "reason": "funding_settlement:golden",
                    "ts_event": str(2 * DAY_NS),
                },
            ]
            self._write_rows(run_dir, rows)
            valid, detail = _validate_official_daily_portfolio_snapshots(
                run_dir=run_dir,
                config=config,
                result=result,
                observations=observations,
                fills=fills,
                funding_rows=funding,
                base_currency=None,
            )
            self.assertTrue(valid, detail)

            tampered = copy.deepcopy(rows)
            tampered[1]["total_equity"][0]["amount"] = str(initial)
            tampered[1]["realized_pnls"] = []
            self._write_rows(run_dir, tampered)
            valid, detail = _validate_official_daily_portfolio_snapshots(
                run_dir=run_dir,
                config=config,
                result=result,
                observations=observations,
                fills=fills,
                funding_rows=funding,
                base_currency=None,
            )
            self.assertFalse(valid)
            self.assertIn("Perpetual daily snapshot mismatch", detail["errors"][0])

    def test_spot_daily_snapshot_replays_balances_and_open_position_marks(self) -> None:
        timestamps = (DAY_NS, 2 * DAY_NS, 3 * DAY_NS)
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            config = self._request(
                run_dir,
                MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            ).lab_run_config
            initial = config.initial_capital.amount
            rows = [
                _snapshot(
                    0,
                    timestamps[0],
                    account_type="CASH",
                    totals=[{"amount": str(initial), "currency": "USDT"}],
                ),
                _snapshot(
                    1,
                    timestamps[1],
                    account_type="CASH",
                    totals=[
                        {"amount": "2", "currency": "BTC"},
                        {"amount": str(initial - 20), "currency": "USDT"},
                    ],
                    unrealized="4",
                ),
                _snapshot(
                    2,
                    timestamps[2],
                    account_type="CASH",
                    totals=[
                        {"amount": "2", "currency": "BTC"},
                        {"amount": str(initial - 20), "currency": "USDT"},
                    ],
                    unrealized="2",
                ),
            ]
            result = {
                "native_daily_portfolio_snapshot_capture": _capture(timestamps, 3),
            }
            observations = {
                "valuation_bars": [
                    {
                        "bar_type": f"{config.instrument_id}-1-DAY-LAST-INTERNAL",
                        "ts_event": timestamp,
                        "ts_init": timestamp,
                        "callback_clock_ns": timestamp,
                        "close": close,
                    }
                    for timestamp, close in zip(
                        timestamps,
                        ("10", "12", "11"),
                        strict=True,
                    )
                ],
            }
            fills = [
                {
                    "fill_index": "0",
                    "event_id": "fill-1",
                    "instrument_id": config.instrument_id,
                    "order_side": "BUY",
                    "order_type": "MARKET",
                    "last_qty": "2",
                    "last_px": "10",
                    "commission": "0 USDT",
                    "currency": "USDT",
                    "ts_event": str(DAY_NS + ONE_MINUTE_NS),
                    "ts_init": str(DAY_NS + ONE_MINUTE_NS),
                },
            ]
            self._write_rows(run_dir, rows)
            valid, detail = _validate_official_daily_portfolio_snapshots(
                run_dir=run_dir,
                config=config,
                result=result,
                observations=observations,
                fills=fills,
                funding_rows=[],
                base_currency="BTC",
            )
            self.assertTrue(valid, detail)

            tampered = copy.deepcopy(rows)
            tampered[1]["total_equity"][0]["amount"] = "3"
            self._write_rows(run_dir, tampered)
            valid, detail = _validate_official_daily_portfolio_snapshots(
                run_dir=run_dir,
                config=config,
                result=result,
                observations=observations,
                fills=fills,
                funding_rows=[],
                base_currency="BTC",
            )
            self.assertFalse(valid)
            self.assertIn("Spot daily snapshot mismatch", detail["errors"][0])


if __name__ == "__main__":
    unittest.main()
