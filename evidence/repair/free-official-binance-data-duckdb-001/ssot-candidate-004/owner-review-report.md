# Owner review — SSOT Candidate 004

Candidate 004 is a complete SSOT file derived directly from the current root `SSOT.md`; Candidate 003 was not used as its base and remains rejected and byte-preserved.

## Exact identities

- Base root SSOT SHA-256: `f51971ed7a09b172c82ff5965f2899d2a302dd71a2af60eb7c920133567b4354`
- Candidate 004 full-file SHA-256: `b4deb7048242239234de7eaa353b623b3e45247eb42f1021dbc26ffd910edb99`
- Generated unified diff SHA-256: `19ff0232d938c320ee9d9cf549a754f244ae00f357be1d3d738b4f303ff74577`
- Official Phase-A semantic analysis identity: `bf7c4d476702a6438e2940d85548943ca1b2b926f74ba64380e20bd0490c654d`

## Data-only outcome bound by the candidate

- Old window: `[2020-12-01T00:00:00Z, 2021-07-01T00:00:00Z)` → `EXPOSED_DATA_BLOCKED_NOT_FINAL_HOLDOUT` because the free official Binance Mark roles all omit 24 required minutes.
- Selected first chronological shift: `[2021-01-01T00:00:00Z, 2021-08-01T00:00:00Z)`, warmup through `2021-02-01T00:00:00Z`, then scoring through the dataset end.
- Selection inspected no strategy performance and consumed no Final Holdout.

## Scope

The amendment adds only free-official-source roles, raw-trade/aggTrade reconciliation, exact partial-minute and no-trade proof, redundant official delivery classification, fail-closed Mark-gap handling, objective whole-month window qualification, and semantic DuckDB rebuild bindings. It does not modify Nautilus runtime, latency, execution, fee, funding-settlement, account, PnL, strategy, Holdout, or claim semantics.

Two independent clean forward/reverse applications passed with exact byte equality and no fuzz or offset. Root `SSOT.md` remains unchanged pending Owner adoption.

Required adoption statement:

``` text
OWNER_ADOPTS_SSOT_CANDIDATE_004_SHA256=b4deb7048242239234de7eaa353b623b3e45247eb42f1021dbc26ffd910edb99
```
