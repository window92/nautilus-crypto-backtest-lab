#!/usr/bin/env python3
"""Fail-closed validator for OWNER_SMOKE_002 replacement 001 evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/research/owner-smoke-002-replacement-001"
REQUIRED = {
    "baseline-attestation.json",
    "owner-authorization.json",
    "protocol.json",
    "strategy-identities.json",
    "dataset-bindings.json",
    "trial-records.jsonl",
    "spot/run-result.json",
    "spot/checker-result.json",
    "spot/replay-result.json",
    "spot/metrics.json",
    "perpetual/run-result.json",
    "perpetual/checker-result.json",
    "perpetual/replay-result.json",
    "perpetual/metrics.json",
    "offline-enforcement.json",
    "deterministic-replay.json",
    "funding-and-mark-validation.json",
    "research-eligibility.json",
    "failed-attempts.jsonl",
    "test-results.json",
    "test-output.txt",
    "owner-report/README.md",
    "spot-report/README.md",
    "perpetual-report/README.md",
    "mechanical-integrity/README.md",
    "deterministic-replay/README.md",
    "charts/spot-equity.svg",
    "charts/spot-drawdown.svg",
    "charts/spot-position.svg",
    "charts/spot-fees.svg",
    "charts/perpetual-equity.svg",
    "charts/perpetual-drawdown.svg",
    "charts/perpetual-position.svg",
    "charts/perpetual-fees.svg",
    "charts/perpetual-funding.svg",
    "charts/spot-vs-perpetual-equity.svg",
    "evidence-inventory.json",
    "final-content-manifest.json",
}
EXPECTED_LOCKS = {
    "SSOT.md": "b4deb7048242239234de7eaa353b623b3e45247eb42f1021dbc26ffd910edb99",
    "runtime.lock.json": "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd",
    "requirements.lock.txt": "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47",
}
EXPECTED = {
    "spot": {
        "dataset_release_id": "fd8542c109cfbf7d6b19d5b7bbb7705c6a161efc807695f3671978c381e34eca",
        "catalog_identity": "db0971d28caba547378e3acba5ad8df1cbd0d6d5be963d153248928a729e374f",
        "strategy_identity": "36a8da3b30f72b20872d12f1556ee6c2b0776c61a2685a05733094970bd96fca",
        "replay_identity": "60a312df85e5bba027306db63ddb007e51f48996fabb168f06cd6209827a6387",
        "orders": 27,
        "fills": 27,
        "net_pnl": "-751.78721000",
        "fees": "119.59221000",
        "ending_equity": "9248.21279000",
    },
    "perpetual": {
        "dataset_release_id": "b6c8f5d659f3441c924b613d770342796c90b90a970f42a3dc8227c856198917",
        "catalog_identity": "7c96897a8e1ea3c02198238a277fb8c3d995f54dd90dc381e534a5f21b017ae0",
        "strategy_identity": "6493e4e80528ea818ba6f0d9f7841d957349cc188576eea97a6d50e3b94492f9",
        "replay_identity": "c02f6b6f0c304dbb6eed9891f43c92c371f40989d3219f6e53b2411e481f4f3a",
        "orders": 55,
        "fills": 55,
        "net_pnl": "-3010.78713375",
        "fees": "242.69077200",
        "funding": "-692.06436175",
        "ending_equity": "6989.21286625",
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(relative: str) -> Any:
    return json.loads((EVIDENCE / relative).read_text(encoding="utf-8"))


def main() -> int:
    failures: list[str] = []
    present = {
        path.relative_to(EVIDENCE).as_posix()
        for path in EVIDENCE.rglob("*")
        if path.is_file()
    }
    if missing := sorted(REQUIRED - present):
        failures.append("missing:" + ",".join(missing))
    for relative in sorted(present):
        raw = (EVIDENCE / relative).read_bytes()
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            failures.append(f"non_utf8:{relative}")
        if b"\r\n" in raw:
            failures.append(f"crlf:{relative}")
        if relative.endswith(".json"):
            try:
                load(relative)
            except Exception as exc:
                failures.append(f"invalid_json:{relative}:{exc}")
    if failures:
        print(json.dumps({"status": "FAIL", "failures": failures}, sort_keys=True))
        return 1

    manifest = load("final-content-manifest.json")
    actual_inventory = {
        relative: {
            "sha256": sha256_file(EVIDENCE / relative),
            "size_bytes": (EVIDENCE / relative).stat().st_size,
        }
        for relative in sorted(present - {"final-content-manifest.json"})
    }
    if manifest.get("files") != actual_inventory:
        failures.append("final_manifest_inventory_mismatch")
    if (
        manifest.get("status") != "PASS"
        or manifest.get("final_holdout_used") is not False
        or manifest.get("real_profitability_claim") is not False
        or manifest.get("secrets_present") is not False
        or any(
            manifest.get(name) is not False
            for name in ("raw_archives_committed", "duckdb_payloads_committed", "catalog_payloads_committed")
        )
    ):
        failures.append("final_manifest_contract_invalid")

    locks = {name: sha256_file(ROOT / name) for name in EXPECTED_LOCKS}
    if locks != EXPECTED_LOCKS:
        failures.append("locked_identity_changed")
    baseline = load("baseline-attestation.json")
    if baseline.get("status") != "PASS" or baseline.get("locked_hashes") != EXPECTED_LOCKS:
        failures.append("baseline_invalid")

    datasets = load("dataset-bindings.json")
    strategies = load("strategy-identities.json")
    replay_all = load("deterministic-replay.json")
    for label, expected in EXPECTED.items():
        metrics = load(f"{label}/metrics.json")
        run = load(f"{label}/run-result.json")
        checker = load(f"{label}/checker-result.json")
        replay = load(f"{label}/replay-result.json")
        if (
            metrics.get("status") != "PASS"
            or metrics.get("completed_daily_bars") != 212
            or metrics.get("scored_decisions") != 181
            or metrics.get("orders") != expected["orders"]
            or metrics.get("fills") != expected["fills"]
            or metrics.get("net_pnl") != expected["net_pnl"]
            or metrics.get("fees") != expected["fees"]
            or metrics.get("ending_equity") != expected["ending_equity"]
            or metrics.get("gross_pnl") != "UNDEFINED"
            or metrics.get("calmar") != "UNDEFINED"
            or metrics.get("completed_native_trades") != "UNDEFINED"
            or metrics.get("checker") != "CHECK_PASS"
            or metrics.get("replay") != "PASS"
            or metrics.get("replay_identity") != expected["replay_identity"]
        ):
            failures.append(f"metrics_invalid:{label}")
        if label == "perpetual" and metrics.get("funding") != expected["funding"]:
            failures.append("perpetual_funding_metric_invalid")
        if (
            run.get("status") != "PASS"
            or run.get("run_state") != "COMPLETED"
            or run.get("checker") != "CHECK_PASS"
            or checker.get("status") != "PASS"
            or checker.get("read_only_regeneration_exact_match") is not True
            or checker.get("checker", {}).get("outcome") != "CHECK_PASS"
            or replay.get("status") != "PASS"
            or replay.get("replay", {}).get("result") != "PASS"
            or replay.get("replay", {}).get("primary_semantic_digest")
            != replay.get("replay", {}).get("replay_semantic_digest")
            or replay_all["profiles"][label]["replay_identity"] != expected["replay_identity"]
            or datasets["profiles"][label]["dataset_release_id"] != expected["dataset_release_id"]
            or datasets["profiles"][label]["catalog_identity"] != expected["catalog_identity"]
            or strategies["profiles"][label]["strategy_identity"] != expected["strategy_identity"]
        ):
            failures.append(f"identity_or_execution_invalid:{label}")

    funding = load("funding-and-mark-validation.json")
    if (
        funding.get("status") != "PASS"
        or funding.get("source_funding_events") != 636
        or funding.get("runtime_updates") != 1272
        or funding.get("eligible_position_boundaries") != 539
        or funding.get("no_position_boundaries") != 97
        or funding.get("native_financial_settlements") != 539
        or funding.get("mark_binding") != "LATEST_CAUSAL_AT_OR_BEFORE_FUNDING_TIMESTAMP"
        or funding.get("mark_age_ns_max") != 46_000_000
        or funding.get("future_mark_used") is not False
        or funding.get("runtime_update_pair_counted_as_two_settlements") is not False
    ):
        failures.append("funding_mark_binding_invalid")
    offline = load("offline-enforcement.json")
    if (
        offline.get("status") != "PASS"
        or offline.get("external_contact_count") != 0
        or any(view.get("attempts") for view in offline.get("profiles", {}).values())
        or not all(view.get("enforced") is True for view in offline.get("profiles", {}).values())
    ):
        failures.append("offline_enforcement_invalid")
    eligibility = load("research-eligibility.json")
    if (
        eligibility.get("status") != "PASS"
        or eligibility.get("final_holdout_used") is not False
        or eligibility.get("real_profitability_claim") is not False
        or eligibility.get("claim_eligibility") != "INELIGIBLE_FOR_REAL_PROFITABILITY_CLAIM"
        or eligibility.get("optimization_performed") is not False
    ):
        failures.append("research_eligibility_invalid")
    tests = load("test-results.json")
    if (
        tests.get("status") != "PASS"
        or tests.get("unique_tests") != 268
        or tests.get("test_execution_occurrences") != 960
        or any(value != "PASS" for value in tests.get("gates", {}).values())
        or any(
            run.get("failures", 0) not in {0, None}
            or run.get("errors", 0) not in {0, None}
            or run.get("skips", run.get("skipped", 0)) not in {0, None}
            or run.get("xfail", 0) not in {0, None}
            for run in tests.get("test_runs", {}).values()
        )
    ):
        failures.append("test_results_invalid")
    trial_records = [
        json.loads(line)
        for line in (EVIDENCE / "trial-records.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    if (
        len(trial_records) != 12
        or sum(item["state"] == "FAILED" for item in trial_records) != 2
        or sum(item["state"] == "COMPLETED" for item in trial_records) != 2
    ):
        failures.append("trial_history_invalid")
    attempts = [
        json.loads(line)
        for line in (EVIDENCE / "failed-attempts.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    if len(attempts) != 4 or not all("RETAINED" in item["status"] for item in attempts):
        failures.append("failed_attempts_invalid")
    forbidden_suffixes = {".duckdb", ".parquet", ".zip"}
    if any(path.suffix in forbidden_suffixes for path in EVIDENCE.rglob("*")):
        failures.append("payload_present_in_evidence")
    forbidden_text = ("api_key", "secret_key", "signed_url", "x-mbx-apikey")
    for path in EVIDENCE.rglob("*"):
        if path.is_file() and any(term in path.read_text(encoding="utf-8").lower() for term in forbidden_text):
            failures.append(f"possible_secret_material:{path.relative_to(EVIDENCE)}")

    status = "PASS" if not failures else "FAIL"
    print(
        json.dumps(
            {
                "schema": "owner-smoke-002-replacement-evidence-validation-v1",
                "status": status,
                "file_count": len(present),
                "failed_attempt_count": len(attempts),
                "failures": failures,
            },
            sort_keys=True,
        ),
    )
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
