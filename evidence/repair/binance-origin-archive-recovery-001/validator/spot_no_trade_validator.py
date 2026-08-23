#!/usr/bin/env python3
"""Validate Binance-origin aggTrade continuity without inventing market state."""

from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
        for line_number, raw_line in enumerate(stream, 1):
            line = raw_line.rstrip("\n")
            if not line:
                errors.append(f"line {line_number}: empty capture marker")
                continue
            try:
                received_time, payload_text = line.split(" ", 1)
                envelope = json.loads(payload_text)
                data = envelope["data"]
                if envelope.get("stream") != "btcusdt@aggTrade":
                    raise ValueError("stream mismatch")
                if data.get("e") != "aggTrade" or data.get("s") != "BTCUSDT":
                    raise ValueError("event type or symbol mismatch")
                values = {name: data[name] for name in ("a", "f", "l", "T", "p", "q")}
                if not all(isinstance(values[name], int) for name in ("a", "f", "l", "T")):
                    raise TypeError("non-integer identity or timestamp")
                if values["f"] > values["l"]:
                    raise ValueError("underlying trade range inverted")
                Decimal(values["p"])
                Decimal(values["q"])
                rows.append(
                    {
                        "capture_order": line_number,
                        "provider_receive_timestamp": received_time,
                        "aggregate_trade_id": values["a"],
                        "first_trade_id": values["f"],
                        "last_trade_id": values["l"],
                        "trade_timestamp_ms": values["T"],
                        "price_text": values["p"],
                        "quantity_text": values["q"],
                    }
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError, InvalidOperation) as exc:
                errors.append(f"line {line_number}: {type(exc).__name__}: {exc}")
    return rows, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--minute-start-ms", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    rows, errors = parse(args.archive)
    ordered = sorted(rows, key=lambda row: row["capture_order"])
    aggregate_counts = Counter(row["aggregate_trade_id"] for row in ordered)
    duplicate_ids = sorted(aggregate_id for aggregate_id, count in aggregate_counts.items() if count > 1)
    aggregate_gaps: list[dict[str, int]] = []
    underlying_gaps: list[dict[str, int]] = []
    overlaps: list[dict[str, int]] = []
    for previous, current in zip(ordered, ordered[1:]):
        if current["aggregate_trade_id"] != previous["aggregate_trade_id"] + 1:
            aggregate_gaps.append(
                {
                    "previous": previous["aggregate_trade_id"],
                    "current": current["aggregate_trade_id"],
                }
            )
        expected = previous["last_trade_id"] + 1
        if current["first_trade_id"] > expected:
            underlying_gaps.append(
                {
                    "previous_last": previous["last_trade_id"],
                    "current_first": current["first_trade_id"],
                }
            )
        if current["first_trade_id"] <= previous["last_trade_id"]:
            overlaps.append(
                {
                    "previous_last": previous["last_trade_id"],
                    "current_first": current["first_trade_id"],
                }
            )

    start = args.minute_start_ms
    end = start + 60_000
    before = [row for row in ordered if row["trade_timestamp_ms"] < start]
    inside = [row for row in ordered if start <= row["trade_timestamp_ms"] < end]
    after = [row for row in ordered if row["trade_timestamp_ms"] >= end]
    boundary_before = before[-1] if before else None
    boundary_after = after[0] if after else None
    archive_continuity_valid = not (errors or duplicate_ids or aggregate_gaps or underlying_gaps or overlaps)
    if inside:
        classification = "NOT_VERIFIED_NO_TRADE_EVENTS_PRESENT"
    elif boundary_before is None or boundary_after is None or not archive_continuity_valid:
        classification = "INSUFFICIENT_ARCHIVE_CONTINUITY"
    elif (
        boundary_after["aggregate_trade_id"] == boundary_before["aggregate_trade_id"] + 1
        and boundary_after["first_trade_id"] == boundary_before["last_trade_id"] + 1
    ):
        classification = "ARCHIVE_EVENT_CONTINUITY_SUPPORTS_NO_EVENT_ONLY"
    else:
        classification = "INSUFFICIENT_ARCHIVE_CONTINUITY"

    payload = {
        "contract": "BINANCE_ORIGIN_SPOT_AGGTRADE_CONTINUITY_VALIDATOR_V1",
        "archive": {
            "path": str(args.archive),
            "size_bytes": args.archive.stat().st_size,
            "sha256": sha256(args.archive),
            "record_classification": "ARCHIVED_BINANCE_ORIGIN_EVENT",
            "provider_normalized_values_used": False,
        },
        "arithmetic": "Decimal parse validation only; no float calculations",
        "event_order": "preserved provider capture order",
        "minute": {
            "start_ms": start,
            "start_utc": utc(start),
            "end_ms": end,
            "end_utc": utc(end),
        },
        "row_count": len(ordered),
        "events_inside_minute_count": len(inside),
        "first_inside_event": inside[0] if inside else None,
        "last_inside_event": inside[-1] if inside else None,
        "boundary_before": boundary_before,
        "boundary_after": boundary_after,
        "integrity": {
            "parse_errors": errors,
            "duplicate_aggregate_ids": duplicate_ids,
            "aggregate_id_gaps": aggregate_gaps,
            "underlying_trade_id_gaps": underlying_gaps,
            "underlying_trade_id_overlaps": overlaps,
            "archive_continuity_valid": archive_continuity_valid,
        },
        "classification": classification,
        "full_verified_no_trade_claim": False,
        "reason": (
            "Real Binance-origin aggTrade events are present in the minute; a no-trade classification is rejected."
            if inside
            else "This validator supplies only the event-archive portion; REST and Daily absence remain separate mandatory gates."
        ),
        "synthetic_ohlcv_created": False,
        "status": "PASS" if classification == "NOT_VERIFIED_NO_TRADE_EVENTS_PRESENT" and archive_continuity_valid else "REVIEW",
    }
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"classification": classification, "events": len(inside), "status": payload["status"]}))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
