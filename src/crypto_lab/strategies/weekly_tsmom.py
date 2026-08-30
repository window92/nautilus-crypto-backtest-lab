"""Registered causal weekly TSMOM28 candidates and fixed Buy-and-Hold benchmark.

Only signals and target quantities are calculated here. NautilusTrader remains
the sole owner of orders, matching, Fills, positions, accounts, fees, funding,
portfolio valuation, and PnL.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from decimal import InvalidOperation
from decimal import localcontext
from enum import StrEnum
from typing import Any

from nautilus_trader.model import Bar
from nautilus_trader.model import BarType
from nautilus_trader.model import Currency

from crypto_lab.config import MarketProfile
from crypto_lab.status import FailureCode
from crypto_lab.timestamps import unix_ns_to_utc_datetime
from crypto_lab.strategies.base import GuardedCausalStrategy
from crypto_lab.strategies.base import OrderIntent
from crypto_lab.strategies.base import StrategySpec
from crypto_lab.strategies.daily_sma_trend import DAY_NS
from crypto_lab.strategies.daily_sma_trend import TargetState
from crypto_lab.strategies.daily_sma_trend import validate_completed_utc_daily_bar


TSMOM_FULL_REGISTRATION_ID = "btcusdt_weekly_tsmom28_full_v1"
TSMOM_VOL20_REGISTRATION_ID = "btcusdt_weekly_tsmom28_vol20_v1"
BUY_AND_HOLD_REGISTRATION_ID = "buy_and_hold_1x_v1"
TSMOM_FAMILY = "BTCUSDT_WEEKLY_TSMOM28_V1"
BUY_AND_HOLD_FAMILY = "BUY_AND_HOLD_1X_V1"
LOOKBACK_CLOSES = 29
VOLATILITY_TARGET = Decimal("0.20")
ANNUALIZATION_DAYS = Decimal(365)


class TsmomCandidateMode(StrEnum):
    FULL_NOTIONAL = "TSMOM28_FULL_NOTIONAL"
    VOLATILITY_TARGET_20 = "TSMOM28_VOLATILITY_TARGET_20"


def _finite_positive(value: Decimal, *, name: str) -> Decimal:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def momentum_28d(closes: tuple[Decimal, ...]) -> Decimal:
    """Return the exact locked C[-1] / C[-29] - 1 signal."""

    if len(closes) != LOOKBACK_CLOSES:
        raise ValueError("exactly 29 completed closes are required")
    first = _finite_positive(closes[0], name="C[-29]")
    last = _finite_positive(closes[-1], name="C[-1]")
    with localcontext() as context:
        context.prec = 50
        return +(last / first - Decimal(1))


def annualized_realized_volatility_28d(closes: tuple[Decimal, ...]) -> Decimal:
    """Use 28 causal Decimal log returns, population std (ddof=0), sqrt(365)."""

    if len(closes) != LOOKBACK_CLOSES:
        raise ValueError("exactly 29 completed closes are required")
    if any(not value.is_finite() or value <= 0 for value in closes):
        raise ValueError("all completed closes must be finite and positive")
    try:
        with localcontext() as context:
            context.prec = 50
            returns = tuple((right / left).ln() for left, right in zip(closes, closes[1:]))
            mean = sum(returns, Decimal(0)) / Decimal(len(returns))
            variance = sum(((value - mean) ** 2 for value in returns), Decimal(0)) / Decimal(
                len(returns),
            )
            result = variance.sqrt() * ANNUALIZATION_DAYS.sqrt()
    except (InvalidOperation, ZeroDivisionError) as exc:
        raise ValueError("realized volatility calculation failed closed") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("realized volatility is invalid")
    return result


def volatility_target_fraction(annualized_volatility: Decimal) -> Decimal:
    """Return the locked unlevered 20% target fraction, or zero at zero vol."""

    if not annualized_volatility.is_finite() or annualized_volatility < 0:
        raise ValueError("annualized volatility must be finite and non-negative")
    if annualized_volatility == 0:
        return Decimal(0)
    with localcontext() as context:
        context.prec = 50
        return +min(Decimal(1), VOLATILITY_TARGET / annualized_volatility)


def weekly_target(momentum: Decimal, profile: MarketProfile) -> TargetState:
    """Map the frozen strict momentum sign to the profile-specific direction."""

    if not momentum.is_finite():
        raise ValueError("momentum must be finite")
    if momentum > 0:
        return TargetState.LONG
    if momentum == 0 or profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
        return TargetState.FLAT
    return TargetState.SHORT


def is_monday_utc_boundary(timestamp_ns: int) -> bool:
    if timestamp_ns < 0 or timestamp_ns % DAY_NS != 0:
        return False
    return unix_ns_to_utc_datetime(timestamp_ns).weekday() == 0


def floor_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    if not value.is_finite() or value < 0 or not increment.is_finite() or increment <= 0:
        raise ValueError("quantity and increment must be finite and non-negative/positive")
    return (value // increment) * increment


def _instrument_id(profile: MarketProfile) -> str:
    return (
        "BTCUSDT.BINANCE"
        if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        else "BTCUSDT-PERP.BINANCE"
    )


def locked_weekly_tsmom_parameters(
    registration_id: str,
    profile: MarketProfile,
) -> dict[str, str]:
    if registration_id not in {TSMOM_FULL_REGISTRATION_ID, TSMOM_VOL20_REGISTRATION_ID}:
        raise ValueError("unknown weekly TSMOM registration")
    mode = (
        TsmomCandidateMode.FULL_NOTIONAL
        if registration_id == TSMOM_FULL_REGISTRATION_ID
        else TsmomCandidateMode.VOLATILITY_TARGET_20
    )
    return {
        "annualization_days": "365",
        "authorization_id": "OWNER_STRATEGY_RESEARCH_001",
        "candidate_mode": mode.value,
        "completed_closes_required": "29",
        "daily_boundary": "UTC",
        "decision_frequency": "WEEKLY_MONDAY_00_UTC",
        "effective_insert_latency_ns": "60000000000",
        "fee_rate": "0.001",
        "log_return_count": "28" if mode is TsmomCandidateMode.VOLATILITY_TARGET_20 else "NOT_APPLICABLE",
        "market_policy": (
            "CASH_LONG_OR_FLAT_NO_BORROW_NO_SHORT"
            if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
            else "USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING_LEVERAGE_1"
        ),
        "max_gross_exposure": "1.0",
        "momentum_formula": "C[-1]/C[-29]-1",
        "network_access": "FORBIDDEN",
        "order_type": "MARKET",
        "quantity_rounding": "FLOOR_TO_EFFECTIVE_SIZE_INCREMENT",
        "real_profitability_claim": "FALSE",
        "reversal_behavior": (
            "NOT_APPLICABLE_SPOT_LONG_FLAT"
            if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
            else "CLOSE_TO_FLAT_CONFIRM_THEN_SEPARATE_REOPEN"
        ),
        "spot_cash_affordability_policy": (
            "QUOTE_NOTIONAL_CAPPED_BY_NATIVE_FREE_QUOTE_AND_INSTRUMENT_MAX_PRICE_ROUNDING_RESERVE"
            if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
            else "NOT_APPLICABLE_PERPETUAL"
        ),
        "strategy_family": TSMOM_FAMILY,
        "target_fraction_rule": (
            "1.0"
            if mode is TsmomCandidateMode.FULL_NOTIONAL
            else "min(1.0,0.20/annualized_realized_volatility)"
        ),
        "volatility_ddof": "NOT_APPLICABLE" if mode is TsmomCandidateMode.FULL_NOTIONAL else "0",
        "volatility_target": "NOT_APPLICABLE" if mode is TsmomCandidateMode.FULL_NOTIONAL else "0.20",
        "zero_volatility_behavior": (
            "NOT_APPLICABLE"
            if mode is TsmomCandidateMode.FULL_NOTIONAL
            else "ZERO_REALIZED_VOLATILITY_FLAT"
        ),
    }


def locked_weekly_tsmom_strategy_spec(
    registration_id: str,
    profile: MarketProfile,
) -> StrategySpec:
    instrument_id = _instrument_id(profile)
    parameters = locked_weekly_tsmom_parameters(registration_id, profile)
    mode = parameters["candidate_mode"]
    return StrategySpec(
        strategy_id=f"owner-strategy-research-001-{profile.value.lower()}-{registration_id}",
        strategy_version="2",
        market_profile=profile,
        instrument_id=instrument_id,
        signal_bar_types=(f"{instrument_id}-1-DAY-LAST-INTERNAL@1-MINUTE-EXTERNAL",),
        parameters=parameters,
        indicator_definitions=(
            "momentum_28d=C[-1]/C[-29]-1 over exactly 29 completed UTC daily closes",
            *(
                (
                    "annualized_realized_volatility=population_std(28 Decimal log returns)*sqrt(365)",
                )
                if mode == TsmomCandidateMode.VOLATILITY_TARGET_20.value
                else ()
            ),
        ),
        warmup_requirement="29_COMPLETED_UTC_DAILY_CLOSES_WITH_ZERO_WARMUP_ORDERS",
        sizing_rule=(
            "NATIVE_EQUITY_TIMES_1X_FLOORED_TO_EFFECTIVE_SIZE_INCREMENT"
            if mode == TsmomCandidateMode.FULL_NOTIONAL.value
            else "NATIVE_EQUITY_TIMES_LOCKED_VOL20_FRACTION_FLOORED_TO_EFFECTIVE_SIZE_INCREMENT"
        ),
        entry_rule="MONDAY_00_UTC_STRICT_28_DAY_TIME_SERIES_MOMENTUM_SIGN",
        exit_rule=(
            "SPOT_NEGATIVE_OR_ZERO_TO_FLAT"
            if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
            else "ZERO_TO_FLAT_OR_CLOSE_FLAT_CONFIRM_SEPARATE_REVERSAL"
        ),
        conflict_rule="FIRST_ELIGIBLE_INTENT",
        terminal_behavior="MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE",
        market_order_time_in_force="GTC",
    )


def locked_buy_and_hold_parameters(profile: MarketProfile, benchmark_id: str) -> dict[str, str]:
    if not benchmark_id:
        raise ValueError("benchmark_id is required")
    return {
        "authorization_id": "OWNER_STRATEGY_RESEARCH_001",
        "benchmark_id": benchmark_id,
        "effective_insert_latency_ns": "60000000000",
        "fee_rate": "0.001",
        "hold_rule": "HOLD_TO_SCORING_END_NO_SYNTHETIC_CLOSE",
        "market_policy": (
            "CASH_LONG_ONLY_NO_BORROW"
            if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
            else "USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING_LEVERAGE_1"
        ),
        "network_access": "FORBIDDEN",
        "order_type": "MARKET",
        "quantity_rounding": "FLOOR_TO_EFFECTIVE_SIZE_INCREMENT",
        "real_profitability_claim": "FALSE",
        "spot_cash_affordability_policy": (
            "QUOTE_NOTIONAL_CAPPED_BY_NATIVE_FREE_QUOTE_AND_INSTRUMENT_MAX_PRICE_ROUNDING_RESERVE"
            if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
            else "NOT_APPLICABLE_PERPETUAL"
        ),
        "strategy_family": BUY_AND_HOLD_FAMILY,
        "target_gross_exposure": "1.0",
    }


def locked_buy_and_hold_strategy_spec(
    profile: MarketProfile,
    benchmark_id: str,
) -> StrategySpec:
    instrument_id = _instrument_id(profile)
    return StrategySpec(
        strategy_id=f"owner-strategy-research-001-{profile.value.lower()}-buy-and-hold-1x",
        strategy_version="2",
        market_profile=profile,
        instrument_id=instrument_id,
        signal_bar_types=(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL",),
        parameters=locked_buy_and_hold_parameters(profile, benchmark_id),
        indicator_definitions=("FIRST_ELIGIBLE_SCORING_EXECUTION_BAR_ONLY",),
        warmup_requirement="FIRST_SCORING_MARKET_STATE_WITH_ZERO_WARMUP_ORDERS",
        sizing_rule="NATIVE_EQUITY_TIMES_1X_FLOORED_TO_EFFECTIVE_SIZE_INCREMENT",
        entry_rule="ENTER_LONG_AT_FIRST_ELIGIBLE_SCORING_MARKET_STATE_AFTER_LOCKED_LATENCY",
        exit_rule="NO_EXIT_MARK_TERMINAL_OPEN_POSITION_WITHOUT_SYNTHETIC_CLOSE",
        conflict_rule="FIRST_ELIGIBLE_INTENT",
        terminal_behavior="MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE",
        market_order_time_in_force="GTC",
    )


@dataclass(frozen=True)
class _PendingReversal:
    target: TargetState
    target_quantity: Decimal
    signal_bar: Bar
    decision_timestamp_ns: int


class _NativeEquityTargetStrategy(GuardedCausalStrategy):
    """Shared read-only native-equity sizing helpers."""

    def _quantity_text(self, value: Decimal) -> str:
        return f"{value:.{self._size_precision}f}"

    def _native_equity(self) -> Decimal:
        assert self._instrument_id is not None
        account = self.cache.account_for_venue(self._instrument_id.venue)
        if account is None:
            raise RuntimeError("native account unavailable at target decision")
        currency = Currency.from_str(self._initial_capital_currency)
        values = self.portfolio.equity(account_id=account.id)
        money = values.get(currency)
        if money is None:
            raise RuntimeError("native settlement-currency Equity unavailable")
        equity = Decimal(str(money.as_decimal()))
        if self._profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
            exposure = self.portfolio.net_exposure(
                self._instrument_id,
                account_id=account.id,
            )
            if exposure is not None:
                equity += Decimal(str(exposure.as_decimal()))
        if not equity.is_finite() or equity <= 0:
            raise RuntimeError("native target Equity is not finite and positive")
        return equity

    def _target_quantity(self, fraction: Decimal, reference_price: Decimal) -> tuple[Decimal, Decimal]:
        if not fraction.is_finite() or not Decimal(0) <= fraction <= Decimal(1):
            raise ValueError("target fraction must be finite inside [0,1]")
        price = _finite_positive(reference_price, name="reference price")
        equity = self._native_equity()
        if fraction == 0:
            return Decimal(0), equity
        denominator = price
        return floor_to_increment(equity * fraction / denominator, self._size_increment), equity

    def _eligible_delta(self, quantity: Decimal, reference_price: Decimal, *, reason: str) -> bool:
        if quantity <= 0:
            return False
        assert self._instrument_id is not None
        instrument = self.cache.instrument(self._instrument_id)
        if instrument is None:
            raise RuntimeError("native Instrument unavailable at target decision")
        notional = quantity * reference_price
        minimum = getattr(instrument, "min_notional", None)
        maximum = getattr(instrument, "max_notional", None)
        if minimum is not None and notional < Decimal(str(minimum.as_decimal())):
            self.observations["no_order_reasons"].append(
                {"reason": "TARGET_DELTA_BELOW_MIN_NOTIONAL", "detail": reason, "notional": str(notional)},
            )
            return False
        if maximum is not None and notional > Decimal(str(maximum.as_decimal())):
            self.observations["no_order_reasons"].append(
                {"reason": "TARGET_DELTA_ABOVE_MAX_NOTIONAL", "detail": reason, "notional": str(notional)},
            )
            return False
        return True

    def on_mark_price(self, event: Any) -> None:
        self.observations["mark_price_update_count"] += 1
        self.observations["latest_mark_price_update"] = {
            "instrument_id": str(event.instrument_id),
            "value": str(event.value),
            "ts_event": int(event.ts_event),
            "ts_init": int(event.ts_init),
        }


class BtcusdtWeeklyTsmom28(_NativeEquityTargetStrategy):
    """The two frozen TSMOM candidates, selected only by bound parameters."""

    IMPLEMENTATION_REVISION = "OWNER_STRATEGY_RESEARCH_001_WEEKLY_TSMOM28_V1"
    REQUIRED_PARAMETERS = set(locked_weekly_tsmom_parameters(
        TSMOM_FULL_REGISTRATION_ID,
        MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
    ))

    def __init__(self) -> None:
        super().__init__()
        self._mode = TsmomCandidateMode.FULL_NOTIONAL
        self._execution_bar_type: BarType | None = None
        self._daily_bar_type: BarType | None = None
        self._closes: list[Decimal] = []
        self._pending_reversal: _PendingReversal | None = None
        self.observations.update(
            {
                "daily_signal_bars": [],
                "signals": [],
                "weekly_decisions": [],
                "target_calculations": [],
                "no_order_reasons": [],
                "reversal_sequence": [],
                "execution_bar_callbacks": 0,
                "mark_price_update_count": 0,
                "latest_mark_price_update": None,
            },
        )

    def configure_registered(self, *, strategy_spec: StrategySpec, **configuration: Any) -> None:
        parameters = dict(strategy_spec.parameters)
        if set(parameters) != self.REQUIRED_PARAMETERS:
            raise ValueError("registered TSMOM parameters are incomplete or unknown")
        mode = TsmomCandidateMode(parameters["candidate_mode"])
        registration_id = (
            TSMOM_FULL_REGISTRATION_ID
            if mode is TsmomCandidateMode.FULL_NOTIONAL
            else TSMOM_VOL20_REGISTRATION_ID
        )
        if parameters != locked_weekly_tsmom_parameters(registration_id, strategy_spec.market_profile):
            raise ValueError("registered TSMOM contract mismatch")
        instrument_id = _instrument_id(strategy_spec.market_profile)
        expected_signal_type = f"{instrument_id}-1-DAY-LAST-INTERNAL@1-MINUTE-EXTERNAL"
        signal_bar_type = configuration.get("bar_type")
        execution_bar_type = configuration.pop("execution_bar_type", None)
        if (
            strategy_spec.instrument_id != instrument_id
            or strategy_spec.signal_bar_types != (expected_signal_type,)
            or not isinstance(signal_bar_type, BarType)
            or str(signal_bar_type) != expected_signal_type
            or not isinstance(execution_bar_type, BarType)
            or str(execution_bar_type) != f"{instrument_id}-1-MINUTE-LAST-EXTERNAL"
        ):
            raise ValueError("registered TSMOM runtime bindings are invalid")
        self._mode = mode
        self._execution_bar_type = execution_bar_type
        self._daily_bar_type = BarType.from_str(expected_signal_type.split("@", maxsplit=1)[0])
        self._configure_runtime(plan=None, execution_bar_type=execution_bar_type, **configuration)

    def on_start(self) -> None:
        if not self._configured:
            raise RuntimeError("strategy must be configured before registration")
        assert self._execution_bar_type is not None and self._bar_type is not None
        self.subscribe_bars(self._execution_bar_type)
        self.subscribe_bars(self._bar_type)
        if self._profile is MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING:
            assert self._instrument_id is not None
            self.subscribe_mark_prices(self._instrument_id)
            self.subscribe_funding_rates(self._instrument_id)
        self._boundary_snapshot(int(self.clock.timestamp_ns()))

    def _submit_if_new(
        self,
        intent: OrderIntent,
        signal_bar: Bar,
        decision_timestamp_ns: int,
    ) -> bool:
        before = len(self.observations["submitted_intents"])
        self._submit_guarded(
            intent,
            signal_bar,
            decision_timestamp_ns=decision_timestamp_ns,
        )
        return len(self.observations["submitted_intents"]) == before + 1

    def _submit_pending_reversal_if_ready(self, observed_at_ns: int) -> None:
        pending = self._pending_reversal
        if pending is None:
            return
        self._refresh_live_order()
        if self._live_client_order_id is not None or self._signed_position() != 0:
            return
        self.observations["reversal_sequence"].append(
            {"event": "NATIVE_FLAT_CONFIRMED", "observed_at_ns": observed_at_ns, "signed_position": "0"},
        )
        side = "BUY" if pending.target is TargetState.LONG else "SELL"
        intent = OrderIntent(
            side,
            self._quantity_text(pending.target_quantity),
            "MARKET",
            f"TSMOM28_REVERSAL_REOPEN_{pending.target.value}_AFTER_NATIVE_FLAT",
        )
        if self._submit_if_new(intent, pending.signal_bar, pending.decision_timestamp_ns):
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

    def _act_on_target(
        self,
        target: TargetState,
        target_quantity: Decimal,
        signal_bar: Bar,
        decision_timestamp_ns: int,
        reference_price: Decimal,
    ) -> None:
        assert self._profile is not None
        signed = self._signed_position()
        desired = {
            TargetState.LONG: target_quantity,
            TargetState.FLAT: Decimal(0),
            TargetState.SHORT: -target_quantity,
        }[target]
        if self._profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY and desired < 0:
            intent = OrderIntent("SELL", self._quantity_text(abs(desired)), "MARKET", "SPOT_SHORT_REJECTED")
            self._record_guard_failure(
                FailureCode.SPOT_SHORT_OR_BORROW_DETECTED,
                intent=intent,
                timestamp_ns=int(self.clock.timestamp_ns()),
                detail="weekly TSMOM Spot candidate cannot target SHORT",
            )
            return
        if desired == signed:
            self.observations["no_order_reasons"].append(
                {"reason": "TARGET_ALREADY_SATISFIED", "decision_timestamp_ns": decision_timestamp_ns},
            )
            return

        current_sign = (signed > 0) - (signed < 0)
        desired_sign = (desired > 0) - (desired < 0)
        if current_sign and desired_sign and current_sign != desired_sign:
            close_side = "SELL" if signed > 0 else "BUY"
            close_intent = OrderIntent(
                close_side,
                self._quantity_text(abs(signed)),
                "MARKET",
                f"TSMOM28_CLOSE_TO_FLAT_BEFORE_{target.value}",
            )
            if self._pending_reversal is not None:
                self._record_guard_failure(
                    FailureCode.CONCURRENT_STRATEGY_ORDER_REJECTED,
                    intent=close_intent,
                    timestamp_ns=int(self.clock.timestamp_ns()),
                    detail="prior close-then-reverse transition remains pending",
                )
                return
            self._pending_reversal = _PendingReversal(
                target,
                abs(desired),
                signal_bar,
                decision_timestamp_ns,
            )
            if self._submit_if_new(close_intent, signal_bar, decision_timestamp_ns):
                submitted = self.observations["submitted_intents"][-1]
                self.observations["reversal_sequence"].append(
                    {
                        "event": "CLOSE_TO_FLAT_SUBMITTED",
                        "observed_at_ns": int(self.clock.timestamp_ns()),
                        "client_order_id": submitted["client_order_id"],
                        "position_before": str(signed),
                        "next_target": target.value,
                    },
                )
            else:
                self._pending_reversal = None
            return

        delta = desired - signed
        if delta == 0:
            return
        quantity = abs(delta)
        if not self._eligible_delta(quantity, reference_price, reason="weekly target delta"):
            return
        side = "BUY" if delta > 0 else "SELL"
        self._submit_guarded(
            OrderIntent(side, self._quantity_text(quantity), "MARKET", f"TSMOM28_REBALANCE_TO_{target.value}"),
            signal_bar,
            decision_timestamp_ns=decision_timestamp_ns,
            spot_quote_notional=(
                quantity * reference_price
                if self._profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
                and side == "BUY"
                else None
            ),
        )

    def on_bar(self, bar: Bar) -> None:
        assert self._daily_bar_type is not None
        if bar.bar_type != self._daily_bar_type:
            self.observations["execution_bar_callbacks"] += 1
            self._boundary_snapshot(int(self.clock.timestamp_ns()))
            self._submit_pending_reversal_if_ready(int(self.clock.timestamp_ns()))
            return

        validate_completed_utc_daily_bar(bar)
        GuardedCausalStrategy.on_bar(self, bar)
        close = Decimal(str(bar.close))
        _finite_positive(close, name="daily close")
        self._closes.append(close)
        if len(self._closes) > LOOKBACK_CLOSES:
            self._closes.pop(0)
        end_ns = int(bar.ts_init)
        self.observations["daily_signal_bars"].append(
            {
                "interval_start_ns": end_ns - DAY_NS,
                "interval_end_exclusive_ns": end_ns,
                "available_at_ns": end_ns,
                "close": str(close),
                "completed_close_count": min(len(self._closes), LOOKBACK_CLOSES),
            },
        )
        if (
            len(self._closes) != LOOKBACK_CLOSES
            or not is_monday_utc_boundary(end_ns)
            or end_ns < self._scoring_start_ns
            or end_ns >= self._scoring_end_exclusive_ns
        ):
            return
        closes = tuple(self._closes)
        momentum = momentum_28d(closes)
        target = weekly_target(momentum, self._profile)
        volatility: Decimal | None = None
        if self._mode is TsmomCandidateMode.VOLATILITY_TARGET_20:
            volatility = annualized_realized_volatility_28d(closes)
            fraction = volatility_target_fraction(volatility)
            if volatility == 0:
                target = TargetState.FLAT
                zero_vol_reason = "ZERO_REALIZED_VOLATILITY_FLAT"
            else:
                zero_vol_reason = "NOT_APPLICABLE"
        else:
            fraction = Decimal(1)
            zero_vol_reason = "NOT_APPLICABLE"
        if target is TargetState.FLAT:
            fraction = Decimal(0)
        target_quantity, native_equity = self._target_quantity(fraction, close)
        signal = {
            "signal_bar_interval_start_ns": end_ns - DAY_NS,
            "signal_bar_interval_end_exclusive_ns": end_ns,
            "signal_bar_available_at_ns": end_ns,
            "signal_timestamp_ns": int(self.clock.timestamp_ns()),
            "decision_timestamp_ns": end_ns,
            "completed_close_count": LOOKBACK_CLOSES,
            "momentum_28d": str(momentum),
            "annualized_realized_volatility": "NOT_APPLICABLE" if volatility is None else str(volatility),
            "target_fraction": str(fraction),
            "target": target.value,
            "zero_volatility_disposition": zero_vol_reason,
        }
        self.observations["signals"].append(signal)
        self.observations["weekly_decisions"].append(signal)
        self.observations["target_calculations"].append(
            {
                "decision_timestamp_ns": end_ns,
                "native_equity": str(native_equity),
                "reference_close": str(close),
                "target_fraction": str(fraction),
                "target_quantity": str(target_quantity),
                "size_increment": str(self._size_increment),
            },
        )
        self._act_on_target(target, target_quantity, bar, end_ns, close)

    def on_position_closed(self, event: Any) -> None:
        super().on_position_closed(event)
        self._submit_pending_reversal_if_ready(int(event.ts_event))


class BtcusdtBuyAndHold1x(_NativeEquityTargetStrategy):
    """Fixed registered benchmark, excluded from the candidate budget."""

    REGISTRATION_ID = BUY_AND_HOLD_REGISTRATION_ID
    IMPLEMENTATION_REVISION = "OWNER_STRATEGY_RESEARCH_001_BUY_AND_HOLD_1X_V1"
    REQUIRED_PARAMETERS = set(locked_buy_and_hold_parameters(
        MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        "PLACEHOLDER",
    ))

    def __init__(self) -> None:
        super().__init__()
        self._entry_attempted = False
        self.observations.update(
            {
                "benchmark_entries": [],
                "no_order_reasons": [],
                "execution_bar_callbacks": 0,
                "mark_price_update_count": 0,
                "latest_mark_price_update": None,
            },
        )

    def configure_registered(self, *, strategy_spec: StrategySpec, **configuration: Any) -> None:
        parameters = dict(strategy_spec.parameters)
        if set(parameters) != self.REQUIRED_PARAMETERS:
            raise ValueError("registered benchmark parameters are incomplete or unknown")
        expected = locked_buy_and_hold_parameters(
            strategy_spec.market_profile,
            parameters["benchmark_id"],
        )
        instrument_id = _instrument_id(strategy_spec.market_profile)
        expected_bar_type = f"{instrument_id}-1-MINUTE-LAST-EXTERNAL"
        execution_bar_type = configuration.pop("execution_bar_type", None)
        if (
            parameters != expected
            or strategy_spec.instrument_id != instrument_id
            or strategy_spec.signal_bar_types != (expected_bar_type,)
            or str(configuration.get("bar_type")) != expected_bar_type
            or str(execution_bar_type) != expected_bar_type
        ):
            raise ValueError("registered benchmark runtime bindings are invalid")
        self._configure_runtime(plan=None, execution_bar_type=execution_bar_type, **configuration)

    def on_start(self) -> None:
        if not self._configured or self._bar_type is None:
            raise RuntimeError("benchmark must be configured before registration")
        self.subscribe_bars(self._bar_type)
        if self._profile is MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING:
            assert self._instrument_id is not None
            self.subscribe_mark_prices(self._instrument_id)
            self.subscribe_funding_rates(self._instrument_id)
        self._boundary_snapshot(int(self.clock.timestamp_ns()))

    def on_bar(self, bar: Bar) -> None:
        self.observations["execution_bar_callbacks"] += 1
        self._boundary_snapshot(int(self.clock.timestamp_ns()))
        self._refresh_live_order()
        if self._entry_attempted:
            return
        interval_end = int(bar.ts_init)
        interval_start = interval_end - int(bar.bar_type.spec.get_interval_ns())
        if interval_start < self._scoring_start_ns or interval_end >= self._scoring_end_exclusive_ns:
            return
        close = Decimal(str(bar.close))
        quantity, native_equity = self._target_quantity(Decimal(1), close)
        self._entry_attempted = True
        if not self._eligible_delta(quantity, close, reason="benchmark initial target"):
            return
        self.observations["benchmark_entries"].append(
            {
                "decision_timestamp_ns": interval_end,
                "signal_bar_interval_start_ns": interval_start,
                "native_equity": str(native_equity),
                "reference_close": str(close),
                "target_quantity": str(quantity),
            },
        )
        self._submit_guarded(
            OrderIntent("BUY", self._quantity_text(quantity), "MARKET", "BUY_AND_HOLD_1X_INITIAL_ENTRY"),
            bar,
            spot_quote_notional=(
                quantity * close
                if self._profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
                else None
            ),
        )


__all__ = [
    "ANNUALIZATION_DAYS",
    "BUY_AND_HOLD_FAMILY",
    "BUY_AND_HOLD_REGISTRATION_ID",
    "BtcusdtBuyAndHold1x",
    "BtcusdtWeeklyTsmom28",
    "LOOKBACK_CLOSES",
    "TSMOM_FAMILY",
    "TSMOM_FULL_REGISTRATION_ID",
    "TSMOM_VOL20_REGISTRATION_ID",
    "TsmomCandidateMode",
    "annualized_realized_volatility_28d",
    "floor_to_increment",
    "is_monday_utc_boundary",
    "locked_buy_and_hold_parameters",
    "locked_buy_and_hold_strategy_spec",
    "locked_weekly_tsmom_parameters",
    "locked_weekly_tsmom_strategy_spec",
    "momentum_28d",
    "volatility_target_fraction",
    "weekly_target",
]
