# Nautilus Crypto Backtest Lab — Research Report

- MechanicalIntegrity: `PASS`
- ResearchEligibility: `INELIGIBLE`
- ResearchIntent: `EXPLORATORY`
- Protocol: `bf31b26961e061dac599d88317d07cac77827977da360537859f14928982b8fc`
- Started trials: `33`
- Selected trial: `adversarial-remediation-002-retry-010-spot-benchmark-buy-and-hold-1x-development`
- Claim scope: `INSTRUMENT_ONLY`
- Real profitability claim: `false`
- Selection rule: `NO_PUBLISHABLE_WINNER_SELECTION_EXPLORATORY_RESULTS_ONLY`
- Tie-break rule: `NOT_APPLICABLE_NO_WINNER_SELECTION`
- Multiple-testing treatment: `HOLM_BONFERRONI`
- Benchmark: `BUY_AND_HOLD_1X_R2_SPOT_ADVERSARIAL_REMEDIATION_002_RETRY_010`

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
- `adversarial-remediation-002-retry-007-perpetual-candidate-a-development` — `FAILED` — DETERMINISTIC_REPLAY_MISMATCH_OR_FAILURE
- `adversarial-remediation-002-retry-008-spot-benchmark-buy-and-hold-1x-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-008-spot-candidate-a-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-008-spot-candidate-b-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-008-perpetual-benchmark-buy-and-hold-1x-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-008-perpetual-candidate-a-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-008-perpetual-candidate-b-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-009-spot-benchmark-buy-and-hold-1x-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-009-spot-candidate-a-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-009-spot-candidate-b-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-009-perpetual-benchmark-buy-and-hold-1x-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-009-perpetual-candidate-a-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-009-perpetual-candidate-b-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED
- `adversarial-remediation-002-retry-010-spot-benchmark-buy-and-hold-1x-development` — `COMPLETED` — OFFICIAL_RUN_COMPLETED

## Research state

- Partitions: chronological DEVELOPMENT / VALIDATION / OOS / FINAL_HOLDOUT.
- Holdout state: `NOT_USED_BY_SELECTED_TRIAL`
- Sample adequacy: `{'BTCUSDT.BINANCE': 'NOT_APPLICABLE'}`
- Monte Carlo results: `0`
- Performance diagnostic bundles: `1`
- Claim reasons: `['QUALIFICATION_EVIDENCE_NOT_PROFITABILITY_PROOF', 'PARTITION_LEAKAGE', 'HOLDOUT_INVALID_OR_CONSUMED', 'MONTE_CARLO_NOT_COMPLETED', 'EXPLORATORY_PROTOCOL', 'LATER_PROTOCOL_REANALYSIS', 'SAMPLE_ADEQUACY_NOT_ADEQUATE']`

## Limitations

- Estimated bar execution and estimated fee assumptions apply.
- Queue position, historical spread, market impact, and liquidation are unsupported/UNKNOWN.
- Qualification evidence is not profitability proof.
- Open terminal positions are disclosed; no synthetic close is inserted.
