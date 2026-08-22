#!/usr/bin/env python3
"""Public CLI shim for the strict Owner workflow."""

from crypto_lab.owner import main


if __name__ == "__main__":
    raise SystemExit(main())
