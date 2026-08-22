# Nautilus Crypto Backtest Lab

A strict, reproducible cryptocurrency backtesting laboratory built on NautilusTrader.

## Engineering Authority

`SSOT.md` is the sole engineering authority for this repository.

## Locked Runtime

NautilusTrader v2.0.0rc2, using the locked official Rust/PyO3 wheel and public
Python API.

## Build Sequence

M0 → M1 → M2 → M3 → M4

Implementation must follow the contracts, qualification requirements, acceptance conditions, and stop conditions defined in `SSOT.md`.

## M0 foundation

M0 provides only the strict configuration/runtime foundation. It does not load
market data, run a strategy, or implement M1–M4 behavior.

The qualified environment is the repository-local `.venv` created with CPython
3.12. Acquire the exact wheel during an explicit network-enabled setup step,
verify its bytes before installation, then install the remaining fully hashed
lock. Installing Nautilus from the verified local file also preserves its wheel
filename and archive digest in `direct_url.json` for runtime preflight:

```bash
python3.12 -m pip download --no-deps --only-binary=:all: \
  --dest /tmp/nautilus-m0-wheel nautilus_trader==2.0.0rc2
sha256sum /tmp/nautilus-m0-wheel/nautilus_trader-2.0.0rc2-cp312-cp312-manylinux_2_34_x86_64.whl
# Required digest: 716169aca15bfb615a27610a9230e670dec5be3d4606fea591fe64eca145a5ac
python3.12 -m venv .venv
.venv/bin/python -m pip install --no-deps \
  /tmp/nautilus-m0-wheel/nautilus_trader-2.0.0rc2-cp312-cp312-manylinux_2_34_x86_64.whl
.venv/bin/python -m pip check
```

Run the M0 suite and regenerate real-runtime evidence with the locked UTC and
locale settings:

```bash
TZ=UTC LC_ALL=C.UTF-8 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -t . -v
TZ=UTC LC_ALL=C.UTF-8 PYTHONPATH=src .venv/bin/python scripts/generate_m0_evidence.py
```

The official NautilusTrader wheel is not stored in this repository. Its exact
filename, SHA-256, source commit, SLSA attestation, and resolved dependencies are
recorded in `runtime.lock.json`, `requirements.lock.txt`, and `evidence/m0/`.

## M1 causal harness

M1 exposes `run_lab(config) -> RunResult` for one isolated Instrument, Market
Profile, StrategySpec, Dataset Release, and initial-capital allocation. The
runner uses the public NautilusTrader v2 `BacktestEngine`, `Strategy`, native
orders/Fills/positions/accounts, `MakerTakerFeeModel`, mark valuation, and
funding settlement. Project code is limited to strict boundary validation,
pre-submit V1 safety guards, immutable evidence projections, and a read-only
post-run checker.

M1 qualification data are synthetic external one-minute LAST bars and, for the
Perpetual profile, native `MarkPriceUpdate` and `FundingRateUpdate` objects. M1
does not acquire market data or execute an Official Run.

Run the completed-phase regression and M1 acceptance suite, generate additive
engine evidence, then validate the evidence without mutation:

```bash
TZ=UTC LC_ALL=C.UTF-8 PYTHONPATH=src .venv/bin/python scripts/run_m1_acceptance.py
TZ=UTC LC_ALL=C.UTF-8 PYTHONPATH=src .venv/bin/python scripts/generate_m1_evidence.py
TZ=UTC LC_ALL=C.UTF-8 PYTHONPATH=src .venv/bin/python scripts/validate_m1_evidence.py
```

## M2 frozen Binance data

M2 freezes exact official Binance Public Data archive bytes in the local
content-addressed `data/raw/sha256/` store before parsing. Publisher checksums,
strict source roles, explicit timestamp rules, exact one-minute grids,
point-in-time metadata limitations, funding schedule evidence, and native
Nautilus catalog semantic identities are bound into immutable
`DatasetRelease` manifests. Raw archives and derived Parquet payloads are not
committed; the small manifests, fixture extracts, identities, and acceptance
evidence are committed.

The bounded qualification uses BTCUSDT only and is not an Official Run or a
research partition. Acquisition is an explicit network-enabled setup step;
all parsing, catalog rebuild, and tests run offline afterward:

```bash
M2_SOURCE_DIR=/path/to/approved-binance-downloads \
  TZ=UTC LC_ALL=C.UTF-8 PYTHONPATH=src .venv/bin/python \
  scripts/generate_m2_evidence.py
TZ=UTC LC_ALL=C.UTF-8 PYTHONPATH=src .venv/bin/python \
  scripts/run_m2_acceptance.py
```

## M3 profile qualification

M3 consumes the strict repaired M2 `DatasetRelease` objects directly and runs
only deterministic `QUALIFICATION` profiles.  The mechanical signal schedules
are frozen in `StrategySpec`; the public Nautilus engine remains the sole owner
of orders, Fills, positions, accounts, fees, funding, PnL, and portfolio state.
The bounded intervals are permanently disclosed as exposed qualification data,
not future Holdout data, and no profitability conclusion is produced.

After the M3 implementation commit is clean and pushed, run the two profiles,
fresh-process replays, and negative controls offline:

```bash
TZ=UTC LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  .venv/bin/python scripts/run_m3_qualifications.py
```

The run starts only when `HEAD == origin/main` and the worktree is clean.  Its
additive output is written under `evidence/m3/m3-acceptance-001/`; M3 never
acquires data, starts research, performs an Official Run, or implements M4.

## M4 research governance and reporting

M4 reads immutable completed evidence and adds strict content-addressed
`ResearchProtocol`, append-only trial history, chronological partition checks,
Holdout consumption, deterministic search-budget enforcement, sample adequacy,
path-risk Monte Carlo diagnostics, claim eligibility, and JSON/Markdown
reporting. It never submits orders, modifies Fills, posts cash, or reconstructs
Nautilus financial truth. Synthetic acceptance workspaces are isolated from the
future Owner journal under `research/`.

From a clean committed checkout, Final V1 Acceptance replays the accepted Spot
and Perpetual qualification paths offline and writes only additive evidence:

```bash
TZ=UTC LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  .venv/bin/python scripts/run_final_v1_acceptance.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  .venv/bin/python scripts/validate_m4_evidence.py
```

The synthetic eligibility fixture proves the claim-gate contract only. It is
permanently labeled as non-research and never constitutes a real profitability
claim or an Owner study.

## Public Owner workflow

The Repair Epoch adds one public, strict interface for a future Owner-selected
study.  Its JSON input is `crypto_lab.OwnerWorkflowInput`; unknown, missing, or
duplicate fields are rejected.  The input freezes the complete
`ResearchProtocol`, trial/candidate/run identities, registered StrategySpec,
Dataset Release and Qualified Profile identities, partition and warmup/scoring
boundaries, Initial Capital, fee assumption, and seed.  It contains no caller
booleans for integrity, Holdout freshness, diagnostics, eligibility, metrics,
or completed trades.

Run it only from a clean checked-out branch whose `HEAD` equals its
`origin/<branch>` tracking ref:

```bash
TZ=UTC LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  .venv/bin/python scripts/run_owner_workflow.py \
  --input /absolute/path/to/strict-owner-workflow.json \
  --repository "$PWD" \
  --output /tmp/owner-workflow-result.json
```

The command performs the complete lifecycle without private imports or a glue
script.  It refreezes and persists the protocol, validates candidate order and
budget, first commits and normal-pushes an immutable workflow intent, then
records `PLANNED` and `STARTED` and publishes the history anchor, runs the
registered Nautilus Strategy in a dedicated
seccomp-isolated child, always records a terminal state, commits and pushes the
Run evidence, reruns the read-only checker, derives diagnostics and Monte Carlo
status from Run evidence, resolves the claim from authoritative identities,
and publishes the full JSON/Markdown report.  A Final Holdout is checked by the
authoritative exposure resolver before designation and consumed/anchored on
first result exposure.  Each history checkpoint is fsynced, committed, and
normal-pushed before the next phase, which preserves a clean-worktree Official
preflight and makes prior anchors independently reconcilable.  A later
invocation detects either a committed intent interrupted before `STARTED` or
an interrupted `STARTED` trial, records the full attempt as `ABORTED` (or
reconciles complete persisted terminal evidence), publishes that recovery, and
stops so a retry must use new trial and run IDs.  An uncommitted journal/anchor
extension without the earlier committed workflow authorization is rejected.

For interface qualification only, this command generates a complete strict
input over the already exposed M3 Spot interval.  It is not a Strategy Research
selection, Owner Study, Final Holdout use, or profitability claim:

```bash
TZ=UTC LC_ALL=C.UTF-8 PYTHONPATH=src .venv/bin/python \
  scripts/generate_owner_workflow_fixture_input.py \
  --repository "$PWD" \
  --frozen-at-utc 2026-08-22T12:00:00Z \
  --trial-id qualification-interface-fixture-001 \
  --run-id qualification-interface-run-001 \
  --output /tmp/qualification-interface-input.json
```

The currently registered implementation is deliberately qualification-only
and permanently claim-ineligible.  A future Owner-selected economic Strategy
must first have a reviewed, static public registry entry; configuration files
cannot provide callables, dynamic imports, source code, or precomputed order
schedules, and no Product Code change is needed between trials of an already
registered implementation.
