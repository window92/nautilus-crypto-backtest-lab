# Comprehensive audit remediation

This document is the central non-normative guide to
`COMPREHENSIVE_AUDIT_REMEDIATION_001`. `SSOT.md` remains the sole engineering
authority. Its Owner-adoption status header was corrected under explicit Owner
authorization without changing any engineering, financial, or scientific
contract. The Owner authorized implementation and Development-data reruns
only; Final Holdout use, a real profitability claim, live trading, automatic
merge, a tag, and a release remain unauthorized.

## Mandatory merge method

> [!CAUTION]
> **DO NOT SQUASH OR REBASE.** Merge Pull Request #1 only with GitHub's
> **Create a merge commit** option.

The final research validator locates each report's unique addition commit and
loads the contemporaneous `research/trials.jsonl`,
`research/history_anchors.jsonl`, Holdout, protocol, replay, diagnostics, and
run-manifest bytes from that commit. A normal merge commit preserves every
branch commit and object ID in ancestry, so these snapshots remain resolvable.
Squash merge omits the report-addition commits from the target ancestry; rebase
merge creates different commit IDs. Both therefore violate the historical
snapshot contract and may produce fail-closed validation errors. No historical
Commit or Evidence may be deleted or rewritten.

## Historical-result policy

Historical evidence bytes, failed trials, provenance, and hash-chain records
are immutable. Three additive manifests under
`evidence/audit/comprehensive-remediation-001/` mark the audited baseline and
the two superseded remediation epochs as:

- `historical_run_status: REVOKED`
- `financial_result_status: INVALIDATED`

The manifest binds the original `checker.json`, `status.json`, and
`evidence_manifest.json` hashes and records the current checker outcome. It is
additive: no old Run is rewritten or deleted. New results use new trial, run,
protocol, replay, and evidence identities.

- `historical-result-status.json`: 28 baseline primary/replay records affected
  by F-001/F-002/F-003/F-004.
- `runtime-proof-supersession-status.json`: 12 first-remediation records whose
  positive installed-file identity was not persisted inside each Run.
- `owner-child-entrypoint-supersession-status.json`: 12 second-remediation
  records whose otherwise matching replays emitted the Python duplicate-module
  `RuntimeWarning`; the bytes remain preserved, but they are not final audit
  authority.

## Remediated contracts

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

## Historical validator semantics

`contracts/historical-contract-snapshots.json` binds every historical validator
to the correct ancestor snapshot. `scripts/run_historical_evidence_acceptance.py`
runs the self-contained validators and reports one of these states:

- `HISTORICAL_SNAPSHOT_VALID`: historical evidence and its frozen contract
  agree, and the same bytes are still current.
- `CURRENT_ROOT_DIFFERS_VALIDLY`: historical evidence and its frozen contract
  agree, while a named current root file has legitimately changed.
- `EVIDENCE_CORRUPT`: the historical snapshot, expected evidence, or validator
  output does not agree. This is fail-closed.

Historical validity does not restore financial acceptance to a Run listed in
the revocation registry. It proves preservation and the historical contract,
not correctness under the repaired contract.

## Acceptance evidence

The remediation acceptance runner records each full command, exit code,
duration, test count, failures, errors, and skips. The required record belongs
under `evidence/audit/comprehensive-remediation-001/` and includes full and
fresh-process discovery, reverse order, targeted regressions, historical
validators, data provenance, runtime verification/tamper controls, `compileall`,
and `pip check` for both locked environments. Replacement qualification and
Development research runs are published separately with deterministic replays.

The active qualification is
`qualification-runtime-proof/qualified-profile-registry.json`, with registry
content identity
`f267296baec7886d2a277c7ac7f2e5b2cd9e0566d0818682fad6146bd8f295c8`.
It contains one Spot and one Perpetual qualified record and four independently
revalidated primary/replay Run directories with persisted `runtime_identity.json`.

The final Development-only result epoch is
`comprehensive-audit-remediation-003`. Its central read-only validation is
`final-research-validation.json`, identity
`3fafaba675216a453d9d3a9f05329e1aefe9950e49a47e438669ec7fef26abd6`.
It revalidates all 12 Run directories, the six deterministic replay identities,
the report source snapshots, the current journal/anchor chain, and the empty
Holdout lock. It authorizes neither Final Holdout nor a profitability claim.

| Profile / trial | Primary Run | Replay identity | Report ID |
|---|---|---|---|
| Spot benchmark | `comprehensive-audit-remediation-003-spot-benchmark-run-d3e25d52686e` | `7edbaaf191ae62638384225452124b53c3d6c18f33f68c40673c75738396bb4f` | `36cecb3f692d3fc7f722de5c1f944002d8f4243bd0bf29663f0a210e68f9e7e7` |
| Spot candidate A | `comprehensive-audit-remediation-003-spot-candidate-a-run-253086685e94` | `c8cf2837d311a86c93dce89bd5bd4b059ae22527514a48b8504ea13427fbf2da` | `95fbce923d933808c8e86b1fa2e922db99d10317ce9c3f7e452ba37d823c6dda` |
| Spot candidate B | `comprehensive-audit-remediation-003-spot-candidate-b-run-736f07f7755e` | `c01743e3b92b024d9f5f5b54b7499bd08a9f7f8cddf2aebb6dc78ca243c2b592` | `4b3911b90bb3e2ff6750addb69c7b0e16a06c1fc2c4461a2bf41533b36635004` |
| Perpetual benchmark | `comprehensive-audit-remediation-003-perpetual-benchmark-run-a0e2b2553ed4` | `25f82d464999cdbd5d68609ad829c30f8c72fa6e40ac699fbbeeb42e1830c6ad` | `0f7be820df1cddfda3aad22248946eb58645fe4d964f7e1e897e765447d66238` |
| Perpetual candidate A | `comprehensive-audit-remediation-003-perpetual-candidate-a-run-5b7c5dba7f8b` | `f777d6a5fccd451a4504bb85f6f9a7fdabaca649a0b3139636bce6bcbb72ff90` | `1f1e8639399b8a8463c38448bb628097139ba2a14da3377ba03570956971b301` |
| Perpetual candidate B | `comprehensive-audit-remediation-003-perpetual-candidate-b-run-85bb3192f559` | `82fa0f2304e41399232d59c8e9c80280136ca9d68e3aca22cdec5f1a7b630aed` | `320a54dd4452ed9f711ec75878f93f46701d5a52573cdf52fcf9132aa19276fb` |

All six are `CHECK_PASS` with a `PASS` fresh-process replay and clean child
diagnostics. Spot reconciliation covers 1, 7, and 15 fills respectively.
Each Perpetual Run binds 542 native settlements exactly once to 636 source
Funding events; the other 94 boundaries have no eligible pre-boundary position.

The GitHub Actions workflow is deliberately a portable review gate. It installs
the hashed Nautilus dependency and runs compilation plus repository-contained
regressions, but it does not claim Official acceptance: GitHub-hosted machines
cannot satisfy the host-specific Runtime Lock or access the intentionally
untracked market-data catalogs.

Run the central result validator from a clean, published branch tip:

```bash
TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONHASHSEED=0 PYTHONPATH=src:. \
  .venv/bin/python scripts/validate_audit_research_runs.py
```
