# Comprehensive audit remediation

This document is the central non-normative guide to
`COMPREHENSIVE_AUDIT_REMEDIATION_001`. `SSOT.md` remains the sole engineering
authority and has not been modified. The Owner authorized implementation and
Development-data reruns only; Final Holdout use, a real profitability claim,
live trading, automatic merge, a tag, and a release remain unauthorized.

## Historical-result policy

Historical evidence bytes, failed trials, provenance, and hash-chain records
are immutable. A separate manifest at
`evidence/audit/comprehensive-remediation-001/historical-result-status.json`
marks each audited primary Run and replay as:

- `historical_run_status: REVOKED`
- `financial_result_status: INVALIDATED`

The manifest binds the original `checker.json`, `status.json`, and
`evidence_manifest.json` hashes and records the current checker outcome. It is
additive: no old Run is rewritten or deleted. New results use new trial, run,
protocol, replay, and evidence identities.

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

The GitHub Actions workflow is deliberately a portable review gate. It installs
the hashed Nautilus dependency and runs compilation plus repository-contained
regressions, but it does not claim Official acceptance: GitHub-hosted machines
cannot satisfy the host-specific Runtime Lock or access the intentionally
untracked market-data catalogs.
