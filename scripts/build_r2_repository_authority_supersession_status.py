#!/usr/bin/env python3
"""Build the additive retry-009 repository-authority supersession registry."""

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
from crypto_lab.result_status import R2_REPOSITORY_AUTHORITY_SUPERSEDED_RESULTS
from crypto_lab.result_status import R2_REPOSITORY_AUTHORITY_SUPERSESSION_AUTHORITY
from crypto_lab.result_status import build_repository_authority_supersession_record_v5
from crypto_lab.result_status import build_repository_authority_supersession_registry_v5


_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
EXPECTED_REPOSITORY_AUTHORITY_EVIDENCE_IDENTITIES = {
    "runs/adversarial-remediation-002-retry-009-perpetual-benchmark-run-3187fedbd2e6": (
        "73f631ea34e4f7523a404a7530705c7f82ca241d4e4a2a4a4a200e6df9d5d621"
    ),
    "runs/adversarial-remediation-002-retry-009-perpetual-candidate-a-run-a0acc30dced8": (
        "c6541c5b5f52483ccb5dcd3fa877bb62041f8d08aedc1ac75adf8f9997f59481"
    ),
    "runs/adversarial-remediation-002-retry-009-perpetual-candidate-b-run-815d4c2bfc7a": (
        "e351e5179c2bb09929cc786fb9a85cb1da4863f2d5a815daefdd77e7f1a35199"
    ),
    "runs/adversarial-remediation-002-retry-009-spot-benchmark-run-018e4b266152": (
        "997d8b71414cbe648844ff128f53e68fb7b220bdccfef204d1a43691e24768bd"
    ),
    "runs/adversarial-remediation-002-retry-009-spot-candidate-a-run-b1bce20cc130": (
        "724cb313462f14ff26f1f626bd955936fe9ba2dc05371af48846776b70a19fae"
    ),
    "runs/adversarial-remediation-002-retry-009-spot-candidate-b-run-d9ff725f8590": (
        "1e27ff4d620dc40f35ec7fe6d16f36102251ad4e41b1b9520c102ef20450e8ef"
    ),
    (
        "runs/replays/adversarial-remediation-002-retry-009-perpetual-benchmark-"
        "buy-and-hold-1x-development/adversarial-remediation-002-retry-009-"
        "perpetual-benchmark-run-3187fedbd2e6"
    ): "8e0e9d74af88275be0625cc329afc691d633210a748797f23a46a8f038fec280",
    (
        "runs/replays/adversarial-remediation-002-retry-009-perpetual-candidate-a-"
        "development/adversarial-remediation-002-retry-009-perpetual-candidate-a-"
        "run-a0acc30dced8"
    ): "689f9a89a6ae95d8bb265191d01ad9f8c5713c6eb1761655c5b63feb879ce5b1",
    (
        "runs/replays/adversarial-remediation-002-retry-009-perpetual-candidate-b-"
        "development/adversarial-remediation-002-retry-009-perpetual-candidate-b-"
        "run-815d4c2bfc7a"
    ): "44d75ff08c94b05b2763e61f9a2d9f2ae7f75fd98eb14b5abc7d0b03864531ef",
    (
        "runs/replays/adversarial-remediation-002-retry-009-spot-benchmark-"
        "buy-and-hold-1x-development/adversarial-remediation-002-retry-009-"
        "spot-benchmark-run-018e4b266152"
    ): "f7c8d759f9e337ec79055f20e6c6d719ecb9a791e2c312f2c9de98b97c2b8fd1",
    (
        "runs/replays/adversarial-remediation-002-retry-009-spot-candidate-a-"
        "development/adversarial-remediation-002-retry-009-spot-candidate-a-"
        "run-b1bce20cc130"
    ): "5d138a6653c7f5e1c4d865426af97142385c3dde09ed688d9a7b05bc26ada033",
    (
        "runs/replays/adversarial-remediation-002-retry-009-spot-candidate-b-"
        "development/adversarial-remediation-002-retry-009-spot-candidate-b-"
        "run-d9ff725f8590"
    ): "6cc54927173599c5e82b1e7dbd8a2cf4ea5740df0e1c6ae49c62c0558490523c",
}


class RepositoryAuthoritySupersessionBuildError(ValueError):
    """The frozen retry-009 supersession registry could not be proven."""


def _recorded_at_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise RepositoryAuthoritySupersessionBuildError(
            "recorded_at_utc must be explicit UTC ending in Z",
        )
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise RepositoryAuthoritySupersessionBuildError("recorded_at_utc is invalid") from exc
    if result.tzinfo is None or result.utcoffset() != UTC.utcoffset(result):
        raise RepositoryAuthoritySupersessionBuildError("recorded_at_utc must use UTC")
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
        raise RepositoryAuthoritySupersessionBuildError(
            f"repository authority is invalid: {exc}",
        ) from exc
    if _GIT_SHA.fullmatch(source_commit) is None:
        raise RepositoryAuthoritySupersessionBuildError(
            "source_commit must be explicit lowercase 40-hex",
        )
    expected_paths = {
        item[key]
        for item in R2_REPOSITORY_AUTHORITY_SUPERSEDED_RESULTS.values()
        for key in ("primary_path", "replay_path")
    }
    if expected_paths != set(EXPECTED_REPOSITORY_AUTHORITY_EVIDENCE_IDENTITIES):
        raise RepositoryAuthoritySupersessionBuildError(
            "frozen repository-authority supersession scope is inconsistent",
        )
    records: list[dict[str, object]] = []
    for logical_id, expected in sorted(R2_REPOSITORY_AUTHORITY_SUPERSEDED_RESULTS.items()):
        for copy_role, key in (
            (HistoricalCopyRole.PRIMARY, "primary_path"),
            (HistoricalCopyRole.REPLAY, "replay_path"),
        ):
            relative = expected[key]
            try:
                record = build_repository_authority_supersession_record_v5(
                    root / relative,
                    repository_root=root,
                    logical_result_id=logical_id,
                    market_profile=expected["market_profile"],
                    result_class=HistoricalResultClass(expected["result_class"]),
                    copy_role=copy_role,
                )
            except (OSError, ValueError) as exc:
                raise RepositoryAuthoritySupersessionBuildError(
                    f"cannot bind immutable superseded result {relative}: {exc}",
                ) from exc
            if (
                canonical_sha256(record["evidence_hashes"])
                != EXPECTED_REPOSITORY_AUTHORITY_EVIDENCE_IDENTITIES[relative]
            ):
                raise RepositoryAuthoritySupersessionBuildError(
                    f"repository-authority supersession evidence identity mismatch: {relative}",
                )
            records.append(record)
    return build_repository_authority_supersession_registry_v5(
        records,
        authority_id=R2_REPOSITORY_AUTHORITY_SUPERSESSION_AUTHORITY,
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
