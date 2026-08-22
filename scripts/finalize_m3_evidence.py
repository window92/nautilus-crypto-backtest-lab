#!/usr/bin/env python3
"""Write the immutable final M3 evidence inventory after all gates pass."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from scripts.validate_m3_evidence import validate


EVIDENCE = ROOT / "evidence/m3/m3-acceptance-001"
FINAL = EVIDENCE / "final-acceptance-manifest.json"


def main() -> int:
    if FINAL.exists():
        raise FileExistsError("refusing to overwrite final M3 acceptance manifest")
    tests = json.loads((EVIDENCE / "test-results.json").read_text(encoding="utf-8"))
    validation = validate(EVIDENCE)
    if tests["status"] != "PASS" or validation["status"] != "PASS":
        raise RuntimeError("M3 evidence cannot finalize before every gate passes")
    entries = [
        {
            "path": str(path.relative_to(EVIDENCE)),
            "byte_size": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(EVIDENCE.rglob("*"))
        if path.is_file() and path != FINAL
    ]
    payload = {
        "schema": "m3-final-acceptance-manifest-v1",
        "status": "PASS",
        "profiles": [
            "BINANCE_SPOT_CASH_LONG_ONLY",
            "BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING",
        ],
        "unique_executable_test_cases": tests["unique_executable_test_cases"],
        "test_execution_occurrences": tests["test_execution_occurrences"],
        "additional_non_test_acceptance_check_count": tests[
            "additional_non_test_acceptance_check_count"
        ],
        "failures": tests["failures"],
        "errors": tests["errors"],
        "skipped": tests["skipped"],
        "entries": entries,
        "inventory_content_sha256": canonical_sha256(entries),
        "manifest_self_excluded": True,
        "m4_started": False,
    }
    FINAL.write_bytes(canonical_json_bytes(payload) + b"\n")
    print(sha256_file(FINAL))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
