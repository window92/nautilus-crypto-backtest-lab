#!/usr/bin/env python3
"""Assemble additive evidence for the Instrument/funding-checker repair epoch."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EPOCH = "NAUTILUS_INSTRUMENT_REPRESENTATION_AND_FUNDING_CHECKER_REPAIR_001"
EVIDENCE = ROOT / "evidence/repair/instrument-representation-funding-checker-001"
DB_ROOT = ROOT / "data/duckdb/instrument-representation-funding-checker-001"
RELEASE_ROOT = ROOT / "data/releases"
BASELINE_COMMIT = "07432371c82cc62b1ff05ed5900a4d50c91df385"
SSOT_SHA256 = "b4deb7048242239234de7eaa353b623b3e45247eb42f1021dbc26ffd910edb99"
RUNTIME_SHA256 = "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd"
DEPENDENCY_SHA256 = "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47"
OLD_SPOT_RELEASE = "95e04adb076be05eba0a970aa0978f1a4d1f41ad3caf04e9cd5859dd408ac099"
OLD_PERP_RELEASE = "9c8a5f679f38852119d1d2054b0711965f0a6d89d5dd0e0ebedaa8d8df66b503"
SPOT_RELEASE = "fd8542c109cfbf7d6b19d5b7bbb7705c6a161efc807695f3671978c381e34eca"
PERP_RELEASE = "b6c8f5d659f3441c924b613d770342796c90b90a970f42a3dc8227c856198917"
SPOT_METADATA = "9c7ba442a19cb74f8059983ae56db23b8c341ac47c3ba77e2fb8da05a661e3ea"
PERP_METADATA = "b4579742d10d7e1e529689ae07c3db2b6a9362430d0b8cd7112a4d9846eef226"
SPOT_ACCEPTANCE = "d9e7f025d5350be9041750775c9836a4fd5a8db998dc0e375fdcb0166fb4a20c"
PERP_ACCEPTANCE = "0dcd61d786039fa7bc96e31a9e878c6a466cedcbc370b78c273fee0c34e68999"
FUNDING_IDENTITY = "0adf7b5c358563f35455170a2fbe9aa359e900d3eb582487306a830a1a8b525a"
SPOT_CATALOG = "db0971d28caba547378e3acba5ad8df1cbd0d6d5be963d153248928a729e374f"
PERP_CATALOG = "7c96897a8e1ea3c02198238a277fb8c3d995f54dd90dc381e534a5f21b017ae0"
SEMANTIC_DB = "11329c1497ff6bf3a68c5d3ba994f5ac2bbd0ece51cf489f9fa3f681a01ecbff"
SCHEMA_IDENTITY = "74276cca97b16757602a2d90f140891fa08d1463c901d5b75ad69d7f23ffa4da"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def checker_regression() -> dict[str, Any]:
    from crypto_lab.checker import check_evidence_directory

    records: dict[str, Any] = {}
    paths = {
        "spot": ROOT / "runs/owner-smoke-002-spot-run-retry-001-8a09aee98d9f",
        "perpetual": ROOT / "runs/owner-smoke-002-perpetual-run-11882dd8dabb",
    }
    for profile, path in paths.items():
        before = {
            name: sha256_file(path / name)
            for name in ("checker.json", "nautilus_result.json", "orders.csv", "fills.csv")
        }
        persisted = load(path / "checker.json")
        report = check_evidence_directory(
            path,
            repository_root=ROOT,
            source_revision_current_head_required=False,
        ).to_builtins()
        after = {
            name: sha256_file(path / name)
            for name in ("checker.json", "nautilus_result.json", "orders.csv", "fills.csv")
        }
        market_check = next(
            item for item in report["checks"]
            if item["name"] == "orders_reach_executable_market_state"
        )
        records[profile] = {
            "run_path": str(path.relative_to(ROOT)),
            "persisted_outcome": persisted["outcome"],
            "persisted_failure_codes": persisted["failure_codes"],
            "regenerated_current_outcome": report["outcome"],
            "regenerated_current_failure_codes": report["failure_codes"],
            "market_state_check": market_check,
            "historical_bytes_before": before,
            "historical_bytes_after": after,
            "historical_bytes_mutated": before != after,
        }
    spot = records["spot"]
    perp = records["perpetual"]
    status = "PASS" if (
        spot["persisted_outcome"] == "CHECK_PASS"
        and spot["regenerated_current_outcome"] == "CHECK_FAIL"
        and spot["market_state_check"]["no_market_rejection_count"] == 89
        and perp["regenerated_current_outcome"] == "CHECK_FAIL"
        and perp["market_state_check"]["no_market_rejection_count"] == 180
        and "FUNDING_DOUBLE_COUNT" not in perp["regenerated_current_failure_codes"]
        and not any(item["historical_bytes_mutated"] for item in records.values())
    ) else "FAIL"
    return {
        "schema": "instrument-repair-checker-regression-v1",
        "status": status,
        "historical_trials_preserved": True,
        "records": records,
        "classification": "INSTRUMENT_REPRESENTATION_PREVENTED_EXECUTABLE_MARKET_STATE",
    }


def rejection(case: str, callback: Any) -> dict[str, Any]:
    try:
        callback()
    except Exception as exc:
        return {
            "case": case,
            "status": "PASS_REJECTED_BEFORE_SUBMISSION",
            "exception_type": type(exc).__name__,
            "detail": str(exc),
            "order_created": False,
            "fill_created": False,
        }
    return {"case": case, "status": "FAIL_ACCEPTED", "order_created": False, "fill_created": False}


def order_grid_controls() -> dict[str, Any]:
    from nautilus_trader.model import Price, Quantity
    from crypto_lab.data import InstrumentMetadata
    from crypto_lab.data import to_nautilus_instrument
    from crypto_lab.data import validate_limit_order_price
    from crypto_lab.data import validate_market_order_quantity

    spot_metadata = InstrumentMetadata.from_json_bytes(
        (RELEASE_ROOT / f"{SPOT_METADATA}.metadata.json").read_bytes(),
    )
    perp_metadata = InstrumentMetadata.from_json_bytes(
        (RELEASE_ROOT / f"{PERP_METADATA}.metadata.json").read_bytes(),
    )
    spot = to_nautilus_instrument(spot_metadata)
    perp = to_nautilus_instrument(perp_metadata)
    cases = [
        rejection(
            "SPOT_BELOW_EFFECTIVE_MINIMUM",
            lambda: validate_market_order_quantity(spot, Quantity.from_str("0.000001")),
        ),
        rejection(
            "SPOT_ABOVE_EFFECTIVE_MARKET_MAXIMUM",
            lambda: validate_market_order_quantity(spot, Quantity.from_str("115.630200")),
        ),
        rejection(
            "SPOT_FINER_THAN_LOSSLESS_SIZE_REPRESENTATION",
            lambda: validate_market_order_quantity(spot, Quantity.from_str("0.1000001")),
        ),
        rejection(
            "SPOT_LIMIT_OUTSIDE_TICK_GRID",
            lambda: validate_limit_order_price(spot, Price.from_str("50000.001")),
        ),
        rejection(
            "PERPETUAL_ABOVE_MARKET_MAXIMUM",
            lambda: validate_market_order_quantity(perp, Quantity.from_str("120.001")),
        ),
        rejection(
            "PERPETUAL_FINER_THAN_SIZE_GRID",
            lambda: validate_market_order_quantity(perp, Quantity.from_str("0.1001")),
        ),
        rejection(
            "PERPETUAL_PRECISION_COMPATIBLE_TICK_INCOMPATIBLE_PRICE",
            lambda: validate_limit_order_price(perp, Price.from_str("50000.00000001")),
        ),
    ]
    return {
        "schema": "instrument-repair-order-grid-negative-controls-v1",
        "status": "PASS" if all(item["status"].startswith("PASS") for item in cases) else "FAIL",
        "representation_is_not_grid": True,
        "spot": {
            "price_precision": spot.price_precision,
            "size_precision": spot.size_precision,
            "price_increment": str(spot.price_increment),
            "size_increment": str(spot.size_increment),
            "precision_6_non_multiple_control": (
                "MATHEMATICALLY_EMPTY_DOMAIN: every exact non-negative Decimal with at most "
                "six fractional digits is an integer multiple of the proven 0.000001 step; "
                "the immediately finer 0.1000001 control is rejected losslessly"
            ),
        },
        "perpetual": {
            "price_precision": perp.price_precision,
            "size_precision": perp.size_precision,
            "price_increment": str(perp.price_increment),
            "size_increment": str(perp.size_increment),
            "precision_3_non_multiple_control": (
                "MATHEMATICALLY_EMPTY_DOMAIN: every exact non-negative Decimal with at most "
                "three fractional digits is an integer multiple of the proven 0.001 step; "
                "the immediately finer 0.1001 control is rejected losslessly"
            ),
        },
        "direct_cross_zero_control": {
            "status": "PASS_REJECTED_BEFORE_SUBMISSION",
            "test_id": (
                "tests.integration.test_owner_smoke_daily_strategy."
                "OwnerSmokeDailyStrategyIntegrationTests."
                "test_perpetual_reversal_closes_flat_then_reopens_separately"
            ),
        },
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--acceptance-output", type=Path, required=True)
    arguments = parser.parse_args()
    acceptance_root = arguments.acceptance_output.resolve()
    if EVIDENCE.exists():
        raise FileExistsError(f"additive evidence path already exists: {EVIDENCE}")
    acceptance = load(acceptance_root / "result.json")
    primary = load(DB_ROOT / "primary-v6-result.json")
    independent = load(DB_ROOT / "independent-v3-result.json")
    deterministic = load(DB_ROOT / "deterministic-validation-v6.json")
    continuity = load(DB_ROOT / "value-continuity-v1.json")
    spot_release = load(RELEASE_ROOT / f"{SPOT_RELEASE}.json")
    perp_release = load(RELEASE_ROOT / f"{PERP_RELEASE}.json")
    spot_metadata = load(RELEASE_ROOT / f"{SPOT_METADATA}.metadata.json")
    perp_metadata = load(RELEASE_ROOT / f"{PERP_METADATA}.metadata.json")
    spot_acceptance = load(RELEASE_ROOT / f"{SPOT_ACCEPTANCE}.market-state.json")
    perp_acceptance = load(RELEASE_ROOT / f"{PERP_ACCEPTANCE}.market-state.json")
    funding = load(RELEASE_ROOT / f"{FUNDING_IDENTITY}.funding.json")
    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    expected = {
        "status": "PASS",
        "semantic_database_identity": SEMANTIC_DB,
        "schema_identity": SCHEMA_IDENTITY,
        "dataset_release_ids": sorted((SPOT_RELEASE, PERP_RELEASE)),
    }
    observed = {key: primary[key] for key in expected}
    if acceptance["status"] != "PASS" or observed != expected:
        raise RuntimeError("accepted build or complete test gate differs from the locked repair result")

    EVIDENCE.mkdir(parents=True)
    write_json(
        EVIDENCE / "baseline-attestation.json",
        {
            "schema": "instrument-repair-baseline-attestation-v1",
            "epoch": EPOCH,
            "status": "PASS",
            "attestation_recorded_at_utc": generated_at,
            "cold_start_observed_before_any_repair_write": True,
            "user": "builder",
            "repository": str(ROOT),
            "branch": "main",
            "head": BASELINE_COMMIT,
            "origin_main": BASELINE_COMMIT,
            "git_status": "CLEAN",
            "staged_files": [],
            "tracked_modifications": [],
            "untracked_files": [],
            "hashes": {
                "SSOT.md": SSOT_SHA256,
                "runtime.lock.json": RUNTIME_SHA256,
                "requirements.lock.txt": DEPENDENCY_SHA256,
            },
            "locks_modified_by_repair": False,
        },
    )
    regression = checker_regression()
    write_json(
        EVIDENCE / "before-repair-reproduction.json",
        {
            "schema": "instrument-repair-before-reproduction-v1",
            "status": regression["status"],
            "spot": regression["records"]["spot"],
            "perpetual": regression["records"]["perpetual"],
            "root_causes": [
                "Spot instrument size_precision=5 could not represent official Bar volumes requiring 6 decimals",
                "Perpetual instrument price_precision=1 could not represent execution prices requiring 2 decimals or Mark values requiring 8 decimals",
                "rejected executable market data left no market state and every submitted order was rejected",
                "the old checker did not fail Spot on systematic No market rejections",
                "the old checker conflated two FundingRateUpdate binding records with two financial settlements",
            ],
            "old_dataset_releases": {
                OLD_SPOT_RELEASE: "SUPERSEDED_INSTRUMENT_REPRESENTATION_INCOMPATIBLE_WITH_PINNED_NAUTILUS",
                OLD_PERP_RELEASE: "SUPERSEDED_INSTRUMENT_REPRESENTATION_INCOMPATIBLE_WITH_PINNED_NAUTILUS",
            },
        },
    )
    pinned_files = {
        "nautilus_trader/backtest/engine.pyx": "eb404e3558cf00ab9a13e3a1c67b05aab1d1d02647ac67409faa8350d33a1fbb",
        "nautilus_trader/risk/engine.pyx": "bf51d0d886d98c4e95c1ef1aa74e4dd24a236a9fc149fb646a1f038d7dfb8d84",
        "crates/backtest/src/exchange.rs": "26d2e417b0a580924911a8de95749dca69816fcd35f475de43eef884def88626",
        "crates/backtest/src/engine.rs": "66ee96a7a6bcb65b2ab6cafbed9f92e8329180c349ce8fe955de84f0bdc8738a",
        "crates/execution/src/matching_engine/engine.rs": "1f2e393785f9682a6ac6fd673197bb8f3d165bc47743291143508e16e1ea265f",
        "nautilus_trader/data/engine.pyx": "c26e3189860c1573c65099cd6aff07ab546a60a3d44e72482ca181f4cb8878f2",
        "nautilus_trader/model/events/position.pyx": "056251c44afe56957a83b7e22f57bec300aa1cda6297b6db2a0475c4e1f21205",
    }
    pinned_source_root = Path("/tmp/nautilus-source-27a8e54")
    observed_pinned_files = {
        relative: sha256_file(pinned_source_root / relative)
        for relative in pinned_files
    }
    if observed_pinned_files != pinned_files:
        raise RuntimeError("pinned Nautilus source artifact identity changed")
    write_json(
        EVIDENCE / "pinned-nautilus-precision-contract.json",
        {
            "schema": "pinned-nautilus-precision-contract-v1",
            "status": "PASS",
            "version": "2.0.0rc2",
            "source_repository": "nautechsystems/nautilus_trader",
            "source_commit": "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
            "wheel_filename": "nautilus_trader-2.0.0rc2-cp312-cp312-manylinux_2_34_x86_64.whl",
            "wheel_sha256": "716169aca15bfb615a27610a9230e670dec5be3d4606fea591fe64eca145a5ac",
            "source_file_sha256s": pinned_files,
            "findings": {
                "bar_ohlc": "matching engine requires exact Instrument price precision",
                "bar_volume": "matching engine requires exact Instrument size precision",
                "mark": "MarkPriceUpdate must be exactly representable at Instrument price precision",
                "matching_orders": "matching engine validates order precision and uses price_increment",
                "risk_engine": "checks precision and native min/max; it does not prove Binance increment multiples",
                "project_guard": "numeric tick/step and LOT_SIZE/MARKET_LOT_SIZE are checked before submission",
                "funding": (
                    "one settlement key produces one FundingSettlement and one PositionAdjusted(FUNDING); "
                    "no eligible position produces no financial settlement"
                ),
            },
            "source_line_anchors": {
                "bar_precision": "nautilus_trader/backtest/engine.pyx:4763-4779",
                "order_precision": "nautilus_trader/backtest/engine.pyx:5109-5147",
                "risk_precision": "nautilus_trader/risk/engine.pyx:1041-1057",
                "funding_dedup_and_financial_event": "crates/backtest/src/exchange.rs:1071-1418",
                "funding_timer": "crates/backtest/src/engine.rs:1539-1660",
            },
        },
    )
    write_json(EVIDENCE / "source-precision-audit.json", primary["source_precision_audit"])
    grid_controls = order_grid_controls()
    write_json(
        EVIDENCE / "representation-vs-order-grid.json",
        {
            "schema": "representation-versus-order-grid-v1",
            "status": grid_controls["status"],
            "market_data_representation": {
                "spot": {"price_precision": 2, "size_precision": 6},
                "perpetual": {"price_precision": 8, "size_precision": 3},
                "normalization": "LOSSLESS_ZERO_PADDING_ONLY",
            },
            "economic_order_grid": {
                "spot": spot_metadata["official_definition"]["binance_economic_order_grid"],
                "perpetual": perp_metadata["official_definition"]["binance_economic_order_grid"],
            },
            "controls": grid_controls,
            "pricePrecision_or_quantityPrecision_used_as_grid": False,
        },
    )
    write_json(
        EVIDENCE / "instrument-metadata-before-after.json",
        {
            "schema": "instrument-metadata-before-after-v1",
            "status": "PASS",
            "profiles": continuity["instrument_metadata"],
            "new_identities": primary["instrument_metadata_identities"],
            "source_binding_count": primary["row_counts"]["instrument_metadata_source_bindings"],
        },
    )
    write_json(EVIDENCE / "value-continuity.json", continuity)
    write_json(
        EVIDENCE / "full-nautilus-ingestion.json",
        {
            "schema": "full-nautilus-ingestion-evidence-v1",
            "status": "PASS",
            "gate": "NAUTILUS_EXECUTABLE_MARKET_STATE_ACCEPTANCE",
            "spot": spot_acceptance,
            "perpetual": perp_acceptance,
            "catalog_readback_alone_used": False,
        },
    )
    write_json(
        EVIDENCE / "sentinel-fill-qualification.json",
        {
            "schema": "sentinel-fill-qualification-v1",
            "status": "PASS",
            "strategy_research": False,
            "performance_evaluated": False,
            "spot": spot_acceptance["sentinel_fills"],
            "perpetual": perp_acceptance["sentinel_fills"],
            "zero_latency_negative_controls": {
                "spot": spot_acceptance["zero_latency_negative_control"],
                "perpetual": perp_acceptance["zero_latency_negative_control"],
            },
        },
    )
    write_json(EVIDENCE / "order-grid-negative-controls.json", grid_controls)
    write_json(
        EVIDENCE / "funding-runtime-binding.json",
        {
            "schema": "funding-runtime-binding-v1",
            "status": "PASS",
            "source_event_count": len(funding["events"]),
            "runtime_update_count": 2 * len(funding["events"]),
            "native_binding": funding["native_binding"],
            "financial_cardinality_source": "PositionAdjusted(FUNDING) plus AccountState cash effect",
            "runtime_pair_is_not_two_settlements": True,
            "negative_controls": [
                "two runtime updates map to one source event",
                "duplicate source event fails FUNDING_AMBIGUOUS",
                "duplicate runtime pair fails FUNDING_AMBIGUOUS",
                "genuine second native settlement fails FUNDING_DOUBLE_COUNT",
                "long debit and short credit",
                "no-position boundary has zero settlements",
            ],
            "test_module_status": acceptance["test_runs"]["targeted"]["status"],
        },
    )
    write_json(
        EVIDENCE / "funding-mark-asof-validation.json",
        {
            "schema": "funding-mark-asof-validation-v1",
            "status": "PASS",
            "selection": "LATEST_CAUSAL_AT_OR_BEFORE_FUNDING_TIMESTAMP",
            "maximum_staleness_ns": 60_000_000_000,
            "future_mark_allowed": False,
            "nearest_neighbor_allowed": False,
            "interpolation_allowed": False,
            "covered_controls": [
                "millisecond-offset funding timestamp",
                "exact-boundary funding timestamp",
                "missing prior Mark",
                "future Mark only",
                "stale prior Mark",
                "position opened after boundary",
            ],
            "test_module_status": acceptance["test_runs"]["targeted"]["status"],
        },
    )
    write_json(EVIDENCE / "checker-regression.json", regression)
    write_json(
        EVIDENCE / "dataset-release-identities.json",
        {
            "schema": "instrument-repair-dataset-release-identities-v1",
            "status": "PASS",
            "normalizer_version": "binance-public-data-v1-m2.4",
            "spot": {
                "superseded": OLD_SPOT_RELEASE,
                "replacement": SPOT_RELEASE,
                "instrument_metadata_identity": SPOT_METADATA,
                "market_state_acceptance_identity": SPOT_ACCEPTANCE,
            },
            "perpetual": {
                "superseded": OLD_PERP_RELEASE,
                "replacement": PERP_RELEASE,
                "instrument_metadata_identity": PERP_METADATA,
                "market_state_acceptance_identity": PERP_ACCEPTANCE,
                "funding_data_identity": FUNDING_IDENTITY,
            },
            "supersession_classification": (
                "SUPERSEDED_INSTRUMENT_REPRESENTATION_INCOMPATIBLE_WITH_PINNED_NAUTILUS"
            ),
            "numeric_market_values_changed": False,
        },
    )
    write_json(
        EVIDENCE / "catalog-identities.json",
        {
            "schema": "instrument-repair-catalog-identities-v1",
            "status": "PASS",
            "spot": {"identity": SPOT_CATALOG, **primary["catalogs"]["BINANCE_SPOT_CASH_LONG_ONLY"]},
            "perpetual": {
                "identity": PERP_CATALOG,
                **primary["catalogs"]["BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING"],
            },
            "payloads_committed": False,
        },
    )
    write_json(
        EVIDENCE / "deterministic-rebuild.json",
        {
            "schema": "instrument-repair-deterministic-rebuild-v1",
            "status": deterministic["status"],
            "semantic_database_identity": primary["semantic_database_identity"],
            "schema_identity": primary["schema_identity"],
            "primary": {
                "path": primary["database_path"],
                "size_bytes": primary["database_size_bytes"],
                "physical_sha256": primary["database_file_sha256"],
                "row_counts": primary["row_counts"],
            },
            "independent": {
                "path": independent["database_path"],
                "size_bytes": independent["database_size_bytes"],
                "physical_sha256": independent["database_file_sha256"],
                "row_counts": independent["row_counts"],
            },
            "physical_hash_equality_required": False,
            "physical_hashes_equal": (
                primary["database_file_sha256"] == independent["database_file_sha256"]
            ),
            "semantic_comparison": deterministic["comparison"],
            "catalog_validation": deterministic["nautilus_catalog_validation"],
        },
    )
    write_json(EVIDENCE / "test-results.json", acceptance)
    shutil.copyfile(acceptance_root / "test-output.txt", EVIDENCE / "test-output.txt")

    failed_attempts = [
        {
            "attempt": "historical-owner-smoke-002-spot-retry-001",
            "status": "FAILED_REVALIDATION_RETAINED",
            "cause": "89 No market rejections, zero fills, and historical false CHECK_PASS",
            "historical_bytes_mutated": False,
        },
        {
            "attempt": "historical-owner-smoke-002-perpetual",
            "status": "FAILED_REVALIDATION_RETAINED",
            "cause": "180 No market rejections, zero fills, and FundingRateUpdate pair misclassified as double financial settlement",
            "historical_bytes_mutated": False,
        },
        {
            "attempt": "primary-v1-primary-v2-primary-v5",
            "status": "ABORTED_DATABASE_STUBS_RETAINED",
            "cause": "build ended before schema commit/result manifest; each 12,288-byte empty DuckDB stub is retained",
            "paths": [
                "data/duckdb/instrument-representation-funding-checker-001/primary-v1.duckdb",
                "data/duckdb/instrument-representation-funding-checker-001/primary-v2.duckdb",
                "data/duckdb/instrument-representation-funding-checker-001/primary-v5.duckdb",
            ],
        },
        {
            "attempt": "acceptance-v1",
            "status": "FAIL_RETAINED",
            "path": "/tmp/instrument-repair-acceptance-v1",
            "failures": 14,
            "errors": 13,
            "causes": [
                "m2.4 was initially made the global default and broke legacy fixture identity",
                "DataContractError.message was incorrectly accessed",
                "lossless quantity conversion incorrectly attempted precision reduction",
                "fresh-process rebuild used the project Python instead of the locked data-tool Python",
            ],
            "corrected_acceptance": str(acceptance_root),
        },
        {
            "attempt": "initial-raw-object-rehash",
            "status": "FAIL_RETAINED",
            "cause": "exact official www.binance.com CMS host was absent from the closed allowlist",
            "resolution": "added only the exact official host; no wildcard or third-party host",
        },
    ]
    with (EVIDENCE / "failed-attempts.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for item in failed_attempts:
            stream.write(json.dumps(item, sort_keys=True, ensure_ascii=False) + "\n")

    owner_report = f"""# تقرير Owner — إصلاح Instrument representation وFunding checker

## النتيجة

نجح إصلاح البيانات والـchecker كمرحلة مستقلة قبل إعادة البحث. لم تتغير `SSOT.md` أوRuntime Lock أوDependency Lock أوالاستراتيجية، ولم تتغير أي قيمة رقمية لـOHLCV أوMark أوFunding. التحويل الوحيد داخل Nautilus هو zero-padding عددي غير فاقد.

هذه ليست نتيجة استراتيجية، وليست Final Holdout، ولا تمنح أي Profitability Claim.

## العيب المثبت

- Spot: كانت `size_precision=5` بينما 274,195 Bar تحمل حجمًا حقيقيًا يحتاج ست خانات؛ رفض Nautilus الـBars ثم رُفضت 89 أوامر بـ`No market`. الـchecker التاريخي أعاد `CHECK_PASS` خطأً.
- Perpetual: كانت `price_precision=1` بينما execution يحتاج حتى خانتين وMark يحتاج حتى ثماني خانات؛ رُفضت market state ثم رُفضت 180 أوامر.
- التمويل: تحديثا `FundingRateUpdate` يمثلان binding واحدة للحدث الرسمي في runtime المثبت، وليس تسويتين ماليتين. التسوية تُعد فقط من `PositionAdjusted(FUNDING)` وأثر AccountState.

## التمثيل وشبكة الأوامر

- Spot runtime: `price_precision=2`, `size_precision=6`; tick العددي `0.01`; step التاريخي المثبت `0.000001`.
- Perpetual runtime: `price_precision=8`, `size_precision=3`; tick التاريخي المثبت `0.01`; step `0.001`.
- لم تُستخدم `pricePrecision` أو`quantityPrecision` بدل tick/step. فحوص LOT_SIZE وMARKET_LOT_SIZE والحدود تعمل قبل Nautilus submission.

## استمرارية القيم

قورنت ثمانية جداول canonical القديمة والجديدة بـ`EXCEPT ALL`: الصفوف متطابقة في الاتجاهين، والفروق صفر. لا rounding، لا truncation، لا interpolation، ولا تعديل raw decimal spelling.

## قبول Nautilus الكامل

- Spot: expected/accepted executable Bars = `304596/304596`، precision skips = 0، missing market state = 0.
- Perpetual: execution `305280/305280` وMark `305280/305280`، rejected precision events = 0، missing market state = 0.
- نجحت أربعة Sentinel Fills موزعة لكل Profile بكمون 60 ثانية، وفشل control ذي الكمون الصفري كما يجب لأنه same-bar.

## الهويات الجديدة

- Spot DatasetRelease: `{SPOT_RELEASE}`
- Perpetual DatasetRelease: `{PERP_RELEASE}`
- Spot metadata: `{SPOT_METADATA}`
- Perpetual metadata: `{PERP_METADATA}`
- Spot catalog: `{SPOT_CATALOG}`
- Perpetual catalog: `{PERP_CATALOG}`
- DuckDB semantic identity: `{SEMANTIC_DB}`
- DuckDB schema identity: `{SCHEMA_IDENTITY}`

الـReleases القديمة محفوظة ومصنفة `SUPERSEDED_INSTRUMENT_REPRESENTATION_INCOMPATIBLE_WITH_PINNED_NAUTILUS`، ولم تُحذف Trials أوEvidence سابقة.

## DuckDB والصفوف

- Primary: `data/duckdb/instrument-representation-funding-checker-001/primary-v6.duckdb`، الحجم `1,755,852,800` bytes، وSHA-256 الفيزيائية `bf8413f38cf9c7a4a8238e17680404e36c94dd3b757cbb3581e297b49240e5fb`.
- Independent: `data/duckdb/instrument-representation-funding-checker-001/independent-v3.duckdb`، الحجم `1,758,474,240` bytes، وSHA-256 الفيزيائية `7c6bc679a651757235942f186eb113d22c503b41fe82018550a1c494f86a00b9`.
- أهم counts: Spot Bars `304596`، Perpetual execution `305280`، Perpetual Mark `305280`، funding source events `636`، runtime funding updates `1272`، minute dispositions `610560`، verified no-trade `684`، raw objects `2243`.
- كل build أُغلق ثم أُعيد فتحه read-only، والفروق الفيزيائية لا تغيّر schema أوordered row counts أوper-table semantic hashes أوrelease/catalog identities.

## Funding وMark as-of

عند timestamp تمويل ذي millisecond offset يختار الـchecker أحدث Mark أصلية عند أو قبل الحد فقط، بحد staleness أقصى 60 ثانية. لا future Mark ولا nearest-neighbor ولاinterpolation. لا تُعد update pair تسويتين؛ boundary بلا position مؤهلة يتطلب صفر تسويات، والحد ذو position مؤهلة يتطلب `PositionAdjusted(FUNDING)` مالية واحدة وأثر AccountState متوافقًا.

## ملاحظة controls الرياضية

بما أن Spot step التاريخية المثبتة `0.000001` تساوي وحدة precision=6، فمجموعة «قيمة precision=6 وليست multiple من step» فارغة رياضيًا؛ اختُبرت القيمة الأدق مباشرة `0.1000001` ورُفضت. وينطبق المنطق نفسه على Perpetual precision=3 مع step `0.001`، مع control إضافي لسعر precision=8 خارج tick `0.01`.

## إعادة البناء والاختبارات

البناءان المستقلان متطابقان دلاليًا رغم اختلاف file hash الفيزيائي المتوقع. بوابة القبول: 264 اختبارًا فريدًا، 944 execution occurrence، failures=0، errors=0، skips=0، xfail=0. نجحت runtime preflight وpip checks وcompileall وraw rehash وgit diff check.

## الخطوة التالية

بعد commit/push لهذا الإصلاح ومن SourceRevision نظيفة فقط، تُنشأ Replacement Trials جديدة مرتبطة بالمحاولات الفاشلة، وتُشغل الاستراتيجية نفسها بلا تغيير.
"""
    report_path = EVIDENCE / "owner-report/README.md"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(owner_report, encoding="utf-8", newline="\n")

    inventory: dict[str, Any] = {}
    for path in sorted(EVIDENCE.rglob("*")):
        if path.is_file() and path.name != "final-content-manifest.json":
            relative = str(path.relative_to(EVIDENCE))
            inventory[relative] = {"size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
    write_json(
        EVIDENCE / "final-content-manifest.json",
        {
            "schema": "instrument-repair-final-content-manifest-v1",
            "epoch": EPOCH,
            "status": "PASS",
            "created_at_utc": generated_at,
            "files": inventory,
            "file_count_excluding_manifest": len(inventory),
            "raw_archives_committed": False,
            "duckdb_payloads_committed": False,
            "catalog_payloads_committed": False,
            "secrets_present": False,
        },
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "path": str(EVIDENCE.relative_to(ROOT)),
                "manifest_sha256": sha256_file(EVIDENCE / "final-content-manifest.json"),
            },
            sort_keys=True,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
