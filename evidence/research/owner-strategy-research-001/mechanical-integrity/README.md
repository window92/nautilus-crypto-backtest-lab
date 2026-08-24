# Mechanical Integrity — OWNER_STRATEGY_RESEARCH_001

> هذه شهادة تنفيذ ميكانيكية، وليست شهادة ربحية.

| Trial | Checker | Orders/Fills | Precision skips | Missing market | Network contacts |
|---|---|---:|---:|---:|---:|
| spot_benchmark | CHECK_PASS | 1/1 | 0 | 0 | 0 |
| spot_candidate_a | CHECK_PASS | 9/9 | 0 | 0 | 0 |
| spot_candidate_b | CHECK_PASS | 15/15 | 0 | 0 | 0 |
| perpetual_benchmark | CHECK_PASS | 1/1 | 0 | 0 | 0 |
| perpetual_candidate_a | CHECK_PASS | 32/32 | 0 | 0 | 0 |
| perpetual_candidate_b | CHECK_PASS | 30/30 | 0 | 0 | 0 |

جميع الـFills سببية بعد availability + 60 ثانية؛ لا same-bar Fill، لا `No market`، لا fatal diagnostics، ولا project-side financial posting. Spot بقي CASH/NETTING long-only. Perpetual بقي MARGIN/NETTING 1x، مع close-flat-confirm-reopen وnative mark/funding.
