# M1 acceptance epoch 001

This additive epoch contains the synthetic M1 causal-harness qualifications on
the adopted NautilusTrader 2.0.0rc2 runtime. The JSON status values are derived
from actual executions; this README is not a PASS assertion.

`failed-attempts.jsonl` and `golden-first-output.txt` retain the failed-first and
repair history. `test-results.json`/`test-output.txt` are produced by
`scripts/run_m1_acceptance.py`. `generate_m1_evidence.py` creates the native
funding, mark, Spot CASH, causal, replay, lifecycle, and persisted Run evidence.
`validate_m1_evidence.py` verifies those artifacts without modifying them.

All Runs in this epoch use synthetic external one-minute LAST bars and are
`QUALIFICATION` Runs. No real market data was acquired and no Official Run was
executed. The G03 `FAILED` Run and G07 `BLOCKED` Run are required negative
evidence, not hidden acceptance failures.

Golden IDs owned by later SSOT phases remain explicitly deferred in
`qualification-matrix.json`; this epoch does not implement M2, M3, or M4.
