# Nautilus Crypto Backtest Lab

A strict, reproducible cryptocurrency backtesting laboratory built on NautilusTrader.

## Engineering Authority

`SSOT.md` is the sole engineering authority for this repository.

## Locked Runtime

NautilusTrader v1.231.0.

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
  --dest /tmp/nautilus-m0-wheel nautilus_trader==1.231.0
sha256sum /tmp/nautilus-m0-wheel/nautilus_trader-1.231.0-cp312-cp312-manylinux_2_35_x86_64.whl
# Required digest: 8c438e95c275a13df0c0ddb7012c462708b5e99ff3612e36a1b7bd49ab39c216
python3.12 -m venv .venv
.venv/bin/python -m pip install --no-deps \
  /tmp/nautilus-m0-wheel/nautilus_trader-1.231.0-cp312-cp312-manylinux_2_35_x86_64.whl
.venv/bin/python -m pip install --require-hashes -r requirements.lock.txt
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
