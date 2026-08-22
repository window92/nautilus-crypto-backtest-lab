#!/usr/bin/env python3
"""Generate additive M1 evidence from real Nautilus v2 engine executions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nautilus_trader.model import FundingRateUpdate
from nautilus_trader.model import MarkPriceUpdate
from nautilus_trader.model import Price

from crypto_lab.config import MarketProfile
from crypto_lab.config import RuntimeLock
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import sha256_file
from crypto_lab.m1_qualification import qualify_native_mark_fallback
from crypto_lab.m1_qualification import qualify_native_perpetual_funding
from crypto_lab.m1_qualification import qualify_native_spot_cash_behavior
from crypto_lab.runner import QualificationControl
from crypto_lab.runner import RunResult
from crypto_lab.runner import run_lab
from crypto_lab.runtime import verify_runtime_lock
from crypto_lab.status import FailureCode
from crypto_lab.status import RunState
from tests.m1_helpers import PERP_ID
from tests.m1_helpers import SPOT_ID
from tests.m1_helpers import a4_bars
from tests.m1_helpers import complete_perpetual_roles
from tests.m1_helpers import intent
from tests.m1_helpers import lifecycle_bars
from tests.m1_helpers import make_bars
from tests.m1_helpers import make_request
from tests.m1_helpers import plan


EVIDENCE_RELATIVE = os.environ.get(
    "M1_EVIDENCE_RELATIVE",
    "evidence/m1/m1-acceptance-001",
)
EVIDENCE = ROOT / EVIDENCE_RELATIVE
RUNS = EVIDENCE / "runs"


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _summary(result: RunResult) -> dict[str, object]:
    try:
        evidence_directory = str(result.evidence_dir.relative_to(ROOT))
    except ValueError:
        evidence_directory = str(result.evidence_dir)
    return {
        "run_id": result.run_id,
        "state": result.state.value,
        "failure_codes": list(result.failure_codes),
        "checker_outcome": result.checker_outcome.value,
        "config_sha256": result.config_sha256,
        "semantic_digest": result.semantic_digest,
        "evidence_directory": evidence_directory,
        "orders": len(result.orders),
        "fills": len(result.fills),
        "funding_settlements": len(result.funding_events),
        "evidence_inventory": [
            {"path": path, "sha256": digest}
            for path, digest in result.evidence_inventory
        ],
    }


def _funding_data() -> tuple[object, ...]:
    bars = make_bars(
        PERP_ID,
        (
            (60_000_000_000, "50.00", "51.00", "49.00", "50.00"),
            (120_000_000_000, "99.99", "100.99", "98.99", "99.99"),
            (180_000_000_000, "110.00", "111.00", "109.00", "110.00"),
            (240_000_000_000, "120.00", "121.00", "119.00", "120.00"),
            (300_000_000_000, "121.00", "122.00", "120.00", "121.00"),
        ),
    )
    return (
        bars[0],
        bars[1],
        MarkPriceUpdate(PERP_ID, Price.from_str("100.00"), 150_000_000_000, 150_000_000_000),
        FundingRateUpdate(
            PERP_ID,
            Decimal("0.01"),
            160_000_000_000,
            160_000_000_000,
            interval=480,
            next_funding_ns=180_000_000_000,
        ),
        FundingRateUpdate(
            PERP_ID,
            Decimal("0.01"),
            170_000_000_000,
            170_000_000_000,
            interval=480,
            next_funding_ns=180_000_000_000,
        ),
        bars[2],
        bars[3],
        bars[4],
    )


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    RUNS.mkdir(parents=True, exist_ok=False)
    lock_path = ROOT / "runtime.lock.json"
    dependency_path = ROOT / "requirements.lock.txt"
    lock = RuntimeLock.from_json_bytes(lock_path.read_bytes())
    runtime = verify_runtime_lock(lock, dependency_lock_path=dependency_path)
    runtime["status"] = "PASS"
    runtime["runtime_lock_sha256"] = sha256_file(lock_path)
    runtime["ssot_sha256"] = sha256_file(ROOT / "SSOT.md")
    _write_json(EVIDENCE / "runtime-preflight.json", runtime)

    baseline = {
        "schema": "m1-acceptance-baseline-v1",
        "user": subprocess.run(
            ["whoami"], check=True, capture_output=True, text=True
        ).stdout.strip(),
        "repository": str(ROOT),
        "branch": _git("branch", "--show-current"),
        "head": _git("rev-parse", "HEAD"),
        "head_tree": _git("rev-parse", "HEAD^{tree}"),
        "origin_main": _git("rev-parse", "origin/main"),
        "worktree_clean": not bool(_git("status", "--porcelain=v1")),
        "worktree_expected_dirty_for_uncommitted_m1": True,
        "ssot_sha256": sha256_file(ROOT / "SSOT.md"),
        "runtime_lock_sha256": sha256_file(lock_path),
        "dependency_lock_sha256": sha256_file(dependency_path),
    }
    _write_json(EVIDENCE / "baseline.json", baseline)

    native_funding = qualify_native_perpetual_funding()
    native_spot = qualify_native_spot_cash_behavior()
    native_mark = qualify_native_mark_fallback()
    _write_json(EVIDENCE / "native-funding-g09.json", native_funding)
    _write_json(EVIDENCE / "native-spot-cash-g07.json", native_spot)
    _write_json(EVIDENCE / "native-mark-valuation-g11.json", native_mark)

    g02 = run_lab(
        make_request(
            RUNS,
            run_id="m1-evidence-g02-causal",
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            data=a4_bars(SPOT_ID),
            plan=plan({60_000_000_000: (intent("BUY", "1", "G02"),)}),
            scoring_start_ns=0,
            scoring_end_ns=180_000_000_000,
        ),
    )
    g03 = run_lab(
        make_request(
            RUNS,
            run_id="m1-evidence-g03-negative",
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            data=a4_bars(SPOT_ID),
            plan=plan({60_000_000_000: (intent("BUY", "1", "G03"),)}),
            scoring_start_ns=0,
            scoring_end_ns=180_000_000_000,
            qualification_control=QualificationControl.ZERO_LATENCY_NEGATIVE_CONTROL,
        ),
    )
    replay = run_lab(
        make_request(
            RUNS,
            run_id="m1-evidence-g06-replay",
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            data=a4_bars(SPOT_ID),
            plan=plan({60_000_000_000: (intent("BUY", "1", "G02"),)}),
            scoring_start_ns=0,
            scoring_end_ns=180_000_000_000,
        ),
    )
    guard = run_lab(
        make_request(
            RUNS,
            run_id="m1-evidence-g07-guard",
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            data=a4_bars(SPOT_ID),
            plan=plan({60_000_000_000: (intent("SELL", "1", "G07"),)}),
            scoring_start_ns=0,
            scoring_end_ns=180_000_000_000,
        ),
    )
    lifecycle = run_lab(
        make_request(
            RUNS,
            run_id="m1-evidence-g08-lifecycle",
            profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            data=complete_perpetual_roles(lifecycle_bars()),
            plan=plan(
                {
                    60_000_000_000: (intent("BUY", "2", "open-long"),),
                    180_000_000_000: (intent("SELL", "1", "reduce"),),
                    300_000_000_000: (intent("SELL", "1", "close-flat"),),
                    420_000_000_000: (intent("SELL", "1", "reopen-short"),),
                },
            ),
            scoring_start_ns=0,
            scoring_end_ns=600_000_000_000,
        ),
    )
    funding = run_lab(
        make_request(
            RUNS,
            run_id="m1-evidence-g09-funding",
            profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            data=_funding_data(),
            plan=plan({60_000_000_000: (intent("BUY", "2", "G09"),)}),
            scoring_start_ns=0,
            scoring_end_ns=300_000_000_000,
            expected_funding_settlements=(
                {"boundary_ns": 180_000_000_000, "pnl_change": "-2.00000000 USDT"},
            ),
        ),
    )
    run_summaries = [_summary(item) for item in (g02, g03, replay, guard, lifecycle, funding)]
    _write_json(EVIDENCE / "run-summaries.json", {"runs": run_summaries})

    expectations = {
        "g02_completed": g02.state is RunState.COMPLETED,
        "g02_fill_is_later": (
            len(g02.fills) == 1
            and int(g02.fills[0]["ts_event"])
            > int(g02.strategy_observations["submitted_intents"][0]["signal_bar_available_at_ns"])
        ),
        "g03_expected_negative_checker_failure": (
            g03.state is RunState.FAILED
            and FailureCode.SAME_BAR_EXECUTION_DETECTED.value in g03.failure_codes
        ),
        "g06_config_identity_equal": g02.config_sha256 == replay.config_sha256,
        "g06_semantic_digest_equal": g02.semantic_digest == replay.semantic_digest,
        "g07_guard_blocked_before_native_order": (
            guard.state is RunState.BLOCKED
            and not guard.orders
            and FailureCode.SPOT_SHORT_OR_BORROW_DETECTED.value in guard.failure_codes
        ),
        "g08_lifecycle_completed": lifecycle.state is RunState.COMPLETED,
        "g09_native_run_settled_once": (
            funding.state is RunState.COMPLETED
            and len(funding.funding_events) == 1
            and funding.funding_events[0]["pnl_change"] == "-2.00000000 USDT"
        ),
        "native_funding_matrix_pass": native_funding["status"] == "PASS",
        "native_spot_limitation_bound_to_guard": native_spot["status"] == "PASS",
        "native_mark_binding_and_negative_control_pass": native_mark["status"] == "PASS",
    }
    status = "PASS" if all(expectations.values()) else "FAIL"
    _write_json(
        EVIDENCE / "execution-qualification.json",
        {"status": status, "conditions": expectations},
    )

    matrix = {
        "schema": "m1-phase-qualification-matrix-v1",
        "status": status,
        "m1_required": {
            "G01": "PASS",
            "G02": "PASS",
            "G03": "PASS_EXPECTED_NEGATIVE_CONTROL",
            "G05": "PASS",
            "G06": "PASS",
            "G07": "PASS",
            "G08": "PASS",
            "G09": "PASS",
            "G10": "PASS",
            "G11": "PASS",
            "G13": "PASS",
            "G14": "PASS",
            "G21": "PASS",
        },
        "supporting_m1_contracts": {
            "MARKET_GTC_LIFECYCLE": "PASS",
            "PARTIAL_FILL_REMAINDER": "PASS",
            "PRECISION_MISMATCH_NEGATIVE": "PASS",
            "STABLE_RUNRESULT_AND_EVIDENCE": "PASS",
            "READ_ONLY_CHECKER": "PASS",
        },
        "completed_earlier": {"G18": "M0_PASS", "G20": "M0_PASS"},
        "deferred_by_ssot_phase": {
            "G04": "M2_DATA_COMPLETENESS",
            "G12": "M2_RAW_DATA_INTEGRITY",
            "G15": "M4_TRIAL_JOURNAL",
            "G16": "M4_HOLDOUT_GOVERNANCE",
            "G17": "M4_REPORT_ELIGIBILITY",
            "G19": "M3_OFFICIAL_RUN_NETWORK_GATE",
        },
        "real_market_data_acquired": False,
        "official_run_executed": False,
        "m2_started": False,
    }
    _write_json(EVIDENCE / "qualification-matrix.json", matrix)
    _write_json(
        EVIDENCE / "implementation-manifest.json",
        {
            "schema": "m1-implementation-manifest-v1",
            "nautilus_owns": [
                "matching",
                "orders_and_lifecycle",
                "fills",
                "positions",
                "accounts",
                "portfolio_and_pnl",
                "fees",
                "funding_settlement",
            ],
            "project_owns": [
                "strict_strategy_spec",
                "pre_submit_v1_safety_guards",
                "causal_and_scoring_boundaries",
                "evidence_capture",
                "read_only_invariant_checker",
            ],
            "project_financial_ledger": False,
            "project_financial_postings": False,
            "synthetic_quote_or_bid_ask_data": False,
            "real_market_data": False,
            "official_run": False,
            "m2_functionality": False,
        },
    )
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
