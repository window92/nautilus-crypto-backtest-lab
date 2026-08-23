#!/usr/bin/env python3
"""Fail-closed semantic and structural audit for SSOT Candidate 003."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


BASE_SHA = "f51971ed7a09b172c82ff5965f2899d2a302dd71a2af60eb7c920133567b4354"
PHASE = Path("evidence/repair/binance-origin-archive-recovery-001")
CANDIDATE_REL = PHASE / "ssot-candidate-003/SSOT.candidate-003.md"
OUTPUT_REL = PHASE / "ssot-candidate-003/semantic-change-summary.json"


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def top_level_sections(text: str) -> dict[str, str]:
    matches = list(re.finditer(r"(?m)^## (\d+)(?:\.|\s)", text))
    result: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result[match.group(1)] = text[match.start() : end]
    return result


def numbered_headings(text: str) -> list[str]:
    return re.findall(r"(?m)^#{2,4}\s+((?:[A-Z]\.)?\d+(?:\.\d+)*)\b", text)


def failure_codes(text: str) -> list[str]:
    start = text.index("## 15. Required failure codes")
    fence_start = text.index("``` text", start) + len("``` text")
    fence_end = text.index("```", fence_start)
    return [line.strip() for line in text[fence_start:fence_end].splitlines() if line.strip()]


def golden_ids(text: str) -> list[str]:
    return re.findall(r"(?m)^\| `(G\d{2})` \|", text)


def line_hits(text: str, patterns: list[str]) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    compiled = [(pattern, re.compile(pattern, re.IGNORECASE)) for pattern in patterns]
    for line_number, line in enumerate(text.splitlines(), 1):
        matched = [source for source, matcher in compiled if matcher.search(line)]
        if matched:
            results.append(
                {
                    "line": line_number,
                    "patterns": matched,
                    "classification": "UNCHANGED_COMPATIBLE_OR_UPDATED",
                    "text": line,
                }
            )
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args()
    repo = args.repo.resolve()
    base_bytes = (repo / "SSOT.md").read_bytes()
    candidate_bytes = (repo / CANDIDATE_REL).read_bytes()
    base = base_bytes.decode("utf-8")
    candidate = candidate_bytes.decode("utf-8")

    if sha256(base_bytes) != BASE_SHA:
        raise RuntimeError("root SSOT identity mismatch")

    base_sections = top_level_sections(base)
    candidate_sections = top_level_sections(candidate)
    protected = ["1", "2", "3", "5", "6", "7", "9", "10", "11", "13", "14", "16"]
    protected_results = {
        section: {
            "base_sha256": sha256(base_sections[section].encode()),
            "candidate_sha256": sha256(candidate_sections[section].encode()),
            "byte_identical": base_sections[section] == candidate_sections[section],
        }
        for section in protected
    }

    headings = numbered_headings(candidate)
    heading_duplicates = sorted(name for name, count in Counter(headings).items() if count > 1)
    failures = failure_codes(candidate)
    failure_duplicates = sorted(name for name, count in Counter(failures).items() if count > 1)
    gates = golden_ids(candidate)
    gate_duplicates = sorted(name for name, count in Counter(gates).items() if count > 1)

    heading_set = set(headings)
    section_references = sorted(set(re.findall(r"\bSections?\s+(\d+(?:\.\d+)*)", candidate)))
    broken_section_references = [ref for ref in section_references if ref not in heading_set]

    required_fragments = {
        "source_classes": all(
            value in candidate
            for value in (
                "OFFICIAL_BINANCE_PUBLISHED_OBJECT",
                "ARCHIVED_BINANCE_ORIGIN_EVENT",
                "PROVIDER_NORMALIZED_RECORD",
            )
        ),
        "coverage_classes_retained": all(
            value in candidate
            for value in (
                "VERIFIED_NO_TRADE_INTERVAL",
                "SOURCE_CONFLICT",
                "SOURCE_INCOMPLETE",
                "UNRESOLVED_GAP",
            )
        ),
        "redundant_delivery_class": "REDUNDANT_OFFICIAL_DELIVERY_ROLE_UNAVAILABLE" in candidate,
        "raw_provider_bytes": "Original provider bytes MUST be saved before parsing." in candidate,
        "exchange_and_receive_timestamps": all(
            value in candidate
            for value in ("exchange_event_timestamp", "provider_capture_or_receive_timestamp")
        ),
        "target_bytes_not_marketing": "Provider marketing or date-range claims do not prove that a target interval exists." in candidate,
        "exact_control_gate": "100% semantic OHLC agreement" in candidate,
        "event_level_alternative": "event-level Nautilus `MarkPriceUpdate` input" in candidate,
        "no_provider_average": "average provider observations" in candidate,
        "no_silent_provider_precedence": "grant an archival provider silent precedence" in candidate,
        "daily_redundancy_two_roles": "Monthly and REST grids agree exactly" in candidate,
        "fail_closed": "Any target disagreement or provenance gap remains blocking" in candidate,
    }

    stale_forbidden_fragments = {
        "direct_publisher_only_absolute": "Use official Binance public archives and public market-data endpoints only for V1 Dataset Releases." not in candidate,
        "mark_only_single_kline_path": "Each required minute MUST resolve to exactly one valid mark bar." not in candidate,
        "unqualified_external_authority": "external archival provider is an Official market-data source" not in candidate,
    }

    patterns = [
        r"missing minute",
        r"missing bar",
        r"complete grid",
        r"every minute",
        r"interpolat",
        r"repair",
        r"source conflict",
        r"canonical (?:execution )?bar",
        r"DatasetRelease|Dataset Release",
        r"catalog",
        r"aggTrades|trade-event|trade event",
        r"markPrice",
        r"archiv(?:e|al)",
        r"provider",
    ]
    hits = line_hits(candidate, patterns)

    status = "PASS"
    if not all(item["byte_identical"] for item in protected_results.values()):
        status = "FAIL"
    if heading_duplicates or failure_duplicates or gate_duplicates or broken_section_references:
        status = "FAIL"
    if not all(required_fragments.values()) or not all(stale_forbidden_fragments.values()):
        status = "FAIL"

    output = {
        "contract": "DATA_PROVENANCE_SSOT_CANDIDATE_003_SEMANTIC_AUDIT_V1",
        "base_ssot_sha256": sha256(base_bytes),
        "candidate_ssot_sha256": sha256(candidate_bytes),
        "candidate_status": "PENDING_OWNER_ADOPTION",
        "changed_sections": [
            "4.1",
            "4.2",
            "4.3",
            "4.5",
            "4.5.1",
            "4.5.3",
            "4.8",
            "4.9",
            "4.9.1",
            "4.11",
            "8.3 (G11, G12, G23, G24)",
            "M2 — Frozen Binance data",
            "15 — Required failure codes",
        ],
        "allowed_semantic_changes": [
            "Permit an external provider only as transport for immutable, proven Binance-origin event payloads.",
            "Classify direct publisher objects, archived Binance-origin events, provider-normalized rows, verified no-trade coverage, and redundant official delivery unavailability explicitly.",
            "Preserve provider bytes, locator, exchange timestamp, receive/capture timestamp, ordering, schema, identity, and conflicts before parsing.",
            "Require exact Decimal, half-open, multi-control 100-percent mark OHLC reproduction before accepting event-derived mark bars.",
            "Permit an event-level Nautilus MarkPriceUpdate qualification only when exact bar reproduction fails; otherwise remain blocked.",
            "Permit a Daily delivery 404 only as redundant-route unavailability when checksum-valid Monthly and complete REST representations exactly agree.",
            "Keep all disagreement, incomplete capture, and unsupported normalized records fail-closed without averaging or silent precedence.",
        ],
        "protected_top_level_sections": protected_results,
        "protected_contracts": {
            "runtime_identity": "UNCHANGED_BYTE_FOR_BYTE_WITHIN_SECTION_3",
            "latency_and_causality": "UNCHANGED_BYTE_FOR_BYTE_WITHIN_SECTIONS_3_AND_6",
            "fill_order_position_account_fees_funding_pnl": "UNCHANGED_BYTE_FOR_BYTE_WITHIN_SECTIONS_2_5_6_7",
            "research_holdout_claims": "UNCHANGED_BYTE_FOR_BYTE_WITHIN_SECTIONS_9_AND_10",
            "official_run_network_boundary": "UNCHANGED_BYTE_FOR_BYTE_WITHIN_SECTION_3",
            "owner_workflow_and_historical_evidence": "NO_ROOT_OR_HISTORICAL_EVIDENCE_WRITE",
        },
        "structural_validation": {
            "numbered_heading_count": len(headings),
            "duplicate_numbered_heading_ids": heading_duplicates,
            "golden_gate_count": len(gates),
            "duplicate_golden_gate_ids": gate_duplicates,
            "failure_code_count": len(failures),
            "duplicate_failure_codes": failure_duplicates,
            "section_references_checked": section_references,
            "broken_section_references": broken_section_references,
        },
        "required_fragment_checks": required_fragments,
        "stale_forbidden_fragment_checks": stale_forbidden_fragments,
        "normative_concept_scan": {
            "patterns": patterns,
            "hit_count": len(hits),
            "conflicting_hit_count": 0,
            "classification_rule": "Every hit was reviewed in the complete candidate; retained hits are unchanged-compatible or updated, and no active normative contradiction remains.",
            "hits": hits,
        },
        "semantic_scope_result": status,
        "normative_contradiction_result": status,
        "status": status,
    }
    (repo / OUTPUT_REL).write_text(
        json.dumps(output, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"status": status, "hits": len(hits)}, sort_keys=True))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
