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
