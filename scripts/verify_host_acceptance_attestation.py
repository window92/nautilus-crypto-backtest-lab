#!/usr/bin/env python3
"""Verify the committed Host Acceptance attestation against product source."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from crypto_lab.host_acceptance import verify_host_acceptance_attestation


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify Host Acceptance attestation. Portable CI uses --portable-only "
            "and must not treat a passing result as Official acceptance."
        ),
    )
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--portable-only", action="store_true")
    arguments = parser.parse_args()
    try:
        report = verify_host_acceptance_attestation(
            arguments.repository,
            portable_only=arguments.portable_only,
        )
    except FileNotFoundError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        print(
            "Green portable CI is not Official acceptance; the host attestation is required.",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    print(
        "portable CI is not Official acceptance",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
