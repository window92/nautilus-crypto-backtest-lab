"""Registered causal BTCUSDT daily price-versus-SMA20 trend strategy.

The implementation calculates signals only. NautilusTrader remains the sole
owner of order lifecycles, fills, positions, fees, funding, accounts, PnL, and
portfolio valuation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from nautilus_trader.indicators import SimpleMovingAverage
from nautilus_trader.model import AggregationSource
from nautilus_trader.model import Bar
from nautilus_trader.model import BarType

from crypto_lab.config import MarketProfile
from crypto_lab.status import FailureCode
from crypto_lab.strategies.base import GuardedCausalStrategy
from crypto_lab.strategies.base import OrderIntent
from crypto_lab.strategies.base import StrategySpec


DAY_NS = 86_400_000_000_000


class TargetState(StrEnum):
    LONG = "LONG"
    FLAT = "FLAT"
    SHORT = "SHORT"


def classify_target(
    close: Decimal,
    sma: Decimal,
    profile: MarketProfile,
) -> TargetState:
    """Apply the frozen inequality/equality rule without financial side effects."""

    if not close.is_finite() or not sma.is_finite():
        raise ValueError("daily close and SMA must be finite")
    if close > sma:
        return TargetState.LONG
    if close == sma or profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
        return TargetState.FLAT
    return TargetState.SHORT


def validate_completed_utc_daily_bar(bar: Bar) -> None:
    """Reject a non-daily, non-internal, off-boundary, or incomplete signal Bar."""

    bar_type = bar.bar_type
    if (
        bar_type.spec.get_interval_ns() != DAY_NS
        or bar_type.aggregation_source is not AggregationSource.INTERNAL
        or int(bar.ts_init) != int(bar.ts_event)
        or int(bar.ts_init) % DAY_NS != 0
    ):
        raise ValueError(
            "TIMEFRAME_AGGREGATION_UNRESOLVED: incomplete or non-UTC daily Bar "
            f"(bar_type={bar_type}, ts_event={int(bar.ts_event)}, ts_init={int(bar.ts_init)})",
        )


def locked_sma20_parameters(profile: MarketProfile) -> dict[str, str]:
    profile_values = BtcusdtDailyPriceVsSma20Trend._expected_profile_parameters(profile)
    return {
        "authorization_id": "OWNER_OPERATIONAL_SMOKE_001",
        "completed_bars_only": "TRUE_REJECT_INCOMPLETE",
        "daily_boundary": "UTC",
        "equality_target": "FLAT",
        "market_policy": profile_values["market_policy"],
        "network_access": "FORBIDDEN",
        "order_quantity": profile_values["order_quantity"],
        "order_type": "MARKET",
        "profitability_claim": "INELIGIBLE",
        "reversal_behavior": profile_values["reversal_behavior"],
        "signal_price": "DAILY_CLOSE_FROM_CANONICAL_1M",
        "sma_lookback": "20",
        "strategy_family": "BTCUSDT_DAILY_PRICE_VS_SMA20_TREND",
    }


def locked_sma20_strategy_spec(profile: MarketProfile) -> StrategySpec:
    instrument_id = (
        "BTCUSDT.BINANCE"
        if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        else "BTCUSDT-PERP.BINANCE"
    )
    is_spot = profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
    return StrategySpec(
        strategy_id=(
            "owner-smoke-001-btcusdt-spot-daily-price-vs-sma20"
            if is_spot
            else "owner-smoke-001-btcusdt-usdm-perpetual-daily-price-vs-sma20"
        ),
        strategy_version="1",
        market_profile=profile,
        instrument_id=instrument_id,
        signal_bar_types=(
            f"{instrument_id}-1-DAY-LAST-INTERNAL@1-MINUTE-EXTERNAL",
        ),
        parameters=locked_sma20_parameters(profile),
        indicator_definitions=(
            "nautilus_trader.indicators.SimpleMovingAverage(period=20,input=COMPLETED_DAILY_CLOSE)",
        ),
        warmup_requirement="20_COMPLETED_UTC_DAILY_BARS_WITH_ZERO_WARMUP_ORDERS",
        sizing_rule=(
            "TARGET_LONG_0.10000_BTC_OR_FLAT_NO_PYRAMIDING"
            if is_spot
            else "TARGET_SIGNED_0.100_BTC_ONE_WAY_NETTING_NO_PYRAMIDING"
        ),
        entry_rule="COMPLETED_DAILY_CLOSE_VERSUS_SMA20_STRICT_INEQUALITY",
        exit_rule=(
            "CLOSE_LONG_TO_FLAT_WHEN_CLOSE_LE_SMA20"
            if is_spot
            else "EQUALITY_TO_FLAT_OR_CLOSE_FLAT_CONFIRM_SEPARATE_REVERSAL"
        ),
        conflict_rule="FIRST_ELIGIBLE_INTENT",
        terminal_behavior="MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE",
        market_order_time_in_force="GTC",
    )


@dataclass(frozen=True)
class _PendingReversal:
    target: TargetState
    signal_bar: Bar


class BtcusdtDailyPriceVsSma20Trend(GuardedCausalStrategy):
    """One locked SMA20 family supporting the two authorized Market Profiles."""

    REGISTRATION_ID = "btcusdt_daily_price_vs_sma20_trend_v1"
    IMPLEMENTATION_REVISION = "OWNER_OPERATIONAL_SMOKE_001_SMA20_V1"
    REQUIRED_PARAMETERS = {
        "authorization_id",
        "completed_bars_only",
        "daily_boundary",
        "equality_target",
        "market_policy",
        "network_access",
        "order_quantity",
        "order_type",
        "profitability_claim",
        "reversal_behavior",
        "signal_price",
        "sma_lookback",
        "strategy_family",
    }

    def __init__(self) -> None:
        super().__init__()
        self._sma = SimpleMovingAverage(20)
        self._quantity = ""
        self._execution_bar_type: BarType | None = None
        self._daily_bar_type: BarType | None = None
        self._pending_reversal: _PendingReversal | None = None
        self.observations.update(
            {
                "daily_signal_bars": [],
                "signals": [],
                "indicator_updates": 0,
                "reversal_sequence": [],
                "execution_bar_callbacks": 0,
                "mark_price_update_count": 0,
                "latest_mark_price_update": None,
            },
        )

    @staticmethod
    def _expected_profile_parameters(profile: MarketProfile) -> dict[str, str]:
        if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
            return {
                "market_policy": "CASH_LONG_OR_FLAT_NO_BORROW_NO_SHORT",
                "order_quantity": "0.10000",
                "reversal_behavior": "NOT_APPLICABLE_SPOT_LONG_FLAT",
            }
        return {
            "market_policy": "USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING_LEVERAGE_1",
            "order_quantity": "0.100",
            "reversal_behavior": "CLOSE_TO_FLAT_CONFIRM_THEN_SEPARATE_REOPEN",
        }

    def configure_registered(self, *, strategy_spec: StrategySpec, **configuration: Any) -> None:
        parameters = dict(strategy_spec.parameters)
        if set(parameters) != self.REQUIRED_PARAMETERS:
            missing = sorted(self.REQUIRED_PARAMETERS - set(parameters))
            unknown = sorted(set(parameters) - self.REQUIRED_PARAMETERS)
            raise ValueError(
                f"registered SMA20 parameters differ; missing={missing}, unknown={unknown}",
            )
        expected = locked_sma20_parameters(strategy_spec.market_profile)
        mismatches = sorted(name for name, value in expected.items() if parameters[name] != value)
        if mismatches:
            raise ValueError("registered SMA20 contract mismatch: " + ",".join(mismatches))
        expected_instrument = (
            "BTCUSDT.BINANCE"
            if strategy_spec.market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
            else "BTCUSDT-PERP.BINANCE"
        )
        expected_signal_type = (
            f"{expected_instrument}-1-DAY-LAST-INTERNAL@1-MINUTE-EXTERNAL"
        )
        if (
            strategy_spec.instrument_id != expected_instrument
            or strategy_spec.signal_bar_types != (expected_signal_type,)
        ):
            raise ValueError("registered SMA20 Instrument or daily signal Bar type mismatch")
        signal_bar_type = configuration.get("bar_type")
        execution_bar_type = configuration.pop("execution_bar_type", None)
        if (
            not isinstance(signal_bar_type, BarType)
            or str(signal_bar_type) != expected_signal_type
            or not isinstance(execution_bar_type, BarType)
            or str(execution_bar_type) != f"{expected_instrument}-1-MINUTE-LAST-EXTERNAL"
        ):
            raise ValueError("registered SMA20 runtime Bar bindings are invalid")
        self._quantity = parameters["order_quantity"]
        self._execution_bar_type = execution_bar_type
        # Nautilus subscribes to the composite bar type, but intentionally
        # delivers the aggregated Bar with the standard target type (without
        # the ``@source`` suffix).
        self._daily_bar_type = BarType.from_str(expected_signal_type.split("@", maxsplit=1)[0])
        self._configure_runtime(plan=None, execution_bar_type=execution_bar_type, **configuration)

    def on_start(self) -> None:
        if not self._configured:
            raise RuntimeError("strategy must be configured before registration")
        assert self._bar_type is not None
        assert self._execution_bar_type is not None
        assert self._daily_bar_type is not None
        self.subscribe_bars(self._execution_bar_type)
        self.register_indicator_for_bars(self._daily_bar_type, self._sma)
        self.subscribe_bars(self._bar_type)
        if self._profile is MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING:
            assert self._instrument_id is not None
            self.subscribe_mark_prices(self._instrument_id)
            self.subscribe_funding_rates(self._instrument_id)
        self._boundary_snapshot(int(self.clock.timestamp_ns()))

    def _quantity_string(self, value: Decimal) -> str:
        return f"{value:.{self._size_precision}f}"

    def _submit_if_new(self, intent: OrderIntent, signal_bar: Bar) -> bool:
        before = len(self.observations["submitted_intents"])
        self._submit_guarded(intent, signal_bar)
        return len(self.observations["submitted_intents"]) == before + 1

    def _submit_pending_reversal_if_ready(self, observed_at_ns: int) -> None:
        pending = self._pending_reversal
        if pending is None:
            return
        self._refresh_live_order()
        if self._live_client_order_id is not None or self._signed_position() != 0:
            return
        self.observations["reversal_sequence"].append(
            {
                "event": "NATIVE_FLAT_CONFIRMED",
                "observed_at_ns": observed_at_ns,
                "signed_position": "0",
            },
        )
        side = "BUY" if pending.target is TargetState.LONG else "SELL"
        intent = OrderIntent(
            side=side,
            quantity=self._quantity,
            order_type="MARKET",
            reason=f"SMA20_REVERSAL_REOPEN_{pending.target.value}_AFTER_NATIVE_FLAT",
        )
        if self._submit_if_new(intent, pending.signal_bar):
            submitted = self.observations["submitted_intents"][-1]
            self.observations["reversal_sequence"].append(
                {
                    "event": "SEPARATE_REOPEN_SUBMITTED",
                    "observed_at_ns": observed_at_ns,
                    "client_order_id": submitted["client_order_id"],
                    "target": pending.target.value,
                },
            )
        self._pending_reversal = None

    def _act_on_target(self, target: TargetState, signal_bar: Bar) -> None:
        assert self._profile is not None
        signed = self._signed_position()
        if self._profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
            if signed < 0 or target is TargetState.SHORT:
                intent = OrderIntent("SELL", self._quantity, "MARKET", "SPOT_SHORT_REJECTED")
                self._record_guard_failure(
                    FailureCode.SPOT_SHORT_OR_BORROW_DETECTED,
                    intent=intent,
                    timestamp_ns=int(self.clock.timestamp_ns()),
                    detail="registered Spot SMA20 strategy cannot target SHORT",
                )
                return
            if target is TargetState.LONG and signed == 0:
                self._submit_guarded(
                    OrderIntent("BUY", self._quantity, "MARKET", "SMA20_TARGET_LONG"),
                    signal_bar,
                )
            elif target is TargetState.FLAT and signed > 0:
                self._submit_guarded(
                    OrderIntent(
                        "SELL",
                        self._quantity_string(signed),
                        "MARKET",
                        "SMA20_TARGET_FLAT_FROM_LONG",
                    ),
                    signal_bar,
                )
            return

        desired_sign = {
            TargetState.LONG: 1,
            TargetState.FLAT: 0,
            TargetState.SHORT: -1,
        }[target]
        current_sign = (signed > 0) - (signed < 0)
        if desired_sign == current_sign:
            return
        if signed == 0:
            side = "BUY" if target is TargetState.LONG else "SELL"
            self._submit_guarded(
                OrderIntent(side, self._quantity, "MARKET", f"SMA20_TARGET_{target.value}_FROM_FLAT"),
                signal_bar,
            )
            return
        close_side = "SELL" if signed > 0 else "BUY"
        close_intent = OrderIntent(
            close_side,
            self._quantity_string(abs(signed)),
            "MARKET",
            f"SMA20_CLOSE_TO_FLAT_BEFORE_{target.value}",
        )
        if target is not TargetState.FLAT:
            if self._pending_reversal is not None:
                self._record_guard_failure(
                    FailureCode.CONCURRENT_STRATEGY_ORDER_REJECTED,
                    intent=close_intent,
                    timestamp_ns=int(self.clock.timestamp_ns()),
                    detail="a prior close-then-reverse transition is still pending",
                )
                return
            self._pending_reversal = _PendingReversal(target=target, signal_bar=signal_bar)
        if self._submit_if_new(close_intent, signal_bar):
            submitted = self.observations["submitted_intents"][-1]
            if target is not TargetState.FLAT:
                self.observations["reversal_sequence"].append(
                    {
                        "event": "CLOSE_TO_FLAT_SUBMITTED",
                        "observed_at_ns": int(self.clock.timestamp_ns()),
                        "client_order_id": submitted["client_order_id"],
                        "position_before": str(signed),
                        "next_target": target.value,
                    },
                )
        elif target is not TargetState.FLAT:
            self._pending_reversal = None

    def on_bar(self, bar: Bar) -> None:
        assert self._daily_bar_type is not None
        if bar.bar_type != self._daily_bar_type:
            self.observations["execution_bar_callbacks"] += 1
            self._boundary_snapshot(int(self.clock.timestamp_ns()))
            self._submit_pending_reversal_if_ready(int(self.clock.timestamp_ns()))
            return

        validate_completed_utc_daily_bar(bar)
        super().on_bar(bar)
        self.observations["indicator_updates"] = self._sma.count
        self.observations["daily_signal_bars"].append(
            {
                "interval_start_ns": int(bar.ts_init) - DAY_NS,
                "interval_end_exclusive_ns": int(bar.ts_init),
                "available_at_ns": int(bar.ts_init),
                "close": str(bar.close),
                "sma_count": self._sma.count,
                "sma_initialized": self._sma.initialized,
            },
        )
        if not self._sma.initialized:
            return
        interval_start = int(bar.ts_init) - DAY_NS
        if interval_start < self._scoring_start_ns or int(bar.ts_init) > self._scoring_end_exclusive_ns:
            return
        close = Decimal(str(bar.close))
        sma = Decimal(str(self._sma.value))
        target = classify_target(close, sma, self._profile)
        self.observations["signals"].append(
            {
                "signal_bar_interval_start_ns": interval_start,
                "signal_bar_interval_end_exclusive_ns": int(bar.ts_init),
                "signal_bar_available_at_ns": int(bar.ts_init),
                "signal_timestamp_ns": int(self.clock.timestamp_ns()),
                "completed_daily_bar_count": self._sma.count,
                "close": str(close),
                "sma20": str(sma),
                "target": target.value,
            },
        )
        self._act_on_target(target, bar)

    def on_position_closed(self, event: Any) -> None:
        super().on_position_closed(event)
        self._submit_pending_reversal_if_ready(int(event.ts_event))

    def on_mark_price(self, event: Any) -> None:
        """Preserve exact coverage counters without duplicating the full 1m mark catalog.

        The immutable Dataset Release and its semantic catalog inventory are the
        row-level mark authority.  Funding-boundary marks are additionally
        captured by the runner from Nautilus's public cache.  Keeping all
        305,280 callbacks again inside ``nautilus_result.json`` would add a
        redundant presentation copy, not stronger financial evidence.
        """

        self.observations["mark_price_update_count"] += 1
        self.observations["latest_mark_price_update"] = {
            "instrument_id": str(event.instrument_id),
            "value": str(event.value),
            "ts_event": int(event.ts_event),
            "ts_init": int(event.ts_init),
        }


__all__ = [
    "BtcusdtDailyPriceVsSma20Trend",
    "DAY_NS",
    "TargetState",
    "classify_target",
    "locked_sma20_parameters",
    "locked_sma20_strategy_spec",
    "validate_completed_utc_daily_bar",
]
