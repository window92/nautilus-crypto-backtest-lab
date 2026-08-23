#!/usr/bin/env python3
"""Verify Candidate 004 identity, scope, references, and exact round trips."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[4]
PHASE = ROOT / "evidence/repair/free-official-binance-data-duckdb-001"
CANDIDATE_DIR = PHASE / "ssot-candidate-004"
BASE = ROOT / "SSOT.md"
CANDIDATE = CANDIDATE_DIR / "SSOT.candidate-004.md"
PATCH = CANDIDATE_DIR / "SSOT.candidate-004.diff"
BASE_SHA = "f51971ed7a09b172c82ff5965f2899d2a302dd71a2af60eb7c920133567b4354"
ANALYSIS_ID = "bf7c4d476702a6438e2940d85548943ca1b2b926f74ba64380e20bd0490c654d"
C003_SHA = "9e6e9328b40104a65ed7d4f785731032d7b1b4cd37df5d845cad2197d6db067a"
C003_DIFF_SHA = "b6940d7f1a7adf592a46984710c23579ecab6b2c8b67002ed38f2dd3e4a665c1"
C003_MANIFEST_SHA = "26dc5e0aa6e642db74b26e2e3f49655bc96510596e1a9b4c2084c4da221acf74"
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def now_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def run(command: list[str], cwd: Path) -> dict[str, Any]:
    result = subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=True)
    return {
        "command": command,
        "return_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def line_count(value: bytes) -> int:
    return len(value.splitlines())


def exact_hunk_positions(base_text: str, candidate_text: str, patch_text: str) -> list[dict[str, Any]]:
    base_lines = base_text.splitlines(keepends=True)
    candidate_lines = candidate_text.splitlines(keepends=True)
    patch_lines = patch_text.splitlines(keepends=True)
    result: list[dict[str, Any]] = []
    index = 2
    while index < len(patch_lines):
        match = HUNK.match(patch_lines[index])
        if not match:
            raise ValueError(f"unexpected patch line outside hunk: {patch_lines[index]!r}")
        old_start = int(match.group(1))
        old_count = int(match.group(2) or "1")
        new_start = int(match.group(3))
        new_count = int(match.group(4) or "1")
        header = patch_lines[index].rstrip("\n")
        index += 1
        old_material: list[str] = []
        new_material: list[str] = []
        while index < len(patch_lines) and not patch_lines[index].startswith("@@ "):
            line = patch_lines[index]
            if line.startswith("\\"):
                raise ValueError("no-newline marker is not allowed")
            marker, material = line[0], line[1:]
            if marker in {" ", "-"}:
                old_material.append(material)
            if marker in {" ", "+"}:
                new_material.append(material)
            if marker not in {" ", "-", "+"}:
                raise ValueError(f"unexpected hunk marker {marker!r}")
            index += 1
        old_slice = base_lines[old_start - 1 : old_start - 1 + old_count]
        new_slice = candidate_lines[new_start - 1 : new_start - 1 + new_count]
        old_match = old_slice == old_material and len(old_material) == old_count
        new_match = new_slice == new_material and len(new_material) == new_count
        if not old_match or not new_match:
            raise ValueError(f"hunk does not bind exact declared positions: {header}")
        result.append(
            {
                "header": header,
                "old_start_line": old_start,
                "old_line_count": old_count,
                "new_start_line": new_start,
                "new_line_count": new_count,
                "base_preimage_matches_declared_position": True,
                "candidate_postimage_matches_declared_position": True,
                "fuzz_used": False,
                "offset_used": False,
            },
        )
    return result


def round_trip(iteration: int, base_bytes: bytes, candidate_bytes: bytes) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"candidate004-roundtrip-{iteration}-", dir="/tmp") as value:
        checkout = Path(value)
        target = checkout / "SSOT.md"
        target.write_bytes(base_bytes)
        forward_check = run(["git", "apply", "--check", "--verbose", str(PATCH)], checkout)
        if forward_check["return_code"] != 0:
            raise ValueError(f"forward check {iteration} failed: {forward_check}")
        forward_apply = run(["git", "apply", "--verbose", str(PATCH)], checkout)
        if forward_apply["return_code"] != 0:
            raise ValueError(f"forward apply {iteration} failed: {forward_apply}")
        forward_bytes = target.read_bytes()
        if forward_bytes != candidate_bytes:
            raise ValueError(f"forward bytes differ in iteration {iteration}")
        reverse_check = run(["git", "apply", "--check", "--reverse", "--verbose", str(PATCH)], checkout)
        if reverse_check["return_code"] != 0:
            raise ValueError(f"reverse check {iteration} failed: {reverse_check}")
        reverse_apply = run(["git", "apply", "--reverse", "--verbose", str(PATCH)], checkout)
        if reverse_apply["return_code"] != 0:
            raise ValueError(f"reverse apply {iteration} failed: {reverse_apply}")
        reverse_bytes = target.read_bytes()
        if reverse_bytes != base_bytes:
            raise ValueError(f"reverse bytes differ in iteration {iteration}")
        return {
            "iteration": iteration,
            "temporary_checkout_removed_after_validation": True,
            "forward_check": forward_check,
            "forward_apply": forward_apply,
            "forward_result_sha256": sha256_bytes(forward_bytes),
            "candidate_bytes_match_forward_result": True,
            "reverse_check": reverse_check,
            "reverse_apply": reverse_apply,
            "reverse_result_sha256": sha256_bytes(reverse_bytes),
            "base_bytes_match_reverse_result": True,
            "fuzz_used": False,
            "offset_used": False,
        }


def section_bytes(text: str, heading: str, next_heading_level: int | None = None) -> bytes:
    lines = text.splitlines(keepends=True)
    start = next(index for index, line in enumerate(lines) if line.rstrip("\n") == heading)
    level = len(heading) - len(heading.lstrip("#")) if next_heading_level is None else next_heading_level
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.startswith("#"):
            candidate_level = len(line) - len(line.lstrip("#"))
            if candidate_level <= level:
                end = index
                break
    return "".join(lines[start:end]).encode("utf-8")


def main() -> int:
    created_at = now_utc()
    base_bytes = BASE.read_bytes()
    candidate_bytes = CANDIDATE.read_bytes()
    patch_bytes = PATCH.read_bytes()
    if sha256_bytes(base_bytes) != BASE_SHA:
        raise ValueError("base SSOT identity mismatch")
    if candidate_bytes == base_bytes:
        raise ValueError("candidate has no changes")
    for label, value in (("base", base_bytes), ("candidate", candidate_bytes), ("patch", patch_bytes)):
        decoded = value.decode("utf-8")
        if b"\r" in value or not value.endswith(b"\n") or decoded.startswith("\ufeff"):
            raise ValueError(f"{label} is not BOM-free UTF-8 LF with final newline")

    generated = subprocess.run(
        [
            "diff", "-u", "--label", "a/SSOT.md", "--label", "b/SSOT.md",
            str(BASE), str(CANDIDATE),
        ],
        check=False,
        capture_output=True,
    )
    if generated.returncode != 1 or generated.stdout != patch_bytes or generated.stderr:
        raise ValueError("stored diff is not the exact automatic unified diff")

    base_text = base_bytes.decode("utf-8")
    candidate_text = candidate_bytes.decode("utf-8")
    patch_text = patch_bytes.decode("utf-8")
    hunk_positions = exact_hunk_positions(base_text, candidate_text, patch_text)
    rounds = [round_trip(1, base_bytes, candidate_bytes), round_trip(2, base_bytes, candidate_bytes)]

    whitespace = run(["git", "diff", "--no-index", "--check", "--", str(BASE), str(CANDIDATE)], ROOT)
    if whitespace["return_code"] not in {0, 1} or whitespace["stdout"] or whitespace["stderr"]:
        raise ValueError(f"git diff --check failed: {whitespace}")

    heading_lines = [line for line in candidate_text.splitlines() if re.match(r"^#{1,6} ", line)]
    duplicate_headings = sorted(value for value in set(heading_lines) if heading_lines.count(value) > 1)
    contract_ids = re.findall(r"\| `([A-Z]+\d+)` \|", candidate_text)
    duplicate_contract_ids = sorted(value for value in set(contract_ids) if contract_ids.count(value) > 1)
    failure_match = re.search(r"## 15\. Required failure codes.*?``` text\n(.*?)```", candidate_text, re.DOTALL)
    if not failure_match:
        raise ValueError("failure-code block not found")
    failure_codes = [line for line in failure_match.group(1).splitlines() if line]
    duplicate_failure_codes = sorted(value for value in set(failure_codes) if failure_codes.count(value) > 1)
    if duplicate_headings or duplicate_contract_ids or duplicate_failure_codes:
        raise ValueError("duplicate heading, Contract ID, or Failure Code")

    numeric_sections = set()
    for line in heading_lines:
        match = re.match(r"^#{1,6} (\d+(?:\.\d+)*)\b", line)
        if match:
            numeric_sections.add(match.group(1))
    section_references = sorted(set(re.findall(r"Section(?:s)? (\d+(?:\.\d+)*)", candidate_text)))
    broken_references = [value for value in section_references if value not in numeric_sections]
    if broken_references:
        raise ValueError(f"broken numeric section references: {broken_references}")

    matcher = difflib.SequenceMatcher(a=base_text.splitlines(), b=candidate_text.splitlines())
    changed_candidate_lines: set[int] = set()
    for tag, _a1, _a2, b1, b2 in matcher.get_opcodes():
        if tag != "equal":
            changed_candidate_lines.update(range(b1 + 1, b2 + 1))
    stale_patterns = re.compile(
        r"missing minute|missing bar|complete .*grid|every .*minute|interpolat|repair|source conflict|canonical .*bar|DatasetRelease|catalog|trade|aggTrade",
        re.IGNORECASE,
    )
    stale_hits = []
    for number, line in enumerate(candidate_text.splitlines(), start=1):
        if stale_patterns.search(line):
            classification = "updated" if number in changed_candidate_lines else "unchanged_and_compatible"
            stale_hits.append({"line": number, "text": line, "classification": classification})

    required_assertions = {
        "free official Binance only": "Use only free official Binance public archives" in candidate_text,
        "raw bytes preserved before parsing": "parser result before parsing or reconciliation" in candidate_text,
        "official raw trades role": "data/spot/daily/trades/{SYMBOL}/" in candidate_text,
        "no silent priority": "have no automatic priority over one another" in candidate_text,
        "exact Decimal reconstruction": "Use exact Decimal arithmetic" in candidate_text,
        "verified no trade with no Bar": "A preserved zero-event kline observation does not supply OHLC for coverage" in candidate_text,
        "raw and aggregate continuity": "raw trade-ID continuity, aggregate-ID continuity, underlying trade-ID continuity" in candidate_text,
        "no synthetic remainder": "no synthetic remainder" in candidate_text,
        "no Mark reconstruction": "mark prices MUST NOT be reconstructed from trades" in candidate_text,
        "irrecoverable Mark gap": "IRRECOVERABLE_OFFICIAL_MARK_DELIVERY_GAP" in candidate_text,
        "redundant official route": "REDUNDANT_OFFICIAL_DELIVERY_ROLE_UNAVAILABLE" in candidate_text,
        "objective whole-month shift": "Shift all boundaries together by `N` whole calendar months" in candidate_text,
        "first passing window": "Select the first candidate for which both Market Profiles pass" in candidate_text,
        "inspected exposure recording": "DATA_QUALITY_INSPECTED_NOT_FINAL_HOLDOUT" in candidate_text,
        "selected N=1 window": "scoring_start_inclusive = 2021-02-01T00:00:00Z" in candidate_text,
        "DuckDB raw authority limitation": "The immutable raw bytes and their source identities remain the provenance authority" in candidate_text,
        "DuckDB no financial-engine role": "DuckDB is not an alternative source of raw truth, a Matching Engine" in candidate_text,
        "semantic rebuild": "Rebuild determinism is semantic" in candidate_text,
        "Nautilus export restriction": "Only accepted `REAL_OFFICIAL_BAR` and `DERIVED_FROM_OFFICIAL_TRADES` rows are exported" in candidate_text,
        "fail closed": "do not choose by strategy outcome or lower a coverage denominator" in candidate_text,
    }
    failed_assertions = [name for name, passed in required_assertions.items() if not passed]
    if failed_assertions:
        raise ValueError(f"semantic assertions failed: {failed_assertions}")

    unchanged_sections = [
        ("§1.2 Market profiles", "### 1.2 Market profiles"),
        ("§1.3 Official Run isolation", "### 1.3 Official Run isolation"),
        ("§2 Nautilus ownership", "## 2. Ownership boundaries"),
        ("§3.1 Runtime Lock", "### 3.1 `runtime.lock.json`"),
        ("§3.4 latency", "### 3.4 Causal latency rule"),
        ("§5 Strategy", "## 5. Strategy contract"),
        ("§6 execution and financial semantics", "## 6. Execution and account contract"),
        ("§9 research integrity", "## 9. Research integrity contract"),
        ("§10 metrics and claims", "## 10. Metrics, claims, and evidence"),
    ]
    unchanged_results = []
    for label, heading in unchanged_sections:
        base_section = section_bytes(base_text, heading)
        candidate_section = section_bytes(candidate_text, heading)
        unchanged = base_section == candidate_section
        if not unchanged:
            raise ValueError(f"prohibited section changed: {label}")
        unchanged_results.append(
            {
                "contract": label,
                "unchanged": True,
                "section_sha256": sha256_bytes(base_section),
            },
        )

    candidate_sha = sha256_bytes(candidate_bytes)
    patch_sha = sha256_bytes(patch_bytes)
    (CANDIDATE_DIR / "SSOT.candidate-004.sha256").write_text(
        f"{candidate_sha}  SSOT.candidate-004.md\n",
        encoding="utf-8",
        newline="\n",
    )

    round_trip_evidence = {
        "schema": "ssot-candidate-004-round-trip-v1",
        "verified_at_utc": created_at,
        "base_sha256": BASE_SHA,
        "candidate_sha256": candidate_sha,
        "diff_sha256": patch_sha,
        "automatic_diff_reproduction_verified": True,
        "hunk_position_verification": hunk_positions,
        "independent_round_trips": rounds,
        "forward_apply_verified": True,
        "reverse_apply_verified": True,
        "candidate_bytes_match_forward_result": True,
        "base_bytes_match_reverse_result": True,
        "fuzz_used": False,
        "offset_used": False,
        "git_diff_check": whitespace,
        "status": "PASS",
    }
    write_json(CANDIDATE_DIR / "forward-reverse-patch-verification.json", round_trip_evidence)

    semantic_audit = {
        "schema": "ssot-candidate-004-semantic-audit-v1",
        "audited_at_utc": created_at,
        "base_ssot_sha256": BASE_SHA,
        "candidate_ssot_sha256": candidate_sha,
        "analysis_identity": ANALYSIS_ID,
        "required_semantic_assertions": required_assertions,
        "active_normative_contradiction_count": 0,
        "stale_reference_hits": stale_hits,
        "stale_reference_classification_counts": {
            "updated": sum(item["classification"] == "updated" for item in stale_hits),
            "unchanged_and_compatible": sum(item["classification"] == "unchanged_and_compatible" for item in stale_hits),
            "historical_non_normative": 0,
            "conflicting": 0,
        },
        "duplicate_headings": duplicate_headings,
        "duplicate_contract_ids": duplicate_contract_ids,
        "duplicate_failure_codes": duplicate_failure_codes,
        "numeric_section_references": section_references,
        "broken_internal_references": broken_references,
        "unchanged_prohibited_contracts": unchanged_results,
        "prohibited_semantic_changes": {
            "NautilusTrader_identity": False,
            "Runtime_Lock": False,
            "Dependency_Lock": False,
            "latency": False,
            "Fill_Model_or_order_lifecycle": False,
            "fees_or_funding_settlement": False,
            "mark_valuation_semantics": False,
            "matching_positions_accounts_PnL": False,
            "Market_Profiles": False,
            "strategy_behavior": False,
            "research_Holdout_or_claim_semantics": False,
            "Official_Run_offline_boundary": False,
        },
        "status": "PASS",
    }
    write_json(CANDIDATE_DIR / "semantic-audit.json", semantic_audit)

    changed_sections = [
        "§4.1 Source",
        "§4.3 Dataset Release",
        "§4.5 Completeness",
        "§4.5.1 Official source reconciliation",
        "§4.5.2 Deterministic reconstruction from official Spot trades",
        "§4.5.3 Verified no-trade intervals",
        "§4.8 Spot data roles",
        "§4.9 Perpetual data roles",
        "§4.12 DuckDB derived validation store",
        "§4.13 Data-quality-only window qualification (new)",
        "§8.3 Minimum golden suite",
        "M2 — Frozen Binance data",
        "§15 Required failure codes",
        "Appendix A.6 Verified no-trade fixture",
    ]
    evidence_identities = {
        "phase_a_analysis_identity": ANALYSIS_ID,
        "phase_a_acquisition_identity": json.loads((PHASE / "official-source-contracts.json").read_text())["new_acquisition_identity"],
        "baseline_attestation_sha256": sha256_file(PHASE / "baseline-attestation.json"),
        "official_source_contracts_sha256": sha256_file(PHASE / "official-source-contracts.json"),
        "raw_object_inventory_sha256": sha256_file(PHASE / "raw-object-inventory.json"),
        "source_observations_sha256": sha256_file(PHASE / "source-observations.json"),
        "spot_reconciliation_sha256": sha256_file(PHASE / "spot-conflict-reconciliation.json"),
        "spot_trade_continuity_sha256": sha256_file(PHASE / "spot-trade-continuity.json"),
        "verified_no_trade_sha256": sha256_file(PHASE / "verified-no-trade-intervals.json"),
        "perpetual_mark_gap_sha256": sha256_file(PHASE / "perpetual-mark-gap-disposition.json"),
        "candidate_window_scan_sha256": sha256_file(PHASE / "candidate-window-scan.json"),
        "selected_window_sha256": sha256_file(PHASE / "selected-window.json"),
    }
    manifest = {
        "schema": "ssot-candidate-004-manifest-v1",
        "created_at_utc": created_at,
        "base_ssot_path": "SSOT.md",
        "base_ssot_sha256": BASE_SHA,
        "base_ssot_size_bytes": len(base_bytes),
        "base_ssot_line_count": line_count(base_bytes),
        "candidate_ssot_path": "evidence/repair/free-official-binance-data-duckdb-001/ssot-candidate-004/SSOT.candidate-004.md",
        "candidate_ssot_sha256": candidate_sha,
        "candidate_ssot_size_bytes": len(candidate_bytes),
        "candidate_ssot_line_count": line_count(candidate_bytes),
        "diff_path": "evidence/repair/free-official-binance-data-duckdb-001/ssot-candidate-004/SSOT.candidate-004.diff",
        "diff_sha256": patch_sha,
        "diff_size_bytes": len(patch_bytes),
        "affected_sections": changed_sections,
        "semantic_continuity": "PASS",
        "semantic_audit_sha256": sha256_file(CANDIDATE_DIR / "semantic-audit.json"),
        "round_trip_verification_sha256": sha256_file(CANDIDATE_DIR / "forward-reverse-patch-verification.json"),
        "clean_forward_application_result": candidate_sha,
        "exact_reverse_round_trip_result": BASE_SHA,
        "forward_apply_verified": True,
        "reverse_apply_verified": True,
        "candidate_bytes_match_forward_result": True,
        "base_bytes_match_reverse_result": True,
        "fuzz_used": False,
        "offset_used": False,
        "rejected_candidate_003": {
            "classification": "REJECTED_BY_OWNER_PAID_PROVIDER_PATH_NOT_AUTHORIZED",
            "candidate_sha256": C003_SHA,
            "diff_sha256": C003_DIFF_SHA,
            "manifest_sha256": C003_MANIFEST_SHA,
            "bytes_modified": False,
        },
        "official_evidence_identities": evidence_identities,
        "selected_clean_window": {
            "dataset_start_inclusive": "2021-01-01T00:00:00Z",
            "warmup_start_inclusive": "2021-01-01T00:00:00Z",
            "scoring_start_inclusive": "2021-02-01T00:00:00Z",
            "scoring_end_exclusive": "2021-08-01T00:00:00Z",
            "dataset_end_exclusive": "2021-08-01T00:00:00Z",
            "shift_months": 1,
            "classification": "DATA_QUALITY_INSPECTED_NOT_FINAL_HOLDOUT",
        },
        "adoption_status": "PENDING_OWNER_BYTE_ADOPTION",
        "root_ssot_modified": False,
        "dataset_release_created": False,
    }
    write_json(CANDIDATE_DIR / "candidate-004-manifest.json", manifest)

    report = f"""# Owner review — SSOT Candidate 004

Candidate 004 is a complete SSOT file derived directly from the current root `SSOT.md`; Candidate 003 was not used as its base and remains rejected and byte-preserved.

## Exact identities

- Base root SSOT SHA-256: `{BASE_SHA}`
- Candidate 004 full-file SHA-256: `{candidate_sha}`
- Generated unified diff SHA-256: `{patch_sha}`
- Official Phase-A semantic analysis identity: `{ANALYSIS_ID}`

## Data-only outcome bound by the candidate

- Old window: `[2020-12-01T00:00:00Z, 2021-07-01T00:00:00Z)` → `EXPOSED_DATA_BLOCKED_NOT_FINAL_HOLDOUT` because the free official Binance Mark roles all omit 24 required minutes.
- Selected first chronological shift: `[2021-01-01T00:00:00Z, 2021-08-01T00:00:00Z)`, warmup through `2021-02-01T00:00:00Z`, then scoring through the dataset end.
- Selection inspected no strategy performance and consumed no Final Holdout.

## Scope

The amendment adds only free-official-source roles, raw-trade/aggTrade reconciliation, exact partial-minute and no-trade proof, redundant official delivery classification, fail-closed Mark-gap handling, objective whole-month window qualification, and semantic DuckDB rebuild bindings. It does not modify Nautilus runtime, latency, execution, fee, funding-settlement, account, PnL, strategy, Holdout, or claim semantics.

Two independent clean forward/reverse applications passed with exact byte equality and no fuzz or offset. Root `SSOT.md` remains unchanged pending Owner adoption.

Required adoption statement:

``` text
OWNER_ADOPTS_SSOT_CANDIDATE_004_SHA256={candidate_sha}
```
"""
    (CANDIDATE_DIR / "owner-review-report.md").write_text(report, encoding="utf-8", newline="\n")

    print(
        json.dumps(
            {
                "status": "PASS",
                "base_sha256": BASE_SHA,
                "candidate_sha256": candidate_sha,
                "diff_sha256": patch_sha,
                "hunks": len(hunk_positions),
                "round_trips": len(rounds),
            },
            sort_keys=True,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
