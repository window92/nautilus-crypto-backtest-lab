#!/usr/bin/env python3
"""Build the closed additive registry for R2 runtime-superseded Run bytes."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from crypto_lab.result_status import (
    HistoricalCopyRole,
    HistoricalResultClass,
    R2_AUDITED_BASELINE_COMMIT,
    R2_RUNTIME_SUPERSEDED_RESULTS,
    R2_RUNTIME_SUPERSESSION_AUTHORITY,
    build_runtime_supersession_record_v3,
    build_runtime_supersession_registry_v3,
)


OUTPUT_RELATIVE_PATH = Path(
    "evidence/audit/adversarial-remediation-002/"
    "runtime-authority-supersession-status.json",
)
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")

# These identities were frozen from the immutable retry-002 and retry-003
# terminal bytes before this builder existed.  Re-running the builder cannot
# silently bless modified evidence merely by computing new hashes.
EXPECTED_RUNTIME_SUPERSESSION_EVIDENCE_HASHES: dict[str, dict[str, str]] = {
    "runs/adversarial-remediation-002-retry-002-spot-benchmark-run-7da743fdaa06": {
        "component_validation.json": "7cee36205bb968d5b89c7a209cb7eacbeb17a933cf09b8b4417340ae6545c1cf",
        "evidence_manifest.json": "671c5c78aea67f6dc8a074082959b3a4def3cac792ff3dcd55a9d43c767c235e",
        "official_seal.json": "edad446a49f4c27fbf1bbe7ad56e4cd156720534f3825dda7a7177dadb7f7ddd",
        "runtime_identity.json": "8b89e215d9173b6cfd73f3e3bbe04e47437464550129a477745f46fb526b40e2",
        "source_revision.json": "6c10c07370e1e77581848f8fdb4fb8da12ce7cc9eb09314a7379bdddfbd72c02",
        "status.json": "d93c9c49cb2221ecff3b1c19b219f12f4379481013688af9736c1389206a6a79",
    },
    "runs/adversarial-remediation-002-retry-002-spot-candidate-a-run-e1cacf032f78": {
        "component_validation.json": "ce6d09830088f4a147df596866efb6936b9aaec5fb2397b23cdcf9fd57488b62",
        "evidence_manifest.json": "b370070092b12d4b33ba0ce4f9b80c1260eba137f95bd3341ba033ce8bcc6544",
        "official_seal.json": "0f447079d7ce210e3b7d57a81224b9c31b3d8887d7afe8138422bbfb610b87c7",
        "runtime_identity.json": "8b89e215d9173b6cfd73f3e3bbe04e47437464550129a477745f46fb526b40e2",
        "source_revision.json": "f0d06413b57f49b6119f42c3a72ae56415634ff2d1e77d5d166d9985187c580e",
        "status.json": "3c0e63ad047c439b1598bea93b23fd4b5c5fda2c7720774d498d565df541a732",
    },
    "runs/adversarial-remediation-002-retry-002-spot-candidate-b-run-9bbdbc35e204": {
        "component_validation.json": "6522c88ea6f39916bf77258ed7eb1d59b65b5ea3d17570f9cb0926d8d246bcc8",
        "evidence_manifest.json": "5b2b68e6893f367ae2c0e34289e7ae27c6b611e5621726b47106027ffb0606b3",
        "official_seal.json": "1cf74016945a6ff3490e300ea2a0edc40633af6b3914bf7dc0a4b963a797879e",
        "runtime_identity.json": "8b89e215d9173b6cfd73f3e3bbe04e47437464550129a477745f46fb526b40e2",
        "source_revision.json": "1a81aa4650ace2a527cf7578504c54669ebe14ad468f1bbb91cf7e0f91c07da4",
        "status.json": "0436874012cf946365d37ec5cb4dc0080127fe4f6fcd4a37673c0f0938de0ea3",
    },
    "runs/adversarial-remediation-002-retry-003-spot-benchmark-run-f28ac747c930": {
        "component_validation.json": "368792899e5e8c2d4795404f4814288fce118c7d7c326b6932fee262587759be",
        "evidence_manifest.json": "0d194d28e5533783ceb95ab5e9cb0ba75f345a2df8aa3d3a22d2c384e9e3dafe",
        "official_seal.json": "46fd98d099d3c8d0dc6be35a05d84c7c64a6c5f1ac1050762b89c43d38640c43",
        "runtime_identity.json": "a56e465c7506ff4456c3d4e7aed9499564bf49fd4f4b0a6bedb93cbd7c3826a2",
        "source_revision.json": "55d05c39aea5a2324280a95d35799ada7039936d15765b3371fe98b4fe49d3fb",
        "status.json": "80d7918b4992173857057ded913d96f13c01597914d4866fc1fc3f5e73bd5bb3",
    },
    "runs/adversarial-remediation-002-retry-004-spot-benchmark-run-524a71ec1f23": {
        "component_validation.json": "222e1adcdf9443cbd711ac1c39aaea8e55f65bd3f0014065cacaa5f47e7e286a",
        "evidence_manifest.json": "8ea36b3b68dbe783306c6cbfd25c3ba09dfca4de6412bfbaad5758d6f47e39b6",
        "official_seal.json": "eb8bac5f3269feef8d034c8046fab1a6045e6c5ceb2a3925913e53e914f667c1",
        "runtime_identity.json": "954a6ad4c20de481cb20f8c851388da76c72bac48cb26755dab3fc26e1895146",
        "source_revision.json": "e7a80fa0e0b0c3cb8b609df987b7bef5ef2f79f2dc5e398e0b5e706c21227889",
        "status.json": "621e64de2138d6901379f40c7d155122a9d9c2e6533db2d3929d3ccb3cb771ab",
    },
    (
        "runs/replays/adversarial-remediation-002-retry-002-spot-benchmark-"
        "buy-and-hold-1x-development/adversarial-remediation-002-retry-002-"
        "spot-benchmark-run-7da743fdaa06"
    ): {
        "component_validation.json": "7cee36205bb968d5b89c7a209cb7eacbeb17a933cf09b8b4417340ae6545c1cf",
        "evidence_manifest.json": "d681ca4c70edd0c9cdbfea5455ab7b04d4485a80b9b68333adaaf9efd363b81b",
        "official_seal.json": "7001d1d5e97ecd48333965872f0226731ded329f39dc5649d09764b5c45ec459",
        "runtime_identity.json": "8b89e215d9173b6cfd73f3e3bbe04e47437464550129a477745f46fb526b40e2",
        "source_revision.json": "b1775fa18adc7539c1cdf3a70a4b873307fda7af92aa6ba223db528a91e2fdb9",
        "status.json": "5a2790cb29595a5d565611d9e540b9cf999685bba71f85d3776647414cccdbe2",
    },
    (
        "runs/replays/adversarial-remediation-002-retry-002-spot-candidate-a-"
        "development/adversarial-remediation-002-retry-002-spot-candidate-a-"
        "run-e1cacf032f78"
    ): {
        "component_validation.json": "ce6d09830088f4a147df596866efb6936b9aaec5fb2397b23cdcf9fd57488b62",
        "evidence_manifest.json": "e991c9d4fcf6e9de148f0e87b6fd1ab7d503a5b6484ff326019eb41c2f0d21f5",
        "official_seal.json": "fc736c77f273a1277170a7c2bc011973773f915912a5ada34f7456f9c449b2b0",
        "runtime_identity.json": "8b89e215d9173b6cfd73f3e3bbe04e47437464550129a477745f46fb526b40e2",
        "source_revision.json": "49c807e52b919291072b9296d907fa624f44bbe633ad08b189db8206a9763f22",
        "status.json": "0907d15c9550bf2f91d2900c19a436f74cededb06790bffa0c38605bd496ceb2",
    },
    (
        "runs/replays/adversarial-remediation-002-retry-002-spot-candidate-b-"
        "development/adversarial-remediation-002-retry-002-spot-candidate-b-"
        "run-9bbdbc35e204"
    ): {
        "component_validation.json": "6522c88ea6f39916bf77258ed7eb1d59b65b5ea3d17570f9cb0926d8d246bcc8",
        "evidence_manifest.json": "978bf5e5a592d3a634e8f6b62308201804d32652d0cb3704bde60ab7d06b0421",
        "official_seal.json": "d19c7aa11f0ca2c9ecc2eeabf9900dd9dc89ab4924fd1c9c4b79e6152e4d23a6",
        "runtime_identity.json": "8b89e215d9173b6cfd73f3e3bbe04e47437464550129a477745f46fb526b40e2",
        "source_revision.json": "156382f423876b0bb43029bef6a818a2bdc45173cb8abd5a97acd79243f6fd63",
        "status.json": "78c470b1a0d60034438c48b25fcf025b8fc302cc54cc1ca810bdfe22fcf485a8",
    },
    (
        "runs/replays/adversarial-remediation-002-retry-003-spot-benchmark-"
        "buy-and-hold-1x-development/adversarial-remediation-002-retry-003-"
        "spot-benchmark-run-f28ac747c930"
    ): {
        "component_validation.json": "368792899e5e8c2d4795404f4814288fce118c7d7c326b6932fee262587759be",
        "evidence_manifest.json": "17c135424a3100a6f0893c53b2604473661ac0fef4ca1af29022f6b272001b86",
        "official_seal.json": "02ad615863589268767fa3211e6b0392c0a32d62d2810e76f5ba2cdbbf31f786",
        "runtime_identity.json": "a56e465c7506ff4456c3d4e7aed9499564bf49fd4f4b0a6bedb93cbd7c3826a2",
        "source_revision.json": "2afbacf05e04b44d49932882df563f6c2d07553ee722febb3d4f2eb0e1c88b7a",
        "status.json": "318d3be55781bc1b9e1b58d9c28447c5fdc5eae96fac611fcb3772442b612184",
    },
    (
        "runs/replays/adversarial-remediation-002-retry-004-spot-benchmark-"
        "buy-and-hold-1x-development/adversarial-remediation-002-retry-004-"
        "spot-benchmark-run-524a71ec1f23"
    ): {
        "component_validation.json": "222e1adcdf9443cbd711ac1c39aaea8e55f65bd3f0014065cacaa5f47e7e286a",
        "evidence_manifest.json": "aa5c4a59086f7f04d69b946bdb90a42d2da6e3b6266d2ab018097db02031942a",
        "official_seal.json": "0a2dd05e70d825ab0766a3f327fa57961330e7788071f3983708ccc61215b6d4",
        "runtime_identity.json": "954a6ad4c20de481cb20f8c851388da76c72bac48cb26755dab3fc26e1895146",
        "source_revision.json": "b98acfc41aa7491439c41a78aadc8c3178e3a8cf405ef14a6a8cdf8323927f7b",
        "status.json": "e5fc2295289f33e1d7baaa4a6bddcc366197a7a552e966115728f1dc33738257",
    },
}


class RuntimeSupersessionBuildError(ValueError):
    """The frozen runtime-supersession registry could not be proven."""


def _recorded_at_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise RuntimeSupersessionBuildError("recorded_at_utc must be explicit UTC ending in Z")
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise RuntimeSupersessionBuildError("recorded_at_utc is invalid") from exc
    if result.tzinfo is None or result.utcoffset() != UTC.utcoffset(result):
        raise RuntimeSupersessionBuildError("recorded_at_utc must use UTC")
    return result


def build_registry(
    *,
    repository_root: Path,
    source_commit: str,
    recorded_at_utc: str,
) -> bytes:
    root = Path(repository_root).resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise RuntimeSupersessionBuildError("repository root must be an exact directory")
    if _GIT_SHA.fullmatch(source_commit) is None:
        raise RuntimeSupersessionBuildError("source_commit must be explicit lowercase 40-hex")
    expected_paths = {
        item[key]
        for item in R2_RUNTIME_SUPERSEDED_RESULTS.values()
        for key in ("primary_path", "replay_path")
    }
    if expected_paths != set(EXPECTED_RUNTIME_SUPERSESSION_EVIDENCE_HASHES):
        raise RuntimeSupersessionBuildError("frozen runtime-supersession scope is inconsistent")
    records: list[dict[str, object]] = []
    for logical_id, expected in sorted(R2_RUNTIME_SUPERSEDED_RESULTS.items()):
        for copy_role, key in (
            (HistoricalCopyRole.PRIMARY, "primary_path"),
            (HistoricalCopyRole.REPLAY, "replay_path"),
        ):
            relative = expected[key]
            try:
                record = build_runtime_supersession_record_v3(
                    root / relative,
                    repository_root=root,
                    logical_result_id=logical_id,
                    market_profile=expected["market_profile"],
                    result_class=HistoricalResultClass(expected["result_class"]),
                    copy_role=copy_role,
                )
            except (OSError, ValueError) as exc:
                raise RuntimeSupersessionBuildError(
                    f"cannot bind immutable superseded result {relative}: {exc}",
                ) from exc
            if record["evidence_hashes"] != EXPECTED_RUNTIME_SUPERSESSION_EVIDENCE_HASHES[relative]:
                raise RuntimeSupersessionBuildError(
                    f"runtime-supersession evidence identity mismatch: {relative}",
                )
            records.append(record)
    return build_runtime_supersession_registry_v3(
        records,
        authority_id=R2_RUNTIME_SUPERSESSION_AUTHORITY,
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
