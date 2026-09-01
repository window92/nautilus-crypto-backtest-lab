# Changelog

All notable repository releases are documented here. The engineering contract
remains `SSOT.md`; this file does not change normative behavior.

## [Unreleased] - 1.0.1.dev0

### Adversarial audit remediation R2

- Corrected the claim gate's Holdout state model: Development/Validation/OOS
  selections remain ineligible with `FINAL_HOLDOUT_NOT_USED` and
  `CLAIM_INELIGIBLE`, while `HOLDOUT_ALREADY_CONSUMED` is reserved for an
  invalid or consumed selected `FINAL_HOLDOUT`. The three partial retry-010
  Spot primaries and three replays are retained byte-for-byte and additively
  superseded before a complete replacement epoch.
- Removed the static Qualified Profile retry list which made each newly
  generated qualification unreachable until Product Code changed again.
  Owner resolution now orders only canonical `qualification-retry-NNN`
  registries enumerated from Git HEAD, reads the newest authority without an
  older fallback, and requires its worktree bytes to equal the committed blob.
  It also rejects a Registry whose qualification-time executable closure
  differs from current Git HEAD for Owner/Benchmark studies. The explicitly
  non-authorizing Qualification interface fixture retains its isolated-Git
  portability. Plan preparation rejects an explicitly supplied older Registry.
  The valid retry-014 package that exposed the cycle is retained as pre-final
  evidence; retry-015 is the rebuilt current authority after this repair.
- Made `repository_root` a mandatory typed field of Qualification as well as
  Official run requests. Runtime, source, Dataset/catalog, bootstrap, and
  evidence authorities can no longer be inferred from the Product module's
  source-tree or installed-Wheel location; fresh-Wheel tests mutate that
  location and execute the installed Product payload against the explicit
  root.
- Classified every immutable retry-009 Primary/Replay package as
  `SUPERSEDED` through a new closed additive registry after that material
  repository-authority interface repair. The classification does not claim a
  financial defect in those bytes; it prevents them from remaining active and
  requires fresh Product authority, qualification, and Development Runs.
- Enforced the unchanged full signal-interval scoring rule at execution,
  daily/weekly aggregation, low-level strategy, checker, diagnostic, and replay
  boundaries; `decision_timestamp` no longer makes a warmup interval eligible.
- Added an additive v2 historical-result status contract: the four affected
  Spot/Perpetual Candidate A/B primaries and four replays are `REVOKED` /
  `INVALIDATED` for `WARMUP_SCORING_ELIGIBILITY_VIOLATION`; historical bytes
  remain immutable. Old Benchmarks are `SUPERSEDED` under the incompatible v2
  result contract, not financially invalidated by that finding alone. Legacy
  Qualified Profile registries are parse-only and cannot authorize new Runs.
- Bound every submitted intent to the exact native Nautilus client-order
  lifecycle, order projection, native Fill events, and Fill projections,
  including identity-set equality, terminal state, leaves/filled quantities,
  immutable semantic sequence, and rehashed missing/duplicate-link controls.
- Added independent read-only Decimal reconciliation of Perpetual native
  Fills, NETTING position/average-entry transitions, commissions and currency,
  realized/unrealized PnL, funding/account deltas, causal terminal mark, and
  ending Equity. Completed units bind detached native `PositionClosed`
  callback payloads so later NETTING reopen cannot mutate historical close
  evidence. Nautilus remains the sole execution and financial engine.
- Expanded Dataset Release v2 provenance to the complete typed Raw-object
  inventory and require exact bidirectional equality with DuckDB's actually
  used Raw inventory, including roles, locators, identities, sizes, checksums,
  Instrument, profile, and window. Every Official research Run now seals and
  semantically revalidates the committed independent four-way rebuild proof;
  the direct M3 qualification exception is forbidden for research workflows.
- Split component validation from Official publication. New Evidence follows
  the acyclic native-Evidence → component-validation → leaf-manifest → status
  → root-attestation path; only a final revalidating verifier emits
  `OFFICIAL_SEAL_PASS`. The public verifier accepts no injected validator,
  callback, result object, or PASS oracle. Manifest, status, and seal schemas
  reject undeclared fields even when all dependent hashes are recomputed.
- Replaced legacy input-only historical snapshots as executable authority with
  validator v2 bindings over source commit/tree, entrypoint, wrapper, schemas,
  executable closure, arguments, and external file dependencies. Historical
  validators execute from isolated immutable snapshots; external inputs are
  independent read-only copies rather than source-mutable symlinks or
  hardlinks, and pinned modules retain standard `__file__`/origin semantics.
  Each authority also binds the exact exit/status/stdout/stderr observation;
  matching a pinned historical `FAIL` proves rejection rather than being
  relabeled as PASS. Historical path arguments which appear in validator
  output are repository-relative so a random snapshot root cannot make the
  output digest nondeterministic. Normal merge ancestry is required.
- Added a standard-library isolated startup authority before Product Code or
  site-package import. The Official child uses safe isolated flags, an exact
  environment and `sys.path`, and content-addressed Python, Product,
  wheel/`RECORD`, native-extension, dependency, and import-origin identities.
  This authority remains separate from `runtime.lock.json`.
- Unified Official performance comparisons on scoring-only UTC daily marked
  total portfolio Equity for Spot and Perpetual, with `365.2425` annualization,
  causal open-position valuation, minimum 30 daily-return samples for risk
  ratios, and explicit undefined/zero-variance behavior. Every daily native
  snapshot is reconciled to an independent Fill/fee/funding ledger and causal
  valuation, not only at the terminal boundary. Spot uses exact daily
  execution-Bar closes; Perpetual preserves every UTC eight-hour material Mark
  and uses the midnight subset. Stale Instruments/currencies, unpriced state,
  duplicate or unexpected currencies, and missing grid points fail closed.
  Differently sampled Nautilus statistics are diagnostics only.
- Closed a same-timestamp event-order defect exposed by retry-005: the pinned
  Nautilus scheduler's automatic UTC-midnight portfolio snapshot precedes a
  Funding event at that exact timestamp. Official metric families now capture
  another native `PortfolioSnapshot` through the public API after the complete
  Mark/Funding/Bar batch, bind that phase, and component validation reconciles
  every selected day before sealing. The incomplete retry-005 plan and its
  original bytes are retained; all of its primary/replay result packages are
  inactive under additive runtime supersession before a replacement epoch.
- Closed a distinct retry-006 Perpetual checker false negative at a one-quantum
  midpoint: native rc2 linear PnL is calculated in binary64 before the
  `f64_to_fixed_i128` Money boundary, whereas the validator had applied
  Decimal half-even directly. The read-only reconciliation now reproduces the
  exact pinned binary64 scaling, ties-away-from-zero, fixed-point overflow,
  Instrument multiplier, and currency precision. Hard-coded midpoint and
  binary-below controls match the installed wheel, and the retained retry-006
  Perpetual Primary/Replay daily ledgers now reconcile without changing their
  bytes. The failed plan is retained additively; its six completed Spot
  Primary/Replay packages are superseded by exact immutable Evidence hashes,
  while the failed Perpetual package remains failed rather than being relabeled.
- Closed the adjacent multi-cycle defect exposed by the retained retry-007
  Perpetual Candidate A attempt. The earlier checker converted persisted
  Decimals directly to `float`, used an exact-Decimal weighted average, and
  treated native `Position.realized_return` as a net-after-cost return. The
  pinned rc2 Position instead crosses the 16-decimal fixed raw boundary,
  carries binary64 signed quantity/open/close averages, applies its reversal
  test before fixed-precision exact-close normalization, and reports a native
  price return. The read-only validator and daily ledger now reproduce that
  exact order while keeping the economic Decimal ledger separate. A retained
  31-Fill/six-cycle Golden regression reconciles every native Position event,
  completed callback, terminal account, and the former one-quantum daily
  mismatch; an altered average entry still fails with its specific code.
- Added exact negative controls for causal intervals, Spot affordability,
  Perpetual accounting/funding, post-boundary events, full Raw inventory,
  final Evidence inventory, validator identity, runtime startup injection,
  missing/duplicate Fills, and altered Instrument identity.
- Published the final additive schema-v2 qualification at
  `qualification-retry-015`, bound to the post-repair Product/runtime and
  repository authority after the final Raw rebuild re-verification.
  Both exposed profiles have independent Primary/Replay evidence;
  all eight qualification negative controls pass, with no Final Holdout or
  profitability authorization. The pre-final retry-007 through retry-014 packages are
  retained, while their record IDs are rejected by the Owner API so an older
  v2 Registry cannot bypass the current Authority.
- Corrected the offline Dataset builder's locked SSOT SHA to the explicitly
  authorized R2 bytes and added a direct identity regression. The stale lock
  had correctly failed closed but prevented the mandated post-repair rebuild.
  A fresh Primary/independent retry-009 rebuild from the immutable Raw corpus
  preserves the exact semantic database, DatasetRelease, catalog, funding,
  Mark, disposition, market-state, and complete Raw-inventory identities; its
  committed proof differs from retry-008 only in the two physical DuckDB hashes
  and sizes. Independent rehashing covers all 2,243 Raw locators with zero
  checksum/size failures and no network use.
- Published the complete retry-011 local acceptance record and all command
  logs. All 20 phases pass; Full, fresh-process, and reverse-order discovery
  each execute 568 tests with zero skips, targeted mutations execute 140
  tests, and the six Development primaries plus six replays pass independent
  Spot/Perpetual reconciliation, funding, seal, and semantic replay gates.
  The acceptance remains explicitly non-Holdout, non-live, and
  non-profitability-authorizing.
- Extended the closed `FailureCode` vocabulary with
  `RUNTIME_STARTUP_MISMATCH`, `DATASET_RAW_INVENTORY_MISMATCH`,
  `WARMUP_SCORING_ELIGIBILITY_VIOLATION`,
  `PERPETUAL_RECONCILIATION_FAILURE`, `OFFICIAL_SEAL_FAILURE`,
  `HISTORICAL_VALIDATOR_IDENTITY_MISMATCH`,
  `PERFORMANCE_METRICS_INVALID`, and `JOURNAL_DURABILITY_FAILURE`.
- Required machine-readable scientific limitations for bar execution,
  unavailable historical fee/filter proof, absent liquidation modeling,
  leverage 1, marked-not-closed terminal positions, daily-not-intraday
  drawdown, single-Instrument scope, Development-only data, unused Final
  Holdout, no profitability authorization, and no live-trading validation.
- NautilusTrader and dependency versions are unchanged. This work authorizes
  neither Final Holdout use, live trading, a profitability claim, a tag,
  release, automatic merge, squash, nor rebase.

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
