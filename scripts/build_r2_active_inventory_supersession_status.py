#!/usr/bin/env python3
"""Build the additive retry-011 official-active-inventory supersession registry."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

from crypto_lab.git_identity import require_repository_root
from crypto_lab.hashing import canonical_sha256
from crypto_lab.result_status import HistoricalCopyRole
from crypto_lab.result_status import HistoricalResultClass
from crypto_lab.result_status import R2_ACTIVE_INVENTORY_SUPERSEDED_RESULTS
from crypto_lab.result_status import R2_ACTIVE_INVENTORY_SUPERSESSION_AUTHORITY
from crypto_lab.result_status import R2_AUDITED_BASELINE_COMMIT
from crypto_lab.result_status import build_active_inventory_supersession_record_v7
from crypto_lab.result_status import build_active_inventory_supersession_registry_v7


_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
EXPECTED_ACTIVE_INVENTORY_EVIDENCE_IDENTITIES = {
    "runs/adversarial-remediation-002-retry-011-perpetual-benchmark-run-09eadae82221": (
        "b34872d212b609f25c0ea268c9b57aa47be898737bdd4a30f385f5d49ea0fbb7"
    ),
    "runs/adversarial-remediation-002-retry-011-perpetual-candidate-a-run-6b933ded4ae4": (
        "7478d918cbbabac827ba2fcfd52ebb4a15f2f186ce7a91223bed9efe15059106"
    ),
    "runs/adversarial-remediation-002-retry-011-perpetual-candidate-b-run-77de68aaa779": (
        "52fb79316583de7d2f24351438b856d0a2b023fa528270854a7da3a0cc35e744"
    ),
    "runs/adversarial-remediation-002-retry-011-spot-benchmark-run-fbfa1ef39cce": (
        "9a321cb760e8f989dd69293ce0aafbf68fb3bdf76d808ec00dfb68a8b1f9760e"
    ),
    "runs/adversarial-remediation-002-retry-011-spot-candidate-a-run-213964820a51": (
        "8d049a88ed481987a921c72dc38983624e7070903065632292f84b270cb35138"
    ),
    "runs/adversarial-remediation-002-retry-011-spot-candidate-b-run-d74d9de1e933": (
        "99806e3bd366a2d5c4421713ddc9944c215ab44ca721c760fabc1d9a31b9f08a"
    ),
    (
        "runs/replays/adversarial-remediation-002-retry-011-perpetual-benchmark-"
        "buy-and-hold-1x-development/adversarial-remediation-002-retry-011-"
        "perpetual-benchmark-run-09eadae82221"
    ): "165b94a457f6bcbfb61c1603cf1a76306f08408eef0727a27de2860e0ef1f483",
    (
        "runs/replays/adversarial-remediation-002-retry-011-perpetual-candidate-a-"
        "development/adversarial-remediation-002-retry-011-perpetual-candidate-a-"
        "run-6b933ded4ae4"
    ): "cd5b5e8aa012599d5dcf8fbc8f62acf09d5e003587a71a416cc0d4db14838aeb",
    (
        "runs/replays/adversarial-remediation-002-retry-011-perpetual-candidate-b-"
        "development/adversarial-remediation-002-retry-011-perpetual-candidate-b-"
        "run-77de68aaa779"
    ): "0b84c7350bee86bed694c9ae81bcbbe5ab5a3f7ad962fef52c2cce754b2abd6e",
    (
        "runs/replays/adversarial-remediation-002-retry-011-spot-benchmark-"
        "buy-and-hold-1x-development/adversarial-remediation-002-retry-011-"
        "spot-benchmark-run-fbfa1ef39cce"
    ): "990122863114247a834aa2d762c1575d24cc3abcc1be821c8100f714fe75af9a",
    (
        "runs/replays/adversarial-remediation-002-retry-011-spot-candidate-a-"
        "development/adversarial-remediation-002-retry-011-spot-candidate-a-"
        "run-213964820a51"
    ): "30cdb25bf25e01a1f9bfbc5a7c9687cfc4a398f624a6e6045e95ef4bd4d6dc95",
    (
        "runs/replays/adversarial-remediation-002-retry-011-spot-candidate-b-"
        "development/adversarial-remediation-002-retry-011-spot-candidate-b-"
        "run-d74d9de1e933"
    ): "8f2428a9cc2b6e6248c2958c7f878e51e532d44d16812bcf0f7bc9d60a21e235",
}


class ActiveInventorySupersessionBuildError(ValueError):
    """The frozen retry-011 supersession registry could not be proven."""


def _recorded_at_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ActiveInventorySupersessionBuildError(
            "recorded_at_utc must be explicit UTC ending in Z",
        )
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ActiveInventorySupersessionBuildError("recorded_at_utc is invalid") from exc
    if result.tzinfo is None or result.utcoffset() != UTC.utcoffset(result):
        raise ActiveInventorySupersessionBuildError("recorded_at_utc must use UTC")
    return result


def build_registry(
    *,
    repository_root: Path,
    source_commit: str,
    recorded_at_utc: str,
) -> bytes:
    try:
        root = require_repository_root(repository_root)
    except (TypeError, ValueError) as exc:
        raise ActiveInventorySupersessionBuildError(
            f"repository authority is invalid: {exc}",
        ) from exc
    if _GIT_SHA.fullmatch(source_commit) is None:
        raise ActiveInventorySupersessionBuildError(
            "source_commit must be explicit lowercase 40-hex",
        )
    expected_paths = {
        item[key]
        for item in R2_ACTIVE_INVENTORY_SUPERSEDED_RESULTS.values()
        for key in ("primary_path", "replay_path")
    }
    if expected_paths != set(EXPECTED_ACTIVE_INVENTORY_EVIDENCE_IDENTITIES):
        raise ActiveInventorySupersessionBuildError(
            "frozen active-inventory supersession scope is inconsistent",
        )
    records: list[dict[str, object]] = []
    for logical_id, expected in sorted(R2_ACTIVE_INVENTORY_SUPERSEDED_RESULTS.items()):
        for copy_role, key in (
            (HistoricalCopyRole.PRIMARY, "primary_path"),
            (HistoricalCopyRole.REPLAY, "replay_path"),
        ):
            relative = expected[key]
            try:
                record = build_active_inventory_supersession_record_v7(
                    root / relative,
                    repository_root=root,
                    logical_result_id=logical_id,
                    market_profile=expected["market_profile"],
                    result_class=HistoricalResultClass(expected["result_class"]),
                    copy_role=copy_role,
                )
            except (OSError, ValueError) as exc:
                raise ActiveInventorySupersessionBuildError(
                    f"cannot bind immutable superseded result {relative}: {exc}",
                ) from exc
            if (
                canonical_sha256(record["evidence_hashes"])
                != EXPECTED_ACTIVE_INVENTORY_EVIDENCE_IDENTITIES[relative]
            ):
                raise ActiveInventorySupersessionBuildError(
                    f"active-inventory supersession evidence identity mismatch: {relative}",
                )
            records.append(record)
    return build_active_inventory_supersession_registry_v7(
        records,
        authority_id=R2_ACTIVE_INVENTORY_SUPERSESSION_AUTHORITY,
        audited_baseline_commit=R2_AUDITED_BASELINE_COMMIT,
        source_commit=source_commit,
        recorded_at_utc=_recorded_at_utc(recorded_at_utc),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--recorded-at-utc", required=True)
    arguments = parser.parse_args(argv)
    sys.stdout.buffer.write(
        build_registry(
            repository_root=arguments.repository,
            source_commit=arguments.source_commit,
            recorded_at_utc=arguments.recorded_at_utc,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
