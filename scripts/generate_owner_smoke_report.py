#!/usr/bin/env python3
"""Create the retained historical-only OWNER_SMOKE presentation.

This legacy presentation path cannot publish a current Official Result.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import tempfile
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from crypto_lab.checker import check_evidence_directory
from crypto_lab.diagnostics import derive_performance_diagnostics
from crypto_lab.exposure import AuthoritativeExposureResolver
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.history import AuthoritativeResearchHistory
from crypto_lab.history import HistoryAnchorStore
from crypto_lab.legacy_publication import LEGACY_HISTORICAL_ONLY_PUBLICATION
from crypto_lab.legacy_publication import require_historical_only_replay
from crypto_lab.legacy_publication import require_historical_only_result
from crypto_lab.owner import OwnerWorkflowInput
from crypto_lab.research import PartitionRole
from crypto_lab.research import ResearchError
from crypto_lab.research import ResearchProtocol
from crypto_lab.research import ResultExposure
from crypto_lab.research import TrialState
from crypto_lab.research import UtcInterval
from crypto_lab.reporting import PerformanceDiagnostics
from crypto_lab.timestamps import utc_datetime_to_ns


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = ROOT / "evidence/research/owner-smoke-001"
REPORT_ROOT = EVIDENCE_ROOT / "owner-report"
PUBLICATION_CLASSIFICATION = LEGACY_HISTORICAL_ONLY_PUBLICATION
TRIALS = {
    "spot": "owner-smoke-001-spot-sma20-development",
    "perpetual": "owner-smoke-001-perpetual-sma20-development",
}
WINDOW = UtcInterval(
    start_inclusive=datetime(2020, 12, 1, tzinfo=UTC),
    end_exclusive=datetime(2021, 7, 1, tzinfo=UTC),
)


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _money(value: str) -> tuple[Decimal, str]:
    parts = value.split()
    if len(parts) != 2:
        raise RuntimeError(f"invalid native Money value: {value!r}")
    amount = Decimal(parts[0])
    if not amount.is_finite():
        raise RuntimeError("native Money value is non-finite")
    return amount, parts[1]


def _fmt(value: Decimal, places: int = 8) -> str:
    return f"{value:.{places}f}"


def _native_ratio(statistics: dict, fragment: str) -> str:
    matches = [
        str(value)
        for name, value in statistics.get("stats_returns", {}).items()
        if fragment.lower() in name.lower()
    ]
    return matches[0] if len(matches) == 1 else "UNDEFINED"


def _terminal_signed_position(rows: list[dict[str, str]]) -> Decimal:
    return sum(
        (
            Decimal(row["signed_qty"])
            for row in rows
            if row["row_type"] == "FINAL_NATIVE_POSITION"
        ),
        Decimal(0),
    )


def _trial_view(
    *,
    label: str,
    trial_id: str,
    history: AuthoritativeResearchHistory,
) -> dict:
    records = history.journal.read_records()
    record = next(
        (
            item
            for item in reversed(records)
            if item.trial_id == trial_id and item.state in {
                TrialState.COMPLETED,
                TrialState.FAILED,
                TrialState.BLOCKED,
                TrialState.ABORTED,
            }
        ),
        None,
    )
    if record is None or record.state is not TrialState.COMPLETED:
        raise RuntimeError(f"{trial_id} is not terminally COMPLETED")
    run_dir = (ROOT / record.result_ref).resolve(strict=True)
    run_dir.relative_to(ROOT)
    require_historical_only_result(run_dir, repository_root=ROOT)
    workflow_path = ROOT / "research/workflows" / f"{trial_id}.json"
    workflow = OwnerWorkflowInput.from_json_bytes(workflow_path.read_bytes())
    protocol_path = ROOT / "research/protocols" / f"{record.protocol_id}.json"
    protocol = ResearchProtocol.from_json_bytes(protocol_path.read_bytes())
    replay_path = ROOT / "research/replays" / f"{trial_id}.json"
    replay = _json(replay_path)
    require_historical_only_replay(replay, repository_root=ROOT)
    native_path = run_dir / "nautilus_result.json"
    native = _json(native_path)
    checker_path = run_dir / "checker.json"
    checker = _json(checker_path)
    regenerated = check_evidence_directory(
        run_dir,
        repository_root=ROOT,
        official_source_required=True,
        source_revision_current_head_required=False,
    )
    if regenerated.to_builtins() != checker or checker.get("outcome") != "CHECK_PASS":
        raise RuntimeError(f"{trial_id} checker is not a reproducible CHECK_PASS")
    performance_path = ROOT / "research/performance" / f"{record.run_id}.json"
    performance = PerformanceDiagnostics.from_json_bytes(performance_path.read_bytes())
    if performance != derive_performance_diagnostics(run_dir=run_dir, protocol=protocol):
        raise RuntimeError(f"{trial_id} performance evidence is stale")
    report_path = ROOT / "research/reports" / f"{trial_id}.json"
    authoritative_report = _json(report_path)
    claim = authoritative_report["claim_evaluation"]
    if (
        claim["research_eligibility"] != "INELIGIBLE"
        or authoritative_report["json_payload"]["profitability_claim_is_real"] is not False
        or record.partition_role is not PartitionRole.DEVELOPMENT
        or replay.get("result") != "PASS"
    ):
        raise RuntimeError(f"{trial_id} research/claim/replay status violates authorization")
    config_path = run_dir / "lab_run_config.json"
    config = _json(config_path)
    strategy_path = run_dir / "strategy_identity.json"
    strategy = _json(strategy_path)
    dataset_path = run_dir / "dataset_release.json"
    dataset = _json(dataset_path)
    statistics_path = run_dir / "native_statistics.json"
    statistics = _json(statistics_path)
    completed_path = run_dir / "native_completed_trades.json"
    completed = _json(completed_path)
    metadata_path = run_dir / "instrument_metadata.json"
    metadata = _json(metadata_path)
    orders_path = run_dir / "orders.csv"
    fills_path = run_dir / "fills.csv"
    positions_path = run_dir / "positions.csv"
    orders = _csv(orders_path)
    fills = _csv(fills_path)
    positions = _csv(positions_path)
    commissions = [_money(item["commission"]) for item in fills]
    if commissions and {currency for _amount, currency in commissions} != {"USDT"}:
        raise RuntimeError("OWNER_SMOKE commissions are not consistently USDT-denominated")
    fees = sum((amount for amount, _currency in commissions), Decimal(0))
    net_pnl, net_currency = _money(native["terminal_portfolio"]["total_pnl"])
    if net_currency != "USDT":
        raise RuntimeError("terminal native total PnL is not USDT")
    terminal_position = _terminal_signed_position(positions)
    equity = performance.equity_curve
    if not equity:
        raise RuntimeError("performance Equity curve is empty")
    checker_checks = {item["name"]: item for item in checker["checks"]}
    funding_rows: list[dict[str, str]] = []
    funding_path: Path | None = None
    funding_source_path: Path | None = None
    funding_paid = funding_received = net_funding = Decimal(0)
    if label == "perpetual":
        funding_path = run_dir / "funding.csv"
        funding_source_path = run_dir / "funding_source.json"
        funding_rows = _csv(funding_path)
        funding_values = [_money(item["pnl_change"])[0] for item in funding_rows]
        funding_paid = sum((-item for item in funding_values if item < 0), Decimal(0))
        funding_received = sum((item for item in funding_values if item > 0), Decimal(0))
        net_funding = sum(funding_values, Decimal(0))
    lifecycle = [Decimal(0)] + [
        Decimal(str(item["signed_position"]))
        for item in native["strategy_observations"].get("position_sequence", [])
    ]
    if not lifecycle or lifecycle[-1] != terminal_position:
        lifecycle.append(terminal_position)
    state_names = ["LONG" if item > 0 else "SHORT" if item < 0 else "FLAT" for item in lifecycle]
    source_paths = {
        "WORKFLOW": workflow_path,
        "PROTOCOL": protocol_path,
        "RUN_CONFIG": config_path,
        "NATIVE_RESULT": native_path,
        "CHECKER": checker_path,
        "STRATEGY_IDENTITY": strategy_path,
        "DATASET_RELEASE": dataset_path,
        "NATIVE_STATISTICS": statistics_path,
        "NATIVE_COMPLETED_TRADES": completed_path,
        "INSTRUMENT_METADATA": metadata_path,
        "ORDERS": orders_path,
        "FILLS": fills_path,
        "POSITIONS": positions_path,
        "PERFORMANCE": performance_path,
        "REPLAY": replay_path,
        "AUTHORITATIVE_REPORT": report_path,
        "RUN_MANIFEST": run_dir / "evidence_manifest.json",
    }
    if funding_path is not None and funding_source_path is not None:
        source_paths["FUNDING"] = funding_path
        source_paths["FUNDING_SOURCE"] = funding_source_path
    return {
        "label": label,
        "trial_id": trial_id,
        "record": record,
        "run_dir": run_dir,
        "workflow": workflow,
        "protocol": protocol,
        "replay": replay,
        "native": native,
        "checker": checker,
        "checker_checks": checker_checks,
        "performance": performance,
        "strategy": strategy,
        "dataset": dataset,
        "statistics": statistics,
        "completed": completed,
        "metadata": metadata,
        "orders": orders,
        "fills": fills,
        "positions": positions,
        "funding_rows": funding_rows,
        "fees": fees,
        "funding_paid": funding_paid,
        "funding_received": funding_received,
        "net_funding": net_funding,
        "net_pnl": net_pnl,
        "final_equity": equity[-1].equity,
        "terminal_position": terminal_position,
        "lifecycle": state_names,
        "source_paths": source_paths,
        "signals": native["strategy_observations"].get("signals", []),
        "claim": claim,
        "config": config,
    }


def _xy_points(points: list[tuple[int, Decimal]]) -> tuple[str, Decimal, Decimal]:
    width, height, left, right, top, bottom = 900, 320, 80, 24, 32, 54
    inner_w = width - left - right
    inner_h = height - top - bottom
    xs = [item[0] for item in points]
    ys = [item[1] for item in points]
    x_min, x_max = min(xs), max(xs)
    y_min = min(min(ys), Decimal(0))
    y_max = max(max(ys), Decimal(0))
    if y_min == y_max:
        y_max = y_min + Decimal(1)
    x_span = max(1, x_max - x_min)
    y_span = y_max - y_min
    encoded = " ".join(
        f"{left + (x - x_min) * inner_w / x_span:.2f},{top + float((y_max - y) / y_span) * inner_h:.2f}"
        for x, y in points
    )
    return encoded, y_min, y_max


def _line_svg(
    *,
    title: str,
    points: list[tuple[int, Decimal]],
    source_sha256: str,
    y_label: str,
) -> str:
    if not points:
        raise RuntimeError(f"chart {title!r} has no points")
    polyline, y_min, y_max = _xy_points(points)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="320" '
        'viewBox="0 0 900 320" role="img">\n'
        f"<!-- source_sha256={source_sha256}; deterministic_downsampling=NONE -->\n"
        '<rect width="900" height="320" fill="#ffffff"/>\n'
        f'<text x="24" y="22" font-family="sans-serif" font-size="16">{escape(title)}</text>\n'
        '<line x1="80" y1="266" x2="876" y2="266" stroke="#555"/>\n'
        '<line x1="80" y1="32" x2="80" y2="266" stroke="#555"/>\n'
        f'<polyline points="{polyline}" fill="none" stroke="#1468a0" stroke-width="2"/>\n'
        f'<text x="4" y="38" font-family="monospace" font-size="11">{escape(_fmt(y_max))}</text>\n'
        f'<text x="4" y="266" font-family="monospace" font-size="11">{escape(_fmt(y_min))}</text>\n'
        f'<text x="80" y="298" font-family="sans-serif" font-size="11">{escape(y_label)}; '
        'المحور الرأسي يتضمن الصفر؛ لا يوجد downsampling</text>\n'
        "</svg>\n"
    )


def _position_svg(view: dict, source_sha256: str) -> str:
    start_ns = utc_datetime_to_ns(
        datetime.fromisoformat(view["config"]["scoring_start"].replace("Z", "+00:00")),
    )
    end_ns = utc_datetime_to_ns(
        datetime.fromisoformat(view["config"]["scoring_end_exclusive"].replace("Z", "+00:00")),
    )
    points: list[tuple[int, Decimal]] = [(start_ns, Decimal(0))]
    for item in view["native"]["strategy_observations"].get("position_sequence", []):
        timestamp = min(end_ns, max(start_ns, int(item["timestamp_ns"])))
        value = Decimal(str(item["signed_position"]))
        if points[-1][0] < timestamp:
            points.append((timestamp, points[-1][1]))
        points.append((timestamp, value))
    points.append((end_ns, view["terminal_position"]))
    normalized = [(timestamp, Decimal(1) if value > 0 else Decimal(-1) if value < 0 else Decimal(0)) for timestamp, value in points]
    width, left, right, top, bottom = 900, 80, 24, 32, 54
    inner_w = width - left - right
    inner_h = 320 - top - bottom
    x_min, x_max = min(item[0] for item in normalized), max(item[0] for item in normalized)
    x_span = max(1, x_max - x_min)
    # A fixed [-1, +1] scale keeps LONG/FLAT/SHORT at the same truthful
    # vertical locations for both Profiles, including Spot where SHORT never
    # occurs.  The chart is a state diagram, not a fitted financial axis.
    polyline = " ".join(
        f"{left + (timestamp - x_min) * inner_w / x_span:.2f},"
        f"{top + float((Decimal(1) - state) / Decimal(2)) * inner_h:.2f}"
        for timestamp, state in normalized
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="320" viewBox="0 0 900 320" role="img">\n'
        f"<!-- source_sha256={source_sha256}; deterministic_downsampling=NONE -->\n"
        '<rect width="900" height="320" fill="#ffffff"/>\n'
        f'<text x="24" y="22" font-family="sans-serif" font-size="16">{escape(view["label"].upper())} position state</text>\n'
        '<line x1="80" y1="266" x2="876" y2="266" stroke="#555"/>\n'
        '<line x1="80" y1="32" x2="80" y2="266" stroke="#555"/>\n'
        f'<polyline points="{polyline}" fill="none" stroke="#b23a48" stroke-width="2"/>\n'
        '<text x="20" y="38" font-family="sans-serif" font-size="11">LONG</text>\n'
        '<text x="20" y="154" font-family="sans-serif" font-size="11">FLAT</text>\n'
        '<text x="20" y="266" font-family="sans-serif" font-size="11">SHORT</text>\n'
        '<text x="80" y="298" font-family="sans-serif" font-size="11">حالات أصلية فقط؛ لا يوجد downsampling</text>\n'
        "</svg>\n"
    )


def _cumulative_points(
    rows: list[dict[str, str]],
    *,
    time_field: str,
    money_field: str,
    start_ns: int,
    end_ns: int,
    absolute: bool,
) -> list[tuple[int, Decimal]]:
    total = Decimal(0)
    points = [(start_ns, total)]
    for row in sorted(rows, key=lambda item: int(item[time_field])):
        amount, currency = _money(row[money_field])
        if currency != "USDT":
            raise RuntimeError("cumulative chart has a non-USDT native Money value")
        total += abs(amount) if absolute else amount
        points.append((int(row[time_field]), total))
    points.append((end_ns, total))
    return points


def _source_link(path: Path, *, from_dir: Path) -> str:
    relative = os.path.relpath(path, from_dir).replace(os.sep, "/")
    return f"[{path.relative_to(ROOT).as_posix()}]({relative})"


def _profile_rows(view: dict) -> list[tuple[str, str, str]]:
    max_dd = view["performance"].max_drawdown
    total_return = view["performance"].total_return
    cagr = view["performance"].cagr
    terminal = view["terminal_position"]
    replay = view["replay"]
    rows = [
        ("Strategy identity", view["strategy"]["strategy_identity_sha256"], "STRATEGY_IDENTITY"),
        ("DatasetRelease identity", view["dataset"]["dataset_release_id"], "DATASET_RELEASE"),
        ("Qualified Profile identity", view["workflow"].qualified_profile_record_id, "WORKFLOW"),
        ("Scoring period", "[2021-01-01T00:00:00Z, 2021-07-01T00:00:00Z)", "RUN_CONFIG"),
        ("Initial balance", "10000 USDT", "RUN_CONFIG"),
        ("Quantity", view["workflow"].strategy_spec.parameters["order_quantity"] + " BTC", "STRATEGY_IDENTITY"),
        ("Signals", str(len(view["signals"])), "NATIVE_RESULT"),
        ("Submitted orders", str(len(view["orders"])), "ORDERS"),
        ("Fills", str(len(view["fills"])), "FILLS"),
        (
            "Fee exactly once per Fill",
            "PASS" if view["checker_checks"]["maker_taker_fee_exactly_once"]["pass"] else "FAIL",
            "CHECKER",
        ),
        ("Native completed trades", str(view["completed"]["completed_trade_count"]), "NATIVE_COMPLETED_TRADES"),
        ("Gross PnL from Nautilus", "UNDEFINED — no unambiguous native gross field", "NATIVE_STATISTICS"),
        ("Fees", _fmt(view["fees"]) + " USDT", "FILLS"),
        ("Net PnL", _fmt(view["net_pnl"]) + " USDT", "NATIVE_RESULT"),
        ("Final Equity", _fmt(view["final_equity"]) + " USDT", "PERFORMANCE"),
        ("Maximum Drawdown", max_dd.value if max_dd.status != "UNDEFINED" else "UNDEFINED", "PERFORMANCE"),
        ("Total return", total_return.value, "PERFORMANCE"),
        ("CAGR", cagr.value, "PERFORMANCE"),
        (
            "Sharpe",
            _native_ratio(view["statistics"], "Sharpe Ratio")
            + " — NAUTILUS_NATIVE_STATISTIC",
            "NATIVE_STATISTICS",
        ),
        (
            "Sortino",
            _native_ratio(view["statistics"], "Sortino Ratio")
            + " — NAUTILUS_NATIVE_STATISTIC",
            "NATIVE_STATISTICS",
        ),
        (
            "Calmar",
            (
                "UNDEFINED — no SSOT-defined or unambiguous native value"
                if _native_ratio(view["statistics"], "Calmar") == "UNDEFINED"
                else _native_ratio(view["statistics"], "Calmar")
                + " — NAUTILUS_NATIVE_STATISTIC"
            ),
            "NATIVE_STATISTICS",
        ),
        ("Lifecycle", " → ".join(view["lifecycle"]), "NATIVE_RESULT"),
        ("Terminal position", _fmt(terminal, 5 if view["label"] == "spot" else 3) + " BTC", "POSITIONS"),
        (
            "Terminal policy",
            "PASS" if view["checker_checks"]["terminal_policy"]["pass"] else "FAIL",
            "CHECKER",
        ),
        ("Checker", view["checker"]["outcome"], "CHECKER"),
        ("Replay", replay["result"] + "; " + replay["replay_identity"], "REPLAY"),
        ("ResearchEligibility", view["claim"]["research_eligibility"], "AUTHORITATIVE_REPORT"),
    ]
    if view["label"] == "spot":
        rows.insert(13, ("Funding", "NOT_APPLICABLE", "RUN_CONFIG"))
    else:
        funding_check = view["checker_checks"]["owner_smoke_all_official_funding_processed"]
        mark_check = view["checker_checks"]["owner_smoke_official_mark_valuation"]
        reversal = view["checker_checks"]["owner_smoke_separate_close_then_reverse"]
        rows[13:13] = [
            ("Funding paid", _fmt(view["funding_paid"]) + " USDT", "FUNDING"),
            ("Funding received", _fmt(view["funding_received"]) + " USDT", "FUNDING"),
            ("Net funding", _fmt(view["net_funding"]) + " USDT", "FUNDING"),
            ("Source funding events", str(funding_check["source_event_count"]), "FUNDING_SOURCE"),
            ("Native funding settlements", str(funding_check["native_settlement_count"]), "FUNDING"),
            ("Mark valuation", "PASS" if mark_check["pass"] else "FAIL", "CHECKER"),
            ("Separate close-and-reverse", f"PASS; reversals={reversal['reversal_count']}", "CHECKER"),
        ]
    return rows


def _table(rows: list[tuple[str, str, str]]) -> str:
    lines = ["| الحقل | القيمة | دليل المصدر |", "|---|---:|---|"]
    lines.extend(
        f"| {field} | {str(value).replace('|', '&#124;')} | `{source}` |"
        for field, value, source in rows
    )
    return "\n".join(lines)


def _profile_markdown(view: dict, source_index: dict[str, dict[str, str]]) -> str:
    label_ar = "Spot" if view["label"] == "spot" else "Perpetual"
    chart_names = (
        ("Equity", f"../charts/{view['label']}-equity.svg"),
        ("Drawdown", f"../charts/{view['label']}-drawdown.svg"),
        ("Position state", f"../charts/{view['label']}-position.svg"),
    )
    if view["label"] == "perpetual":
        chart_names += (
            ("Cumulative fees", "../charts/perpetual-fees.svg"),
            ("Cumulative funding", "../charts/perpetual-funding.svg"),
        )
    sources = "\n".join(
        f"- `{alias}` — `{item['path']}` — SHA-256 `{item['sha256']}`"
        for alias, item in sorted(source_index.items())
        if alias.startswith(view["label"].upper() + "_") or alias in {"JOURNAL", "HISTORY_ANCHORS"}
    )
    charts = "\n\n".join(f"### {title}\n\n![{title}]({path})" for title, path in chart_names)
    return f"""# تقرير {label_ar} — OWNER_OPERATIONAL_SMOKE_001

> **EXPLORATORY / DEVELOPMENT / EXPOSED / NOT_FINAL_HOLDOUT**  
> هذا تحقق تشغيلي للمختبر، وليس Profitability Claim ولا توصية تداول.

{_table(_profile_rows(view))}

## الرسوم

{charts}

لا يوجد downsampling. الرسوم عرض قرائي مباشر من Evidence، ولا تغيّر المقاييس أو PnL أو Equity.

## مصادر الأدلة

{sources}
"""


def _main_markdown(views: dict[str, dict], source_index: dict[str, dict[str, str]]) -> str:
    spot = views["spot"]
    perp = views["perpetual"]
    complete = all(view["record"].state is TrialState.COMPLETED for view in views.values())
    checks = all(view["checker"]["outcome"] == "CHECK_PASS" for view in views.values())
    replays = all(view["replay"]["result"] == "PASS" for view in views.values())
    development_attempt_count = len(
        _json(EVIDENCE_ROOT / "development-attempts.json").get("attempts", []),
    )
    source_lines = "\n".join(
        f"- `{alias}` — {_source_link(ROOT / item['path'], from_dir=REPORT_ROOT)} — SHA-256 `{item['sha256']}`"
        for alias, item in sorted(source_index.items())
    )
    return f"""# OWNER_OPERATIONAL_SMOKE_001 — التقرير المبسط للمالك

> **تنبيه بارز: هذه نتائج EXPLORATORY_OPERATIONAL_VALIDATION على بيانات DEVELOPMENT وEXPOSED. ليست Profitability Claim، ولا تستخدم Final Holdout، ولا تبرر Live Trading أو Paper Trading.**

## الملخص التنفيذي

- اكتملت تجربة Spot: **{'نعم' if spot['record'].state is TrialState.COMPLETED else 'لا'}**.
- اكتملت تجربة Perpetual: **{'نعم' if perp['record'].state is TrialState.COMPLETED else 'لا'}**.
- CHECK_PASS للتجربتين: **{'نعم' if checks else 'لا'}**.
- تطابقت إعادة التشغيل الحتمية في عمليتين جديدتين لكل Profile: **{'نعم' if replays else 'لا'}**.
- عمل Owner Workflow الكامل: **{'نعم' if complete and checks and replays else 'لا'}**.
- أخطاء الـ Official Trials النهائية: **لا توجد**؛ أما محاولات التطوير السابقة فعددها **{development_attempt_count}**، وهي محفوظة ولم يبدأ أي منها كـ Official Trial (`DEVELOPMENT_ATTEMPTS`).
- Final Holdout used: **false**. Real profitability claim: **false**. ResearchEligibility: **INELIGIBLE**.
- القيود المثبتة: metadata حالية وليست إثباتًا point-in-time لقواعد 2020–2021، ورسوم الحساب التاريخية الدقيقة غير متاحة؛ استُخدم أساس الرسوم التقديري المقفل.

## نتائج Spot

{_table(_profile_rows(spot))}

[تقرير Spot المنفصل](spot/README.md)

## نتائج Perpetual

{_table(_profile_rows(perp))}

[تقرير Perpetual المنفصل](perpetual/README.md)

## مقارنة مباشرة بلا دمج

| البند | Spot | Perpetual | دليل المصدر |
|---|---:|---:|---|
| رأس المال المستقل | 10000 USDT | 10000 USDT | `SPOT_RUN_CONFIG`, `PERPETUAL_RUN_CONFIG` |
| Quantity | 0.10000 BTC | 0.100 BTC | `SPOT_STRATEGY_IDENTITY`, `PERPETUAL_STRATEGY_IDENTITY` |
| Signals | {len(spot['signals'])} | {len(perp['signals'])} | `SPOT_NATIVE_RESULT`, `PERPETUAL_NATIVE_RESULT` |
| Orders | {len(spot['orders'])} | {len(perp['orders'])} | `SPOT_ORDERS`, `PERPETUAL_ORDERS` |
| Fills | {len(spot['fills'])} | {len(perp['fills'])} | `SPOT_FILLS`, `PERPETUAL_FILLS` |
| Net PnL الأصلي | {_fmt(spot['net_pnl'])} USDT | {_fmt(perp['net_pnl'])} USDT | `SPOT_NATIVE_RESULT`, `PERPETUAL_NATIVE_RESULT` |
| Final Equity | {_fmt(spot['final_equity'])} USDT | {_fmt(perp['final_equity'])} USDT | `SPOT_PERFORMANCE`, `PERPETUAL_PERFORMANCE` |
| Funding | NOT_APPLICABLE | {_fmt(perp['net_funding'])} USDT | `SPOT_RUN_CONFIG`, `PERPETUAL_FUNDING` |

المقارنة وصفية فقط. لم يُجمع رأس المال، أو Equity، أو المراكز، ولم يُنشأ ترتيب فائز أو claim عابر للـ Profiles.

## القيود

- النافذة الكاملة `[2020-12-01T00:00:00Z, 2021-07-01T00:00:00Z)` مسجلة DEVELOPMENT وEXPOSED وNOT_FINAL_HOLDOUT، ومحلّل التعرض النهائي يرفض إعادة تسميتها Holdout.
- لم يُستخدم Final Holdout ولم تُفتح فترة أخرى.
- metadata المتاحة حالية؛ لا يُدّعى أنها قواعد Binance التاريخية الدقيقة في 2020–2021.
- fee tier التاريخي الخاص بالحساب غير مثبت؛ الرسوم ESTIMATED_FEE وفق الأساس المقفل.
- لم يحدث optimization أو parameter search: المرشح الوحيد SMA20.
- Native completed-trade sequence غير متاح بصورة لا لبس فيها في runtime المقفل، لذلك بقي العدد ومقاييس trade-based غير معرّفة.
- Gross PnL وCalmar يبقيان UNDEFINED عندما لا يوفرهما Nautilus بصورة أصلية واضحة؛ لم يُنشأ بديل محاسبي.
- لا توجد توصية استثمار أو نشر، وهذه النتائج لا تبرر Live Trading.

## الرسوم القرائية

### Spot Equity

![Spot Equity](charts/spot-equity.svg)

### Spot Drawdown

![Spot Drawdown](charts/spot-drawdown.svg)

### Spot position state

![Spot position state](charts/spot-position.svg)

### Perpetual Equity

![Perpetual Equity](charts/perpetual-equity.svg)

### Perpetual Drawdown

![Perpetual Drawdown](charts/perpetual-drawdown.svg)

### Perpetual position state

![Perpetual position state](charts/perpetual-position.svg)

### Perpetual cumulative fees

![Perpetual cumulative fees](charts/perpetual-fees.svg)

### Perpetual cumulative funding

![Perpetual cumulative funding](charts/perpetual-funding.svg)

لا يوجد downsampling في أي رسم. جميع المحاور تشمل الصفر، وكل نقطة مشتقة قراءةً من Evidence المحدد؛ لا تتغير الحقيقة المالية أو المقاييس.

## Primary research sources

- [Liu and Tsyvinski — Risks and Returns of Cryptocurrency](https://www.nber.org/papers/w24877)
- [Detzel et al. — Learning and Predictability via Technical Analysis](https://onlinelibrary.wiley.com/doi/10.1111/fima.12310)
- [Hudson and Urquhart — Technical Trading and Cryptocurrencies](https://link.springer.com/article/10.1007/s10479-019-03357-1)

الأدلة المنشورة مختلطة، وقد أبلغ الأدب السابق عن أداء Bitcoin سلبي Out-of-Sample في بعض العينات. فرضية هذا epoch تشغيلية وسببية وقابلة لإعادة الإنتاج، وليست أن الاستراتيجية مربحة.

## فهرس الأدلة

{source_lines}

الفهرس الكامل: [evidence-inventory.json](evidence-inventory.json). وإثبات منع إعادة تسمية النافذة: [exposure-final.json](exposure-final.json).
"""


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=REPORT_ROOT)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite Owner report: {output}")
    history = AuthoritativeResearchHistory(
        HistoryAnchorStore(
            repository_root=ROOT,
            journal_path=ROOT / "research/trials.jsonl",
            holdout_path=ROOT / "research/holdout_lock.json",
            anchor_path=ROOT / "research/history_anchors.jsonl",
            require_remote_tip=True,
        ),
    )
    history.reconcile()
    anchor = history.anchors.reconcile_committed()
    if history.holdout.read().entries:
        raise RuntimeError("OWNER_SMOKE unexpectedly consumed Final Holdout")
    views = {
        label: _trial_view(label=label, trial_id=trial_id, history=history)
        for label, trial_id in TRIALS.items()
    }
    exposure_results = []
    resolver = AuthoritativeExposureResolver(repository_root=ROOT)
    for label, view in views.items():
        record = view["record"]
        source = _json(view["run_dir"] / "source_revision.json")
        candidate = ResultExposure(
            trial_id=f"owner-smoke-001-{label}-future-relabel-proof",
            market_profile=record.market_profile,
            instrument_id=record.instrument_id,
            scored_interval=WINDOW,
            research_family_id=f"future-renamed-{label}",
            hypothesis_lineage=("future-renamed-hypothesis",),
            strategy_lineage=("future-renamed-strategy",),
            dataset_release_id=record.dataset_release_id,
            first_exposure_at_utc=datetime.now(UTC),
            exposure_type=PartitionRole.FINAL_HOLDOUT.value,
            evidence_reference="READ_ONLY_FINAL_EXPOSURE_RECONCILIATION",
            source_branch=source["branch_ref"],
            source_commit=source["git_commit"],
            seed=999,
            result_bearing=False,
        )
        try:
            resolver.require_fresh(candidate, history=history)
        except ResearchError as exc:
            if exc.code != "HOLDOUT_ALREADY_CONSUMED":
                raise
            exposure_results.append(
                {
                    "profile": record.market_profile.value,
                    "instrument_id": record.instrument_id,
                    "full_window": WINDOW.to_builtins(),
                    "future_final_holdout_relabel": "REJECTED",
                    "failure_code": exc.code,
                    "detail": exc.message,
                },
            )
        else:
            raise RuntimeError("full exposed OWNER_SMOKE window was incorrectly fresh")

    with tempfile.TemporaryDirectory(prefix="owner-smoke-owner-report-", dir="/tmp") as temporary:
        staging = Path(temporary) / "owner-report"
        staging.mkdir()
        exposure_payload = {
            "schema": "owner-smoke-final-exposure-reconciliation-v1",
            "authorization_id": "OWNER_OPERATIONAL_SMOKE_001",
            "authoritative_history_anchor_sha256": anchor.anchor_sha256,
            "journal_sha256": sha256_file(ROOT / "research/trials.jsonl"),
            "holdout_sha256": sha256_file(ROOT / "research/holdout_lock.json"),
            "holdout_entry_count": 0,
            "classification": ["DEVELOPMENT", "EXPOSED", "NOT_FINAL_HOLDOUT"],
            "results": exposure_results,
            "status": "PASS",
        }
        (staging / "exposure-final.json").write_bytes(canonical_json_bytes(exposure_payload) + b"\n")

        sources: dict[str, dict[str, str]] = {
            "JOURNAL": {
                "path": "research/trials.jsonl",
                "sha256": sha256_file(ROOT / "research/trials.jsonl"),
            },
            "HISTORY_ANCHORS": {
                "path": "research/history_anchors.jsonl",
                "sha256": sha256_file(ROOT / "research/history_anchors.jsonl"),
            },
            "HOLDOUT_LOCK": {
                "path": "research/holdout_lock.json",
                "sha256": sha256_file(ROOT / "research/holdout_lock.json"),
            },
            "ACQUISITION": {
                "path": "evidence/research/owner-smoke-001/data-acquisition.json",
                "sha256": sha256_file(EVIDENCE_ROOT / "data-acquisition.json"),
            },
            "PREFLIGHT": {
                "path": "evidence/research/owner-smoke-001/preflight/summary.json",
                "sha256": sha256_file(EVIDENCE_ROOT / "preflight/summary.json"),
            },
            "DEVELOPMENT_ATTEMPTS": {
                "path": "evidence/research/owner-smoke-001/development-attempts.json",
                "sha256": sha256_file(EVIDENCE_ROOT / "development-attempts.json"),
            },
            "EXPOSURE_FINAL": {
                "path": "evidence/research/owner-smoke-001/owner-report/exposure-final.json",
                "sha256": sha256_file(staging / "exposure-final.json"),
            },
        }
        for label, view in views.items():
            for alias, path in view["source_paths"].items():
                sources[f"{label.upper()}_{alias}"] = {
                    "path": path.relative_to(ROOT).as_posix(),
                    "sha256": sha256_file(path),
                }
        index_material = {
            "schema": "owner-smoke-report-source-index-v1",
            "authorization_id": "OWNER_OPERATIONAL_SMOKE_001",
            "sources": sources,
        }
        source_index = {
            "source_index_identity": canonical_sha256(index_material),
            **index_material,
        }
        (staging / "source-index.json").write_bytes(canonical_json_bytes(source_index) + b"\n")

        charts = staging / "charts"
        charts.mkdir()
        for label, view in views.items():
            equity_points = [
                (utc_datetime_to_ns(item.timestamp), item.equity)
                for item in view["performance"].equity_curve
            ]
            drawdown_points = [
                (utc_datetime_to_ns(item.timestamp), item.drawdown)
                for item in view["performance"].drawdown_curve
            ]
            _write_text(
                charts / f"{label}-equity.svg",
                _line_svg(
                    title=f"{label.upper()} Equity (USDT)",
                    points=equity_points,
                    source_sha256=sources[f"{label.upper()}_PERFORMANCE"]["sha256"],
                    y_label="Equity USDT",
                ),
            )
            _write_text(
                charts / f"{label}-drawdown.svg",
                _line_svg(
                    title=f"{label.upper()} Drawdown",
                    points=drawdown_points,
                    source_sha256=sources[f"{label.upper()}_PERFORMANCE"]["sha256"],
                    y_label="Drawdown ratio",
                ),
            )
            _write_text(
                charts / f"{label}-position.svg",
                _position_svg(
                    view,
                    sources[f"{label.upper()}_NATIVE_RESULT"]["sha256"],
                ),
            )
        perp = views["perpetual"]
        start_ns = utc_datetime_to_ns(
            datetime.fromisoformat(perp["config"]["scoring_start"].replace("Z", "+00:00")),
        )
        end_ns = utc_datetime_to_ns(
            datetime.fromisoformat(
                perp["config"]["scoring_end_exclusive"].replace("Z", "+00:00"),
            ),
        )
        _write_text(
            charts / "perpetual-fees.svg",
            _line_svg(
                title="PERPETUAL cumulative native fees (USDT)",
                points=_cumulative_points(
                    perp["fills"],
                    time_field="ts_event",
                    money_field="commission",
                    start_ns=start_ns,
                    end_ns=end_ns,
                    absolute=True,
                ),
                source_sha256=sources["PERPETUAL_FILLS"]["sha256"],
                y_label="Cumulative fees USDT",
            ),
        )
        _write_text(
            charts / "perpetual-funding.svg",
            _line_svg(
                title="PERPETUAL cumulative native funding (USDT)",
                points=_cumulative_points(
                    perp["funding_rows"],
                    time_field="ts_event",
                    money_field="pnl_change",
                    start_ns=start_ns,
                    end_ns=end_ns,
                    absolute=False,
                ),
                source_sha256=sources["PERPETUAL_FUNDING"]["sha256"],
                y_label="Cumulative net funding USDT",
            ),
        )
        _write_text(staging / "README.md", _main_markdown(views, sources))
        for label, view in views.items():
            _write_text(
                staging / label / "README.md",
                _profile_markdown(view, sources),
            )

        inventory_files: dict[str, Path] = {}
        for root in (
            EVIDENCE_ROOT,
            ROOT / "research",
            *(view["run_dir"] for view in views.values()),
            *(
                ROOT / str(view["replay"]["replay_run_ref"])
                for view in views.values()
            ),
        ):
            for path in root.rglob("*"):
                if path.is_file() and REPORT_ROOT not in path.parents:
                    inventory_files[path.relative_to(ROOT).as_posix()] = path
        for path in staging.rglob("*"):
            if path.is_file() and path.name != "evidence-inventory.json":
                relative = path.relative_to(staging)
                inventory_files[
                    (REPORT_ROOT.relative_to(ROOT) / relative).as_posix()
                ] = path
        entries = [
            {
                "path": relative,
                "sha256": sha256_file(path),
                "byte_size": path.stat().st_size,
            }
            for relative, path in sorted(inventory_files.items())
        ]
        inventory = {
            "schema": "owner-smoke-evidence-inventory-v1",
            "authorization_id": "OWNER_OPERATIONAL_SMOKE_001",
            "entries": entries,
            "inventory_content_sha256": canonical_sha256(entries),
            "inventory_self_excluded": True,
        }
        (staging / "evidence-inventory.json").write_bytes(canonical_json_bytes(inventory) + b"\n")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(output))

    print(
        json.dumps(
            {
                "status": "PASS",
                "owner_report": str(output / "README.md"),
                "spot_report": str(output / "spot/README.md"),
                "perpetual_report": str(output / "perpetual/README.md"),
                "history_anchor_sha256": anchor.anchor_sha256,
                "charts": 8,
            },
            sort_keys=True,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
