#!/usr/bin/env python3
"""Finalize additive evidence after native-metrics acceptance passes."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/repair/native-research-metrics-readiness-001"
REPORT_MARKER = "## القبول النهائي"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--acceptance-root", type=Path, required=True)
    arguments = parser.parse_args()
    acceptance = arguments.acceptance_root.resolve()
    result_path = acceptance / "result.json"
    output_path = acceptance / "test-output.txt"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("status") != "PASS" or any(
        value != "PASS" for value in result.get("gates", {}).values()
    ):
        raise RuntimeError("refusing to finalize failed native-metrics acceptance")
    shutil.copyfile(result_path, EVIDENCE / "test-results.json")
    shutil.copyfile(output_path, EVIDENCE / "test-output.txt")

    report_path = EVIDENCE / "owner-report/README.md"
    report = report_path.read_text(encoding="utf-8")
    report = report.split(REPORT_MARKER, 1)[0].rstrip()
    report += (
        "\n\n"
        + REPORT_MARKER
        + "\n"
        + "\n"
        + f"نجحت `{result['unique_tests']}` حالة اختبار فريدة عبر "
        + f"`{result['test_execution_occurrences']}` عملية تنفيذ، بما فيها discovery كامل "
        + "ومستقل وترتيب عكسي حتمي. كانت failures/errors/skips/xfail جميعها صفرًا، "
        + "ونجحت Runtime preflight وpip check وcompileall وhistorical evidence validators.\n\n"
        + "الحكم: `NATIVE_RESEARCH_METRICS_READINESS_PASS`. هذه جاهزية قياس فقط، "
        + "ولا تعني أن SMA20 مربحة ولا تغيّر نتائجها السابقة.\n"
    )
    report_path.write_text(report, encoding="utf-8", newline="\n")

    files = {
        path.relative_to(EVIDENCE).as_posix(): {
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(EVIDENCE.rglob("*"))
        if path.is_file() and path.name != "final-content-manifest.json"
    }
    manifest = {
        "schema": "native-research-metrics-readiness-final-content-manifest-v1",
        "status": "PASS",
        "created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "epoch": "NATIVE_RESEARCH_METRICS_READINESS_001",
        "files": files,
        "file_count_excluding_manifest": len(files),
        "strategy_rerun": False,
        "optimization_run": False,
        "final_holdout_used": False,
        "historical_run_evidence_modified": False,
        "project_trade_pairing_used": False,
        "project_pnl_engine_used": False,
        "raw_data_payloads_committed": False,
        "duckdb_payloads_committed": False,
        "catalog_payloads_committed": False,
        "secrets_present": False,
    }
    write_json(EVIDENCE / "final-content-manifest.json", manifest)
    print(
        json.dumps(
            {
                "status": "PASS",
                "file_count": len(files) + 1,
                "manifest_sha256": sha256_file(EVIDENCE / "final-content-manifest.json"),
            },
            sort_keys=True,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
