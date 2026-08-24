from __future__ import annotations

import inspect
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from nautilus_trader.core import UUID4
from nautilus_trader.model import AccountId
from nautilus_trader.model import ClientOrderId
from nautilus_trader.model import Currency
from nautilus_trader.model import LiquiditySide
from nautilus_trader.model import Money
from nautilus_trader.model import OrderFilled
from nautilus_trader.model import OrderSide
from nautilus_trader.model import OrderType
from nautilus_trader.model import Position
from nautilus_trader.model import PositionAdjusted
from nautilus_trader.model import PositionAdjustmentType
from nautilus_trader.model import PositionId
from nautilus_trader.model import Price
from nautilus_trader.model import Quantity
from nautilus_trader.model import StrategyId
from nautilus_trader.model import TradeId
from nautilus_trader.model import TraderId
from nautilus_trader.model import VenueOrderId

from crypto_lab.config import MarketProfile
from crypto_lab.diagnostics import _completed_trade_series
from crypto_lab.native_metrics import qualify_native_calmar
from crypto_lab.native_positions import NativePositionSequenceError
from crypto_lab.native_positions import capture_native_completed_position_sequence
from crypto_lab.reporting import generate_native_research_metrics_readiness
from crypto_lab.research import CompletedTradeSeries
from crypto_lab.research import SampleAdequacy
from crypto_lab.research import evaluate_sample_adequacy
from crypto_lab.runner import _native_returns_basis
from tests.m1_helpers import make_instrument
from tests.m4_helpers import valid_protocol


USDT = Currency.from_str("USDT")


def _fill(
    instrument: Any,
    *,
    position_id: str,
    order_id: str,
    trade_id: str,
    side: OrderSide,
    quantity: str,
    price: str,
    commission: str,
    timestamp_ns: int,
) -> OrderFilled:
    return OrderFilled(
        trader_id=TraderId("TRADER-001"),
        strategy_id=StrategyId("STRATEGY-001"),
        instrument_id=instrument.id,
        client_order_id=ClientOrderId(order_id),
        venue_order_id=VenueOrderId(order_id.removeprefix("O-")),
        account_id=AccountId("SIM-001"),
        trade_id=TradeId(trade_id),
        order_side=side,
        order_type=OrderType.MARKET,
        last_qty=Quantity.from_str(quantity),
        last_px=Price.from_str(price),
        currency=USDT,
        liquidity_side=LiquiditySide.TAKER,
        event_id=UUID4(),
        ts_event=timestamp_ns,
        ts_init=timestamp_ns,
        reconciliation=False,
        position_id=PositionId(position_id),
        commission=Money.from_str(f"{commission} USDT"),
    )


def _closed_position(
    *,
    profile: MarketProfile = MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
    position_id: str = "P-001",
    opening_order_id: str = "O-001",
    closing_order_id: str = "O-002",
    opening_side: OrderSide = OrderSide.BUY,
    opening_price: str = "100.00",
    closing_price: str = "115.00",
    funding: str | None = None,
) -> tuple[Any, Position]:
    instrument = make_instrument(profile)
    closing_side = OrderSide.SELL if opening_side is OrderSide.BUY else OrderSide.BUY
    position = Position(
        instrument,
        _fill(
            instrument,
            position_id=position_id,
            order_id=opening_order_id,
            trade_id="T-001",
            side=opening_side,
            quantity="2",
            price=opening_price,
            commission="0.10",
            timestamp_ns=1,
        ),
    )
    if funding is not None:
        position.apply_adjustment(
            PositionAdjusted(
                trader_id=TraderId("TRADER-001"),
                strategy_id=StrategyId("STRATEGY-001"),
                instrument_id=instrument.id,
                position_id=position.id,
                account_id=AccountId("SIM-001"),
                adjustment_type=PositionAdjustmentType.FUNDING,
                quantity_change=None,
                pnl_change=Money.from_str(f"{funding} USDT"),
                reason="native funding qualification",
                event_id=UUID4(),
                ts_event=2,
                ts_init=2,
            ),
        )
    position.apply(
        _fill(
            instrument,
            position_id=position_id,
            order_id=closing_order_id,
            trade_id="T-002",
            side=closing_side,
            quantity="2",
            price=closing_price,
            commission="0.20",
            timestamp_ns=3,
        ),
    )
    return instrument, position


class _CacheFixture:
    def __init__(
        self,
        *,
        current: tuple[Any, ...],
        snapshots: tuple[Any, ...] = (),
        snapshot_parent: str | None = None,
    ) -> None:
        self.current = current
        self.snapshots = snapshots
        self.snapshot_parent = snapshot_parent

    def positions(self, *, instrument_id: Any) -> list[Any]:
        return list(self.current)

    def positions_open(self, *, instrument_id: Any) -> list[Any]:
        return [
            position
            for position in self.current
            if isinstance(position, Position) and position.is_open
        ]

    def positions_closed(self, *, instrument_id: Any) -> list[Any]:
        return [
            position
            for position in self.current
            if isinstance(position, Position) and position.is_closed
        ]

    def position_snapshots(self, *, position_id: Any | None = None) -> list[Any]:
        if position_id is None:
            return list(self.snapshots)
        return (
            list(self.snapshots)
            if self.snapshot_parent is not None and str(position_id) == self.snapshot_parent
            else []
        )


def _capture(
    instrument: Any,
    cache: _CacheFixture,
    *,
    expected: int,
    run_id: str = "native-run-001",
):
    return capture_native_completed_position_sequence(
        cache,
        instrument_id=instrument.id,
        source_run_id=run_id,
        expected_settlement_currency="USDT",
        expected_closed_cycle_count=expected,
    )


class NativePositionSequenceTests(unittest.TestCase):
    def test_spot_closed_position_is_one_native_completed_cycle(self) -> None:
        instrument, position = _closed_position()
        sequence = _capture(instrument, _CacheFixture(current=(position,)), expected=1)
        self.assertEqual(sequence.completed_trade_count, 1)
        self.assertEqual(sequence.terminal_open_position_count, 0)
        self.assertEqual(sequence.units[0].entry_side, "BUY")
        self.assertEqual(sequence.units[0].realized_pnl, Decimal("29.70000000"))
        self.assertEqual(sequence.units[0].realized_return, Decimal("0.15"))
        self.assertEqual(sequence.units[0].peak_quantity, Decimal("2"))

    def test_settlement_commissions_and_funding_are_native_position_pnl(self) -> None:
        instrument, position = _closed_position(
            profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            funding="-2.00",
        )
        sequence = _capture(instrument, _CacheFixture(current=(position,)), expected=1)
        unit = sequence.units[0]
        self.assertEqual(unit.realized_pnl, Decimal("27.70000000"))
        self.assertEqual(sum((item.amount for item in unit.commissions), Decimal(0)), Decimal("0.3"))
        self.assertEqual(unit.funding_adjustment_count, 1)
        self.assertEqual(unit.realized_return, Decimal("0.15"))
        self.assertTrue(sequence.unambiguous_net_after_cost)
        self.assertEqual(sequence.net_outcomes, (Decimal("27.70000000"),))

    def test_netting_snapshot_is_counted_once_and_terminal_open_is_excluded(self) -> None:
        profile = MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING
        instrument, snapshot = _closed_position(
            profile=profile,
            position_id="P-001-SNAPSHOT",
        )
        current = Position(
            instrument,
            _fill(
                instrument,
                position_id="P-001",
                order_id="O-003",
                trade_id="T-003",
                side=OrderSide.SELL,
                quantity="2",
                price="114.00",
                commission="0.10",
                timestamp_ns=4,
            ),
        )
        sequence = _capture(
            instrument,
            _CacheFixture(
                current=(current,),
                snapshots=(snapshot,),
                snapshot_parent="P-001",
            ),
            expected=1,
        )
        self.assertEqual(sequence.completed_trade_count, 1)
        self.assertEqual(sequence.terminal_open_position_count, 1)
        self.assertEqual(sequence.units[0].source_kind, "CACHE_POSITION_SNAPSHOT")
        self.assertEqual(sequence.units[0].entry_side, "BUY")

    def test_partial_reduction_is_not_a_completed_cycle(self) -> None:
        instrument = make_instrument(MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY)
        position = Position(
            instrument,
            _fill(
                instrument,
                position_id="P-001",
                order_id="O-001",
                trade_id="T-001",
                side=OrderSide.BUY,
                quantity="2",
                price="100.00",
                commission="0.10",
                timestamp_ns=1,
            ),
        )
        position.apply(
            _fill(
                instrument,
                position_id="P-001",
                order_id="O-002",
                trade_id="T-002",
                side=OrderSide.SELL,
                quantity="1",
                price="110.00",
                commission="0.10",
                timestamp_ns=2,
            ),
        )
        sequence = _capture(instrument, _CacheFixture(current=(position,)), expected=0)
        self.assertEqual(sequence.completed_trade_count, 0)
        self.assertEqual(sequence.terminal_open_position_count, 1)

    def test_missing_or_orphan_snapshot_fails_closed(self) -> None:
        instrument = make_instrument(MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY)
        current = Position(
            instrument,
            _fill(
                instrument,
                position_id="P-001",
                order_id="O-001",
                trade_id="T-001",
                side=OrderSide.BUY,
                quantity="1",
                price="100.00",
                commission="0.10",
                timestamp_ns=1,
            ),
        )
        with self.assertRaisesRegex(NativePositionSequenceError, "disagree"):
            _capture(instrument, _CacheFixture(current=(current,)), expected=1)
        _, orphan = _closed_position(position_id="P-ORPHAN")
        with self.assertRaisesRegex(NativePositionSequenceError, "orphan"):
            _capture(
                instrument,
                _CacheFixture(current=(current,), snapshots=(orphan,)),
                expected=1,
            )

    def test_duplicate_and_forged_completed_units_fail_closed(self) -> None:
        instrument, position = _closed_position()
        duplicate = _CacheFixture(
            current=(position,),
            snapshots=(position, position),
            snapshot_parent=str(position.id),
        )
        with self.assertRaisesRegex(NativePositionSequenceError, "duplicate"):
            _capture(instrument, duplicate, expected=2)
        _, conflicting = _closed_position(
            position_id=str(position.id),
            closing_price="116.00",
        )
        conflict = _CacheFixture(
            current=(position,),
            snapshots=(position, conflicting),
            snapshot_parent=str(position.id),
        )
        with self.assertRaisesRegex(NativePositionSequenceError, "duplicate"):
            _capture(instrument, conflict, expected=2)
        with self.assertRaisesRegex(NativePositionSequenceError, "forged"):
            _capture(instrument, _CacheFixture(current=({"forged": True},)), expected=0)

    def test_manual_fill_pairing_is_not_an_input_to_native_capture(self) -> None:
        signature = inspect.signature(capture_native_completed_position_sequence)
        self.assertNotIn("fills", signature.parameters)
        instrument, position = _closed_position()
        with self.assertRaises(TypeError):
            capture_native_completed_position_sequence(
                _CacheFixture(current=(position,)),
                instrument_id=instrument.id,
                source_run_id="native-run-001",
                expected_settlement_currency="USDT",
                expected_closed_cycle_count=1,
                fills=(),
            )

    def test_semantic_sequence_is_deterministic_across_runtime_ids(self) -> None:
        instrument, first = _closed_position(
            position_id="P-FIRST",
            opening_order_id="O-101",
            closing_order_id="O-102",
        )
        _, second = _closed_position(
            position_id="P-SECOND",
            opening_order_id="O-201",
            closing_order_id="O-202",
        )
        first_sequence = _capture(
            instrument,
            _CacheFixture(current=(first,)),
            expected=1,
            run_id="run-one",
        )
        second_sequence = _capture(
            instrument,
            _CacheFixture(current=(second,)),
            expected=1,
            run_id="run-two",
        )
        self.assertEqual(
            first_sequence.semantic_sequence_sha256,
            second_sequence.semantic_sequence_sha256,
        )
        self.assertNotEqual(first_sequence.sequence_id, second_sequence.sequence_id)

    def test_diagnostics_consumes_v2_native_sequence_without_fill_pairing(self) -> None:
        instrument, position = _closed_position()
        sequence = _capture(instrument, _CacheFixture(current=(position,)), expected=1)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "native_completed_trades.json"
            path.write_bytes(sequence.to_json_bytes() + b"\n")
            series = _completed_trade_series(
                path,
                expected_run_id="native-run-001",
                settlement_currency="USDT",
            )
            self.assertTrue(series.stable_native_sequence)
            self.assertEqual(series.native_completed_unit_count, 1)
            self.assertEqual(series.realized_pnl_outcomes, (Decimal("29.70000000"),))
            with self.assertRaisesRegex(Exception, "Run mismatch"):
                _completed_trade_series(
                    path,
                    expected_run_id="wrong-run",
                    settlement_currency="USDT",
                )


class ResearchReadinessTests(unittest.TestCase):
    def _series(self, count: int = 2) -> CompletedTradeSeries:
        pnls = (Decimal("2"), Decimal("-1"))[:count]
        returns = (Decimal("0.02"), Decimal("-0.01"))[:count]
        return CompletedTradeSeries(
            source="NAUTILUS_NATIVE_COMPLETED_TRADES",
            evidence_sha256="a" * 64,
            settlement_currency="USDT",
            stable_native_sequence=True,
            native_completed_unit_count=count,
            realized_pnl_outcomes=pnls,
            realized_returns=returns,
            unambiguous_net_after_cost=True,
            net_outcomes=pnls,
        )

    def test_native_averages_are_reported_but_gross_pnl_is_not_invented(self) -> None:
        calmar = qualify_native_calmar(
            returns=(
                (1, Decimal("0.10")),
                (86_400_000_000_001, Decimal("-0.10")),
            ),
            returns_basis="PORTFOLIO_DAILY_ACCOUNT_RETURNS",
            scored_start_ns=0,
            scoring_end_exclusive_ns=2 * 86_400_000_000_000,
            period=2,
        )
        result = generate_native_research_metrics_readiness(
            run_id="qualified-run",
            completed_trades=self._series(),
            sample_adequacy=SampleAdequacy.LOW_CONFIDENCE,
            native_calmar=calmar,
            terminal_open_position_excluded=True,
        )
        self.assertEqual(result.completed_native_units.value, "2")
        self.assertEqual(result.average_trade_realized_pnl.value, "0.50000000")
        self.assertEqual(result.average_trade_realized_return.value, "0.00500000")
        self.assertEqual(result.gross_pnl.value, "UNDEFINED")
        self.assertEqual(
            result.gross_pnl.undefined_reason,
            "UNDEFINED_NATIVE_GROSS_PNL_NOT_EXPOSED",
        )
        self.assertEqual(result.calmar.status, "NATIVE")
        self.assertEqual(result.calmar.value, "-0.099999999999999")
        self.assertEqual(result.calmar_qualification_id, calmar.qualification_id)
        self.assertEqual(result.calmar_input_returns_sha256, calmar.input_returns_sha256)
        with self.assertRaisesRegex(Exception, "terminal open Position"):
            generate_native_research_metrics_readiness(
                run_id="qualified-run",
                completed_trades=self._series(),
                sample_adequacy=SampleAdequacy.LOW_CONFIDENCE,
                native_calmar=calmar,
                terminal_open_position_excluded=False,
            )

    def test_calmar_rejects_position_return_fallback_and_zero_drawdown(self) -> None:
        position_basis = qualify_native_calmar(
            returns=((1, Decimal("-0.1")),),
            returns_basis="POSITION_RETURNS_FALLBACK",
            scored_start_ns=0,
            scoring_end_exclusive_ns=10,
        )
        self.assertEqual(position_basis.status, "UNDEFINED")
        self.assertIn("PORTFOLIO", position_basis.undefined_reason)
        zero_drawdown = qualify_native_calmar(
            returns=((1, Decimal("0.1")), (2, Decimal("0.1"))),
            returns_basis="PORTFOLIO_DAILY_ACCOUNT_RETURNS",
            scored_start_ns=0,
            scoring_end_exclusive_ns=10,
        )
        self.assertEqual(zero_drawdown.status, "UNDEFINED")
        self.assertIn("ZERO_DRAWDOWN", zero_drawdown.undefined_reason)

    def test_terminal_open_is_not_added_to_native_completed_sample(self) -> None:
        series = self._series(1)
        rule = valid_protocol().sample_adequacy_rule
        self.assertEqual(evaluate_sample_adequacy(rule, series), SampleAdequacy.LOW_CONFIDENCE)
        self.assertEqual(series.native_completed_unit_count, 1)

    def test_conflicting_native_count_is_rejected(self) -> None:
        with self.assertRaisesRegex(Exception, "values are incomplete"):
            CompletedTradeSeries(
                source="NAUTILUS_NATIVE_COMPLETED_TRADES",
                evidence_sha256="b" * 64,
                settlement_currency="USDT",
                stable_native_sequence=True,
                native_completed_unit_count=2,
                realized_pnl_outcomes=(Decimal("1"),),
                realized_returns=(Decimal("0.01"),),
                unambiguous_net_after_cost=True,
                net_outcomes=(Decimal("1"),),
            )

    def test_returns_basis_uses_native_snapshot_gate_then_position_fallback(self) -> None:
        instrument, position = _closed_position()
        sequence = _capture(instrument, _CacheFixture(current=(position,)), expected=1)
        day_ns = 86_400_000_000_000
        portfolio_result = SimpleNamespace(
            returns_series={day_ns: 0.01, 2 * day_ns: -0.02},
        )
        snapshots = [
            {
                "unpriced_instruments": [],
                "total_equity": [{"currency": "USDT", "amount": "100"}],
                "base_currency_equity": None,
                "ts_event": day_ns,
            },
            {
                "unpriced_instruments": [],
                "total_equity": [{"currency": "USDT", "amount": "101"}],
                "base_currency_equity": None,
                "ts_event": 3 * day_ns,
            },
        ]
        self.assertEqual(
            _native_returns_basis(
                portfolio_result,
                native_completed=sequence,
                portfolio_snapshots=snapshots,
            ),
            "PORTFOLIO_DAILY_ACCOUNT_RETURNS",
        )

        fallback_result = SimpleNamespace(
            returns_series={
                sequence.units[0].closed_ns: float(sequence.units[0].realized_return),
            },
        )
        self.assertEqual(
            _native_returns_basis(
                fallback_result,
                native_completed=sequence,
                portfolio_snapshots=[
                    {
                        "unpriced_instruments": [],
                        "total_equity": [
                            {"currency": "BTC", "amount": "1"},
                            {"currency": "USDT", "amount": "100"},
                        ],
                        "base_currency_equity": None,
                        "ts_event": day_ns,
                    },
                ],
            ),
            "POSITION_RETURNS_FALLBACK",
        )

    def test_returns_basis_disagreement_fails_closed(self) -> None:
        instrument, position = _closed_position()
        sequence = _capture(instrument, _CacheFixture(current=(position,)), expected=1)
        with self.assertRaisesRegex(ValueError, "NATIVE_RETURNS_BASIS_AMBIGUOUS"):
            _native_returns_basis(
                SimpleNamespace(returns_series={42: 0.5}),
                native_completed=sequence,
                portfolio_snapshots=[],
            )


if __name__ == "__main__":
    unittest.main()
