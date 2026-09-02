#!/usr/bin/env python3
"""Build the additive partial retry-010 claim/Holdout supersession registry."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from crypto_lab.git_identity import require_repository_root
from crypto_lab.hashing import canonical_sha256
from crypto_lab.result_status import HistoricalCopyRole
from crypto_lab.result_status import HistoricalResultClass
from crypto_lab.result_status import R2_AUDITED_BASELINE_COMMIT
from crypto_lab.result_status import R2_CLAIM_HOLDOUT_SUPERSEDED_RESULTS
from crypto_lab.result_status import R2_CLAIM_HOLDOUT_SUPERSESSION_AUTHORITY
from crypto_lab.result_status import build_claim_holdout_supersession_record_v6
from crypto_lab.result_status import build_claim_holdout_supersession_registry_v6


_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
EXPECTED_CLAIM_HOLDOUT_EVIDENCE_IDENTITIES = {
    "runs/adversarial-remediation-002-retry-010-spot-benchmark-run-3963f3de97c8": (
        "a258fe34614ee797b2e804235b1b097892649bb4b132752c3a828e2b47b318af"
    ),
    "runs/adversarial-remediation-002-retry-010-spot-candidate-a-run-eb0dee924b6b": (
        "f7250dd89c7db5e3d8da01ebfded8d8da7e9ea7df53f3b1a3358d7fb36142dfa"
    ),
    "runs/adversarial-remediation-002-retry-010-spot-candidate-b-run-e50933d46dd4": (
        "0b70b11b229d4f1c0cbdf22fe0c021e3bbd84d124a202edc85791359706e332a"
    ),
    (
        "runs/replays/adversarial-remediation-002-retry-010-spot-benchmark-"
        "buy-and-hold-1x-development/adversarial-remediation-002-retry-010-"
        "spot-benchmark-run-3963f3de97c8"
    ): "caff91c23df761125d7c1144ba32d17efea688479cf7ea90ea57dd7d5eb22acc",
    (
        "runs/replays/adversarial-remediation-002-retry-010-spot-candidate-a-"
        "development/adversarial-remediation-002-retry-010-spot-candidate-a-"
        "run-eb0dee924b6b"
    ): "d384ab7b4079be57449ebd230b830068c7242fb78c63b4f6d176095c12d95dee",
    (
        "runs/replays/adversarial-remediation-002-retry-010-spot-candidate-b-"
        "development/adversarial-remediation-002-retry-010-spot-candidate-b-"
        "run-e50933d46dd4"
    ): "8a111def57b612bc2b297118de762bf47f98eb81ccd160bf2c6d9e918272fda5",
}


class ClaimHoldoutSupersessionBuildError(ValueError):
    """The frozen partial retry-010 scope could not be content-bound."""


def _recorded_at_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ClaimHoldoutSupersessionBuildError(
            "recorded_at_utc must be explicit UTC ending in Z",
        )
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ClaimHoldoutSupersessionBuildError("recorded_at_utc is invalid") from exc
    if result.tzinfo is None or result.utcoffset() != UTC.utcoffset(result):
        raise ClaimHoldoutSupersessionBuildError("recorded_at_utc must use UTC")
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
        raise ClaimHoldoutSupersessionBuildError(
            f"repository authority is invalid: {exc}",
        ) from exc
    if _GIT_SHA.fullmatch(source_commit) is None:
        raise ClaimHoldoutSupersessionBuildError(
            "source_commit must be explicit lowercase 40-hex",
        )
    expected_paths = {
        item[key]
        for item in R2_CLAIM_HOLDOUT_SUPERSEDED_RESULTS.values()
        for key in ("primary_path", "replay_path")
    }
    if expected_paths != set(EXPECTED_CLAIM_HOLDOUT_EVIDENCE_IDENTITIES):
        raise ClaimHoldoutSupersessionBuildError(
            "frozen claim/Holdout supersession scope is inconsistent",
        )
    records: list[dict[str, object]] = []
    for logical_id, expected in sorted(R2_CLAIM_HOLDOUT_SUPERSEDED_RESULTS.items()):
        for copy_role, key in (
            (HistoricalCopyRole.PRIMARY, "primary_path"),
            (HistoricalCopyRole.REPLAY, "replay_path"),
        ):
            relative = expected[key]
            try:
                record = build_claim_holdout_supersession_record_v6(
                    root / relative,
                    repository_root=root,
                    logical_result_id=logical_id,
                    market_profile=expected["market_profile"],
                    result_class=HistoricalResultClass(expected["result_class"]),
                    copy_role=copy_role,
                )
            except (OSError, ValueError) as exc:
                raise ClaimHoldoutSupersessionBuildError(
                    f"cannot bind immutable superseded result {relative}: {exc}",
                ) from exc
            if (
                canonical_sha256(record["evidence_hashes"])
                != EXPECTED_CLAIM_HOLDOUT_EVIDENCE_IDENTITIES[relative]
            ):
                raise ClaimHoldoutSupersessionBuildError(
                    f"claim/Holdout supersession evidence identity mismatch: {relative}",
                )
            records.append(record)
    return build_claim_holdout_supersession_registry_v6(
        records,
        authority_id=R2_CLAIM_HOLDOUT_SUPERSESSION_AUTHORITY,
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
