# Nautilus Crypto Backtest Lab — Research Report

- MechanicalIntegrity: `PASS`
- ResearchEligibility: `INELIGIBLE`
- ResearchIntent: `EXPLORATORY`
- Protocol: `fc5d79a9324178cb0740e50832bb53f1f3b53ed996add829dc8d8ab3fc7daf84`
- Started trials: `7`
- Selected trial: `owner-strategy-research-001-perpetual-candidate-b-development`
- Claim scope: `INSTRUMENT_ONLY`
- Real profitability claim: `false`
- Selection rule: `NO_PUBLISHABLE_WINNER_SELECTION_EXPLORATORY_RESULTS_ONLY`
- Tie-break rule: `NOT_APPLICABLE_NO_WINNER_SELECTION`
- Multiple-testing treatment: `HOLM_BONFERRONI`
- Benchmark: `BUY_AND_HOLD_1X_V1_PERPETUAL`

## Complete trial history

- `owner-strategy-research-001-spot-benchmark-buy-and-hold-1x-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `owner-strategy-research-001-spot-candidate-a-development` — `FAILED` — DETERMINISTIC_REPLAY_MISMATCH_OR_FAILURE
- `owner-strategy-research-001-spot-candidate-a-development-retry-001` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `owner-strategy-research-001-spot-candidate-b-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `owner-strategy-research-001-perpetual-benchmark-buy-and-hold-1x-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `owner-strategy-research-001-perpetual-candidate-a-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `owner-strategy-research-001-perpetual-candidate-b-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED

## Research state

- Partitions: chronological DEVELOPMENT / VALIDATION / OOS / FINAL_HOLDOUT.
- Holdout state: `NOT_USED_BY_SELECTED_TRIAL`
- Sample adequacy: `{'BTCUSDT-PERP.BINANCE': 'NOT_APPLICABLE'}`
- Monte Carlo results: `0`
- Performance diagnostic bundles: `1`
- Claim reasons: `['HOLDOUT_INVALID_OR_CONSUMED', 'MONTE_CARLO_NOT_COMPLETED', 'EXPLORATORY_PROTOCOL', 'LATER_PROTOCOL_REANALYSIS', 'SAMPLE_ADEQUACY_NOT_ADEQUATE']`

## Limitations

- Estimated bar execution and estimated fee assumptions apply.
- Queue position, historical spread, market impact, and liquidation are unsupported/UNKNOWN.
- Qualification evidence is not profitability proof.
- Open terminal positions are disclosed; no synthetic close is inserted.
