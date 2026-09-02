from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SPOT_CONFIG_FIXTURE = ROOT / "tests/golden/fixtures/spot-lab-run-config.json"
SOURCE_REVISION_FIXTURE = ROOT / "tests/golden/fixtures/source-revision.json"


def load_spot_config_dict() -> dict[str, Any]:
    return json.loads(SPOT_CONFIG_FIXTURE.read_text(encoding="utf-8"))


def load_source_revision_dict() -> dict[str, Any]:
    return json.loads(SOURCE_REVISION_FIXTURE.read_text(encoding="utf-8"))


def encode_config(data: dict[str, Any]) -> bytes:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def initialize_product_repository(root: Path) -> Path:
    """Create the smallest explicit Git product root accepted by L-4 tests."""

    root.mkdir(parents=True, exist_ok=True)
    if (root / ".git").is_dir():
        return root
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "config", "user.name", "Test Product"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test-product@example.invalid"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.invalid/test-product.git"],
        cwd=root,
        check=True,
    )
    (root / "SSOT.md").write_text("synthetic test product authority\n", encoding="utf-8")
    package = root / "src/crypto_lab"
    package.mkdir(parents=True)
    (package / "sealing.py").write_text("# synthetic authority marker\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initialize synthetic product authority"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return root
