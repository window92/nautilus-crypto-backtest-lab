#!/usr/bin/env python3
"""Assemble fail-closed evidence for OWNER_SMOKE_002 replacement 001."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from crypto_lab.checker import check_evidence_directory
from crypto_lab.diagnostics import derive_performance_diagnostics
from crypto_lab.hashing import canonical_sha256
from crypto_lab.reporting import PerformanceDiagnostics
from crypto_lab.research import ResearchProtocol


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/research/owner-smoke-002-replacement-001"
REPAIR_EVIDENCE = ROOT / "evidence/repair/instrument-representation-funding-checker-001"
BASELINE_COMMIT = "07432371c82cc62b1ff05ed5900a4d50c91df385"
SSOT_SHA256 = "b4deb7048242239234de7eaa353b623b3e45247eb42f1021dbc26ffd910edb99"
RUNTIME_SHA256 = "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd"
DEPENDENCY_SHA256 = "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47"
SPOT_RELEASE = "fd8542c109cfbf7d6b19d5b7bbb7705c6a161efc807695f3671978c381e34eca"
PERP_RELEASE = "b6c8f5d659f3441c924b613d770342796c90b90a970f42a3dc8227c856198917"
SPOT_CATALOG = "db0971d28caba547378e3acba5ad8df1cbd0d6d5be963d153248928a729e374f"
PERP_CATALOG = "7c96897a8e1ea3c02198238a277fb8c3d995f54dd90dc381e534a5f21b017ae0"
SPOT_METADATA = "9c7ba442a19cb74f8059983ae56db23b8c341ac47c3ba77e2fb8da05a661e3ea"
PERP_METADATA = "b4579742d10d7e1e529689ae07c3db2b6a9362430d0b8cd7112a4d9846eef226"
SEMANTIC_DB = "11329c1497ff6bf3a68c5d3ba994f5ac2bbd0ece51cf489f9fa3f681a01ecbff"
SCHEMA_DB = "74276cca97b16757602a2d90f140891fa08d1463c901d5b75ad69d7f23ffa4da"
SCORING_START_NS = 1_612_137_600_000_000_000
SCORING_END_NS = 1_627_776_000_000_000_000
TRIALS = {
    "spot": {
        "trial_id": "owner-smoke-002-replacement-001-spot-sma20-development-retry-002",
        "run_dir": "runs/owner-smoke-002-replacement-001-spot-run-retry-002-b25302d138b2",
        "release_id": SPOT_RELEASE,
        "catalog_id": SPOT_CATALOG,
        "metadata_id": SPOT_METADATA,
        "protocol_id": "4734d273510a80fffead77e81e1a96507162b6db98ac16c000966ffc63b0403b",
        "replay_id": "60a312df85e5bba027306db63ddb007e51f48996fabb168f06cd6209827a6387",
        "report_id": "c12a160c04deecdf55ea8a66f43bd861cc0de642820961570b20767e07db1e5a",
        "strategy_id": "36a8da3b30f72b20872d12f1556ee6c2b0776c61a2685a05733094970bd96fca",
    },
    "perpetual": {
        "trial_id": "owner-smoke-002-replacement-001-perpetual-sma20-development",
        "run_dir": "runs/owner-smoke-002-replacement-001-perpetual-run-1959c892b218",
        "release_id": PERP_RELEASE,
        "catalog_id": PERP_CATALOG,
        "metadata_id": PERP_METADATA,
        "protocol_id": "abcc94723ba95eb9ab36bd6acb4b62d6a22d9f881f2ee7046ba57bc1d471cdc5",
        "replay_id": "c02f6b6f0c304dbb6eed9891f43c92c371f40989d3219f6e53b2411e481f4f3a",
        "report_id": "4e3b87ff3390fdd0c1a595d0d872634ebc57fb5475c1b4d8c81a7b784e2837d1",
        "strategy_id": "6493e4e80528ea818ba6f0d9f7841d957349cc188576eea97a6d50e3b94492f9",
    },
}


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


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def copy_normalized_text(source: Path, target: Path) -> None:
    target.write_bytes(source.read_bytes().rstrip(b"\n") + b"\n")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def binding(path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def stable_repair_bindings() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name in (
        "dataset-release-identities.json",
        "catalog-identities.json",
        "value-continuity.json",
        "full-nautilus-ingestion.json",
        "funding-runtime-binding.json",
    ):
        path = REPAIR_EVIDENCE / name
        result[path.relative_to(ROOT).as_posix()] = binding(path)
    return result


def refresh_inventory() -> None:
    inventory_path = EVIDENCE / "evidence-inventory.json"
    manifest_path = EVIDENCE / "final-content-manifest.json"
    inventory = load(inventory_path)
    copy_normalized_text(EVIDENCE / "test-output.txt", EVIDENCE / "test-output.txt")
    source_entries = {
        key: value
        for key, value in inventory["source_entries"].items()
        if key
        != (REPAIR_EVIDENCE / "final-content-manifest.json").relative_to(ROOT).as_posix()
    }
    source_entries.update(stable_repair_bindings())
    generated_entries = {
        path.relative_to(EVIDENCE).as_posix(): {
            "path": path.relative_to(ROOT).as_posix(),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in EVIDENCE.rglob("*")
        if path.is_file() and path.name not in {"evidence-inventory.json", "final-content-manifest.json"}
    }
    inventory.update(
        {
            "source_entries": dict(sorted(source_entries.items())),
            "generated_entries": dict(sorted(generated_entries.items())),
            "source_inventory_identity": canonical_sha256(source_entries),
            "generated_inventory_identity": canonical_sha256(generated_entries),
        },
    )
    write_json(inventory_path, inventory)
    manifest = load(manifest_path)
    manifest_files = {
        path.relative_to(EVIDENCE).as_posix(): {
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(EVIDENCE.rglob("*"))
        if path.is_file() and path.name != "final-content-manifest.json"
    }
    manifest.update(
        {
            "files": manifest_files,
            "file_count_excluding_manifest": len(manifest_files),
        },
    )
    write_json(manifest_path, manifest)


def money(value: str) -> tuple[Decimal, str]:
    parts = value.split()
    if len(parts) != 2:
        raise RuntimeError(f"invalid native Money value: {value!r}")
    amount = Decimal(parts[0])
    if not amount.is_finite():
        raise RuntimeError("non-finite native Money value")
    return amount, parts[1]


def decimal_stat(statistics: dict[str, Any], name: str) -> str:
    value = statistics.get("stats_returns", {}).get(name)
    return str(value) if value is not None else "UNDEFINED"


def exposure(events: list[dict[str, str]]) -> tuple[int, str]:
    ordered = [item for item in events if item["row_type"] != "FINAL_NATIVE_POSITION"]
    ordered.sort(key=lambda item: (int(item["ts_event"]), int(item["event_index"])))
    cursor = SCORING_START_NS
    state = Decimal(0)
    active_ns = 0
    for item in ordered:
        timestamp = max(SCORING_START_NS, min(SCORING_END_NS, int(item["ts_event"])))
        if timestamp > cursor and state != 0:
            active_ns += timestamp - cursor
        cursor = max(cursor, timestamp)
        state = Decimal(item["signed_qty"])
    if cursor < SCORING_END_NS and state != 0:
        active_ns += SCORING_END_NS - cursor
    ratio = Decimal(active_ns) / Decimal(SCORING_END_NS - SCORING_START_NS)
    return active_ns, f"{ratio:.12f}"


def profile_view(label: str, spec: dict[str, str]) -> dict[str, Any]:
    trial_id = spec["trial_id"]
    run_dir = ROOT / spec["run_dir"]
    workflow_path = ROOT / "research/workflows" / f"{trial_id}.json"
    replay_path = ROOT / "research/replays" / f"{trial_id}.json"
    report_path = ROOT / "research/reports" / f"{trial_id}.json"
    protocol_path = ROOT / "research/protocols" / f"{spec['protocol_id']}.json"
    performance_path = ROOT / "research/performance" / f"{load(run_dir / 'status.json')['run_id']}.json"
    workflow = load(workflow_path)
    replay = load(replay_path)
    report = load(report_path)
    protocol = ResearchProtocol.from_json_bytes(protocol_path.read_bytes())
    checker_path = run_dir / "checker.json"
    checker = load(checker_path)
    regenerated = check_evidence_directory(
        run_dir,
        repository_root=ROOT,
        official_source_required=True,
        source_revision_current_head_required=False,
    ).to_builtins()
    if checker != regenerated or checker.get("outcome") != "CHECK_PASS":
        raise RuntimeError(f"{label} read-only checker is not an exact CHECK_PASS")
    performance = PerformanceDiagnostics.from_json_bytes(performance_path.read_bytes())
    if performance != derive_performance_diagnostics(run_dir=run_dir, protocol=protocol):
        raise RuntimeError(f"{label} performance diagnostics are stale")
    status = load(run_dir / "status.json")
    native = load(run_dir / "nautilus_result.json")
    statistics = load(run_dir / "native_statistics.json")
    completed = load(run_dir / "native_completed_trades.json")
    strategy = load(run_dir / "strategy_identity.json")
    dataset = load(run_dir / "dataset_release.json")
    metadata = load(run_dir / "instrument_metadata.json")
    source_revision = load(run_dir / "source_revision.json")
    orders = read_csv(run_dir / "orders.csv")
    fills = read_csv(run_dir / "fills.csv")
    positions = read_csv(run_dir / "positions.csv")
    funding = read_csv(run_dir / "funding.csv") if label == "perpetual" else []
    checks = {item["name"]: item for item in checker["checks"]}
    daily = checks["owner_smoke_sma20_daily_causality"]
    market = checks["orders_reach_executable_market_state"]
    acceptance = checks["nautilus_executable_market_state_acceptance"]["validation"]
    if (
        status != {
            "checker_outcome": "CHECK_PASS",
            "failure_codes": [],
            "run_id": status["run_id"],
            "started_run_retained": True,
            "state": "COMPLETED",
        }
        or workflow["dataset_release_id"] != spec["release_id"]
        or dataset["dataset_release_id"] != spec["release_id"]
        or strategy["strategy_identity_sha256"] != spec["strategy_id"]
        or workflow["protocol"]["protocol_id"] != spec["protocol_id"]
        or replay.get("result") != "PASS"
        or replay.get("replay_identity") != spec["replay_id"]
        or replay.get("primary_semantic_digest") != replay.get("replay_semantic_digest")
        or report.get("report_id") != spec["report_id"]
        or report["claim_evaluation"]["mechanical_integrity"] != "PASS"
        or report["claim_evaluation"]["research_eligibility"] != "INELIGIBLE"
        or report["json_payload"]["profitability_claim_is_real"] is not False
        or daily.get("daily_completed_bars") != 212
        or daily.get("actual_scored_signals") != 181
        or market.get("order_count") != len(orders)
        or market.get("fill_count") != len(fills)
        or market.get("no_market_rejection_count") != 0
        or acceptance.get("precision_skipped_bars") != 0
        or acceptance.get("rejected_precision_events") != 0
        or acceptance.get("missing_market_state") != 0
        or acceptance.get("catalog_identity") != spec["catalog_id"]
        or acceptance.get("instrument_metadata_identity") != spec["metadata_id"]
    ):
        raise RuntimeError(f"{label} locked replacement identity or acceptance mismatch")
    fees = sum((money(item["commission"])[0] for item in fills), Decimal(0))
    funding_values = [money(item["pnl_change"])[0] for item in funding]
    funding_paid = sum((-item for item in funding_values if item < 0), Decimal(0))
    funding_received = sum((item for item in funding_values if item > 0), Decimal(0))
    net_funding = sum(funding_values, Decimal(0))
    net_pnl, currency = money(native["terminal_portfolio"]["total_pnl"])
    if currency != "USDT":
        raise RuntimeError("replacement PnL is not USDT-denominated")
    position_events = [item for item in positions if item["row_type"] != "FINAL_NATIVE_POSITION"]
    opened = [item for item in position_events if item["row_type"] == "PositionOpened"]
    closed = [item for item in position_events if item["row_type"] == "PositionClosed"]
    active_ns, exposure_ratio = exposure(positions)
    final_positions = [item for item in positions if item["row_type"] == "FINAL_NATIVE_POSITION"]
    terminal_qty = sum((Decimal(item["signed_qty"]) for item in final_positions), Decimal(0))
    perf_json = load(performance_path)
    pnl_stats = statistics.get("stats_pnls", {}).get("USDT", {})
    metrics = {
        "schema": "owner-smoke-002-replacement-metrics-v1",
        "status": "PASS",
        "profile": label,
        "completed_daily_bars": daily["daily_completed_bars"],
        "scored_decisions": daily["actual_scored_signals"],
        "orders": len(orders),
        "fills": len(fills),
        "native_positions": native["backtest_result"]["total_positions"],
        "completed_native_trades": completed["completed_trade_count"],
        "completed_native_trades_status": completed["status"],
        "long_entries": sum(Decimal(item["signed_qty"]) > 0 for item in opened),
        "short_entries": sum(Decimal(item["signed_qty"]) < 0 for item in opened),
        "position_exits": len(closed),
        "gross_pnl": "UNDEFINED",
        "gross_pnl_reason": "PINNED_NAUTILUS_NATIVE_GROSS_FIELD_UNAVAILABLE_UNAMBIGUOUSLY",
        "net_pnl": f"{net_pnl:.8f}",
        "fees": f"{fees:.8f}",
        "funding": "NOT_APPLICABLE" if label == "spot" else f"{net_funding:.8f}",
        "funding_paid": "NOT_APPLICABLE" if label == "spot" else f"{funding_paid:.8f}",
        "funding_received": "NOT_APPLICABLE" if label == "spot" else f"{funding_received:.8f}",
        "ending_equity": perf_json["equity_curve"][-1]["equity"],
        "maximum_drawdown": perf_json["max_drawdown"]["value"],
        "total_return": perf_json["total_return"]["value"],
        "cagr": perf_json["cagr"]["value"],
        "sharpe": decimal_stat(statistics, "Sharpe Ratio (252 days)"),
        "sortino": decimal_stat(statistics, "Sortino Ratio (252 days)"),
        "calmar": "UNDEFINED",
        "calmar_reason": "NO_SSOT_FALLBACK_OR_UNAMBIGUOUS_PINNED_NAUTILUS_VALUE",
        "win_rate": str(pnl_stats.get("Win Rate", "UNDEFINED")),
        "profit_factor": decimal_stat(statistics, "Profit Factor"),
        "average_trade": "UNDEFINED",
        "average_trade_reason": "NATIVE_COMPLETED_TRADE_SEQUENCE_UNAVAILABLE",
        "native_expectancy": str(pnl_stats.get("Expectancy", "UNDEFINED")),
        "exposure_ratio": exposure_ratio,
        "exposure_active_ns": active_ns,
        "terminal_signed_quantity": str(terminal_qty),
        "terminal_position_open": native["terminal_position_open"],
        "terminal_portfolio": native["terminal_portfolio"],
        "checker": checker["outcome"],
        "replay": replay["result"],
        "replay_identity": replay["replay_identity"],
        "currency": "USDT",
        "undefined_metrics_not_coerced_to_zero": True,
    }
    sources = {
        "workflow": workflow_path,
        "protocol": protocol_path,
        "run_config": run_dir / "lab_run_config.json",
        "status": run_dir / "status.json",
        "native_result": run_dir / "nautilus_result.json",
        "checker": checker_path,
        "strategy_identity": run_dir / "strategy_identity.json",
        "dataset_release": run_dir / "dataset_release.json",
        "instrument_metadata": run_dir / "instrument_metadata.json",
        "source_revision": run_dir / "source_revision.json",
        "native_statistics": run_dir / "native_statistics.json",
        "native_completed_trades": run_dir / "native_completed_trades.json",
        "orders": run_dir / "orders.csv",
        "fills": run_dir / "fills.csv",
        "positions": run_dir / "positions.csv",
        "account": run_dir / "account.csv",
        "performance": performance_path,
        "replay": replay_path,
        "authoritative_report": report_path,
        "run_manifest": run_dir / "evidence_manifest.json",
    }
    if label == "perpetual":
        sources["funding"] = run_dir / "funding.csv"
        sources["funding_source"] = run_dir / "funding_source.json"
    return {
        "label": label,
        "spec": spec,
        "run_dir": run_dir,
        "workflow": workflow,
        "protocol": load(protocol_path),
        "replay": replay,
        "report": report,
        "checker": checker,
        "checks": checks,
        "native": native,
        "statistics": statistics,
        "performance": perf_json,
        "metrics": metrics,
        "source_revision": source_revision,
        "orders": orders,
        "fills": fills,
        "positions": positions,
        "funding": funding,
        "sources": sources,
    }


def coordinate(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def utc_ns(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = parsed - epoch
    return (
        delta.days * 86_400_000_000_000
        + delta.seconds * 1_000_000_000
        + delta.microseconds * 1_000
    )


def points_for_chart(points: list[tuple[int, Decimal]]) -> tuple[str, Decimal, Decimal]:
    width = Decimal(796)
    height = Decimal(234)
    left = Decimal(80)
    top = Decimal(32)
    x_min = min(item[0] for item in points)
    x_max = max(item[0] for item in points)
    y_min = min(min(item[1] for item in points), Decimal(0))
    y_max = max(max(item[1] for item in points), Decimal(0))
    if y_min == y_max:
        y_max += Decimal(1)
    x_span = Decimal(max(1, x_max - x_min))
    y_span = y_max - y_min
    result = " ".join(
        f"{coordinate(left + Decimal(timestamp - x_min) * width / x_span)},"
        f"{coordinate(top + (y_max - value) * height / y_span)}"
        for timestamp, value in points
    )
    return result, y_min, y_max


def line_svg(title: str, points: list[tuple[int, Decimal]], source_hash: str, label: str) -> str:
    encoded, y_min, y_max = points_for_chart(points)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="320" viewBox="0 0 900 320" role="img">\n'
        f"<!-- source_sha256={source_hash}; deterministic_downsampling=NONE -->\n"
        '<rect width="900" height="320" fill="#fff"/>\n'
        f'<text x="24" y="22" font-family="sans-serif" font-size="16">{escape(title)}</text>\n'
        '<line x1="80" y1="266" x2="876" y2="266" stroke="#555"/>\n'
        '<line x1="80" y1="32" x2="80" y2="266" stroke="#555"/>\n'
        f'<polyline points="{encoded}" fill="none" stroke="#1468a0" stroke-width="2"/>\n'
        f'<text x="4" y="40" font-family="monospace" font-size="10">{escape(str(y_max))}</text>\n'
        f'<text x="4" y="264" font-family="monospace" font-size="10">{escape(str(y_min))}</text>\n'
        f'<text x="80" y="298" font-family="sans-serif" font-size="11">{escape(label)}; no downsampling</text>\n'
        "</svg>\n"
    )


def comparison_svg(series: dict[str, list[tuple[int, Decimal]]]) -> str:
    all_points = [point for points in series.values() for point in points]
    x_min = min(item[0] for item in all_points)
    x_max = max(item[0] for item in all_points)
    y_min = min(min(item[1] for item in all_points), Decimal(0))
    y_max = max(max(item[1] for item in all_points), Decimal(0))
    if y_min == y_max:
        y_max += Decimal(1)
    x_span = Decimal(max(1, x_max - x_min))
    y_span = y_max - y_min
    colors = {"spot": "#1468a0", "perpetual": "#b23a48"}
    polylines = []
    for name, points in series.items():
        encoded = " ".join(
            f"{coordinate(Decimal(80) + Decimal(timestamp - x_min) * Decimal(796) / x_span)},"
            f"{coordinate(Decimal(32) + (y_max - value) * Decimal(234) / y_span)}"
            for timestamp, value in points
        )
        polylines.append(
            f'<polyline points="{encoded}" fill="none" stroke="{colors[name]}" stroke-width="2"/>',
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="320" viewBox="0 0 900 320" role="img">\n'
        '<!-- independent_profile_equity_series; shared_axis; deterministic_downsampling=NONE -->\n'
        '<rect width="900" height="320" fill="#fff"/>\n'
        '<text x="24" y="22" font-family="sans-serif" font-size="16">Spot versus Perpetual independent equity</text>\n'
        '<line x1="80" y1="266" x2="876" y2="266" stroke="#555"/>\n'
        '<line x1="80" y1="32" x2="80" y2="266" stroke="#555"/>\n'
        + "\n".join(polylines)
        + f'\n<text x="4" y="40" font-family="monospace" font-size="10">{escape(str(y_max))}</text>\n'
        + f'<text x="4" y="264" font-family="monospace" font-size="10">{escape(str(y_min))}</text>\n'
        + '<text x="80" y="298" font-family="sans-serif" font-size="11">Spot blue; Perpetual red; shared axis; independent 10000 USDT accounts; no ranking</text>\n</svg>\n'
    )


def cumulative(rows: list[dict[str, str]], field: str, absolute: bool) -> list[tuple[int, Decimal]]:
    total = Decimal(0)
    points = [(SCORING_START_NS, total)]
    for item in sorted(rows, key=lambda row: int(row["ts_event"])):
        value = money(item[field])[0]
        total += abs(value) if absolute else value
        points.append((int(item["ts_event"]), total))
    points.append((SCORING_END_NS, total))
    return points


def position_points(view: dict[str, Any]) -> list[tuple[int, Decimal]]:
    points = [(SCORING_START_NS, Decimal(0))]
    for item in view["native"]["strategy_observations"].get("position_sequence", []):
        timestamp = max(SCORING_START_NS, min(SCORING_END_NS, int(item["timestamp_ns"])))
        value = Decimal(str(item["signed_position"]))
        if timestamp > points[-1][0]:
            points.append((timestamp, points[-1][1]))
        points.append((timestamp, value))
    points.append((SCORING_END_NS, Decimal(view["metrics"]["terminal_signed_quantity"])))
    return points


def metric_table(metrics: dict[str, Any]) -> str:
    fields = [
        ("Completed Daily Bars", "completed_daily_bars"),
        ("Scored decisions", "scored_decisions"),
        ("Orders", "orders"),
        ("Fills", "fills"),
        ("Native positions", "native_positions"),
        ("Completed native trades", "completed_native_trades"),
        ("Long entries", "long_entries"),
        ("Short entries", "short_entries"),
        ("Exits", "position_exits"),
        ("Gross PnL", "gross_pnl"),
        ("Net PnL (USDT)", "net_pnl"),
        ("Fees (USDT)", "fees"),
        ("Funding (USDT)", "funding"),
        ("Ending equity (USDT)", "ending_equity"),
        ("Maximum drawdown", "maximum_drawdown"),
        ("Sharpe", "sharpe"),
        ("Sortino", "sortino"),
        ("Calmar", "calmar"),
        ("Win rate (native PnL statistic)", "win_rate"),
        ("Profit factor", "profit_factor"),
        ("Average trade", "average_trade"),
        ("Exposure ratio", "exposure_ratio"),
        ("Terminal signed quantity", "terminal_signed_quantity"),
        ("Checker", "checker"),
        ("Replay", "replay"),
    ]
    lines = ["| الحقل | القيمة |", "|---|---:|"]
    lines.extend(f"| {name} | {metrics[key]} |" for name, key in fields)
    return "\n".join(lines)


def report_markdown(views: dict[str, dict[str, Any]]) -> tuple[str, str, str, str, str]:
    spot = views["spot"]
    perp = views["perpetual"]
    common = (
        "> **هذه ليست توصية تداول، وليست Final Holdout، ولا تسمح بأي Profitability Claim.**\n\n"
        "الغرض `EXPLORATORY_OPERATIONAL_VALIDATION` فقط، والنافذة "
        "`[2021-01-01T00:00:00Z, 2021-08-01T00:00:00Z)` مع scoring "
        "`[2021-02-01T00:00:00Z, 2021-08-01T00:00:00Z)` مصنفة "
        "`DATA_QUALITY_INSPECTED_NOT_FINAL_HOLDOUT`.\n"
    )
    main = f"""# OWNER_SMOKE_002 Replacement 001 — تقرير Owner

{common}

## الحكم التنفيذي

- Spot: `CHECK_PASS` وdeterministic replay `PASS`؛ 27 Order و27 Fill.
- Perpetual: `CHECK_PASS` وdeterministic replay `PASS`؛ 55 Order و55 Fill.
- لا executable Bar أوMark رُفضت بسبب precision، ولا `No market` منهجي.
- Final Holdout used: `false`؛ Real profitability claim: `false`؛ Research eligibility: `INELIGIBLE`.
- كانت النتيجة المالية سلبية في التجربتين. لم تتغير الاستراتيجية أوparameters أوالنافذة أوالبيانات لجعل النتيجة أفضل.

## Spot

{metric_table(spot['metrics'])}

[تقرير Spot المفصل](../spot-report/README.md)

## Perpetual

{metric_table(perp['metrics'])}

[تقرير Perpetual المفصل](../perpetual-report/README.md)

## الهوية

| الربط | Spot | Perpetual |
|---|---|---|
| DatasetRelease | `{SPOT_RELEASE}` | `{PERP_RELEASE}` |
| Catalog | `{SPOT_CATALOG}` | `{PERP_CATALOG}` |
| Strategy identity | `{spot['spec']['strategy_id']}` | `{perp['spec']['strategy_id']}` |
| SourceRevision | `{spot['source_revision']['git_commit']}` | `{perp['source_revision']['git_commit']}` |
| Replay identity | `{spot['spec']['replay_id']}` | `{perp['spec']['replay_id']}` |

## القيود

- التنفيذ bar-based والرسوم `ESTIMATED_FEE` وفق العقد المقفل؛ لا ادعاء spread أوqueue أوimpact تاريخي.
- لا يوفر runtime المقفل completed-trade sequence أوgross PnL أوCalmar بصورة غير ملتبسة؛ بقيت القيم `UNDEFINED` ولم تُحوّل إلى صفر.
- المركز النهائي مفتوح في التجربتين وفق terminal policy المقفلة.
- [Mechanical Integrity](../mechanical-integrity/README.md) و[Deterministic Replay](../deterministic-replay/README.md) يعرضان البوابات المستقلة.

## الرسوم

![Spot equity](../charts/spot-equity.svg)

![Perpetual equity](../charts/perpetual-equity.svg)

![Equity comparison](../charts/spot-vs-perpetual-equity.svg)
"""
    spot_report = f"""# تقرير Spot — OWNER_SMOKE_002 Replacement 001

{common}

{metric_table(spot['metrics'])}

## التنفيذ

- Profile: `BINANCE_SPOT_CASH_LONG_ONLY`؛ CASH/NETTING؛ borrowing وshort وfunding غير مطبقة.
- 304,596/304,596 executable Bars قُبلت؛ precision skips وmissing market state و`No market` = 0.
- كل الـ27 Order وصلت إلى market state سببية وأنتجت 27 Fill أصلية من Nautilus بعد latency 60 ثانية.
- 14 long entries و13 exits؛ لا short entry. المركز النهائي LONG `0.1 BTC`.
- Net PnL الحقيقي `-751.78721000 USDT`، والـfees `119.59221000 USDT`، والـending equity `9248.21279000 USDT`.

## الرسوم

![Equity](../charts/spot-equity.svg)

![Drawdown](../charts/spot-drawdown.svg)

![Position](../charts/spot-position.svg)

![Cumulative fees](../charts/spot-fees.svg)

DatasetRelease `{SPOT_RELEASE}`؛ Catalog `{SPOT_CATALOG}`؛ Strategy `{spot['spec']['strategy_id']}`؛ SourceRevision `{spot['source_revision']['git_commit']}`.
"""
    perp_report = f"""# تقرير Perpetual — OWNER_SMOKE_002 Replacement 001

{common}

{metric_table(perp['metrics'])}

## التنفيذ والتمويل

- Profile: `BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING`؛ MARGIN/NETTING، leverage=1، Hedge Mode معطل.
- execution Bars `305280/305280` وMark `305280/305280` قُبلت بلا precision rejection أوmissing market state.
- 55 Order و55 Fill؛ 14 long و14 short entries؛ 27 reversal نُفذت close-to-flat ثم أمرًا مستقلًا بلا direct cross-zero.
- 636 source funding events أنتجت 1,272 runtime updates، لكنها أنتجت 539 settlement مالية أصلية فقط للـ539 boundary ذات position؛ 97 boundary بلا position أنتجت صفر settlement.
- latest causal Mark فقط: age من 0 إلى 46,000,000 ns، تحت الحد 60,000,000,000 ns؛ لا future أوnearest أوinterpolation أوfallback.
- Net PnL الحقيقي `-3010.78713375 USDT`؛ fees `242.69077200 USDT`؛ net funding `-692.06436175 USDT`؛ ending equity `6989.21286625 USDT`.

## الرسوم

![Equity](../charts/perpetual-equity.svg)

![Drawdown](../charts/perpetual-drawdown.svg)

![Position](../charts/perpetual-position.svg)

![Cumulative fees](../charts/perpetual-fees.svg)

![Cumulative funding](../charts/perpetual-funding.svg)

DatasetRelease `{PERP_RELEASE}`؛ Catalog `{PERP_CATALOG}`؛ Strategy `{perp['spec']['strategy_id']}`؛ SourceRevision `{perp['source_revision']['git_commit']}`.
"""
    mechanical = f"""# Mechanical Integrity — OWNER_SMOKE_002 Replacement 001

{common}

| البوابة | Spot | Perpetual |
|---|---:|---:|
| Read-only checker | CHECK_PASS | CHECK_PASS |
| Daily bars / decisions | 212 / 181 | 212 / 181 |
| Orders / Fills | 27 / 27 | 55 / 55 |
| No-market rejections | 0 | 0 |
| Precision-skipped executable Bars | 0 | 0 |
| Rejected Mark precision events | N/A | 0 |
| Causality / 60s latency | PASS | PASS |
| Fee exactly once | PASS | PASS |
| Native funding cardinality | N/A | 539/539 eligible boundaries |
| Offline boundary / contacts | PASS / 0 | PASS / 0 |
| Terminal policy | PASS | PASS |

Spot القديمة التي أعادت false `CHECK_PASS` يعيدها checker الحالي `CHECK_FAIL`. Pair الـFundingRateUpdate لا تُعد settlement مالية مزدوجة؛ الدليل المالي هو `PositionAdjusted(FUNDING)` مع أثر AccountState. المحاولات الفاشلة والـTrials السابقة بقيت immutable ومتصلة بسلسلة supersession.
"""
    deterministic = f"""# Deterministic Replay — OWNER_SMOKE_002 Replacement 001

{common}

- Spot: primary/replay semantic digest `{spot['replay']['primary_semantic_digest']}`؛ replay identity `{spot['replay']['replay_identity']}`؛ `PASS`.
- Perpetual: primary/replay semantic digest `{perp['replay']['primary_semantic_digest']}`؛ replay identity `{perp['replay']['replay_identity']}`؛ `PASS`.
- كل replay شُغلت في process جديد، وأعيد checker read-only، وتطابقت signals/orders/fills/positions/accounts/fees/funding/equity/terminal state دلاليًا.
- اختلاف paths أوcapture timestamps غير داخل الهوية الدلالية.
"""
    return main, spot_report, perp_report, mechanical, deterministic


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--acceptance-output", type=Path, required=True)
    parser.add_argument("--refresh-inventory-only", action="store_true")
    args = parser.parse_args()
    acceptance_root = args.acceptance_output.resolve()
    if args.refresh_inventory_only:
        if not EVIDENCE.is_dir():
            raise FileNotFoundError(f"replacement evidence does not exist: {EVIDENCE}")
        refresh_inventory()
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "refreshed": True,
                    "manifest_sha256": sha256_file(EVIDENCE / "final-content-manifest.json"),
                },
                sort_keys=True,
            ),
        )
        return 0
    if EVIDENCE.exists():
        raise FileExistsError(f"refusing to overwrite additive evidence: {EVIDENCE}")
    if git("status", "--porcelain"):
        raise RuntimeError("evidence assembly requires a clean worktree")
    if git("rev-parse", "HEAD") != git("rev-parse", "origin/main"):
        raise RuntimeError("HEAD and origin/main diverge before evidence assembly")
    locks = {
        "SSOT.md": sha256_file(ROOT / "SSOT.md"),
        "runtime.lock.json": sha256_file(ROOT / "runtime.lock.json"),
        "requirements.lock.txt": sha256_file(ROOT / "requirements.lock.txt"),
    }
    expected_locks = {
        "SSOT.md": SSOT_SHA256,
        "runtime.lock.json": RUNTIME_SHA256,
        "requirements.lock.txt": DEPENDENCY_SHA256,
    }
    if locks != expected_locks:
        raise RuntimeError("locked SSOT/runtime/dependency identity changed")
    acceptance = load(acceptance_root / "result.json")
    if (
        acceptance.get("status") != "PASS"
        or acceptance.get("unique_tests") != 268
        or acceptance.get("test_execution_occurrences") != 960
        or any(value != "PASS" for value in acceptance.get("gates", {}).values())
        or acceptance.get("official_trial") is not False
        or acceptance.get("strategy_run") is not False
        or acceptance.get("network_used") is not False
    ):
        raise RuntimeError("final repair acceptance identity is not PASS")
    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    views = {label: profile_view(label, spec) for label, spec in TRIALS.items()}
    EVIDENCE.mkdir(parents=True)

    write_json(
        EVIDENCE / "baseline-attestation.json",
        {
            "schema": "owner-smoke-002-replacement-baseline-v1",
            "status": "PASS",
            "repair_baseline_commit": BASELINE_COMMIT,
            "artifact_assembly_head": git("rev-parse", "HEAD"),
            "artifact_assembly_origin_main": git("rev-parse", "origin/main"),
            "worktree_clean_before_assembly": True,
            "locked_hashes": locks,
            "official_source_revisions": {
                label: view["source_revision"] for label, view in views.items()
            },
            "recorded_at_utc": generated_at,
        },
    )
    write_json(
        EVIDENCE / "owner-authorization.json",
        {
            "schema": "owner-smoke-002-replacement-authorization-v1",
            "status": "PASS",
            "repair_epoch": "NAUTILUS_INSTRUMENT_REPRESENTATION_AND_FUNDING_CHECKER_REPAIR_001",
            "strategy": "btcusdt_daily_price_vs_sma20_trend_v1",
            "strategy_parameters_changed": False,
            "market_numeric_values_changed": False,
            "final_holdout_used": False,
            "optimization_used": False,
            "real_profitability_claim": False,
        },
    )
    write_json(
        EVIDENCE / "protocol.json",
        {
            "schema": "owner-smoke-002-replacement-protocol-bindings-v1",
            "status": "PASS",
            "purpose": "EXPLORATORY_OPERATIONAL_VALIDATION",
            "profiles": {
                label: {
                    "protocol_id": view["spec"]["protocol_id"],
                    "binding": binding(view["sources"]["protocol"]),
                    "protocol": view["protocol"],
                }
                for label, view in views.items()
            },
        },
    )
    write_json(
        EVIDENCE / "strategy-identities.json",
        {
            "schema": "owner-smoke-002-replacement-strategy-identities-v1",
            "status": "PASS",
            "strategy_semantics_changed": False,
            "profiles": {
                label: {
                    "strategy_identity": view["spec"]["strategy_id"],
                    "binding": binding(view["sources"]["strategy_identity"]),
                    "source_revision": view["source_revision"],
                }
                for label, view in views.items()
            },
        },
    )
    write_json(
        EVIDENCE / "dataset-bindings.json",
        {
            "schema": "owner-smoke-002-replacement-dataset-bindings-v1",
            "status": "PASS",
            "duckdb_semantic_identity": SEMANTIC_DB,
            "duckdb_schema_identity": SCHEMA_DB,
            "window": {
                "warmup_start": "2021-01-01T00:00:00Z",
                "scoring_start": "2021-02-01T00:00:00Z",
                "end_exclusive": "2021-08-01T00:00:00Z",
                "classification": "DATA_QUALITY_INSPECTED_NOT_FINAL_HOLDOUT",
            },
            "profiles": {
                label: {
                    "dataset_release_id": view["spec"]["release_id"],
                    "catalog_identity": view["spec"]["catalog_id"],
                    "instrument_metadata_identity": view["spec"]["metadata_id"],
                    "release_binding": binding(view["sources"]["dataset_release"]),
                    "metadata_binding": binding(view["sources"]["instrument_metadata"]),
                }
                for label, view in views.items()
            },
            "canonical_market_numeric_values_changed": False,
        },
    )

    relevant_prefix = "owner-smoke-002-replacement-001-"
    journal_lines = []
    for line in (ROOT / "research/trials.jsonl").read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if value["trial_id"].startswith(relevant_prefix):
            journal_lines.append(line)
    if len(journal_lines) != 12:
        raise RuntimeError(f"expected 12 retained replacement trial records, got {len(journal_lines)}")
    write_text(EVIDENCE / "trial-records.jsonl", "\n".join(journal_lines) + "\n")

    for label, view in views.items():
        run_binding = {
            "schema": "owner-smoke-002-replacement-run-result-v1",
            "status": "PASS",
            "profile": label,
            "trial_id": view["spec"]["trial_id"],
            "run_id": load(view["sources"]["status"])["run_id"],
            "run_state": "COMPLETED",
            "checker": "CHECK_PASS",
            "source_revision": view["source_revision"],
            "metrics": view["metrics"],
            "source_bindings": {
                name: binding(path) for name, path in sorted(view["sources"].items())
            },
        }
        write_json(EVIDENCE / label / "run-result.json", run_binding)
        write_json(
            EVIDENCE / label / "checker-result.json",
            {
                "schema": "owner-smoke-002-replacement-checker-result-v1",
                "status": "PASS",
                "profile": label,
                "read_only_regeneration_exact_match": True,
                "checker_binding": binding(view["sources"]["checker"]),
                "checker": view["checker"],
            },
        )
        write_json(
            EVIDENCE / label / "replay-result.json",
            {
                "schema": "owner-smoke-002-replacement-replay-result-v1",
                "status": "PASS",
                "profile": label,
                "replay_binding": binding(view["sources"]["replay"]),
                "replay": view["replay"],
            },
        )
        write_json(EVIDENCE / label / "metrics.json", view["metrics"])

    write_json(
        EVIDENCE / "offline-enforcement.json",
        {
            "schema": "owner-smoke-002-replacement-offline-enforcement-v1",
            "status": "PASS",
            "network_syscalls_blocked": True,
            "dns_blocked": True,
            "child_process_network_blocked": True,
            "external_contact_count": 0,
            "profiles": {
                label: view["native"]["network_guard"] for label, view in views.items()
            },
        },
    )
    write_json(
        EVIDENCE / "deterministic-replay.json",
        {
            "schema": "owner-smoke-002-replacement-deterministic-replay-v1",
            "status": "PASS",
            "semantic_dimensions": [
                "signals", "orders", "fills", "positions", "account_events", "fees",
                "funding", "equity_series", "terminal_state", "mechanical_integrity",
                "report_inputs",
            ],
            "profiles": {
                label: {
                    "result": view["replay"]["result"],
                    "fresh_processes": view["replay"]["fresh_processes"],
                    "primary_semantic_digest": view["replay"]["primary_semantic_digest"],
                    "replay_semantic_digest": view["replay"]["replay_semantic_digest"],
                    "replay_identity": view["replay"]["replay_identity"],
                    "checker": view["replay"]["replay_checker"],
                }
                for label, view in views.items()
            },
        },
    )
    funding_check = views["perpetual"]["checks"]["owner_smoke_all_official_funding_processed"]
    write_json(
        EVIDENCE / "funding-and-mark-validation.json",
        {
            "schema": "owner-smoke-002-replacement-funding-mark-v1",
            "status": "PASS",
            "source_funding_events": funding_check["source_event_count"],
            "runtime_updates": funding_check["runtime_update_count"],
            "eligible_position_boundaries": funding_check["applicable_open_position_boundaries"],
            "no_position_boundaries": funding_check["no_position_boundaries"],
            "native_financial_settlements": funding_check["native_settlement_count"],
            "mark_binding": funding_check["mark_binding"],
            "mark_age_ns_min": funding_check["mark_age_ns_min"],
            "mark_age_ns_max": funding_check["mark_age_ns_max"],
            "maximum_mark_staleness_ns": funding_check["maximum_mark_staleness_ns"],
            "future_mark_used": False,
            "nearest_mark_used": False,
            "interpolation_used": False,
            "runtime_update_pair_counted_as_two_settlements": False,
        },
    )
    write_json(
        EVIDENCE / "research-eligibility.json",
        {
            "schema": "owner-smoke-002-replacement-research-eligibility-v1",
            "status": "PASS",
            "purpose": "EXPLORATORY_OPERATIONAL_VALIDATION",
            "partition_role": "DEVELOPMENT",
            "data_window_classification": "DATA_QUALITY_INSPECTED_NOT_FINAL_HOLDOUT",
            "final_holdout_used": False,
            "real_profitability_claim": False,
            "claim_eligibility": "INELIGIBLE_FOR_REAL_PROFITABILITY_CLAIM",
            "optimization_performed": False,
            "parameter_search_performed": False,
        },
    )

    failed = [
        {
            "attempt": "replacement-spot-base",
            "trial_id": "owner-smoke-002-replacement-001-spot-sma20-development",
            "status": "FAILED_RETAINED",
            "cause": "Official child inherited non-UTC TZ and failed the locked runtime environment preflight",
            "run_ref": "runs/owner-smoke-002-replacement-001-spot-run-a754e2c26324",
            "repair_commit": "7b7ba1a",
        },
        {
            "attempt": "replacement-spot-retry-001",
            "trial_id": "owner-smoke-002-replacement-001-spot-sma20-development-retry-001",
            "status": "FAILED_RETAINED",
            "cause": "Checker compared 304596 accepted sparse Spot Bars against 305280 complete minute dispositions including 684 verified no-trade minutes",
            "run_ref": "runs/owner-smoke-002-replacement-001-spot-run-retry-001-abbedb975f37",
            "repair_commit": "e02b9ff",
            "financial_execution_retained": {"orders": 27, "fills": 27},
        },
        {
            "attempt": "replacement-spot-retry-002-report-attempt-001",
            "status": "BLOCKED_REPORT_RECOVERY_RETAINED",
            "cause": "Report resolver reinterpreted immutable older failed checker evidence using the repaired current checker",
            "detail": "EVIDENCE_INCOMPLETE: selected trial and Run evidence mismatch",
            "repair_commit": "9e24530",
        },
        {
            "attempt": "replacement-spot-retry-002-report-attempt-002",
            "status": "BLOCKED_REPORT_RECOVERY_RETAINED",
            "cause": "Retained failure codes were compared as ordered lists instead of semantic sets",
            "detail": "EVIDENCE_INCOMPLETE: selected trial and Run evidence mismatch",
            "repair_commit": "c7c46a7",
        },
    ]
    with (EVIDENCE / "failed-attempts.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for item in failed:
            stream.write(json.dumps(item, sort_keys=True, ensure_ascii=False) + "\n")
    write_json(EVIDENCE / "test-results.json", acceptance)
    copy_normalized_text(acceptance_root / "test-output.txt", EVIDENCE / "test-output.txt")

    charts = EVIDENCE / "charts"
    for label, view in views.items():
        perf_hash = sha256_file(view["sources"]["performance"])
        equity = [
            (utc_ns(item["timestamp"]), Decimal(item["equity"]))
            for item in view["performance"]["equity_curve"]
        ]
        drawdown = [
            (utc_ns(item["timestamp"]), Decimal(item["drawdown"]))
            for item in view["performance"]["drawdown_curve"]
        ]
        write_text(charts / f"{label}-equity.svg", line_svg(f"{label.upper()} Equity (USDT)", equity, perf_hash, "Equity USDT"))
        write_text(charts / f"{label}-drawdown.svg", line_svg(f"{label.upper()} Drawdown", drawdown, perf_hash, "Drawdown ratio"))
        write_text(charts / f"{label}-position.svg", line_svg(f"{label.upper()} signed position (BTC)", position_points(view), sha256_file(view["sources"]["native_result"]), "Signed BTC position"))
        write_text(charts / f"{label}-fees.svg", line_svg(f"{label.upper()} cumulative fees (USDT)", cumulative(view["fills"], "commission", True), sha256_file(view["sources"]["fills"]), "Cumulative fees USDT"))
    write_text(charts / "perpetual-funding.svg", line_svg("PERPETUAL cumulative funding (USDT)", cumulative(views["perpetual"]["funding"], "pnl_change", False), sha256_file(views["perpetual"]["sources"]["funding"]), "Cumulative net funding USDT"))
    equity_series = {
        label: [
            (utc_ns(item["timestamp"]), Decimal(item["equity"]))
            for item in view["performance"]["equity_curve"]
        ]
        for label, view in views.items()
    }
    write_text(charts / "spot-vs-perpetual-equity.svg", comparison_svg(equity_series))

    owner, spot_report, perp_report, mechanical, deterministic_report = report_markdown(views)
    write_text(EVIDENCE / "owner-report/README.md", owner)
    write_text(EVIDENCE / "spot-report/README.md", spot_report)
    write_text(EVIDENCE / "perpetual-report/README.md", perp_report)
    write_text(EVIDENCE / "mechanical-integrity/README.md", mechanical)
    write_text(EVIDENCE / "deterministic-replay/README.md", deterministic_report)

    source_entries: dict[str, dict[str, Any]] = {
        "research/trials.jsonl": binding(ROOT / "research/trials.jsonl"),
        "research/history_anchors.jsonl": binding(ROOT / "research/history_anchors.jsonl"),
        "research/holdout_lock.json": binding(ROOT / "research/holdout_lock.json"),
    }
    source_entries.update(stable_repair_bindings())
    for view in views.values():
        for path in view["sources"].values():
            source_entries[path.relative_to(ROOT).as_posix()] = binding(path)
    evidence_files = {
        path.relative_to(EVIDENCE).as_posix(): {
            "path": path.relative_to(ROOT).as_posix(),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in EVIDENCE.rglob("*")
        if path.is_file() and path.name not in {"evidence-inventory.json", "final-content-manifest.json"}
    }
    write_json(
        EVIDENCE / "evidence-inventory.json",
        {
            "schema": "owner-smoke-002-replacement-evidence-inventory-v1",
            "status": "PASS",
            "source_entries": dict(sorted(source_entries.items())),
            "generated_entries": dict(sorted(evidence_files.items())),
            "source_inventory_identity": canonical_sha256(source_entries),
            "generated_inventory_identity": canonical_sha256(evidence_files),
            "raw_data_copied": False,
            "duckdb_payload_copied": False,
            "catalog_payload_copied": False,
            "inventory_self_excluded": True,
        },
    )
    inventory = {
        path.relative_to(EVIDENCE).as_posix(): {
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(EVIDENCE.rglob("*"))
        if path.is_file() and path.name != "final-content-manifest.json"
    }
    write_json(
        EVIDENCE / "final-content-manifest.json",
        {
            "schema": "owner-smoke-002-replacement-final-content-manifest-v1",
            "status": "PASS",
            "epoch": "OWNER_SMOKE_002_REPLACEMENT_001",
            "created_at_utc": generated_at,
            "files": inventory,
            "file_count_excluding_manifest": len(inventory),
            "raw_archives_committed": False,
            "duckdb_payloads_committed": False,
            "catalog_payloads_committed": False,
            "secrets_present": False,
            "final_holdout_used": False,
            "real_profitability_claim": False,
        },
    )
    print(json.dumps({"status": "PASS", "path": str(EVIDENCE.relative_to(ROOT)), "manifest_sha256": sha256_file(EVIDENCE / "final-content-manifest.json")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
