# Nautilus Crypto Backtest Lab

Nautilus Crypto Backtest Lab is a strict, reproducible laboratory for causal,
bar-based cryptocurrency backtests on official Binance data. NautilusTrader
`2.0.0rc2` is the only owner of financial truth: orders, matching, fills,
positions, accounts, portfolio state, PnL, fees, funding settlement, and mark
valuation.

The repository is currently at `1.0.1.dev0`, an authorized adversarial-audit
remediation candidate for the two locked BTCUSDT profiles below. The audit
proved that the previous Development Candidate A/B runs could trade from a bar
whose interval began in warmup, and that the old checker/sealing path did not
independently prove complete Perpetual accounting, full Raw provenance,
startup-time import identity, or final-package completeness. The affected
historical Candidate results and replays are retained byte-for-byte but are
additively `REVOKED` / `INVALIDATED` with reason
`WARMUP_SCORING_ELIGIBILITY_VIOLATION`. Their historical `CHECK_PASS` bytes are
not current Official evidence.

The interrupted retry-010 publication also exposed a claim-reporting defect:
its three Spot Development reports correctly stated that Final Holdout was not
used, but incorrectly labeled that absence as an already consumed Holdout.
Those three primaries and their replays are preserved and additively
`SUPERSEDED` without alleging a financial defect. Current claim evaluation
uses `FINAL_HOLDOUT_NOT_USED` for non-Holdout selections and reserves
`HOLDOUT_ALREADY_CONSUMED` for an invalid selected `FINAL_HOLDOUT`.

All legacy Qualified Profile registries are likewise historical, parse-only
evidence: none can authorize a new Run under the v2 component/startup/data/
metric/seal contract. The old Benchmarks are not accused of the Candidate
warmup defect, but are additively `SUPERSEDED` and inactive under the
incompatible current result contract. No old qualification, primary, or replay
is current Official authority.

The recorded V1 test results remain historical facts about the old contract,
not proof under the repaired contract. Current remediation status, contracts,
and validation rules are described in
[docs/AUDIT_REMEDIATION.md](docs/AUDIT_REMEDIATION.md). Nothing in this branch
authorizes Final Holdout use, a profitability claim, or live trading.

The final local R2 acceptance record is
[`final-acceptance-retry-011/acceptance.json`](evidence/audit/adversarial-remediation-002/final-acceptance-retry-011/acceptance.json).
It binds source commit `e569d669a4c5dce57ee5224a8d31d371bdc33791`, the
fresh retry-009 DuckDB, the locked Nautilus Wheel, and the rebuilt Project
Wheel. All 20 local phases pass, including 568-test Full/Fresh/Reverse runs,
targeted mutations, Raw/provenance gates, both financial reconciliations,
Official seals, deterministic replays, and a fresh installed-Wheel process.
Every published retry-011 result remains Development-only and claim-ineligible.
The GitHub Actions workflow `portable-review-gates` is a portable review gate
only. A green portable CI run is not Official host acceptance. Official
acceptance is the host attestation bound to product-source identity, SSOT,
locks, Raw/DuckDB/Release/catalog identities, and the executed acceptance
runner.

## Merge-history safety

> [!CAUTION]
> **DO NOT SQUASH OR REBASE.** The remediation Pull Request must be merged with
> GitHub's **Create a merge commit** option only.

Historical validator v2 binds the exact source commit/tree, wrapper,
entrypoint, schemas, executable closure, arguments, and external file
bindings, then runs those bytes from an isolated snapshot. A normal merge
retains the bound commits and object IDs as ancestors. Squash discards that
ancestry and rebase replaces commit IDs, so either operation fails closed.
Do not delete, rewrite, or reorder the historical report or Evidence commits.

## Supported V1 scope

| Profile | Instrument | Account/position model | Direction |
|---|---|---|---|
| `BINANCE_SPOT_CASH_LONG_ONLY` | Binance Spot `BTCUSDT` | CASH / NETTING | Long or flat; no borrowing or shorting |
| `BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING` | Binance USDⓈ-M Linear Perpetual `BTCUSDT` | MARGIN / NETTING / leverage 1 | Long or short; one-way only |

The accepted V1 data window is
`[2021-01-01T00:00:00Z, 2021-08-01T00:00:00Z)`. It is exposed Development
data and is not a fresh Final Holdout.

## What the project does

- Preserves official Binance response/archive bytes before parsing and binds
  them by locator, role, size, and SHA-256.
- Reconciles source observations without silent precedence, synthetic prices,
  interpolation, or forward fill.
- Uses DuckDB as a derived canonical validation/query store and creates
  immutable `DatasetRelease` identities whose complete typed Raw inventory
  must equal the DuckDB used-Raw inventory in both directions.
- Exports accepted real market data to Nautilus-compatible
  `ParquetDataCatalog` payloads.
- Verifies an allowlisted isolated Python startup before Product Code or
  Nautilus imports, then runs registered causal strategies through public
  Nautilus APIs inside a process-level offline boundary.
- Preserves Engine truth, performs independent read-only Spot/Perpetual
  reconciliation and causal/component validation, then verifies an exact
  acyclic Official seal over the final Evidence package.
- Binds every submitted intent to its complete native Nautilus order lifecycle
  and exact Fill projections; orphaned, missing, duplicate, or rehashed links
  fail the component checker. Completed cycles use detached native
  `PositionClosed` callback snapshots, so a later NETTING reopen cannot mutate
  earlier completed-position evidence.
- Uses scoring-window daily marked total portfolio Equity as the single
  Official performance basis and reconciles every native daily snapshot to an
  independent event replay. Spot uses causal daily execution-Bar closes;
  Perpetual retains an exact eight-hour material Mark grid whose UTC-midnight
  subset values the daily series. Missing boundaries, unexpected currencies,
  or any stale Instrument/currency/unpriced marker fail closed. Differently
  sampled Nautilus statistics remain diagnostics only.

It does not provide live trading, exchange connectivity for orders, an order
book simulator, tick-level execution, parameter optimization, automatic
Holdout authorization, or a profitability guarantee. DuckDB is not a matching,
ledger, funding, portfolio, or PnL engine.

## Architecture

```text
official Binance bytes + publisher checksums
                  │
                  ▼
content-addressed raw store (authority; immutable)
                  │
                  ▼
DuckDB validation / conflicts / minute dispositions
       ║ exact bidirectional used-Raw inventory
                  │
                  ▼
DatasetRelease manifest ──► ParquetDataCatalog
                                  │
                                  ▼
               isolated startup authority/bootstrap
                                  │
                                  ▼
                         NautilusTrader engine truth
                                  │
                                  ▼
                    native immutable Evidence
                                  │
                                  ▼
              component validation + independent reconciliation
                                  │
                                  ▼
               manifest → status → root attestation → Official seal
                                  │
                                  ▼
                 eligible sealed research evidence/reports
```

`SSOT.md` is the sole engineering authority. The shorter release contract is
in [docs/RELEASE_V1.md](docs/RELEASE_V1.md), and local data handling is in
[docs/DATA_STORAGE_AND_REBUILD.md](docs/DATA_STORAGE_AND_REBUILD.md).

## System requirements

- Linux x86-64 with glibc `2.39` (the wheel requires glibc `>=2.34`).
- CPython `3.12.3` / ABI `cp312`.
- UTC and locale `C.UTF-8` for locked execution.
- Enough local storage for official raw data, two DuckDB rebuilds, and Parquet
  catalogs. These payloads are intentionally absent from Git.
- Git for authoritative history; network access is used only during explicit
  setup/acquisition and push operations, never during an Official Run.

## Create the project runtime

Download the exact official Nautilus wheel during an explicit network-enabled
setup step, verify it, and install it locally. Do not install it globally.

```bash
python3.12 -m pip download --no-deps --only-binary=:all: \
  --dest /tmp/nautilus-v1-wheel nautilus_trader==2.0.0rc2
echo '716169aca15bfb615a27610a9230e670dec5be3d4606fea591fe64eca145a5ac  /tmp/nautilus-v1-wheel/nautilus_trader-2.0.0rc2-cp312-cp312-manylinux_2_34_x86_64.whl' \
  | sha256sum --check --strict
python3.12 -m venv .venv
.venv/bin/python -m pip install --no-deps \
  /tmp/nautilus-v1-wheel/nautilus_trader-2.0.0rc2-cp312-cp312-manylinux_2_34_x86_64.whl
.venv/bin/python -m pip check
```

Verify the complete runtime identity:

```bash
TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONPATH=src \
  .venv/bin/python - <<'PY'
from pathlib import Path
from crypto_lab.config import RuntimeLock
from crypto_lab.runtime import verify_runtime_lock

root = Path.cwd()
identity = verify_runtime_lock(
    RuntimeLock.from_json_bytes((root / "runtime.lock.json").read_bytes()),
    dependency_lock_path=root / "requirements.lock.txt",
)
print(identity["nautilus_version"], identity["installed_wheel_sha256"])
PY
```

The expected output is `2.0.0rc2` followed by
`716169aca15bfb615a27610a9230e670dec5be3d4606fea591fe64eca145a5ac`.

Both Qualification and Official run requests bind an explicit
`repository_root`. The installed `crypto_lab` Wheel location is never treated
as the repository authority: Runtime/Dependency Locks, Source Revision,
Dataset/catalog inputs, bootstrap authority, and Evidence resolve only below
the caller-bound root. This is required for the same Product Wheel to execute
from an isolated environment without silently looking for authority files in
`site-packages`.

That interactive command is a runtime diagnostic, not an Official startup.
An Official child starts through the standard-library bootstrap with
`-I -P -S -B -X pycache_prefix=/dev/null`, an exact environment allowlist,
and the content-addressed `runtime-bootstrap-authority.json`. The authority
binds Product Code/import origins and installed `RECORD`/native payloads before
any Product or dependency import. It is separately bound by Source Revision
and the root attestation; it is deliberately not a project-Git field in
`runtime.lock.json`.

## Obtain and rebuild official Binance data

Data acquisition is a separate network-enabled phase. The acquisition drivers
accept only the official source allowlist and preserve response bytes before
parsing:

```bash
TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONPATH=src \
  .venv/bin/python scripts/run_data_provenance_repair.py acquire
TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONPATH=src \
  .venv/bin/python \
  evidence/repair/free-official-binance-data-duckdb-001/tools/acquire_phase_a.py acquire
TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONPATH=src \
  .venv/bin/python \
  evidence/repair/free-official-binance-data-duckdb-001/tools/analyze_phase_a.py
```

The exact accepted V1 identities require the complete attested raw-object set,
including the official historical order-grid observations. A current
re-download that differs by one byte or observation identity must fail closed
and become a new candidate release; it must not be relabeled as V1. Follow the
complete acquisition and rebuild procedure in
[docs/DATA_STORAGE_AND_REBUILD.md](docs/DATA_STORAGE_AND_REBUILD.md).

Install DuckDB `1.4.5` in the independent data-tool environment, then run two
fresh builds and the semantic comparator:

```bash
python3.12 -m venv .data-venv
mkdir -p .data-wheelhouse
python3.12 -m pip download --no-deps --only-binary=:all: \
  --dest .data-wheelhouse duckdb==1.4.5
.data-venv/bin/python -m pip install --no-index --no-deps \
  --find-links .data-wheelhouse --require-hashes -r requirements.data.lock.txt

DATA_PYTHONPATH="$PWD/src:$PWD:$PWD/.venv/lib/python3.12/site-packages"
TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONPATH="$DATA_PYTHONPATH" \
  .data-venv/bin/python scripts/build_free_official_binance_release.py \
  --database data/duckdb/adversarial-remediation-002/primary.duckdb \
  --catalog-root data/catalog/adversarial-remediation-002/primary \
  --staging data/duckdb/adversarial-remediation-002/staging-primary \
  --result data/duckdb/adversarial-remediation-002/primary-result.json \
  --role PRIMARY
TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONPATH="$DATA_PYTHONPATH" \
  .data-venv/bin/python scripts/build_free_official_binance_release.py \
  --database data/duckdb/adversarial-remediation-002/independent.duckdb \
  --catalog-root data/catalog/adversarial-remediation-002/independent \
  --staging data/duckdb/adversarial-remediation-002/staging-independent \
  --result data/duckdb/adversarial-remediation-002/independent-result.json \
  --role INDEPENDENT_REBUILD
TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONPATH="$DATA_PYTHONPATH" \
  .data-venv/bin/python scripts/validate_free_official_binance_rebuild.py \
  --primary-result data/duckdb/adversarial-remediation-002/primary-result.json \
  --independent-result data/duckdb/adversarial-remediation-002/independent-result.json \
  --primary-catalog-root data/catalog/adversarial-remediation-002/primary \
  --independent-catalog-root data/catalog/adversarial-remediation-002/independent \
  --artifact-root data/duckdb/adversarial-remediation-002/release-artifacts \
  --output evidence/audit/adversarial-remediation-002/data-rebuild-validation.json
```

The validator materializes content-addressed catalogs only after both builds
agree semantically. Commit the canonical validation output before any new
research Run; each such Run carries the same payload as a sealed leaf and the
checker revalidates its selected release/profile record. Compare its output with
[release/v1.0.0-manifest.json](release/v1.0.0-manifest.json).

## Run a backtest

The supported public entry point is the strict Owner Workflow. It deliberately
has no material defaults: first create and review an `OwnerWorkflowInput` JSON
with a registered strategy, frozen protocol, new trial/run identities, accepted
DatasetRelease, and qualified profile. Then run from a clean commit whose
`HEAD` equals the matching published remote branch tip (for example
`origin/fix/comprehensive-audit-remediation` during review, or `origin/main`
after an Owner-approved merge):

```bash
OWNER_INPUT=/tmp/strict-owner-workflow.json
test -f "$OWNER_INPUT"
env -i PATH=/usr/bin:/bin LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC \
  .venv/bin/python -I -P -S -B -X pycache_prefix=/dev/null \
  scripts/isolated_runtime_bootstrap.py \
  --authority runtime-bootstrap-authority.json \
  --repository "$PWD" \
  --entrypoint crypto_lab.owner:main -- \
  --input "$OWNER_INPUT" \
  --repository "$PWD" \
  --output /tmp/owner-workflow-result.json
```

This is not a lightweight local shortcut: the command enforces the official
journal/history checkpoints, creates and pushes ordinary commits where the
workflow requires them, starts both the outer coordinator and the Nautilus
children through the isolated bootstrap and offline boundary, executes
component validators and final seal verification, and writes the report. A
direct `PYTHONPATH`/wrapper launch is not an Official R2 command. The workflow
refuses reused identities, unqualified data, dirty source, or ambiguous
research authority.

To re-run final verification read-only for the sealed Run produced by that
workflow result:

```bash
RUN_ID="$(.venv/bin/python -c \
  'import json; print(json.load(open("/tmp/owner-workflow-result.json"))["run_id"])')"
RUN_DIR="$(find runs -mindepth 1 -maxdepth 1 -type d -name "${RUN_ID}-*" -print -quit)"
test -n "$RUN_DIR" && test -d "$RUN_DIR"
TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONPATH=src \
  .venv/bin/python - "$RUN_DIR" <<'PY'
import json
import sys
from pathlib import Path
from crypto_lab.sealing import OfficialSealOutcome, verify_official_seal

report = verify_official_seal(
    Path(sys.argv[1]),
    repository_root=Path.cwd(),
    source_revision_current_head_required=False,
)
print(json.dumps(report.to_builtins(), indent=2, sort_keys=True))
raise SystemExit(
    0 if report.outcome is OfficialSealOutcome.OFFICIAL_SEAL_PASS else 2
)
PY
```

`component_validation.json` may contain `COMPONENT_CHECK_PASS`; that is not an
Official result by itself. Only the final verifier may emit
`OFFICIAL_SEAL_PASS` after checking exact inventory, root bindings, and a fresh
read-only component revalidation. Its public API accepts no caller-supplied
validator, callback, or PASS oracle; it resolves the Product-bound component
checker from the attested source closure.

## Results and reports

The pre-remediation V1 reports are preserved for audit history. Their affected
financial Run results are not accepted. The three additive v1 status
registries under `evidence/audit/comprehensive-remediation-001/` remain
historical, while the R2 v2 registry supplies the effective status for the
audited 003 results. Neither form edits the original Run directories.

The former Development-only `comprehensive-audit-remediation-003` Candidates
are not replacement authority: Candidate A/B for both profiles, primary and
replay, are `REVOKED` / `INVALIDATED`. Their Benchmark peers were not shown to
have the same warmup defect, but are `SUPERSEDED` rather than `INVALIDATED`
because the schema-v2 Dataset, component, metric, runtime, and Official-seal
contract is incompatible with their evidence. The old schema-v1 qualification
registries are parse-only and cannot authorize replacement Runs. See
[docs/AUDIT_REMEDIATION.md](docs/AUDIT_REMEDIATION.md) for the exact historical
identities and additive status policy.

The current replacement qualification is the additive schema-v2 package at
`evidence/audit/adversarial-remediation-002/qualification-retry-016`, generated
from clean local/remote commit `3580cee854a5f9f90d3ceb3f2bd6ec2db929769d`.
It authorizes only the two exposed Development profiles; it does not authorize
Final Holdout use, a profitability claim, or live trading. Older v2
qualification records, including retry-015, remain historical and are rejected
by the Owner API.

New Development Runs are publishable only after full Raw inventory equality,
isolated startup verification, independent Spot/Perpetual reconciliation,
causal component validation, deterministic fresh-process replay, and
`OFFICIAL_SEAL_PASS`. Even then they remain `DEVELOPMENT_ONLY_DATA` and
`NO_PROFITABILITY_AUTHORIZATION`. Final Holdout is not used and the Holdout
lock remains empty.

The retained retry-005 epoch is not replacement authority. Its first
Perpetual workflow demonstrated that the pinned runtime's scheduled midnight
portfolio snapshot occurs before Funding with the same timestamp; the
independent daily reconciliation rejected that intermediate state even though
terminal finance reconciled. The replacement runner captures a second native
snapshot after the complete timestamp batch, and the component checker must
reconcile it before sealing. All retry-005 primary/replay packages are inactive
through the additive status authority; none of their bytes were rewritten.

- Trial JSON/Markdown reports: `research/reports/`.
- Immutable run evidence: `runs/` and `runs/replays/`.
- Historical Owner research report:
  [evidence/research/owner-strategy-research-001/owner-report/README.md](evidence/research/owner-strategy-research-001/owner-report/README.md).
- Historical mechanical integrity report:
  [evidence/research/owner-strategy-research-001/mechanical-integrity/README.md](evidence/research/owner-strategy-research-001/mechanical-integrity/README.md).
- Data repair report:
  [evidence/repair/instrument-representation-funding-checker-001/owner-report/README.md](evidence/repair/instrument-representation-funding-checker-001/owner-report/README.md).

Open any `README.md` directly in GitHub, or serve the repository through your
normal authenticated GitHub browser session. Historical failed, blocked, and
now-revoked attempts remain part of the evidence and are never rewritten.

## Simulation limits and disclaimer

V1 is bar-based. `DefaultFillModel(1.0, 1.0, 0)` and the locked latency/fee
contracts do not recreate an exchange order book, queue position, tick path,
market impact, or all live slippage conditions. A Fill is a Nautilus simulation
result constrained by available causal bar state, not a promise of live
execution quality.

The Evidence does not prove the historical account fee tier or every 2021
exchange filter. V1 has no liquidation simulation, fixes Perpetual leverage at
1, marks terminal open positions instead of actually closing them, measures
Official drawdown daily rather than intraday, and supports only the single
BTCUSDT Instrument in each locked profile. The report schema must preserve
these limitations; a valid seal cannot turn them into modeled facts.

Research results in this repository use exposed Development data unless their
authoritative protocol explicitly says otherwise. They are not investment
advice, do not constitute a real profitability claim, and do not guarantee
future performance. They are not validated for live trading.

## Repository and large-data policy

Git contains Product Code, tests, scripts, configuration, the SSOT, lock files,
small DatasetRelease manifests, checksums, and review evidence. It excludes raw
Binance archives, DuckDB payloads, Parquet catalogs, local virtual
environments, secrets, temporary files, and large run caches. A clone therefore
does not include market-data payloads automatically.

See [CHANGELOG.md](CHANGELOG.md) for release history and
[docs/DATA_STORAGE_AND_REBUILD.md](docs/DATA_STORAGE_AND_REBUILD.md) before
moving, backing up, or rebuilding local data.
