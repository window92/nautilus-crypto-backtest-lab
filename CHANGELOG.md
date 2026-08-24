# Changelog

All notable repository releases are documented here. The engineering contract
remains `SSOT.md`; this file does not change normative behavior.

## [1.0.0] - 2026-08-24

### Released

- Completed the M0–M4 laboratory: locked runtime, Nautilus-native execution,
  official Binance data boundary, qualified Spot/Perpetual profiles, research
  governance, Holdout protection, diagnostics, and Owner Workflow.
- Accepted free-official Binance BTCUSDT DatasetReleases for Spot CASH
  long-only and USDⓈ-M Linear Perpetual one-way NETTING.
- Added canonical DuckDB validation/storage and deterministic Nautilus
  `ParquetDataCatalog` exports while retaining raw bytes as authority.
- Repaired lossless Nautilus instrument representation, Binance economic order
  grid guards, market-state acceptance, and native funding checker semantics.
- Qualified native completed-position research metrics without project-side
  fill pairing or PnL reconstruction.
- Completed `OWNER_STRATEGY_RESEARCH_001` with the recorded verdict
  `OWNER_STRATEGY_RESEARCH_001_PASS_COMMITTED_AND_PUSHED`.

### Final pressure test

- 294 unique tests passed across 924 execution occurrences.
- Failures, errors, skips, and xfails: zero.
- Six result-bearing mechanical runs returned `CHECK_PASS`.
- Six independent fresh-process deterministic replays returned `PASS`.
- Final Holdout was not used and no real profitability claim was authorized.

### Distribution

- Git includes code, tests, scripts, contracts, locks, manifests, checksums, and
  evidence/reports.
- Raw Binance archives, DuckDB databases, Parquet catalog payloads, virtual
  environments, temporary files, secrets, and large caches are excluded.

### Known limitations

- V1 supports only the two locked Binance BTCUSDT profiles.
- Execution is bar-based and does not model a live order book or queue.
- Official reproducibility requires the exact attested raw-object set; a
  byte-different re-download creates a new candidate identity.
- Published research evidence is exposed Development work, not a Final Holdout
  or a guarantee of future profitability.

[1.0.0]: https://github.com/window92/nautilus-crypto-backtest-lab/releases/tag/v1.0.0
