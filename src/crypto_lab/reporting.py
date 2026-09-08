"""Read-only M4 diagnostics and reporting from persisted immutable evidence."""

from __future__ import annotations

import os
import tempfile
from dataclasses import fields
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from decimal import ROUND_HALF_EVEN
from decimal import localcontext
from pathlib import Path
from typing import Any

from crypto_lab.config import NOT_APPLICABLE
from crypto_lab.config import StrictModel
from crypto_lab.config import _freeze_field
from crypto_lab.config import _require_nonempty
from crypto_lab.config import _require_sha256
from crypto_lab.config import _require_utc
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.native_metrics import NativeCalmarQualification
from crypto_lab.research import ClaimEvaluation
from crypto_lab.research import MonteCarloResult
from crypto_lab.research import MonteCarloStatus
from crypto_lab.research import ResearchEligibility
from crypto_lab.research import ResearchError
from crypto_lab.research import ResearchProtocol
from crypto_lab.research import SampleAdequacy
from crypto_lab.research import TrialRecord
from crypto_lab.research import TrialState
from crypto_lab.research import CompletedTradeSeries
from crypto_lab.status import FailureCode


QUANTUM = Decimal("0.00000001")
OFFICIAL_ANNUALIZATION_DAYS = Decimal("365.2425")
OFFICIAL_RISK_MINIMUM_SAMPLE_COUNT = 30
OFFICIAL_EQUITY_OBSERVATION_BASIS = (
    "DAILY_MARKED_NATIVE_PORTFOLIO_EQUITY_SCORING_ONLY"
)
NATIVE_STATISTICS_DIAGNOSTIC_ROLE = "DIAGNOSTIC_ONLY_NOT_OFFICIAL_METRIC_INPUT"

REQUIRED_SCIENTIFIC_LIMITATIONS = (
    "BAR_BASED_EXECUTION_NO_ORDER_BOOK_SPREAD_DEPTH_OR_QUEUE",
    "HISTORICAL_ACCOUNT_FEE_TIER_NOT_PROVEN",
    "HISTORICAL_EXCHANGE_FILTERS_NOT_FULLY_PROVEN",
    "LIQUIDATION_SIMULATION_NOT_AVAILABLE",
    "PERPETUAL_LEVERAGE_FIXED_AT_ONE_IN_V1",
    "TERMINAL_OPEN_POSITION_IS_MARKED_NOT_ACTUALLY_CLOSED",
    "DAILY_DRAWDOWN_DOES_NOT_CAPTURE_INTRADAY_DRAWDOWN",
    "SINGLE_INSTRUMENT_BTCUSDT_ONLY",
    "DEVELOPMENT_ONLY_DATA",
    "FINAL_HOLDOUT_NOT_USED",
    "NO_PROFITABILITY_AUTHORIZATION",
    "NOT_VALIDATED_FOR_LIVE_TRADING",
)


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(QUANTUM, rounding=ROUND_HALF_EVEN)


class EquityObservation(StrictModel):
    timestamp: datetime
    equity: Decimal

    def __post_init__(self) -> None:
        _require_utc(self.timestamp, "equity.timestamp")
        if not self.equity.is_finite():
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "Equity must be finite")


class DailyReturnObservation(StrictModel):
    timestamp: datetime
    return_value: Decimal

    def __post_init__(self) -> None:
        _require_utc(self.timestamp, "daily_return.timestamp")
        if not self.return_value.is_finite():
            raise ResearchError(
                FailureCode.EVIDENCE_INCOMPLETE,
                "daily portfolio return must be finite",
            )


class DrawdownObservation(StrictModel):
    timestamp: datetime
    drawdown: Decimal

    def __post_init__(self) -> None:
        _require_utc(self.timestamp, "drawdown.timestamp")
        if not self.drawdown.is_finite() or self.drawdown < 0:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "drawdown must be finite and non-negative")


class DrawdownEpisode(StrictModel):
    start_utc: datetime
    end_utc: datetime
    duration_seconds: int
    max_drawdown: Decimal
    open_at_terminal: bool

    def __post_init__(self) -> None:
        _require_utc(self.start_utc, "drawdown_episode.start")
        _require_utc(self.end_utc, "drawdown_episode.end")
        if self.end_utc < self.start_utc or self.duration_seconds < 0:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "invalid drawdown episode duration")


class CalendarYearReturn(StrictModel):
    year: int
    return_value: Decimal | str
    partial_year: bool
    first_observation_utc: datetime
    last_observation_utc: datetime

    def __post_init__(self) -> None:
        _require_utc(self.first_observation_utc, "calendar_year.first")
        _require_utc(self.last_observation_utc, "calendar_year.last")
        if not 1 <= self.year <= 9999:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "invalid calendar year")


class DiagnosticValue(StrictModel):
    status: str
    value: str
    unit: str
    formula: str
    inputs: tuple[str, ...]
    source: str
    undefined_reason: str

    def __post_init__(self) -> None:
        if self.status not in {"CALCULATED", "NATIVE", "UNDEFINED", NOT_APPLICABLE}:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "unknown diagnostic value status")
        for name in ("value", "unit", "formula", "source", "undefined_reason"):
            _require_nonempty(getattr(self, name), f"diagnostic.{name}")
        if self.status == "UNDEFINED":
            if self.value != "UNDEFINED" or self.undefined_reason == NOT_APPLICABLE:
                raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "undefined diagnostic needs a reason")
        elif self.undefined_reason != NOT_APPLICABLE:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "defined diagnostic cannot have undefined reason")


def _calculated(
    value: Decimal | int,
    *,
    unit: str,
    formula: str,
    inputs: tuple[str, ...],
    source: str = "PROJECT_FALLBACK_ABSENT_NAUTILUS_NATIVE_METRIC",
) -> DiagnosticValue:
    encoded = format(_rounded(value), ".8f") if isinstance(value, Decimal) else str(value)
    return DiagnosticValue(
        status="CALCULATED",
        value=encoded,
        unit=unit,
        formula=formula,
        inputs=inputs,
        source=source,
        undefined_reason=NOT_APPLICABLE,
    )


def _native(value: str, *, unit: str, metric: str) -> DiagnosticValue:
    try:
        parsed = Decimal(value)
    except Exception as exc:
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, f"native {metric} is not Decimal data") from exc
    if not parsed.is_finite():
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, f"native {metric} must be finite")
    return DiagnosticValue(
        status="NATIVE",
        value=value,
        unit=unit,
        formula="NAUTILUS_NATIVE_REPORT_OR_STATISTIC",
        inputs=(metric,),
        source="NAUTILUS_NATIVE",
        undefined_reason=NOT_APPLICABLE,
    )


def _undefined(
    *,
    unit: str,
    formula: str,
    inputs: tuple[str, ...],
    reason: str,
    source: str = "PROJECT_FALLBACK_ABSENT_NAUTILUS_NATIVE_METRIC",
) -> DiagnosticValue:
    return DiagnosticValue(
        status="UNDEFINED",
        value="UNDEFINED",
        unit=unit,
        formula=formula,
        inputs=inputs,
        source=source,
        undefined_reason=reason,
    )


class PerformanceDiagnostics(StrictModel):
    schema_version: int
    diagnostics_id: str
    run_id: str
    input_evidence_hashes: dict[str, str]
    equity_observation_basis: str
    scored_start: datetime
    scoring_end_exclusive: datetime
    settlement_currency: str
    valuation_frequency: str
    annualization_days: Decimal
    minimum_risk_sample_count: int
    native_statistics_role: str
    daily_return_sample_count: int
    intraday_drawdown_captured: bool
    scientific_limitations: tuple[str, ...]
    ending_equity: DiagnosticValue
    total_return: DiagnosticValue
    cagr: DiagnosticValue
    sharpe: DiagnosticValue
    sortino: DiagnosticValue
    fees: DiagnosticValue
    funding: DiagnosticValue
    realized_pnl: DiagnosticValue
    unrealized_pnl: DiagnosticValue
    total_pnl: DiagnosticValue
    calendar_year_returns: tuple[CalendarYearReturn, ...]
    max_drawdown: DiagnosticValue
    max_drawdown_duration: DiagnosticValue
    average_drawdown_duration: DiagnosticValue
    time_under_water: DiagnosticValue
    completed_trade_count: DiagnosticValue
    win_rate: DiagnosticValue
    max_consecutive_losses: DiagnosticValue
    equity_curve: tuple[EquityObservation, ...]
    daily_returns: tuple[DailyReturnObservation, ...]
    drawdown_curve: tuple[DrawdownObservation, ...]
    drawdown_episodes: tuple[DrawdownEpisode, ...]
    benchmark_comparison: DiagnosticValue
    sample_adequacy: SampleAdequacy
    monte_carlo_status: MonteCarloStatus
    claim_scope: str

    def __post_init__(self) -> None:
        if self.schema_version != 2:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "unknown diagnostics schema")
        _require_sha256(self.diagnostics_id, "diagnostics.diagnostics_id")
        _require_nonempty(self.run_id, "diagnostics.run_id")
        _require_nonempty(self.equity_observation_basis, "diagnostics.equity_observation_basis")
        _require_utc(self.scored_start, "diagnostics.scored_start")
        _require_utc(self.scoring_end_exclusive, "diagnostics.scoring_end_exclusive")
        if self.scored_start >= self.scoring_end_exclusive:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "diagnostics scored interval is empty")
        if (
            self.equity_observation_basis != OFFICIAL_EQUITY_OBSERVATION_BASIS
            or self.valuation_frequency != "DAILY_MARKED_PORTFOLIO_EQUITY_UTC"
            or self.annualization_days != OFFICIAL_ANNUALIZATION_DAYS
            or self.minimum_risk_sample_count != OFFICIAL_RISK_MINIMUM_SAMPLE_COUNT
            or self.native_statistics_role != NATIVE_STATISTICS_DIAGNOSTIC_ROLE
            or self.daily_return_sample_count != len(self.daily_returns)
            or self.intraday_drawdown_captured
            or self.scientific_limitations != REQUIRED_SCIENTIFIC_LIMITATIONS
        ):
            raise ResearchError(
                FailureCode.PERFORMANCE_METRICS_INVALID,
                "official performance-metric basis is invalid",
            )
        _validate_official_performance_metrics(self)
        for identity in self.input_evidence_hashes.values():
            _require_sha256(identity, "diagnostics.input_evidence_hashes")
        if canonical_sha256(self.material_payload()) != self.diagnostics_id:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "diagnostics content identity mismatch")
        _freeze_field(self, "input_evidence_hashes")

    def material_payload(self) -> dict[str, Any]:
        return {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if field.name != "diagnostics_id"
        }

    @classmethod
    def create(cls, **values: Any) -> PerformanceDiagnostics:
        material = {"schema_version": 2, **values}
        return cls(diagnostics_id=canonical_sha256(material), **material)


class NativeResearchMetricsReadiness(StrictModel):
    """Additive reporting contract for native completed-position metrics."""

    schema_version: int
    readiness_id: str
    run_id: str
    native_sequence_evidence_sha256: str
    completed_native_units: DiagnosticValue
    average_trade_realized_pnl: DiagnosticValue
    average_trade_realized_return: DiagnosticValue
    gross_pnl: DiagnosticValue
    calmar: DiagnosticValue
    calmar_qualification_id: str
    calmar_input_returns_sha256: str
    calmar_returns_basis: str
    sample_adequacy: SampleAdequacy
    monte_carlo_input_status: str
    terminal_open_position_excluded: bool
    historical_run_evidence_mutated: bool

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "unknown native metrics readiness schema")
        _require_sha256(self.readiness_id, "native_metrics_readiness.readiness_id")
        _require_sha256(
            self.native_sequence_evidence_sha256,
            "native_metrics_readiness.native_sequence_evidence_sha256",
        )
        _require_sha256(
            self.calmar_qualification_id,
            "native_metrics_readiness.calmar_qualification_id",
        )
        _require_sha256(
            self.calmar_input_returns_sha256,
            "native_metrics_readiness.calmar_input_returns_sha256",
        )
        _require_nonempty(self.run_id, "native_metrics_readiness.run_id")
        _require_nonempty(
            self.calmar_returns_basis,
            "native_metrics_readiness.calmar_returns_basis",
        )
        if self.monte_carlo_input_status not in {
            "NATIVE_NET_COMPLETED_UNIT_SEQUENCE_READY",
            "MC_LOW_CONFIDENCE",
        }:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "unknown Monte Carlo input readiness")
        if self.historical_run_evidence_mutated:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "historical Run evidence cannot be mutated")
        if not self.terminal_open_position_excluded:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE,
                "terminal open Position must be excluded from completed native units",
            )
        if canonical_sha256(self.material_payload()) != self.readiness_id:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "native metrics readiness identity mismatch")

    def material_payload(self) -> dict[str, Any]:
        return {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if field.name != "readiness_id"
        }

    @classmethod
    def create(cls, **values: Any) -> NativeResearchMetricsReadiness:
        material = {"schema_version": 1, **values}
        return cls(readiness_id=canonical_sha256(material), **material)


def generate_native_research_metrics_readiness(
    *,
    run_id: str,
    completed_trades: CompletedTradeSeries,
    sample_adequacy: SampleAdequacy,
    native_calmar: NativeCalmarQualification,
    terminal_open_position_excluded: bool,
) -> NativeResearchMetricsReadiness:
    """Report only metrics carried by the native completed-position sequence."""

    if completed_trades.stable_native_sequence:
        assert isinstance(completed_trades.native_completed_unit_count, int)
        count = completed_trades.native_completed_unit_count
        completed_count = _native(
            str(count),
            unit="NAUTILUS_NATIVE_COMPLETED_POSITIONS",
            metric="cache.position_snapshots()/cache.positions_closed()",
        )
        if count:
            average_pnl = _calculated(
                sum(completed_trades.realized_pnl_outcomes, Decimal(0)) / Decimal(count),
                unit=completed_trades.settlement_currency,
                formula="mean(native Position.realized_pnl for completed Position units)",
                inputs=("native_completed_position_sequence",),
                source="NAUTILUS_NATIVE_POSITION_REALIZED_PNL_SEQUENCE",
            )
            average_return = _calculated(
                sum(completed_trades.realized_returns, Decimal(0)) / Decimal(count),
                unit="ratio",
                formula="mean(native Position.realized_return for completed Position units)",
                inputs=("native_completed_position_sequence",),
                source="NAUTILUS_NATIVE_POSITION_REALIZED_RETURN_SEQUENCE",
            )
        else:
            average_pnl = _undefined(
                unit=completed_trades.settlement_currency,
                formula="mean(native Position.realized_pnl for completed Position units)",
                inputs=("native_completed_position_sequence",),
                reason="average trade is undefined for zero native completed positions",
            )
            average_return = _undefined(
                unit="ratio",
                formula="mean(native Position.realized_return for completed Position units)",
                inputs=("native_completed_position_sequence",),
                reason="average trade is undefined for zero native completed positions",
            )
    else:
        completed_count = _undefined(
            unit="NAUTILUS_NATIVE_COMPLETED_POSITIONS",
            formula="count(persisted Nautilus native completed Position units)",
            inputs=("native_completed_position_sequence",),
            reason="stable native completed-position sequence unavailable",
        )
        average_pnl = _undefined(
            unit=completed_trades.settlement_currency,
            formula="mean(native Position.realized_pnl for completed Position units)",
            inputs=("native_completed_position_sequence",),
            reason="native completed-unit realized PnL sequence unavailable",
        )
        average_return = _undefined(
            unit="ratio",
            formula="mean(native Position.realized_return for completed Position units)",
            inputs=("native_completed_position_sequence",),
            reason="native completed-unit realized-return sequence unavailable",
        )

    gross = _undefined(
        unit=completed_trades.settlement_currency,
        formula="NAUTILUS_NATIVE_GROSS_PNL",
        inputs=("native Position.realized_pnl", "native commissions", "native funding"),
        reason="UNDEFINED_NATIVE_GROSS_PNL_NOT_EXPOSED",
    )
    calmar = (
        _native(native_calmar.value, unit="ratio", metric="CalmarRatio(252)")
        if native_calmar.status == "NATIVE"
        else _undefined(
            unit="ratio",
            formula="NATIVE_CAGR_252 / abs(NATIVE_MAX_DRAWDOWN)",
            inputs=("native portfolio daily returns",),
            reason=native_calmar.undefined_reason,
        )
    )
    monte_carlo_status = (
        "NATIVE_NET_COMPLETED_UNIT_SEQUENCE_READY"
        if completed_trades.stable_native_sequence
        and completed_trades.unambiguous_net_after_cost
        and bool(completed_trades.net_outcomes)
        else "MC_LOW_CONFIDENCE"
    )
    return NativeResearchMetricsReadiness.create(
        run_id=run_id,
        native_sequence_evidence_sha256=completed_trades.evidence_sha256,
        completed_native_units=completed_count,
        average_trade_realized_pnl=average_pnl,
        average_trade_realized_return=average_return,
        gross_pnl=gross,
        calmar=calmar,
        calmar_qualification_id=native_calmar.qualification_id,
        calmar_input_returns_sha256=native_calmar.input_returns_sha256,
        calmar_returns_basis=native_calmar.returns_basis,
        sample_adequacy=sample_adequacy,
        monte_carlo_input_status=monte_carlo_status,
        terminal_open_position_excluded=terminal_open_position_excluded,
        historical_run_evidence_mutated=False,
    )


def _drawdown_state(
    observations: tuple[EquityObservation, ...],
    scoring_end_exclusive: datetime,
) -> tuple[tuple[DrawdownObservation, ...], tuple[DrawdownEpisode, ...]]:
    peak = observations[0].equity
    current_start: datetime | None = None
    current_max = Decimal(0)
    curve: list[DrawdownObservation] = []
    episodes: list[DrawdownEpisode] = []
    for observation in observations:
        if observation.equity >= peak:
            if current_start is not None:
                duration = int((observation.timestamp - current_start).total_seconds())
                episodes.append(
                    DrawdownEpisode(
                        start_utc=current_start,
                        end_utc=observation.timestamp,
                        duration_seconds=duration,
                        max_drawdown=_rounded(current_max),
                        open_at_terminal=False,
                    ),
                )
                current_start = None
                current_max = Decimal(0)
            peak = observation.equity
            drawdown = Decimal(0)
        else:
            drawdown = (peak - observation.equity) / peak if peak > 0 else Decimal(0)
            if current_start is None:
                current_start = observation.timestamp
            current_max = max(current_max, drawdown)
        curve.append(DrawdownObservation(timestamp=observation.timestamp, drawdown=_rounded(drawdown)))
    if current_start is not None:
        duration = int((scoring_end_exclusive - current_start).total_seconds())
        episodes.append(
            DrawdownEpisode(
                start_utc=current_start,
                end_utc=scoring_end_exclusive,
                duration_seconds=duration,
                max_drawdown=_rounded(current_max),
                open_at_terminal=True,
            ),
        )
    return tuple(curve), tuple(episodes)


def _calendar_returns(
    observations: tuple[EquityObservation, ...],
    scored_start: datetime,
    scoring_end_exclusive: datetime,
) -> tuple[CalendarYearReturn, ...]:
    by_year: dict[int, list[EquityObservation]] = {}
    for observation in observations:
        by_year.setdefault(observation.timestamp.year, []).append(observation)
    result: list[CalendarYearReturn] = []
    for year, items in sorted(by_year.items()):
        first, last = items[0], items[-1]
        value: Decimal | str = (
            _rounded(last.equity / first.equity - 1)
            if first.equity > 0
            else "UNDEFINED"
        )
        year_start = datetime(year, 1, 1, tzinfo=UTC)
        year_end = datetime(year + 1, 1, 1, tzinfo=UTC)
        result.append(
            CalendarYearReturn(
                year=year,
                return_value=value,
                partial_year=scored_start > year_start or scoring_end_exclusive < year_end,
                first_observation_utc=first.timestamp,
                last_observation_utc=last.timestamp,
            ),
        )
    return tuple(result)


def _official_daily_returns(
    observations: tuple[EquityObservation, ...],
) -> tuple[tuple[DailyReturnObservation, ...], str | None]:
    result: list[DailyReturnObservation] = []
    for previous, current in zip(observations, observations[1:], strict=False):
        if previous.equity <= 0:
            return (
                (),
                "UNDEFINED_NON_POSITIVE_PREVIOUS_DAILY_EQUITY;"
                f"timestamp={previous.timestamp.isoformat()}",
            )
        result.append(
            DailyReturnObservation(
                timestamp=current.timestamp,
                return_value=current.equity / previous.equity - Decimal(1),
            ),
        )
    return tuple(result), None


def _official_risk_metric(
    returns: tuple[DailyReturnObservation, ...],
    *,
    metric: str,
    invalid_returns_reason: str | None,
) -> DiagnosticValue:
    formula = {
        "SHARPE": (
            "sqrt(365.2425) * mean(daily_marked_portfolio_returns) / "
            "sample_stddev(daily_marked_portfolio_returns)"
        ),
        "SORTINO": (
            "sqrt(365.2425) * mean(daily_marked_portfolio_returns) / "
            "sqrt(mean(min(daily_return,0)^2))"
        ),
    }[metric]
    inputs = ("official_daily_marked_portfolio_equity",)
    if invalid_returns_reason is not None:
        return _undefined(
            unit="annualized_ratio",
            formula=formula,
            inputs=inputs,
            reason=invalid_returns_reason,
            source="OFFICIAL_DAILY_MARKED_PORTFOLIO_EQUITY",
        )
    if len(returns) < OFFICIAL_RISK_MINIMUM_SAMPLE_COUNT:
        return _undefined(
            unit="annualized_ratio",
            formula=formula,
            inputs=inputs,
            reason=(
                "INELIGIBLE_MINIMUM_30_DAILY_RETURN_SAMPLES;"
                f"observed={len(returns)}"
            ),
            source="OFFICIAL_DAILY_MARKED_PORTFOLIO_EQUITY",
        )
    values = tuple(item.return_value for item in returns)
    mean = sum(values, Decimal(0)) / Decimal(len(values))
    with localcontext() as context:
        context.prec = 50
        annualizer = Decimal("365.2425").sqrt()
        if metric == "SHARPE":
            variance = sum(
                ((value - mean) ** 2 for value in values),
                Decimal(0),
            ) / Decimal(len(values) - 1)
            denominator = variance.sqrt()
            undefined_reason = "UNDEFINED_ZERO_DAILY_RETURN_VARIANCE"
        else:
            downside_variance = sum(
                (min(value, Decimal(0)) ** 2 for value in values),
                Decimal(0),
            ) / Decimal(len(values))
            denominator = downside_variance.sqrt()
            undefined_reason = "UNDEFINED_ZERO_DOWNSIDE_DEVIATION"
        if denominator == 0:
            return _undefined(
                unit="annualized_ratio",
                formula=formula,
                inputs=inputs,
                reason=undefined_reason,
                source="OFFICIAL_DAILY_MARKED_PORTFOLIO_EQUITY",
            )
        value = annualizer * mean / denominator
    return _calculated(
        value,
        unit="annualized_ratio",
        formula=formula,
        inputs=inputs,
        source="OFFICIAL_DAILY_MARKED_PORTFOLIO_EQUITY",
    )


def _official_cagr(
    *,
    initial_capital: Decimal,
    ending_equity: Decimal,
    scored_days: Decimal,
) -> DiagnosticValue:
    formula = "(ending_equity / starting_equity)^(365.2425 / scored_days) - 1"
    inputs = ("official_daily_marked_portfolio_equity", "scored_days")
    if ending_equity <= 0 or scored_days <= 0:
        return _undefined(
            unit="ratio_per_year",
            formula=formula,
            inputs=inputs,
            reason="UNDEFINED_NON_POSITIVE_ENDING_EQUITY_OR_SCORED_DURATION",
            source="OFFICIAL_DAILY_MARKED_PORTFOLIO_EQUITY",
        )
    with localcontext() as context:
        context.prec = 50
        annualized = (
            (ending_equity / initial_capital).ln()
            * (OFFICIAL_ANNUALIZATION_DAYS / scored_days)
        ).exp() - 1
    return _calculated(
        annualized,
        unit="ratio_per_year",
        formula=formula,
        inputs=inputs,
        source="OFFICIAL_DAILY_MARKED_PORTFOLIO_EQUITY",
    )


def _validate_official_performance_metrics(value: PerformanceDiagnostics) -> None:
    observations = value.equity_curve
    if not observations or observations[0].timestamp != value.scored_start:
        raise ResearchError(
            FailureCode.PERFORMANCE_METRICS_INVALID,
            "Official Equity curve does not begin at scoring_start",
        )
    if observations[-1].timestamp != value.scoring_end_exclusive or any(
        right.timestamp - left.timestamp != timedelta(days=1)
        for left, right in zip(observations, observations[1:], strict=False)
    ):
        raise ResearchError(
            FailureCode.PERFORMANCE_METRICS_INVALID,
            "Official Equity curve is not the exact UTC-daily scoring grid",
        )
    if observations[0].equity <= 0:
        raise ResearchError(
            FailureCode.PERFORMANCE_METRICS_INVALID,
            "Official starting Equity must be positive",
        )
    daily_returns, invalid_returns_reason = _official_daily_returns(observations)
    if value.daily_returns != daily_returns:
        raise ResearchError(
            FailureCode.PERFORMANCE_METRICS_INVALID,
            "persisted daily returns differ from marked portfolio Equity",
        )
    expected_ending = _calculated(
        observations[-1].equity,
        unit=value.settlement_currency,
        formula="last official daily marked portfolio Equity at scoring_end_exclusive",
        inputs=("official_daily_marked_portfolio_equity",),
        source="OFFICIAL_DAILY_MARKED_PORTFOLIO_EQUITY",
    )
    expected_total = _calculated(
        observations[-1].equity / observations[0].equity - 1,
        unit="ratio",
        formula="ending_equity / starting_equity - 1",
        inputs=("official_daily_marked_portfolio_equity",),
        source="OFFICIAL_DAILY_MARKED_PORTFOLIO_EQUITY",
    )
    scored_days = Decimal(
        str((value.scoring_end_exclusive - value.scored_start).total_seconds()),
    ) / Decimal(86400)
    expected_cagr = _official_cagr(
        initial_capital=observations[0].equity,
        ending_equity=observations[-1].equity,
        scored_days=scored_days,
    )
    drawdown_curve, _ = _drawdown_state(observations, value.scoring_end_exclusive)
    expected_drawdown = _calculated(
        max((item.drawdown for item in drawdown_curve), default=Decimal(0)),
        unit="ratio",
        formula="max((high_water_mark - equity) / high_water_mark)",
        inputs=("official_daily_marked_portfolio_equity",),
        source="OFFICIAL_DAILY_MARKED_PORTFOLIO_EQUITY",
    )
    expected_sharpe = _official_risk_metric(
        daily_returns,
        metric="SHARPE",
        invalid_returns_reason=invalid_returns_reason,
    )
    expected_sortino = _official_risk_metric(
        daily_returns,
        metric="SORTINO",
        invalid_returns_reason=invalid_returns_reason,
    )
    expected = {
        "ending_equity": expected_ending,
        "total_return": expected_total,
        "cagr": expected_cagr,
        "max_drawdown": expected_drawdown,
        "sharpe": expected_sharpe,
        "sortino": expected_sortino,
    }
    mismatched = [name for name, item in expected.items() if getattr(value, name) != item]
    if mismatched:
        raise ResearchError(
            FailureCode.PERFORMANCE_METRICS_INVALID,
            "Official metrics differ from daily marked portfolio Equity: "
            + ",".join(mismatched),
        )


def generate_performance_diagnostics(
    *,
    run_id: str,
    scored_start: datetime,
    scoring_end_exclusive: datetime,
    initial_capital: Decimal,
    settlement_currency: str,
    equity_observations: tuple[EquityObservation, ...],
    completed_trades: CompletedTradeSeries,
    benchmark_return: Decimal | None,
    sample_adequacy: SampleAdequacy,
    monte_carlo_status: MonteCarloStatus,
    claim_scope: str,
    input_evidence_hashes: dict[str, str],
    financial_components: dict[str, Decimal] | None = None,
) -> PerformanceDiagnostics:
    if not initial_capital.is_finite() or initial_capital <= 0:
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "initial capital must be finite and positive")
    if not equity_observations:
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "persisted Equity observations are required")
    if any(left.timestamp >= right.timestamp for left, right in zip(equity_observations, equity_observations[1:])):
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "Equity observations must be strictly chronological")
    if (
        equity_observations[0].timestamp < scored_start
        or equity_observations[-1].timestamp > scoring_end_exclusive
    ):
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "Equity observation lies outside scored interval")
    if equity_observations[0].equity != initial_capital:
        raise ResearchError(
            FailureCode.EVIDENCE_INCOMPLETE,
            "scoring must begin at frozen Initial Capital with no warm-up financial state",
        )
    if (
        equity_observations[0].timestamp != scored_start
        or equity_observations[-1].timestamp != scoring_end_exclusive
        or any(
            right.timestamp - left.timestamp != timedelta(days=1)
            for left, right in zip(
                equity_observations,
                equity_observations[1:],
                strict=False,
            )
        )
    ):
        raise ResearchError(
            FailureCode.EVIDENCE_INCOMPLETE,
            "Official Equity observations must be one complete UTC-daily scoring grid",
        )
    ending = equity_observations[-1].equity
    daily_returns, invalid_returns_reason = _official_daily_returns(equity_observations)
    official_total_return = ending / initial_capital - 1
    total_return = _calculated(
        official_total_return,
        unit="ratio",
        formula="ending_equity / starting_equity - 1",
        inputs=("official_daily_marked_portfolio_equity",),
        source="OFFICIAL_DAILY_MARKED_PORTFOLIO_EQUITY",
    )
    seconds = Decimal(str((scoring_end_exclusive - scored_start).total_seconds()))
    scored_days = seconds / Decimal(86400)
    cagr = _official_cagr(
        initial_capital=initial_capital,
        ending_equity=ending,
        scored_days=scored_days,
    )
    drawdown_curve, episodes = _drawdown_state(equity_observations, scoring_end_exclusive)
    max_drawdown_value = max((item.drawdown for item in drawdown_curve), default=Decimal(0))
    durations = [item.duration_seconds for item in episodes]
    max_duration = max(durations, default=0)
    average_duration = (
        Decimal(sum(durations)) / Decimal(len(durations))
        if durations
        else Decimal(0)
    )
    time_under_water_value = (
        Decimal(sum(durations)) / seconds if seconds > 0 else Decimal(0)
    )
    max_drawdown = _calculated(
        max_drawdown_value,
        unit="ratio",
        formula="max((high_water_mark - equity) / high_water_mark)",
        inputs=("official_daily_marked_portfolio_equity",),
        source="OFFICIAL_DAILY_MARKED_PORTFOLIO_EQUITY",
    )
    max_drawdown_duration = _calculated(
        max_duration,
        unit="seconds",
        formula="maximum drawdown episode duration including open terminal episode",
        inputs=("drawdown_episodes", "scoring_end_exclusive"),
    )
    average_drawdown_duration = _calculated(
        average_duration,
        unit="seconds",
        formula="arithmetic mean of drawdown episode durations",
        inputs=("drawdown_episodes",),
    )
    time_under_water = _calculated(
        time_under_water_value,
        unit="scored_time_fraction",
        formula="sum(drawdown_duration_seconds) / scored_elapsed_seconds",
        inputs=("drawdown_episodes", "scored_elapsed_seconds"),
    )
    if completed_trades.stable_native_sequence:
        assert isinstance(completed_trades.native_completed_unit_count, int)
        completed_trade_count = _calculated(
            completed_trades.native_completed_unit_count,
            unit="NAUTILUS_NATIVE_COMPLETED_TRADES",
            formula="count(persisted Nautilus native closed Position units)",
            inputs=("native_completed_position_sequence",),
            source="NAUTILUS_NATIVE_POSITION_SEQUENCE",
        )
    else:
        completed_trade_count = _undefined(
            unit="NAUTILUS_NATIVE_COMPLETED_TRADES",
            formula="count(persisted Nautilus native closed Position units)",
            inputs=("native_completed_position_sequence",),
            reason="stable Nautilus native completed-position sequence unavailable",
        )

    if completed_trades.unambiguous_net_after_cost:
        outcomes = completed_trades.net_outcomes
        win_rate = (
            _calculated(
                Decimal(sum(item > 0 for item in outcomes)) / Decimal(len(outcomes)),
                unit="fraction",
                formula="winning native completed trades / native completed trades",
                inputs=("native_completed_trade_sequence",),
            )
            if outcomes
            else _undefined(
                unit="fraction",
                formula="winning native completed trades / native completed trades",
                inputs=("native_completed_trade_sequence",),
                reason="win rate is undefined for zero completed trades",
            )
        )
        current = maximum = 0
        for outcome in outcomes:
            if outcome < 0:
                current += 1
                maximum = max(maximum, current)
            else:
                current = 0
        max_consecutive_losses = _calculated(
            maximum,
            unit="trades",
            formula="maximum consecutive native net trade outcomes < 0",
            inputs=("native_completed_trade_sequence",),
        )
    else:
        reason = "unambiguous Nautilus native net-after-cost outcome sequence unavailable"
        win_rate = _undefined(
            unit="fraction",
            formula="winning native completed trades / native completed trades",
            inputs=("native_completed_trade_sequence",),
            reason=reason,
        )
        max_consecutive_losses = _undefined(
            unit="trades",
            formula="maximum consecutive native net trade outcomes < 0",
            inputs=("native_completed_trade_sequence",),
            reason=reason,
        )
    benchmark = (
        _calculated(
            official_total_return - benchmark_return,
            unit="return_difference",
            formula="run_total_return - frozen_benchmark_total_return",
            inputs=("run_total_return", "benchmark_total_return"),
        )
        if benchmark_return is not None
        else _undefined(
            unit="return_difference",
            formula="run_total_return - frozen_benchmark_total_return",
            inputs=("run_total_return", "benchmark_total_return"),
            reason="frozen benchmark result is unavailable",
        )
    )
    components = dict(financial_components or {})
    if {
        "fees",
        "funding",
        "realized_pnl",
        "unrealized_pnl",
        "total_pnl",
    }.issubset(components):
        if (
            components["fees"] < 0
            or components["total_pnl"]
            != components["realized_pnl"] + components["unrealized_pnl"]
            or ending != initial_capital + components["total_pnl"]
        ):
            raise ResearchError(
                FailureCode.EVIDENCE_INCOMPLETE,
                "financial components do not reconcile to Official ending Equity",
            )

    def component(name: str, *, default_reason: str) -> DiagnosticValue:
        value = components.get(name)
        if value is None:
            return _undefined(
                unit=settlement_currency,
                formula=f"sum(native {name} evidence inside Scoring)",
                inputs=("native_run_evidence",),
                reason=default_reason,
            )
        if not value.is_finite():
            raise ResearchError(
                FailureCode.EVIDENCE_INCOMPLETE,
                f"{name} financial component is non-finite",
            )
        return _calculated(
            value,
            unit=settlement_currency,
            formula=f"sum(native {name} evidence inside Scoring)",
            inputs=("native_run_evidence",),
            source="INDEPENDENT_READ_ONLY_RECONCILIATION_OF_NATIVE_EVIDENCE",
        )

    return PerformanceDiagnostics.create(
        run_id=run_id,
        input_evidence_hashes=input_evidence_hashes,
        equity_observation_basis=OFFICIAL_EQUITY_OBSERVATION_BASIS,
        scored_start=scored_start,
        scoring_end_exclusive=scoring_end_exclusive,
        settlement_currency=settlement_currency,
        valuation_frequency="DAILY_MARKED_PORTFOLIO_EQUITY_UTC",
        annualization_days=OFFICIAL_ANNUALIZATION_DAYS,
        minimum_risk_sample_count=OFFICIAL_RISK_MINIMUM_SAMPLE_COUNT,
        native_statistics_role=NATIVE_STATISTICS_DIAGNOSTIC_ROLE,
        daily_return_sample_count=len(daily_returns),
        intraday_drawdown_captured=False,
        scientific_limitations=REQUIRED_SCIENTIFIC_LIMITATIONS,
        ending_equity=_calculated(
            ending,
            unit=settlement_currency,
            formula="last official daily marked portfolio Equity at scoring_end_exclusive",
            inputs=("official_daily_marked_portfolio_equity",),
            source="OFFICIAL_DAILY_MARKED_PORTFOLIO_EQUITY",
        ),
        total_return=total_return,
        cagr=cagr,
        sharpe=_official_risk_metric(
            daily_returns,
            metric="SHARPE",
            invalid_returns_reason=invalid_returns_reason,
        ),
        sortino=_official_risk_metric(
            daily_returns,
            metric="SORTINO",
            invalid_returns_reason=invalid_returns_reason,
        ),
        fees=component("fees", default_reason="NATIVE_FEE_EVIDENCE_UNAVAILABLE"),
        funding=component("funding", default_reason="NATIVE_FUNDING_EVIDENCE_UNAVAILABLE"),
        realized_pnl=component(
            "realized_pnl",
            default_reason="NATIVE_REALIZED_PNL_UNAVAILABLE",
        ),
        unrealized_pnl=component(
            "unrealized_pnl",
            default_reason="CAUSAL_TERMINAL_VALUATION_UNAVAILABLE",
        ),
        total_pnl=component("total_pnl", default_reason="NATIVE_TOTAL_PNL_UNAVAILABLE"),
        calendar_year_returns=_calendar_returns(
            equity_observations,
            scored_start,
            scoring_end_exclusive,
        ),
        max_drawdown=max_drawdown,
        max_drawdown_duration=max_drawdown_duration,
        average_drawdown_duration=average_drawdown_duration,
        time_under_water=time_under_water,
        completed_trade_count=completed_trade_count,
        win_rate=win_rate,
        max_consecutive_losses=max_consecutive_losses,
        equity_curve=equity_observations,
        daily_returns=daily_returns,
        drawdown_curve=drawdown_curve,
        drawdown_episodes=episodes,
        benchmark_comparison=benchmark,
        sample_adequacy=sample_adequacy,
        monte_carlo_status=monte_carlo_status,
        claim_scope=claim_scope,
    )


class ReportInput(StrictModel):
    schema_version: int
    protocol: ResearchProtocol
    claim_evaluation: ClaimEvaluation
    trial_records: tuple[TrialRecord, ...]
    included_trial_ids: tuple[str, ...]
    selected_trial_id: str
    performance_diagnostics: tuple[PerformanceDiagnostics, ...]
    monte_carlo_results: tuple[MonteCarloResult, ...]
    sample_adequacy_by_instrument: dict[str, str]
    holdout_state: dict[str, Any]
    benchmark_result: dict[str, Any]
    multiple_testing_treatment: str
    qualification_limitations: tuple[str, ...]
    open_terminal_positions: dict[str, str]
    source_evidence_hashes: dict[str, str]
    source_revision: dict[str, str]
    report_purpose: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "unknown report input schema")
        if self.claim_evaluation.protocol_id != self.protocol.protocol_id:
            raise ResearchError(FailureCode.DOWNSTREAM_CONTRACT_FAILURE, "claim and protocol identities differ")
        if self.selected_trial_id != NOT_APPLICABLE and self.selected_trial_id not in self.included_trial_ids:
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, "selected trial is absent from report input")
        if self.multiple_testing_treatment != self.protocol.multiple_testing_treatment:
            raise ResearchError(FailureCode.DOWNSTREAM_CONTRACT_FAILURE,
                "report changed the frozen multiple-testing treatment",
            )
        if len(set(self.included_trial_ids)) != len(self.included_trial_ids):
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, "report contains duplicate trial identities")
        if (
            len(set(self.qualification_limitations))
            != len(self.qualification_limitations)
            or not set(REQUIRED_SCIENTIFIC_LIMITATIONS).issubset(
                self.qualification_limitations,
            )
        ):
            raise ResearchError(
                FailureCode.CLAIM_INELIGIBLE,
                "report omits a mandatory scientific limitation",
            )
        started = {
            item.trial_id for item in self.trial_records if item.state is TrialState.STARTED
        }
        if not set(self.included_trial_ids).issubset(started):
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, "report references an unstarted trial")
        for identity in self.source_evidence_hashes.values():
            _require_sha256(identity, "report.source_evidence_hashes")
        required_source_fields = {"repository", "branch_ref", "git_commit", "git_tree"}
        if set(self.source_revision) != required_source_fields or any(
            not value for value in self.source_revision.values()
        ):
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE,
                "report SourceRevision identity is incomplete",
            )
        _freeze_field(self, "sample_adequacy_by_instrument")
        _freeze_field(self, "holdout_state")
        _freeze_field(self, "benchmark_result")
        _freeze_field(self, "open_terminal_positions")
        _freeze_field(self, "source_evidence_hashes")
        _freeze_field(self, "source_revision")

    @classmethod
    def synthetic(
        cls,
        *,
        protocol: ResearchProtocol,
        claim_evaluation: ClaimEvaluation,
        trial_records: tuple[TrialRecord, ...],
        included_trial_ids: tuple[str, ...],
        selected_trial_id: str,
    ) -> ReportInput:
        return cls(
            schema_version=1,
            protocol=protocol,
            claim_evaluation=claim_evaluation,
            trial_records=trial_records,
            included_trial_ids=included_trial_ids,
            selected_trial_id=selected_trial_id,
            performance_diagnostics=(),
            monte_carlo_results=(),
            sample_adequacy_by_instrument={"BTCUSDT.BINANCE": "ADEQUATE"},
            holdout_state={"state": "SYNTHETIC_CONTRACT_FIXTURE"},
            benchmark_result={"state": "SYNTHETIC_VALID"},
            multiple_testing_treatment=protocol.multiple_testing_treatment,
            qualification_limitations=(
                *REQUIRED_SCIENTIFIC_LIMITATIONS,
                "SYNTHETIC_CONTRACT_FIXTURE_NOT_REAL_CLAIM",
            ),
            open_terminal_positions={},
            source_evidence_hashes={"synthetic-contract": "f" * 64},
            source_revision={
                "repository": NOT_APPLICABLE,
                "branch_ref": NOT_APPLICABLE,
                "git_commit": NOT_APPLICABLE,
                "git_tree": NOT_APPLICABLE,
            },
            report_purpose="SYNTHETIC_CONTRACT_FIXTURE",
        )


class ReportOutput(StrictModel):
    schema_version: int
    report_id: str
    claim_evaluation: ClaimEvaluation
    json_payload: dict[str, Any]
    markdown: str
    source_evidence_hashes: dict[str, str]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "unknown report output schema")
        _require_sha256(self.report_id, "report.report_id")
        if canonical_sha256(self.material_payload()) != self.report_id:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "report content identity mismatch")
        limitations = self.json_payload.get("qualification_limitations")
        if (
            not isinstance(limitations, list)
            or not set(REQUIRED_SCIENTIFIC_LIMITATIONS).issubset(limitations)
            or self.json_payload.get("profitability_claim_is_real") is not False
            or self.json_payload.get("live_trading_authorized") is not False
            or self.json_payload.get("final_holdout_used") is not False
        ):
            raise ResearchError(
                FailureCode.CLAIM_INELIGIBLE,
                "report attempts a claim outside the enforced V1 scientific scope",
            )
        _freeze_field(self, "json_payload")
        _freeze_field(self, "source_evidence_hashes")

    def material_payload(self) -> dict[str, Any]:
        return {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if field.name != "report_id"
        }

    @classmethod
    def create(cls, **values: Any) -> ReportOutput:
        material = {"schema_version": 1, **values}
        return cls(report_id=canonical_sha256(material), **material)


def _build_report_from_resolved_evidence(value: ReportInput) -> ReportOutput:
    started_ids: list[str] = []
    latest: dict[str, TrialRecord] = {}
    for record in value.trial_records:
        latest[record.trial_id] = record
        if record.state is TrialState.STARTED and record.trial_id not in started_ids:
            started_ids.append(record.trial_id)
    missing = sorted(set(started_ids) - set(value.included_trial_ids))
    claim = value.claim_evaluation
    if missing:
        claim = claim.force_ineligible(
            "TRIAL_HISTORY_INCOMPLETE",
            f"REPORT_OMITTED_STARTED_TRIALS:{','.join(missing)}",
        )
    trial_summary = [
        {
            "trial_id": trial_id,
            "state": latest[trial_id].state.value,
            "result_ref": latest[trial_id].result_ref,
            "failure_or_block_reason": latest[trial_id].failure_or_block_reason,
        }
        for trial_id in started_ids
    ]
    real_claim = (
        claim.research_eligibility is ResearchEligibility.ELIGIBLE
        and value.report_purpose == "OFFICIAL_RESEARCH_REPORT"
        and "SYNTHETIC_CONTRACT_FIXTURE_NOT_REAL_CLAIM" not in claim.limitations
        and "DEVELOPMENT_ONLY_DATA" not in value.qualification_limitations
        and "NO_PROFITABILITY_AUTHORIZATION" not in value.qualification_limitations
    )
    payload = {
        "schema": "m4-research-report-v1",
        "report_purpose": value.report_purpose,
        "mechanical_integrity": claim.mechanical_integrity.value,
        "research_eligibility": claim.research_eligibility.value,
        "research_intent": claim.research_intent.value,
        "protocol_id": value.protocol.protocol_id,
        "trial_count": len(started_ids),
        "trial_history": trial_summary,
        "selected_trial_id": value.selected_trial_id,
        "selection_rule": value.protocol.selection_rule,
        "tie_break_rule": value.protocol.tie_break_rule,
        "partitions": {
            "development": value.protocol.development_interval.to_builtins(),
            "validation": value.protocol.validation_interval.to_builtins(),
            "oos": value.protocol.oos_interval.to_builtins(),
            "final_holdout": value.protocol.final_holdout_interval.to_builtins(),
        },
        "holdout_state": dict(value.holdout_state),
        "benchmark": dict(value.benchmark_result),
        "multiple_testing_treatment": value.multiple_testing_treatment,
        "sample_adequacy": dict(value.sample_adequacy_by_instrument),
        "monte_carlo": [item.to_builtins() for item in value.monte_carlo_results],
        "performance_diagnostics": [item.to_builtins() for item in value.performance_diagnostics],
        "claim_scope": value.protocol.intended_claim_scope.value,
        "source_revision": dict(value.source_revision),
        "claim_result": claim.to_builtins(),
        "qualification_limitations": list(value.qualification_limitations),
        "open_terminal_positions": dict(value.open_terminal_positions),
        "estimated_bar_execution": True,
        "estimated_fee_limitation": True,
        "queue_position_claim": "NOT_MODELED",
        "market_impact_claim": "NOT_MODELED",
        "historical_spread_claim": "NOT_MODELED",
        "liquidation_claim": "NOT_MODELED",
        "historical_fee_tier_claim": "NOT_PROVEN",
        "historical_exchange_filter_claim": "NOT_FULLY_PROVEN",
        "perpetual_leverage": "FIXED_AT_ONE_IN_V1",
        "terminal_position_disposition": "CAUSALLY_MARKED_NOT_ACTUALLY_CLOSED",
        "drawdown_frequency": "DAILY_NOT_INTRADAY",
        "development_only": True,
        "final_holdout_used": False,
        "live_trading_authorized": False,
        "profitability_claim_is_real": real_claim,
    }
    lines = [
        "# Nautilus Crypto Backtest Lab — Research Report",
        "",
        f"- MechanicalIntegrity: `{claim.mechanical_integrity.value}`",
        f"- ResearchEligibility: `{claim.research_eligibility.value}`",
        f"- ResearchIntent: `{claim.research_intent.value}`",
        f"- Protocol: `{value.protocol.protocol_id}`",
        f"- Started trials: `{len(started_ids)}`",
        f"- Selected trial: `{value.selected_trial_id}`",
        f"- Claim scope: `{value.protocol.intended_claim_scope.value}`",
        f"- Real profitability claim: `{str(real_claim).lower()}`",
        f"- Selection rule: `{value.protocol.selection_rule}`",
        f"- Tie-break rule: `{value.protocol.tie_break_rule}`",
        f"- Multiple-testing treatment: `{value.multiple_testing_treatment}`",
        f"- Benchmark: `{value.protocol.required_benchmark.benchmark_id}`",
        "",
        "## Complete trial history",
        "",
    ]
    lines.extend(
        f"- `{item['trial_id']}` — `{item['state']}` — {item['failure_or_block_reason']}"
        for item in trial_summary
    )
    if not trial_summary:
        lines.append("- No result-bearing Owner study was executed.")
    lines.extend(
        [
            "",
            "## Research state",
            "",
            f"- Partitions: chronological DEVELOPMENT / VALIDATION / OOS / FINAL_HOLDOUT.",
            f"- Holdout state: `{value.holdout_state.get('state', 'UNKNOWN')}`",
            f"- Sample adequacy: `{dict(value.sample_adequacy_by_instrument)}`",
            f"- Monte Carlo results: `{len(value.monte_carlo_results)}`",
            f"- Performance diagnostic bundles: `{len(value.performance_diagnostics)}`",
            f"- Claim reasons: `{list(claim.reasons)}`",
            "",
            "## Limitations",
            "",
            "- Estimated bar execution and estimated fee assumptions apply.",
            "- Queue position, historical spread, market impact, and liquidation are unsupported/UNKNOWN.",
            "- Qualification evidence is not profitability proof.",
            "- Open terminal positions are disclosed; no synthetic close is inserted.",
        ],
    )
    markdown = "\n".join(lines) + "\n"
    return ReportOutput.create(
        claim_evaluation=claim,
        json_payload=payload,
        markdown=markdown,
        source_evidence_hashes=dict(value.source_evidence_hashes),
    )


def build_report(value: ReportInput) -> ReportOutput:
    """Build only a non-Official low-level contract fixture.

    ``OFFICIAL_RESEARCH_REPORT`` is deliberately unavailable here because a
    caller-created ``ReportInput`` contains material truth assertions.  Official
    production reports are built by ``OfficialEvidenceResolver``.
    """

    if value.report_purpose == "OFFICIAL_RESEARCH_REPORT":
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE,
            "Official reports require an OfficialEvidenceLocator",
        )
    return _build_report_from_resolved_evidence(value)


def _write_resolved_report(
    output: ReportOutput,
    *,
    json_path: Path,
    markdown_path: Path,
) -> None:
    """Persist an already-built report atomically without touching Run evidence."""

    for path, payload in (
        (Path(json_path), canonical_json_bytes(output.to_builtins()) + b"\n"),
        (Path(markdown_path), output.markdown.encode("utf-8")),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, delete=False)
        temporary = Path(handle.name)
        try:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            os.replace(temporary, path)
        finally:
            if not handle.closed:
                handle.close()
            temporary.unlink(missing_ok=True)


def write_report(output: ReportOutput, *, json_path: Path, markdown_path: Path) -> None:
    """Persist a non-Official contract fixture without touching Run evidence."""

    if output.json_payload.get("report_purpose") == "OFFICIAL_RESEARCH_REPORT":
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE,
            "Official report persistence requires OfficialEvidenceResolver",
        )
    _write_resolved_report(output, json_path=json_path, markdown_path=markdown_path)


__all__ = [
    "CalendarYearReturn",
    "DailyReturnObservation",
    "DiagnosticValue",
    "DrawdownEpisode",
    "DrawdownObservation",
    "EquityObservation",
    "NATIVE_STATISTICS_DIAGNOSTIC_ROLE",
    "NativeResearchMetricsReadiness",
    "OFFICIAL_ANNUALIZATION_DAYS",
    "OFFICIAL_EQUITY_OBSERVATION_BASIS",
    "OFFICIAL_RISK_MINIMUM_SAMPLE_COUNT",
    "PerformanceDiagnostics",
    "REQUIRED_SCIENTIFIC_LIMITATIONS",
    "ReportInput",
    "ReportOutput",
    "build_report",
    "generate_native_research_metrics_readiness",
    "generate_performance_diagnostics",
    "write_report",
]
