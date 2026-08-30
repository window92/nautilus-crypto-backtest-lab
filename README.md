# Nautilus Crypto Backtest Lab

Nautilus Crypto Backtest Lab is a strict, reproducible laboratory for causal,
bar-based cryptocurrency backtests on official Binance data. NautilusTrader
`2.0.0rc2` is the only owner of financial truth: orders, matching, fills,
positions, accounts, portfolio state, PnL, fees, funding settlement, and mark
valuation.

The repository is currently at `1.0.1.dev0`, an authorized audit-remediation
candidate for the two locked BTCUSDT profiles below. A post-release audit found
that the V1 checker could accept unreconciled Spot CASH activity, engine data
beyond the scoring boundary, incompletely attested runtime files, and
insufficiently bound Perpetual funding. The affected historical financial
results and replays are retained byte-for-byte but are `REVOKED` /
`INVALIDATED`; they must not be used as accepted financial evidence.

The recorded V1 test results remain historical facts about the old contract,
not proof under the repaired contract. Current remediation status, contracts,
and validation rules are described in
[docs/AUDIT_REMEDIATION.md](docs/AUDIT_REMEDIATION.md). Nothing in this branch
authorizes Final Holdout use, a profitability claim, or live trading.

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
  immutable `DatasetRelease` identities.
- Exports accepted real market data to Nautilus-compatible
  `ParquetDataCatalog` payloads.
- Runs registered causal strategies through the public Nautilus APIs inside a
  process-level offline boundary.
- Produces immutable run evidence, a read-only checker result, deterministic
  replay identity, research governance records, and Owner reports.

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
                  │
                  ▼
DatasetRelease manifest ──► ParquetDataCatalog
                                  │
                                  ▼
                         NautilusTrader engine
                                  │
                                  ▼
               checker + replay + research evidence/reports
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
  --database data/duckdb/v1.0.0-rebuild/primary.duckdb \
  --catalog-root data/catalog/v1.0.0-rebuild/primary \
  --staging data/duckdb/v1.0.0-rebuild/staging-primary \
  --result data/duckdb/v1.0.0-rebuild/primary-result.json \
  --role PRIMARY
TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONPATH="$DATA_PYTHONPATH" \
  .data-venv/bin/python scripts/build_free_official_binance_release.py \
  --database data/duckdb/v1.0.0-rebuild/independent.duckdb \
  --catalog-root data/catalog/v1.0.0-rebuild/independent \
  --staging data/duckdb/v1.0.0-rebuild/staging-independent \
  --result data/duckdb/v1.0.0-rebuild/independent-result.json \
  --role INDEPENDENT_REBUILD
TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONPATH="$DATA_PYTHONPATH" \
  .data-venv/bin/python scripts/validate_free_official_binance_rebuild.py \
  --primary-result data/duckdb/v1.0.0-rebuild/primary-result.json \
  --independent-result data/duckdb/v1.0.0-rebuild/independent-result.json \
  --primary-catalog-root data/catalog/v1.0.0-rebuild/primary \
  --independent-catalog-root data/catalog/v1.0.0-rebuild/independent \
  --artifact-root data/duckdb/v1.0.0-rebuild/release-artifacts \
  --output data/duckdb/v1.0.0-rebuild/validation.json
```

The validator materializes content-addressed catalogs only after both builds
agree semantically. Compare its output with
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
TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  .venv/bin/python scripts/run_owner_workflow.py \
  --input "$OWNER_INPUT" \
  --repository "$PWD" \
  --output /tmp/owner-workflow-result.json
```

This is not a lightweight local shortcut: the command enforces the official
journal/history checkpoints, creates and pushes ordinary commits where the
workflow requires them, runs the Nautilus child offline, executes the read-only
checker, and writes the report. It refuses reused identities, unqualified data,
dirty source, or ambiguous research authority.

To re-run the checker read-only for the run produced by that workflow result:

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
from crypto_lab.checker import check_evidence_directory

report = check_evidence_directory(
    Path(sys.argv[1]),
    repository_root=Path.cwd(),
    official_source_required=True,
    source_revision_current_head_required=False,
)
print(json.dumps(report.to_builtins(), indent=2, sort_keys=True))
raise SystemExit(0 if report.outcome.value == "CHECK_PASS" else 2)
PY
```

## Results and reports

The following V1 reports are preserved for audit history. Their affected
financial Run results are not currently accepted. The additive status registry
at
`evidence/audit/comprehensive-remediation-001/historical-result-status.json`
is authoritative once generated and committed; it does not edit the original
Run directories.

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

Research results in this repository use exposed Development data unless their
authoritative protocol explicitly says otherwise. They are not investment
advice, do not constitute a real profitability claim, and do not guarantee
future performance.

## Repository and large-data policy

Git contains Product Code, tests, scripts, configuration, the SSOT, lock files,
small DatasetRelease manifests, checksums, and review evidence. It excludes raw
Binance archives, DuckDB payloads, Parquet catalogs, local virtual
environments, secrets, temporary files, and large run caches. A clone therefore
does not include market-data payloads automatically.

See [CHANGELOG.md](CHANGELOG.md) for release history and
[docs/DATA_STORAGE_AND_REBUILD.md](docs/DATA_STORAGE_AND_REBUILD.md) before
moving, backing up, or rebuilding local data.
