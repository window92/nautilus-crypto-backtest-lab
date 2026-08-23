# DATA_PROVENANCE_DUCKDB_REPAIR_001

This additive evidence bundle records the adopted SSOT amendment, official-only Binance acquisition, exact-Decimal Spot reconciliation, trade-ID continuity proofs, minute coverage, USDⓈ-M execution/mark/funding validation, DuckDB 1.4.5 materialization, independent rebuild, and sparse Nautilus qualification.

The final gate is `DATA_REPAIR_BLOCKED_UNRESOLVED_OFFICIAL_DATA`. No successful DatasetRelease or ParquetDataCatalog was created, and no strategy or Official Trial was started. The exact blocking timestamps are in `dataset-release-manifest.json` and the Arabic Owner report.

Large raw objects, canonical diagnostic exports, and DuckDB payloads remain available locally under ignored `data/` paths. Their tracked manifests contain exact paths, sizes, SHA-256 identities, and deterministic semantic identities.

Offline rebuild from the preserved raw objects requires previously nonexistent database and export targets. For example:

```bash
.data-venv/bin/python scripts/build_data_provenance_database.py --database data/duckdb/rebuild-audit.duckdb --staging data/duckdb/staging-rebuild-audit --export-dir data/duckdb/exports-rebuild-audit --role INDEPENDENT_REBUILD
```

The builders configure DuckDB with extension autoload/autoinstall disabled and perform no network access. Acquisition is a separate network-enabled phase implemented by `scripts/run_data_provenance_repair.py`; it refuses unofficial URLs and saves each response before parsing.
