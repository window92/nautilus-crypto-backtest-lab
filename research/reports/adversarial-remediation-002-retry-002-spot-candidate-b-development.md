# Nautilus Crypto Backtest Lab — Research Report

- MechanicalIntegrity: `PASS`
- ResearchEligibility: `INELIGIBLE`
- ResearchIntent: `EXPLORATORY`
- Protocol: `6ddac01fdd127bc25245291c9f9722a31f9e057601f95975455084f16b13474b`
- Started trials: `4`
- Selected trial: `adversarial-remediation-002-retry-002-spot-candidate-b-development`
- Claim scope: `INSTRUMENT_ONLY`
- Real profitability claim: `false`
- Selection rule: `NO_PUBLISHABLE_WINNER_SELECTION_EXPLORATORY_RESULTS_ONLY`
- Tie-break rule: `NOT_APPLICABLE_NO_WINNER_SELECTION`
- Multiple-testing treatment: `HOLM_BONFERRONI`
- Benchmark: `BUY_AND_HOLD_1X_R2_SPOT`

## Complete trial history

- `adversarial-remediation-002-spot-benchmark-buy-and-hold-1x-development` — `ABORTED` — FAIL_CLOSED_RECOVERY_BEFORE_OFFICIAL_PROCESS_START
- `adversarial-remediation-002-retry-002-spot-benchmark-buy-and-hold-1x-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-002-spot-candidate-a-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-002-spot-candidate-b-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED

## Research state

- Partitions: chronological DEVELOPMENT / VALIDATION / OOS / FINAL_HOLDOUT.
- Holdout state: `NOT_USED_BY_SELECTED_TRIAL`
- Sample adequacy: `{'BTCUSDT.BINANCE': 'NOT_APPLICABLE'}`
- Monte Carlo results: `0`
- Performance diagnostic bundles: `1`
- Claim reasons: `['UNDERLYING_OFFICIAL_RUN_INVALID', 'PARTITION_LEAKAGE', 'HOLDOUT_INVALID_OR_CONSUMED', 'MONTE_CARLO_NOT_COMPLETED', 'EXPLORATORY_PROTOCOL', 'SAMPLE_ADEQUACY_NOT_ADEQUATE']`

## Limitations

- Estimated bar execution and estimated fee assumptions apply.
- Queue position, historical spread, market impact, and liquidation are unsupported/UNKNOWN.
- Qualification evidence is not profitability proof.
- Open terminal positions are disclosed; no synthetic close is inserted.
