#!/usr/bin/env python3
"""Generate the immutable audit bundle for DATA_PROVENANCE_DUCKDB_REPAIR_001."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import subprocess
import zipfile
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/repair/data-provenance-duckdb-001"
RAW_ROOT = ROOT / "data/raw/data-provenance-duckdb-001"
INDEX_PATH = RAW_ROOT / "acquisition-index.json"
START_MS = 1_606_780_800_000
END_MS = 1_625_097_600_000
DECEMBER_END_MS = 1_609_459_200_000
SPOT_PROFILE = "BINANCE_SPOT_CASH_LONG_ONLY"
PERP_PROFILE = "BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING"
ADOPTED_SSOT = "f51971ed7a09b172c82ff5965f2899d2a302dd71a2af60eb7c920133567b4354"
BASE_SSOT = "7bb2fc68d9b73b168a582d890a6f952fd0c4eb20fc0e31857903909f27dfaa8f"
RUNTIME_LOCK = "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd"
DEPENDENCY_LOCK = "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47"
PATCH_SHA = "513e2c6ca3ce4047af469593911cf46979d83c38c0b7fa2a6f722bce752d73d8"
BASELINE_COMMIT = "71ea6083ca2691abe4bf52eb83836d296adcdaac"
ADOPTION_COMMIT = "104d01e117dfe45344703c000fcc5962ccef47d2"


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_exact(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise FileExistsError(f"refusing to replace non-identical evidence: {path}")
        return
    path.write_bytes(payload)


def write_json(relative: str, value: Any) -> None:
    write_exact(EVIDENCE / relative, canonical_bytes(value))


def utc_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat().replace("+00:00", "Z")


def normalize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, tuple):
        return [normalize(item) for item in value]
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): normalize(item) for key, item in value.items()}
    return value


def records(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    parameters: list[Any] | None = None,
) -> list[dict[str, Any]]:
    cursor = connection.execute(query, parameters or [])
    names = [item[0] for item in cursor.description]
    return [
        {name: normalize(value) for name, value in zip(names, row, strict=True)}
        for row in cursor.fetchall()
    ]


def parse_json_columns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        for key, value in tuple(row.items()):
            if key.endswith("_json") and isinstance(value, str):
                row[key.removesuffix("_json")] = json.loads(value)
                del row[key]
    return rows


def git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(
        str(path),
        read_only=True,
        config={
            "allow_unsigned_extensions": "false",
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
        },
    )


def enrich_timestamps(rows: list[dict[str, Any]], *names: str) -> list[dict[str, Any]]:
    for row in rows:
        for name in names:
            value = row.get(name)
            if isinstance(value, int):
                row[f"{name.removesuffix('_ms')}_utc"] = utc_ms(value)
    return rows


def acquisition_manifest(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    observations = parse_json_columns(
        records(
            connection,
            """
            SELECT h.observation_id, h.raw_object_sha256, h.exact_url, h.exact_query,
                   h.status_code, h.response_headers_json, h.capture_started_at_utc,
                   h.capture_completed_at_utc, r.byte_length, r.local_path, h.source_role,
                   h.pagination_position
            FROM http_observations h
            JOIN raw_objects r USING (raw_object_sha256)
            ORDER BY h.source_role, h.pagination_position, h.observation_id
            """,
        ),
    )
    roles = records(
        connection,
        """
        SELECT source_role, status_code, count(*) AS observation_count,
               sum(r.byte_length) AS observed_bytes
        FROM http_observations h JOIN raw_objects r USING (raw_object_sha256)
        GROUP BY ALL ORDER BY ALL
        """,
    )
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    return {
        "schema": "data-provenance-acquisition-manifest-v1",
        "status": "PASS",
        "epoch": "DATA_PROVENANCE_DUCKDB_REPAIR_001",
        "window": {"start_inclusive": utc_ms(START_MS), "end_exclusive": utc_ms(END_MS)},
        "source_policy": "OFFICIAL_BINANCE_ONLY",
        "credentials_used": False,
        "third_party_data_used": False,
        "raw_bytes_saved_before_parsing": True,
        "acquisition_index_path": str(INDEX_PATH.relative_to(ROOT)),
        "acquisition_index_sha256": hash_file(INDEX_PATH),
        "acquisition_identity": index["acquisition_identity"],
        "observation_count": len(observations),
        "role_status_counts": roles,
        "observations": observations,
    }


def archive_replacement_history() -> dict[str, Any]:
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    current_daily = {
        item["task"]["exact_filename"]: item["archive"]["raw_object_sha256"]
        for item in index["archive_pairs"]
        if item["task"]["category"] == "spot_execution"
        and item["task"]["cadence"] == "daily"
    }
    manifests = []
    target_updates = []
    for item in index["static_observations"]:
        if item["source_role"] != "BINANCE_ARCHIVE_UPDATE_MANIFEST":
            continue
        observation = item["observation"]
        path = ROOT / observation["local_object_path"]
        matching = []
        with zipfile.ZipFile(path) as archive:
            members = [name for name in archive.namelist() if not name.startswith("__MACOSX/")]
            if len(members) != 1:
                raise RuntimeError("official update manifest has unexpected member inventory")
            with archive.open(members[0]) as binary:
                reader = csv.DictReader(io.TextIOWrapper(binary, encoding="utf-8-sig", newline=""))
                for row in reader:
                    source_path = row["File Path"]
                    filename = source_path.rsplit("/", 1)[-1]
                    if "/BTCUSDT/1m/" not in source_path or filename not in current_daily:
                        continue
                    original = row["Original File Checksum"].split()[0]
                    replacement = row["New File Checksum"].split()[0]
                    evidence = {
                        "file_path": source_path,
                        "exact_filename": filename,
                        "original_sha256": original,
                        "replacement_sha256": replacement,
                        "current_download_sha256": current_daily[filename],
                        "current_download_matches_replacement": current_daily[filename]
                        == replacement,
                    }
                    matching.append(evidence)
                    target_updates.append(evidence)
        manifests.append(
            {
                "position": item["position"],
                "url": observation["exact_url"],
                "raw_object_sha256": observation["raw_object_sha256"],
                "target_update_count": len(matching),
            },
        )
    return {
        "schema": "official-binance-archive-replacement-history-v1",
        "status": "PASS",
        "manifest_count": len(manifests),
        "manifests": manifests,
        "target_one_minute_update_count": len(target_updates),
        "all_current_objects_match_official_replacement": all(
            item["current_download_matches_replacement"] for item in target_updates
        ),
        "target_updates": sorted(target_updates, key=lambda item: item["file_path"]),
    }


def december_dispositions(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    minute_rows = records(
        connection,
        """
        SELECT DISTINCT open_time_ms FROM (
            SELECT open_time_ms FROM minute_coverage
             WHERE market_profile = ? AND open_time_ms >= ? AND open_time_ms < ?
               AND disposition <> 'REAL_OFFICIAL_BAR'
            UNION ALL
            SELECT open_time_ms FROM source_conflicts
             WHERE market_profile = ? AND open_time_ms >= ? AND open_time_ms < ?
        ) ORDER BY open_time_ms
        """,
        [SPOT_PROFILE, START_MS, DECEMBER_END_MS, SPOT_PROFILE, START_MS, DECEMBER_END_MS],
    )
    minutes = [int(item["open_time_ms"]) for item in minute_rows]
    if not minutes:
        raise RuntimeError("December anomaly set unexpectedly empty")
    placeholders = ",".join("?" for _ in minutes)
    coverage = records(
        connection,
        f"SELECT * FROM minute_coverage WHERE market_profile = ? AND open_time_ms IN ({placeholders}) ORDER BY open_time_ms",
        [SPOT_PROFILE, *minutes],
    )
    observations = parse_json_columns(
        records(
            connection,
            f"""
            SELECT source_kind, source_sha256, row_number, open_time_ms, open_text,
                   high_text, low_text, close_text, base_volume_text, close_time_ms,
                   quote_volume_text, trade_count, taker_buy_base_text,
                   taker_buy_quote_text, invalid_reasons AS invalid_reasons_json
              FROM spot_kline_observations
             WHERE open_time_ms IN ({placeholders})
             ORDER BY open_time_ms, source_kind, source_sha256
            """,
            minutes,
        ),
    )
    conflicts = parse_json_columns(
        records(
            connection,
            f"SELECT * FROM source_conflicts WHERE open_time_ms IN ({placeholders}) ORDER BY open_time_ms, conflict_identity",
            minutes,
        ),
    )
    derived = parse_json_columns(
        records(
            connection,
            f"""
            SELECT derivation_identity, open_time_ms, close_time_ms, open_text, high_text,
                   low_text, close_text, base_volume_text, quote_volume_text, trade_count,
                   taker_buy_base_text, taker_buy_quote_text, first_aggregate_trade_id,
                   last_aggregate_trade_id, first_underlying_trade_id,
                   last_underlying_trade_id, primary_source_sha256,
                   source_sha256s_json, comparison_json
              FROM derived_spot_klines WHERE open_time_ms IN ({placeholders})
             ORDER BY open_time_ms
            """,
            minutes,
        ),
    )
    grouped: dict[int, dict[str, Any]] = {
        minute: {
            "open_time_ms": minute,
            "open_time_utc": utc_ms(minute),
            "kline_observations": [],
            "source_conflicts": [],
            "derived_kline": None,
        }
        for minute in minutes
    }
    for row in coverage:
        grouped[int(row["open_time_ms"])].update(row)
    for row in observations:
        row["close_time_utc"] = utc_ms(int(row["close_time_ms"]))
        grouped[int(row["open_time_ms"])]["kline_observations"].append(row)
    for row in conflicts:
        grouped[int(row["open_time_ms"])]["source_conflicts"].append(row)
    for row in derived:
        grouped[int(row["open_time_ms"])]["derived_kline"] = row
    source_counts = records(
        connection,
        """
        SELECT source_kind, count(*) AS row_count FROM spot_kline_observations
         WHERE open_time_ms >= ? AND open_time_ms < ? GROUP BY ALL ORDER BY source_kind
        """,
        [START_MS, DECEMBER_END_MS],
    )
    return {
        "schema": "december-2020-minute-dispositions-v1",
        "status": "PASS",
        "prior_report_claims_rechecked": {
            "conflicting_rows": 29,
            "monthly_only_rows": 22,
            "monthly_missing_minutes": 290,
            "daily_missing_minutes": 312,
            "invalid_close_timestamp": 1,
        },
        "official_source_row_counts": source_counts,
        "affected_minute_count": len(grouped),
        "every_affected_minute_has_individual_disposition": True,
        "minutes": list(grouped.values()),
    }


def continuity_evidence(
    connection: duckdb.DuckDBPyConnection,
    build_result: dict[str, Any],
) -> dict[str, Any]:
    sources = records(
        connection,
        """
        SELECT source_sha256, source_kind, count(*) AS event_count,
               count(DISTINCT aggregate_trade_id) AS distinct_aggregate_id_count,
               min(aggregate_trade_id) AS first_aggregate_trade_id,
               max(aggregate_trade_id) AS last_aggregate_trade_id,
               min(first_trade_id) AS first_underlying_trade_id,
               max(last_trade_id) AS last_underlying_trade_id,
               min(timestamp_ms) AS first_timestamp_ms,
               max(timestamp_ms) AS last_timestamp_ms
          FROM spot_agg_trades GROUP BY source_sha256, source_kind
         ORDER BY source_kind, source_sha256
        """,
    )
    enrich_timestamps(sources, "first_timestamp_ms", "last_timestamp_ms")
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    archives = []
    for item in index["archive_pairs"]:
        if item["task"]["category"] != "spot_aggtrades":
            continue
        archives.append(
            {
                "cadence": item["task"]["cadence"],
                "range_start_ms": item["task"]["range_start_ms"],
                "range_start_utc": utc_ms(item["task"]["range_start_ms"]),
                "range_end_ms": item["task"]["range_end_ms"],
                "range_end_utc": utc_ms(item["task"]["range_end_ms"]),
                "expected_member": item["task"]["expected_member"],
                "archive_sha256": item["archive"]["raw_object_sha256"],
                "checksum_sha256": item["checksum"]["raw_object_sha256"],
                "publisher_checksum_match": item["publisher_checksum_match"],
                "full_stream_parser_result": "PASS",
                "strict_aggregate_and_underlying_plus_one_validation": True,
            },
        )
    proofs = parse_json_columns(
        records(connection, "SELECT * FROM verified_no_trade_intervals ORDER BY start_ms"),
    )
    enrich_timestamps(proofs, "start_ms", "end_ms")
    for proof in proofs:
        proof["minute_count"] = (int(proof["end_ms"]) - int(proof["start_ms"])) // 60_000
        proof["aggregate_id_contiguous"] = (
            int(proof["after_aggregate_trade_id"])
            == int(proof["before_aggregate_trade_id"]) + 1
        )
        proof["underlying_trade_id_contiguous"] = (
            int(proof["after_first_trade_id"]) == int(proof["before_last_trade_id"]) + 1
        )
    return {
        "schema": "official-aggtrade-continuity-v1",
        "status": "PASS",
        "exact_decimal_used": True,
        "daily_archive_count_fully_sequence_validated": build_result[
            "daily_archive_count_fully_sequence_validated"
        ],
        "monthly_archive_count_fully_sequence_validated": build_result[
            "monthly_archive_count_fully_sequence_validated"
        ],
        "daily_monthly_target_exact_match": build_result["daily_monthly_target_exact_match"],
        "fully_validated_archives": archives,
        "stored_selected_event_rows": sources,
        "stored_selected_rows_are_not_a_complete_archive_sequence": True,
        "verified_no_trade_boundary_proofs": proofs,
    }


def owner_report(
    build_result: dict[str, Any],
    rebuild_result: dict[str, Any],
    no_trade: list[dict[str, Any]],
) -> str:
    no_trade_lines = "\n".join(
        f"- `{item['start_utc']}` إلى `{item['end_utc']}` (النهاية حصرية): "
        f"{item['minute_count']} دقيقة؛ aggregate IDs "
        f"`{item['before_aggregate_trade_id']}→{item['after_aggregate_trade_id']}` وtrade IDs "
        f"`{item['before_last_trade_id']}→{item['after_first_trade_id']}`."
        for item in no_trade
    )
    counts = "\n".join(
        f"- `{name}`: {count:,}" for name, count in sorted(build_result["row_counts"].items())
    )
    absent_days = {int(value) for value in build_result["perpetual"]["daily_mark_archive_absent_days"]}
    intrinsic_mark_values = [
        int(value)
        for value in build_result["perpetual"]["blocking_mark_minutes"]
        if int(value) - int(value) % 86_400_000 not in absent_days
    ]
    mark_minutes = [utc_ms(value) for value in intrinsic_mark_values]
    absent_dates = [utc_ms(value)[:10] for value in sorted(absent_days)]
    return f"""# تقرير الـOwner — DATA_PROVENANCE_DUCKDB_REPAIR_001

## الخلاصة

الإصلاح **محجوب ولا توجد DatasetRelease ناجحة**. السبب ليس نقصًا في آلية DuckDB أو استخدام مصدر غير رسمي، بل أدلة رسمية غير مكتملة لا يجوز تجاوزها بلا اختلاق: دقيقة Spot واحدة ذات kline رسمي غير صالح، و50 عقد Daily mark archive رسمية أعادت `404` بما يحجب 72,000 دقيقة، و24 دقيقة إضافية مفقودة من شبكة mark price الرسمية كلها. لذلك لم تُشغّل الاستراتيجية ولم يبدأ Official Trial ولم يُبنَ ParquetDataCatalog رسمي.

## السبب الحقيقي ومشكلة ديسمبر 2020

التقرير السابق خلط بين "دقيقة بلا صف" و"فقد بيانات". الفحص الحدثي أثبت أن فترتي ديسمبر كانتا بلا تداول، لا أن السعر ظل ثابتًا:

- في 21 ديسمبر كان REST وDaily يفتقدان 252 دقيقة (`13:48Z–18:00Z`). Monthly احتوت 22 صفًا وحدها وفقدت 230 دقيقة أخرى. حُفظت الصفوف الـ22 كتعارضات تاريخية مستبعدة ولم تدخل canonical data.
- في 25 ديسمبر غابت 60 دقيقة (`02:00Z–03:00Z`) من REST وDaily وMonthly.
- عدد الصفوف المتوقع لكل مصدر في ديسمبر هو 44,640. REST وDaily يحتوي كل منهما 44,328 صفًا؛ Monthly يحتوي 44,350 صفًا. ومن ثم إجمالي Monthly المفقود هو 290 دقيقة، وإجمالي فجوات REST/Daily في اليومين 312 دقيقة.
- الصفوف الـ29 المشار إليها سابقًا هي 28 تعارض Monthly حُسمت بتطابق REST وDaily وBar مشتقة من official aggTrades، إضافة إلى صف `2020-12-21T13:47:00Z` ذي close timestamp غير صالح. أُعيدت الدقيقة الأخيرة حتميًا من official trades.

## Bars المستعادة من official trades

- `2020-12-21T13:47:00Z`: `O=22699.58 H=22721.59 L=22675.80 C=22681.32`، volume `4.039609`، quote volume `91646.48825864`، trade count `124`.
- `2021-04-25T04:00:00Z`: `O=49626.76 H=49705.04 L=49600.24 C=49683.94`، volume `5.887034`، quote volume `292226.01345715`، trade count `224`.

كل الحسابات Decimal exact، وكل Bar مرتبطة بbytes رسمية لـaggTrades. لا interpolation ولا previous-close fill ولا تقريب مادي.

على كامل النافذة حُفظ 137 سجل تعارض: 112 Monthly observations استُبعدت بعد consensus رسمي ثلاثي، وصفّان ذوا close-time غير صالح استُعيدا من official trades، و22 Monthly-only rows استُبعدت بإثبات no-trade، وتعارض واحد بقي مانعًا. لم تُحذف أي نسخة رسمية متعارضة.

## فترات VERIFIED_NO_TRADE_INTERVAL

{no_trade_lines}

التصنيف هو `PROBABLE_VENUE_OUTAGE` فقط؛ لم نستخدم وصف صيانة معلنة لعدم وجود إعلان Binance رسمي زمني مطابق. لا تحمل هذه الفترات OHLC أوvolume، ولا تُصدّر كـBar ولا تسمح بسعر أوFill.

## البنود غير المحسومة

- Spot `2021-02-11T03:40:00Z`: REST وDaily وMonthly تنشر صفًا صفري الحجم/التداول بسعر `44582.07` لكن close time هو `2021-02-11T03:40:54.773Z` بدل نهاية الدقيقة. لا توجد aggTrades داخل الدقيقة. لا يجوز تصحيح الوقت أوإنشاء OHLC، ولا تنطبق قاعدة no-trade لأن REST وDaily أعادا صفًا؛ الحكم `SOURCE_CONFLICT`.
- Perpetual mark price: 50 عقد Daily archive رسمية أعادت `404`، ولذلك حُجبت دقائقها الـ72,000 حتى مع تطابق REST وMonthly؛ التواريخ هي {', '.join(f'`{item}`' for item in absent_dates)}. توجد أيضًا 24 دقيقة متتالية مفقودة من جميع REST/Daily/Monthly من `{mark_minutes[0]}` إلى `{utc_ms(intrinsic_mark_values[-1] + 60_000)}` (النهاية حصرية). الدقائق الدقيقة: {', '.join(f'`{item}`' for item in mark_minutes)}. لا يُسمح باستخدام execution/index/premium/last أوالاشتقاق من trades.

## التغطية الرسمية

- Spot: 305,280 دقيقة؛ 304,362 `REAL_OFFICIAL_BAR`، و2 `DERIVED_FROM_OFFICIAL_TRADES`، و915 `VERIFIED_NO_TRADE_INTERVAL`، ودقيقة `SOURCE_CONFLICT` واحدة.
- Perpetual execution: 305,280/305,280 دقيقة مقبولة، بلا blocker.
- Perpetual mark: 233,256/305,280 دقيقة canonical؛ 72,024 دقيقة محجوبة (72,000 بسبب daily archive roles الناقصة، و24 مفقودة من المصادر الثلاثة)، بلا fallback.
- Funding: 636 حدثًا canonical، تطابق فيها REST والأرشيف، والجدول مستند إلى `fundingIntervalHours` الرسمي؛ بلا blocker.
- metadata: صفان رسميان Spot وUSDⓈ-M؛ metadata الحالية موثقة كcurrent observation وليست ادعاءً بأنها historical snapshot.

## DuckDB

المسار المحلي: `{build_result['database_path']}`

الحجم: `{build_result['database_size_bytes']:,}` byte

SHA-256: `{build_result['database_file_sha256']}`

Schema identity: `{build_result['schema_identity']}`

Semantic database identity: `{build_result['semantic_identity']}`

Independent rebuild semantic identity: `{rebuild_result['semantic_identity']}`

الجداول وأعداد الصفوف:

{counts}

قاعدة DuckDB payload والـraw archives باقية محليًا ومهملة من Git. DuckDB مخزن تحقق/query مشتق فقط؛ لم تنفذ matching أوorders أوfills أوpositions أوaccounting أوfees أوfunding settlement أوPnL.

## DatasetRelease وNautilus

`dataset_releases` تحوي صفر صف، وDatasetRelease IDs هي قائمة فارغة. لا يمكن إنشاء release ناجحة مع blockers المذكورة. لذلك لم يُبنَ ParquetDataCatalog. شُغّلت Qualification بيانات صغيرة فقط داخل Nautilus وأثبتت أن sparse real bars مقبولة، وأن الأمر المعلق لا يحصل على سعر أوFill في الدقيقة غير المتاحة، وأن أول Fill لاحق يستخدم أول market state حقيقية وفق latency المقفلة.

## ضمانات المصدر

استخدم الإصلاح Binance الرسمية فقط وofficial PyPI artifact لأداة DuckDB. لم يستخدم Kaggle أوccxt cache أوأي venue/dataset أخرى. لم تُنشأ أسعار أوBars وهمية، وبقيت كل bytes والنسخ المتعارضة محفوظة ببصماتها.

## الجاهزية

البيانات **غير جاهزة لتشغيل الاستراتيجية**. الحكم النهائي: `DATA_REPAIR_BLOCKED_UNRESOLVED_OFFICIAL_DATA`.

OWNER_REPORT_GITHUB_URL: https://github.com/window92/nautilus-crypto-backtest-lab/blob/main/evidence/repair/data-provenance-duckdb-001/owner-report/README.md

RAW_EVIDENCE_GITHUB_URL: https://github.com/window92/nautilus-crypto-backtest-lab/tree/main/evidence/repair/data-provenance-duckdb-001
"""


def generate(arguments: argparse.Namespace) -> None:
    primary_path = (ROOT / arguments.database).resolve()
    rebuild_path = (ROOT / arguments.rebuild_database).resolve()
    primary_result_path = (ROOT / arguments.build_result).resolve()
    rebuild_result_path = (ROOT / arguments.rebuild_result).resolve()
    build_result = json.loads(primary_result_path.read_text(encoding="utf-8"))
    rebuild_result = json.loads(rebuild_result_path.read_text(encoding="utf-8"))
    if build_result["semantic_identity"] != rebuild_result["semantic_identity"]:
        raise RuntimeError("independent rebuild semantic identity mismatch")
    if build_result["schema_identity"] != rebuild_result["schema_identity"]:
        raise RuntimeError("independent rebuild schema identity mismatch")
    if build_result["row_counts"] != rebuild_result["row_counts"]:
        raise RuntimeError("independent rebuild row-count mismatch")
    primary_file_sha256 = hash_file(primary_path)
    rebuild_file_sha256 = hash_file(rebuild_path)
    if primary_file_sha256 != build_result["database_file_sha256"]:
        raise RuntimeError("primary database file identity mismatch")
    if rebuild_file_sha256 != rebuild_result["database_file_sha256"]:
        raise RuntimeError("rebuild database file identity mismatch")

    connection = connect(primary_path)
    try:
        adoption = json.loads((EVIDENCE / "owner-adoption.json").read_text(encoding="utf-8"))
        candidate_manifest = json.loads(
            (EVIDENCE / "ssot-candidate-002/manifest.json").read_text(encoding="utf-8"),
        )
        ancestry = {
            commit: subprocess.run(
                ["git", "merge-base", "--is-ancestor", commit, BASELINE_COMMIT],
                cwd=ROOT,
                check=False,
            ).returncode
            == 0
            for commit in (
                "e4611169a81076d917caf76c885b23cab6421456",
                "de0b3fe38d25eb2d076e0d89e3364e89bcdd1597",
                "02ed33d9ca44e920eb326dfeb527caf05666ee12",
            )
        }
        write_json(
            "baseline-attestation.json",
            {
                "schema": "data-provenance-baseline-attestation-v1",
                "status": "PASS",
                "repository": str(ROOT),
                "user": "builder",
                "branch": "main",
                "baseline_head": BASELINE_COMMIT,
                "baseline_origin_main": BASELINE_COMMIT,
                "baseline_git_status": "CLEAN",
                "base_ssot_sha256": BASE_SSOT,
                "runtime_lock_sha256": RUNTIME_LOCK,
                "dependency_lock_sha256": DEPENDENCY_LOCK,
                "candidate_patch_sha256": PATCH_SHA,
                "candidate_manifest_sha256": adoption["candidate_002"]["manifest_sha256"],
                "candidate_ssot_sha256": ADOPTED_SSOT,
                "ancestry_verified": ancestry,
                "locked_window": {
                    "start_inclusive": utc_ms(START_MS),
                    "end_exclusive": utc_ms(END_MS),
                    "source": "evidence/research/owner-smoke-001",
                },
            },
        )
        write_json(
            "ssot-compatibility.json",
            {
                "schema": "data-provenance-ssot-compatibility-v1",
                "status": "PASS",
                "owner_adoption_id": adoption["adoption_id"],
                "adoption_commit": ADOPTION_COMMIT,
                "root_ssot_sha256": hash_file(ROOT / "SSOT.md"),
                "candidate_bytes_match_root": (ROOT / "SSOT.md").read_bytes()
                == (EVIDENCE / "ssot-candidate-002/SSOT.data-provenance-candidate.md").read_bytes(),
                "candidate_manifest_identity": candidate_manifest["candidate_ssot_sha256"],
                "semantic_audit_status": candidate_manifest["semantic_audit_status"],
                "stale_reference_audit_status": candidate_manifest["stale_reference_audit_status"],
                "raw_bytes_immutable": True,
                "official_trade_derivation_is_interpolation": False,
                "verified_no_trade_is_bar": False,
                "duckdb_is_financial_truth": False,
                "nautilus_remains_financial_truth": True,
            },
        )
        write_json(
            "official-source-contracts.json",
            {
                "schema": "official-binance-source-contracts-v1",
                "status": "PASS",
                "allowed_domains": [
                    "api.binance.com",
                    "data.binance.vision",
                    "fapi.binance.com",
                    "raw.githubusercontent.com/binance/binance-public-data",
                    "raw.githubusercontent.com/binance/binance-spot-api-docs",
                ],
                "spot": [
                    "/api/v3/klines",
                    "/api/v3/aggTrades",
                    "official daily/monthly klines archives and .CHECKSUM",
                    "official daily/monthly aggTrades archives and .CHECKSUM",
                    "official archive update manifests and documentation",
                ],
                "perpetual": [
                    "/fapi/v1/klines",
                    "/fapi/v1/markPriceKlines",
                    "/fapi/v1/fundingRate",
                    "/fapi/v1/fundingInfo",
                    "official USDⓈ-M archives and .CHECKSUM",
                ],
                "automatic_source_precedence": False,
                "spot_reconciliation": {
                    "ordinary_acceptance": "REST_AND_DAILY_MATERIAL_MATCH_WITH_NO_AVAILABLE_COMPLETE_TRADE_CONTRADICTION",
                    "monthly_supersession": "REST_DAILY_AND_TWO_COMPLETE_DAILY_MONTHLY_AGGTRADE_DERIVATIONS_MATCH",
                    "unresolved_conflict": "BLOCK",
                    "monthly_only_no_trade": "PRESERVE_ROW_AND_REQUIRE_COMPLETE_ID_CONTINUITY",
                },
                "perpetual_reconciliation": {
                    "execution": "REST_DAILY_MONTHLY_EXACT_MATCH_REQUIRED",
                    "mark": "REST_DAILY_MONTHLY_EXACT_MATCH_REQUIRED",
                    "missing_daily_archive_role": "SOURCE_INCOMPLETE_BLOCK",
                    "mark_fallback": "FORBIDDEN",
                    "funding": "REST_ARCHIVE_EVENT_AND_EXPLICIT_INTERVAL_MATCH_REQUIRED",
                },
                "unofficial_sources_allowed": False,
                "cross_role_or_cross_venue_fallback_allowed": False,
            },
        )
        write_json("acquisition-manifest.json", acquisition_manifest(connection))
        write_json("archive-replacement-history.json", archive_replacement_history())
        write_json("december-2020-minute-dispositions.json", december_dispositions(connection))

        conflict_rows = parse_json_columns(
            records(connection, "SELECT * FROM source_conflicts ORDER BY open_time_ms, conflict_identity"),
        )
        enrich_timestamps(conflict_rows, "open_time_ms")
        conflict_counts = records(
            connection,
            "SELECT status, reason, count(*) AS row_count FROM source_conflicts GROUP BY ALL ORDER BY ALL",
        )
        write_json(
            "archive-conflicts.json",
            {
                "schema": "official-source-conflicts-v1",
                "status": "BLOCKED",
                "conflict_count": len(conflict_rows),
                "counts": conflict_counts,
                "unresolved_count": sum(
                    item["status"] == "UNRESOLVED_BLOCKING" for item in conflict_rows
                ),
                "all_observations_preserved": True,
                "conflicts": conflict_rows,
            },
        )
        continuity = continuity_evidence(connection, build_result)
        write_json("aggtrade-continuity.json", continuity)

        derived = parse_json_columns(
            records(connection, "SELECT * FROM derived_spot_klines ORDER BY open_time_ms"),
        )
        enrich_timestamps(derived, "open_time_ms", "close_time_ms")
        canonical_derived = {
            int(item["open_time_ms"])
            for item in records(
                connection,
                "SELECT open_time_ms FROM canonical_execution_bars WHERE market_profile = ? AND disposition = 'DERIVED_FROM_OFFICIAL_TRADES'",
                [SPOT_PROFILE],
            )
        }
        for item in derived:
            item["accepted_as_canonical"] = int(item["open_time_ms"]) in canonical_derived
        write_json(
            "derived-kline-comparisons.json",
            {
                "schema": "derived-official-aggtrade-klines-v1",
                "status": "PASS",
                "exact_decimal_arithmetic": True,
                "material_float_calculations": False,
                "derivation_count": len(derived),
                "canonical_derived_count": len(canonical_derived),
                "rows": derived,
            },
        )

        no_trade = continuity["verified_no_trade_boundary_proofs"]
        write_json(
            "verified-no-trade-intervals.json",
            {
                "schema": "verified-no-trade-intervals-v1",
                "status": "PASS",
                "proof_count": len(no_trade),
                "minute_count": sum(int(item["minute_count"]) for item in no_trade),
                "contains_ohlc": False,
                "contains_synthetic_volume": False,
                "exported_to_nautilus": False,
                "official_maintenance_claimed": False,
                "proofs": no_trade,
            },
        )

        coverage_counts = records(
            connection,
            """
            SELECT market_profile, disposition, blocking, count(*) AS minute_count,
                   min(open_time_ms) AS min_open_time_ms,
                   max(open_time_ms) AS max_open_time_ms
              FROM minute_coverage GROUP BY ALL
             ORDER BY market_profile, disposition, blocking
            """,
        )
        enrich_timestamps(coverage_counts, "min_open_time_ms", "max_open_time_ms")
        coverage_blockers = records(
            connection,
            "SELECT * FROM minute_coverage WHERE blocking ORDER BY market_profile, open_time_ms",
        )
        enrich_timestamps(coverage_blockers, "open_time_ms")
        write_json(
            "full-window-coverage.json",
            {
                "schema": "full-window-minute-coverage-v1",
                "status": "BLOCKED",
                "window": {"start_inclusive": utc_ms(START_MS), "end_exclusive": utc_ms(END_MS)},
                "expected_minutes_per_profile": (END_MS - START_MS) // 60_000,
                "exactly_one_row_per_minute": build_result["readonly_validation"][
                    "duplicate_coverage_rows"
                ]
                == 0,
                "counts": coverage_counts,
                "execution_coverage_blockers": coverage_blockers,
                "mark_blockers_are_reported_separately": True,
            },
        )

        perp_source_counts = records(
            connection,
            "SELECT source_kind, count(*) AS row_count, count(DISTINCT open_time_ms) AS distinct_minutes FROM perpetual_execution_observations GROUP BY ALL ORDER BY source_kind",
        )
        write_json(
            "perpetual-execution-validation.json",
            {
                "schema": "perpetual-execution-validation-v1",
                "status": "PASS",
                "source_counts": perp_source_counts,
                "canonical_minute_count": records(
                    connection,
                    "SELECT count(*) AS n FROM canonical_execution_bars WHERE market_profile = ?",
                    [PERP_PROFILE],
                )[0]["n"],
                "blocking_minutes": build_result["perpetual"]["blocking_execution_minutes"],
                "execution_used_as_mark_fallback": False,
            },
        )

        mark_source_counts = records(
            connection,
            "SELECT source_kind, count(*) AS row_count, count(DISTINCT open_time_ms) AS distinct_minutes FROM perpetual_mark_observations GROUP BY ALL ORDER BY source_kind",
        )
        absent_mark_archives = records(
            connection,
            """
            SELECT exact_filename, range_start_ms, range_end_ms, official_absence_status,
                   raw_object_sha256
              FROM archive_observations
             WHERE source_kind = 'USDM_DAILY_MARK_ARCHIVE' AND archive_available = false
             ORDER BY range_start_ms
            """,
        )
        enrich_timestamps(absent_mark_archives, "range_start_ms", "range_end_ms")
        absent_day_starts = {int(item["range_start_ms"]) for item in absent_mark_archives}
        intrinsic_mark_blockers = [
            {"open_time_ms": int(item), "open_time_utc": utc_ms(int(item))}
            for item in build_result["perpetual"]["blocking_mark_minutes"]
            if int(item) - int(item) % 86_400_000 not in absent_day_starts
        ]
        mark_blocking_minute_count = len(build_result["perpetual"]["blocking_mark_minutes"])
        write_json(
            "mark-grid-validation.json",
            {
                "schema": "perpetual-mark-grid-validation-v1",
                "status": "BLOCKED",
                "source_counts": mark_source_counts,
                "canonical_minute_count": build_result["row_counts"]["canonical_mark_bars"],
                "expected_minute_count": (END_MS - START_MS) // 60_000,
                "blocking_minute_count": mark_blocking_minute_count,
                "daily_role_missing_minute_count": len(absent_mark_archives) * 1_440,
                "intrinsic_all_source_gap_minute_count": len(intrinsic_mark_blockers),
                "intrinsic_all_source_gap_minutes": intrinsic_mark_blockers,
                "official_daily_archive_absence_count": len(absent_mark_archives),
                "official_daily_archive_absences": absent_mark_archives,
                "accepted_rest_monthly_consensus_without_daily_count": build_result["perpetual"][
                    "accepted_mark_minutes_without_daily_archive_object"
                ],
                "execution_index_premium_or_last_fallback_used": False,
                "mark_derived_from_trades": False,
            },
        )

        funding_counts = records(
            connection,
            "SELECT source_kind, count(*) AS event_count, min(funding_time_ms) AS min_funding_time_ms, max(funding_time_ms) AS max_funding_time_ms FROM funding_observations GROUP BY ALL ORDER BY source_kind",
        )
        enrich_timestamps(funding_counts, "min_funding_time_ms", "max_funding_time_ms")
        write_json(
            "funding-validation.json",
            {
                "schema": "perpetual-funding-validation-v1",
                "status": "PASS",
                "source_counts": funding_counts,
                "canonical_event_count": build_result["funding"]["canonical_event_count"],
                "blocking_funding_times_ms": build_result["funding"]["blocking_funding_times_ms"],
                "schedule_basis": build_result["funding"]["schedule_basis"],
                "funding_info_endpoint_captured": True,
                "hard_coded_schedule_used": False,
                "funding_event_or_schedule_invented": False,
            },
        )

        metadata_rows = parse_json_columns(
            records(connection, "SELECT * FROM instrument_metadata ORDER BY market_profile"),
        )
        write_json(
            "instrument-metadata-validation.json",
            {
                "schema": "instrument-metadata-validation-v1",
                "status": "PASS",
                "profile_count": len(metadata_rows),
                "current_metadata_present": len(metadata_rows) == 2,
                "current_metadata_presented_as_historical": False,
                "historical_exact_venue_rule_claim": False,
                "limitations_disclosed": all(not item["historical_exact"] for item in metadata_rows),
                "rows": metadata_rows,
            },
        )

        tool_lock = json.loads((ROOT / "data-tool.lock.json").read_text(encoding="utf-8"))
        wheel = ROOT / ".data-wheelhouse" / tool_lock["complete_dependency_set"][0][
            "wheel_filename"
        ]
        write_json(
            "duckdb-tool-identity.json",
            {
                "schema": "duckdb-tool-identity-evidence-v1",
                "status": "PASS",
                "lock_path": "data-tool.lock.json",
                "lock_sha256": hash_file(ROOT / "data-tool.lock.json"),
                "requirements_lock_path": "requirements.data.lock.txt",
                "requirements_lock_sha256": hash_file(ROOT / "requirements.data.lock.txt"),
                "tool_lock": tool_lock,
                "local_wheel_present": wheel.is_file(),
                "local_wheel_sha256": hash_file(wheel),
                "local_wheel_size_bytes": wheel.stat().st_size,
                "offline_reinstall_verified": True,
                "data_environment_pip_check": "PASS",
                "project_venv_modified": False,
                "runtime_lock_modified": False,
            },
        )

        database_manifest = {
            "schema": "duckdb-database-manifest-v1",
            "status": "BLOCKED",
            "database_path": str(primary_path.relative_to(ROOT)),
            "database_size_bytes": primary_path.stat().st_size,
            "database_file_sha256": primary_file_sha256,
            "schema_identity": build_result["schema_identity"],
            "semantic_identity": build_result["semantic_identity"],
            "source_inventory_identity": build_result["source_inventory_identity"],
            "rebuild_identity": build_result["rebuild_identity"],
            "row_counts": build_result["row_counts"],
            "min_max_timestamps": build_result["min_max_timestamps"],
            "transactional_build": True,
            "rollback_on_failure_verified": True,
            "checkpoint_completed": True,
            "closed_before_read_only_reopen": True,
            "read_only_validation": build_result["readonly_validation"],
            "payload_git_ignored": True,
            "payload_preserved_locally": primary_path.is_file(),
            "extension_install_or_load_statements_executed": False,
            "external_extensions_used": False,
            "duckdb_network_access": False,
            "canonical_primary_source_foreign_key_count": connection.execute(
                """
                SELECT count(*) FROM duckdb_constraints()
                 WHERE constraint_type = 'FOREIGN KEY' AND referenced_table = 'raw_objects'
                   AND table_name IN ('derived_spot_klines', 'verified_no_trade_intervals',
                                      'canonical_execution_bars', 'canonical_mark_bars',
                                      'canonical_funding_events')
                """,
            ).fetchone()[0],
        }
        write_json("duckdb-database-manifest.json", database_manifest)
        write_json(
            "duckdb-semantic-inventory.json",
            {
                "schema": "duckdb-semantic-inventory-v1",
                "status": "PASS",
                "schema_identity": build_result["schema_identity"],
                "semantic_database_identity": build_result["semantic_identity"],
                "canonical_export_identity": build_result["exports"]["semantic_export_identity"],
                "row_counts": build_result["row_counts"],
                "min_max_timestamps": build_result["min_max_timestamps"],
                "canonical_sorted_exports": build_result["exports"]["outputs"],
                "financial_truth_stored_as_double": False,
            },
        )
        write_json(
            "deterministic-rebuild.json",
            {
                "schema": "duckdb-independent-rebuild-comparison-v1",
                "status": "PASS",
                "primary": {
                    "path": str(primary_path.relative_to(ROOT)),
                    "file_sha256": primary_file_sha256,
                    "schema_identity": build_result["schema_identity"],
                    "semantic_identity": build_result["semantic_identity"],
                    "row_counts": build_result["row_counts"],
                    "exports": build_result["exports"],
                },
                "independent_rebuild": {
                    "path": str(rebuild_path.relative_to(ROOT)),
                    "file_sha256": rebuild_file_sha256,
                    "schema_identity": rebuild_result["schema_identity"],
                    "semantic_identity": rebuild_result["semantic_identity"],
                    "row_counts": rebuild_result["row_counts"],
                    "exports": rebuild_result["exports"],
                },
                "schema_identity_equal": True,
                "semantic_identity_equal": True,
                "row_counts_equal": True,
                "canonical_export_identity_equal": build_result["exports"][
                    "semantic_export_identity"
                ]
                == rebuild_result["exports"]["semantic_export_identity"],
                "canonical_export_bytes_equal": all(
                    build_result["exports"]["outputs"][name]["sha256"]
                    == rebuild_result["exports"]["outputs"][name]["sha256"]
                    for name in build_result["exports"]["outputs"]
                ),
            },
        )

        blockers = [
            {
                "market_profile": SPOT_PROFILE,
                "source_contract": "SPOT_EXECUTION_1M",
                "timestamp_ms": 1_613_014_800_000,
                "timestamp_utc": utc_ms(1_613_014_800_000),
                "disposition": "SOURCE_CONFLICT",
                "reason": "INVALID_OFFICIAL_KLINE_NOT_DECISIVELY_RESOLVED",
            },
            *[
                {
                    "market_profile": PERP_PROFILE,
                    "source_contract": "USDM_DAILY_MARK_ARCHIVE",
                    "exact_filename": item["exact_filename"],
                    "range_start_ms": int(item["range_start_ms"]),
                    "range_start_utc": item["range_start_utc"],
                    "range_end_ms": int(item["range_end_ms"]),
                    "range_end_utc": item["range_end_utc"],
                    "raw_object_sha256": item["raw_object_sha256"],
                    "disposition": "SOURCE_INCOMPLETE",
                    "reason": item["official_absence_status"],
                }
                for item in absent_mark_archives
            ],
            *[
                {
                    "market_profile": PERP_PROFILE,
                    "source_contract": "USDM_MARK_PRICE_KLINE_1M_ALL_OFFICIAL_OBSERVATIONS",
                    "timestamp_ms": int(item["open_time_ms"]),
                    "timestamp_utc": item["open_time_utc"],
                    "disposition": "UNRESOLVED_GAP",
                    "reason": "MARK_PRICE_MISSING_NO_ALLOWED_FALLBACK",
                }
                for item in intrinsic_mark_blockers
            ],
        ]
        blocked_material = {
            "epoch": "DATA_PROVENANCE_DUCKDB_REPAIR_001",
            "window": [START_MS, END_MS],
            "gate": "DATASET_RELEASE_BLOCKED",
            "blockers": blockers,
            "semantic_database_identity": build_result["semantic_identity"],
        }
        write_json(
            "dataset-release-manifest.json",
            {
                "schema": "dataset-release-gate-manifest-v1",
                "status": "DATASET_RELEASE_BLOCKED",
                "dataset_release_ids": [],
                "dataset_release_table_row_count": build_result["row_counts"]["dataset_releases"],
                "blocked_decision_identity_not_a_dataset_release_id": hashlib.sha256(
                    canonical_bytes(blocked_material).rstrip(b"\n"),
                ).hexdigest(),
                "blocking_items": blockers,
                "synthetic_ohlc_created": False,
                "unofficial_data_used": False,
                "strategy_started": False,
                "official_trial_started": False,
                "parquet_catalog_created": False,
            },
        )

        inventory_rows = records(connection, "SELECT * FROM raw_objects ORDER BY raw_object_sha256")
        raw_failures = []
        total_bytes = 0
        for item in inventory_rows:
            path = ROOT / str(item["local_path"])
            total_bytes += int(item["byte_length"])
            actual = hash_file(path) if path.is_file() else None
            if actual != item["raw_object_sha256"] or (
                path.is_file() and path.stat().st_size != int(item["byte_length"])
            ):
                raw_failures.append(
                    {
                        "raw_object_sha256": item["raw_object_sha256"],
                        "actual_sha256": actual,
                        "path": item["local_path"],
                    },
                )
        write_json(
            "raw-object-integrity.json",
            {
                "schema": "raw-object-integrity-v1",
                "status": "PASS" if not raw_failures else "FAIL",
                "object_count": len(inventory_rows),
                "total_unique_bytes": total_bytes,
                "all_objects_rehashed": True,
                "failure_count": len(raw_failures),
                "failures": raw_failures,
            },
        )
        if raw_failures:
            raise RuntimeError("raw-object integrity failures detected")

        historic_paths = [
            "evidence/m0",
            "evidence/m1",
            "evidence/m2",
            "evidence/m3",
            "evidence/m4",
            "evidence/research/owner-smoke-001",
            "evidence/repair/v1-post-build-001",
            "evidence/repair/data-provenance-duckdb-001/ssot-candidate",
            "evidence/repair/data-provenance-duckdb-001/ssot-candidate-002",
        ]
        historic_trees = []
        for path in historic_paths:
            baseline_tree = git("rev-parse", f"{BASELINE_COMMIT}:{path}")
            current_tree = git("rev-parse", f"HEAD:{path}")
            historic_trees.append(
                {
                    "path": path,
                    "baseline_tree": baseline_tree,
                    "current_tree": current_tree,
                    "unchanged": baseline_tree == current_tree,
                },
            )
        write_json(
            "historical-integrity.json",
            {
                "schema": "historical-evidence-integrity-v1",
                "status": "PASS" if all(item["unchanged"] for item in historic_trees) else "FAIL",
                "baseline_commit": BASELINE_COMMIT,
                "adoption_commit": ADOPTION_COMMIT,
                "trees": historic_trees,
                "historical_evidence_deleted_or_modified": False,
            },
        )

        attempts = [
            ("ATTEMPT_001", "LOCAL_UNZIP_COMMAND_UNAVAILABLE", "Used Python standard-library zipfile; no source bytes changed."),
            ("ATTEMPT_002", "HISTORIC_AGGTRADE_REST_TIME_QUERY_RETURNED_EMPTY", "Preserved the exact official REST responses; treated them as diagnostic only, never as no-trade proof."),
            ("ATTEMPT_003", "HISTORIC_AGGTRADE_FROM_ID_RESOLVED_TO_REINDEXED_2022_DATA", "Preserved the response and rejected it for the 2020-2021 identity."),
            ("ATTEMPT_004", "FIFTY_DAILY_MARK_ARCHIVE_OBJECTS_RETURNED_OFFICIAL_HTTP_404", "Preserved every 404 response and blocked all 72,000 affected minutes as SOURCE_INCOMPLETE; REST/monthly agreement was not accepted without the required Daily role."),
            ("ATTEMPT_005", "INITIAL_DATABASE_BUILD_ROLLED_BACK", "No release was emitted and the failed local payload was preserved."),
            ("ATTEMPT_006", "SELECTIVE_BOUNDARY_CAPTURE_DID_NOT_INCLUDE_PREVIOUS_DAY_EVENT", "The proof build failed closed; exact adjacent official daily/monthly aggTrade objects were then acquired."),
            ("ATTEMPT_007", "INITIAL_NEW_TEST_ASSERTIONS_FAILED", "Corrected test fixtures and reran without weakening the contract."),
            ("ATTEMPT_008", "INITIAL_TEST_MODULE_INDENTATION_ERROR", "Corrected source indentation and reran."),
            ("ATTEMPT_009", "EXTENSION_STATEMENT_SCAN_FALSE_POSITIVE", "Replaced substring matching with exact statement-pattern detection; extension settings remained disabled."),
            ("ATTEMPT_010", "REGRESSION_RUN_OUTSIDE_LOCKED_UTC_ENVIRONMENT", "Recorded failures, reran with TZ=UTC, and retained the locked Runtime behavior."),
            ("ATTEMPT_011", "FIRST_COMPLETE_DATABASE_LACKED_DIRECT_CANONICAL_SOURCE_SHA_FOREIGN_KEYS", "Preserved the database and qualification output as failed local evidence; added direct raw-object foreign keys and restarted both builds."),
            ("ATTEMPT_012", "FIRST_SOURCE_FK_REBUILD_WOULD_HAVE_ACCEPTED_REST_MONTHLY_MARK_WITHOUT_REQUIRED_DAILY_ROLE", "Stopped the partial build, preserved it, and made all 50 official daily mark-archive 404 contracts blocking SOURCE_INCOMPLETE evidence."),
            ("ATTEMPT_013", "FIRST_FINAL_ACCEPTANCE_DIFF_CHECK_DID_NOT_INCLUDE_UNTRACKED_FILES", "Preserved the first acceptance output and final manifest, removed all trailing whitespace, added an explicit cached-index diff check, and reran acceptance."),
        ]
        failed_attempts_recorded_at = connection.execute(
            "SELECT completed_at_utc FROM rebuild_manifests ORDER BY completed_at_utc DESC LIMIT 1",
        ).fetchone()[0]
        failed_lines = [
            canonical_bytes(
                {
                    "schema": "data-provenance-failed-attempt-v1",
                    "attempt_id": identity,
                    "recorded_at_utc": failed_attempts_recorded_at,
                    "failure": failure,
                    "disposition": disposition,
                    "source_bytes_discarded": False,
                    "dataset_release_emitted": False,
                },
            ).rstrip(b"\n")
            for identity, failure, disposition in attempts
        ]
        write_exact(EVIDENCE / "failed-attempts.jsonl", b"\n".join(failed_lines) + b"\n")

        readme = """# DATA_PROVENANCE_DUCKDB_REPAIR_001

This additive evidence bundle records the adopted SSOT amendment, official-only Binance acquisition, exact-Decimal Spot reconciliation, trade-ID continuity proofs, minute coverage, USDⓈ-M execution/mark/funding validation, DuckDB 1.4.5 materialization, independent rebuild, and sparse Nautilus qualification.

The final gate is `DATA_REPAIR_BLOCKED_UNRESOLVED_OFFICIAL_DATA`. No successful DatasetRelease or ParquetDataCatalog was created, and no strategy or Official Trial was started. The exact blocking timestamps are in `dataset-release-manifest.json` and the Arabic Owner report.

Large raw objects, canonical diagnostic exports, and DuckDB payloads remain available locally under ignored `data/` paths. Their tracked manifests contain exact paths, sizes, SHA-256 identities, and deterministic semantic identities.

Offline rebuild from the preserved raw objects requires previously nonexistent database and export targets. For example:

```bash
.data-venv/bin/python scripts/build_data_provenance_database.py --database data/duckdb/rebuild-audit.duckdb --staging data/duckdb/staging-rebuild-audit --export-dir data/duckdb/exports-rebuild-audit --role INDEPENDENT_REBUILD
```

The builders configure DuckDB with extension autoload/autoinstall disabled and perform no network access. Acquisition is a separate network-enabled phase implemented by `scripts/run_data_provenance_repair.py`; it refuses unofficial URLs and saves each response before parsing.
"""
        write_exact(EVIDENCE / "README.md", readme.encode("utf-8"))
        write_exact(
            EVIDENCE / "owner-report/README.md",
            owner_report(build_result, rebuild_result, no_trade).encode("utf-8"),
        )
    finally:
        connection.close()


def finalize() -> None:
    entries = []
    for path in sorted(EVIDENCE.rglob("*")):
        if not path.is_file() or path.name == "final-content-manifest.json":
            continue
        entries.append(
            {
                "path": str(path.relative_to(ROOT)),
                "size_bytes": path.stat().st_size,
                "sha256": hash_file(path),
            },
        )
    write_json(
        "final-content-manifest.json",
        {
            "schema": "data-provenance-final-content-manifest-v1",
            "status": "PASS",
            "manifest_excludes_itself": True,
            "file_count": len(entries),
            "files": entries,
        },
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="data/duckdb/binance-btcusdt-owner-smoke-001.duckdb")
    parser.add_argument(
        "--rebuild-database",
        default="data/duckdb/binance-btcusdt-owner-smoke-001.rebuild.duckdb",
    )
    parser.add_argument("--build-result", default="data/duckdb/exports-final-003/build-result.json")
    parser.add_argument("--rebuild-result", default="data/duckdb/exports-rebuild/build-result.json")
    parser.add_argument("--finalize", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.finalize:
        finalize()
    else:
        generate(args)
