# Nautilus Crypto Backtest Lab — Research Report

- MechanicalIntegrity: `PASS`
- ResearchEligibility: `INELIGIBLE`
- ResearchIntent: `EXPLORATORY`
- Protocol: `9133dc5b59ebc24c7f9aa37fedd1986ba79829092518e54202ba1ac84175be1e`
- Started trials: `2`
- Selected trial: `owner-smoke-002-spot-sma20-development-retry-001`
- Claim scope: `INSTRUMENT_ONLY`
- Real profitability claim: `false`
- Selection rule: `ONLY_PREDECLARED_SMA20_CANDIDATE_NO_RANKING`
- Tie-break rule: `NOT_APPLICABLE_SINGLE_CANDIDATE`
- Multiple-testing treatment: `NOT_APPLICABLE`
- Benchmark: `OWNER_SMOKE_002_SPOT_NO_PROFITABILITY_BENCHMARK`

## Complete trial history

- `owner-smoke-002-spot-sma20-development` — `FAILED` — DETERMINISTIC_REPLAY_MISMATCH_OR_FAILURE
- `owner-smoke-002-spot-sma20-development-retry-001` — `COMPLETED` — OFFICIAL_RUN_COMPLETED

## Research state

- Partitions: chronological DEVELOPMENT / VALIDATION / OOS / FINAL_HOLDOUT.
- Holdout state: `NOT_USED_BY_SELECTED_TRIAL`
- Sample adequacy: `{'BTCUSDT.BINANCE': 'NOT_APPLICABLE'}`
- Monte Carlo results: `0`
- Performance diagnostic bundles: `1`
- Claim reasons: `['HOLDOUT_INVALID_OR_CONSUMED', 'BENCHMARK_MISSING_OR_INVALID', 'MONTE_CARLO_NOT_COMPLETED', 'EXPLORATORY_PROTOCOL', 'LATER_PROTOCOL_REANALYSIS', 'SAMPLE_ADEQUACY_NOT_ADEQUATE']`

## Limitations

- Estimated bar execution and estimated fee assumptions apply.
- Queue position, historical spread, market impact, and liquidation are unsupported/UNKNOWN.
- Qualification evidence is not profitability proof.
- Open terminal positions are disclosed; no synthetic close is inserted.
