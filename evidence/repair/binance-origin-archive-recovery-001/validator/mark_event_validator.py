#!/usr/bin/env python3
"""Evidence-only validator for Binance-origin markPriceUpdate archives.

The validator performs no interpolation, filling, averaging, or numeric float
conversion. It derives candidate minute OHLC from real event records and compares
them field-by-field with official Binance markPriceKlines observations.
"""

from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
import pathlib
import sys
from typing import Any


MINUTE_MS = 60_000


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_archived_events(path: pathlib.Path, start_ms: int, end_ms: int) -> tuple[list[dict[str, Any]], list[str]]:
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            line = raw_line.rstrip("\n")
            if not line:
                errors.append(f"line {line_number}: disconnect/empty marker")
                continue
            try:
                receive_timestamp, payload_text = line.split(" ", 1)
                envelope = json.loads(payload_text)
                data = envelope["data"]
                event_ms = data["E"]
                price_text = data["p"]
                price = Decimal(price_text)
            except (KeyError, ValueError, TypeError, json.JSONDecodeError, InvalidOperation) as exc:
                errors.append(f"line {line_number}: malformed event: {type(exc).__name__}")
                continue
            if envelope.get("stream") != "btcusdt@markPrice@1s":
                errors.append(f"line {line_number}: source stream mismatch")
            if data.get("e") != "markPriceUpdate" or data.get("s") != "BTCUSDT":
                errors.append(f"line {line_number}: event type/symbol mismatch")
            if not isinstance(event_ms, int):
                errors.append(f"line {line_number}: non-integer exchange timestamp")
                continue
            if start_ms <= event_ms < end_ms:
                events.append(
                    {
                        "line_number": line_number,
                        "receive_timestamp": receive_timestamp,
                        "exchange_event_timestamp_ms": event_ms,
                        "price_text": price_text,
                        "price": price,
                    }
                )
    return events, errors


def derive(events: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for event in events:
        minute = event["exchange_event_timestamp_ms"] // MINUTE_MS * MINUTE_MS
        grouped.setdefault(minute, []).append(event)
    result: dict[int, dict[str, Any]] = {}
    for minute, values in grouped.items():
        ordered = sorted(values, key=lambda row: (row["exchange_event_timestamp_ms"], row["line_number"]))
        prices = [row["price"] for row in ordered]
        result[minute] = {
            "open": ordered[0]["price_text"],
            "high": format(max(prices), "f"),
            "low": format(min(prices), "f"),
            "close": ordered[-1]["price_text"],
            "event_count": len(ordered),
            "first_exchange_event_timestamp_ms": ordered[0]["exchange_event_timestamp_ms"],
            "last_exchange_event_timestamp_ms": ordered[-1]["exchange_event_timestamp_ms"],
            "first_receive_timestamp": ordered[0]["receive_timestamp"],
            "last_receive_timestamp": ordered[-1]["receive_timestamp"],
            "first_capture_line": ordered[0]["line_number"],
            "last_capture_line": ordered[-1]["line_number"],
        }
    return result


def official_bars(path: pathlib.Path) -> dict[int, dict[str, str]]:
    payload = json.loads(path.read_bytes())
    bars: dict[int, dict[str, str]] = {}
    for row in payload:
        bars[row[0]] = {"open": row[1], "high": row[2], "low": row[3], "close": row[4]}
    return bars


def compare_window(spec: str) -> dict[str, Any]:
    fields = spec.split("|")
    if len(fields) != 5:
        raise ValueError("window must be label|archive|official|start_ms|end_ms")
    label, archive_text, official_text, start_text, end_text = fields
    archive = pathlib.Path(archive_text)
    official = pathlib.Path(official_text)
    start_ms, end_ms = int(start_text), int(end_text)
    events, parse_errors = parse_archived_events(archive, start_ms, end_ms)
    derived = derive(events)
    published = official_bars(official)
    comparisons: list[dict[str, Any]] = []
    all_match = True
    minute = start_ms
    while minute < end_ms:
        candidate = derived.get(minute)
        reference = published.get(minute)
        field_results: dict[str, bool] = {}
        if candidate is not None and reference is not None:
            for field in ("open", "high", "low", "close"):
                field_results[field] = Decimal(candidate[field]) == Decimal(reference[field])
        minute_match = bool(candidate and reference and all(field_results.values()))
        all_match = all_match and minute_match
        comparisons.append(
            {
                "open_time_ms": minute,
                "open_time_utc": utc_iso(minute),
                "candidate": candidate,
                "official_mark_price_kline": reference,
                "field_semantic_matches": field_results,
                "exact_semantic_ohlc_match": minute_match,
            }
        )
        minute += MINUTE_MS
    return {
        "label": label,
        "window": {"start_ms": start_ms, "start_utc": utc_iso(start_ms), "end_ms": end_ms, "end_utc": utc_iso(end_ms)},
        "archive": {"path": str(archive), "size_bytes": archive.stat().st_size, "sha256": sha256(archive)},
        "official_reference": {"path": str(official), "size_bytes": official.stat().st_size, "sha256": sha256(official)},
        "event_count": len(events),
        "derived_minute_count": len(derived),
        "required_minute_count": (end_ms - start_ms) // MINUTE_MS,
        "parse_errors": parse_errors,
        "continuous_real_events_each_minute": len(derived) == (end_ms - start_ms) // MINUTE_MS,
        "all_minute_ohlc_semantically_match": all_match,
        "comparisons": comparisons,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", action="append", required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    results = [compare_window(spec) for spec in args.window]
    passed = all(
        not result["parse_errors"]
        and result["continuous_real_events_each_minute"]
        and result["all_minute_ohlc_semantically_match"]
        for result in results
    )
    payload = {
        "contract": "BINANCE_ORIGIN_MARK_EVENT_CONTROL_VALIDATION_V1",
        "arithmetic": "Python Decimal exact; no float material arithmetic",
        "minute_boundaries": "half-open UTC [minute_start, minute_start + 60s) by Binance exchange event timestamp E",
        "event_order": "exchange event timestamp E, then preserved Tardis capture line order",
        "prohibited_operations": ["interpolation", "forward_fill", "averaging", "synthetic_ohlc", "execution_index_premium_last_substitution"],
        "acceptance": "100% semantic OHLC agreement for every control minute",
        "windows": results,
        "summary": {
            "window_count": len(results),
            "minute_count": sum(result["required_minute_count"] for result in results),
            "event_count": sum(result["event_count"] for result in results),
            "matched_minute_count": sum(
                comparison["exact_semantic_ohlc_match"]
                for result in results
                for comparison in result["comparisons"]
            ),
            "status": "PASS" if passed else "FAIL",
        },
    }
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(payload["summary"]))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
