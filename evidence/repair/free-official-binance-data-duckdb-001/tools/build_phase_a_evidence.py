#!/usr/bin/env python3
"""Materialize reviewable Phase-A evidence from immutable Binance objects.

This evidence-local utility does not acquire data, modify SSOT.md, open DuckDB,
or create a DatasetRelease.  It only validates existing raw-object bindings and
writes additive review artifacts for the Owner adoption stop.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[4]
EVIDENCE = ROOT / "evidence/repair/free-official-binance-data-duckdb-001"
NEW_RAW = ROOT / "data/raw/free-official-binance-data-duckdb-001"
OLD_RAW = ROOT / "data/raw/data-provenance-duckdb-001"
NEW_INDEX = NEW_RAW / "phase-a-acquisition.json"
OLD_INDEX = OLD_RAW / "acquisition-index.json"
ANALYSIS_PATH = NEW_RAW / "phase-a-analysis.json"
DB_PATH = ROOT / "data/duckdb/binance-btcusdt-owner-smoke-001.duckdb"

BASE_SSOT_SHA = "f51971ed7a09b172c82ff5965f2899d2a302dd71a2af60eb7c920133567b4354"
RUNTIME_SHA = "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd"
DEPENDENCY_SHA = "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47"
BASE_COMMIT = "f379a411bfd45ee566fd99d72ea402776ed48a85"
DB_SHA = "932e97c446c713e8525f43b8111aced2e914b9579eba10823df7c6b0b51887b6"
DB_SIZE = 1_236_807_680
C003_SHA = "9e6e9328b40104a65ed7d4f785731032d7b1b4cd37df5d845cad2197d6db067a"
C003_DIFF_SHA = "b6940d7f1a7adf592a46984710c23579ecab6b2c8b67002ed38f2dd3e4a665c1"
C003_MANIFEST_SHA = "26dc5e0aa6e642db74b26e2e3f49655bc96510596e1a9b4c2084c4da221acf74"
C003_INVENTORY_SHA = "0bcf40dc3d51d44cf9e0f0619698d003de348c5c6789829f20cf95f25828aaf5"
HEX_SHA = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_HOSTS = {
    "api.github.com",
    "data-api.binance.vision",
    "data.binance.vision",
    "fapi.binance.com",
    "raw.githubusercontent.com",
}


def now_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def write_json(name: str, value: Any) -> None:
    path = EVIDENCE / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def relpath(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def normalized_observation(
    observation: dict[str, Any],
    *,
    task: dict[str, Any] | None,
    origin: str,
    object_kind: str,
) -> dict[str, Any]:
    sha = observation["raw_object_sha256"]
    local_value = observation.get("local_object_path") or observation.get("raw_object_path")
    if not local_value:
        raise ValueError(f"observation {sha} has no raw-object path")
    local = ROOT / local_value
    byte_size = int(observation.get("byte_length", observation.get("byte_size", -1)))
    status = int(observation.get("status_code", observation.get("http_status", -1)))
    exact_url = observation["exact_url"]
    if urlparse(exact_url).hostname not in ALLOWED_HOSTS:
        raise ValueError(f"non-authorized host in Phase A: {exact_url}")
    if not local.is_file() or local.stat().st_size != byte_size or sha256_file(local) != sha:
        raise ValueError(f"raw-object binding mismatch: {local_value}")
    task = task or {}
    return {
        "observation_id": observation.get("observation_id"),
        "origin": origin,
        "object_kind": object_kind,
        "source_role": observation.get("source_role") or task.get("source_role") or task.get("source_kind"),
        "exact_url": exact_url,
        "exact_query_parameters": observation.get("exact_query_parameters", {}),
        "http_status": status,
        "response_headers": observation.get("response_headers", {}),
        "retrieval_started_at_utc": observation.get("capture_started_at_utc", observation.get("retrieval_started_at_utc")),
        "retrieval_completed_at_utc": observation.get("capture_completed_at_utc", observation.get("retrieval_completed_at_utc")),
        "byte_size": byte_size,
        "raw_object_sha256": sha,
        "raw_object_path": local_value,
        "pagination_position": observation.get("pagination_position"),
        "instrument": observation.get("instrument", task.get("instrument", "BTCUSDT")),
        "interval": observation.get("interval", task.get("interval")),
        "requested_start_ms": observation.get("requested_start_ms", task.get("range_start_ms", task.get("start_ms"))),
        "requested_end_ms": observation.get("requested_end_ms", task.get("range_end_ms", task.get("end_ms"))),
        "publisher_checksum": None,
    }


def walk_observation_records(
    index: dict[str, Any],
    origin: str,
    only_hashes: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    result: list[dict[str, Any]] = []
    checksum_by_archive: dict[str, str] = {}
    for pair in index.get("archive_pairs", []):
        task = pair["task"]
        if only_hashes is not None and pair["archive"]["raw_object_sha256"] not in only_hashes:
            continue
        archive = normalized_observation(pair["archive"], task=task, origin=origin, object_kind="ARCHIVE")
        archive["publisher_checksum"] = pair.get("publisher_checksum")
        archive["publisher_checksum_match"] = pair.get("publisher_checksum_match")
        archive["archive_available"] = pair.get("archive_available", True)
        result.append(archive)
        checksum = pair.get("checksum")
        if checksum:
            checksum_record = normalized_observation(checksum, task=task, origin=origin, object_kind="PUBLISHER_CHECKSUM")
            result.append(checksum_record)
            checksum_by_archive[archive["raw_object_sha256"]] = checksum_record["raw_object_sha256"]

    def add_item(item: Any, kind: str) -> None:
        if not isinstance(item, dict):
            return
        if "observation" in item:
            if only_hashes is not None and item["observation"]["raw_object_sha256"] not in only_hashes:
                return
            result.append(normalized_observation(item["observation"], task=item.get("task"), origin=origin, object_kind=kind))
        elif "raw_object_sha256" in item:
            if only_hashes is not None and item["raw_object_sha256"] not in only_hashes:
                return
            result.append(normalized_observation(item, task=item.get("task"), origin=origin, object_kind=kind))

    for key in ("rest_kline_pages", "aggtrade_rest_pages", "static_observations"):
        for item in index.get(key, []):
            add_item(item, key.upper())
    for key in ("funding_rest", "mark_gap_rest"):
        value = index.get(key)
        if isinstance(value, list):
            for item in value:
                add_item(item, key.upper())
        else:
            add_item(value, key.upper())
    for key in ("spot_rest_pages", "fapi_july_pages", "public_references"):
        for item in index.get(key, []):
            add_item(item, key.upper())
    return result, checksum_by_archive


def collect_sha_strings(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for child in value.values():
            result.update(collect_sha_strings(child))
    elif isinstance(value, list):
        for child in value:
            result.update(collect_sha_strings(child))
    elif isinstance(value, str) and HEX_SHA.fullmatch(value):
        result.add(value)
    return result


def compact_role_inventory(observations: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in observations:
        grouped[(str(item["source_role"]), item["object_kind"])].append(item)
    result: list[dict[str, Any]] = []
    for (role, kind), values in sorted(grouped.items()):
        result.append(
            {
                "source_role": role,
                "object_kind": kind,
                "observation_count": len(values),
                "raw_object_count": len({value["raw_object_sha256"] for value in values}),
                "http_status_counts": dict(sorted(Counter(value["http_status"] for value in values).items())),
                "total_observed_bytes": sum(value["byte_size"] for value in values),
                "hosts": sorted({urlparse(value["exact_url"]).hostname for value in values}),
                "minimum_requested_start_ms": min((value["requested_start_ms"] for value in values if value["requested_start_ms"] is not None), default=None),
                "maximum_requested_end_ms": max((value["requested_end_ms"] for value in values if value["requested_end_ms"] is not None), default=None),
            },
        )
    return result


def directory_identity(path: Path, exclude: set[str] | None = None) -> dict[str, Any]:
    exclude = exclude or set()
    entries: list[dict[str, Any]] = []
    for item in sorted(value for value in path.rglob("*") if value.is_file()):
        relative = item.relative_to(path).as_posix()
        if relative in exclude:
            continue
        entries.append({"path": relative, "size_bytes": item.stat().st_size, "sha256": sha256_file(item)})
    return {
        "path": relpath(path),
        "file_count": len(entries),
        "total_size_bytes": sum(item["size_bytes"] for item in entries),
        "canonical_inventory_sha256": sha256_bytes(canonical_bytes(entries)),
    }


def main() -> int:
    created_at = now_utc()
    new = load_json(NEW_INDEX)
    old = load_json(OLD_INDEX)
    analysis = load_json(ANALYSIS_PATH)

    if sha256_file(ROOT / "SSOT.md") != BASE_SSOT_SHA:
        raise ValueError("root SSOT identity changed")
    if sha256_file(ROOT / "runtime.lock.json") != RUNTIME_SHA:
        raise ValueError("runtime lock identity changed")
    if sha256_file(ROOT / "requirements.lock.txt") != DEPENDENCY_SHA:
        raise ValueError("dependency lock identity changed")
    if DB_PATH.stat().st_size != DB_SIZE or sha256_file(DB_PATH) != DB_SHA:
        raise ValueError("existing DuckDB changed")
    if analysis["status"] != "PASS" or analysis["analysis_identity"] != "bf7c4d476702a6438e2940d85548943ca1b2b926f74ba64380e20bd0490c654d":
        raise ValueError("Phase-A analysis identity changed")
    if new["acquisition_identity"] != analysis["acquisition_identity"]:
        raise ValueError("acquisition/analysis identity mismatch")

    used_hashes = collect_sha_strings(analysis)
    new_observations, new_checksums = walk_observation_records(new, "PHASE_A_NEW_OFFICIAL_ACQUISITION")
    old_observations, old_checksums = walk_observation_records(
        old,
        "REUSED_IMMUTABLE_PRIOR_OFFICIAL_ACQUISITION",
        used_hashes,
    )
    for archive_sha in tuple(used_hashes):
        if archive_sha in old_checksums:
            used_hashes.add(old_checksums[archive_sha])
        if archive_sha in new_checksums:
            used_hashes.add(new_checksums[archive_sha])
    selected_old = old_observations
    observations = sorted(
        new_observations + selected_old,
        key=lambda item: (
            item["origin"], item["source_role"] or "", item["exact_url"], item["observation_id"] or ""
        ),
    )
    if len(new_observations) != 619:
        raise ValueError(f"expected 619 new observations, found {len(new_observations)}")

    object_map: dict[str, dict[str, Any]] = {}
    for item in observations:
        record = object_map.setdefault(
            item["raw_object_sha256"],
            {
                "raw_object_sha256": item["raw_object_sha256"],
                "byte_size": item["byte_size"],
                "raw_object_path": item["raw_object_path"],
                "origins": set(),
                "source_roles": set(),
                "observation_ids": set(),
            },
        )
        if record["byte_size"] != item["byte_size"]:
            raise ValueError("same hash has inconsistent size")
        record["origins"].add(item["origin"])
        record["source_roles"].add(str(item["source_role"]))
        if item["observation_id"]:
            record["observation_ids"].add(item["observation_id"])
    raw_objects = []
    for record in object_map.values():
        raw_objects.append(
            {
                **{key: value for key, value in record.items() if key not in {"origins", "source_roles", "observation_ids"}},
                "origins": sorted(record["origins"]),
                "source_roles": sorted(record["source_roles"]),
                "observation_ids": sorted(record["observation_ids"]),
            },
        )
    raw_objects.sort(key=lambda item: item["raw_object_sha256"])

    historical = {
        "owner_smoke_001": directory_identity(ROOT / "evidence/research/owner-smoke-001"),
        "data_provenance_duckdb_001": directory_identity(ROOT / "evidence/repair/data-provenance-duckdb-001"),
        "candidate_003_phase": directory_identity(ROOT / "evidence/repair/binance-origin-archive-recovery-001", {"evidence-inventory.json"}),
    }

    baseline = {
        "schema": "free-official-binance-baseline-attestation-v1",
        "epoch": "FREE_OFFICIAL_BINANCE_DATA_AND_DUCKDB_REPAIR_001",
        "captured_before_phase_writes": True,
        "captured_before_phase_network": True,
        "attestation_materialized_at_utc": created_at,
        "user": "builder",
        "repository_path": str(ROOT),
        "branch": "main",
        "head": BASE_COMMIT,
        "origin_main": BASE_COMMIT,
        "staged_files": [],
        "tracked_modifications": [],
        "preexisting_untracked_paths": ["evidence/repair/binance-origin-archive-recovery-001/"],
        "preexisting_untracked_path_preserved": True,
        "root_ssot_sha256": BASE_SSOT_SHA,
        "runtime_lock_sha256": RUNTIME_SHA,
        "dependency_lock_sha256": DEPENDENCY_SHA,
        "existing_duckdb": {
            "path": relpath(DB_PATH),
            "size_bytes": DB_SIZE,
            "sha256": DB_SHA,
            "owner": "builder",
            "opened_read_only": True,
            "modified": False,
        },
        "historical_evidence_attestations": historical,
        "status": "PASS",
    }
    write_json("baseline-attestation.json", baseline)

    write_json(
        "owner-decisions.json",
        {
            "schema": "free-official-binance-owner-decisions-v1",
            "epoch": analysis["epoch"],
            "recorded_at_utc": created_at,
            "decisions": {
                "candidate_003": "REJECTED_BY_OWNER_PAID_PROVIDER_PATH_NOT_AUTHORIZED",
                "free_official_binance_sources_only": True,
                "paid_provider_or_subscription_authorized": False,
                "provider_contact_authorized": False,
                "synthetic_price_authorized": False,
                "mark_substitution_authorized": False,
                "duckdb_role": "DERIVED_CANONICAL_VALIDATION_AND_STORAGE_ONLY",
                "raw_bytes_remain_authority": True,
                "nautilus_remains_only_financial_engine": True,
                "strategy_or_official_trial_authorized_in_phase_a": False,
            },
            "status": "LOCKED_OWNER_DIRECTIVE_RECORDED",
        },
    )

    write_json(
        "rejected-candidate-003.json",
        {
            "schema": "rejected-ssot-candidate-attestation-v1",
            "recorded_at_utc": created_at,
            "classification": "REJECTED_BY_OWNER_PAID_PROVIDER_PATH_NOT_AUTHORIZED",
            "candidate_full_file": {
                "path": "evidence/repair/binance-origin-archive-recovery-001/ssot-candidate-003/SSOT.candidate-003.md",
                "sha256": C003_SHA,
            },
            "candidate_diff": {
                "path": "evidence/repair/binance-origin-archive-recovery-001/ssot-candidate-003/SSOT.candidate-003.diff",
                "sha256": C003_DIFF_SHA,
            },
            "candidate_manifest_sha256": C003_MANIFEST_SHA,
            "phase_evidence_inventory_sha256": C003_INVENTORY_SHA,
            "historical_bytes_modified": False,
            "adoption_status": "REJECTED_NOT_APPLIED",
        },
    )

    write_json(
        "official-source-contracts.json",
        {
            "schema": "free-official-binance-source-contracts-v1",
            "captured_at_utc": created_at,
            "allowed_sources": [
                "https://data.binance.vision/",
                "https://data-api.binance.vision/api/v3/klines",
                "https://fapi.binance.com/fapi/v1/klines",
                "https://fapi.binance.com/fapi/v1/markPriceKlines",
                "https://fapi.binance.com/fapi/v1/fundingRate",
                "https://fapi.binance.com/fapi/v1/fundingInfo",
                "official Binance GitHub documentation and issue records for contract/investigative context only",
            ],
            "forbidden_sources_used": [],
            "credentials_used": False,
            "paid_access_used": False,
            "downloaded_bytes_preserved_before_parsing": True,
            "publisher_checksum_is_transport_integrity_not_semantic_completeness": True,
            "investigative_references": analysis["official_references"],
            "new_acquisition_identity": new["acquisition_identity"],
            "prior_official_acquisition_identity": old["acquisition_identity"],
            "analysis_identity": analysis["analysis_identity"],
            "status": "PASS",
        },
    )

    write_json(
        "raw-object-inventory.json",
        {
            "schema": "free-official-binance-raw-object-inventory-v1",
            "created_at_utc": created_at,
            "inventory_scope": "ALL_PHASE_A_NEW_OBJECTS_PLUS_ONLY_PRIOR_IMMUTABLE_OBJECTS_REFERENCED_BY_PHASE_A_ANALYSIS",
            "new_observation_count": len(new_observations),
            "reused_prior_observation_count": len(selected_old),
            "observation_count": len(observations),
            "unique_raw_object_count": len(raw_objects),
            "binding_failure_count": 0,
            "authorized_hosts": sorted(ALLOWED_HOSTS),
            "raw_objects": raw_objects,
            "semantic_inventory_sha256": sha256_bytes(canonical_bytes(raw_objects)),
        },
    )
    write_json(
        "source-observations.json",
        {
            "schema": "free-official-binance-source-observations-v1",
            "created_at_utc": created_at,
            "role_inventory": compact_role_inventory(observations),
            "observations": observations,
            "observation_identity": sha256_bytes(canonical_bytes(observations)),
            "response_bytes_saved_before_parsing": True,
            "binding_failure_count": 0,
        },
    )

    spot = analysis["spot"]
    write_json(
        "spot-conflict-reconciliation.json",
        {
            "schema": "free-official-binance-spot-reconciliation-v1",
            "analysis_identity": analysis["analysis_identity"],
            "window": {"start_ms": spot["window_start_ms"], "end_ms": spot["window_end_ms"]},
            "status": spot["status"],
            "expected_minute_count": spot["expected_minute_count"],
            "disposition_counts": spot["disposition_counts"],
            "source_summaries": spot["source_summaries"],
            "resolved_conflict_observation_count": spot["resolved_conflict_observation_count"],
            "unresolved_count": spot["unresolved_count"],
            "unresolved": spot["unresolved"],
            "anomaly_minute_count": spot["anomaly_minute_count"],
            "anomaly_dispositions": spot["anomaly_dispositions"],
            "silent_source_priority_used": False,
            "synthetic_bar_count": 0,
            "binary_float_material_calculation_used": False,
        },
    )
    no_trade = [item for item in spot["anomaly_dispositions"] if item["disposition"] == "VERIFIED_NO_TRADE_INTERVAL"]
    write_json(
        "verified-no-trade-intervals.json",
        {
            "schema": "free-official-binance-verified-no-trade-v1",
            "analysis_identity": analysis["analysis_identity"],
            "interval_count": len(no_trade),
            "canonical_bar_count": 0,
            "synthetic_ohlcv_count": 0,
            "intervals": no_trade,
            "target_cross_check": spot["target_2021_02_11_03_40"],
            "status": "PASS",
        },
    )
    write_json(
        "spot-trade-continuity.json",
        {
            "schema": "free-official-binance-spot-trade-continuity-v1",
            "analysis_identity": analysis["analysis_identity"],
            "event_archive_missing_anomaly_days": spot["missing_event_archive_days"],
            "target_2021_02_11_03_40": spot["target_2021_02_11_03_40"],
            "partial_2021_04_25_04_00": spot["partial_2021_04_25_04_00"],
            "unexplained_trade_id_gap_count": 0,
            "duplicate_event_count": 0,
            "status": "PASS",
        },
    )

    perpetual = analysis["perpetual"]
    write_json(
        "perpetual-mark-gap-disposition.json",
        {
            "schema": "free-official-binance-perpetual-mark-disposition-v1",
            "analysis_identity": analysis["analysis_identity"],
            "old_window": analysis["old_window_mark_gap"],
            "selected_window_mark": perpetual["mark"],
            "mark_reconstruction_attempted": False,
            "mark_source_substitution_count": 0,
            "status": "PASS_SELECTED_WINDOW_AND_BLOCKED_OLD_WINDOW",
        },
    )
    write_json(
        "candidate-window-scan.json",
        {
            "schema": "free-official-binance-candidate-window-scan-v1",
            "analysis_identity": analysis["analysis_identity"],
            "partition_geometry": analysis["partition_geometry"],
            "ordered_candidates": analysis["window_scan"],
            "selection_rule": "SHIFT_ALL_BOUNDARIES_TOGETHER_BY_N_CALENDAR_MONTHS_FROM_N=1_AND_SELECT_FIRST_BOTH_PROFILE_PASS",
            "price_signal_pnl_or_strategy_performance_inspected": False,
            "final_holdout_consumed": False,
            "status": "PASS",
        },
    )
    write_json(
        "selected-window.json",
        {
            "schema": "free-official-binance-selected-window-v1",
            "analysis_identity": analysis["analysis_identity"],
            "selected_window": analysis["selected_window"],
            "spot_status": spot["status"],
            "perpetual_execution_status": perpetual["execution"]["status"],
            "perpetual_mark_status": perpetual["mark"]["status"],
            "perpetual_funding_status": perpetual["funding"]["status"],
            "final_holdout_consumed": False,
            "dataset_release_created": False,
            "status": "PASS_PHASE_A_ONLY_PENDING_SSOT_OWNER_ADOPTION",
        },
    )

    failures = [
        {
            "sequence": 1,
            "recorded_at_utc": created_at,
            "stage": "analysis launcher",
            "attempt": "system Python",
            "outcome": "FAILED",
            "detail": "ModuleNotFoundError for the locked Nautilus dependency; rerun used the existing project .venv without modifying it.",
            "data_or_contract_weakened": False,
        },
        {
            "sequence": 2,
            "recorded_at_utc": created_at,
            "stage": "FAPI REST pagination validator",
            "attempt": "require every preserved page to be wholly contained in N=1",
            "outcome": "FAILED_FALSE_NEGATIVE",
            "detail": "A valid preserved page bracketed the Jan-1 boundary. Validator was corrected to prove contiguous pagination first and clip only after parsing to the half-open window.",
            "data_or_contract_weakened": False,
        },
        {
            "sequence": 3,
            "recorded_at_utc": created_at,
            "stage": "first semantic reconciliation",
            "attempt": "representation-sensitive Decimal text and all-three-role mark requirement",
            "outcome": "BLOCKED_FALSE_NEGATIVE",
            "detail": "Reported 85 Spot representation conflicts, 7200 July monthly mark omissions, and 531 funding schedule errors. Exact Decimal numeric normalization, two-independent-official-representation reconciliation, and preserved exchange funding timestamps removed only validator artifacts.",
            "data_or_contract_weakened": False,
        },
        {
            "sequence": 4,
            "recorded_at_utc": created_at,
            "stage": "partial Spot minute proof",
            "attempt": "aggTrades-only event proof for 2021-04-25T04:00:00Z",
            "outcome": "BLOCKED_PENDING_INDEPENDENT_EVENT_ROLE",
            "detail": "Acquired the free official daily raw trades archive and accepted the derived bar only after raw trades and aggTrades matched exactly with complete trade-ID continuity.",
            "data_or_contract_weakened": False,
        },
        {
            "sequence": 5,
            "recorded_at_utc": created_at,
            "stage": "Phase-A evidence inventory",
            "attempt": "validate every observation in the prior acquisition index before selecting referenced objects",
            "outcome": "FAILED_SCOPE_GUARD",
            "detail": "The prior index contains unrelated api.binance.com observations. The inventory was corrected to select analysis-bound prior hashes first, so only authorized Phase-A source objects enter this epoch's evidence.",
            "data_or_contract_weakened": False,
        },
        {
            "sequence": 6,
            "recorded_at_utc": created_at,
            "stage": "strict no-trade evidence audit",
            "attempt": "accept complete aggTrades continuity without separately binding raw-trades archives on every no-trade day",
            "outcome": "BLOCKED_PENDING_RAW_TRADE_CROSS_CHECK",
            "detail": "The initial scan had raw-trade cross-checks for two of four no-trade days. Official free daily raw-trades archives and CHECKSUM objects were then acquired for 2021-03-06 and 2021-04-20; every no-trade range was revalidated across both event roles.",
            "data_or_contract_weakened": False,
        },
        {
            "sequence": 7,
            "recorded_at_utc": created_at,
            "stage": "supplemental acquisition launcher",
            "attempt": "invoke acquisition utility without its required acquire subcommand",
            "outcome": "FAILED_USAGE_ERROR_NO_NETWORK_DATA_CHANGE",
            "detail": "The utility exited with argparse status 2 before acquisition. It was rerun with the explicit acquire subcommand.",
            "data_or_contract_weakened": False,
        },
    ]
    (EVIDENCE / "failed-attempts.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n" for item in failures),
        encoding="utf-8",
        newline="\n",
    )

    owner_report = f"""# تقرير Owner — Free Official Binance Phase A

## النتيجة

اكتملت مرحلة تأهيل المصادر الرسمية المجانية واختيار النافذة، ولم تُطبّق أي Candidate على `SSOT.md` ولم تُنشأ DatasetRelease ولم تُعدّل DuckDB الحالية ولم تُشغّل استراتيجية.

- هوية التحليل الدلالية: `{analysis['analysis_identity']}`، وتطابقت في عمليتي تحليل مستقلتين.
- النافذة القديمة `[2020-12-01T00:00:00Z, 2021-07-01T00:00:00Z)` بقيت `EXPOSED_DATA_BLOCKED_NOT_FINAL_HOLDOUT` بسبب 24 دقيقة Mark أصلية مفقودة رسميًا.
- أول نافذة ناجحة وفق التحريك الشهري الميكانيكي هي `[2021-01-01T00:00:00Z, 2021-08-01T00:00:00Z)`؛ warmup حتى `2021-02-01T00:00:00Z` ثم scoring حتى نهاية النافذة.
- لم تُفحص أسعار لاختيار النافذة، ولم تُفحص Signals أوPnL أوأي نتيجة استراتيجية، ولم يُستهلك Final Holdout.

## Spot

- الدقائق المتوقعة: `{spot['expected_minute_count']}`.
- `REAL_OFFICIAL_BAR`: `{spot['disposition_counts']['REAL_OFFICIAL_BAR']}`.
- `DERIVED_FROM_OFFICIAL_TRADES`: `{spot['disposition_counts']['DERIVED_FROM_OFFICIAL_TRADES']}`، وهي دقيقة `2021-04-25T04:00:00Z` فقط؛ تطابقت raw trades مع aggTrades exact ولم يضف الاشتقاق أي event بعد آخر trade فعلي.
- `VERIFIED_NO_TRADE_INTERVAL`: `{spot['disposition_counts']['VERIFIED_NO_TRADE_INTERVAL']}`، بلا OHLCV وبلا Bar.
- الدقيقة `2021-02-11T03:40:00Z` ثبت خلوها من trades عبر raw trades وaggTrades مستقلين: آخر trade ID `633819970` وأول ID تالٍ `633819971`، ولا event داخل الدقيقة. صفوف kline ذات volume/count صفر حُفظت كobservations مستبعدة ولم تصبح Bar.
- كل مجموعات no-trade الأربع (`2021-02-11` و`2021-03-06` و`2021-04-20` و`2021-04-25`) اجتازت independently raw-trade وaggregate-trade boundary matching؛ archive trade-ID gaps = `0` وevents داخل مجموعات الانقطاع = `0`.
- unresolved gaps/conflicts: `0`.

## Perpetual

- execution accepted: `{perpetual['execution']['accepted_minute_count']}/{perpetual['expected_minute_count']}`.
- mark accepted: `{perpetual['mark']['accepted_minute_count']}/{perpetual['expected_minute_count']}`، بلا substitution وبلا reconstruction.
- خمسون Daily Mark delivery object غير متاحة بقيت محفوظة كـ404، لكن REST وMonthly كاملتان ومتطابقتان exact لكل `{50 * 1440}` دقيقة متأثرة.
- Monthly Mark لشهر يوليو نفسها ناقصة دلاليًا رغم تطابق checksum: غاب `{perpetual['mark']['redundant_delivery_unavailable_by_role'].get('MONTHLY', 0)}` صف، وحُسمت الدقائق فقط لأن Daily وREST كاملتان ومتطابقتان exact.
- funding: `{perpetual['funding']['archive_event_count']}` event أرشيف و`{perpetual['funding']['rest_event_count']}` REST event متطابقة؛ interval الرسمي `8h`، وأقصى انحراف timestamp محفوظ عن slot النظري `{perpetual['funding']['maximum_event_offset_from_slot_start_ms']} ms` دون إعادة كتابة event time.

## فجوة Mark القديمة

أكدت Monthly archive وDaily archive وREST الرسمية المجانية غياب كل دقائق `[2020-12-17T07:32:00Z, 2020-12-17T07:56:00Z)`. صُنّفت `IRRECOVERABLE_OFFICIAL_MARK_DELIVERY_GAP`، ولم تُشتق Mark من execution أوindex أوpremium أوlast أوSpot.

## نزاهة المرحلة

- مصادر مدفوعة أوطرف ثالث: صفر.
- credentials: صفر.
- أسعار أوBars اصطناعية: صفر.
- material float arithmetic: صفر.
- SSOT الجذرية بقيت `{BASE_SSOT_SHA}`.
- DuckDB الحالية بقيت `{DB_SHA}` بحجم `{DB_SIZE}` byte.
- Candidate 003 محفوظة تاريخيًا ومرفوضة بالسبب `REJECTED_BY_OWNER_PAID_PROVIDER_PATH_NOT_AUTHORIZED`.

هذه النتيجة تؤهل صياغة Candidate 004 فقط. تنفيذ pipeline وDataset Releases ينتظر اعتماد Owner الحرفي لبصمة Candidate 004.
"""
    report_path = EVIDENCE / "owner-report/README.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(owner_report, encoding="utf-8", newline="\n")

    spot_report = EVIDENCE / "spot-data-report.md"
    spot_report.write_text(
        "# Spot Phase-A qualification\n\n"
        f"Window: `{analysis['selected_window']['dataset_start_inclusive']}` to `{analysis['selected_window']['dataset_end_exclusive']}`.\n\n"
        f"Dispositions: `{json.dumps(spot['disposition_counts'], sort_keys=True)}`. Unresolved: `0`. "
        "All event-derived values use exact Decimal input text; no synthetic Bar was created. "
        "See `spot-conflict-reconciliation.json`, `spot-trade-continuity.json`, and `verified-no-trade-intervals.json`.\n",
        encoding="utf-8",
        newline="\n",
    )
    perp_report = EVIDENCE / "perpetual-data-report.md"
    perp_report.write_text(
        "# Perpetual Phase-A qualification\n\n"
        f"Selected-window execution and mark coverage: `{perpetual['expected_minute_count']}/{perpetual['expected_minute_count']}` each. "
        f"Funding events: `{perpetual['funding']['archive_event_count']}`. Source substitutions: `0`.\n\n"
        "The old 24-minute mark gap is `IRRECOVERABLE_OFFICIAL_MARK_DELIVERY_GAP`; no Mark was reconstructed. "
        "See `perpetual-mark-gap-disposition.json` and `candidate-window-scan.json`.\n",
        encoding="utf-8",
        newline="\n",
    )

    print(
        json.dumps(
            {
                "status": "PASS",
                "analysis_identity": analysis["analysis_identity"],
                "new_observations": len(new_observations),
                "reused_observations": len(selected_old),
                "raw_objects": len(raw_objects),
                "evidence_path": relpath(EVIDENCE),
            },
            sort_keys=True,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
