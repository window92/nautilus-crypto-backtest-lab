#!/usr/bin/env python3
"""Read-only validator for DATA_PROVENANCE_DUCKDB_REPAIR_001 evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import duckdb


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/repair/data-provenance-duckdb-001"
DATABASE = ROOT / "data/duckdb/binance-btcusdt-owner-smoke-001.duckdb"
ADOPTED_SSOT = "f51971ed7a09b172c82ff5965f2899d2a302dd71a2af60eb7c920133567b4354"
RUNTIME_LOCK = "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd"
DEPENDENCY_LOCK = "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47"
ALLOWED_HOSTS = {
    "api.binance.com",
    "data.binance.vision",
    "fapi.binance.com",
    "raw.githubusercontent.com",
}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def load(name: str) -> Any:
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8"))


def validate(*, pre_final: bool) -> dict[str, Any]:
    required = {
        "README.md",
        "owner-adoption.json",
        "baseline-attestation.json",
        "ssot-compatibility.json",
        "official-source-contracts.json",
        "acquisition-manifest.json",
        "archive-replacement-history.json",
        "december-2020-minute-dispositions.json",
        "archive-conflicts.json",
        "aggtrade-continuity.json",
        "derived-kline-comparisons.json",
        "verified-no-trade-intervals.json",
        "full-window-coverage.json",
        "perpetual-execution-validation.json",
        "mark-grid-validation.json",
        "funding-validation.json",
        "instrument-metadata-validation.json",
        "duckdb-tool-identity.json",
        "duckdb-schema.sql",
        "duckdb-database-manifest.json",
        "duckdb-semantic-inventory.json",
        "deterministic-rebuild.json",
        "parquet-export-comparison.json",
        "dataset-release-manifest.json",
        "raw-object-integrity.json",
        "historical-integrity.json",
        "failed-attempts.jsonl",
        "owner-report/README.md",
    }
    if not pre_final:
        required |= {"test-results.json", "test-output.txt", "final-content-manifest.json"}
    missing = sorted(name for name in required if not (EVIDENCE / name).is_file())
    checks: dict[str, bool] = {"required_files_present": not missing}

    json_errors = []
    for path in EVIDENCE.rglob("*.json"):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            json_errors.append({"path": str(path.relative_to(ROOT)), "error": str(exc)})
    checks["all_json_valid_utf8"] = not json_errors
    checks["adopted_ssot_identity"] = digest(ROOT / "SSOT.md") == ADOPTED_SSOT
    checks["runtime_lock_identity"] = digest(ROOT / "runtime.lock.json") == RUNTIME_LOCK
    checks["dependency_lock_identity"] = (
        digest(ROOT / "requirements.lock.txt") == DEPENDENCY_LOCK
    )
    checks["candidate_bytes_equal_root"] = (
        ROOT / "SSOT.md"
    ).read_bytes() == (
        EVIDENCE / "ssot-candidate-002/SSOT.data-provenance-candidate.md"
    ).read_bytes()

    acquisition = load("acquisition-manifest.json")
    checks["all_acquisition_urls_official"] = all(
        urlsplit(item["exact_url"]).hostname in ALLOWED_HOSTS
        and (
            urlsplit(item["exact_url"]).hostname != "raw.githubusercontent.com"
            or urlsplit(item["exact_url"]).path.startswith("/binance/")
        )
        for item in acquisition["observations"]
    )
    checks["raw_saved_before_parse"] = acquisition["raw_bytes_saved_before_parsing"] is True
    checks["no_third_party_data"] = acquisition["third_party_data_used"] is False
    replacement_history = load("archive-replacement-history.json")
    checks["official_archive_replacement_history"] = (
        replacement_history["status"] == "PASS"
        and replacement_history["target_one_minute_update_count"] == 10
        and replacement_history["all_current_objects_match_official_replacement"] is True
    )
    raw = load("raw-object-integrity.json")
    checks["raw_objects_rehashed"] = (
        raw["status"] == "PASS"
        and raw["all_objects_rehashed"] is True
        and raw["failure_count"] == 0
        and raw["object_count"] == acquisition["observation_count"] - 1
    )

    db_manifest = load("duckdb-database-manifest.json")
    checks["database_present_and_hashed"] = (
        DATABASE.is_file()
        and DATABASE.stat().st_size == db_manifest["database_size_bytes"]
        and digest(DATABASE) == db_manifest["database_file_sha256"]
    )
    connection = duckdb.connect(
        str(DATABASE),
        read_only=True,
        config={
            "allow_unsigned_extensions": "false",
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
        },
    )
    try:
        row_counts = {
            row[0]: int(row[1])
            for row in connection.execute(
                """
                SELECT table_name, estimated_size
                  FROM duckdb_tables() WHERE schema_name = 'main'
                 ORDER BY table_name
                """,
            ).fetchall()
        }
        actual_counts = {
            table: int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
            for table in row_counts
        }
        checks["database_row_counts_match_manifest"] = actual_counts == db_manifest["row_counts"]
        checks["one_coverage_row_per_minute"] = connection.execute(
            """
            SELECT count(*) = 2 FROM (
              SELECT market_profile FROM minute_coverage
               GROUP BY market_profile HAVING count(*) = 305280
            )
            """,
        ).fetchone()[0]
        checks["no_forbidden_canonical_disposition"] = connection.execute(
            """
            SELECT count(*) = 0 FROM canonical_execution_bars
             WHERE disposition NOT IN ('REAL_OFFICIAL_BAR','DERIVED_FROM_OFFICIAL_TRADES')
            """,
        ).fetchone()[0]
        checks["no_verified_no_trade_exported"] = connection.execute(
            """
            SELECT count(*) = 0
              FROM minute_coverage c JOIN canonical_execution_bars b
                ON c.market_profile = b.market_profile
               AND c.symbol = b.symbol AND c.open_time_ms = b.open_time_ms
             WHERE c.disposition = 'VERIFIED_NO_TRADE_INTERVAL'
            """,
        ).fetchone()[0]
        checks["publisher_checksums_all_match"] = connection.execute(
            "SELECT count(*) = 632 AND bool_and(local_match) FROM publisher_checksums",
        ).fetchone()[0]
        checks["dataset_release_table_empty"] = connection.execute(
            "SELECT count(*) = 0 FROM dataset_releases",
        ).fetchone()[0]
        checks["expected_blockers_retained"] = (
            connection.execute("SELECT count(*) FROM minute_coverage WHERE blocking").fetchone()[0]
            == 1
            and load("mark-grid-validation.json")["blocking_minute_count"] == 72_024
            and load("mark-grid-validation.json")["official_daily_archive_absence_count"] == 50
        )
    finally:
        connection.close()

    rebuild = load("deterministic-rebuild.json")
    checks["deterministic_rebuild"] = all(
        rebuild[name]
        for name in (
            "schema_identity_equal",
            "semantic_identity_equal",
            "row_counts_equal",
            "canonical_export_identity_equal",
            "canonical_export_bytes_equal",
        )
    )
    release = load("dataset-release-manifest.json")
    checks["blocked_release_is_truthful"] = (
        release["status"] == "DATASET_RELEASE_BLOCKED"
        and release["dataset_release_ids"] == []
        and len(release["blocking_items"]) == 75
        and release["strategy_started"] is False
        and release["official_trial_started"] is False
    )
    checks["sparse_nautilus_qualification"] = (
        load("parquet-export-comparison.json")["status"] == "PASS"
    )
    checks["historical_evidence_integrity"] = load("historical-integrity.json")[
        "status"
    ] == "PASS"
    if not pre_final:
        tests = load("test-results.json")
        checks["acceptance_tests_pass"] = tests["status"] == "PASS"
        manifest = load("final-content-manifest.json")
        manifest_ok = True
        for item in manifest["files"]:
            path = ROOT / item["path"]
            manifest_ok = (
                manifest_ok
                and path.is_file()
                and path.stat().st_size == item["size_bytes"]
                and digest(path) == item["sha256"]
            )
        checks["final_content_manifest"] = manifest_ok

    failed = sorted(name for name, passed in checks.items() if not passed)
    return {
        "schema": "data-provenance-evidence-validation-v1",
        "status": "PASS" if not failed else "FAIL",
        "mode": "PRE_FINAL" if pre_final else "FINAL",
        "checks": checks,
        "missing_files": missing,
        "json_errors": json_errors,
        "failed_checks": failed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pre-final", action="store_true")
    arguments = parser.parse_args()
    result = validate(pre_final=arguments.pre_final)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
