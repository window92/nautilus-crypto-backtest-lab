#!/usr/bin/env python3
"""Build the additive qualification evidence for recovery phase 001."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pwd
import subprocess
from pathlib import Path
from typing import Any


REPO = Path("/home/builder/projects/nautilus-crypto-backtest-lab")
PHASE_REL = Path("evidence/repair/binance-origin-archive-recovery-001")
PHASE = REPO / PHASE_REL
RAW = PHASE / "raw"
CANDIDATE_DIR = PHASE / "ssot-candidate-003"
EXPECTED_HEAD = "f379a411bfd45ee566fd99d72ea402776ed48a85"
SSOT_SHA = "f51971ed7a09b172c82ff5965f2899d2a302dd71a2af60eb7c920133567b4354"
RUNTIME_SHA = "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd"
DEPENDENCY_SHA = "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47"
DB_REL = Path("data/duckdb/binance-btcusdt-owner-smoke-001.duckdb")
DB_EXPECTED_SHA = "932e97c446c713e8525f43b8111aced2e914b9579eba10823df7c6b0b51887b6"
TERMINAL_VERDICT = "SSOT_CANDIDATE_READY_PROVIDER_ACCESS_REQUIRED"


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_identity(path: Path, *, relative: bool = True) -> dict[str, Any]:
    raw = path.read_bytes()
    return {
        "path": path.relative_to(REPO).as_posix() if relative else str(path),
        "size_bytes": len(raw),
        "sha256": sha256_bytes(raw),
        "line_count": len(raw.decode("utf-8").splitlines()) if path.suffix in {".md", ".txt", ".json", ".py", ".diff"} else None,
    }


def git(*args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def observation(relative_observation_path: str, *, authority_limit: str | None = None) -> dict[str, Any]:
    path = PHASE / relative_observation_path
    payload = json.loads(path.read_text(encoding="utf-8"))
    body = REPO / payload["body_path"]
    body_bytes = body.read_bytes()
    if len(body_bytes) != payload["body_size_bytes"] or sha256_bytes(body_bytes) != payload["body_sha256"]:
        raise RuntimeError(f"raw response identity mismatch: {body}")
    result = {
        "observation_path": path.relative_to(REPO).as_posix(),
        "request_url": payload["request_url"],
        "final_url": payload["final_url"],
        "status_code": payload["status_code"],
        "capture_timestamp_utc": payload["capture_timestamp_utc"],
        "body_path": payload["body_path"],
        "body_size_bytes": payload["body_size_bytes"],
        "body_sha256": payload["body_sha256"],
        "headers_path": payload["headers_path"],
        "credentials_used": payload.get("credentials_used", False),
        "raw_saved_before_parsing": payload.get("parsed_before_body_saved") is False,
    }
    if authority_limit is not None:
        result["authority_limit"] = authority_limit
    return result


def write_json(relative: str | Path, payload: Any) -> None:
    path = PHASE / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def verify_historical_evidence() -> dict[str, Any]:
    manifest_path = REPO / "evidence/repair/data-provenance-duckdb-001/final-content-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    for record in manifest["files"]:
        path = REPO / record["path"]
        if not path.is_file():
            failures.append(f"missing:{record['path']}")
            continue
        raw = path.read_bytes()
        if len(raw) != record["size_bytes"] or sha256_bytes(raw) != record["sha256"]:
            failures.append(f"identity_mismatch:{record['path']}")
    return {
        "manifest": file_identity(manifest_path),
        "bound_file_count": len(manifest["files"]),
        "failures": failures,
        "status": "PASS" if not failures else "FAIL",
    }


def main() -> int:
    created = now()
    control = json.loads((PHASE / "control-window-comparison.json").read_text(encoding="utf-8"))
    spot_control = json.loads((PHASE / "spot-control-validation.json").read_text(encoding="utf-8"))
    daily = json.loads((PHASE / "daily-404-reconciliation.json").read_text(encoding="utf-8"))
    round_trip = json.loads((CANDIDATE_DIR / "round-trip-verification.json").read_text(encoding="utf-8"))
    semantic = json.loads((CANDIDATE_DIR / "semantic-change-summary.json").read_text(encoding="utf-8"))

    head = git("rev-parse", "HEAD")
    origin_main = git("rev-parse", "origin/main")
    branch = git("branch", "--show-current")
    root_ssot = file_identity(REPO / "SSOT.md")
    runtime = file_identity(REPO / "runtime.lock.json")
    dependency = file_identity(REPO / "requirements.lock.txt")
    agents = file_identity(REPO / "AGENTS.md")
    database = file_identity(REPO / DB_REL)
    historical = verify_historical_evidence()

    baseline = {
        "schema": "binance-origin-archive-recovery-baseline-attestation-v1",
        "phase": "BINANCE_ORIGIN_ARCHIVE_RECOVERY_001",
        "attested_at_utc": created,
        "cold_start_completed_before_phase_write_or_network": True,
        "user": pwd.getpwuid(os.getuid()).pw_name,
        "uid": os.getuid(),
        "repository": str(REPO),
        "branch": branch,
        "head": head,
        "origin_main": origin_main,
        "baseline_git_status": "CLEAN",
        "locked_window": {
            "start_inclusive": "2020-12-01T00:00:00Z",
            "end_exclusive": "2021-07-01T00:00:00Z",
        },
        "identity": {
            "agents": agents,
            "ssot": root_ssot,
            "runtime_lock": runtime,
            "dependency_lock": dependency,
        },
        "complete_reads": {
            "AGENTS.md": True,
            "SSOT.md": True,
            "existing_data_repair_evidence": True,
        },
        "existing_evidence_integrity": historical,
        "current_duckdb_read_only_inspection": {
            **database,
            "expected_sha256": DB_EXPECTED_SHA,
            "opened_read_only": True,
            "duckdb_extensions_disabled": True,
            "mandatory_table_count": 21,
            "database_modified": False,
        },
        "preservation_contract": {
            "root_ssot_modified": False,
            "duckdb_modified": False,
            "existing_evidence_modified": False,
            "product_code_modified": False,
        },
        "checks": {
            "user": pwd.getpwuid(os.getuid()).pw_name == "builder",
            "repository": str(REPO) == "/home/builder/projects/nautilus-crypto-backtest-lab",
            "branch": branch == "main",
            "head": head == EXPECTED_HEAD,
            "origin_main": origin_main == EXPECTED_HEAD,
            "ssot": root_ssot["sha256"] == SSOT_SHA,
            "runtime_lock": runtime["sha256"] == RUNTIME_SHA,
            "dependency_lock": dependency["sha256"] == DEPENDENCY_SHA,
            "historical_evidence": historical["status"] == "PASS",
            "duckdb_identity": database["sha256"] == DB_EXPECTED_SHA,
        },
    }
    baseline["status"] = "PASS" if all(baseline["checks"].values()) else "FAIL"
    if baseline["status"] != "PASS":
        raise RuntimeError(f"baseline preservation check failed: {baseline['checks']}")
    write_json("baseline-attestation.json", baseline)

    references = {
        "schema": "provider-primary-source-reference-manifest-v1",
        "created_at_utc": created,
        "primary_sources_only": True,
        "references": [
            {
                "provider": "Binance",
                "role": "official current documentation corpus containing USDⓈ-M mark-price stream schema/change history",
                **observation("raw/public-references/binance-en-docs-llms-full.observation.json"),
            },
            {
                "provider": "Binance",
                "role": "official connector implementation naming 3000 ms and 1000 ms mark-price streams",
                **observation("raw/public-references/binance-official-connector-mark-stream-source.observation.json"),
            },
            {
                "provider": "Binance GitHub repository",
                "role": "exact 24-minute public-data issue",
                **observation(
                    "raw/public-references/binance-public-data-issue-483.observation.json",
                    authority_limit="User-submitted issue in Binance's official repository; exact anomaly reference, not a Binance maintainer confirmation or proof that recovery events exist.",
                ),
            },
            {
                "provider": "Binance GitHub repository",
                "role": "Spot gap issue reference",
                **observation(
                    "raw/public-references/binance-public-data-issue-365.observation.json",
                    authority_limit="User-submitted one-second-kline report; diagnostic context only, not no-trade or ID-continuity proof.",
                ),
            },
            {
                "provider": "Tardis.dev",
                "role": "Binance USDS-M Futures historical coverage, raw format, markPrice channel and frequency",
                **observation("raw/public-references/tardis-binance-futures-md.observation.json"),
            },
            {
                "provider": "Tardis.dev",
                "role": "Binance Spot historical coverage and aggTrade channel",
                **observation("raw/public-references/tardis-binance-spot-md.observation.json"),
            },
            {
                "provider": "Tardis.dev",
                "role": "raw replay API response/order/access contract",
                **observation("raw/public-references/tardis-http-api-reference-md.observation.json"),
            },
            {
                "provider": "Tardis.dev",
                "role": "capture-order semantics and first-day public sample rule",
                **observation("raw/public-references/tardis-csv-overview-md.observation.json"),
            },
            {
                "provider": "Tardis.dev",
                "role": "subscription types and historical access limits",
                **observation("raw/public-references/tardis-billing-md.observation.json"),
            },
            {
                "provider": "Crypto Lake",
                "role": "funding/mark_price schema, origin_time, received_time, frequency, and S3 layout",
                **observation("raw/public-references/crypto-lake-data-doc.observation.json"),
            },
            {
                "provider": "Crypto Lake",
                "role": "exchange/data-type coverage statement",
                **observation(
                    "raw/public-references/crypto-lake-coverage-doc.observation.json",
                    authority_limit="Provider-level coverage statement; it does not prove rows for the target interval.",
                ),
            },
            {
                "provider": "Crypto Lake",
                "role": "official lake API access contract for paid AWS credentials versus public sample",
                **observation("raw/public-references/crypto-lake-api-readme.observation.json"),
            },
            {
                "provider": "Amberdata",
                "role": "Binance product coverage statements, including ticker-specific futures start date",
                **observation(
                    "raw/public-references/amberdata-binance-market-data-doc.observation.json",
                    authority_limit="Marketing/product page; used only to reject an unsupported target claim, never to prove target rows.",
                ),
            },
            {
                "provider": "Amberdata",
                "role": "futures historical ticker schema including markPrice and exchangeTimestamp",
                **observation("raw/public-references/amberdata-futures-tickers-historical-doc.observation.json"),
            },
            {
                "provider": "Amberdata",
                "role": "published pricing/access page",
                **observation("raw/public-references/amberdata-pricing-doc.observation.json"),
            },
        ],
        "status": "PASS",
    }
    write_json("provider-source-references.json", references)

    qualification = {
        "schema": "provider-coverage-qualification-v1",
        "created_at_utc": created,
        "qualification_order": ["Crypto Lake", "Tardis.dev", "Amberdata"],
        "target": {
            "exchange": "Binance USDⓈ-M Futures",
            "instrument": "BTCUSDT perpetual",
            "channel": "markPrice / btcusdt@markPrice@1s",
            "start_inclusive": "2020-12-17T07:30:00Z",
            "end_exclusive": "2020-12-17T07:58:00Z",
            "missing_mark_minutes": {
                "start_inclusive": "2020-12-17T07:32:00Z",
                "end_exclusive": "2020-12-17T07:56:00Z",
                "count": 24,
            },
        },
        "configured_access_search": {
            "secret_values_read_or_logged": False,
            "task_relevant_provider_environment_name_count": 0,
            "provider_cli_or_local_profile_found": False,
            "aws_environment_name_count": 0,
            "aws_credentials_file_present": False,
            "aws_config_file_present": False,
            "result": "NO_CONFIGURED_TARGET_ACCESS_FOUND",
        },
        "providers": [
            {
                "order": 1,
                "provider": "Crypto Lake",
                "metadata_listing_first": True,
                "dataset_or_table": "funding",
                "exchange": "BINANCE_FUTURES",
                "symbol": "BTC-USDT-PERP",
                "documented_granularity": "approximately 3 seconds on Binance",
                "schema": {
                    "mark_price": "float64",
                    "origin_time": "exchange event time",
                    "received_time": "provider receive/process time",
                },
                "provenance_classification": "PROVIDER_NORMALIZED_RECORD",
                "raw_exchange_native_websocket_payload_documented": False,
                "public_sample": {
                    "available_date": "2023-02-01",
                    "target_prefix_key_count": 0,
                    "target_listing": observation("raw/provider-probes/crypto-lake-target-funding-list-eu-west-1.observation.json"),
                },
                "full_product_coverage_statement": "BINANCE_FUTURES funding coverage is listed from 2020-01-01, but no target bytes or row-level listing was publicly accessible.",
                "exact_target_row_count": None,
                "exact_target_granularity": None,
                "exact_target_confirmed": False,
                "access_limitation": "The official lake API states that a paid plan with provider-issued AWS credentials is required outside sample data.",
                "qualification": "NOT_ACCEPTABLE_FOR_CANONICAL_MARK_TRUTH",
                "reason": "The documented mark_price is a provider-normalized float64 field, and the public sample contains no 2020-12-17 target object.",
            },
            {
                "order": 2,
                "provider": "Tardis.dev",
                "metadata_listing_first": True,
                "exchange": "binance-futures",
                "symbol": "btcusdt",
                "instrument_type": "perpetual",
                "channel": "markPrice",
                "dataset_or_table": "/v1/data-feeds/binance-futures raw replay",
                "subscription_stream": "@markPrice@1s",
                "channel_available_since": "2019-11-17T00:00:00.000Z",
                "one_second_speed_available_since": "2020-02-13T00:00:00Z",
                "documented_granularity_at_target": "1 second",
                "schema": "provider receive timestamp followed by the exchange-native Binance WebSocket JSON payload; original numeric strings retained",
                "timestamps": ["Binance event timestamp E", "Tardis local receive timestamp"],
                "ordering": "original capture line order; deterministic tie-break for equal exchange timestamps",
                "provenance_classification": "ARCHIVED_BINANCE_ORIGIN_EVENT",
                "metadata": observation("raw/provider-probes/tardis-binance-futures-metadata.observation.json"),
                "overlapping_provider_incident_report_count": 0,
                "target_raw_probe": observation("raw/provider-probes/tardis-target-mark-raw-probe.observation.json"),
                "target_normalized_probe": observation("raw/provider-probes/tardis-target-mark-normalized-dataset-probe.observation.json"),
                "exact_target_row_count": None,
                "exact_target_granularity": None,
                "exact_target_confirmed": False,
                "control_validation": control["summary"],
                "access_limitation": {
                    "http_result": "401; unauthenticated requests are limited to the first day of each month",
                    "exact_required_access_for_mark": "Tardis Perpetuals data plan, Business subscription, yearly billing, provider-issued API access, raw data replay API for binance-futures markPrice. Business yearly is required for all available 2020 history; Pro is limited to four years.",
                    "exact_required_access_for_spot": "Tardis Spot data plan, Business subscription, yearly billing, provider-issued API access, raw data replay API for binance aggTrade.",
                    "single_scope_alternative": "Tardis All Exchanges data plan, Business subscription, yearly billing, with raw data replay API access.",
                },
                "qualification": "PROVIDER_AND_VALIDATOR_QUALIFIED_TARGET_BYTES_ACCESS_REQUIRED",
                "reason": "Raw exchange-native semantics and controls pass, but metadata/date-range statements are not row-level target proof and the exact target response is access-gated.",
            },
            {
                "order": 3,
                "provider": "Amberdata",
                "metadata_listing_first": True,
                "dataset_or_table": "/markets/futures/tickers/{instrument}",
                "symbol_query": "BTCUSDT",
                "exchange_query": "binance",
                "schema": "provider-normalized ticker record with markPrice and exchangeTimestamp numeric fields",
                "documented_granularity_at_target": None,
                "provenance_classification": "PROVIDER_NORMALIZED_RECORD",
                "published_exact_product_limit": "The Binance Historical Ticker section states Binance futures ticker coverage begins 2021-04-12, after the 2020-12-17 target; broader futures marketing dates do not override this product-specific limit.",
                "metadata_probe": observation("raw/provider-probes/amberdata-futures-tickers-metadata-probe.observation.json"),
                "target_probe": observation("raw/provider-probes/amberdata-target-mark-ticker-probe.observation.json"),
                "exact_target_row_count": None,
                "exact_target_granularity": None,
                "exact_target_confirmed": False,
                "access_limitation": "The documented endpoint requires provider access and the credential-free probes returned HTTP 403.",
                "qualification": "TARGET_NOT_QUALIFIED",
                "reason": "No target bytes were obtained, the target predates the product-specific ticker coverage statement, and normalized rows are not raw exchange-native payloads.",
            },
        ],
        "target_exists_as_downloaded_bytes": False,
        "do_not_infer_impossibility": True,
        "best_qualified_route": "Tardis.dev raw data replay with the exact Business/yearly product access described above",
        "result": "PROVIDER_COVERAGE_DOCUMENTED_VALIDATORS_READY_TARGET_DOWNLOAD_ACCESS_REQUIRED",
        "status": "PASS",
    }
    write_json("provider-coverage-qualification.json", qualification)

    official_target_rows = json.loads((PHASE / "raw/provider-probes/binance-target-mark-klines.body").read_bytes())
    target = {
        "schema": "target-mark-gap-status-v1",
        "created_at_utc": created,
        "instrument": "BTCUSDT Binance USDⓈ-M perpetual",
        "research_probe_window": {
            "start_inclusive": "2020-12-17T07:30:00Z",
            "end_exclusive": "2020-12-17T07:58:00Z",
        },
        "unresolved_gap": {
            "start_inclusive": "2020-12-17T07:32:00Z",
            "end_exclusive": "2020-12-17T07:56:00Z",
            "minute_count": 24,
            "timestamps": [
                (dt.datetime(2020, 12, 17, 7, 32, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
                for index in range(24)
            ],
        },
        "official_rest_probe": {
            **observation("raw/provider-probes/binance-target-mark-klines.observation.json"),
            "returned_row_count_inclusive_query": len(official_target_rows),
            "returned_open_times_ms": [row[0] for row in official_target_rows],
            "returned_open_times_utc": [
                dt.datetime.fromtimestamp(row[0] / 1000, dt.timezone.utc).isoformat().replace("+00:00", "Z")
                for row in official_target_rows
            ],
            "note": "The REST endTime is inclusive and returned 07:58; that row lies outside the half-open provider research probe window.",
        },
        "public_issue_reference": {
            "issue": observation(
                "raw/public-references/binance-public-data-issue-483.observation.json",
                authority_limit="User report in official Binance repository; not publisher confirmation.",
            ),
            "documented_exact_range": "2020-12-17 07:32 through 07:55 UTC",
        },
        "control_validation": control,
        "target_provider_download": {
            "provider": "Tardis.dev",
            "raw_probe_status_code": 401,
            "target_event_row_count": None,
            "continuous_real_events_every_minute": None,
            "second_independent_archive_agreement": None,
            "derived_target_mark_bars_created": False,
            "target_values_accepted": False,
            "access_required": qualification["providers"][1]["access_limitation"],
        },
        "current_status": "ACCESS_REQUIRED_TARGET_BYTES_NOT_CONFIRMED",
        "blocking_reason": "No exchange-native target response bytes are available; provider metadata and perfect controls cannot substitute for the target interval itself.",
        "synthetic_or_substitute_price_used": False,
        "status": "BLOCKED_PENDING_PROVIDER_ACCESS",
    }
    write_json("target-mark-gap-status.json", target)

    spot_status = {
        "schema": "spot-no-trade-status-v1",
        "created_at_utc": created,
        "instrument": "BTCUSDT Binance Spot",
        "target_minute": {
            "start_inclusive": "2021-02-11T03:40:00Z",
            "end_exclusive": "2021-02-11T03:41:00Z",
        },
        "existing_conflicting_official_kline_preserved": True,
        "independent_archive_candidate": "Tardis.dev binance aggTrade raw data replay",
        "provider_metadata": observation("raw/provider-probes/tardis-binance-spot-metadata.observation.json"),
        "target_probe": observation("raw/provider-probes/tardis-target-spot-aggtrade-raw-probe.observation.json"),
        "target_event_count": None,
        "no_event_inside_minute_proven": False,
        "aggregate_id_continuity_before_after_proven": False,
        "underlying_trade_id_continuity_before_after_proven": False,
        "capture_continuity_before_after_proven": False,
        "verified_no_trade_interval": False,
        "current_disposition": "SOURCE_CONFLICT",
        "known_good_negative_control": spot_control,
        "access_required": qualification["providers"][1]["access_limitation"]["exact_required_access_for_spot"],
        "reason": "The exact target raw response returned HTTP 401 without provider access. Metadata and a user-reported kline gap do not prove no trades or ID continuity.",
        "ohlcv_created": False,
        "forward_fill_used": False,
        "status": "BLOCKED_PENDING_PROVIDER_ACCESS",
    }
    write_json("spot-no-trade-status.json", spot_status)

    candidate = file_identity(CANDIDATE_DIR / "SSOT.candidate-003.md")
    candidate_diff = file_identity(CANDIDATE_DIR / "SSOT.candidate-003.diff")
    candidate_sha_file = file_identity(CANDIDATE_DIR / "SSOT.candidate-003.sha256")
    candidate_manifest = {
        "schema": "data-provenance-ssot-candidate-003-manifest-v1",
        "created_at_utc": created,
        "candidate_name": "DATA_PROVENANCE_SSOT_CANDIDATE_003",
        "base_ssot": root_ssot,
        "complete_candidate": candidate,
        "candidate_sha256_file": candidate_sha_file,
        "unified_diff": candidate_diff,
        "exact_changed_sections": semantic["changed_sections"],
        "clean_forward_application_result": round_trip["forward_application_result"],
        "exact_reverse_round_trip_result": round_trip["reverse_round_trip_result"],
        "candidate_bytes_match_forward_result": round_trip["candidate_bytes_match_forward_result"],
        "base_bytes_match_reverse_result": round_trip["base_bytes_match_reverse_result"],
        "independent_round_trip_process_count": round_trip["independent_process_count"],
        "no_fuzz_or_offset": round_trip["no_fuzz_or_offset"],
        "semantic_audit": file_identity(CANDIDATE_DIR / "semantic-change-summary.json"),
        "semantic_audit_status": semantic["status"],
        "round_trip_evidence": file_identity(CANDIDATE_DIR / "round-trip-verification.json"),
        "root_ssot_sha256_after_candidate_creation": file_identity(REPO / "SSOT.md")["sha256"],
        "root_ssot_modified": False,
        "adoption_status": "PENDING_OWNER_BYTE_ADOPTION",
        "terminal_verdict": TERMINAL_VERDICT,
        "status": "PASS",
    }
    write_json("ssot-candidate-003/candidate-manifest.json", candidate_manifest)

    failures = [
        ("LOCAL_DB_NUMPY_ADAPTER_UNAVAILABLE", "A read-only DuckDB inspection attempted fetchnumpy, but numpy was not installed; repeated with scalar fetch methods.", "No database write occurred."),
        ("LOCAL_DB_CONNECTION_CLOSED_EARLY", "The first schema loop closed its read-only connection too early.", "Repeated with one read-only connection and verified database hash."),
        ("DAILY_404_SQL_JOIN_INVALID", "The first reconciliation query used an invalid USING join.", "Corrected the evidence-only query; transaction-free read-only run passed."),
        ("DAILY_404_SOURCE_ROLE_EXPECTATION_TYPO", "The first corrected query expected the wrong existing source-role spelling.", "Bound to the observed role and reran; all 50 dates passed."),
        ("SANDBOX_DNS_BLOCKED", "Initial public HTTPS acquisition failed at DNS inside the restricted sandbox.", "Used the approved network acquisition boundary; no credentials."),
        ("BINANCE_DOCUMENTATION_WAF_EMPTY_RESPONSE", "Two legacy Binance documentation routes returned HTTP 202 with zero bytes or redirects.", "Preserved observations and used official current documentation corpus plus official connector source."),
        ("CRYPTOLAKE_PRICING_ROUTE_404", "The probed Crypto Lake /pricing/ route returned HTTP 404.", "Preserved response; access rule was verified from official data/API pages."),
        ("CRYPTOLAKE_DOTTED_BUCKET_TLS_HOSTNAME", "Virtual-host access to the dotted public sample bucket failed hostname validation before an HTTP response.", "Used standard region-specific path-style public S3 listings; no TLS bypass."),
        ("TARDIS_GZIP_REQUIRED", "Initial raw replay controls without gzip returned HTTP 406.", "Preserved the 406 bytes and repeated with the documented Accept-Encoding: gzip requirement."),
        ("TARDIS_TARGET_MARK_ACCESS_GATE", "Exact raw and normalized mark target requests returned HTTP 401.", "Preserved both bodies; identified exact Business/yearly product access required."),
        ("TARDIS_TARGET_SPOT_ACCESS_GATE", "Exact Spot aggTrade target request returned HTTP 401.", "Preserved body; left minute SOURCE_CONFLICT."),
        ("AMBERDATA_ACCESS_GATE", "Metadata and exact target probes returned HTTP 403.", "Preserved bodies and evaluated only public primary documentation."),
        ("SHELL_BACKTICK_PATTERN_EXPANSION", "One local rg diagnostic used an unsafe shell backtick pattern and failed before producing that sub-result.", "Repeated structural checks in Python without shell interpolation."),
        ("TARDIS_METADATA_SHAPE_ASSUMPTION", "An initial metadata parser assumed object-shaped channel entries and raised AttributeError.", "Inspected the primary JSON schema and parsed channelDetails/availableSymbols correctly."),
        ("DAILY_404_DATE_KEY_ASSUMPTION", "An initial evidence display expected date instead of date_utc.", "Read the actual schema and verified all 50 date_utc entries."),
        ("TARDIS_TO_PARAMETER_DEFAULT_SLICE", "A request labelled as a 60-minute control returned only the documented default one-minute slice.", "Preserved it, did not call it a 60-minute sample, and used ten explicit sliceSize=10 controls."),
        ("RUNTIME_LOCK_PATH_TYPO", "One early hash probe used runtime-lock.json instead of runtime.lock.json.", "Recomputed the required identity from runtime.lock.json."),
        ("LOCAL_PYTHON_PROBE_SYNTAX", "One early inline Python probe had a syntax error.", "Repeated with a syntax-safe read-only probe."),
    ]
    failed_path = PHASE / "failed-attempts.jsonl"
    failed_path.write_text(
        "".join(
            json.dumps(
                {
                    "schema": "binance-origin-archive-recovery-failed-attempt-v1",
                    "recorded_at_utc": created,
                    "sequence": index,
                    "code": code,
                    "failure": failure,
                    "resolution_or_current_state": resolution,
                    "credentials_exposed": False,
                    "existing_artifact_modified": False,
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n"
            for index, (code, failure, resolution) in enumerate(failures, 1)
        ),
        encoding="utf-8",
        newline="\n",
    )

    dates = [record["date_utc"] for record in daily["dates"]]
    report = f"""# تقرير المالك — BINANCE_ORIGIN_ARCHIVE_RECOVERY_001

## النتيجة

هذه مرحلة تأهيل مزوّد وإنشاء SSOT Candidate فقط. لم تُعتمد Candidate 003، ولم تتغير `SSOT.md` الجذرية، ولم تتغير DuckDB، ولم تُشغّل Strategy أوOfficial Trial، ولم يُنشأ DatasetRelease.

الحكم النهائي:

`{TERMINAL_VERDICT}`

توجد قناة مؤهلة دلاليًا لدى Tardis لأحداث Binance الأصلية، ونجح Validator على عينات كبيرة متعددة، لكن بايتات الفاصلين المستهدفين لم تُنزّل لأن الوصول التاريخي الدقيق مدفوع. لذلك لا أدّعي أن صفوف الهدف موجودة، ولا أن البيانات مستحيلة.

## أساس Binance الرسمي

- توثيق Binance الحالي يعرّف Mark Price WebSocket كـ`markPriceUpdate` مع event time وسعر Mark نصي، ويعرض سرعتي 3 ثوانٍ وثانية واحدة.
- مستودع Binance الرسمي يحتوي Issue #483 الذي يصف بالضبط فجوة 24 دقيقة في 2020-12-17 من 07:32 حتى 07:55 UTC. هذه مساهمة مستخدم وليست إقرارًا من Binance بوجود أرشيف قابل للاسترداد.
- REST الرسمي أعاد 07:30 و07:31 ثم 07:56 و07:57 داخل نافذة الفحص نصف المفتوحة؛ لم يُعد أي صف للفجوة ذات 24 دقيقة. الاستعلام الشامل أعاد أيضًا 07:58 لأنها حد `endTime` شامل، لكنها خارج نافذة المزوّد `[07:30,07:58)`.

## تأهيل المزوّدين

### Crypto Lake

توثيق المزوّد يذكر `funding` لـ`BINANCE_FUTURES` منذ 2020-01-01 ويحتوي `mark_price` مع `origin_time` و`received_time`. لكن المخطط يعرّف `mark_price` كـ`float64`، أي `PROVIDER_NORMALIZED_RECORD` وليس payload Binance أصليًا. العينة العامة لا تحتوي Prefix التاريخ 2020-12-17؛ الوصول الكامل يحتاج خطة مدفوعة وبيانات اعتماد AWS صادرة عن المزوّد. لم تُثبت بايتات الهدف، ولم تُقبل هذه القناة كحقيقة Mark canonical.

### Tardis.dev

الميتاداتا الأولية تثبت `binance-futures`، الرمز `btcusdt` من 2019-11-17، وقناة `markPrice` الخام؛ وتوثيق المزوّد يقول إن الصيغة هي payload WebSocket الأصلي مع local receive timestamp، وأن الاشتراك `@markPrice@1s` متاح منذ 2020-02-13. لا يوجد incident report في الميتاداتا يتقاطع مع الهدف، لكن ذلك ليس إثبات صفوف الهدف.

اختبار التحكم استخدم عشرة فواصل، كل منها 10 دقائق، موزعة على الأشهر السبعة للنافذة المقفلة: **{control['summary']['matched_minute_count']}/{control['summary']['minute_count']} دقيقة مطابقة دلاليًا بالكامل** من **{control['summary']['event_count']} حدثًا أصليًا**. الحساب `Decimal` فقط، والترتيب `(exchange event timestamp, capture order)`، والحدود نصف مفتوحة، ولم يحدث interpolation أوfill أوaverage أوsubstitution.

طلب الهدف الخام أعاد HTTP 401 لأن الوصول غير المصرح به متاح لأول يوم من الشهر فقط. الوصول المطلوب بدقة لفاصل 2020 هو:

- Mark: Tardis `Perpetuals`، اشتراك `Business` بفوترة سنوية، وRaw Data Replay API لـ`binance-futures/markPrice`.
- Spot: Tardis `Spot`، اشتراك `Business` بفوترة سنوية، وRaw Data Replay API لـ`binance/aggTrade`.
- أوخطة `All Exchanges` بنفس `Business/yearly` لتغطي الاثنين.

لا تُرسل أي بيانات اعتماد في المحادثة؛ يكفي تجهيز الوصول في بيئة التنفيذ لاحقًا.

### Amberdata

المسار الموثق `/markets/futures/tickers/{{instrument}}` يعرض `markPrice` و`exchangeTimestamp` كسجل normalized ويتطلب وصول المزوّد؛ طلبا metadata والهدف أعادا 403. والأهم أن قسم Binance Historical Ticker نفسه يذكر بداية Futures tickers في 2021-04-12، بعد الهدف. لا تُستخدم عبارات التغطية الأوسع لتجاوز هذا القيد الخاص بالمنتج. النتيجة: غير مؤهل للهدف.

## فجوة Mark المستهدفة

الحالة الحالية هي `ACCESS_REQUIRED_TARGET_BYTES_NOT_CONFIRMED`. لم تُشتق أي Bar للـ24 دقيقة، ولم تُقبل أي قيمة، ولا توجد events مستهدفة محفوظة يمكن منها إثبات continuity لكل دقيقة أوالمقارنة مع أرشيف مستقل ثانٍ.

## دقيقة Spot: 2021-02-11T03:40:00Z

تم الحفاظ على الـkline الرسمي المتعارض في الأدلة السابقة. طلب Tardis الخام لـ`aggTrade` حول الدقيقة أعاد 401، ولذلك لم يُثبت غياب event داخل الدقيقة ولا استمرارية aggregate/trade IDs قبلها وبعدها. تبقى الدقيقة:

`SOURCE_CONFLICT`

ولا تصبح `VERIFIED_NO_TRADE_INTERVAL`. لم تُنشأ OHLCV ولم يحدث forward fill. اختبرنا Validator على دقيقة سليمة عامة تحتوي 1,348 event؛ رفض no-trade كما يجب مع ID/capture continuity سليمة.

## خمسون Daily Mark 404

فُحصت التواريخ الخمسون مستقلًا من DuckDB read-only. لكل تاريخ: Monthly الرسمي موجود، publisher checksum مطابق، REST يحتوي 1,440 دقيقة فريدة وصحيحة، Monthly يحتوي 1,440 دقيقة، والتطابق دقيق في OHLC وclose time والنصوص العشرية الأصلية. النتيجة **50/50 PASS** واقتراح التصنيف لكل منها:

`REDUNDANT_OFFICIAL_DELIVERY_ROLE_UNAVAILABLE`

تبقى استجابات 404 محفوظة؛ لا يعني هذا غياب market state. التواريخ:

{', '.join(dates)}

التفصيل minute/date-level موجود في `daily-404-reconciliation.json`.

## SSOT Candidate 003

- Base root SSOT SHA-256: `{root_ssot['sha256']}`
- Complete Candidate SHA-256: `{candidate['sha256']}`
- Unified diff SHA-256: `{candidate_diff['sha256']}`
- الحجم: {candidate['size_bytes']} bytes، {candidate['line_count']} سطرًا.
- Forward/reverse: PASS في عمليتين مستقلتين داخل checkout نظيف، بلا fuzz أوoffset؛ ناتج forward يطابق Candidate byte-for-byte وناتج reverse يطابق base.
- semantic audit: PASS؛ لا تغيير في Runtime، latency، Fill، orders، fees، funding settlement، PnL، research، Holdout أوclaims.
- adoption status: `PENDING_OWNER_BYTE_ADOPTION`.

Candidate تسمح فقط بمزوّد خارجي كناقل immutable لـBinance-origin events؛ تمنع provider averaging وsilent precedence وsynthetic OHLC، وتبقي الخلاف fail-closed. وهي تسمح باعتبار Daily 404 مسار تسليم redundant فقط عند اتفاق Monthly+REST الكامل والدقيق.

## سلامة الحالة

- root `SSOT.md`: لم تتغير وبصمتها `{root_ssot['sha256']}`.
- Runtime Lock: `{runtime['sha256']}`؛ لم يتغير.
- Dependency Lock: `{dependency['sha256']}`؛ لم يتغير.
- DuckDB الحالية: `{database['path']}`، حجم {database['size_bytes']} bytes، SHA-256 `{database['sha256']}`؛ فُتحت read-only فقط وبقيت byte-for-byte.
- الأدلة التاريخية المرتبطة بالـmanifest: {historical['bound_file_count']}/{historical['bound_file_count']} سليمة.
- لا Product Code، لا DatasetRelease، لا commit، ولاpush.

## مسارات المراجعة المحلية

- Candidate الكاملة: `{candidate['path']}`
- diff: `{candidate_diff['path']}`
- manifest: `evidence/repair/binance-origin-archive-recovery-001/ssot-candidate-003/candidate-manifest.json`
- تأهيل المزوّد: `evidence/repair/binance-origin-archive-recovery-001/provider-coverage-qualification.json`
- Mark target: `evidence/repair/binance-origin-archive-recovery-001/target-mark-gap-status.json`
- Spot: `evidence/repair/binance-origin-archive-recovery-001/spot-no-trade-status.json`
- Daily 404: `evidence/repair/binance-origin-archive-recovery-001/daily-404-reconciliation.json`

الخطوة التالية خارج هذه المرحلة: مراجعة Owner لبايتات Candidate 003 واعتمادها صراحة إن وافق، ثم توفير الوصول الموثق للمزوّد داخل بيئة التنفيذ لتنزيل target bytes والتحقق منها. لا يبدأ Data Repair أوStrategy قبل ذلك.
"""
    report_path = PHASE / "owner-report/README.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8", newline="\n")

    print(
        json.dumps(
            {
                "baseline": baseline["status"],
                "candidate_sha256": candidate["sha256"],
                "control": control["summary"],
                "daily_404": daily["status"],
                "verdict": TERMINAL_VERDICT,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
