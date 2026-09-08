#!/usr/bin/env python3
"""Fail-closed notice for the obsolete direct Owner wrapper.

Official execution must start with the documented ``python -I -P -S``
standard-library bootstrap command.  A Python wrapper cannot retroactively
undo startup hooks which may already have executed before this file loads.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    print(
        json.dumps(
            {
                "schema": "isolated-runtime-bootstrap-failure-v1",
                "status": "BLOCKED",
                "failure_code": "RUNTIME_STARTUP_MISMATCH",
                "reason": "DIRECT_OWNER_WRAPPER_FORBIDDEN",
                "detail": (
                    "Use scripts/isolated_runtime_bootstrap.py with the committed "
                    "runtime-bootstrap-authority.json and crypto_lab.owner:main"
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=sys.stderr,
    )
    return 120


if __name__ == "__main__":
    raise SystemExit(main())
