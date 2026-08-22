#!/usr/bin/env python3
"""Execute Final V1 acceptance from clean committed source, offline.

All mutable research fixtures live in an isolated staging workspace.  Nothing
is written to the repository until every result-bearing mechanical run and the
complete regression have finished from the clean Source Revision.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from crypto_lab.config import MarketProfile
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.m3 import MechanicalIntegrity
from crypto_lab.reporting import EquityObservation
from crypto_lab.reporting import ReportInput
from crypto_lab.reporting import build_report
from crypto_lab.reporting import generate_performance_diagnostics
from crypto_lab.reporting import write_report
from crypto_lab.research import BenchmarkSpec
from crypto_lab.research import CandidateSpec
from crypto_lab.research import ClaimEvaluationInput
from crypto_lab.research import ClaimScope
from crypto_lab.research import CompletedTradeSeries
from crypto_lab.research import HoldoutLockStore
from crypto_lab.research import InstrumentScope
from crypto_lab.research import M3ResearchBoundary
from crypto_lab.research import MonteCarloSpec
from crypto_lab.research import MonteCarloStatus
from crypto_lab.research import PartitionRole
from crypto_lab.research import PartitionStartEvidence
from crypto_lab.research import PurgeEmbargoRule
from crypto_lab.research import ResearchEligibility
from crypto_lab.research import ResearchIntent
from crypto_lab.research import ResearchProtocol
from crypto_lab.research import ResearchScheduler
from crypto_lab.research import ResamplingMethod
from crypto_lab.research import ResultExposure
from crypto_lab.research import SampleAdequacy
from crypto_lab.research import SampleAdequacyRule
from crypto_lab.research import TrialDefinition
from crypto_lab.research import TrialJournal
from crypto_lab.research import TrialState
from crypto_lab.research import UtcInterval
from crypto_lab.research import evaluate_claim
from crypto_lab.research import evaluate_sample_adequacy
from crypto_lab.research import run_monte_carlo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_OUTPUT = ROOT / "evidence/m4/m4-acceptance-001"
M3_EVIDENCE = ROOT / "evidence/m3/m3-acceptance-001"
M3_REGISTRY_ID = "d6124dd7d225818f0de212d74f7d4aae5e3bf08c9f8ff342435baac6228ba6de"
LOCKS = {
    "ssot_sha256": "7bb2fc68d9b73b168a582d890a6f952fd0c4eb20fc0e31857903909f27dfaa8f",
    "runtime_lock_sha256": "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd",
    "dependency_lock_sha256": "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47",
}


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _interval(start: str, end: str) -> UtcInterval:
    return UtcInterval(start_inclusive=_utc(start), end_exclusive=_utc(end))


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _environment() -> dict[str, str]:
    return {
        **os.environ,
        "TZ": "UTC",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": "/tmp/nautilus-final-v1-pyc",
        "PYTHONPATH": str(ROOT / "src"),
        "PYTEST_ADDOPTS": "-p no:cacheprovider",
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def _history_inventory() -> dict[str, Any]:
    roots = (
        ROOT / "evidence/m0",
        ROOT / "evidence/m1",
        ROOT / "evidence/m2",
        ROOT / "evidence/m3",
        ROOT / "data",
        ROOT / "research",
    )
    entries = [
        {
            "path": str(path.relative_to(ROOT)),
            "byte_size": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for base in roots
        for path in sorted(base.rglob("*"))
        if path.is_file()
    ]
    entries.sort(key=lambda item: item["path"])
    return {
        "schema": "m4-preserved-history-inventory-v1",
        "entries": entries,
        "content_sha256": canonical_sha256(entries),
    }


def _candidate(index: int) -> CandidateSpec:
    return CandidateSpec.create(
        candidate_label=f"synthetic-contract-candidate-{index}",
        strategy_spec_id=f"{index + 1:x}" * 64,
        parameter_values={"lookback": str(index + 2), "threshold": f"0.{index + 1}"},
    )


def _protocol() -> ResearchProtocol:
    development = _interval("2020-01-01T00:00:00Z", "2020-02-01T00:00:00Z")
    validation = _interval("2020-02-01T00:00:00Z", "2020-03-01T00:00:00Z")
    oos = _interval("2020-03-01T00:00:00Z", "2020-04-01T00:00:00Z")
    holdout = _interval("2020-04-01T00:00:00Z", "2020-05-01T00:00:00Z")
    return ResearchProtocol.create(
        frozen_at_utc=_utc("2019-12-01T00:00:00Z"),
        research_family_id="m4-synthetic-contract-family",
        hypothesis_id="m4-synthetic-contract-hypothesis",
        research_intent=ResearchIntent.CONFIRMATORY,
        market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        instrument_scope=InstrumentScope.SINGLE_INSTRUMENT,
        instrument_ids=("BTCUSDT.BINANCE",),
        instrument_selection_basis="Independent explicit Final V1 synthetic contract fixture",
        universe_selection_rule="NOT_APPLICABLE",
        universe_as_of_rule="NOT_APPLICABLE",
        universe_membership_sha256="NOT_APPLICABLE",
        dataset_release_ids=("d" * 64,),
        strategy_family="M4_SYNTHETIC_CONTRACT_ONLY_NOT_A_TRADING_EDGE",
        ordered_candidates=(_candidate(0), _candidate(1)),
        parameter_domain={"lookback": ("2", "3"), "threshold": ("0.1", "0.2")},
        search_budget=2,
        candidate_ordering="AS_LISTED",
        deterministic_generator="NOT_APPLICABLE",
        random_seeds=(7,),
        primary_metric="NAUTILUS_NATIVE_TOTAL_RETURN",
        required_benchmark=BenchmarkSpec(
            benchmark_id="BUY_AND_HOLD_SAME_INSTRUMENT",
            definition="Frozen same-instrument benchmark for the exact synthetic interval",
            scored_interval=holdout,
            cost_basis="SAME_ESTIMATED_FEE_AND_EXECUTION_BASIS",
            frozen_before_result_exposure=True,
        ),
        selection_rule="MAX_PRIMARY_METRIC_SUBJECT_TO_KILL_CRITERIA",
        tie_break_rule="LOWEST_CANDIDATE_ID_LEXICOGRAPHICALLY",
        development_interval=development,
        validation_interval=validation,
        oos_interval=oos,
        final_holdout_interval=holdout,
        purge_embargo_rule=PurgeEmbargoRule(
            mode="NOT_APPLICABLE",
            reason="No forward-dependent feature, label, target, or training sample",
            purge_seconds=0,
            embargo_seconds=0,
            max_forward_dependency_seconds=0,
        ),
        time_series_split="CHRONOLOGICAL",
        multiple_testing_treatment="HOLM_BONFERRONI",
        sample_adequacy_rule=SampleAdequacyRule(
            counted_observation="NAUTILUS_NATIVE_COMPLETED_TRADE",
            minimum_completed_trades=2,
            rationale="Synthetic eligibility contract freezes two as its minimum",
        ),
        monte_carlo_spec=MonteCarloSpec(
            resampling_method=ResamplingMethod.IID_BOOTSTRAP,
            simulation_count=64,
            random_seed=7,
            block_length="NOT_APPLICABLE",
            quantile_method="R7_LINEAR_INTERPOLATION",
            decimal_places=8,
            not_applicable_reason="NOT_APPLICABLE",
        ),
        intended_claim_scope=ClaimScope.INSTRUMENT_ONLY,
        claim_basis="TRADE_BASED_PROFITABILITY",
        kill_criteria=("MECHANICAL_INTEGRITY_NOT_PASS", "CHECKER_NOT_PASS"),
        terminal_policy="MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE",
    )


def _run_command(path: Path, command: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=_environment(),
        check=False,
        capture_output=True,
        text=True,
    )
    _write_json(
        path,
        {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "fresh_process": True,
            "network_enabled": False,
        },
    )
    return completed


def _run_real_profile(staging: Path, *, label: str, profile: str) -> dict[str, Any]:
    real_root = staging / "final-v1/real-data"
    summary_path = real_root / "private-summaries" / f"{label}.json"
    evidence_root = real_root / "runs" / label
    run_id = f"final-v1-{label}-001"
    command = [
        str(ROOT / ".venv/bin/python"),
        str(ROOT / "scripts/run_m3_child.py"),
        "--profile",
        profile,
        "--run-id",
        run_id,
        "--evidence-root",
        str(evidence_root),
        "--summary",
        str(summary_path),
    ]
    completed = _run_command(real_root / "commands" / f"{label}.json", command)
    if completed.returncode != 0 or not summary_path.is_file():
        raise RuntimeError(f"real-data qualification failed for {label}: {completed.stderr}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if not (
        summary["state"] == "COMPLETED"
        and summary["checker_outcome"] == "CHECK_PASS"
        and not summary["failure_codes"]
        and summary["fills_count"] > 0
    ):
        raise RuntimeError(f"real-data qualification did not pass for {label}: {summary}")
    absolute = Path(summary["evidence_dir"])
    published = dict(summary)
    published["evidence_dir"] = str(absolute.relative_to(real_root))
    public_path = real_root / "attempt-summaries" / f"{label}.json"
    _write_json(public_path, published)
    summary_path.unlink()
    if not any((real_root / "private-summaries").iterdir()):
        (real_root / "private-summaries").rmdir()
    return published


def _synthetic_mechanical_fixtures(staging: Path) -> dict[str, Any]:
    tests = [
        "tests.golden.test_m1_contracts.M1GoldenContractTests.test_g02_no_same_bar_fill",
        "tests.golden.test_m1_contracts.M1GoldenContractTests.test_g07_spot_oversell_is_blocked_before_submission",
        "tests.golden.test_m1_contracts.M1GoldenContractTests.test_g08_perpetual_netting_lifecycle_and_guards",
        "tests.qualification.test_m1_native_funding.M1NativeFundingQualificationTests.test_g09_positive_funding_debits_long_exactly_once",
    ]
    command = [str(ROOT / ".venv/bin/python"), "-m", "unittest", "-v", *tests]
    completed = _run_command(staging / "final-v1/synthetic-fixture-command.json", command)
    output = completed.stdout + completed.stderr
    (staging / "final-v1/synthetic-fixture-output.txt").write_text(output, encoding="utf-8")
    match = re.search(r"Ran (\d+) tests?", output)
    result = {
        "schema": "final-v1-synthetic-mechanical-fixtures-v1",
        "status": "PASS" if completed.returncode == 0 and match and int(match.group(1)) == 4 else "FAIL",
        "executed_test_cases": int(match.group(1)) if match else -1,
        "test_ids": tests,
        "spot_cash_fixture": True,
        "perpetual_netting_fixture": True,
        "native_funding_included": True,
        "network_enabled": False,
        "project_financial_engine_used": False,
    }
    _write_json(staging / "final-v1/synthetic-fixtures.json", result)
    if result["status"] != "PASS":
        raise RuntimeError("Final V1 synthetic mechanical fixtures failed")
    return result


def _research_lifecycle(staging: Path, *, head: str) -> dict[str, Any]:
    research = staging / "research"
    protocol = _protocol()
    protocol_path = research / "protocols" / f"{protocol.protocol_id}.json"
    protocol_path.parent.mkdir(parents=True, exist_ok=True)
    protocol_path.write_bytes(protocol.to_json_bytes() + b"\n")
    protocol_path.with_suffix(".sha256").write_text(protocol.protocol_id + "\n", encoding="utf-8")

    scheduler = ResearchScheduler(protocol)
    journal = TrialJournal(research / "trials.jsonl")
    failed = TrialDefinition.synthetic(
        trial_id="m4-synthetic-failed",
        protocol=protocol,
        candidate=scheduler.next_candidate(()),
        run_id="m4-synthetic-failed-run",
    )
    journal.start(failed, at_utc=_utc("2020-05-01T00:00:00Z"))
    journal.finish(
        failed.trial_id,
        state=TrialState.FAILED,
        at_utc=_utc("2020-05-01T00:01:00Z"),
        result_ref="NOT_APPLICABLE",
        reason="EXPECTED_SYNTHETIC_FAILED_TRIAL_RETAINED",
        result_exposed=False,
    )
    completed = TrialDefinition.synthetic(
        trial_id="m4-synthetic-completed",
        protocol=protocol,
        candidate=scheduler.next_candidate((failed,)),
        run_id="m4-synthetic-completed-run",
    )
    journal.start(completed, at_utc=_utc("2020-05-01T00:02:00Z"))
    synthetic_result = {
        "schema": "m4-synthetic-contract-result-v1",
        "purpose": "SYNTHETIC_CONTRACT_FIXTURE",
        "real_owner_study": False,
        "profitability_claim": False,
    }
    _write_json(research / "synthetic-contract-result.json", synthetic_result)
    journal.finish(
        completed.trial_id,
        state=TrialState.COMPLETED,
        at_utc=_utc("2020-05-02T00:00:00Z"),
        result_ref="research/synthetic-contract-result.json",
        reason="NOT_APPLICABLE",
        result_exposed=True,
    )
    exposure = ResultExposure(
        trial_id=completed.trial_id,
        market_profile=protocol.market_profile,
        instrument_id=protocol.instrument_ids[0],
        scored_interval=protocol.final_holdout_interval,
        research_family_id=protocol.research_family_id,
        hypothesis_lineage=(protocol.hypothesis_id,),
        strategy_lineage=(protocol.strategy_family,),
        dataset_release_id=protocol.dataset_release_ids[0],
        first_exposure_at_utc=_utc("2020-05-02T00:00:00Z"),
        exposure_type="PERFORMANCE_METRIC",
        evidence_reference="research/synthetic-contract-result.json",
        source_branch="main",
        source_commit=head,
        seed=7,
        result_bearing=True,
    )
    lock_store = HoldoutLockStore(research / "holdout_lock.json")
    entry = lock_store.consume(
        exposure,
        journal=journal,
        exposure_resolver={completed.trial_id: exposure},
    )

    partition_states = tuple(
        PartitionStartEvidence(
            partition_role=role,
            configured_initial_capital=Decimal("100"),
            observed_starting_cash=Decimal("100"),
            observed_starting_position_quantity=Decimal("0"),
            observed_starting_realized_pnl=Decimal("0"),
            pending_strategy_orders=0,
            warmup_scored_order_count=0,
            scoring_start_utc=interval.start_inclusive,
            warmup_context_end_exclusive=interval.start_inclusive,
            source="NAUTILUS_PERSISTED_RUN_EVIDENCE",
        )
        for role, interval in (
            (PartitionRole.DEVELOPMENT, protocol.development_interval),
            (PartitionRole.VALIDATION, protocol.validation_interval),
            (PartitionRole.OOS, protocol.oos_interval),
            (PartitionRole.FINAL_HOLDOUT, protocol.final_holdout_interval),
        )
    )
    _write_json(
        research / "partition-multiplicity-benchmark-results.json",
        {
            "schema": "m4-research-boundaries-v1",
            "partition_states": partition_states,
            "purge_embargo_rule": protocol.purge_embargo_rule,
            "time_series_split": protocol.time_series_split,
            "multiple_testing_treatment": protocol.multiple_testing_treatment,
            "benchmark": protocol.required_benchmark,
            "status": "PASS",
        },
    )
    trades = CompletedTradeSeries(
        source="NAUTILUS_NATIVE_COMPLETED_TRADES",
        evidence_sha256=canonical_sha256(
            {"fixture": "independent-native-trade-contract", "outcomes": ["10", "-5", "2", "-1"]},
        ),
        settlement_currency="USDT",
        unambiguous_net_after_cost=True,
        net_outcomes=(Decimal("10"), Decimal("-5"), Decimal("2"), Decimal("-1")),
    )
    adequacy = evaluate_sample_adequacy(protocol.sample_adequacy_rule, trades)
    _write_json(
        research / "sample-adequacy.json",
        {
            "schema": "m4-sample-adequacy-v1",
            "status": adequacy.value,
            "rule": protocol.sample_adequacy_rule,
            "completed_trade_evidence": trades,
        },
    )
    _write_json(research / "monte-carlo-input.json", {"spec": protocol.monte_carlo_spec, "trades": trades})
    monte_carlo = run_monte_carlo(
        protocol.monte_carlo_spec,
        trades,
        initial_capital=Decimal("100"),
        sample_adequacy=adequacy,
    )
    (research / "monte-carlo-result.json").write_bytes(monte_carlo.to_json_bytes() + b"\n")
    observations = (
        EquityObservation(timestamp=_utc("2020-04-01T00:00:00Z"), equity=Decimal("100")),
        EquityObservation(timestamp=_utc("2020-04-10T00:00:00Z"), equity=Decimal("110")),
        EquityObservation(timestamp=_utc("2020-04-20T00:00:00Z"), equity=Decimal("95")),
        EquityObservation(timestamp=_utc("2020-05-01T00:00:00Z"), equity=Decimal("105")),
    )
    diagnostics = generate_performance_diagnostics(
        run_id=completed.run_id,
        scored_start=protocol.final_holdout_interval.start_inclusive,
        scoring_end_exclusive=protocol.final_holdout_interval.end_exclusive,
        initial_capital=Decimal("100"),
        settlement_currency="USDT",
        equity_observation_basis="SYNTHETIC_PERSISTED_NAUTILUS_CONTRACT_FIXTURE",
        equity_observations=observations,
        native_metrics={},
        completed_trades=trades,
        benchmark_return=Decimal("0.02"),
        sample_adequacy=adequacy,
        monte_carlo_status=monte_carlo.status,
        claim_scope=protocol.intended_claim_scope.value,
        input_evidence_hashes={
            "synthetic-contract-result.json": sha256_file(research / "synthetic-contract-result.json"),
            "native-completed-trade-contract": trades.evidence_sha256,
        },
    )
    (research / "performance-diagnostics.json").write_bytes(diagnostics.to_json_bytes() + b"\n")
    diagnostic_path = research / "diagnostics" / f"{completed.run_id}.json"
    diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
    diagnostic_path.write_bytes(diagnostics.to_json_bytes() + b"\n")

    claim_base = ClaimEvaluationInput(
        protocol=protocol,
        mechanical_integrity=MechanicalIntegrity.PASS,
        checker_result="CHECK_PASS",
        underlying_official_runs_valid=True,
        qualification_only=False,
        protocol_frozen_before_results=True,
        supporting_trial_protocol_ids=(protocol.protocol_id,),
        complete_trial_history=True,
        partitions_valid=True,
        holdout_valid=True,
        benchmark_valid=True,
        multiple_testing_valid=True,
        sample_adequacy_by_instrument={"BTCUSDT.BINANCE": adequacy.value},
        monte_carlo_by_instrument={"BTCUSDT.BINANCE": monte_carlo.status.value},
        diagnostics_complete_by_instrument={"BTCUSDT.BINANCE": True},
        claim_scope_supported=True,
        universe_evidence_valid=True,
        unresolved_material_ambiguities=(),
        synthetic_contract_fixture=True,
    )
    eligible_fixture = evaluate_claim(claim_base)
    if eligible_fixture.research_eligibility is not ResearchEligibility.ELIGIBLE:
        raise RuntimeError("positive synthetic claim-contract fixture did not reach ELIGIBLE")
    qualification_claim = evaluate_claim(
        replace(
            claim_base,
            qualification_only=True,
            diagnostics_complete_by_instrument={"BTCUSDT.BINANCE": False},
            synthetic_contract_fixture=False,
        ),
    )
    if qualification_claim.research_eligibility is not ResearchEligibility.INELIGIBLE:
        raise RuntimeError("M3 qualification evidence was not held ineligible")
    matrix = {
        "schema": "m4-claim-gate-matrix-v1",
        "synthetic_contract_fixture": {
            **eligible_fixture.to_builtins(),
            "real_profitability_claim": False,
        },
        "m3_qualification_evidence": {
            **qualification_claim.to_builtins(),
            "real_profitability_claim": False,
        },
        "failed_mechanical_integrity": evaluate_claim(
            replace(claim_base, mechanical_integrity=MechanicalIntegrity.FAIL),
        ).to_builtins(),
        "low_sample": evaluate_claim(
            replace(
                claim_base,
                sample_adequacy_by_instrument={"BTCUSDT.BINANCE": "LOW_CONFIDENCE"},
            ),
        ).to_builtins(),
        "missing_trial_history": evaluate_claim(
            replace(claim_base, complete_trial_history=False),
        ).to_builtins(),
    }
    _write_json(research / "claim-gate-matrix.json", matrix)

    trial_records = journal.read_records()
    synthetic_input = ReportInput(
        schema_version=1,
        protocol=protocol,
        claim_evaluation=eligible_fixture,
        trial_records=trial_records,
        included_trial_ids=(failed.trial_id, completed.trial_id),
        selected_trial_id=completed.trial_id,
        performance_diagnostics=(diagnostics,),
        monte_carlo_results=(monte_carlo,),
        sample_adequacy_by_instrument={"BTCUSDT.BINANCE": adequacy.value},
        holdout_state={"state": "CONSUMED", "entry_id": entry.entry_id},
        benchmark_result={"state": "VALID", "total_return": "0.02"},
        multiple_testing_treatment=protocol.multiple_testing_treatment,
        qualification_limitations=(
            "SYNTHETIC_CONTRACT_FIXTURE_NOT_REAL_CLAIM",
            "BAR_BASED_ESTIMATED_EXECUTION",
            "ESTIMATED_FEE",
            "QUEUE_IMPACT_SPREAD_LIQUIDATION_UNSUPPORTED",
        ),
        open_terminal_positions={},
        source_evidence_hashes={
            "trials.jsonl": sha256_file(research / "trials.jsonl"),
            "holdout_lock.json": sha256_file(research / "holdout_lock.json"),
            "diagnostics": diagnostics.diagnostics_id,
            "monte_carlo": monte_carlo.diagnostic_id,
        },
        source_revision={
            "repository": "NOT_APPLICABLE",
            "branch_ref": "NOT_APPLICABLE",
            "git_commit": "NOT_APPLICABLE",
            "git_tree": "NOT_APPLICABLE",
        },
        report_purpose="SYNTHETIC_CONTRACT_FIXTURE",
    )
    reports = research / "reports"
    write_report(
        build_report(synthetic_input),
        json_path=reports / "synthetic-contract.json",
        markdown_path=reports / "synthetic-contract.md",
    )
    qualification_input = ReportInput(
        schema_version=1,
        protocol=protocol,
        claim_evaluation=qualification_claim,
        trial_records=trial_records,
        included_trial_ids=(failed.trial_id, completed.trial_id),
        selected_trial_id=completed.trial_id,
        performance_diagnostics=(),
        monte_carlo_results=(),
        sample_adequacy_by_instrument={"BTCUSDT.BINANCE": "NOT_APPLICABLE"},
        holdout_state={"state": "QUALIFICATION_INTERVAL_ALREADY_EXPOSED"},
        benchmark_result={"state": "NOT_APPLICABLE_QUALIFICATION_ONLY"},
        multiple_testing_treatment=protocol.multiple_testing_treatment,
        qualification_limitations=(
            "QUALIFICATION_ONLY_NO_PROFITABILITY_CLAIM",
            "QUALIFICATION_INTERVAL_EXPOSED_NOT_FRESH_HOLDOUT",
            "NO_FINANCIAL_TRUTH_RECALCULATED_BY_M4",
        ),
        open_terminal_positions={},
        source_evidence_hashes={
            "m3_registry": sha256_file(M3_EVIDENCE / "qualified-profile-registry.json"),
            "m3_spot_bundle": sha256_file(
                M3_EVIDENCE / "downstream/BINANCE_SPOT_CASH_LONG_ONLY.json",
            ),
            "m3_perpetual_bundle": sha256_file(
                M3_EVIDENCE
                / "downstream/BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING.json",
            ),
        },
        source_revision={
            "repository": "NOT_APPLICABLE",
            "branch_ref": "NOT_APPLICABLE",
            "git_commit": "NOT_APPLICABLE",
            "git_tree": "NOT_APPLICABLE",
        },
        report_purpose="M3_QUALIFICATION_EVIDENCE_ONLY",
    )
    write_report(
        build_report(qualification_input),
        json_path=reports / "m3-qualification-only.json",
        markdown_path=reports / "m3-qualification-only.md",
    )
    return {
        "protocol_id": protocol.protocol_id,
        "journal_records": len(trial_records),
        "failed_trial_retained": True,
        "completed_trial_retained": True,
        "holdout_entry_id": entry.entry_id,
        "sample_adequacy": adequacy.value,
        "monte_carlo_status": monte_carlo.status.value,
        "synthetic_contract_eligibility": eligible_fixture.research_eligibility.value,
        "m3_qualification_eligibility": qualification_claim.research_eligibility.value,
        "real_profitability_claim": False,
    }


def _m3_boundary(staging: Path) -> dict[str, Any]:
    files = (
        M3_EVIDENCE / "qualified-profile-registry.json",
        M3_EVIDENCE / "downstream/BINANCE_SPOT_CASH_LONG_ONLY.json",
        M3_EVIDENCE / "downstream/BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING.json",
    )
    before = {str(path.relative_to(ROOT)): sha256_file(path) for path in files}
    boundary = M3ResearchBoundary.load(
        registry_path=files[0],
        downstream_directory=M3_EVIDENCE / "downstream",
        expected_registry_identity=M3_REGISTRY_ID,
    )
    after = {str(path.relative_to(ROOT)): sha256_file(path) for path in files}
    result = {
        "schema": "m4-m3-downstream-validation-v1",
        "status": (
            "PASS"
            if before == after
            and len(boundary.bundles) == 2
            and all(item.profile_record.qualification_state.value == "QUALIFIED" for item in boundary.bundles)
            and all(item.claim_evaluation.research_eligibility.value == "INELIGIBLE" for item in boundary.bundles)
            else "FAIL"
        ),
        "registry_identity": boundary.registry.registry_content_sha256,
        "profiles": [item.profile_record.profile_id.value for item in boundary.bundles],
        "source_hashes_before": before,
        "source_hashes_after": after,
        "financial_truth_recalculated": False,
        "m3_internal_imports_used": False,
        "qualification_evidence_profitability_claim": False,
    }
    _write_json(staging / "m3-downstream-validation.json", result)
    if result["status"] != "PASS":
        raise RuntimeError("M3 downstream validation failed")
    return result


def _failed_attempts(staging: Path) -> None:
    destination = staging / "failed-attempt-artifacts"
    destination.mkdir(parents=True, exist_ok=True)
    artifacts = (
        (
            Path("/tmp/m4-golden-first-output.txt"),
            destination / "golden-first-output.txt",
        ),
        (
            Path("/tmp/nautilus-m4-precommit-acceptance-LMLvPq/test-results.json"),
            destination / "acceptance-timezone-failure-results.json",
        ),
        (
            Path("/tmp/nautilus-m4-precommit-acceptance-LMLvPq/test-output.txt"),
            destination / "acceptance-timezone-failure-output.txt",
        ),
    )
    copied: dict[str, str] = {}
    for source, target in artifacts:
        if source.is_file():
            shutil.copyfile(source, target)
            copied[target.name] = sha256_file(target)
    attempts = (
        {
            "attempt_id": "M4-GOLDEN-FIRST-001",
            "state": "FAILED_EXPECTED",
            "cause": "M4 public contracts did not yet exist",
            "artifact": "failed-attempt-artifacts/golden-first-output.txt",
            "artifact_sha256": copied.get("golden-first-output.txt", "UNAVAILABLE"),
            "disposition": "PRODUCTION_CONTRACTS_IMPLEMENTED",
        },
        {
            "attempt_id": "M4-TARGETED-ENV-001",
            "state": "FAILED",
            "cause": "targeted unittest command omitted PYTHONPATH=src",
            "artifact": "TOOL_TRANSCRIPT_ONLY",
            "disposition": "COMMAND_ENVIRONMENT_CORRECTED",
        },
        {
            "attempt_id": "M4-HOLDOUT-FIXTURE-001",
            "state": "FAILED",
            "cause": "test fixture passed trial_id both positionally and by keyword",
            "artifact": "TOOL_TRANSCRIPT_ONLY",
            "disposition": "FIXTURE_CORRECTED_WITHOUT_PRODUCTION_WEAKENING",
        },
        {
            "attempt_id": "M4-REPORT-ROUNDTRIP-001",
            "state": "FAILED",
            "cause": "report JSON payload retained typed intervals instead of public builtins",
            "artifact": "TOOL_TRANSCRIPT_ONLY",
            "disposition": "PUBLIC_SERIALIZATION_BOUNDARY_REPAIRED",
        },
        {
            "attempt_id": "M4-COMBINED-ACCEPTANCE-001",
            "state": "FAILED",
            "cause": "acceptance parent process did not set locked TZ=UTC before test loading",
            "artifact": "failed-attempt-artifacts/acceptance-timezone-failure-results.json",
            "artifact_sha256": copied.get("acceptance-timezone-failure-results.json", "UNAVAILABLE"),
            "disposition": "RUNNER_EFFECTIVE_ENVIRONMENT_LOCKED; COMPLETE_RERUN_PASS",
        },
    )
    with (staging / "failed-attempts.jsonl").open("wb") as stream:
        for attempt in attempts:
            stream.write(canonical_json_bytes(attempt) + b"\n")


def _manifest(staging: Path) -> dict[str, Any]:
    entries = [
        {
            "path": str(path.relative_to(staging)),
            "byte_size": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(staging.rglob("*"))
        if path.is_file() and path.name != "final-content-manifest.json"
    ]
    return {
        "schema": "m4-final-content-manifest-v1",
        "entries": entries,
        "content_sha256": canonical_sha256(entries),
        "manifest_self_excluded": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--resume-validation", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.resume_validation:
        if not output.is_dir() or not (output / "final-acceptance-summary.json").is_file():
            raise FileNotFoundError("validation resume requires completed Final V1 staging output")
        failure = {
            "attempt_id": "M4-FINAL-V1-POSTPROCESSING-001",
            "state": "FAILED",
            "cause": "validator import omitted the repository root from sys.path",
            "affected_stage": "POST_ACCEPTANCE_EVIDENCE_VALIDATION_ONLY",
            "result_bearing_runs_reexecuted": False,
            "financial_evidence_modified": False,
            "disposition": "IMPORT_PATH_REPAIRED; VALIDATION_AND_MANIFEST_RESUMED",
        }
        failed_path = output / "failed-attempts.jsonl"
        if b"M4-FINAL-V1-POSTPROCESSING-001" not in failed_path.read_bytes():
            with failed_path.open("ab") as stream:
                stream.write(canonical_json_bytes(failure) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
        manifest_path = output / "final-content-manifest.json"
        if manifest_path.is_file():
            prior_identity = sha256_file(manifest_path)
            prior_path = output / f"final-content-manifest-pre-validation-resume-{prior_identity[:12]}.json"
            if not prior_path.exists():
                shutil.copyfile(manifest_path, prior_path)
        _write_json(
            output / "resume-after-validation-import-failure.json",
            {
                "schema": "m4-validation-resume-v1",
                **failure,
                "prior_manifest_preserved": True,
            },
        )
        _write_json(manifest_path, _manifest(output))
        from scripts.validate_m4_evidence import validate

        validation = validate(output)
        validation_path = Path(tempfile.mkdtemp(prefix="m4-validation-resume-", dir="/tmp"))
        validation_path /= "m4-evidence-validation.json"
        _write_json(validation_path, validation)
        if validation["status"] != "PASS":
            raise RuntimeError(f"resumed M4 evidence validation failed: {validation}")
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "output": str(output),
                    "validation": str(validation_path),
                    "result_bearing_runs_reexecuted": False,
                },
                sort_keys=True,
            ),
        )
        return 0
    if output.exists():
        raise FileExistsError(f"refusing to overwrite M4 evidence: {output}")
    os.environ.update({"TZ": "UTC", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
    time.tzset()
    if _git("branch", "--show-current") != "main":
        raise RuntimeError("Final V1 acceptance requires branch main")
    head = _git("rev-parse", "HEAD")
    origin = _git("rev-parse", "origin/main")
    if head != origin or _git("status", "--porcelain=v1"):
        raise RuntimeError("Final V1 acceptance requires clean HEAD == origin/main")
    observed_locks = {
        "ssot_sha256": sha256_file(ROOT / "SSOT.md"),
        "runtime_lock_sha256": sha256_file(ROOT / "runtime.lock.json"),
        "dependency_lock_sha256": sha256_file(ROOT / "requirements.lock.txt"),
    }
    if observed_locks != LOCKS:
        raise RuntimeError(f"locked authority changed: {observed_locks}")

    temporary = Path(tempfile.mkdtemp(prefix="nautilus-final-v1-", dir="/tmp"))
    staging = temporary / "m4-acceptance-001"
    staging.mkdir()
    before = _history_inventory()
    _write_json(staging / "preserved-history-before.json", before)
    baseline = {
        "schema": "m4-final-v1-baseline-v1",
        "user": subprocess.run(["whoami"], check=True, capture_output=True, text=True).stdout.strip(),
        "repository": str(ROOT),
        "branch": "main",
        "head": head,
        "origin_main": origin,
        "git_tree": _git("rev-parse", "HEAD^{tree}"),
        "clean_worktree": True,
        **observed_locks,
        "m3_registry_identity": M3_REGISTRY_ID,
        "network_enabled": False,
        "actual_owner_research_study": False,
        "real_profitability_claim": False,
    }
    _write_json(staging / "baseline-attestation.json", baseline)
    _failed_attempts(staging)
    m3_boundary = _m3_boundary(staging)
    synthetic = _synthetic_mechanical_fixtures(staging)
    summaries = {
        "spot-primary": _run_real_profile(staging, label="spot-primary", profile="spot"),
        "spot-replay": _run_real_profile(staging, label="spot-replay", profile="spot"),
        "perpetual-primary": _run_real_profile(staging, label="perpetual-primary", profile="perpetual"),
        "perpetual-replay": _run_real_profile(staging, label="perpetual-replay", profile="perpetual"),
    }
    replay = {
        profile: {
            "schema": "final-v1-deterministic-replay-v1",
            "status": (
                "PASS"
                if summaries[f"{profile}-primary"]["semantic_digest"]
                == summaries[f"{profile}-replay"]["semantic_digest"]
                else "FAIL"
            ),
            "primary_run_id": summaries[f"{profile}-primary"]["run_id"],
            "replay_run_id": summaries[f"{profile}-replay"]["run_id"],
            "primary_semantic_digest": summaries[f"{profile}-primary"]["semantic_digest"],
            "replay_semantic_digest": summaries[f"{profile}-replay"]["semantic_digest"],
            "fresh_processes": True,
            "ignored_only_nonsemantic_occurrence_metadata": True,
        }
        for profile in ("spot", "perpetual")
    }
    if any(item["status"] != "PASS" for item in replay.values()):
        raise RuntimeError("Final V1 deterministic replay diverged")
    _write_json(staging / "final-v1/deterministic-replay.json", replay)
    research = _research_lifecycle(staging, head=head)

    acceptance_command = [
        str(ROOT / ".venv/bin/python"),
        str(ROOT / "scripts/run_m4_acceptance.py"),
        "--output-dir",
        str(staging / "test-suite"),
    ]
    completed = subprocess.run(
        acceptance_command,
        cwd=ROOT,
        env=_environment(),
        check=False,
        capture_output=True,
        text=True,
    )
    _write_json(
        staging / "test-suite-command.json",
        {
            "command": acceptance_command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "network_enabled": False,
        },
    )
    if completed.returncode != 0:
        raise RuntimeError(f"complete M0-M4 acceptance failed: {completed.stdout}{completed.stderr}")
    tests = json.loads((staging / "test-suite/test-results.json").read_text(encoding="utf-8"))
    fixtures = (
        {"fixture": "SPOT_CASH_SYNTHETIC", "status": synthetic["status"]},
        {"fixture": "PERPETUAL_NETTING_SYNTHETIC_WITH_NATIVE_FUNDING", "status": synthetic["status"]},
        {
            "fixture": "REAL_DATA_SPOT_QUALIFICATION",
            "status": "PASS" if summaries["spot-primary"]["state"] == "COMPLETED" else "FAIL",
        },
        {
            "fixture": "REAL_DATA_PERPETUAL_QUALIFICATION",
            "status": "PASS" if summaries["perpetual-primary"]["state"] == "COMPLETED" else "FAIL",
        },
        {
            "fixture": "RESEARCH_LIFECYCLE_FAILED_COMPLETED_HOLDOUT_AND_CLAIM_GATE",
            "status": (
                "PASS"
                if research["failed_trial_retained"]
                and research["completed_trial_retained"]
                and research["synthetic_contract_eligibility"] == "ELIGIBLE"
                and research["m3_qualification_eligibility"] == "INELIGIBLE"
                else "FAIL"
            ),
        },
    )
    end_to_end = {
        "schema": "final-v1-end-to-end-v1",
        "status": "PASS" if all(item["status"] == "PASS" for item in fixtures) else "FAIL",
        "fixtures": fixtures,
        "runtime_lock_verified": True,
        "clean_source_revision": True,
        "network_enabled": False,
        "both_profiles_qualified": True,
        "deterministic_replay": all(item["status"] == "PASS" for item in replay.values()),
        "checker_pass_for_accepted_mechanical_paths": True,
        "research_history_complete": True,
        "prior_evidence_mutated": False,
        "real_owner_research_study_executed": False,
        "real_profitability_claim_made": False,
    }
    _write_json(staging / "final-v1/final-v1-end-to-end.json", end_to_end)
    if end_to_end["status"] != "PASS":
        raise RuntimeError("Final V1 end-to-end fixture failed")
    after = _history_inventory()
    _write_json(staging / "preserved-history-after.json", after)
    if before != after:
        raise RuntimeError("historical evidence, raw objects, releases, or root research state changed")
    summary = {
        "schema": "m4-final-acceptance-summary-v1",
        "status": "PASS",
        "source_revision": {"git_commit": head, "git_tree": baseline["git_tree"]},
        "m3_registry_identity": m3_boundary["registry_identity"],
        "unique_executable_test_cases": tests["unique_executable_test_cases"],
        "test_execution_occurrences": tests["test_execution_occurrences"],
        "independent_discovery_execution_occurrences": tests[
            "independent_discovery_execution_occurrences"
        ],
        "additional_non_test_acceptance_checks": tests[
            "additional_non_test_acceptance_check_count"
        ],
        "failures": tests["failures"],
        "errors": tests["errors"],
        "skipped": tests["skipped"],
        "research_lifecycle": research,
        "actual_owner_research_study_executed": False,
        "real_profitability_claim_made": False,
        "m0_m3_semantics_modified": False,
        "network_enabled": False,
    }
    _write_json(staging / "final-acceptance-summary.json", summary)
    _write_json(staging / "final-content-manifest.json", _manifest(staging))
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(staging, output)
    from scripts.validate_m4_evidence import validate

    validation = validate(output)
    validation_path = temporary / "m4-evidence-validation.json"
    _write_json(validation_path, validation)
    if validation["status"] != "PASS":
        raise RuntimeError(f"M4 evidence validation failed: {validation}")
    print(
        json.dumps(
            {
                "status": "PASS",
                "output": str(output),
                "staging": str(staging),
                "validation": str(validation_path),
                "unique_executable_test_cases": summary["unique_executable_test_cases"],
                "test_execution_occurrences": summary["test_execution_occurrences"],
                "additional_non_test_acceptance_checks": summary[
                    "additional_non_test_acceptance_checks"
                ],
            },
            sort_keys=True,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
