#!/usr/bin/env python3
"""Finalize the repair epoch with retained Replacement Owner Smoke outcomes."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPAIR = ROOT / "evidence/repair/instrument-representation-funding-checker-001"
RESEARCH = ROOT / "evidence/research/owner-smoke-002-replacement-001"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--acceptance-output", type=Path, required=True)
    args = parser.parse_args()
    acceptance_root = args.acceptance_output.resolve()
    acceptance = load(acceptance_root / "result.json")
    research_manifest = load(RESEARCH / "final-content-manifest.json")
    if (
        acceptance.get("status") != "PASS"
        or acceptance.get("unique_tests") != 268
        or acceptance.get("test_execution_occurrences") != 960
        or research_manifest.get("status") != "PASS"
    ):
        raise RuntimeError("final acceptance or replacement research evidence is not PASS")
    shutil.copyfile(acceptance_root / "result.json", REPAIR / "test-results.json")
    (REPAIR / "test-output.txt").write_bytes(
        (acceptance_root / "test-output.txt").read_bytes().rstrip(b"\n") + b"\n",
    )

    replacement = {
        "schema": "instrument-repair-replacement-owner-smoke-validation-v1",
        "status": "PASS",
        "research_evidence_path": str(RESEARCH.relative_to(ROOT)),
        "research_manifest_sha256": sha256_file(RESEARCH / "final-content-manifest.json"),
        "spot": {
            "trial_id": "owner-smoke-002-replacement-001-spot-sma20-development-retry-002",
            "dataset_release_id": "fd8542c109cfbf7d6b19d5b7bbb7705c6a161efc807695f3671978c381e34eca",
            "catalog_identity": "db0971d28caba547378e3acba5ad8df1cbd0d6d5be963d153248928a729e374f",
            "checker": "CHECK_PASS",
            "replay": "PASS",
            "orders": 27,
            "fills": 27,
            "net_pnl": "-751.78721000 USDT",
        },
        "perpetual": {
            "trial_id": "owner-smoke-002-replacement-001-perpetual-sma20-development",
            "dataset_release_id": "b6c8f5d659f3441c924b613d770342796c90b90a970f42a3dc8227c856198917",
            "catalog_identity": "7c96897a8e1ea3c02198238a277fb8c3d995f54dd90dc381e534a5f21b017ae0",
            "checker": "CHECK_PASS",
            "replay": "PASS",
            "orders": 55,
            "fills": 55,
            "net_pnl": "-3010.78713375 USDT",
            "source_funding_events": 636,
            "runtime_funding_updates": 1272,
            "native_financial_settlements": 539,
        },
        "strategy_semantics_changed": False,
        "canonical_market_numeric_values_changed": False,
        "final_holdout_used": False,
        "real_profitability_claim": False,
    }
    write_json(REPAIR / "replacement-owner-smoke-validation.json", replacement)

    additions = [
        {
            "attempt": "replacement-spot-base",
            "status": "FAILED_TRIAL_RETAINED",
            "cause": "Official child inherited non-UTC TZ and failed the locked runtime environment preflight",
            "run_ref": "runs/owner-smoke-002-replacement-001-spot-run-a754e2c26324",
            "repair_commit": "7b7ba1a",
        },
        {
            "attempt": "replacement-spot-retry-001",
            "status": "FAILED_TRIAL_RETAINED",
            "cause": "Sparse Spot executable Bar count was incorrectly compared to complete minute disposition count",
            "run_ref": "runs/owner-smoke-002-replacement-001-spot-run-retry-001-abbedb975f37",
            "repair_commit": "e02b9ff",
        },
        {
            "attempt": "replacement-spot-retry-002-report-attempt-001",
            "status": "BLOCKED_REPORT_RECOVERY_RETAINED",
            "cause": "Current checker reinterpretation conflicted with immutable historical failed evidence",
            "repair_commit": "9e24530",
        },
        {
            "attempt": "replacement-spot-retry-002-report-attempt-002",
            "status": "BLOCKED_REPORT_RECOVERY_RETAINED",
            "cause": "Semantically equal retained failure codes were compared in list order",
            "repair_commit": "c7c46a7",
        },
        {
            "attempt": "final-runtime-preflight-unbound-shell-environment",
            "status": "FAIL_RETAINED",
            "cause": "Manual final preflight invocation omitted the locked TZ=UTC environment and correctly failed with current timezone=None",
            "resolution": "Identical read-only preflight rerun with TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 passed",
            "official_run_affected": False,
        },
    ]
    failed_path = REPAIR / "failed-attempts.jsonl"
    existing = [
        json.loads(line)
        for line in failed_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    known = {item["attempt"] for item in existing}
    existing.extend(item for item in additions if item["attempt"] not in known)
    failed_path.write_text(
        "".join(json.dumps(item, sort_keys=True, ensure_ascii=False) + "\n" for item in existing),
        encoding="utf-8",
        newline="\n",
    )

    report_path = REPAIR / "owner-report/README.md"
    report = report_path.read_text(encoding="utf-8")
    report = report.replace(
        "بوابة القبول: 264 اختبارًا فريدًا، 944 execution occurrence",
        "بوابة القبول النهائية: 268 اختبارًا فريدًا، 960 execution occurrence",
    )
    old = """## الخطوة التالية

بعد commit/push لهذا الإصلاح ومن SourceRevision نظيفة فقط، تُنشأ Replacement Trials جديدة مرتبطة بالمحاولات الفاشلة، وتُشغل الاستراتيجية نفسها بلا تغيير.
"""
    new = """## Replacement Owner Smoke

أُنشئت Replacement Trials من SourceRevision نظيفة وبالاستراتيجية والنافذة والـparameters نفسها:

- Spot: `CHECK_PASS` وreplay `PASS`، 27 Order و27 Fill، وNet PnL `-751.78721000 USDT`.
- Perpetual: `CHECK_PASS` وreplay `PASS`، 55 Order و55 Fill، وNet PnL `-3010.78713375 USDT`.
- 636 source funding events ارتبطت بـ1,272 runtime updates، لكن عُدت 539 settlement مالية أصلية فقط للحدود المؤهلة.
- Final Holdout remained `false`، وReal profitability claim remained `false`؛ النتائج السلبية معروضة كما هي.

التقرير البحثي الكامل: `evidence/research/owner-smoke-002-replacement-001/owner-report/README.md`.
"""
    if old not in report and new not in report:
        raise RuntimeError("repair Owner report terminal section changed unexpectedly")
    report_path.write_text(
        report.replace(old, new) if old in report else report,
        encoding="utf-8",
        newline="\n",
    )

    old_manifest = load(REPAIR / "final-content-manifest.json")
    inventory = {
        path.relative_to(REPAIR).as_posix(): {
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(REPAIR.rglob("*"))
        if path.is_file() and path.name != "final-content-manifest.json"
    }
    old_manifest.update(
        {
            "status": "PASS",
            "finalized_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "files": inventory,
            "file_count_excluding_manifest": len(inventory),
            "replacement_owner_smoke_status": "PASS",
        },
    )
    write_json(REPAIR / "final-content-manifest.json", old_manifest)
    print(
        json.dumps(
            {
                "status": "PASS",
                "repair_manifest_sha256": sha256_file(REPAIR / "final-content-manifest.json"),
                "research_manifest_sha256": sha256_file(RESEARCH / "final-content-manifest.json"),
            },
            sort_keys=True,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
