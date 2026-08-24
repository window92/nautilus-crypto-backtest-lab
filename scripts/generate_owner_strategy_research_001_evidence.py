#!/usr/bin/env python3
"""Validate and publish additive evidence for OWNER_STRATEGY_RESEARCH_001."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from crypto_lab.checker import check_evidence_directory
from crypto_lab.diagnostics import derive_performance_diagnostics
from crypto_lab.hashing import canonical_sha256
from crypto_lab.native_metrics import qualify_native_calmar
from crypto_lab.native_positions import NativeCompletedPositionSequence
from crypto_lab.reporting import PerformanceDiagnostics
from crypto_lab.reporting import generate_native_research_metrics_readiness
from crypto_lab.research import CompletedTradeSeries
from crypto_lab.research import ResearchProtocol
from crypto_lab.research import SampleAdequacy
from crypto_lab.research import evaluate_sample_adequacy


ROOT = Path(__file__).resolve().parents[1]
EPOCH = ROOT / "evidence/research/owner-strategy-research-001"
BASELINE_COMMIT = "621caa3d71106f85f10015c54d0e31e75e0d42cd"
EXPECTED_LOCKS = {
    "SSOT.md": "b4deb7048242239234de7eaa353b623b3e45247eb42f1021dbc26ffd910edb99",
    "runtime.lock.json": "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd",
    "requirements.lock.txt": "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47",
}
SPOT_RELEASE = "fd8542c109cfbf7d6b19d5b7bbb7705c6a161efc807695f3671978c381e34eca"
PERPETUAL_RELEASE = "b6c8f5d659f3441c924b613d770342796c90b90a970f42a3dc8227c856198917"
SPOT_CATALOG = "db0971d28caba547378e3acba5ad8df1cbd0d6d5be963d153248928a729e374f"
PERPETUAL_CATALOG = "7c96897a8e1ea3c02198238a277fb8c3d995f54dd90dc381e534a5f21b017ae0"
DUCKDB_SEMANTIC_IDENTITY = (
    "11329c1497ff6bf3a68c5d3ba994f5ac2bbd0ece51cf489f9fa3f681a01ecbff"
)
SPOT_PROTOCOL = "2b0239a69faff825c7c35aa0dd372d112a49973d8076aeaf62e25f1e2abb4a12"
PERPETUAL_PROTOCOL = "fc5d79a9324178cb0740e50832bb53f1f3b53ed996add829dc8d8ab3fc7daf84"
SCORING_START_NS = 1_612_137_600_000_000_000
SCORING_END_NS = 1_627_776_000_000_000_000
RESEARCH_FAMILY = "BTCUSDT_WEEKLY_TSMOM28_V1"
RUNS: dict[str, dict[str, str]] = {
    "spot_benchmark": {
        "profile_group": "spot",
        "kind": "BENCHMARK",
        "name": "BUY_AND_HOLD_1X_V1",
        "registration_id": "buy_and_hold_1x_v1",
        "trial_id": "owner-strategy-research-001-spot-benchmark-buy-and-hold-1x-development",
        "run_id": "owner-strategy-research-001-spot-benchmark-run",
        "run_dir": "runs/owner-strategy-research-001-spot-benchmark-run-ef60cf17606c",
        "protocol_id": SPOT_PROTOCOL,
        "release_id": SPOT_RELEASE,
        "catalog_id": SPOT_CATALOG,
    },
    "spot_candidate_a": {
        "profile_group": "spot",
        "kind": "CANDIDATE_A",
        "name": "TSMOM28_FULL_NOTIONAL",
        "registration_id": "btcusdt_weekly_tsmom28_full_v1",
        "trial_id": "owner-strategy-research-001-spot-candidate-a-development-retry-001",
        "run_id": "owner-strategy-research-001-spot-candidate-a-run-retry-001",
        "run_dir": "runs/owner-strategy-research-001-spot-candidate-a-run-retry-001-f1e2c8bc7b40",
        "protocol_id": SPOT_PROTOCOL,
        "release_id": SPOT_RELEASE,
        "catalog_id": SPOT_CATALOG,
    },
    "spot_candidate_b": {
        "profile_group": "spot",
        "kind": "CANDIDATE_B",
        "name": "TSMOM28_VOLATILITY_TARGET_20",
        "registration_id": "btcusdt_weekly_tsmom28_vol20_v1",
        "trial_id": "owner-strategy-research-001-spot-candidate-b-development",
        "run_id": "owner-strategy-research-001-spot-candidate-b-run",
        "run_dir": "runs/owner-strategy-research-001-spot-candidate-b-run-91f36cf4151c",
        "protocol_id": SPOT_PROTOCOL,
        "release_id": SPOT_RELEASE,
        "catalog_id": SPOT_CATALOG,
    },
    "perpetual_benchmark": {
        "profile_group": "perpetual",
        "kind": "BENCHMARK",
        "name": "BUY_AND_HOLD_1X_V1",
        "registration_id": "buy_and_hold_1x_v1",
        "trial_id": "owner-strategy-research-001-perpetual-benchmark-buy-and-hold-1x-development",
        "run_id": "owner-strategy-research-001-perpetual-benchmark-run",
        "run_dir": "runs/owner-strategy-research-001-perpetual-benchmark-run-4d2108bc43f7",
        "protocol_id": PERPETUAL_PROTOCOL,
        "release_id": PERPETUAL_RELEASE,
        "catalog_id": PERPETUAL_CATALOG,
    },
    "perpetual_candidate_a": {
        "profile_group": "perpetual",
        "kind": "CANDIDATE_A",
        "name": "TSMOM28_FULL_NOTIONAL",
        "registration_id": "btcusdt_weekly_tsmom28_full_v1",
        "trial_id": "owner-strategy-research-001-perpetual-candidate-a-development",
        "run_id": "owner-strategy-research-001-perpetual-candidate-a-run",
        "run_dir": "runs/owner-strategy-research-001-perpetual-candidate-a-run-7c03f28261fe",
        "protocol_id": PERPETUAL_PROTOCOL,
        "release_id": PERPETUAL_RELEASE,
        "catalog_id": PERPETUAL_CATALOG,
    },
    "perpetual_candidate_b": {
        "profile_group": "perpetual",
        "kind": "CANDIDATE_B",
        "name": "TSMOM28_VOLATILITY_TARGET_20",
        "registration_id": "btcusdt_weekly_tsmom28_vol20_v1",
        "trial_id": "owner-strategy-research-001-perpetual-candidate-b-development",
        "run_id": "owner-strategy-research-001-perpetual-candidate-b-run",
        "run_dir": "runs/owner-strategy-research-001-perpetual-candidate-b-run-d61049dfda6b",
        "protocol_id": PERPETUAL_PROTOCOL,
        "release_id": PERPETUAL_RELEASE,
        "catalog_id": PERPETUAL_CATALOG,
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


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


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


def git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
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


def money(value: str) -> tuple[Decimal, str]:
    parts = value.split()
    if len(parts) != 2:
        raise RuntimeError(f"invalid native Money spelling: {value!r}")
    amount = Decimal(parts[0])
    if not amount.is_finite():
        raise RuntimeError("non-finite native Money value")
    return amount, parts[1]


def metric(value: dict[str, Any]) -> str:
    return str(value["value"])


def exposure(rows: list[dict[str, str]]) -> tuple[int, str]:
    events = [row for row in rows if row["row_type"] != "FINAL_NATIVE_POSITION"]
    events.sort(key=lambda row: (int(row["ts_event"]), int(row["event_index"])))
    cursor = SCORING_START_NS
    signed_quantity = Decimal(0)
    active_ns = 0
    for row in events:
        timestamp = max(SCORING_START_NS, min(SCORING_END_NS, int(row["ts_event"])))
        if timestamp > cursor and signed_quantity != 0:
            active_ns += timestamp - cursor
        cursor = max(cursor, timestamp)
        signed_quantity = Decimal(row["signed_qty"])
    if cursor < SCORING_END_NS and signed_quantity != 0:
        active_ns += SCORING_END_NS - cursor
    ratio = Decimal(active_ns) / Decimal(SCORING_END_NS - SCORING_START_NS)
    return active_ns, f"{ratio:.12f}"


def completed_series(
    sequence: NativeCompletedPositionSequence,
    path: Path,
) -> CompletedTradeSeries:
    return CompletedTradeSeries(
        source="NAUTILUS_NATIVE_COMPLETED_TRADES",
        evidence_sha256=sha256_file(path),
        settlement_currency=sequence.settlement_currency,
        stable_native_sequence=True,
        native_completed_unit_count=sequence.completed_trade_count,
        realized_pnl_outcomes=tuple(unit.realized_pnl for unit in sequence.units),
        realized_returns=sequence.realized_returns,
        unambiguous_net_after_cost=sequence.unambiguous_net_after_cost,
        net_outcomes=sequence.net_outcomes,
    )


def _check_by_name(checker: dict[str, Any], name: str) -> dict[str, Any]:
    values = [item for item in checker["checks"] if item["name"] == name]
    if len(values) != 1:
        raise RuntimeError(f"checker has {len(values)} occurrences of {name}")
    return values[0]


def validate_run(key: str, spec: dict[str, str]) -> dict[str, Any]:
    run_dir = ROOT / spec["run_dir"]
    protocol_path = ROOT / "research/protocols" / f"{spec['protocol_id']}.json"
    replay_path = ROOT / "research/replays" / f"{spec['trial_id']}.json"
    report_path = ROOT / "research/reports" / f"{spec['trial_id']}.json"
    performance_path = ROOT / "research/performance" / f"{spec['run_id']}.json"
    workflow_path = ROOT / "research/workflows" / f"{spec['trial_id']}.json"
    required_paths = {
        "run_config": run_dir / "lab_run_config.json",
        "dataset_release": run_dir / "dataset_release.json",
        "strategy_spec": run_dir / "strategy_spec.json",
        "strategy_identity": run_dir / "strategy_identity.json",
        "source_revision": run_dir / "source_revision.json",
        "status": run_dir / "status.json",
        "checker": run_dir / "checker.json",
        "native_result": run_dir / "nautilus_result.json",
        "native_statistics": run_dir / "native_statistics.json",
        "native_completed_positions": run_dir / "native_completed_trades.json",
        "orders": run_dir / "orders.csv",
        "fills": run_dir / "fills.csv",
        "positions": run_dir / "positions.csv",
        "performance": performance_path,
        "protocol": protocol_path,
        "workflow": workflow_path,
        "replay": replay_path,
        "report": report_path,
    }
    if spec["profile_group"] == "perpetual":
        required_paths["funding"] = run_dir / "funding.csv"
        required_paths["funding_source"] = run_dir / "funding_source.json"
    missing = [role for role, path in required_paths.items() if not path.is_file()]
    if missing:
        raise RuntimeError(f"{key} missing evidence roles: {','.join(missing)}")

    status = load(required_paths["status"])
    checker = load(required_paths["checker"])
    regenerated_checker = check_evidence_directory(
        run_dir,
        repository_root=ROOT,
        official_source_required=True,
        source_revision_current_head_required=False,
    ).to_builtins()
    replay = load(replay_path)
    report = load(report_path)
    workflow = load(workflow_path)
    result = load(required_paths["native_result"])
    statistics = load(required_paths["native_statistics"])
    release = load(required_paths["dataset_release"])
    strategy_identity = load(required_paths["strategy_identity"])
    source_revision = load(required_paths["source_revision"])
    protocol = ResearchProtocol.from_json_bytes(protocol_path.read_bytes())
    performance = PerformanceDiagnostics.from_json_bytes(performance_path.read_bytes())
    benchmark = spec["kind"] == "BENCHMARK"
    regenerated_performance = derive_performance_diagnostics(
        run_dir=run_dir,
        protocol=protocol,
        benchmark_directory=ROOT / "research/benchmarks",
        resolve_benchmark=not benchmark,
    )
    if performance != regenerated_performance:
        raise RuntimeError(f"{key} performance diagnostics are stale")
    if (
        status.get("state") != "COMPLETED"
        or status.get("checker_outcome") != "CHECK_PASS"
        or status.get("failure_codes") != []
        or checker != regenerated_checker
        or checker.get("outcome") != "CHECK_PASS"
        or replay.get("result") != "PASS"
        or replay.get("primary_checker") != "CHECK_PASS"
        or replay.get("replay_checker") != "CHECK_PASS"
        or replay.get("primary_semantic_digest") != replay.get("replay_semantic_digest")
        or report["claim_evaluation"]["mechanical_integrity"] != "PASS"
        or report["claim_evaluation"]["research_eligibility"] != "INELIGIBLE"
        or report["json_payload"]["profitability_claim_is_real"] is not False
        or workflow["dataset_release_id"] != spec["release_id"]
        or workflow["registered_strategy_id"] != spec["registration_id"]
        or workflow["protocol"]["protocol_id"] != spec["protocol_id"]
        or release["dataset_release_id"] != spec["release_id"]
        or strategy_identity["registration_id"] != spec["registration_id"]
        or result["run_id"] != spec["run_id"]
        or result["engine_completed"] is not True
        or result["engine_error"] is not None
        or result["preflight_failure_codes"] != []
        or result["project_fee_postings"] != 0
        or result["project_funding_postings"] != 0
        or result["project_financial_ledger"] is not False
        or result["mark_fallback_accepted"] is not False
    ):
        raise RuntimeError(f"{key} immutable Official result contract mismatch")
    if protocol.protocol_id != spec["protocol_id"]:
        raise RuntimeError(f"{key} protocol identity mismatch")
    if protocol.multiple_testing_treatment != "HOLM_BONFERRONI":
        raise RuntimeError(f"{key} multiple-testing contract changed")
    if (
        protocol.search_budget != 2
        or len(protocol.ordered_candidates) != 2
        or protocol.sample_adequacy_rule.minimum_completed_trades != "NOT_APPLICABLE"
        or protocol.monte_carlo_spec.resampling_method.value != "NOT_APPLICABLE"
    ):
        raise RuntimeError(f"{key} frozen exploratory budget/metrics contract changed")

    market = _check_by_name(checker, "nautilus_executable_market_state_acceptance")
    market_validation = market["validation"]
    order_market = _check_by_name(checker, "orders_reach_executable_market_state")
    if (
        not market["pass"]
        or market_validation["catalog_identity"] != spec["catalog_id"]
        or market_validation["precision_skipped_bars"] != 0
        or market_validation["rejected_precision_events"] != 0
        or market_validation["missing_market_state"] != 0
        or market_validation["fatal_runtime_diagnostics"] != 0
        or order_market["no_market_rejection_count"] != 0
        or order_market["rejected_order_count"] != 0
        or not _check_by_name(checker, "causal_fills")["pass"]
        or not _check_by_name(checker, "maker_taker_fee_exactly_once")["pass"]
    ):
        raise RuntimeError(f"{key} market-state/causality acceptance failed")
    network = result["network_guard"]
    if (
        network["attempts"]
        or network["enforced"] is not True
        or network["process_isolation"]["external_endpoint_contacted"] is not False
        or network["process_isolation"]["child_dns_probe_blocked"] is not True
        or network["process_isolation"]["child_native_probe_blocked"] is not True
    ):
        raise RuntimeError(f"{key} process offline boundary failed")

    observations = result["strategy_observations"]
    if benchmark:
        signals = len(observations["benchmark_entries"])
        if signals != 1:
            raise RuntimeError(f"{key} benchmark entry count changed")
        completed_daily_bars: int | str = "NOT_APPLICABLE_BENCHMARK"
        weekly_decisions: int | str = "NOT_APPLICABLE_BENCHMARK"
    else:
        weekly = _check_by_name(checker, "weekly_tsmom28_daily_causality")
        completed_daily_bars = int(weekly["daily_completed_bars"])
        weekly_decisions = int(weekly["actual_weekly_decisions"])
        signals = len(observations["signals"])
        if completed_daily_bars != 212 or weekly_decisions != signals or signals != 26:
            raise RuntimeError(f"{key} weekly signal schedule mismatch")

    completed_path = required_paths["native_completed_positions"]
    native_sequence = NativeCompletedPositionSequence.from_json_bytes(
        completed_path.read_bytes(),
    )
    completed = completed_series(native_sequence, completed_path)
    sample_adequacy = evaluate_sample_adequacy(protocol.sample_adequacy_rule, completed)
    if sample_adequacy is not SampleAdequacy.NOT_APPLICABLE:
        raise RuntimeError(f"{key} exploratory sample adequacy was retrofitted")
    returns = tuple(
        (int(row["ts_event"]), Decimal(str(row["return"])))
        for row in statistics["returns_series"]
    )
    calmar = qualify_native_calmar(
        returns=returns,
        returns_basis=statistics["returns_basis"],
        scored_start_ns=SCORING_START_NS,
        scoring_end_exclusive_ns=SCORING_END_NS,
    )
    readiness = generate_native_research_metrics_readiness(
        run_id=spec["run_id"],
        completed_trades=completed,
        sample_adequacy=sample_adequacy,
        native_calmar=calmar,
        terminal_open_position_excluded=True,
    )

    orders = read_csv(required_paths["orders"])
    fills = read_csv(required_paths["fills"])
    positions = read_csv(required_paths["positions"])
    if len(orders) != order_market["order_count"] or len(fills) != order_market["fill_count"]:
        raise RuntimeError(f"{key} order/fill checker cardinality mismatch")
    fee_values: list[Decimal] = []
    for row in fills:
        amount, currency = money(row["commission"])
        if currency != "USDT":
            raise RuntimeError(f"{key} non-USDT native commission")
        fee_values.append(amount)
    funding_rows = (
        read_csv(required_paths["funding"])
        if spec["profile_group"] == "perpetual"
        else []
    )
    funding_values: list[Decimal] = []
    for row in funding_rows:
        amount, currency = money(row["pnl_change"])
        if currency != "USDT" or row["adjustment_type"] != "FUNDING":
            raise RuntimeError(f"{key} invalid native funding record")
        funding_values.append(amount)
    net_pnl, net_currency = money(result["terminal_portfolio"]["total_pnl"])
    if net_currency != "USDT":
        raise RuntimeError(f"{key} non-USDT terminal PnL")
    active_ns, exposure_ratio = exposure(positions)
    position_events = [row for row in positions if row["row_type"] != "FINAL_NATIVE_POSITION"]
    opened = [row for row in position_events if row["row_type"] == "PositionOpened"]
    closed = [row for row in position_events if row["row_type"] == "PositionClosed"]
    terminal = [row for row in positions if row["row_type"] == "FINAL_NATIVE_POSITION"]
    if len(terminal) != 1:
        raise RuntimeError(f"{key} terminal native Position cardinality changed")
    stats_returns = statistics["stats_returns"]
    source_bindings = {
        role: binding(path)
        for role, path in sorted(required_paths.items())
    }
    metrics = {
        "signals_or_benchmark_entries": signals,
        "completed_daily_bars": completed_daily_bars,
        "weekly_decisions": weekly_decisions,
        "orders": len(orders),
        "fills": len(fills),
        "native_completed_units": native_sequence.completed_trade_count,
        "long_entries": sum(Decimal(row["signed_qty"]) > 0 for row in opened),
        "short_entries": sum(Decimal(row["signed_qty"]) < 0 for row in opened),
        "position_exits": len(closed),
        "gross_pnl": "UNDEFINED_NATIVE_GROSS_PNL_NOT_EXPOSED",
        "net_pnl": f"{net_pnl:.8f}",
        "fees": f"{sum(fee_values, Decimal(0)):.8f}",
        "funding": (
            f"{sum(funding_values, Decimal(0)):.8f}"
            if spec["profile_group"] == "perpetual"
            else "NOT_APPLICABLE"
        ),
        "funding_settlement_count": (
            len(funding_rows) if spec["profile_group"] == "perpetual" else 0
        ),
        "ending_equity": str(performance.equity_curve[-1].equity),
        "maximum_drawdown": metric(performance.max_drawdown.to_builtins()),
        "sharpe": str(stats_returns.get("Sharpe Ratio (252 days)", "UNDEFINED")),
        "sortino": str(stats_returns.get("Sortino Ratio (252 days)", "UNDEFINED")),
        "calmar": readiness.calmar.value,
        "calmar_status": readiness.calmar.status,
        "calmar_undefined_reason": readiness.calmar.undefined_reason,
        "profit_factor": str(stats_returns.get("Profit Factor", "UNDEFINED")),
        "native_returns_basis": statistics["returns_basis"],
        "win_rate_completed_units": performance.win_rate.value,
        "average_native_realized_pnl": readiness.average_trade_realized_pnl.value,
        "average_native_realized_return": readiness.average_trade_realized_return.value,
        "exposure_ratio": exposure_ratio,
        "exposure_active_ns": active_ns,
        "terminal_position": {
            "open": result["terminal_position_open"],
            "side": terminal[0]["side"],
            "signed_quantity": terminal[0]["signed_qty"],
        },
        "sample_adequacy": sample_adequacy.value,
        "monte_carlo_status": "NOT_APPLICABLE",
        "checker": checker["outcome"],
        "replay": replay["result"],
        "replay_identity": replay["replay_identity"],
    }
    if result["terminal_position_open"] is not True:
        raise RuntimeError(f"{key} terminal open Position contract changed")
    return {
        "schema": "owner-strategy-research-001-trial-result-v1",
        "status": "PASS",
        "key": key,
        "profile_group": spec["profile_group"],
        "trial_kind": spec["kind"],
        "candidate_or_benchmark_name": spec["name"],
        "trial_id": spec["trial_id"],
        "run_id": spec["run_id"],
        "protocol_id": spec["protocol_id"],
        "dataset_release_id": spec["release_id"],
        "catalog_identity": spec["catalog_id"],
        "strategy_registration_id": spec["registration_id"],
        "strategy_spec_id": strategy_identity["strategy_spec_id"],
        "strategy_identity_sha256": strategy_identity["strategy_identity_sha256"],
        "implementation_code_sha256": strategy_identity["implementation_code_sha256"],
        "source_revision": source_revision,
        "semantic_digest": result["semantic_digest"],
        "replay_semantic_digest": replay["replay_semantic_digest"],
        "report_id": report["report_id"],
        "claim_evaluation_id": report["claim_evaluation"]["claim_evaluation_id"],
        "research_eligibility": "INELIGIBLE_FOR_REAL_PROFITABILITY_CLAIM",
        "final_holdout_used": False,
        "real_profitability_claim": False,
        "optimization_performed": False,
        "metrics": metrics,
        "native_research_metrics_readiness": readiness.to_builtins(),
        "market_state_acceptance": market_validation,
        "offline_enforcement": network,
        "source_bindings": source_bindings,
    }


def _format_metric(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def result_table(results: list[dict[str, Any]]) -> str:
    lines = [
        "| Trial | Signals | Orders/Fills | Native units | Net PnL USDT | Fees | Funding | Ending equity | Max DD | Sharpe | Sortino | Calmar | PF | Exposure |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        value = result["metrics"]
        lines.append(
            "| {name} | {signals} | {orders}/{fills} | {units} | {pnl} | {fees} | {funding} | {equity} | {dd} | {sharpe} | {sortino} | {calmar} | {pf} | {exposure} |".format(
                name=result["candidate_or_benchmark_name"],
                signals=value["signals_or_benchmark_entries"],
                orders=value["orders"],
                fills=value["fills"],
                units=value["native_completed_units"],
                pnl=value["net_pnl"],
                fees=value["fees"],
                funding=value["funding"],
                equity=_format_metric(value["ending_equity"]),
                dd=value["maximum_drawdown"],
                sharpe=value["sharpe"],
                sortino=value["sortino"],
                calmar=value["calmar"],
                pf=value["profit_factor"],
                exposure=value["exposure_ratio"],
            ),
        )
    return "\n".join(lines)


def profile_report(profile: str, results: list[dict[str, Any]]) -> str:
    label = "Spot" if profile == "spot" else "Perpetual"
    candidates = [item for item in results if item["trial_kind"] != "BENCHMARK"]
    benchmark = [item for item in results if item["trial_kind"] == "BENCHMARK"]
    lines = [
        f"# تقرير {label} — OWNER_STRATEGY_RESEARCH_001",
        "",
        "> بحث Development/Exploratory على بيانات مكشوفة. ليس Final Holdout، ولا توصية تداول، ولا Profitability Claim.",
        "",
        result_table([*candidates, *benchmark]),
        "",
        "## الوحدات الأصلية",
        "",
    ]
    for item in candidates:
        value = item["metrics"]
        lines.extend(
            [
                f"- `{item['candidate_or_benchmark_name']}`: {value['native_completed_units']} وحدات Position مكتملة أصلية؛ متوسط realized PnL `{value['average_native_realized_pnl']}` USDT، ومتوسط realized return `{value['average_native_realized_return']}`. المركز الطرفي `{value['terminal_position']['side']} {value['terminal_position']['signed_quantity']}` مفتوح ومستبعد من العينة.",
            ],
        )
    lines.extend(
        [
            "",
            "Gross PnL لجميع Runs هو `UNDEFINED_NATIVE_GROSS_PNL_NOT_EXPOSED`؛ لم يُشتق من Net أوfees أوfunding. SampleAdequacy وMonte Carlo هما `NOT_APPLICABLE` وفق البروتوكول الاستكشافي المجمد، دون pooling بين البروفايلين.",
            "",
            "كل checker هو `CHECK_PASS`، وكل replay هو `PASS`، ولا توجد `No market` أوprecision rejection أوnetwork contact. كل المراكز الطرفية عُلّمت دون synthetic close.",
        ],
    )
    if profile == "spot":
        lines.extend(
            [
                "",
                "مرشّحا Spot يستخدمان LONG/FLAT فقط؛ لا short ولاborrowing ولاfunding. Calmar للمرشحين غير معرّفة لأن pinned Nautilus أعاد Position-return fallback، لا portfolio daily returns. Calmar الـbenchmark فقط معرّفة أصلًا.",
            ],
        )
    else:
        lines.extend(
            [
                "",
                "العقود الدائمة استخدمت MARGIN/NETTING/leverage 1، وMark الرسمية وfunding الأصلية. كل reversal اجتاز close-to-flat ثم separate reopen؛ لا direct cross-zero ولا project funding posting.",
            ],
        )
    return "\n".join(lines) + "\n"


def owner_report(results: dict[str, dict[str, Any]], acceptance: dict[str, Any]) -> str:
    spot = [results[key] for key in ("spot_candidate_a", "spot_candidate_b", "spot_benchmark")]
    perpetual = [
        results[key]
        for key in ("perpetual_candidate_a", "perpetual_candidate_b", "perpetual_benchmark")
    ]
    return f"""# تقرير Owner — OWNER_STRATEGY_RESEARCH_001

## الحكم

اكتملت العائلة المجمدة `BTCUSDT_WEEKLY_TSMOM28_V1` ميكانيكيًا على Spot وPerpetual. جميع التجارب الست انتهت `COMPLETED`، وكل checker أعاد `CHECK_PASS`، وكل replay تطابق دلاليًا. هذا **ليس** إثبات ربحية، ولا Final Holdout، ولا توصية تداول.

## العقد المجمد

- Candidate budget: مرشحان بالضبط: `TSMOM28_FULL_NOTIONAL` و`TSMOM28_VOLATILITY_TARGET_20`.
- Benchmark منفصل لكل Profile: `BUY_AND_HOLD_1X_V1`، ولا يدخل candidate budget.
- القرار أسبوعي الاثنين 00:00 UTC من 29 close مكتملة؛ momentum هو `C[-1]/C[-29]-1`؛ latency = 60 ثانية.
- multiple-testing policy = `HOLM_BONFERRONI`، لكن لا winner selection ولا claim ولا p-value promotion في هذا البحث الاستكشافي.
- النافذة `[2021-01-01, 2021-08-01)` والتسجيل `[2021-02-01, 2021-08-01)` مصنفة `EXPOSED_DEVELOPMENT_DATA` و`DATA_QUALITY_INSPECTED_NOT_FINAL_HOLDOUT`.

## Spot

{result_table(spot)}

## Perpetual

{result_table(perpetual)}

## قراءة المقاييس

- الوحدات المكتملة أصلية من Nautilus NETTING snapshots/closed Positions: Spot = 3 لكل مرشح؛ Perpetual = 6 لكل مرشح. الـbenchmark يحتفظ بمركز طرفي مفتوح، ولذلك completed units = 0.
- متوسط realized PnL/return مشتق فقط من سلسلة Position الأصلية المكتملة. لا Fill pairing ولا Trade IDs مصطنعة.
- Gross PnL = `UNDEFINED_NATIVE_GROSS_PNL_NOT_EXPOSED` لكل Run.
- Calmar تُعرض فقط عندما يقبل `CalmarRatio(252)` الأصلي portfolio daily returns؛ تبقى غير معرّفة لمرشحي Spot ذوي Position-return fallback.
- SampleAdequacy = `NOT_APPLICABLE` وMonte Carlo = `NOT_APPLICABLE` لأن البروتوكول الاستكشافي جمدهما قبل النتائج؛ لم يُضع threshold بعد التعرض.

## البيانات والتنفيذ

- Spot release `{SPOT_RELEASE}`؛ catalog `{SPOT_CATALOG}`.
- Perpetual release `{PERPETUAL_RELEASE}`؛ catalog `{PERPETUAL_CATALOG}`.
- DuckDB semantic identity `{DUCKDB_SEMANTIC_IDENTITY}`.
- لا acquisition ولاتعديل raw/DuckDB/release/catalog. كل Run عمل داخل process-level offline boundary، external contacts = 0.
- Nautilus وحده امتلك orders/Fills/positions/accounts/PnL/fees/funding/mark valuation. لا synthetic terminal Fill.

## المقارنة التاريخية المقيدة

SMA20 التاريخية تبقى benchmark مكشوفًا غير معاد التشغيل: Spot Net PnL `-751.78721000 USDT`، وPerpetual Net PnL `-3010.78713375 USDT`. لا تُستخدم هذه المقارنة لاختيار winner أوتغيير parameter.

## النزاهة والاختبارات

المحاولة الأولى لـSpot Candidate A بقيت `FAILED/CHECK_FAIL` ومحفوظة؛ أُعيد candidate نفسه بهوية Trial جديدة بعد إصلاح Product لا يغير semantics. نتيجة القبول النهائية: `{acceptance['status']}`؛ unique tests `{acceptance['unique_tests']}`؛ execution occurrences `{acceptance['test_execution_occurrences']}`؛ failures/errors/skips/xfail كلها صفر.

## الأهلية

`final_holdout_used=false`، `real_profitability_claim=false`، `optimization_performed=false`، والحالة `INELIGIBLE_FOR_REAL_PROFITABILITY_CLAIM`. لا يُسمى أي مرشح proven أوwinner، وتنتظر أي عائلة أوHoldout جديدة مراجعة Owner مستقلة.
"""


def mechanical_report(results: dict[str, dict[str, Any]]) -> str:
    rows = []
    for key, result in results.items():
        acceptance = result["market_state_acceptance"]
        rows.append(
            f"| {key} | {result['metrics']['checker']} | {result['metrics']['orders']}/{result['metrics']['fills']} | {acceptance['precision_skipped_bars']} | {acceptance['missing_market_state']} | 0 |",
        )
    return """# Mechanical Integrity — OWNER_STRATEGY_RESEARCH_001

> هذه شهادة تنفيذ ميكانيكية، وليست شهادة ربحية.

| Trial | Checker | Orders/Fills | Precision skips | Missing market | Network contacts |
|---|---|---:|---:|---:|---:|
""" + "\n".join(rows) + """

جميع الـFills سببية بعد availability + 60 ثانية؛ لا same-bar Fill، لا `No market`، لا fatal diagnostics، ولا project-side financial posting. Spot بقي CASH/NETTING long-only. Perpetual بقي MARGIN/NETTING 1x، مع close-flat-confirm-reopen وnative mark/funding.
"""


def replay_report(results: dict[str, dict[str, Any]]) -> str:
    rows = [
        f"| {key} | {value['metrics']['replay']} | `{value['metrics']['replay_identity']}` | `{value['semantic_digest']}` |"
        for key, value in results.items()
    ]
    return """# Deterministic Replay — OWNER_STRATEGY_RESEARCH_001

كل replay شُغلت في process جديد وداخل offline boundary. اختلاف Run paths/IDs غير مادي؛ تطابقت signals/orders/Fills/positions/account events/fees/funding/equity/terminal state دلاليًا.

| Trial | Result | Replay identity | Semantic digest |
|---|---|---|---|
""" + "\n".join(rows) + "\n"


def authoritative_history() -> dict[str, Any]:
    records = [
        json.loads(line)
        for line in (ROOT / "research/trials.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    family = [record for record in records if record["research_family_id"] == RESEARCH_FAMILY]
    terminal = [record for record in family if record["state"] in {"COMPLETED", "FAILED", "BLOCKED", "ABORTED"}]
    if len(terminal) != 7 or sum(record["state"] == "COMPLETED" for record in terminal) != 6:
        raise RuntimeError("authoritative family history does not retain six passes plus one failure")
    return {
        "schema": "owner-strategy-research-001-authoritative-history-v1",
        "status": "PASS",
        "research_family_id": RESEARCH_FAMILY,
        "record_count": len(family),
        "terminal_attempt_count": len(terminal),
        "completed_attempt_count": sum(record["state"] == "COMPLETED" for record in terminal),
        "failed_attempt_count": sum(record["state"] == "FAILED" for record in terminal),
        "records": family,
        "trial_journal": binding(ROOT / "research/trials.jsonl"),
        "history_anchors": binding(ROOT / "research/history_anchors.jsonl"),
        "failed_run_preserved": binding(
            ROOT / "runs/owner-strategy-research-001-spot-candidate-a-run-f1e2c8bc7b40/evidence_manifest.json",
        ),
    }


def validate_all() -> dict[str, dict[str, Any]]:
    observed = {name: sha256_file(ROOT / name) for name in EXPECTED_LOCKS}
    if observed != EXPECTED_LOCKS:
        raise RuntimeError(f"locked identity mismatch: {observed}")
    if git("status", "--porcelain"):
        raise RuntimeError("evidence validation requires a clean worktree")
    if git("rev-parse", "HEAD") != git("rev-parse", "origin/main"):
        raise RuntimeError("HEAD and origin/main differ")
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", BASELINE_COMMIT, "HEAD"],
        cwd=ROOT,
        check=False,
    ).returncode:
        raise RuntimeError("baseline commit is not an ancestor")
    results = {key: validate_run(key, spec) for key, spec in RUNS.items()}
    authoritative_history()
    return results


def publish(results: dict[str, dict[str, Any]], acceptance_dir: Path) -> None:
    acceptance_path = acceptance_dir / "result.json"
    output_path = acceptance_dir / "test-output.txt"
    if not acceptance_path.is_file() or not output_path.is_file():
        raise RuntimeError("acceptance result and test output are required")
    acceptance = load(acceptance_path)
    if acceptance.get("status") != "PASS":
        raise RuntimeError("final acceptance did not pass")
    if any(
        acceptance.get(name) != 0
        for name in ("failures", "errors", "skips", "xfail")
    ):
        raise RuntimeError("acceptance contains failure/error/skip/xfail")

    for key, value in results.items():
        write_json(EPOCH / "run-results" / f"{key}.json", value)
    write_json(
        EPOCH / "native-research-metrics.json",
        {
            "schema": "owner-strategy-research-001-native-metrics-v1",
            "status": "PASS",
            "gross_pnl_disposition": "UNDEFINED_NATIVE_GROSS_PNL_NOT_EXPOSED",
            "results": {
                key: value["native_research_metrics_readiness"]
                for key, value in results.items()
            },
        },
    )
    write_json(
        EPOCH / "mechanical-integrity.json",
        {
            "schema": "owner-strategy-research-001-mechanical-integrity-v1",
            "status": "PASS",
            "results": {
                key: {
                    "checker": value["metrics"]["checker"],
                    "market_state_acceptance": value["market_state_acceptance"],
                    "offline_enforcement": value["offline_enforcement"],
                }
                for key, value in results.items()
            },
        },
    )
    write_json(
        EPOCH / "deterministic-replay.json",
        {
            "schema": "owner-strategy-research-001-replay-v1",
            "status": "PASS",
            "results": {
                key: {
                    "result": value["metrics"]["replay"],
                    "replay_identity": value["metrics"]["replay_identity"],
                    "primary_semantic_digest": value["semantic_digest"],
                    "replay_semantic_digest": value["replay_semantic_digest"],
                }
                for key, value in results.items()
            },
        },
    )
    write_json(
        EPOCH / "strategy-identities.json",
        {
            "schema": "owner-strategy-research-001-strategy-identities-v1",
            "status": "PASS",
            "results": {
                key: {
                    field: value[field]
                    for field in (
                        "strategy_registration_id",
                        "strategy_spec_id",
                        "strategy_identity_sha256",
                        "implementation_code_sha256",
                        "source_revision",
                    )
                }
                for key, value in results.items()
            },
        },
    )
    write_json(
        EPOCH / "research-eligibility.json",
        {
            "schema": "owner-strategy-research-001-eligibility-v1",
            "status": "INELIGIBLE_FOR_REAL_PROFITABILITY_CLAIM",
            "research_purpose": "EXPLORATORY_OPERATIONAL_VALIDATION",
            "research_intent": "EXPLORATORY",
            "final_holdout_used": False,
            "real_profitability_claim": False,
            "optimization_performed": False,
            "sample_adequacy": {key: "NOT_APPLICABLE" for key in results},
            "monte_carlo": {key: "NOT_APPLICABLE" for key in results},
            "winner_selected": False,
            "next_family_or_holdout_authorized": False,
        },
    )
    write_json(
        EPOCH / "multiple-testing.json",
        {
            "schema": "owner-strategy-research-001-multiple-testing-v1",
            "status": "PASS",
            "policy": "HOLM_BONFERRONI",
            "candidate_budget": 2,
            "candidate_profile_trial_count": 4,
            "benchmark_trial_count": 2,
            "candidate_addition_after_results": False,
            "p_value_claim_performed": False,
            "publishable_winner_selected": False,
            "interpretation": "Frozen multiplicity policy retained; exploratory outputs do not create an eligible hypothesis-test claim.",
        },
    )
    write_json(
        EPOCH / "historical-sma20-comparison.json",
        {
            "schema": "owner-strategy-research-001-historical-sma20-comparison-v1",
            "status": "DISCLOSED_EXPOSED_BENCHMARK_ONLY",
            "rerun": False,
            "spot_net_pnl": "-751.78721000 USDT",
            "perpetual_net_pnl": "-3010.78713375 USDT",
            "used_for_parameter_change": False,
            "used_for_winner_selection": False,
        },
    )
    write_json(EPOCH / "authoritative-history.json", authoritative_history())
    write_json(EPOCH / "test-results.json", acceptance)
    write_text(EPOCH / "test-output.txt", output_path.read_text(encoding="utf-8"))
    write_text(
        EPOCH / "owner-report/README.md",
        owner_report(results, acceptance),
    )
    write_text(
        EPOCH / "spot-report/README.md",
        profile_report(
            "spot",
            [results[key] for key in ("spot_candidate_a", "spot_candidate_b", "spot_benchmark")],
        ),
    )
    write_text(
        EPOCH / "perpetual-report/README.md",
        profile_report(
            "perpetual",
            [
                results[key]
                for key in (
                    "perpetual_candidate_a",
                    "perpetual_candidate_b",
                    "perpetual_benchmark",
                )
            ],
        ),
    )
    write_text(EPOCH / "mechanical-integrity/README.md", mechanical_report(results))
    write_text(EPOCH / "deterministic-replay/README.md", replay_report(results))

    files = {
        path.relative_to(EPOCH).as_posix(): {
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(EPOCH.rglob("*"))
        if path.is_file()
        and path.name not in {"evidence-inventory.json", "final-content-manifest.json"}
    }
    write_json(
        EPOCH / "evidence-inventory.json",
        {
            "schema": "owner-strategy-research-001-evidence-inventory-v1",
            "status": "PASS",
            "files": files,
            "file_count": len(files),
            "inventory_identity": canonical_sha256(files),
            "raw_data_copied_to_git": False,
            "duckdb_or_catalog_payload_copied_to_git": False,
        },
    )
    manifest_files = {
        path.relative_to(EPOCH).as_posix(): {
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(EPOCH.rglob("*"))
        if path.is_file() and path.name != "final-content-manifest.json"
    }
    write_json(
        EPOCH / "final-content-manifest.json",
        {
            "schema": "owner-strategy-research-001-final-content-manifest-v1",
            "status": "PASS",
            "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "source_head": git("rev-parse", "HEAD"),
            "files": manifest_files,
            "file_count_excluding_manifest": len(manifest_files),
            "content_identity": canonical_sha256(manifest_files),
            "final_holdout_used": False,
            "real_profitability_claim": False,
            "optimization_performed": False,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--acceptance-dir", type=Path)
    arguments = parser.parse_args()
    results = validate_all()
    if arguments.validate_only:
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "validated_run_count": len(results),
                    "checker_pass_count": sum(
                        value["metrics"]["checker"] == "CHECK_PASS"
                        for value in results.values()
                    ),
                    "replay_pass_count": sum(
                        value["metrics"]["replay"] == "PASS"
                        for value in results.values()
                    ),
                },
                sort_keys=True,
            ),
        )
        return 0
    if arguments.acceptance_dir is None:
        parser.error("--acceptance-dir is required unless --validate-only is used")
    publish(results, arguments.acceptance_dir.resolve())
    print(
        json.dumps(
            {
                "status": "PASS",
                "published_run_count": len(results),
                "epoch": EPOCH.relative_to(ROOT).as_posix(),
            },
            sort_keys=True,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
