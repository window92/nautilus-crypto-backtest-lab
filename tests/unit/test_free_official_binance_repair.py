from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from crypto_lab.config import ConfigError
from crypto_lab.data import CoverageDisposition
from crypto_lab.data import DataContractError
from crypto_lab.data import MinuteDisposition
from crypto_lab.data import NOT_APPLICABLE
from crypto_lab.data import SourceRole
from crypto_lab.data import minute_coverage_identity
from crypto_lab.data import to_nautilus_execution_bars
from crypto_lab.data import to_nautilus_mark_updates
from crypto_lab.data import validate_one_minute_grid
from crypto_lab.data import validate_sparse_one_minute_grid
from crypto_lab.status import FailureCode
from tests.m2_helpers import perp_execution_bars
from tests.m2_helpers import perp_mark_bars
from tests.m2_helpers import perp_metadata
from tests.m2_helpers import perp_range
from tests.m2_helpers import spot_bars
from tests.m2_helpers import spot_metadata
from tests.m2_helpers import spot_range


ROOT = Path(__file__).resolve().parents[2]
DATA_PYTHON = ROOT / ".data-venv/bin/python"
SCHEMA = ROOT / "schemas/free_official_binance_duckdb.sql"


def accepted(bar, disposition: CoverageDisposition) -> MinuteDisposition:
    return MinuteDisposition(
        open_time_ns=bar.interval_start_ns,
        disposition=disposition,
        canonical_bar_identity=bar.source_row_sha256,
        proof_identity=NOT_APPLICABLE,
        source_reconciliation_identity="a" * 64,
    )


class SparseOfficialGridTests(unittest.TestCase):
    def test_verified_no_trade_is_coverage_without_a_bar(self) -> None:
        bars = spot_bars()
        sparse = (bars[0], bars[2], bars[3])
        dispositions = (
            accepted(bars[0], CoverageDisposition.REAL_OFFICIAL_BAR),
            MinuteDisposition(
                open_time_ns=bars[1].interval_start_ns,
                disposition=CoverageDisposition.VERIFIED_NO_TRADE_INTERVAL,
                canonical_bar_identity=NOT_APPLICABLE,
                proof_identity="b" * 64,
                source_reconciliation_identity="c" * 64,
            ),
            accepted(bars[2], CoverageDisposition.DERIVED_FROM_OFFICIAL_TRADES),
            accepted(bars[3], CoverageDisposition.REAL_OFFICIAL_BAR),
        )
        result = validate_sparse_one_minute_grid(
            sparse,
            source_role=SourceRole.SPOT_EXECUTION_1M,
            time_range=spot_range(),
            dispositions=dispositions,
        )
        self.assertEqual(result.expected_count, 4)
        self.assertEqual(result.actual_count, 4)
        self.assertEqual(len(minute_coverage_identity(dispositions)), 64)

    def test_bar_during_verified_no_trade_is_rejected_as_synthetic_inventory(self) -> None:
        bars = spot_bars()
        dispositions = (
            accepted(bars[0], CoverageDisposition.REAL_OFFICIAL_BAR),
            MinuteDisposition(
                open_time_ns=bars[1].interval_start_ns,
                disposition=CoverageDisposition.VERIFIED_NO_TRADE_INTERVAL,
                canonical_bar_identity=NOT_APPLICABLE,
                proof_identity="b" * 64,
                source_reconciliation_identity="c" * 64,
            ),
            accepted(bars[2], CoverageDisposition.REAL_OFFICIAL_BAR),
            accepted(bars[3], CoverageDisposition.REAL_OFFICIAL_BAR),
        )
        with self.assertRaises(DataContractError) as raised:
            validate_sparse_one_minute_grid(
                bars,
                source_role=SourceRole.SPOT_EXECUTION_1M,
                time_range=spot_range(),
                dispositions=dispositions,
            )
        self.assertEqual(raised.exception.code, FailureCode.DATA_GAP.value)

    def test_blocking_or_duplicate_minute_disposition_fails_closed(self) -> None:
        bars = spot_bars()
        blocking = MinuteDisposition(
            open_time_ns=bars[1].interval_start_ns,
            disposition=CoverageDisposition.SOURCE_CONFLICT,
            canonical_bar_identity=NOT_APPLICABLE,
            proof_identity=NOT_APPLICABLE,
            source_reconciliation_identity="d" * 64,
        )
        dispositions = (
            accepted(bars[0], CoverageDisposition.REAL_OFFICIAL_BAR),
            blocking,
            accepted(bars[2], CoverageDisposition.REAL_OFFICIAL_BAR),
            accepted(bars[3], CoverageDisposition.REAL_OFFICIAL_BAR),
        )
        with self.assertRaises(DataContractError) as raised:
            validate_sparse_one_minute_grid(
                (bars[0], bars[2], bars[3]),
                source_role=SourceRole.SPOT_EXECUTION_1M,
                time_range=spot_range(),
                dispositions=dispositions,
            )
        self.assertEqual(raised.exception.code, FailureCode.DATA_GAP.value)
        with self.assertRaises(DataContractError) as duplicate:
            validate_sparse_one_minute_grid(
                bars,
                source_role=SourceRole.SPOT_EXECUTION_1M,
                time_range=spot_range(),
                dispositions=(*dispositions, dispositions[0]),
            )
        self.assertEqual(duplicate.exception.code, FailureCode.DATA_DUPLICATE_CONFLICT.value)

    def test_disposition_shape_cannot_attach_ohlc_to_no_trade(self) -> None:
        with self.assertRaises(ConfigError):
            MinuteDisposition(
                open_time_ns=spot_bars()[0].interval_start_ns,
                disposition=CoverageDisposition.VERIFIED_NO_TRADE_INTERVAL,
                canonical_bar_identity="e" * 64,
                proof_identity="f" * 64,
                source_reconciliation_identity="a" * 64,
            )

    def test_nautilus_export_preserves_official_text_precision(self) -> None:
        source = spot_bars()[0]
        precise = replace(
            source,
            open=source.open + source.open.__class__("0.001"),
            high=source.high + source.high.__class__("0.001"),
            low=source.low + source.low.__class__("0.001"),
            close=source.close + source.close.__class__("0.001"),
        )
        exported = to_nautilus_execution_bars((precise,), metadata=spot_metadata())
        self.assertEqual(str(exported[0].open), format(precise.open, "f"))

    def test_missing_mark_minute_and_execution_price_substitution_are_rejected(self) -> None:
        marks = perp_mark_bars()
        with self.assertRaises(DataContractError) as missing:
            validate_one_minute_grid(
                marks[:-1],
                source_role=SourceRole.USDM_PERPETUAL_MARK_1M,
                time_range=perp_range(),
            )
        self.assertEqual(missing.exception.code, FailureCode.DATA_GAP.value)
        with self.assertRaises(DataContractError) as substituted:
            to_nautilus_mark_updates(perp_execution_bars(), metadata=perp_metadata())
        self.assertEqual(substituted.exception.code, FailureCode.MARK_ROLE_INVALID.value)


DUCKDB_NEGATIVE_PROBE = r"""
import json
import pathlib
import sys
from decimal import Decimal

import duckdb

schema = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
path = pathlib.Path(sys.argv[2])
config = {
    "allow_unsigned_extensions": "false",
    "autoinstall_known_extensions": "false",
    "autoload_known_extensions": "false",
}
connection = duckdb.connect(str(path), config=config)
connection.execute(schema)
connection.executemany(
    "INSERT INTO raw_objects VALUES (?, ?, ?, ?, ?)",
    [
        ("a" * 64, 1, "raw/a", "IMMUTABLE_BINANCE_OFFICIAL_RAW_BYTES", True),
        ("b" * 64, 1, "raw/b", "IMMUTABLE_BINANCE_OFFICIAL_RAW_BYTES", True),
    ],
)
valid = ("c" * 64, "BTCUSDT.BINANCE", 0, 60000000000, 10, 11, 20, 21, "a" * 64, "b" * 64, "{}")
connection.execute("INSERT INTO verified_no_trade_intervals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", valid)
id_gap_rejected = False
try:
    connection.execute(
        "INSERT INTO verified_no_trade_intervals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("d" * 64, "BTCUSDT.BINANCE", 60000000000, 120000000000, 11, 13, 21, 23, "a" * 64, "b" * 64, "{}"),
    )
except Exception:
    id_gap_rejected = True
zero_volume_spot_bar_rejected = False
try:
    connection.execute(
        "INSERT INTO spot_execution_bars_1m VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "1" * 64, "BTCUSDT.BINANCE", 0, 60000000000, 60000000000,
            "REAL_OFFICIAL_BAR", "1", Decimal("1"), "1", Decimal("1"),
            "1", Decimal("1"), "1", Decimal("1"), "0", Decimal("0"),
            "0", Decimal("0"), 0, "0", Decimal("0"), "0", Decimal("0"),
            "a" * 64, '["' + "a" * 64 + '"]',
        ),
    )
except Exception:
    zero_volume_spot_bar_rejected = True
wrong_event_role_rejected = False
try:
    connection.execute(
        "INSERT INTO spot_agg_trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "a" * 64, 1, "USDM_PERPETUAL_EXECUTION", "BTCUSDT", 1,
            "1", Decimal("1"), "1", Decimal("1"), 1, 1, 1, False, True,
        ),
    )
except Exception:
    wrong_event_role_rejected = True
duplicate_funding_rejected = False
connection.execute(
    "INSERT INTO perpetual_funding_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    ("2" * 64, "BTCUSDT-PERP.BINANCE", 1, 8, "0.0001", Decimal("0.0001"), "a" * 64, '["' + "a" * 64 + '"]'),
)
try:
    connection.execute(
        "INSERT INTO perpetual_funding_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("3" * 64, "BTCUSDT-PERP.BINANCE", 1, 4, "0.0002", Decimal("0.0002"), "a" * 64, '["' + "a" * 64 + '"]'),
    )
except Exception:
    duplicate_funding_rejected = True
timestamp_unit_mismatch_rejected = False
try:
    connection.execute(
        "INSERT INTO minute_dispositions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("P", "BTCUSDT.BINANCE", 1, "SOURCE_INCOMPLETE", None, None, "4" * 64, "BAD_UNIT", True),
    )
except Exception:
    timestamp_unit_mismatch_rejected = True
connection.execute("BEGIN TRANSACTION")
connection.execute(
    "INSERT INTO minute_dispositions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
    ("P", "BTCUSDT.BINANCE", 0, "VERIFIED_NO_TRADE_INTERVAL", None, "c" * 64, "e" * 64, "PROOF", False),
)
connection.execute("ROLLBACK")
rollback_ok = connection.execute("SELECT count(*) FROM minute_dispositions").fetchone()[0] == 0
source_mutation_rejected = False
try:
    connection.execute("UPDATE raw_objects SET raw_object_sha256 = ? WHERE raw_object_sha256 = ?", ["f" * 64, "a" * 64])
except Exception:
    source_mutation_rejected = True
double_columns = connection.execute(
    "SELECT count(*) FROM duckdb_columns() WHERE internal = false AND data_type IN ('DOUBLE', 'FLOAT', 'REAL')",
).fetchone()[0]
table_count = connection.execute(
    "SELECT count(*) FROM duckdb_tables() WHERE internal = false",
).fetchone()[0]
connection.execute("CHECKPOINT")
connection.close()
readonly = duckdb.connect(str(path), read_only=True, config=config)
readonly_write_rejected = False
try:
    readonly.execute("DELETE FROM verified_no_trade_intervals")
except Exception:
    readonly_write_rejected = True
readonly.close()
print(json.dumps({
    "duplicate_funding_rejected": duplicate_funding_rejected,
    "double_columns": double_columns,
    "id_gap_rejected": id_gap_rejected,
    "readonly_write_rejected": readonly_write_rejected,
    "rollback_ok": rollback_ok,
    "source_mutation_rejected": source_mutation_rejected,
    "table_count": table_count,
    "timestamp_unit_mismatch_rejected": timestamp_unit_mismatch_rejected,
    "wrong_event_role_rejected": wrong_event_role_rejected,
    "zero_volume_spot_bar_rejected": zero_volume_spot_bar_rejected,
}, sort_keys=True))
"""


REBUILD_MISMATCH_PROBE = r"""
import copy
import json
import pathlib
import sys

from scripts.validate_free_official_binance_rebuild import compare_build_results

first = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
second = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
semantic_mutation_rejected = False
catalog_mutation_rejected = False
mutated = copy.deepcopy(second)
mutated["row_counts"]["spot_execution_bars_1m"] += 1
try:
    compare_build_results(first, mutated)
except RuntimeError as exc:
    semantic_mutation_rejected = "DETERMINISTIC_REBUILD_MISMATCH" in str(exc)
mutated = copy.deepcopy(second)
profile = sorted(mutated["catalogs"])[0]
mutated["catalogs"][profile]["catalog_identity"] = "0" * 64
try:
    compare_build_results(first, mutated)
except RuntimeError as exc:
    catalog_mutation_rejected = "DETERMINISTIC_REBUILD_MISMATCH" in str(exc)
print(json.dumps({
    "catalog_mutation_rejected": catalog_mutation_rejected,
    "semantic_mutation_rejected": semantic_mutation_rejected,
}, sort_keys=True))
"""


class NewDuckDBContractTests(unittest.TestCase):
    def test_constraints_rollback_readonly_and_exact_types(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [DATA_PYTHON, "-c", DUCKDB_NEGATIVE_PROBE, SCHEMA, Path(temporary) / "probe.duckdb"],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        if result.returncode:
            self.fail(result.stdout + result.stderr)
        material = json.loads(result.stdout)
        self.assertEqual(material["table_count"], 18)
        self.assertEqual(material["double_columns"], 0)
        self.assertTrue(material["duplicate_funding_rejected"])
        self.assertTrue(material["id_gap_rejected"])
        self.assertTrue(material["rollback_ok"])
        self.assertTrue(material["source_mutation_rejected"])
        self.assertTrue(material["readonly_write_rejected"])
        self.assertTrue(material["timestamp_unit_mismatch_rejected"])
        self.assertTrue(material["wrong_event_role_rejected"])
        self.assertTrue(material["zero_volume_spot_bar_rejected"])

    def test_nondeterministic_rebuild_and_catalog_mutation_are_rejected(self) -> None:
        site_packages = ROOT / ".venv/lib/python3.12/site-packages"
        environment = {
            **os.environ,
            "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}:{site_packages}",
        }
        result = subprocess.run(
            [
                DATA_PYTHON,
                "-c",
                REBUILD_MISMATCH_PROBE,
                ROOT / "data/duckdb/free-official-binance-data-duckdb-001/primary-v4-result.json",
                ROOT / "data/duckdb/free-official-binance-data-duckdb-001/independent-v4-result.json",
            ],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            self.fail(result.stdout + result.stderr)
        material = json.loads(result.stdout)
        self.assertTrue(material["semantic_mutation_rejected"])
        self.assertTrue(material["catalog_mutation_rejected"])

    def test_builder_prohibits_duckdb_extensions_and_network(self) -> None:
        sources = "\n".join(
            (
                (ROOT / "scripts/build_free_official_binance_release.py").read_text(
                    encoding="utf-8",
                ),
                SCHEMA.read_text(encoding="utf-8"),
            ),
        )
        self.assertIsNone(re.search(r"(?im)^\s*(?:INSTALL|LOAD)\s+", sources))
        self.assertNotIn("HTTPFS", sources.upper())
        self.assertNotIn("REQUESTS.", sources.upper())


if __name__ == "__main__":
    unittest.main()
