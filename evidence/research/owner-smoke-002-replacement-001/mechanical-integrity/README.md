# Mechanical Integrity — OWNER_SMOKE_002 Replacement 001

> **هذه ليست توصية تداول، وليست Final Holdout، ولا تسمح بأي Profitability Claim.**

الغرض `EXPLORATORY_OPERATIONAL_VALIDATION` فقط، والنافذة `[2021-01-01T00:00:00Z, 2021-08-01T00:00:00Z)` مع scoring `[2021-02-01T00:00:00Z, 2021-08-01T00:00:00Z)` مصنفة `DATA_QUALITY_INSPECTED_NOT_FINAL_HOLDOUT`.


| البوابة | Spot | Perpetual |
|---|---:|---:|
| Read-only checker | CHECK_PASS | CHECK_PASS |
| Daily bars / decisions | 212 / 181 | 212 / 181 |
| Orders / Fills | 27 / 27 | 55 / 55 |
| No-market rejections | 0 | 0 |
| Precision-skipped executable Bars | 0 | 0 |
| Rejected Mark precision events | N/A | 0 |
| Causality / 60s latency | PASS | PASS |
| Fee exactly once | PASS | PASS |
| Native funding cardinality | N/A | 539/539 eligible boundaries |
| Offline boundary / contacts | PASS / 0 | PASS / 0 |
| Terminal policy | PASS | PASS |

Spot القديمة التي أعادت false `CHECK_PASS` يعيدها checker الحالي `CHECK_FAIL`. Pair الـFundingRateUpdate لا تُعد settlement مالية مزدوجة؛ الدليل المالي هو `PositionAdjusted(FUNDING)` مع أثر AccountState. المحاولات الفاشلة والـTrials السابقة بقيت immutable ومتصلة بسلسلة supersession.
