# Data storage and deterministic rebuild

This document describes distribution and local storage for V1. It does not
change the normative data contract in `SSOT.md`.

## What Git contains

The repository contains:

- Product Code under `src/crypto_lab/`;
- tests and executable scripts;
- configuration, `SSOT.md`, `runtime.lock.json`, `requirements.lock.txt`,
  `data-tool.lock.json`, and `requirements.data.lock.txt`;
- small DatasetRelease, instrument, funding, and market-state manifests under
  `data/releases/`;
- official-source checksums and content identities;
- additive evidence, failed-attempt history, and Owner reports.

Git does not contain:

- raw Binance archive/REST payloads;
- DuckDB database or staging payloads;
- Parquet catalog payloads;
- `.venv`, `.data-venv`, or `.data-wheelhouse`;
- `.env` files, credentials, private keys, or secrets;
- temporary files or unbounded run caches.

Historical public-reference evidence may contain clearly labeled, publicly
published documentation examples (for example illustrative cryptographic keys
or expired sample signed URLs). Those are not project credentials and are
preserved byte-for-byte as source evidence.

## Server-local data root

On the qualified server, the repository-local data root is `data/`:

| Path | Role | Git policy |
|---|---|---|
| `data/raw/` | immutable content-addressed official source bytes and HTTP observations | payloads ignored |
| `data/duckdb/` | versioned derived validation databases, staging, and build results | payloads ignored |
| `data/catalog/` | content-addressed Nautilus Parquet catalogs | payloads ignored |
| `data/releases/` | small immutable release/instrument/funding/acceptance manifests | tracked |

The currently accepted primary DuckDB is
`data/duckdb/instrument-representation-funding-checker-001/primary-v6.duckdb`.
Its physical SHA-256 is
`bf8413f38cf9c7a4a8238e17680404e36c94dd3b757cbb3581e297b49240e5fb`;
its semantic identity is
`11329c1497ff6bf3a68c5d3ba994f5ac2bbd0ece51cf489f9fa3f681a01ecbff`.
The physical hash is recorded but the semantic identity is the deterministic
content contract.

Accepted catalogs are materialized at:

- `data/catalog/db0971d28caba547378e3acba5ad8df1cbd0d6d5be963d153248928a729e374f`
  for Spot;
- `data/catalog/7c96897a8e1ea3c02198238a277fb8c3d995f54dd90dc381e534a5f21b017ae0`
  for Perpetual.

Do not move payloads into a tracked path. Back them up as server data while
preserving relative paths, permissions, sizes, and hashes.

## Authority chain

1. Exact official Binance bytes in the content-addressed raw store are source
   authority.
2. Publisher `.CHECKSUM` files attest archive transport integrity; REST bodies
   are bound by request identity, size, and SHA-256.
3. DuckDB stores derived source observations, conflicts, minute dispositions,
   canonical market rows, validation results, and release bindings.
4. DatasetRelease manifests bind accepted rows and source identities.
5. Nautilus catalogs are deterministic exports of accepted execution/mark data;
   verified no-trade intervals and conflicts are not exported as bars.
6. Nautilus remains the only financial engine.

DuckDB never supersedes raw bytes, and catalog readback alone is not sufficient
for data acceptance.

## A fresh clone

A fresh clone contains no market payloads. To build locally:

1. Create and verify `.venv` and `.data-venv` exactly as shown in the
   [README](../README.md#create-the-project-runtime).
2. Run the two existing acquisition drivers in an explicitly network-enabled
   phase. They use official Binance endpoints and preserve bodies before
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

3. Ensure the exact official historical order-grid objects named by
   `scripts/build_free_official_binance_release.py` exist at their declared
   content-addressed paths and match these SHA-256 values:

   - Spot:
     `89e0fb77408dde49f342bfb7929f7adb08af6c73e26eaf203143641ede99ea9a`;
   - Perpetual:
     `ca85fd9286601514fdb00610aef15f7ee43b5e0657c865ad18e97c0763e8b5d1`.

   The official locators, sizes, roles, and provenance are in the builder and
   `evidence/repair/instrument-representation-funding-checker-001/representation-vs-order-grid.json`.
   The builder rejects missing or byte-different objects; never substitute a
   third-party object.

4. Run two fresh builds and
   `scripts/validate_free_official_binance_rebuild.py` using the exact commands
   in the [README](../README.md#obtain-and-rebuild-official-binance-data).
5. Keep network disabled during parsing, DuckDB validation, catalog creation,
   Nautilus acceptance, and every Official Run.

Because REST capture metadata and some official objects can change over time,
a current re-download is not automatically the accepted V1 acquisition. If any
attested raw byte, request observation, checksum, or identity differs, the
pipeline must fail closed. The result is a new candidate requiring the normal
data-quality and Owner adoption workflow; no one may edit a manifest to force
the V1 identity.

## Verify accepted identities

The release objects are strict canonical JSON whose internal IDs are verified
when parsed. This read-only command confirms the two tracked manifests and
their catalog bindings:

```bash
TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONPATH=src \
  .venv/bin/python - <<'PY'
from pathlib import Path
from crypto_lab.data import DatasetRelease

expected = {
    "fd8542c109cfbf7d6b19d5b7bbb7705c6a161efc807695f3671978c381e34eca":
        "db0971d28caba547378e3acba5ad8df1cbd0d6d5be963d153248928a729e374f",
    "b6c8f5d659f3441c924b613d770342796c90b90a970f42a3dc8227c856198917":
        "7c96897a8e1ea3c02198238a277fb8c3d995f54dd90dc381e534a5f21b017ae0",
}
for release_id, catalog_id in expected.items():
    path = Path("data/releases") / f"{release_id}.json"
    release = DatasetRelease.from_json_bytes(path.read_bytes())
    assert release.dataset_release_id == release_id
    assert release.catalog_identity == catalog_id
    print(release.market_profile.value, release_id, catalog_id)
PY
```

After payload rebuild, use the deterministic validator rather than a file hash
alone. It compares schema, ordered table hashes, conflict/minute dispositions,
DatasetRelease IDs, catalog semantic inventories, and read-only resolution. The
expected semantic identities are in
[`release/v1.0.0-manifest.json`](../release/v1.0.0-manifest.json).

## How the laboratory uses local data

`DatasetRelease.resolve_runtime_data(data_root)` resolves the tracked release
manifest against `data/catalog/<catalog-identity>`. Official preflight verifies
the source/data/catalog identities before loading any market state. The Run
then occurs inside the locked offline process boundary. A missing local payload
blocks; it is never downloaded during a Run and never replaced by another
source.

## Future interface integration

A future user interface must call the same laboratory boundary and point to the
same server-side `data/` root. It must not copy raw archives, DuckDB databases,
or catalogs into a UI repository or browser bundle. The UI may display
manifests, identities, reports, and status returned by the lab; it may not
become an alternative data or financial engine.
