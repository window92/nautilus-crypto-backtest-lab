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
| R2 remediated contract | `c55f73ef591926081dbdaecb59002c60dad62fa13045fa393ab035353b196142` | New contract bytes; new Runs additionally bind their final committed Git Source Revision |
| R2 pinned-native-precision addendum | `cc71cccda50e5905cfa3d4fbba12062df739c871538944f4c32c686d128d006d` | Strengthens Perpetual fixed/binary64 reconciliation after the retained retry-007 Candidate A negative control; the preceding R2 identity remains historical |
| R2 explicit repository-authority addendum | `9dbf82fee879a7ae0865b77619e2966cc9b9e1cea86cf8b85674e3a20983d256` | Requires Qualification and Official requests to resolve all external authority from a caller-bound repository root; package/install location inference is forbidden |
| R2 Holdout claim-state clarification | `ab78a388ee6727cb5409504e8e63e2543ad4827ed2cb3c2a2cb7489d66532947` | Preserves the prohibition on Final Holdout use while distinguishing an unused Final Holdout from an invalid or previously consumed selected Final Holdout |

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

The current schema-v2 qualification authority is
`evidence/audit/adversarial-remediation-002/qualification-retry-016`. It was
generated from clean local/remote commit
`3580cee854a5f9f90d3ceb3f2bd6ec2db929769d`, after the L-1..L-4 freeze and the
matching isolated runtime bootstrap rebuild. Its exact Manifest SHA-256 is
`75dc65b4b58f2d0ca682b6532755f43c1bc5568fc1f668bbfd50e52f4546755f`;
the Qualified Profile Registry file SHA-256 is
`39f2cbf62618e34e2d3d7c1038eacb901a256b4a988db7bb040cb1e1c32082d2`.
Both profiles' fresh Primary and Replay component checks pass, all eight
qualification negative controls are bound, and the validators record
`final_holdout_used=false` and `profitability_claim_authorized=false`.
Owner resolution discovers canonical committed retry authorities newest-first,
requires an unchanged executable closure, and accepts records from this current
Registry only. `qualification-retry-007` through `qualification-retry-015` are
retained additive pre-final qualification history; their otherwise valid v2
record IDs cannot authorize a new Owner workflow. Re-running a current validator over legacy
bytes does not upgrade them.

The current Development epoch is `adversarial-remediation-002-retry-012`,
bound by the ACTIVE execution-plan pointer. Retry-011 remains
`SUPERSEDED`. Host Acceptance for this epoch is recorded at
`evidence/audit/adversarial-remediation-002/final-acceptance-retry-012`
(20/20 PASS, Full/Fresh/Reverse discovery 597 tests, no Final Holdout).

After the data-builder SSOT binding was repaired, a fresh two-build
Raw-to-DuckDB-and-catalog verification was executed from clean local/remote
commit `e79f70a63aa03154522d961a35f1d84a8059b45a`. The additive canonical proof
is `data-rebuild-reverification-retry-007.json` (SHA-256
`ebbf5fee5d4bf4ec4195a18aaa08da123866f70d4b82cc99233155a8d31bbb49`).
The Primary and independent physical DuckDB hashes differ, as permitted, but
the validator reports the same semantic database identity
`df7aeddd2f8cd2274cc6ccb5e568c98ffd896fd69e608067442b0182f0332c7b`,
the same two DatasetRelease identities, the same two catalog identities, and
exact four-way inventory equality for all 774 Spot and 1457 Perpetual Raw
objects. A recursive comparison with the prior committed proof differs only
in the two physical database hashes and sizes; no release, catalog, Raw
inventory, table, row-count, disposition, funding, Mark, or market-state
semantic changed. The large retry databases are temporary rebuild products,
not replacement historical Evidence.

After the final R2 Product authority and repository-authority supersession
were published, a second fresh two-build verification was executed from clean
local/remote commit `c991dd4ae8669d17f8349cd8b28fb0f21383c1a1` without network access or a
strategy/Official trial. Its additive proof is
`data-rebuild-reverification-retry-008.json` (SHA-256
`f46d6bacc4e03fc14b0678fc6d3073766cce6d90f4b080a89273b4e8e7e4e30b`).
Both builds again report semantic database identity
`df7aeddd2f8cd2274cc6ccb5e568c98ffd896fd69e608067442b0182f0332c7b`,
the same two DatasetRelease and catalog identities, exact four-way equality
for 774 Spot and 1457 Perpetual Raw objects, and zero publisher-checksum
failures. A recursive comparison with retry-007 differs only in the two
permitted physical DuckDB hashes and sizes. The retry-008 databases, staging
trees, and duplicate catalogs are temporary rebuild products, not historical
Evidence or replacements for the accepted content-addressed releases.

The first final Full/Fresh/Reverse acceptance over retry-011 then found one
fail-closed authority mismatch: the later Owner-authorized Holdout claim-state
clarification had changed the SSOT bytes, while the offline builder preflight
still pinned the preceding SSOT identity. No data transform or accepted release
had changed. Commit `6260725d75f907f80e14fe0ea42df5beb0080876` updates only
that exact preflight binding and records the defect additively. Two fresh
offline builds from the immutable Raw corpus produced the additive proof
`data-rebuild-reverification-retry-009.json` (SHA-256
`8139406a9eae5f27f59d6feae975da5b31c3612ee4112ee411f8746e2db653b9`).
Its Primary and independent DuckDB hashes are respectively
`9d54177faadd64ad5bbc9c3fadd10749919c48b1fba41a0735e32822d4bdc6bc`
and `d796740ffbb3c341a304d1272746d1e030c2f33370df23df2212f666f3e5cb24`;
both are 1,761,095,680 bytes. The semantic database, DatasetRelease, catalog,
table, row-count, funding, Mark, disposition, market-state, and complete Raw
inventory identities remain equal to retry-008. A recursive comparison differs
at exactly four permitted fields: the two physical hashes and two physical
sizes. The independent Raw validator rehashed 2,243 source locators with zero
hash/size failures and no network use; the read-only semantic gate again proves
four-way equality for all 774 Spot and 1,457 Perpetual objects. This additive
post-repair proof does not rewrite the retry-008 proof sealed into the immutable
retry-011 Runs.

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

Retry-005 is likewise retained as a failed qualification epoch. Its three Spot
workflows reached reports, but the first Perpetual workflow exposed a distinct
same-timestamp defect: NautilusTrader `2.0.0rc2` produced its scheduled
UTC-midnight `PortfolioSnapshot` before processing Funding at that timestamp.
The terminal ledger reconciled, while the intermediate daily snapshot omitted
the exact Funding debit and therefore could not support Official daily
performance metrics. The plan, four Owner result summaries, mismatch values,
and immutable evidence hashes are bound under
`evidence/audit/adversarial-remediation-002/failed-plans/c8fc32a-*`. No report
was published for the failed Perpetual trial, its two Perpetual Candidate
workflows were never started, and the retry-005 result packages are made
inactive additively before any replacement epoch; their Run/Replay bytes are
not edited.

Retry-006 is also retained. Its three Spot workflows completed, but the first
Perpetual workflow exposed a separate checker false negative at one daily
valuation. The exact Decimal price PnL was `2244.52758836500`; Decimal
half-even produced `2244.52758836`, while the pinned rc2 path first evaluated
the linear formula in IEEE-754 binary64 and its native `Money` result was
`2244.52758837`. Primary and Replay were semantically identical and both
failed closed. The repaired read-only validator now reproduces the pinned
`f64_to_fixed_i128` currency boundary exactly—including binary64 scaling,
ties-away-from-zero, fixed-point overflow, and the Instrument multiplier—while
keeping all event/account arithmetic in Decimal and never feeding the replay
back to Nautilus. The plan, Owner summaries, mismatch, and immutable evidence
hashes are retained under
`evidence/audit/adversarial-remediation-002/failed-plans/dc5a80d-*`. The failed
Perpetual Run/Replay remain unchanged. The six completed Spot Primary/Replay
packages are additively `SUPERSEDED` by the 24-record runtime-authority
registry (`registry_identity`
`b25d87f0c56be89a8aa8c61f7aa351833e0b0c265f006f7f56e017d7cbc7f047`);
a new complete epoch is required under rebuilt runtime authority.

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
| R2-003 | Decimal read-only Perpetual reconciliation proves Fill order, NETTING position/average entry, commission amount/currency, realized/unrealized PnL, exact funding/account deltas, causal terminal mark, and ending Equity. Native fixed `Price`/`Quantity`/`Money` conversion, signed-quantity accumulation, open/close averages, partial reductions, exact-close normalization, price return, and binary64-to-`Money` boundaries replay the pinned rc2 operation order rather than Decimal or direct-float shortcuts. Every completed cycle additionally binds a detached native `PositionClosed` callback payload so later NETTING reopen cannot mutate the past. Account, position, fee, PnL, Fill, funding, reversal, callback-snapshot, mark, multiplier, and rounding mutations fail. |
| R2-004 | Dataset Release v2 contains the complete typed Raw inventory and proves exact bidirectional equality to the DuckDB used-Raw inventory. Missing, extra, hash, role, locator, Instrument, profile, or window mutations fail. |
| R2-005 | Native Evidence → component validation → exact leaf manifest → status → root attestation is acyclic. A missing, extra, altered, invalid-empty, symlinked, escaped, or cross-identity file cannot receive `OFFICIAL_SEAL_PASS`. The public verifier cannot accept a caller-supplied component validator or PASS oracle. |
| R2-006 | Historical validator v2 binds and executes its source commit/tree, wrapper, entrypoint, schema/dependency closure, arguments, external bindings, and exact exit/status/stdout/stderr output. Current `HEAD` cannot reinterpret old Evidence, and a matching pinned FAIL remains a rejected historical result rather than being relabeled PASS. |
| R2-007 | A standard-library bootstrap verifies isolated flags, exact environment/`sys.path`, Python/Product/dependency/import identities, `RECORD` and native bytes before Product Code import. Startup authority is separate from `runtime.lock.json`. |
| R2-008 | Both profiles use scoring-only UTC daily marked total portfolio Equity, `365.2425` annualization, causal open-position valuation, and explicit minimum-sample/undefined behavior. Every daily native snapshot must reconcile to an independent event ledger: Spot Fills/fees plus the causal daily execution-Bar close; Perpetual Fills/fees/funding plus the UTC-midnight subset of an exact eight-hour material Mark grid and the same pinned rc2 fixed/binary64 arithmetic as terminal reconciliation. Missing points, stale currencies/Instruments, unpriced state, unexpected currencies, and non-terminal intermediate tampering fail. Native metrics with other semantics are diagnostics only. |
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
- an Official metric snapshot at a timestamp shared with Mark, Funding, and a
  Bar is captured only after the complete same-timestamp batch through
  Nautilus's public `Portfolio.build_snapshot` API; the project neither
  computes that native state nor posts a financial event;
- the Official verifier's public signature has no component-validator or PASS
  callback parameter, and all Official resolution paths use that verifier;
- all legacy qualification registries and all twelve old 003 primary/replay
  copies are inactive for current Official resolution. Candidate financial
  results are invalidated; Benchmark results are superseded without claiming
  the same financial defect.
- Development claim evaluation preserves the difference between
  `FINAL_HOLDOUT_NOT_USED` and an invalid/consumed selected Final Holdout. The
  three partial retry-010 Spot primaries and their replays are additively
  `SUPERSEDED`, not financially invalidated, because their reports conflated
  those states; a fresh six-Run epoch is required.

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
`ff7d28325e9cd5bcfecbc7a352cc9877201c6740` has project-runtime authority
SHA-256
`eca75af0742c2613649753313d2e04dcff1153b666b92a9125d65aecdfe74759`,
combined data-runtime authority SHA-256
`6bd3ebf93b62a7368c032f4672c3c561ef445a3906974c56de81b849d7c55617`,
expected-results SHA-256
`6b8a25b3c9eecfccc138328b0801002c146330826f2a2cb84d9bf5e5659c76ff`,
build-spec SHA-256
`382d7cf61d02c89c9edf311422c7ab29639bf1f5ac0d563003655d35b044c5a7`,
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

The final local acceptance record is
`final-acceptance-retry-011/acceptance.json` (file SHA-256
`a0d63b8afb7b6a926b2b6e3433fa710595dd53b49314e2ca6586ccd0d2b23004`,
semantic acceptance identity
`c91ea6e5eb54929ad61c51043ab3e9992d86899fbeaa3596464772efb5290f8c`).
It binds source commit `e569d669a4c5dce57ee5224a8d31d371bdc33791`,
retry-011 plan epoch, retry-009 Primary DuckDB SHA-256
`9d54177faadd64ad5bbc9c3fadd10749919c48b1fba41a0735e32822d4bdc6bc`,
locked Nautilus Wheel SHA-256
`716169aca15bfb615a27610a9230e670dec5be3d4606fea591fe64eca145a5ac`,
and Project Wheel SHA-256
`88c7b6c314fb29d157f9d2595ee6ef99c651857bd30f7a3fde0c15de836a0ffd`.
All 20 phases pass with zero failures, errors, or skips; Full, independent
fresh-process, and reverse-order discovery each execute 568 tests. The record
also preserves every command log, the 12-directory independent Run/replay
validation, and the historical-validator result. It explicitly records no
network use, no Final Holdout, no live trading, and no profitability
authorization. Its later Evidence-publication commit changes no Product,
financial, causal, DatasetRelease, catalog, Run, replay, or accepted Wheel
bytes.

The GitHub Actions workflow remains a portable review gate. It installs
the hashed Nautilus dependency and runs compilation plus repository-contained
regressions, but it does not claim Official acceptance: GitHub-hosted machines
cannot satisfy the host-specific Runtime Lock or access the intentionally
untracked market-data catalogs.

No R2 outcome, including `OFFICIAL_SEAL_PASS`, authorizes Final Holdout use, a
profitability claim, investment advice, live trading, a tag, release, automatic
merge, squash, or rebase.
