#!/usr/bin/env python3
"""Generate only the exposed-data, claim-ineligible public workflow fixture."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from crypto_lab.owner import qualification_workflow_fixture_input


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--frozen-at-utc", required=True)
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.frozen_at_utc.endswith("Z"):
        parser.error("--frozen-at-utc must be an explicit UTC timestamp ending in Z")
    frozen_at = datetime.fromisoformat(args.frozen_at_utc[:-1] + "+00:00")
    value = qualification_workflow_fixture_input(
        repository_root=args.repository,
        frozen_at_utc=frozen_at,
        trial_id=args.trial_id,
        run_id=args.run_id,
    )
    args.output.write_bytes(value.to_json_bytes() + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
