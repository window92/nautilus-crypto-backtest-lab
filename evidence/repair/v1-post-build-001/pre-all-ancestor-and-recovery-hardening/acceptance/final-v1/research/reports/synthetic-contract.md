# Nautilus Crypto Backtest Lab — Research Report

- MechanicalIntegrity: `PASS`
- ResearchEligibility: `ELIGIBLE`
- ResearchIntent: `CONFIRMATORY`
- Protocol: `f09d8f5a72557b73576fa0f33393a92ee7fe9f7259c5862ad42f4c908bfc7f80`
- Started trials: `2`
- Selected trial: `m4-synthetic-completed`
- Claim scope: `INSTRUMENT_ONLY`
- Real profitability claim: `false`
- Selection rule: `MAX_PRIMARY_METRIC_SUBJECT_TO_KILL_CRITERIA`
- Tie-break rule: `LOWEST_CANDIDATE_ID_LEXICOGRAPHICALLY`
- Multiple-testing treatment: `HOLM_BONFERRONI`
- Benchmark: `BUY_AND_HOLD_SAME_INSTRUMENT`

## Complete trial history

- `m4-synthetic-failed` — `FAILED` — EXPECTED_SYNTHETIC_FAILED_TRIAL_RETAINED
- `m4-synthetic-completed` — `COMPLETED` — NOT_APPLICABLE

## Research state

- Partitions: chronological DEVELOPMENT / VALIDATION / OOS / FINAL_HOLDOUT.
- Holdout state: `CONSUMED`
- Sample adequacy: `{'BTCUSDT.BINANCE': 'ADEQUATE'}`
- Monte Carlo results: `1`
- Performance diagnostic bundles: `1`
- Claim reasons: `[]`

## Limitations

- Estimated bar execution and estimated fee assumptions apply.
- Queue position, historical spread, market impact, and liquidation are unsupported/UNKNOWN.
- Qualification evidence is not profitability proof.
- Open terminal positions are disclosed; no synthetic close is inserted.
