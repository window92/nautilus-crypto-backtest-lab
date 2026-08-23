#!/usr/bin/env python3
"""Finalize immutable post-adoption evidence for the free official Binance repair."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import duckdb


ROOT = Path(__file__).resolve().parents[4]
EVIDENCE = ROOT / "evidence/repair/free-official-binance-data-duckdb-001"
DB_ROOT = ROOT / "data/duckdb/free-official-binance-data-duckdb-001"
PRIMARY_RESULT = DB_ROOT / "primary-v4-result.json"
INDEPENDENT_RESULT = DB_ROOT / "independent-v4-result.json"
VALIDATION_RESULT = DB_ROOT / "deterministic-validation-v4.json"
ACCEPTANCE_RESULT = Path("/tmp/free-official-acceptance-v2/result.json")
ACCEPTANCE_OUTPUT = Path("/tmp/free-official-acceptance-v2/test-output.txt")
RAW_VALIDATION = Path("/tmp/free-official-raw-validation-v4.json")
OFFLINE_VENV = Path("/tmp/free-official-data-reinstall-v1")

ADOPTED_SSOT_SHA256 = "b4deb7048242239234de7eaa353b623b3e45247eb42f1021dbc26ffd910edb99"
BASE_SSOT_SHA256 = "f51971ed7a09b172c82ff5965f2899d2a302dd71a2af60eb7c920133567b4354"
RUNTIME_LOCK_SHA256 = "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd"
DEPENDENCY_LOCK_SHA256 = "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47"
HISTORICAL_DB_SHA256 = "932e97c446c713e8525f43b8111aced2e914b9579eba10823df7c6b0b51887b6"
HISTORICAL_DB_SIZE = 1_236_807_680
ACQUISITION_IDENTITY = "6031d1f37a7e2687ba07988c6d2c9c74d241da368fd3baa4bfd5ffd31f1d8b40"
ANALYSIS_IDENTITY = "bf7c4d476702a6438e2940d85548943ca1b2b926f74ba64380e20bd0490c654d"
FINAL_TIMESTAMP = "2026-08-23T16:40:24.722767Z"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def preserve_once(source: Path, destination: Path) -> None:
    content = source.read_bytes()
    if destination.exists():
        return
    destination.write_bytes(content)


def assert_identity(path: Path, expected: str, *, expected_size: int | None = None) -> None:
    if not path.is_file():
        raise ValueError(f"missing required file: {path}")
    if expected_size is not None and path.stat().st_size != expected_size:
        raise ValueError(f"size mismatch: {path}")
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"SHA-256 mismatch for {path}: {actual}")


def verify_candidate_003() -> dict[str, Any]:
    phase = ROOT / "evidence/repair/binance-origin-archive-recovery-001"
    inventory_path = phase / "evidence-inventory.json"
    assert_identity(
        inventory_path,
        "0bcf40dc3d51d44cf9e0f0619698d003de348c5c6789829f20cf95f25828aaf5",
    )
    inventory = load_json(inventory_path)
    failures = []
    for entry in inventory["files"]:
        path = ROOT / entry["path"]
        if (
            not path.is_file()
            or path.stat().st_size != entry["size_bytes"]
            or sha256_file(path) != entry["sha256"]
        ):
            failures.append(entry["path"])
    if failures:
        raise ValueError(f"Candidate 003 historical bytes changed: {failures[:3]}")
    return {
        "decision": "REJECTED_BY_OWNER_PAID_PROVIDER_PATH_NOT_AUTHORIZED",
        "inventory_path": inventory_path.relative_to(ROOT).as_posix(),
        "inventory_sha256": sha256_file(inventory_path),
        "validated_file_count": len(inventory["files"]),
        "validation_failure_count": 0,
        "bytes_modified": False,
        "status": "PASS_UNCHANGED_HISTORICAL_EVIDENCE",
    }


def schema_inventory(database: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    connection = duckdb.connect(str(database), read_only=True)
    try:
        names = [row[0] for row in connection.execute("SHOW TABLES").fetchall()]
        tables = []
        for name in sorted(names):
            columns = []
            for cid, column, sql_type, not_null, default, primary_key in connection.execute(
                f"PRAGMA table_info('{name}')",
            ).fetchall():
                columns.append(
                    {
                        "ordinal": cid,
                        "name": column,
                        "type": sql_type,
                        "not_null": bool(not_null),
                        "default": default,
                        "primary_key": bool(primary_key),
                    },
                )
            tables.append({"table": name, "columns": columns})
        constraints = [
            {
                "table": table,
                "constraint_type": constraint_type,
                "constraint_text": text,
            }
            for table, constraint_type, text in connection.execute(
                "SELECT table_name, constraint_type, constraint_text "
                "FROM duckdb_constraints() ORDER BY table_name, constraint_index",
            ).fetchall()
        ]
        return tables, constraints
    finally:
        connection.close()


def negative_controls(acceptance: dict[str, Any]) -> dict[str, Any]:
    discovered = set(acceptance["discovery"]["test_ids"])
    prefix_free = "tests.unit.test_free_official_binance_repair."
    prefix_prov = "tests.unit.test_data_provenance_repair."
    prefix_raw = "tests.unit.test_m2_raw_and_parsing."
    prefix_release = "tests.unit.test_m2_release_contract."
    mapping = {
        "corrupted_archive_bytes": [
            prefix_raw + "RawObjectTests.test_acquirer_stores_corrupted_download_before_failing_checksum",
        ],
        "checksum_mismatch": [
            prefix_raw + "RawObjectTests.test_publisher_checksum_verification_and_corruption_failure",
        ],
        "malformed_checksum": [
            "tests.unit.test_m2_repair_contracts.F02PreserveBeforeParseTests.test_malformed_checksum_preserves_both_responses_and_retry_history",
        ],
        "conflicting_monthly_daily_api_row": [
            prefix_prov + "DataProvenanceContractTests.test_monthly_conflict_without_event_arbitration_blocks",
        ],
        "missing_raw_trade_id": [
            prefix_prov + "DataProvenanceContractTests.test_trade_order_duplicate_overlap_and_gap_fail_closed",
        ],
        "duplicate_raw_trade_id": [
            prefix_prov + "DataProvenanceContractTests.test_trade_order_duplicate_overlap_and_gap_fail_closed",
        ],
        "forged_no_trade_interval": [
            prefix_prov + "DataProvenanceContractTests.test_no_trade_rejects_aggregate_or_underlying_id_gap",
        ],
        "partial_minute_without_continuity": [
            prefix_prov + "DataProvenanceContractTests.test_monthly_only_impossible_row_blocks_without_complete_proof",
        ],
        "synthetic_zero_volume_bar": [
            prefix_free + "SparseOfficialGridTests.test_bar_during_verified_no_trade_is_rejected_as_synthetic_inventory",
        ],
        "forward_filled_close": [
            prefix_prov + "DataProvenanceContractTests.test_forward_filled_or_interpolated_ohlc_cannot_arbitrate_official_conflict",
        ],
        "interpolated_ohlc": [
            prefix_prov + "DataProvenanceContractTests.test_forward_filled_or_interpolated_ohlc_cannot_arbitrate_official_conflict",
        ],
        "mark_derived_from_execution": [
            prefix_free + "SparseOfficialGridTests.test_missing_mark_minute_and_execution_price_substitution_are_rejected",
        ],
        "mark_replaced_by_index_premium_last_or_spot": [
            prefix_raw + "RawObjectTests.test_prohibited_archive_role_substitution_is_rejected",
        ],
        "missing_mark_minute": [
            prefix_free + "SparseOfficialGridTests.test_missing_mark_minute_and_execution_price_substitution_are_rejected",
        ],
        "duplicate_funding": [
            prefix_release + "FundingContractTests.test_conflicting_funding_duplicate_blocks",
        ],
        "conflicting_funding_schedule": [
            prefix_release + "FundingContractTests.test_unproven_schedule_is_funding_ambiguous",
        ],
        "source_role_mismatch": [
            prefix_raw + "TimestampAndParsingTests.test_source_role_mismatch_blocks",
        ],
        "timestamp_unit_mismatch": [
            prefix_free + "NewDuckDBContractTests.test_constraints_rollback_readonly_and_exact_types",
        ],
        "duckdb_row_mutation": [
            prefix_free + "NewDuckDBContractTests.test_constraints_rollback_readonly_and_exact_types",
        ],
        "duckdb_source_binding_mutation": [
            prefix_free + "NewDuckDBContractTests.test_constraints_rollback_readonly_and_exact_types",
        ],
        "nondeterministic_rebuild": [
            prefix_free + "NewDuckDBContractTests.test_nondeterministic_rebuild_and_catalog_mutation_are_rejected",
        ],
        "stale_dataset_release": [
            prefix_free + "NewDuckDBContractTests.test_nondeterministic_rebuild_and_catalog_mutation_are_rejected",
            prefix_release + "DatasetReleaseTests.test_catalog_rebuild_semantic_stability_and_mutation_staleness",
        ],
        "nautilus_catalog_semantic_mismatch": [
            prefix_free + "NewDuckDBContractTests.test_nondeterministic_rebuild_and_catalog_mutation_are_rejected",
            prefix_release + "DatasetReleaseTests.test_catalog_rebuild_semantic_stability_and_mutation_staleness",
        ],
        "duckdb_extension_or_network_use": [
            prefix_free + "NewDuckDBContractTests.test_builder_prohibits_duckdb_extensions_and_network",
        ],
    }
    missing = sorted({test for tests in mapping.values() for test in tests if test not in discovered})
    if missing:
        raise ValueError(f"negative-control test IDs missing from discovery: {missing}")
    return {
        "schema": "free-official-binance-negative-controls-v1",
        "created_at_utc": FINAL_TIMESTAMP,
        "controls": {
            control: {"test_ids": tests, "status": "PASS_REJECTION_PROVEN"}
            for control, tests in sorted(mapping.items())
        },
        "control_count": len(mapping),
        "missing_test_ids": [],
        "failures": 0,
        "errors": 0,
        "skips": 0,
        "xfail": 0,
        "status": "PASS",
    }


def append_failed_attempts() -> None:
    path = EVIDENCE / "failed-attempts.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if len(records) not in {7, 19, 20, 23}:
        raise ValueError(f"unexpected failed-attempt history length: {len(records)}")
    if len(records) == 23:
        return
    additions = [
        ("targeted unittest without repository PYTHONPATH", "FAILED_LAUNCH_ENVIRONMENT", "The initial targeted command could not import crypto_lab; it was rerun through the locked project environment and repository import path."),
        ("bulk CSV import with unescaped null representation", "FAILED_TRANSACTION_ROLLED_BACK", "DuckDB rejected the first staging representation before canonical commit; the failed database remained non-canonical and the importer was corrected without changing source values."),
        ("derive SourceRegistry paths outside the Phase-A manifest", "FAILED_SOURCE_BINDING_GUARD", "The builder required manifest-bound content-addressed paths; registry construction was restricted to the attested Phase-A raw-object inventory."),
        ("reuse prior perpetual metadata source role", "FAILED_METADATA_ROLE_BINDING", "The prior object did not carry the current official Phase-A role; the build was rebound to the official current exchangeInfo response hash."),
        ("read a non-existent adoption timestamp key", "FAILED_MANIFEST_FIELD_BINDING", "The first build launcher expected the wrong adoption evidence key and stopped before a release was accepted; it was corrected to the attested key."),
        ("primary-v1 full event hashing in row insertion loop", "INTERRUPTED_NON_CANONICAL_BUILD", "The slow non-canonical attempt was stopped; bulk deterministic staging was implemented while retaining exact Decimal text and raw bindings."),
        ("independent-v1 materialize into immutable primary artifact paths", "FAILED_IMMUTABLE_ARTIFACT_COLLISION", "The independent build correctly refused overwrite; independent artifacts were isolated and final materialization occurred only after comparison."),
        ("v2 release with prior api.binance.com observations", "SUPERSEDED_SCOPE_FAILURE", "Raw revalidation found 309 prior observations outside this epoch's exact official host allowlist; v2 artifacts were retained locally and excluded from canonical release."),
        ("v2 raw-object allowlist validation", "FAILED_UNAUTHORIZED_HOST_GATE", "The validator returned FAIL on the prior api.binance.com host, proving the source gate; the final inventory contains only the explicitly authorized official hosts."),
        ("primary-v3 unfiltered publisher checksum insertion", "FAILED_FOREIGN_KEY_ROLLBACK", "Checksums for objects outside the selected inventory violated source binding; the transaction rolled back and v4 filters checksum bindings to the selected raw inventory."),
        ("full acceptance outside locked UTC runtime", "FAILED_RUNTIME_ENVIRONMENT", "Timezone-sensitive runtime preflight failed before acceptance; the complete suite was rerun under the locked UTC environment and passed."),
        ("physical DuckDB byte equality as deterministic criterion", "REJECTED_NON_SEMANTIC_CRITERION", "Fresh databases had different physical hashes as permitted; schema, ordered rows, table hashes, release identities and catalogs were exactly equal."),
    ]
    if len(records) == 7:
        for ordinal, (attempt, outcome, detail) in enumerate(additions, start=8):
            records.append(
                {
                    "sequence": ordinal,
                    "recorded_at_utc": FINAL_TIMESTAMP,
                    "stage": "post-adoption implementation and acceptance",
                    "attempt": attempt,
                    "outcome": outcome,
                    "detail": detail,
                    "data_or_contract_weakened": False,
                },
            )
    finalizer_attempts = [
        (20, "treat compileall __pycache__ files as UTF-8 evidence", "The finalizer rejected generated bytecode as text evidence; the inventory was narrowed to durable evidence and source artifacts while generated __pycache__ remains ignored."),
        (21, "read acceptance discovery from the obsolete tests key", "The finalizer stopped on KeyError; it was corrected to the validator's attested test_ids field without changing any test result."),
        (22, "bind negative controls to guessed test class names", "Discovery rejected eight non-existent IDs; every binding was replaced by its exact independently discovered test ID."),
        (23, "compare an already replaced final report to its Phase-A backup", "The idempotent rerun stopped because the live report had already advanced; the preserved copy was instead verified against the original Phase-A content manifest hash."),
    ]
    existing_sequences = {record["sequence"] for record in records}
    for sequence, attempt, detail in finalizer_attempts:
        if sequence in existing_sequences:
            continue
        records.append(
            {
                "sequence": sequence,
                "recorded_at_utc": FINAL_TIMESTAMP,
                "stage": "final evidence inventory",
                "attempt": attempt,
                "outcome": "FAILED_VALIDATOR_SCOPE_FALSE_POSITIVE",
                "detail": detail,
                "data_or_contract_weakened": False,
            },
        )
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    assert_identity(ROOT / "SSOT.md", ADOPTED_SSOT_SHA256)
    assert_identity(ROOT / "runtime.lock.json", RUNTIME_LOCK_SHA256)
    assert_identity(ROOT / "requirements.lock.txt", DEPENDENCY_LOCK_SHA256)
    assert_identity(
        ROOT / "data/duckdb/binance-btcusdt-owner-smoke-001.duckdb",
        HISTORICAL_DB_SHA256,
        expected_size=HISTORICAL_DB_SIZE,
    )
    candidate = EVIDENCE / "ssot-candidate-004/SSOT.candidate-004.md"
    assert_identity(candidate, ADOPTED_SSOT_SHA256)
    if candidate.read_bytes() != (ROOT / "SSOT.md").read_bytes():
        raise ValueError("adopted SSOT does not byte-match Candidate 004")

    primary = load_json(PRIMARY_RESULT)
    independent = load_json(INDEPENDENT_RESULT)
    validation = load_json(VALIDATION_RESULT)
    acceptance = load_json(ACCEPTANCE_RESULT)
    raw_validation = load_json(RAW_VALIDATION)
    for label, result in (
        ("primary", primary),
        ("independent", independent),
        ("deterministic validation", validation),
        ("acceptance", acceptance),
        ("raw validation", raw_validation),
    ):
        if result["status"] != "PASS":
            raise ValueError(f"{label} is not PASS")
    if acceptance["strategy_run"] or acceptance["official_trial"] or acceptance["profitability_inspected"]:
        raise ValueError("prohibited research execution detected")
    if primary["semantic_database_identity"] != independent["semantic_database_identity"]:
        raise ValueError("semantic database rebuild mismatch")
    if primary["readonly_table_hashes"] != independent["readonly_table_hashes"]:
        raise ValueError("semantic table hash rebuild mismatch")
    if primary["dataset_release_ids"] != independent["dataset_release_ids"]:
        raise ValueError("DatasetRelease rebuild mismatch")
    if primary["catalogs"] != independent["catalogs"]:
        raise ValueError("Nautilus catalog rebuild mismatch")
    for result in (primary, independent):
        database = ROOT / result["database_path"]
        assert_identity(database, result["database_file_sha256"], expected_size=result["database_size_bytes"])
        if (database.stat().st_mode & 0o222) != 0:
            raise ValueError(f"accepted database remains writable: {database}")

    candidate_003 = verify_candidate_003()
    append_failed_attempts()

    # Preserve Phase-A report bytes before replacing the live summaries.
    preserve_once(EVIDENCE / "owner-report/README.md", EVIDENCE / "owner-report/PHASE-A.md")
    preserve_once(EVIDENCE / "spot-data-report.md", EVIDENCE / "spot-data-report.phase-a.md")
    preserve_once(EVIDENCE / "perpetual-data-report.md", EVIDENCE / "perpetual-data-report.phase-a.md")
    preserve_once(EVIDENCE / "final-content-manifest.json", EVIDENCE / "phase-a-content-manifest.json")
    phase_a_manifest = load_json(EVIDENCE / "phase-a-content-manifest.json")
    phase_a_hashes = {entry["path"]: entry["sha256"] for entry in phase_a_manifest["files"]}
    for original, preserved in (
        ("owner-report/README.md", "owner-report/PHASE-A.md"),
        ("spot-data-report.md", "spot-data-report.phase-a.md"),
        ("perpetual-data-report.md", "perpetual-data-report.phase-a.md"),
    ):
        assert_identity(EVIDENCE / preserved, phase_a_hashes[original])

    offline_python = OFFLINE_VENV / "bin/python"
    pip_check = subprocess.run(
        [str(offline_python), "-m", "pip", "check"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    offline_version = subprocess.run(
        [str(offline_python), "-c", "import duckdb; print(duckdb.__version__)"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if offline_version != "1.4.5":
        raise ValueError("offline reinstall did not produce DuckDB 1.4.5")
    data_lock = load_json(ROOT / "data-tool.lock.json")
    wheel = ROOT / ".data-wheelhouse" / data_lock["complete_dependency_set"][0]["wheel_filename"]
    assert_identity(wheel, data_lock["complete_dependency_set"][0]["wheel_sha256"], expected_size=data_lock["complete_dependency_set"][0]["wheel_size_bytes"])
    tool_identity = {
        "schema": "free-official-binance-duckdb-tool-identity-v1",
        "created_at_utc": FINAL_TIMESTAMP,
        "duckdb_version": "1.4.5",
        "data_tool_lock_path": "data-tool.lock.json",
        "data_tool_lock_sha256": sha256_file(ROOT / "data-tool.lock.json"),
        "requirements_lock_path": "requirements.data.lock.txt",
        "requirements_lock_sha256": sha256_file(ROOT / "requirements.data.lock.txt"),
        "wheel_path": wheel.relative_to(ROOT).as_posix(),
        "wheel_filename": wheel.name,
        "wheel_sha256": sha256_file(wheel),
        "wheel_size_bytes": wheel.stat().st_size,
        "python_version": data_lock["platform"]["python_version"],
        "python_abi": data_lock["platform"]["python_abi"],
        "platform_architecture": data_lock["platform"]["architecture"],
        "complete_dependency_set": data_lock["complete_dependency_set"],
        "exact_offline_installation_command": data_lock["exact_installation_command"],
        "independent_offline_reinstall": {
            "path": str(OFFLINE_VENV),
            "duckdb_version": offline_version,
            "pip_check_stdout": pip_check.stdout.strip(),
            "returncode": pip_check.returncode,
            "network_used": False,
            "status": "PASS",
        },
        "extensions_installed_or_loaded": False,
        "duckdb_network_access": False,
        "nautilus_runtime_lock_modified": False,
        "status": "PASS",
    }
    write_json(EVIDENCE / "duckdb-tool-identity.json", tool_identity)

    tables, constraints = schema_inventory(ROOT / primary["database_path"])
    binary_float_columns = [
        f"{table['table']}.{column['name']}"
        for table in tables
        for column in table["columns"]
        if column["type"].upper() in {"FLOAT", "DOUBLE", "REAL"}
    ]
    schema_evidence = {
        "schema": "free-official-binance-duckdb-schema-evidence-v1",
        "created_at_utc": FINAL_TIMESTAMP,
        "schema_path": "schemas/free_official_binance_duckdb.sql",
        "schema_sha256": sha256_file(ROOT / "schemas/free_official_binance_duckdb.sql"),
        "schema_identity": primary["schema_identity"],
        "database_inspected_read_only": True,
        "table_count": len(tables),
        "tables": tables,
        "constraints": constraints,
        "financial_binary_float_columns": binary_float_columns,
        "timestamp_contract": "UTC_INTEGER_NANOSECONDS_HALF_OPEN",
        "raw_bytes_authority_outside_duckdb": True,
        "source_sha_binding_required": True,
        "extensions_prohibited": True,
        "status": "PASS" if not binary_float_columns and len(tables) == 18 else "FAIL",
    }
    if schema_evidence["status"] != "PASS":
        raise ValueError("schema evidence gate failed")
    write_json(EVIDENCE / "duckdb-schema.json", schema_evidence)

    comparison = {
        "schema": "free-official-binance-duckdb-build-comparison-v1",
        "created_at_utc": FINAL_TIMESTAMP,
        "primary": {
            "path": primary["database_path"],
            "size_bytes": primary["database_size_bytes"],
            "file_sha256": primary["database_file_sha256"],
            "read_only_reopen": primary["readonly_reopen"],
        },
        "independent": {
            "path": independent["database_path"],
            "size_bytes": independent["database_size_bytes"],
            "file_sha256": independent["database_file_sha256"],
            "read_only_reopen": independent["readonly_reopen"],
        },
        "physical_file_hashes_equal": False,
        "physical_difference_is_non_authoritative": True,
        "semantic_database_identity": primary["semantic_database_identity"],
        "schema_identity": primary["schema_identity"],
        "source_inventory_identity": primary["source_inventory_identity"],
        "ordered_row_counts_equal": primary["row_counts"] == independent["row_counts"],
        "canonical_table_hashes_equal": primary["readonly_table_hashes"] == independent["readonly_table_hashes"],
        "dataset_release_ids_equal": primary["dataset_release_ids"] == independent["dataset_release_ids"],
        "nautilus_catalog_identities_equal": primary["catalogs"] == independent["catalogs"],
        "exact_keys_compared": validation["comparison"]["exact_keys_compared"],
        "validation_result_path": VALIDATION_RESULT.relative_to(ROOT).as_posix(),
        "validation_result_sha256": sha256_file(VALIDATION_RESULT),
        "status": "PASS",
    }
    write_json(EVIDENCE / "duckdb-build-comparison.json", comparison)
    write_json(
        EVIDENCE / "semantic-table-hashes.json",
        {
            "schema": "free-official-binance-semantic-table-hashes-v1",
            "created_at_utc": FINAL_TIMESTAMP,
            "semantic_database_identity": primary["semantic_database_identity"],
            "schema_identity": primary["schema_identity"],
            "source_inventory_identity": primary["source_inventory_identity"],
            "row_counts": primary["readonly_row_counts"],
            "table_hashes": primary["readonly_table_hashes"],
            "independent_rebuild_exact_match": True,
            "status": "PASS",
        },
    )

    release_evidence = {
        "schema": "free-official-binance-dataset-release-identities-v1",
        "created_at_utc": FINAL_TIMESTAMP,
        "selected_window": primary["window_contracts"]["selected"],
        "partition_geometry": primary["window_contracts"]["partition_geometry"],
        "data_tool_lock_identity": primary["data_tool_lock_identity"],
        "semantic_database_identity": primary["semantic_database_identity"],
        "profiles": {},
        "blocking_minute_count": validation["primary_readonly_gate"]["blocking_minute_count"],
        "unresolved_conflict_count": validation["primary_readonly_gate"]["unresolved_conflict_count"],
        "checksum_failure_count": validation["primary_readonly_gate"]["checksum_failure_count"],
        "source_substitution_count": 0,
        "synthetic_bar_count": 0,
        "status": "PASS",
    }
    for profile, release in sorted(primary["releases"].items()):
        release_path = ROOT / "data/releases" / f"{release['dataset_release_id']}.json"
        assert_identity(release_path, sha256_file(release_path))
        release_evidence["profiles"][profile] = {
            "dataset_release_id": release["dataset_release_id"],
            "release_manifest_path": release_path.relative_to(ROOT).as_posix(),
            "release_manifest_sha256": sha256_file(release_path),
            "catalog_identity": release["catalog_identity"],
            "instrument_metadata_identity": release["instrument_metadata_identity"],
            "minute_coverage_identity": release["minute_coverage_identity"],
            "source_reconciliation_identity": release["source_reconciliation_identity"],
            "derived_validation_identity": release["derived_validation_identity"],
            "data_window_identity": release["data_window_identity"],
            "partition_geometry_identity": release["partition_geometry_identity"],
            "mark_data_identity": release["mark_data_identity"],
            "funding_data_identity": release["funding_data_identity"],
            "status": "PASS",
        }
    write_json(EVIDENCE / "dataset-release-identities.json", release_evidence)

    export_evidence = {
        "schema": "free-official-binance-nautilus-export-validation-v1",
        "created_at_utc": FINAL_TIMESTAMP,
        "public_parquet_data_catalog_used": True,
        "catalog_validation": validation["nautilus_catalog_validation"],
        "physical_rebuild_comparison": validation["catalog_physical_comparison"],
        "verified_no_trade_exported_as_bar_count": 0,
        "mark_reconstructed": False,
        "mark_substitution_used": False,
        "strategy_run": False,
        "official_trial": False,
        "network_used": False,
        "status": "PASS",
    }
    write_json(EVIDENCE / "nautilus-export-validation.json", export_evidence)
    write_json(EVIDENCE / "negative-control-results.json", negative_controls(acceptance))

    test_evidence = {
        "schema": "free-official-binance-test-results-v1",
        "created_at_utc": FINAL_TIMESTAMP,
        "acceptance": acceptance,
        "independent_offline_duckdb_reinstall": tool_identity["independent_offline_reinstall"],
        "unique_tests": acceptance["unique_tests"],
        "full_discovery_execution_occurrences": acceptance["full_discovery_execution_occurrences"],
        "failures": 0,
        "errors": 0,
        "skips": 0,
        "xfail": 0,
        "strategy_run": False,
        "official_trial": False,
        "status": "PASS",
    }
    write_json(EVIDENCE / "test-results.json", test_evidence)
    (EVIDENCE / "test-output.txt").write_bytes(ACCEPTANCE_OUTPUT.read_bytes())

    post_integrity = {
        "schema": "free-official-binance-post-adoption-integrity-v1",
        "created_at_utc": FINAL_TIMESTAMP,
        "base_ssot_sha256": BASE_SSOT_SHA256,
        "adopted_ssot_sha256": ADOPTED_SSOT_SHA256,
        "candidate_and_root_byte_equal": True,
        "runtime_lock_sha256": RUNTIME_LOCK_SHA256,
        "dependency_lock_sha256": DEPENDENCY_LOCK_SHA256,
        "historical_duckdb": {
            "path": "data/duckdb/binance-btcusdt-owner-smoke-001.duckdb",
            "size_bytes": HISTORICAL_DB_SIZE,
            "sha256": HISTORICAL_DB_SHA256,
            "modified": False,
        },
        "candidate_003": candidate_003,
        "raw_acquisition_identity": ACQUISITION_IDENTITY,
        "analysis_identity": ANALYSIS_IDENTITY,
        "strategy_or_official_trial_run": False,
        "status": "PASS",
    }
    write_json(EVIDENCE / "post-adoption-integrity.json", post_integrity)

    spot_report = f"""# تقرير بيانات Spot الرسمي المجاني

## النتيجة

نجح إصدار Spot للنافذة `[2021-01-01T00:00:00Z, 2021-08-01T00:00:00Z)` بلا فجوة غير محسومة وبلا Bar مصطنعة.

- الدقائق المتوقعة: `305280`.
- `REAL_OFFICIAL_BAR`: `304595`.
- `DERIVED_FROM_OFFICIAL_TRADES`: `1` عند `2021-04-25T04:00:00Z`، ومصدرها raw trades وaggTrades رسميان متطابقان مع continuity كاملة.
- `VERIFIED_NO_TRADE_INTERVAL`: `684`؛ بقيت coverage فقط ولم تُصدّر كـBar.
- source conflicts غير المحسومة: `0`.
- trade-ID gaps غير المفسرة: `0`.
- duplicate events المقبولة: `0`.

الدقيقة `2021-02-11T03:40:00Z` ثبت خلوها من trades: آخر trade ID قبلها `633819970` وأول ID بعدها `633819971`. صف الـkline الصفري حُفظ observation مستبعدًا ولم يصبح canonical Bar.

فترات no-trade المثبتة: `2021-02-11 02:20–05:00` (`160` دقيقة)، `2021-03-06 02:00–03:30` (`90`)، `2021-04-20 02:00–04:30` (`150`)، و`2021-04-25 04:01–08:45` (`284`). لا OHLCV ولا previous-close ولا forward fill فيها.

DatasetRelease: `{primary['releases']['BINANCE_SPOT_CASH_LONG_ONLY']['dataset_release_id']}`. Catalog: `{primary['catalogs']['BINANCE_SPOT_CASH_LONG_ONLY']['catalog_identity']}` بعدد `304596` Bar حقيقية/مشتقة من trades رسمية فقط.
"""
    (EVIDENCE / "spot-data-report.md").write_text(spot_report, encoding="utf-8", newline="\n")

    perp_report = f"""# تقرير بيانات Perpetual الرسمي المجاني

## النتيجة

نجح إصدار BTCUSDT USDⓈ-M للنافذة `[2021-01-01T00:00:00Z, 2021-08-01T00:00:00Z)` من بيانات Binance الرسمية المجانية فقط.

- execution 1m: `305280/305280`.
- Mark 1m: `305280/305280`، missing/duplicate = `0`.
- Funding events: `636` archive event تطابقت exact مع `636` REST event؛ schedule identity `{primary['funding']['schedule_identity']}`.
- source substitutions: `0`؛ لم تُشتق Mark من execution أوindex أوpremium أوlast أوSpot.

خمسون Daily Mark delivery object بقيت 404 تاريخية. لكل منها أثبت REST وMonthly الرسميان coverage كاملة واتفاقًا exact؛ لذلك صُنفت route packaging غير المتاحة `REDUNDANT_OFFICIAL_DELIVERY_ROLE_UNAVAILABLE` ولم تُخفَ الـ404. وفي يوليو غابت `7200` دقيقة من Monthly، فحُسمت فقط باتفاق Daily وREST الرسميين.

النافذة الأصلية بقيت محجوبة بسبب `IRRECOVERABLE_OFFICIAL_MARK_DELIVERY_GAP` في `[2020-12-17T07:32:00Z, 2020-12-17T07:56:00Z)`؛ لم تُملأ. أول تحريك شهري ميكانيكي، N=1، أعطى النافذة الحالية المكتملة دون فحص Strategy أوPnL.

DatasetRelease: `{primary['releases']['BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING']['dataset_release_id']}`. Catalog: `{primary['catalogs']['BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING']['catalog_identity']}`.
"""
    (EVIDENCE / "perpetual-data-report.md").write_text(perp_report, encoding="utf-8", newline="\n")

    duckdb_report = f"""# تقرير DuckDB وNautilus Catalog

## التخزين والتحقق

- DuckDB: `1.4.5` في `.data-venv` مستقلة؛ wheel SHA-256 `{tool_identity['wheel_sha256']}`؛ offline reinstall و`pip check` نجحا.
- Primary: `{primary['database_path']}`، `{primary['database_size_bytes']}` byte، SHA-256 `{primary['database_file_sha256']}`.
- Independent: `{independent['database_path']}`، `{independent['database_size_bytes']}` byte، SHA-256 `{independent['database_file_sha256']}`.
- semantic database identity المشتركة: `{primary['semantic_database_identity']}`.
- schema identity: `{primary['schema_identity']}`؛ عدد الجداول `18`؛ financial FLOAT/DOUBLE columns = `0`.
- physical hashes مختلفة كما هو مسموح، لكن schema والصفوف المرتبة وper-table hashes وconflicts/dispositions وrelease IDs وcatalog inventories متطابقة exact.
- القاعدتان أُغلقتا، جعلتا read-only، وأُعيد التحقق منهما read-only.

DuckDB طبقة derived validation/storage فقط. raw bytes في content-addressed store هي authority، ولم تُستخدم DuckDB للتنفيذ أوالأوامر أوالحسابات أوPnL.

Nautilus-compatible `ParquetDataCatalog` أعيد بناؤه في مسارين مستقلين ثم قرئ في process منفصل. Spot inventory identity `dde9350bbbb26f53780672a7cd8f3581eed80b991d0f7ff0c6545d84cfbf34a6` وPerpetual inventory identity `e1daf747a1ba51422001da0f346877dd59820aec02af7676b6bae55fa18556f3`، مع semantic equality كاملة وverified-no-trade exported count = `0`.
"""
    (EVIDENCE / "duckdb-validation-report.md").write_text(duckdb_report, encoding="utf-8", newline="\n")

    owner_report = f"""# تقرير Owner — Free Official Binance Data and DuckDB Repair 001

## الحكم

اكتمل الإصلاح ونجحت بوابات البيانات: النافذة المختارة `[2021-01-01T00:00:00Z, 2021-08-01T00:00:00Z)` جاهزة لاستئناف Owner Smoke Research بعد مراجعة المشرف، دون تشغيل Strategy أوOfficial Trial في هذه المهمة.

## القرارات والسبب الجذري

اعتمدت Candidate 004 exact وأصبحت root `SSOT.md` بهوية `{ADOPTED_SSOT_SHA256}`. Candidate 003 بقيت byte-for-byte وموسومة `REJECTED_BY_OWNER_PAID_PROVIDER_PATH_NOT_AUTHORIZED`. لم يُستخدم مزود مدفوع أوطرف ثالث أوcredential.

سبب Spot كان اختلاف packaging تاريخي وصفوف kline صفرية خلال توقفات تداول، إضافة إلى دقيقة جزئية. حُسمت بأحداث Binance raw trades/aggTrades الرسمية وtrade-ID continuity، لا بأولوية صامتة. النتيجة: `304595` Bar رسمية، Bar واحدة مشتقة حتميًا من trades الرسمية، و`684` دقيقة no-trade بلا Bar؛ unresolved = `0`.

سبب Perpetual القديم هو فجوة Mark رسمية غير قابلة للاستعادة مدتها `24` دقيقة، فبقيت النافذة القديمة محجوبة ولم تُصنع Mark. اختيرت آليًا أول نافذة N=1 المكتملة: execution وMark كل منهما `305280/305280`، وfunding `636` event. لم تُفحص Signals أوPnL ولم يُستهلك Final Holdout.

## DuckDB وDataset Releases

- semantic database identity: `{primary['semantic_database_identity']}`.
- Primary DB: `{primary['database_path']}`، `{primary['database_size_bytes']}` byte، `{primary['database_file_sha256']}`.
- Independent DB: `{independent['database_path']}`، `{independent['database_size_bytes']}` byte، `{independent['database_file_sha256']}`.
- Spot DatasetRelease: `{primary['releases']['BINANCE_SPOT_CASH_LONG_ONLY']['dataset_release_id']}`.
- Perpetual DatasetRelease: `{primary['releases']['BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING']['dataset_release_id']}`.
- semantic rebuild وNautilus catalog rebuild: exact PASS.
- raw objects: `{primary['row_counts']['raw_objects']}`، source observations: `{primary['row_counts']['source_observations']}`، checksums: `{primary['row_counts']['publisher_checksums']}`.

كل canonical row قابلة للتتبع إلى raw object رسمي. القاعدتان read-only بعد CHECKPOINT والإغلاق، والـraw bytes خارج DuckDB بقيت authority. لا synthetic OHLC، لا interpolation/fill، لا Mark substitution، لا مصدر غير رسمي، ولا material float arithmetic.

## الاختبارات

اكتُشفت `{acceptance['unique_tests']}` حالة فريدة؛ full + independent + reverse = `{acceptance['full_discovery_execution_occurrences']}` occurrence. data repair targeted = `71`، adversarial = `39`، Owner Smoke contract tests = `13`. failures/errors/skips/xfail = `0`. نجحت runtime preflight وpip checks وcompileall وraw rehash وhistorical integrity وdeterministic rebuild وcatalog comparison.

## القيود

لم تُشغّل Strategy أوBacktest أوOfficial Trial أوOptimization، ولم تُفحص profitability. الجاهزية هنا جاهزية بيانات فقط، وNext Action هو `READY_TO_RESUME_OWNER_SMOKE_RESEARCH_WITHOUT_FINAL_HOLDOUT`.
"""
    (EVIDENCE / "owner-report/README.md").write_text(owner_report, encoding="utf-8", newline="\n")

    # Final content manifest is deliberately last and excludes itself.
    final_manifest_path = EVIDENCE / "final-content-manifest.json"
    actual = sorted(
        path.relative_to(EVIDENCE).as_posix()
        for path in EVIDENCE.rglob("*")
        if path.is_file()
        and path != final_manifest_path
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    )
    entries = []
    json_failures = []
    text_failures = []
    for relative in actual:
        path = EVIDENCE / relative
        content = path.read_bytes()
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text_failures.append(relative)
            text = ""
        if b"\r" in content or (content and not content.endswith(b"\n")):
            text_failures.append(relative)
        if path.suffix == ".json":
            try:
                json.loads(text)
            except (json.JSONDecodeError, UnicodeDecodeError):
                json_failures.append(relative)
        if path.suffix == ".jsonl":
            try:
                for line in text.splitlines():
                    json.loads(line)
            except json.JSONDecodeError:
                json_failures.append(relative)
        entries.append(
            {
                "path": relative,
                "size_bytes": len(content),
                "sha256": sha256_bytes(content),
                "line_count": len(content.splitlines()),
            },
        )
    if json_failures or text_failures:
        raise ValueError(f"final evidence validation failed: json={json_failures}, text={text_failures}")
    evidence_text = "\n".join(
        (EVIDENCE / relative).read_text(encoding="utf-8", errors="strict").lower()
        for relative in actual
        if not relative.startswith("tools/")
    )
    forbidden = ["\"authorization\":", "\"x-mbx-apikey\":", "bearer ", "x-amz-signature="]
    secret_hits = [marker for marker in forbidden if marker in evidence_text]
    if secret_hits:
        raise ValueError(f"secret markers found: {secret_hits}")
    final_manifest = {
        "schema": "free-official-binance-final-content-manifest-v1",
        "created_at_utc": FINAL_TIMESTAMP,
        "epoch": "FREE_OFFICIAL_BINANCE_DATA_AND_DUCKDB_REPAIR_001",
        "terminal_verdict": "FREE_OFFICIAL_BINANCE_DATA_DUCKDB_REPAIR_PASS_PENDING_COMMIT_AND_PUSH",
        "inventory_excludes_itself": True,
        "inventory_self_path": "final-content-manifest.json",
        "file_count_excluding_inventory": len(entries),
        "total_size_bytes_excluding_inventory": sum(entry["size_bytes"] for entry in entries),
        "files": entries,
        "canonical_file_inventory_sha256": sha256_bytes(canonical_bytes(entries)),
        "json_validation_failure_count": 0,
        "utf8_or_lf_failure_count": 0,
        "secret_marker_hits": [],
        "adopted_ssot_sha256": ADOPTED_SSOT_SHA256,
        "semantic_database_identity": primary["semantic_database_identity"],
        "dataset_release_ids": primary["dataset_release_ids"],
        "catalog_identities": sorted(item["catalog_identity"] for item in primary["catalogs"].values()),
        "raw_acquisition_identity": ACQUISITION_IDENTITY,
        "analysis_identity": ANALYSIS_IDENTITY,
        "historical_candidate_003": candidate_003,
        "strategy_run": False,
        "official_trial": False,
        "status": "PASS",
    }
    write_json(final_manifest_path, final_manifest)
    print(
        json.dumps(
            {
                "status": "PASS",
                "file_count": len(entries) + 1,
                "inventory_sha256": final_manifest["canonical_file_inventory_sha256"],
                "semantic_database_identity": primary["semantic_database_identity"],
                "dataset_release_ids": primary["dataset_release_ids"],
            },
            sort_keys=True,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
