# Nautilus Crypto Backtest Lab v1.0.0

> **Post-release audit notice (2026-08-30):** this page is a historical release
> record. The original evidence is intentionally unchanged, but affected V1
> financial runs/replays are now `REVOKED` and their financial conclusions are
> `INVALIDATED` because the old acceptance contract did not prove Spot CASH
> reconciliation, the scoring boundary, installed runtime payloads, or exact
> funding binding. See [AUDIT_REMEDIATION.md](AUDIT_REMEDIATION.md) and the
> additive status registry. This notice does not authorize Final Holdout use or
> any profitability claim.

V1 closes the repository as a functionally complete, strict cryptocurrency
backtesting laboratory for the supported BTCUSDT profiles. The accepted product
and pressure-test commit is
`08c4c970fbd52a1e6928e046f377b9ecbdf3ca00`; the annotated `v1.0.0` tag adds
only release documentation, the release manifest, and ignore-policy hardening
to that tested product state; it does not change Product Code or runtime/data
semantics.

## Supported scope

- Binance Spot BTCUSDT, CASH/NETTING, long-only.
- Binance USDⓈ-M Linear Perpetual BTCUSDT, MARGIN/NETTING, one-way long/short,
  leverage 1.
- Causal bar-based registered strategies.
- Official Binance source pipeline, checksums, source reconciliation, DuckDB
  canonical validation/storage, and Nautilus `ParquetDataCatalog` export.
- Runtime, data, catalog, strategy, source revision, trial, and report identity.
- Offline Official Runs, read-only checker, deterministic replay, research
  governance, Holdout protection, and public Owner Workflow.

NautilusTrader `2.0.0rc2` owns every order, Fill, position, account effect,
portfolio value, PnL, fee, funding settlement, and mark valuation. Project code
does not replace its financial engine.

## M0–M4 and final repairs

1. **M0:** locked CPython/Nautilus wheel identity and fail-closed runtime
   preflight.
2. **M1:** public Nautilus execution boundary, native financial state, immutable
   evidence, and checker.
3. **M2:** official Binance raw-byte preservation, DatasetRelease, and catalog
   identities.
4. **M3:** qualified Spot and Perpetual mechanical profiles with offline replay.
5. **M4:** frozen research protocols, authoritative journal/history anchors,
   Holdout exposure controls, diagnostics, and reporting.
6. **Data provenance repairs:** official-source reconciliation, exact Decimal
   derivation from official trades where authorized, verified no-trade
   dispositions, and fail-closed Perpetual Mark coverage.
7. **Instrument/checker repair:** lossless market-data precision, economic order
   grid enforcement, all-data Nautilus ingestion, sentinel fills, and native
   funding/mark as-of checks.
8. **Native metrics readiness:** Nautilus-native completed NETTING cycles and
   explicit handling of unavailable Gross PnL/Calmar semantics.
9. **Owner research pressure test:** exactly two frozen TSMOM28 candidates plus
   fixed benchmarks over exposed Development data.

## Final acceptance

The historically recorded research verdict was
`OWNER_STRATEGY_RESEARCH_001_PASS_COMMITTED_AND_PUSHED`. It is preserved as an
old-contract fact and is not a current financial acceptance verdict.

| Gate | Result |
|---|---:|
| Unique tests | 294 PASS |
| Execution occurrences | 924 PASS |
| Failures / errors / skips / xfails | 0 / 0 / 0 / 0 |
| Mechanical result-bearing runs | 6 × `CHECK_PASS` |
| Fresh-process deterministic replays | 6 × `PASS` |
| Final Holdout used | false |
| Real profitability claim | false |

The content identity of the final pressure-test evidence is
`9757f5edef476db27c1da51c7d5b6e98762014c66893a988a38ebed0d556f437`.
Its inventory identity is
`9f7680befe70dc445b368eefb21efe1b023f7a23c2c0ee795258208b9c1fa33f`.

## Quick start

Follow the verified commands in the repository
[README](../README.md#create-the-project-runtime). A backtest requires:

1. the exact local runtime;
2. an accepted DatasetRelease and its materialized catalog;
3. a reviewed strict `OwnerWorkflowInput` with new identities;
4. a clean `HEAD == origin/main` checkpoint;
5. the public isolated-bootstrap command documented in the README, targeting
   `crypto_lab.owner:main`; direct `PYTHONPATH`/wrapper execution is not an
   Official R2 launch.

The workflow runs the checker and produces JSON/Markdown reports. It does not
silently create a strategy, data window, fee, or research claim.

## Data build and identities

The full storage/rebuild contract is in
[DATA_STORAGE_AND_REBUILD.md](DATA_STORAGE_AND_REBUILD.md). The accepted V1
identities are bound in [the release manifest](../release/v1.0.0-manifest.json).
Large raw, DuckDB, and Parquet payloads are not Release assets and are not in
Git.

## Owner reports

- [Final strategy research](../evidence/research/owner-strategy-research-001/owner-report/README.md)
- [Mechanical integrity](../evidence/research/owner-strategy-research-001/mechanical-integrity/README.md)
- [Deterministic replay](../evidence/research/owner-strategy-research-001/deterministic-replay/README.md)
- [Corrected Spot/Perpetual execution](../evidence/research/owner-smoke-002-replacement-001/owner-report/README.md)
- [Instrument representation and checker repair](../evidence/repair/instrument-representation-funding-checker-001/owner-report/README.md)
- [Free official Binance data repair](../evidence/repair/free-official-binance-data-duckdb-001/owner-report/README.md)

## Limits and disclaimer

- The Fill Model is bar-based; it cannot reproduce a live order book, queue
  position, tick path, market impact, or every source of slippage.
- The accepted data window is exposed Development data, not fresh Final
  Holdout data.
- V1 does not authorize optimization, live trading, or a real profitability
  claim.
- Research results, including positive results, do not guarantee future
  performance and are not investment advice.
- A clone contains manifests and checksums, not large data payloads.

No DuckDB database, raw Binance archive, or Parquet catalog is attached to this
release.
