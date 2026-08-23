from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATA_PYTHON = ROOT / ".data-venv/bin/python"
SCHEMA = ROOT / "evidence/repair/data-provenance-duckdb-001/duckdb-schema.sql"


DUCKDB_PROBE = r"""
import hashlib
import json
import pathlib
import sys

import duckdb

root = pathlib.Path(sys.argv[1])
schema_path = pathlib.Path(sys.argv[2])
schema = schema_path.read_text(encoding="utf-8")
config = {
    "allow_unsigned_extensions": "false",
    "autoinstall_known_extensions": "false",
    "autoload_known_extensions": "false",
}


def connect(path, read_only=False):
    return duckdb.connect(str(path), read_only=read_only, config=config)


def semantic(connection):
    rows = connection.execute(
        "SELECT * FROM minute_coverage ORDER BY market_profile, symbol, open_time_ms",
    ).fetchall()
    payload = json.dumps(rows, default=str, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def insert_rows(connection, reverse=False):
    rows = [
        (
            "P",
            "BTCUSDT",
            0,
            "REAL_OFFICIAL_BAR",
            "a" * 64,
            None,
            "REAL",
            False,
        ),
        (
            "P",
            "BTCUSDT",
            60000,
            "VERIFIED_NO_TRADE_INTERVAL",
            None,
            "b" * 64,
            "NO_TRADE",
            False,
        ),
    ]
    connection.executemany("INSERT INTO minute_coverage VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows[::-1] if reverse else rows)


first_path = root / "first.duckdb"
second_path = root / "second.duckdb"
first = connect(first_path)
first.execute(schema)
first.execute("BEGIN TRANSACTION")
first.execute("INSERT INTO raw_objects VALUES (?, ?, ?, ?)", ["c" * 64, 1, "raw/c", True])
first.execute("ROLLBACK")
rollback_ok = first.execute("SELECT count(*) FROM raw_objects").fetchone()[0] == 0
source_fk_rejected = False
try:
    first.execute(
        "INSERT INTO verified_no_trade_intervals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["e" * 64, "BTCUSDT", 0, 60000, 1, 2, 10, 11, "f" * 64, '["f"]', "NO_TRADE_OBSERVED", None],
    )
except Exception:
    source_fk_rejected = True
insert_rows(first)
duplicate_rejected = False
try:
    first.execute(
        "INSERT INTO minute_coverage VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ["P", "BTCUSDT", 0, "REAL_OFFICIAL_BAR", "d" * 64, None, "DUP", False],
    )
except Exception:
    duplicate_rejected = True
check_rejected = False
try:
    first.execute(
        "INSERT INTO minute_coverage VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ["P", "BTCUSDT", 120000, "VERIFIED_NO_TRADE_INTERVAL", None, None, "BAD", False],
    )
except Exception:
    check_rejected = True
first_semantic = semantic(first)
table_count = first.execute("SELECT count(*) FROM duckdb_tables() WHERE internal = false").fetchone()[0]
canonical_source_foreign_keys = first.execute(
    "SELECT count(*) FROM duckdb_constraints() "
    "WHERE constraint_type = 'FOREIGN KEY' AND referenced_table = 'raw_objects' "
    "AND table_name IN ('derived_spot_klines', 'verified_no_trade_intervals', "
    "'canonical_execution_bars', 'canonical_mark_bars', 'canonical_funding_events')",
).fetchone()[0]
double_financial_columns = first.execute(
    "SELECT count(*) FROM duckdb_columns() "
    "WHERE internal = false AND data_type IN ('DOUBLE', 'FLOAT', 'REAL')",
).fetchone()[0]
settings = {
    name: first.execute("SELECT current_setting(?)", [name]).fetchone()[0]
    for name in (
        "allow_unsigned_extensions",
        "autoinstall_known_extensions",
        "autoload_known_extensions",
    )
}
first.execute("CHECKPOINT")
first.close()

second = connect(second_path)
second.execute(schema)
insert_rows(second, reverse=True)
second_semantic = semantic(second)
second.close()

readonly = connect(first_path, read_only=True)
readonly_count = readonly.execute("SELECT count(*) FROM minute_coverage").fetchone()[0]
readonly_write_rejected = False
try:
    readonly.execute("DELETE FROM minute_coverage")
except Exception:
    readonly_write_rejected = True
readonly.close()

print(json.dumps({
    "canonical_source_foreign_keys": canonical_source_foreign_keys,
    "check_rejected": check_rejected,
    "double_financial_columns": double_financial_columns,
    "duplicate_rejected": duplicate_rejected,
    "duckdb_version": duckdb.__version__,
    "first_semantic": first_semantic,
    "readonly_count": readonly_count,
    "readonly_write_rejected": readonly_write_rejected,
    "rollback_ok": rollback_ok,
    "second_semantic": second_semantic,
    "settings": settings,
    "source_fk_rejected": source_fk_rejected,
    "table_count": table_count,
}, sort_keys=True))
"""


class DuckDBDataToolContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not DATA_PYTHON.is_file():
            raise AssertionError("locked .data-venv is required for the data-tool contract tests")
        cls._temporary = tempfile.TemporaryDirectory()
        completed = subprocess.run(
            [DATA_PYTHON, "-c", DUCKDB_PROBE, cls._temporary.name, SCHEMA],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stdout + completed.stderr)
        cls.result = json.loads(completed.stdout)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def test_exact_tool_and_schema_identity(self) -> None:
        lock = json.loads((ROOT / "data-tool.lock.json").read_text(encoding="utf-8"))
        dependency = lock["complete_dependency_set"][0]
        wheel = ROOT / ".data-wheelhouse" / dependency["wheel_filename"]
        self.assertEqual(self.result["duckdb_version"], "1.4.5")
        self.assertEqual(dependency["version"], "1.4.5")
        self.assertEqual(wheel.stat().st_size, dependency["wheel_size_bytes"])
        self.assertEqual(hashlib.sha256(wheel.read_bytes()).hexdigest(), dependency["wheel_sha256"])
        self.assertEqual(self.result["table_count"], 21)
        self.assertEqual(self.result["canonical_source_foreign_keys"], 5)

    def test_pk_check_transaction_and_read_only_enforcement(self) -> None:
        self.assertTrue(self.result["duplicate_rejected"])
        self.assertTrue(self.result["check_rejected"])
        self.assertTrue(self.result["rollback_ok"])
        self.assertTrue(self.result["source_fk_rejected"])
        self.assertEqual(self.result["readonly_count"], 2)
        self.assertTrue(self.result["readonly_write_rejected"])

    def test_semantic_identity_is_independent_of_insert_order(self) -> None:
        self.assertEqual(self.result["first_semantic"], self.result["second_semantic"])

    def test_no_binary_float_and_no_extension_autoload(self) -> None:
        self.assertEqual(self.result["double_financial_columns"], 0)
        self.assertEqual(
            self.result["settings"],
            {
                "allow_unsigned_extensions": False,
                "autoinstall_known_extensions": False,
                "autoload_known_extensions": False,
            },
        )
        sources = "\n".join(
            [
                (ROOT / "scripts/build_data_provenance_database.py").read_text(encoding="utf-8"),
                (ROOT / "evidence/repair/data-provenance-duckdb-001/duckdb-schema.sql").read_text(encoding="utf-8"),
            ],
        ).upper()
        self.assertIsNone(re.search(r"(?m)^\s*(?:INSTALL|LOAD)\s+", sources))


if __name__ == "__main__":
    unittest.main()
