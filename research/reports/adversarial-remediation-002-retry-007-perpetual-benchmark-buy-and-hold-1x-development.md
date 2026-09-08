# Nautilus Crypto Backtest Lab — Research Report

- MechanicalIntegrity: `PASS`
- ResearchEligibility: `INELIGIBLE`
- ResearchIntent: `EXPLORATORY`
- Protocol: `4c3bdc9765193a014c675aa1bde3f7b694a9efd54ac580cf9828d102698b33b2`
- Started trials: `19`
- Selected trial: `adversarial-remediation-002-retry-007-perpetual-benchmark-buy-and-hold-1x-development`
- Claim scope: `INSTRUMENT_ONLY`
- Real profitability claim: `false`
- Selection rule: `NO_PUBLISHABLE_WINNER_SELECTION_EXPLORATORY_RESULTS_ONLY`
- Tie-break rule: `NOT_APPLICABLE_NO_WINNER_SELECTION`
- Multiple-testing treatment: `HOLM_BONFERRONI`
- Benchmark: `BUY_AND_HOLD_1X_R2_PERPETUAL_ADVERSARIAL_REMEDIATION_002_RETRY_007`

## Complete trial history

- `adversarial-remediation-002-spot-benchmark-buy-and-hold-1x-development` — `ABORTED` — FAIL_CLOSED_RECOVERY_BEFORE_OFFICIAL_PROCESS_START
- `adversarial-remediation-002-retry-002-spot-benchmark-buy-and-hold-1x-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-002-spot-candidate-a-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-002-spot-candidate-b-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-002-perpetual-benchmark-buy-and-hold-1x-development` — `FAILED` — DETERMINISTIC_REPLAY_MISMATCH_OR_FAILURE
- `adversarial-remediation-002-retry-003-spot-benchmark-buy-and-hold-1x-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-004-spot-benchmark-buy-and-hold-1x-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-005-spot-benchmark-buy-and-hold-1x-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-005-spot-candidate-a-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-005-spot-candidate-b-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-005-perpetual-benchmark-buy-and-hold-1x-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-006-spot-benchmark-buy-and-hold-1x-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-006-spot-candidate-a-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-006-spot-candidate-b-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-006-perpetual-benchmark-buy-and-hold-1x-development` — `FAILED` — DETERMINISTIC_REPLAY_MISMATCH_OR_FAILURE
- `adversarial-remediation-002-retry-007-spot-benchmark-buy-and-hold-1x-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-007-spot-candidate-a-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-007-spot-candidate-b-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-007-perpetual-benchmark-buy-and-hold-1x-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED

## Research state

- Partitions: chronological DEVELOPMENT / VALIDATION / OOS / FINAL_HOLDOUT.
- Holdout state: `NOT_USED_BY_SELECTED_TRIAL`
- Sample adequacy: `{'BTCUSDT-PERP.BINANCE': 'NOT_APPLICABLE'}`
- Monte Carlo results: `0`
- Performance diagnostic bundles: `1`
- Claim reasons: `['QUALIFICATION_EVIDENCE_NOT_PROFITABILITY_PROOF', 'PARTITION_LEAKAGE', 'HOLDOUT_INVALID_OR_CONSUMED', 'MONTE_CARLO_NOT_COMPLETED', 'EXPLORATORY_PROTOCOL', 'LATER_PROTOCOL_REANALYSIS', 'SAMPLE_ADEQUACY_NOT_ADEQUATE']`

## Limitations

- Estimated bar execution and estimated fee assumptions apply.
- Queue position, historical spread, market impact, and liquidation are unsupported/UNKNOWN.
- Qualification evidence is not profitability proof.
- Open terminal positions are disclosed; no synthetic close is inserted.
