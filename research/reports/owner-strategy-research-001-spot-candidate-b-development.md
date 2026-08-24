# Nautilus Crypto Backtest Lab — Research Report

- MechanicalIntegrity: `PASS`
- ResearchEligibility: `INELIGIBLE`
- ResearchIntent: `EXPLORATORY`
- Protocol: `2b0239a69faff825c7c35aa0dd372d112a49973d8076aeaf62e25f1e2abb4a12`
- Started trials: `4`
- Selected trial: `owner-strategy-research-001-spot-candidate-b-development`
- Claim scope: `INSTRUMENT_ONLY`
- Real profitability claim: `false`
- Selection rule: `NO_PUBLISHABLE_WINNER_SELECTION_EXPLORATORY_RESULTS_ONLY`
- Tie-break rule: `NOT_APPLICABLE_NO_WINNER_SELECTION`
- Multiple-testing treatment: `HOLM_BONFERRONI`
- Benchmark: `BUY_AND_HOLD_1X_V1_SPOT`

## Complete trial history

- `owner-strategy-research-001-spot-benchmark-buy-and-hold-1x-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `owner-strategy-research-001-spot-candidate-a-development` — `FAILED` — DETERMINISTIC_REPLAY_MISMATCH_OR_FAILURE
- `owner-strategy-research-001-spot-candidate-a-development-retry-001` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `owner-strategy-research-001-spot-candidate-b-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED

## Research state

- Partitions: chronological DEVELOPMENT / VALIDATION / OOS / FINAL_HOLDOUT.
- Holdout state: `NOT_USED_BY_SELECTED_TRIAL`
- Sample adequacy: `{'BTCUSDT.BINANCE': 'NOT_APPLICABLE'}`
- Monte Carlo results: `0`
- Performance diagnostic bundles: `1`
- Claim reasons: `['UNDERLYING_OFFICIAL_RUN_INVALID', 'HOLDOUT_INVALID_OR_CONSUMED', 'MONTE_CARLO_NOT_COMPLETED', 'EXPLORATORY_PROTOCOL', 'SAMPLE_ADEQUACY_NOT_ADEQUATE']`

## Limitations

- Estimated bar execution and estimated fee assumptions apply.
- Queue position, historical spread, market impact, and liquidation are unsupported/UNKNOWN.
- Qualification evidence is not profitability proof.
- Open terminal positions are disclosed; no synthetic close is inserted.
