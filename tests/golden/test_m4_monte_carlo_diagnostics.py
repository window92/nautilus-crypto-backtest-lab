from __future__ import annotations

import unittest
from datetime import timedelta
from decimal import Decimal

from crypto_lab.reporting import EquityObservation
from crypto_lab.reporting import generate_performance_diagnostics
from crypto_lab.research import CompletedTradeSeries
from crypto_lab.research import MonteCarloStatus
from crypto_lab.research import MonteCarloSpec
from crypto_lab.research import ResamplingMethod
from crypto_lab.research import SampleAdequacy
from crypto_lab.research import evaluate_sample_adequacy
from crypto_lab.research import run_monte_carlo
from tests.m4_helpers import NATIVE_TRADES
from tests.m4_helpers import instant
from tests.m4_helpers import valid_protocol


def native_trades(*, unambiguous: bool = True) -> CompletedTradeSeries:
    return CompletedTradeSeries(
        source="NAUTILUS_NATIVE_COMPLETED_TRADES",
        evidence_sha256="a" * 64,
        settlement_currency="USDT",
        stable_native_sequence=True,
        native_completed_unit_count=len(NATIVE_TRADES),
        realized_pnl_outcomes=NATIVE_TRADES,
        realized_returns=tuple(item / Decimal("100") for item in NATIVE_TRADES),
        unambiguous_net_after_cost=unambiguous,
        net_outcomes=NATIVE_TRADES if unambiguous else (),
    )


class MonteCarloKnownResultTests(unittest.TestCase):
    def test_iid_bootstrap_known_result_and_byte_identical_replay(self) -> None:
        protocol = valid_protocol()
        first = run_monte_carlo(
            protocol.monte_carlo_spec,
            native_trades(),
            initial_capital=Decimal("100"),
            sample_adequacy=SampleAdequacy.ADEQUATE,
        )
        second = run_monte_carlo(
            protocol.monte_carlo_spec,
            native_trades(),
            initial_capital=Decimal("100"),
            sample_adequacy=SampleAdequacy.ADEQUATE,
        )
        self.assertEqual(first.to_json_bytes(), second.to_json_bytes())
        self.assertEqual(first.status, MonteCarloStatus.COMPLETED)
        self.assertEqual(first.final_equity_distribution.to_builtins(), {
            "p05": "105.00000000",
            "p50": "105.00000000",
            "p95": "120.00000000",
        })
        self.assertEqual(first.positive_simulation_rate, Decimal("1.00000000"))
        self.assertEqual(first.worst_simulated_drawdown, Decimal("0.05000000"))
        self.assertEqual(first.original_result_location_in_distribution, Decimal("0.62500000"))
        self.assertEqual(first.top_winner_dependency, "1.00000000")
        self.assertEqual(
            first.outlier_dependency,
            {"original_net_pnl": "5.00000000", "without_top_winner": "-5.00000000"},
        )

    def test_ambiguous_native_trade_sequence_is_low_confidence_not_reconstructed(self) -> None:
        result = run_monte_carlo(
            valid_protocol().monte_carlo_spec,
            native_trades(unambiguous=False),
            initial_capital=Decimal("100"),
            sample_adequacy=SampleAdequacy.ADEQUATE,
        )
        self.assertEqual(result.status, MonteCarloStatus.MC_LOW_CONFIDENCE)
        self.assertEqual(result.simulations_completed, 0)
        self.assertIn("ambiguous", result.status_reason.lower())

    def test_moving_block_bootstrap_is_deterministic_and_uses_exact_native_sample_length(self) -> None:
        spec = MonteCarloSpec(
            resampling_method=ResamplingMethod.MOVING_BLOCK_BOOTSTRAP,
            simulation_count=12,
            random_seed=11,
            block_length=2,
            quantile_method="R7_LINEAR_INTERPOLATION",
            decimal_places=8,
            not_applicable_reason="NOT_APPLICABLE",
        )
        trades = CompletedTradeSeries(
            source="NAUTILUS_NATIVE_COMPLETED_TRADES",
            evidence_sha256="9" * 64,
            settlement_currency="USDT",
            stable_native_sequence=True,
            native_completed_unit_count=4,
            realized_pnl_outcomes=(Decimal("2"), Decimal("-1"), Decimal("3"), Decimal("-2")),
            realized_returns=(Decimal("0.02"), Decimal("-0.01"), Decimal("0.03"), Decimal("-0.02")),
            unambiguous_net_after_cost=True,
            net_outcomes=(Decimal("2"), Decimal("-1"), Decimal("3"), Decimal("-2")),
        )
        first = run_monte_carlo(
            spec,
            trades,
            initial_capital=Decimal("100"),
            sample_adequacy=SampleAdequacy.ADEQUATE,
        )
        second = run_monte_carlo(
            spec,
            trades,
            initial_capital=Decimal("100"),
            sample_adequacy=SampleAdequacy.ADEQUATE,
        )
        self.assertEqual(first.to_json_bytes(), second.to_json_bytes())
        self.assertEqual(first.simulations_completed, 12)
        self.assertEqual(first.block_length, 2)

    def test_sample_adequacy_uses_native_completed_trades_only(self) -> None:
        rule = valid_protocol().sample_adequacy_rule
        self.assertEqual(evaluate_sample_adequacy(rule, native_trades()), SampleAdequacy.ADEQUATE)
        one = CompletedTradeSeries(
            source="NAUTILUS_NATIVE_COMPLETED_TRADES",
            evidence_sha256="b" * 64,
            settlement_currency="USDT",
            stable_native_sequence=True,
            native_completed_unit_count=1,
            realized_pnl_outcomes=(Decimal("1"),),
            realized_returns=(Decimal("0.01"),),
            unambiguous_net_after_cost=True,
            net_outcomes=(Decimal("1"),),
        )
        self.assertEqual(evaluate_sample_adequacy(rule, one), SampleAdequacy.LOW_CONFIDENCE)
        self.assertEqual(
            evaluate_sample_adequacy(rule, native_trades(unambiguous=False)),
            SampleAdequacy.ADEQUATE,
        )
        unavailable = CompletedTradeSeries(
            source="NAUTILUS_NATIVE_COMPLETED_TRADES",
            evidence_sha256="c" * 64,
            settlement_currency="USDT",
            stable_native_sequence=False,
            native_completed_unit_count="UNDEFINED",
            realized_pnl_outcomes=(),
            realized_returns=(),
            unambiguous_net_after_cost=False,
            net_outcomes=(),
        )
        self.assertEqual(
            evaluate_sample_adequacy(rule, unavailable),
            SampleAdequacy.LOW_CONFIDENCE,
        )


class PerformanceDiagnosticsKnownResultTests(unittest.TestCase):
    def test_fallback_equity_diagnostics_include_open_drawdown_and_no_smoothing(self) -> None:
        start = instant("2024-01-01T00:00:00Z")
        observations = (
            EquityObservation(timestamp=start, equity=Decimal("100")),
            EquityObservation(timestamp=start + timedelta(days=1), equity=Decimal("120")),
            EquityObservation(timestamp=start + timedelta(days=2), equity=Decimal("90")),
            EquityObservation(timestamp=start + timedelta(days=3), equity=Decimal("100")),
            EquityObservation(timestamp=start + timedelta(days=4), equity=Decimal("100")),
        )
        result = generate_performance_diagnostics(
            run_id="synthetic-diagnostics",
            scored_start=start,
            scoring_end_exclusive=start + timedelta(days=4),
            initial_capital=Decimal("100"),
            settlement_currency="USDT",
            equity_observations=observations,
            completed_trades=native_trades(),
            benchmark_return=Decimal("0.05"),
            sample_adequacy=SampleAdequacy.ADEQUATE,
            monte_carlo_status=MonteCarloStatus.COMPLETED,
            claim_scope="INSTRUMENT_ONLY",
            input_evidence_hashes={"account.csv": "c" * 64, "nautilus_result.json": "d" * 64},
        )
        self.assertEqual(result.total_return.value, "0.00000000")
        self.assertEqual(result.max_drawdown.value, "0.25000000")
        self.assertEqual(result.max_drawdown_duration.value, "172800")
        self.assertEqual(result.time_under_water.value, "0.50000000")
        self.assertTrue(result.drawdown_episodes[-1].open_at_terminal)
        self.assertEqual(result.completed_trade_count.value, "2")
        self.assertEqual(result.win_rate.value, "0.50000000")
        self.assertEqual(result.max_consecutive_losses.value, "1")

    def test_stable_native_count_survives_cost_ambiguity(self) -> None:
        start = instant("2024-01-01T00:00:00Z")
        ambiguous = native_trades(unambiguous=False)
        result = generate_performance_diagnostics(
            run_id="undefined-trades",
            scored_start=start,
            scoring_end_exclusive=start + timedelta(days=1),
            initial_capital=Decimal("100"),
            settlement_currency="USDT",
            equity_observations=(
                EquityObservation(timestamp=start, equity=Decimal("100")),
                EquityObservation(timestamp=start + timedelta(days=1), equity=Decimal("101")),
            ),
            completed_trades=ambiguous,
            benchmark_return=None,
            sample_adequacy=SampleAdequacy.LOW_CONFIDENCE,
            monte_carlo_status=MonteCarloStatus.MC_LOW_CONFIDENCE,
            claim_scope="INSTRUMENT_ONLY",
            input_evidence_hashes={"account.csv": "c" * 64},
        )
        self.assertEqual(result.completed_trade_count.status, "CALCULATED")
        self.assertEqual(result.completed_trade_count.value, "2")
        self.assertEqual(result.win_rate.status, "UNDEFINED")
        self.assertEqual(result.benchmark_comparison.status, "UNDEFINED")

    def test_empty_native_trade_sequence_has_zero_count_but_undefined_win_rate(self) -> None:
        start = instant("2024-01-01T00:00:00Z")
        empty = CompletedTradeSeries(
            source="NAUTILUS_NATIVE_COMPLETED_TRADES",
            evidence_sha256="8" * 64,
            settlement_currency="USDT",
            stable_native_sequence=True,
            native_completed_unit_count=0,
            realized_pnl_outcomes=(),
            realized_returns=(),
            unambiguous_net_after_cost=True,
            net_outcomes=(),
        )
        result = generate_performance_diagnostics(
            run_id="empty-native-trades",
            scored_start=start,
            scoring_end_exclusive=start + timedelta(days=1),
            initial_capital=Decimal("100"),
            settlement_currency="USDT",
            equity_observations=(
                EquityObservation(timestamp=start, equity=Decimal("100")),
                EquityObservation(timestamp=start + timedelta(days=1), equity=Decimal("100")),
            ),
            completed_trades=empty,
            benchmark_return=Decimal("0"),
            sample_adequacy=SampleAdequacy.LOW_CONFIDENCE,
            monte_carlo_status=MonteCarloStatus.MC_LOW_CONFIDENCE,
            claim_scope="INSTRUMENT_ONLY",
            input_evidence_hashes={"account.csv": "7" * 64},
        )
        self.assertEqual(result.completed_trade_count.value, "0")
        self.assertEqual(result.win_rate.status, "UNDEFINED")
        self.assertEqual(result.win_rate.value, "UNDEFINED")

    def test_native_metric_override_is_not_an_official_metrics_api(self) -> None:
        start = instant("2024-01-01T00:00:00Z")
        with self.assertRaises(TypeError):
            generate_performance_diagnostics(
                run_id="invalid-native-metric",
                scored_start=start,
                scoring_end_exclusive=start + timedelta(days=1),
                initial_capital=Decimal("100"),
                settlement_currency="USDT",
                equity_observations=(
                    EquityObservation(timestamp=start, equity=Decimal("100")),
                    EquityObservation(timestamp=start + timedelta(days=1), equity=Decimal("101")),
                ),
                native_metrics={"total_return": "NaN"},
                completed_trades=native_trades(),
                benchmark_return=Decimal("0"),
                sample_adequacy=SampleAdequacy.ADEQUATE,
                monte_carlo_status=MonteCarloStatus.COMPLETED,
                claim_scope="INSTRUMENT_ONLY",
                input_evidence_hashes={"account.csv": "7" * 64},
            )


if __name__ == "__main__":
    unittest.main()
