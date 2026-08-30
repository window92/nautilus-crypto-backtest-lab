# Changelog

All notable repository releases are documented here. The engineering contract
remains `SSOT.md`; this file does not change normative behavior.

## [Unreleased] - 1.0.1.dev0

### Audit remediation

- Added exact independent Spot CASH reconciliation across native Fill,
  AccountState, Position, commission, and base/quote balances; unfunded buys
  and unavailable-base sells now fail closed.
- Enforced the causal engine data window before ingestion, including distinct
  completed-interval and point-event boundary semantics for bars, marks, and
  funding.
- Extended runtime attestation from version/wheel identity to the installed
  `RECORD` payload, every recorded hash/size, native extensions, prohibited
  extras, and reproducible cache validation.
- Generalized exact source-to-native Perpetual funding binding to every
  Official Run, including boundary position, causal mark, rate, cardinality,
  settlement currency, and account delta.
- Rejected cross-instrument Mark/Funding relabeling at converter and catalog
  boundaries.
- Made TrialJournal and Holdout transitions interprocess-locked, head-rechecked,
  durable, and crash-tested.
- Replaced float timestamp conversion with shared integer Unix-epoch
  arithmetic.
- Bound historical validators to immutable contract snapshots with explicit
  valid-snapshot, valid-current-drift, and evidence-corruption states.
- Synchronized the `FailureCode` type with the complete SSOT vocabulary.
- Added an additive `REVOKED` / `INVALIDATED` registry for affected historical
  runs and replays without changing their bytes.
- Persisted and bound the executed installed-file Runtime identity in every new
  Run, and bound Official runs/reports to the exact qualified-profile registry
  and record rather than a mutable or historical default.
- Replaced the duplicate-module child invocation with the repository CLI,
  superseded the warned epoch additively, and published a clean six-workflow
  Development epoch with 12 read-only-revalidated primary/replay directories.
- Added a central fail-closed result validator which checks Spot reconciliation,
  exact Perpetual Funding binding, half-open engine windows, Runtime/profile
  authority, deterministic replay, historical report snapshots, journal/anchor
  history, and the empty Holdout lock.

### Status and limits

- Package version is `1.0.1.dev0`; no tag or release is created by this work.
- NautilusTrader remains pinned to `2.0.0rc2`; the active dependency-lock name
  and installed-payload identity are corrected.
- This remediation is authorized for exposed Development data only. Final
  Holdout use, a real profitability claim, live trading, automatic merge, and a
  release remain unauthorized.
- New replacement qualification and research evidence is accepted only after
  the branch acceptance record reports every mandatory gate as `PASS`.

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
