# Adversarial audit remediation

This document is the central non-normative guide to
`ADVERSARIAL_AUDIT_REMEDIATION_002`, based on audited commit
`b5c865c28b83526ffab38152e7e6821f39b77014`. `SSOT.md` remains the sole
engineering authority. The package candidate is `1.0.1.dev0`. The remediation
strengthens verification and evidence; it does not replace NautilusTrader as
the sole execution and financial truth. Only Development-data rebuilds are
authorized. Final Holdout use, a real profitability claim, live trading,
automatic merge, a tag, and a release remain unauthorized.

The previous SSOT and historical Evidence bytes remain preserved at their
original Git identities. New Runs bind the remediated SSOT through their Source
Revision and Official root attestation. No historical Run directory is edited
to make it conform to the new contract.

| SSOT generation | SHA-256 | Meaning |
|---|---|---|
| Audited base at `b5c865c28b83526ffab38152e7e6821f39b77014` | `9232ebca20e3933b8b36538991001880ae54dbbe37b2da322dca2ac6608d0917` | Immutable historical contract bytes |
| R2 remediated contract | `94cc5ac01c6c8c778c1b5332cf1851f238d2082792d389e4c32c0c92206234db` | New contract bytes; new Runs additionally bind their final committed Git Source Revision |

## Authority model

The remediation separates five meanings that older reports could blur:

| Meaning | Authority |
|---|---|
| Engine truth | Native Nautilus orders, Fills, positions, account, fees, funding, PnL, and portfolio state |
| Component validation | Read-only causal, financial, provenance, runtime, and Evidence checks; `COMPONENT_CHECK_PASS` is not publication authority |
| Official seal | Exact final inventory plus manifest/status/root bindings and fresh Product-bound component revalidation; only this non-injectable verifier emits `OFFICIAL_SEAL_PASS` |
| Research eligibility | Protocol, complete journal/history, partition, benchmark, multiplicity, sample, and claim-scope rules |
| Profitability authorization | Not granted by this remediation, regardless of metrics or seal outcome |

## Mandatory merge method

> [!CAUTION]
> **DO NOT SQUASH OR REBASE.** Merge the remediation Pull Request only with
> GitHub's **Create a merge commit** option.

Historical validator v2 binds the exact source commit/tree, entrypoint,
wrapper, schemas, executable closure, arguments, isolated interpreter profile,
external file dependencies, and exact exit/status/stdout/stderr output. It
materializes and executes those exact bytes
from an independent snapshot. Pinned imports preserve standard `__file__` and
module-origin semantics, and every external input is copied into a distinct
read-only inode so the validator cannot escape through a symlink or mutate the
authoritative source through a hardlink. A normal merge preserves every bound
commit and object ID in ancestry. Squash omits commits and rebase creates
different IDs; both therefore fail closed. No historical Commit or Evidence
may be deleted or rewritten.

## Historical-result policy

Historical Evidence bytes, failed trials, provenance, and hash-chain records
are immutable. Existing v1 additive manifests remain historical. The v2 status
authority adds one closed, content-addressed record per logical result and copy
role; every Official resolver rejects a result whose effective status is not
active.

Candidate A/B for Spot and Perpetual, both primary and replay, are classified:

- `historical_run_status: REVOKED`
- `financial_result_status: INVALIDATED`
- `reason_code: WARMUP_SCORING_ELIGIBILITY_VIOLATION`

The v2 record binds the original `checker.json`, `status.json`, and
`evidence_manifest.json` hashes. It is additive: no old Run is rewritten or
deleted. New results use new Dataset, trial, run, protocol, replay, component,
metric, and seal identities.

| Logical result | Primary path | Replay parent / status |
|---|---|---|
| Spot Candidate A | `runs/comprehensive-audit-remediation-003-spot-candidate-a-run-253086685e94` | `runs/replays/comprehensive-audit-remediation-003-spot-candidate-a-development/comprehensive-audit-remediation-003-spot-candidate-a-run-253086685e94`; revoked/invalidated |
| Spot Candidate B | `runs/comprehensive-audit-remediation-003-spot-candidate-b-run-736f07f7755e` | `runs/replays/comprehensive-audit-remediation-003-spot-candidate-b-development/comprehensive-audit-remediation-003-spot-candidate-b-run-736f07f7755e`; revoked/invalidated |
| Perpetual Candidate A | `runs/comprehensive-audit-remediation-003-perpetual-candidate-a-run-5b7c5dba7f8b` | `runs/replays/comprehensive-audit-remediation-003-perpetual-candidate-a-development/comprehensive-audit-remediation-003-perpetual-candidate-a-run-5b7c5dba7f8b`; revoked/invalidated |
| Perpetual Candidate B | `runs/comprehensive-audit-remediation-003-perpetual-candidate-b-run-85bb3192f559` | `runs/replays/comprehensive-audit-remediation-003-perpetual-candidate-b-development/comprehensive-audit-remediation-003-perpetual-candidate-b-run-85bb3192f559`; revoked/invalidated |

The old Spot and Perpetual Benchmarks were not shown to contain this warmup
defect. The incompatible v2 Dataset/component/startup/metric/seal result
contract makes them inactive and the additive registry classifies them
`SUPERSEDED`, not `INVALIDATED` for an unproved financial reason:

- `runs/comprehensive-audit-remediation-003-spot-benchmark-run-d3e25d52686e`
- `runs/comprehensive-audit-remediation-003-perpetual-benchmark-run-a0e2b2553ed4`

The old qualification registries are retained but parse-only under the v2
contract and cannot authorize a new Official Run:

- `evidence/m3/m3-acceptance-001/qualified-profile-registry.json`
- `evidence/audit/comprehensive-remediation-001/qualification/qualified-profile-registry.json`
- `evidence/audit/comprehensive-remediation-001/qualification-runtime-proof/qualified-profile-registry.json`

Current authority must be regenerated as schema v2 from the remediated source,
Runtime/startup authority, full-inventory Dataset Releases, component
validators, and fresh primary/replay qualification. Re-running a current
validator over the legacy bytes does not upgrade them.

The following older registries remain preserved as historical records; they
do not override the v2 result status:

- `historical-result-status.json`: 28 baseline primary/replay records affected
  by F-001/F-002/F-003/F-004.
- `runtime-proof-supersession-status.json`: 12 first-remediation records whose
  positive installed-file identity was not persisted inside each Run.
- `owner-child-entrypoint-supersession-status.json`: 12 second-remediation
  records whose otherwise matching replays emitted the Python duplicate-module
  `RuntimeWarning`; the bytes remain preserved, but they are not final audit
  authority.

## Earlier remediation contracts retained

| Finding | Enforced contract |
|---|---|
| F-001 | Every Spot Fill reconciles exactly to native account and position state, including commission and base/quote balances; funding and inventory guards are causal and fail closed. |
| F-002 | Only warm-up and scoring-eligible observations enter Nautilus; point events at `scoring_end_exclusive` are excluded and the last eligible completed observation alone may value terminal state. |
| F-003 | Runtime acceptance verifies installed-distribution `RECORD`, every recorded hash/size, native extensions, prohibited extras, and reproducible cache exceptions. |
| F-004 | Every Official Perpetual funding settlement binds exactly once to its source event, pre-boundary position, causal mark, rate, currency, and native account delta. |
| F-005 | Mark and Funding conversion/catalog boundaries reject an instrument identity mismatch instead of relabeling it. |
| F-006 | TrialJournal and Holdout read/validate/head/sequence/hash/write transitions are one Linux interprocess-locked operation with crash-safe persistence. |
| F-007 | UTC datetime conversion uses shared integer arithmetic from the Unix epoch; no float timestamp path is accepted. |
| F-008 | Each historical validator resolves its frozen contract snapshot and distinguishes historical validity from current-root drift and evidence corruption. |
| F-009 | `FailureCode` is tested as an exact set against SSOT section 15. |

NautilusTrader remains the sole financial engine. The reconciliation and
funding components are read-only independent checkers; they do not create a
parallel ledger, synthesize fills, or replace native PnL.

## R2 contracts

| ID | Enforced contract and independent negative proof |
|---|---|
| R2-001 | Full signal interval, not `decision_timestamp`, controls scoring eligibility for execution, daily/weekly aggregation, low-level strategy APIs, checker, diagnostics, and replay. A bar `[T0-period,T0)` cannot submit an order. |
| R2-002 | The eight affected Candidate primary/replay copies are additively revoked/invalidated; old Benchmarks become superseded only after replacements. Resolvers reject every inactive status. |
| R2-003 | Decimal read-only Perpetual reconciliation proves Fill order, NETTING position/average entry, commission amount/currency, realized/unrealized PnL, exact funding/account deltas, causal terminal mark, and ending Equity. Every completed cycle additionally binds a detached native `PositionClosed` callback payload so later NETTING reopen cannot mutate the past. Account, position, fee, PnL, Fill, funding, reversal, callback-snapshot, and mark mutations fail. |
| R2-004 | Dataset Release v2 contains the complete typed Raw inventory and proves exact bidirectional equality to the DuckDB used-Raw inventory. Missing, extra, hash, role, locator, Instrument, profile, or window mutations fail. |
| R2-005 | Native Evidence → component validation → exact leaf manifest → status → root attestation is acyclic. A missing, extra, altered, invalid-empty, symlinked, escaped, or cross-identity file cannot receive `OFFICIAL_SEAL_PASS`. The public verifier cannot accept a caller-supplied component validator or PASS oracle. |
| R2-006 | Historical validator v2 binds and executes its source commit/tree, wrapper, entrypoint, schema/dependency closure, arguments, external bindings, and exact exit/status/stdout/stderr output. Current `HEAD` cannot reinterpret old Evidence, and a matching pinned FAIL remains a rejected historical result rather than being relabeled PASS. |
| R2-007 | A standard-library bootstrap verifies isolated flags, exact environment/`sys.path`, Python/Product/dependency/import identities, `RECORD` and native bytes before Product Code import. Startup authority is separate from `runtime.lock.json`. |
| R2-008 | Both profiles use scoring-only UTC daily marked total portfolio Equity, `365.2425` annualization, causal open-position valuation, and explicit minimum-sample/undefined behavior. Every daily native snapshot must reconcile to an independent event ledger: Spot Fills/fees plus the causal daily execution-Bar close; Perpetual Fills/fees/funding plus the UTC-midnight subset of an exact eight-hour material Mark grid. Missing points, stale currencies/Instruments, unpriced state, unexpected currencies, and non-terminal intermediate tampering fail. Native metrics with other semantics are diagnostics only. |
| R2-009 | Post-boundary receipt is derived from actual events; missing Evidence returns structured codes; raw funding lexemes and Instrument/profile identities remain bound; journal/anchor writes and locks fail closed. |
| R2-010 | Targeted mutations must fail for the intended causal, affordability, funding, commission, account, position, terminal mark, Raw inventory, manifest, validator, runtime, Fill, and Instrument contract—not for an incidental reason. |
| R2-011 | Machine-readable claims retain the scientific limits: bar execution, no order book/spread/depth/queue, unproved historical fee/filter detail, no liquidation, leverage 1, terminal mark not close, daily not intraday drawdown, single Instrument, Development only, no Final Holdout/profitability/live authorization. |

## Adjacent post-fix closure

The second adversarial pass also requires these neighboring invariants; they
are not inferred from a passing Manifest hash:

- submitted-intent IDs, native order-event groups, and projected order IDs are
  equal sets; every native/projected Fill belongs to that chain and exact
  lifecycle quantities and terminal status agree;
- the semantic order digest is recomputed from the same immutable native
  events, so rehashing a deleted or duplicated lifecycle link does not help;
- native completed units use detached public `Position.to_dict()` snapshots
  captured at `PositionClosed`, not a cache object observed after reopen;
- the Perpetual material Mark evidence is exactly one correct-Instrument point
  at every UTC eight-hour boundary; the daily metric grid is its exact
  UTC-midnight subset;
- every Spot and Perpetual daily native portfolio snapshot is non-stale,
  complete, currency-exact, and independently reconciled at that timestamp;
- the Official verifier's public signature has no component-validator or PASS
  callback parameter, and all Official resolution paths use that verifier;
- all legacy qualification registries and all twelve old 003 primary/replay
  copies are inactive for current Official resolution. Candidate financial
  results are invalidated; Benchmark results are superseded without claiming
  the same financial defect.

## Historical validator semantics

`contracts/historical-contract-snapshots.json` v1 remains only an
input-integrity record. It cannot establish executable-validator authority and
cannot make a historical result currently acceptable. The v2 authority binds
the exact Git and executable closure and reports:

- `HISTORICAL_EXECUTABLE_SNAPSHOT_VALID`: exact historical executable bytes,
  authority, ancestry, isolated runtime, exit code, status, and complete
  stdout/stderr digests agree with the per-validator observation.
- `HISTORICAL_EXECUTABLE_UNAVAILABLE`: the exact historical executable cannot
  be proved or run; it is not a PASS.
- `LEGACY_CONTRACT_ONLY`: old input bytes match but executable semantics were
  not bound; `acceptable=false`.
- `EVIDENCE_CORRUPT`: the historical snapshot, expected evidence, or validator
  output does not agree. This is fail-closed.

The old `CURRENT_ROOT_DIFFERS_VALIDLY` label is parse-compatible historical
vocabulary only and is not proof that changed validator semantics are valid.
Changing a PASS condition or wrapper, deleting a validator, using a different
commit, or losing ancestry fails with
`HISTORICAL_VALIDATOR_IDENTITY_MISMATCH`. Historical validity never restores a
Run listed in the revocation registry.

Authority execution and Evidence acceptance are separate facts. The v2 batch
gate succeeds only when all fourteen pinned executions match their complete
output contracts, while it reports independently how many historical Evidence
sets the pinned validators actually accept. It does not assume an all-PASS
past. In particular, an exact reproducible `FAIL` is evidence of historical
rejection, not a gate failure and not a financial or Official PASS. Validator
path arguments which are reflected in stdout are bound as canonical
repository-relative targets; the random isolated snapshot directory therefore
cannot change the output digest.

The authority built from Product commit
`c48c2965a61d89ff481c04d434c84e4ba8fdff70` has runtime-authority SHA-256
`ea828a9a09b72457005cca073489f3c43ff31ab92f5c0b6e0962924de7ab109b`,
build-spec SHA-256
`ea7655653922392c1391bf871e8d4d9b92dc09683be2424996baebadb7f28b0f`,
and historical manifest SHA-256
`14af6dbeb28427f1877e2f4945c80d1466fe7f5f056a744c9020fd1aaad2d778`.
Its canonical acceptance record (SHA-256
`ae72d01f5e2e1910729736f051df69f87ebeabd60a486c0f49380265af012f7b`)
matches all fourteen complete output contracts. Twelve pinned validators accept
their Evidence; `validate_audit_qualification.py` and
`validate_audit_research_runs.py` reproducibly return `FAIL` and therefore
reject the corresponding old Evidence. This is historical-authority proof,
not current research or Official-seal acceptance.

## Acceptance evidence

The former `comprehensive-audit-remediation-003` acceptance and its numerical
counts are historical claims under the old checker. They are not R2 acceptance
evidence and MUST NOT be copied into a new report without recomputation. In
particular, deterministic replay of an invalid warmup-triggered Candidate only
repeated the same error.

R2 acceptance records every command, exit code, duration, test count,
failures, errors, skips, and semantic identity. It requires at least:

- full, fresh-process, and reverse-order discovery with zero skip/xfail;
- causal, Spot, Perpetual, funding, boundary, provenance, runtime-startup,
  sealing, historical-validator, journal/Holdout, and mutation regressions;
- full intent → native-order lifecycle → Fill-chain mutations, non-injectable
  Official-sealer control, detached close-callback snapshot/reopen control, and
  intermediate daily ledger-versus-native-snapshot tampering for both profiles;
- two independent data builds, publisher checksum validation, full Raw
  inventory equality, and Dataset/catalog semantic identity;
- a committed four-way rebuild-validation payload sealed into every new
  research Run and rechecked against its exact DatasetRelease/profile;
- installed runtime payload verification, fresh locked-environment install,
  `compileall`, `pip check` for both environments, and `git diff --check`;
- six new Development primaries and six independent replays, each with
  `COMPONENT_CHECK_PASS`, profile reconciliation, deterministic semantic
  equality, and `OFFICIAL_SEAL_PASS`;
- new scoring-only daily marked metrics and machine-readable scientific
  limitations;
- a clean published branch tip and portable GitHub CI.

New Run, replay, Dataset Release, report, registry, and acceptance identities
must be inserted here only after those artifacts exist and their final bytes
have been independently verified. Until then, the absence of a published R2
identity is not a PASS.

The GitHub Actions workflow remains a portable review gate. It installs
the hashed Nautilus dependency and runs compilation plus repository-contained
regressions, but it does not claim Official acceptance: GitHub-hosted machines
cannot satisfy the host-specific Runtime Lock or access the intentionally
untracked market-data catalogs.

No R2 outcome, including `OFFICIAL_SEAL_PASS`, authorizes Final Holdout use, a
profitability claim, investment advice, live trading, a tag, release, automatic
merge, squash, or rebase.
