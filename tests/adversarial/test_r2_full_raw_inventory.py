from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from crypto_lab.config import ConfigError, MarketProfile
from crypto_lab.data import (
    DataContractError,
    DatasetRawInventory,
    DatasetRelease,
    FULL_RAW_INVENTORY_NORMALIZER_VERSION,
    M3_QUALIFICATION_FULL_RAW_INVENTORY_NORMALIZER_VERSION,
    NOT_AVAILABLE,
    PublisherChecksumBinding,
    RawInventoryObject,
    RawInventoryOrigin,
    SourceObjectBinding,
    SourceRole,
    build_dataset_release,
    parse_funding_csv,
    verify_dataset_raw_inventory,
    validate_research_dataset_rebuild_proof,
)
from crypto_lab.hashing import canonical_sha256
from crypto_lab.status import FailureCode
from tests.m2_helpers import FIXTURES, spot_bars, spot_bindings, spot_metadata, spot_range


ROOT = Path(__file__).resolve().parents[2]
DATA_PYTHON = ROOT / ".data-venv/bin/python"
PRODUCT_SITE = ROOT / ".venv/lib/python3.12/site-packages"
SPOT_PROFILE = MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
CREATED = datetime(2026, 8, 31, tzinfo=UTC)


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def historical_grid_binding(raw_sha256: str, byte_size: int) -> SourceObjectBinding:
    return SourceObjectBinding(
        source_role=SourceRole.SPOT_HISTORICAL_ORDER_GRID,
        source_locator=(
            "https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query"
            "?articleCode=6925d618ab6b47e2936cc4614eaad64b"
        ),
        exact_filename="binance-spot-btcusdt-step-size-2021-08-26.json",
        byte_size=byte_size,
        sha256=raw_sha256,
        publisher_checksum=NOT_AVAILABLE,
        instrument="BTCUSDT",
        market_profile=SPOT_PROFILE.value,
        requested_interval="NOT_APPLICABLE",
        requested_time_range="NOT_APPLICABLE",
        conflicts_with_sha256=(),
    )


def origin(*, raw_sha256: str, role: str, locator: str) -> RawInventoryOrigin:
    return RawInventoryOrigin(
        observation_id=canonical_sha256(
            {"raw_object_sha256": raw_sha256, "source_role": role, "locator": locator},
        ),
        source_role=role,
        exact_locator=locator,
        exact_query_json="{}",
        http_status=200,
        validation_status="RAW_PRESERVED",
        delivery_classification="NOT_APPLICABLE",
    )


def make_release(
    *,
    checksum_text: bytes | None = None,
) -> tuple[DatasetRelease, dict[str, bytes]]:
    execution = b"official execution archive bytes"
    metadata = b"official metadata response bytes"
    grid = b"official historical grid response bytes"
    execution_sha = digest(execution)
    filename = "BTCUSDT-1m-2025-01-01.zip"
    checksum = checksum_text or f"{execution_sha}  {filename}\n".encode("ascii")
    checksum_sha = digest(checksum)
    blobs = {
        execution_sha: execution,
        digest(metadata): metadata,
        digest(grid): grid,
        checksum_sha: checksum,
    }

    direct = list(spot_bindings())
    direct[0] = replace(
        direct[0],
        sha256=execution_sha,
        byte_size=len(execution),
        publisher_checksum=execution_sha,
    )
    direct[1] = replace(
        direct[1],
        sha256=digest(metadata),
        byte_size=len(metadata),
        publisher_checksum=NOT_AVAILABLE,
    )
    direct.append(historical_grid_binding(digest(grid), len(grid)))
    bindings = tuple(
        sorted(direct, key=lambda item: (item.source_role.value, item.source_locator, item.sha256)),
    )

    raw_objects = (
        RawInventoryObject(
            raw_object_sha256=execution_sha,
            byte_size=len(execution),
            instrument="BTCUSDT",
            market_profile=SPOT_PROFILE,
            origins=(
                origin(
                    raw_sha256=execution_sha,
                    role="SPOT_EXECUTION_1M",
                    locator=(
                        "https://data.binance.vision/data/spot/daily/klines/"
                        "BTCUSDT/1m/BTCUSDT-1m-2025-01-01.zip"
                    ),
                ),
            ),
            publisher_checksum_bindings=(
                PublisherChecksumBinding(
                    checksum_raw_object_sha256=checksum_sha,
                    exact_filename=filename,
                    publisher_sha256=execution_sha,
                ),
            ),
        ),
        RawInventoryObject(
            raw_object_sha256=digest(metadata),
            byte_size=len(metadata),
            instrument="BTCUSDT",
            market_profile=SPOT_PROFILE,
            origins=(
                origin(
                    raw_sha256=digest(metadata),
                    role="SPOT_INSTRUMENT_METADATA",
                    locator="https://data-api.binance.vision/api/v3/exchangeInfo?symbol=BTCUSDT",
                ),
            ),
            publisher_checksum_bindings=(),
        ),
        RawInventoryObject(
            raw_object_sha256=digest(grid),
            byte_size=len(grid),
            instrument="BTCUSDT",
            market_profile=SPOT_PROFILE,
            origins=(
                origin(
                    raw_sha256=digest(grid),
                    role="SPOT_HISTORICAL_ORDER_GRID",
                    locator=(
                        "https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query"
                        "?articleCode=6925d618ab6b47e2936cc4614eaad64b"
                    ),
                ),
            ),
            publisher_checksum_bindings=(),
        ),
        RawInventoryObject(
            raw_object_sha256=checksum_sha,
            byte_size=len(checksum),
            instrument="BTCUSDT",
            market_profile=SPOT_PROFILE,
            origins=(
                origin(
                    raw_sha256=checksum_sha,
                    role="PUBLISHER_CHECKSUM",
                    locator=(
                        "https://data.binance.vision/data/spot/daily/klines/"
                        "BTCUSDT/1m/BTCUSDT-1m-2025-01-01.zip.CHECKSUM"
                    ),
                ),
            ),
            publisher_checksum_bindings=(),
        ),
    )
    inventory = DatasetRawInventory.create(
        market_profile=SPOT_PROFILE,
        instrument_id="BTCUSDT.BINANCE",
        data_window_identity="b" * 64,
        source_reconciliation_identity="c" * 64,
        raw_object_count=len(raw_objects),
        raw_objects=tuple(sorted(raw_objects, key=lambda item: item.raw_object_sha256)),
    )
    release = build_dataset_release(
        market_profile=SPOT_PROFILE,
        instrument_id="BTCUSDT.BINANCE",
        source_objects=bindings,
        normalized_time_range=spot_range(),
        instrument_metadata=spot_metadata(),
        execution_bars=spot_bars(),
        catalog_identity="d" * 64,
        created_at_utc=CREATED,
        data_window_identity="b" * 64,
        partition_geometry_identity="e" * 64,
        source_reconciliation_identity="c" * 64,
        derived_validation_identity="f" * 64,
        data_tool_lock_identity="1" * 64,
        data_quality_exposure_identity="2" * 64,
        normalizer_version=FULL_RAW_INVENTORY_NORMALIZER_VERSION,
        raw_inventory=inventory,
    )
    return release, blobs


def materialize(root: Path, blobs: dict[str, bytes]) -> None:
    for raw_sha256, payload in blobs.items():
        target = root / "raw/sha256" / raw_sha256[:2] / f"{raw_sha256}.blob"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


class FullRawInventoryContractTests(unittest.TestCase):
    @staticmethod
    def _rebuild_proof(release: DatasetRelease) -> dict[str, object]:
        inventory = release.raw_inventory
        assert isinstance(inventory, DatasetRawInventory)
        spot = MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY.value
        perpetual = (
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING.value
        )
        other_release = "9" * 64
        other_catalog = "8" * 64
        other_inventory = "7" * 64
        materialized = {
            spot: {
                "dataset_release_id": release.dataset_release_id,
                "catalog_identity": release.catalog_identity,
                "raw_inventory_identity": inventory.raw_inventory_identity,
                "raw_inventory_object_count": inventory.raw_object_count,
            },
            perpetual: {
                "dataset_release_id": other_release,
                "catalog_identity": other_catalog,
                "raw_inventory_identity": other_inventory,
                "raw_inventory_object_count": 1,
            },
        }
        catalogs = {
            spot: {"status": "PASS", "catalog_identity": release.catalog_identity},
            perpetual: {"status": "PASS", "catalog_identity": other_catalog},
        }
        inventory_results = [
            {
                "market_profile": spot,
                "dataset_release_id": release.dataset_release_id,
                "raw_inventory_identity": inventory.raw_inventory_identity,
                "raw_object_count": inventory.raw_object_count,
                "four_way_equality": True,
            },
            {
                "market_profile": perpetual,
                "dataset_release_id": other_release,
                "raw_inventory_identity": other_inventory,
                "raw_object_count": 1,
                "four_way_equality": True,
            },
        ]
        gate = {"status": "PASS", "full_raw_inventory_results": inventory_results}
        return {
            "schema": (
                "free-official-binance-deterministic-rebuild-validation-v2-"
                "full-raw-inventory"
            ),
            "status": "PASS",
            "duckdb_version": "1.4.5",
            "comparison": {
                "status": "PASS",
                "dataset_release_ids": sorted(
                    [release.dataset_release_id, other_release],
                ),
                "catalog_identities": sorted(
                    [release.catalog_identity, other_catalog],
                ),
            },
            "primary_readonly_gate": gate,
            "independent_readonly_gate": json.loads(json.dumps(gate)),
            "catalog_physical_comparison": {},
            "materialized_release_artifacts": materialized,
            "nautilus_catalog_validation": catalogs,
            "strategy_run": False,
            "official_trial": False,
            "network_used": False,
        }

    def test_research_release_requires_exact_independent_four_way_proof(self) -> None:
        release, _ = make_release()
        proof = self._rebuild_proof(release)
        validate_research_dataset_rebuild_proof(release, proof)
        changed = json.loads(json.dumps(proof))
        changed["materialized_release_artifacts"][release.market_profile.value][
            "raw_inventory_object_count"
        ] += 1
        with self.assertRaises(DataContractError) as raised:
            validate_research_dataset_rebuild_proof(release, changed)
        self.assertEqual(
            raised.exception.code,
            FailureCode.DATASET_RAW_INVENTORY_MISMATCH.value,
        )
        extra = json.loads(json.dumps(proof))
        extra["undeclared"] = True
        with self.assertRaises(DataContractError) as root_shape:
            validate_research_dataset_rebuild_proof(release, extra)
        self.assertEqual(
            root_shape.exception.code,
            FailureCode.DATASET_RAW_INVENTORY_MISMATCH.value,
        )

    def test_qualification_full_inventory_is_distinct_and_does_not_weaken_research_grid(self) -> None:
        release, _ = make_release()
        inventory = release.raw_inventory
        assert isinstance(inventory, DatasetRawInventory)
        grid_sha = next(
            source.sha256
            for source in release.source_objects
            if source.source_role is SourceRole.SPOT_HISTORICAL_ORDER_GRID
        )
        sources = tuple(
            source
            for source in release.source_objects
            if source.source_role is not SourceRole.SPOT_HISTORICAL_ORDER_GRID
        )
        source_reconciliation_identity = canonical_sha256(
            [source.to_builtins() for source in sources],
        )
        raw_objects = tuple(
            item for item in inventory.raw_objects if item.raw_object_sha256 != grid_sha
        )
        qualified_inventory = DatasetRawInventory.create(
            market_profile=inventory.market_profile,
            instrument_id=inventory.instrument_id,
            data_window_identity=inventory.data_window_identity,
            source_reconciliation_identity=source_reconciliation_identity,
            raw_object_count=len(raw_objects),
            raw_objects=raw_objects,
        )
        material = release.material_payload()
        material.update(
            {
                "normalizer_version": M3_QUALIFICATION_FULL_RAW_INVENTORY_NORMALIZER_VERSION,
                "source_objects": [source.to_builtins() for source in sources],
                "source_reconciliation_identity": source_reconciliation_identity,
                "derived_validation_identity": "NOT_APPLICABLE",
                "data_tool_lock_identity": "NOT_APPLICABLE",
                "raw_inventory": qualified_inventory.to_builtins(),
            },
        )
        qualification_release = replace(
            release,
            dataset_release_id=canonical_sha256(material),
            normalizer_version=M3_QUALIFICATION_FULL_RAW_INVENTORY_NORMALIZER_VERSION,
            source_objects=sources,
            source_reconciliation_identity=source_reconciliation_identity,
            derived_validation_identity="NOT_APPLICABLE",
            data_tool_lock_identity="NOT_APPLICABLE",
            raw_inventory=qualified_inventory,
        )
        self.assertTrue(qualification_release.has_full_raw_inventory)
        with self.assertRaisesRegex(ConfigError, "Spot source roles are invalid"):
            replace(
                qualification_release,
                normalizer_version=FULL_RAW_INVENTORY_NORMALIZER_VERSION,
                derived_validation_identity=release.derived_validation_identity,
                data_tool_lock_identity=release.data_tool_lock_identity,
            )

    def test_schema_v2_is_typed_canonical_and_round_trips(self) -> None:
        release, _ = make_release()
        self.assertEqual(release.schema_version, 2)
        self.assertIsInstance(release.raw_inventory, DatasetRawInventory)
        self.assertEqual(len(release.source_objects), 3)
        self.assertEqual(release.raw_inventory.raw_object_count, 4)
        self.assertEqual(DatasetRelease.from_json_bytes(release.to_json_bytes()), release)

    def test_missing_direct_source_and_missing_checksum_closure_fail_closed(self) -> None:
        release, _ = make_release()
        inventory = release.raw_inventory
        assert isinstance(inventory, DatasetRawInventory)
        metadata_sha = release.source_objects[-1].sha256
        without_metadata = tuple(
            item for item in inventory.raw_objects if item.raw_object_sha256 != metadata_sha
        )
        reduced = DatasetRawInventory.create(
            market_profile=inventory.market_profile,
            instrument_id=inventory.instrument_id,
            data_window_identity=inventory.data_window_identity,
            source_reconciliation_identity=inventory.source_reconciliation_identity,
            raw_object_count=len(without_metadata),
            raw_objects=without_metadata,
        )
        with self.assertRaises(ConfigError):
            replace(
                release,
                raw_inventory=reduced,
                dataset_release_id=canonical_sha256(
                    {**release.material_payload(), "raw_inventory": reduced.to_builtins()},
                ),
            )
        checksum_hashes = {
            binding.checksum_raw_object_sha256
            for item in inventory.raw_objects
            for binding in item.publisher_checksum_bindings
        }
        without_checksum = tuple(
            item for item in inventory.raw_objects if item.raw_object_sha256 not in checksum_hashes
        )
        with self.assertRaises(ConfigError):
            DatasetRawInventory.create(
                market_profile=inventory.market_profile,
                instrument_id=inventory.instrument_id,
                data_window_identity=inventory.data_window_identity,
                source_reconciliation_identity=inventory.source_reconciliation_identity,
                raw_object_count=len(without_checksum),
                raw_objects=without_checksum,
            )

    def test_order_profile_and_locator_mutations_fail_closed(self) -> None:
        release, _ = make_release()
        inventory = release.raw_inventory
        assert isinstance(inventory, DatasetRawInventory)
        with self.assertRaises(ConfigError):
            DatasetRawInventory.create(
                market_profile=inventory.market_profile,
                instrument_id=inventory.instrument_id,
                data_window_identity=inventory.data_window_identity,
                source_reconciliation_identity=inventory.source_reconciliation_identity,
                raw_object_count=inventory.raw_object_count,
                raw_objects=tuple(reversed(inventory.raw_objects)),
            )
        first = inventory.raw_objects[0]
        with self.assertRaises(ConfigError):
            DatasetRawInventory.create(
                market_profile=inventory.market_profile,
                instrument_id=inventory.instrument_id,
                data_window_identity=inventory.data_window_identity,
                source_reconciliation_identity=inventory.source_reconciliation_identity,
                raw_object_count=inventory.raw_object_count,
                raw_objects=(
                    replace(
                        first,
                        market_profile=(
                            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING
                        ),
                    ),
                    *inventory.raw_objects[1:],
                ),
            )
        with self.assertRaises(ConfigError):
            replace(
                first,
                origins=(
                    replace(
                        first.origins[0],
                        exact_locator="https://attacker.invalid/object",
                    ),
                ),
            )

    def test_runtime_verifier_rejects_missing_and_same_size_tamper(self) -> None:
        release, blobs = make_release()
        inventory = release.raw_inventory
        assert isinstance(inventory, DatasetRawInventory)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            materialize(root, blobs)
            verify_dataset_raw_inventory(release, root)
            indirect = next(
                item.raw_object_sha256
                for item in inventory.raw_objects
                if item.raw_object_sha256 not in {source.sha256 for source in release.source_objects}
            )
            indirect_path = root / "raw/sha256" / indirect[:2] / f"{indirect}.blob"
            original = indirect_path.read_bytes()
            indirect_path.unlink()
            with self.assertRaises(DataContractError) as missing:
                verify_dataset_raw_inventory(release, root)
            self.assertEqual(missing.exception.code, FailureCode.DATA_HASH_MISMATCH.value)
            indirect_path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
            with self.assertRaises(DataContractError) as altered:
                verify_dataset_raw_inventory(release, root)
            self.assertEqual(altered.exception.code, FailureCode.DATA_HASH_MISMATCH.value)

    def test_publisher_checksum_lexeme_is_verified_after_rehashing(self) -> None:
        release, blobs = make_release(
            checksum_text=f"{'0' * 64}  BTCUSDT-1m-2025-01-01.zip\n".encode("ascii"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            materialize(root, blobs)
            with self.assertRaises(DataContractError) as raised:
                verify_dataset_raw_inventory(release, root)
        self.assertEqual(raised.exception.code, FailureCode.DATA_SOURCE_INVALID.value)

    def test_funding_raw_lexeme_is_material_and_cannot_be_normalized_silently(self) -> None:
        payload = FIXTURES.joinpath("usdm-funding.csv").read_bytes()
        first_raw = payload.decode("utf-8").splitlines()[1].split(",")[2]
        event = parse_funding_csv(
            payload,
            instrument_id="BTCUSDT-PERP.BINANCE",
        )[0]
        self.assertEqual(event.raw_rate_text, first_raw)
        alternate = first_raw + "0"
        self.assertEqual(Decimal(alternate), event.funding_rate)
        with self.assertRaises(DataContractError) as raised:
            replace(event, raw_rate_text=alternate)
        self.assertEqual(raised.exception.code, FailureCode.DATA_HASH_MISMATCH.value)
        alternate_key = canonical_sha256(
            {
                "instrument_id": event.instrument_id,
                "calc_time_ns": event.calc_time_ns,
                "funding_interval_hours": event.funding_interval_hours,
                "funding_rate": event.funding_rate,
                "funding_rate_raw_lexeme": alternate,
            },
        )
        alternate_event = replace(event, raw_rate_text=alternate, event_key=alternate_key)
        self.assertNotEqual(
            canonical_sha256(event.semantic_payload()),
            canonical_sha256(alternate_event.semantic_payload()),
        )

    def test_raw_materialization_is_atomic_copy_not_hardlink(self) -> None:
        probe = r'''
import json, pathlib, sys
from scripts.validate_free_official_binance_rebuild import preserve_raw_independent_copy
source = pathlib.Path(sys.argv[1]); target = pathlib.Path(sys.argv[2])
preserve_raw_independent_copy(source, target)
print(json.dumps({
    "bytes_equal": source.read_bytes() == target.read_bytes(),
    "same_inode": (source.stat().st_dev, source.stat().st_ino) == (target.stat().st_dev, target.stat().st_ino),
    "mode": target.stat().st_mode & 0o777,
}))
'''
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "corpus.bin"
            target = root / "portable/raw.bin"
            source.write_bytes(b"immutable corpus bytes")
            result = subprocess.run(
                [DATA_PYTHON, "-c", probe, source, target],
                cwd=ROOT,
                env={
                    **os.environ,
                    "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}:{PRODUCT_SITE}",
                },
                check=False,
                capture_output=True,
                text=True,
            )
        if result.returncode:
            self.fail(result.stdout + result.stderr)
        material = json.loads(result.stdout)
        self.assertTrue(material["bytes_equal"])
        self.assertFalse(material["same_inode"])
        self.assertEqual(material["mode"], 0o444)

    def test_independent_projection_includes_indirect_and_checksum_sources(self) -> None:
        probe = r'''
import json
from scripts.build_free_official_binance_release import SPOT_PROFILE, derive_participating_raw_hashes
a,b,c,d,e,f,z,g = (char * 64 for char in "abcdef01")
class Result:
    def __init__(self, rows): self.rows = rows
    def fetchall(self): return self.rows
class Connection:
    def execute(self, query, parameters=None):
        compact = " ".join(query.split())
        if "source_sha256s_json FROM spot_execution_bars_1m" in compact: return Result([(json.dumps([a]),)])
        if "FROM spot_agg_trades" in compact: return Result([(b,)])
        if "FROM verified_no_trade_intervals" in compact: return Result([(c,d)])
        if "source_observation_ids_json FROM source_conflicts" in compact: return Result([])
        if "observation_id, raw_object_sha256 FROM source_observations" in compact: return Result([])
        if "FROM instrument_metadata_source_bindings AS binding" in compact: return Result([(e,)])
        if "archive_raw_object_sha256, checksum_raw_object_sha256 FROM publisher_checksums" in compact: return Result([(a,f),(z,g)])
        raise AssertionError(compact)
actual = derive_participating_raw_hashes(Connection(), SPOT_PROFILE)
print(json.dumps({"actual": sorted(actual), "expected": sorted({a,b,c,d,e,f})}))
'''
        result = subprocess.run(
            [DATA_PYTHON, "-c", probe],
            cwd=ROOT,
            env={
                **os.environ,
                "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}:{PRODUCT_SITE}",
            },
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            self.fail(result.stdout + result.stderr)
        material = json.loads(result.stdout)
        self.assertEqual(material["actual"], material["expected"])

    def test_four_way_gate_rejects_missing_member_and_changed_role_by_code(self) -> None:
        probe = r'''
import hashlib, json, pathlib, sys
from types import SimpleNamespace
from crypto_lab.config import MarketProfile
from crypto_lab.data import DatasetRawInventory, RawInventoryObject, RawInventoryOrigin
from crypto_lab.hashing import canonical_sha256
from scripts.build_free_official_binance_release import verify_full_raw_inventory_gate
path = pathlib.Path(sys.argv[1]); payload = path.read_bytes(); digest = hashlib.sha256(payload).hexdigest()
origin = RawInventoryOrigin(
    observation_id=canonical_sha256({"raw": digest}),
    source_role="SPOT_EXECUTION_1M",
    exact_locator="https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/a.zip",
    exact_query_json="{}", http_status=200, validation_status="RAW_PRESERVED",
    delivery_classification="NOT_APPLICABLE",
)
item = RawInventoryObject(
    raw_object_sha256=digest, byte_size=len(payload), instrument="BTCUSDT",
    market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
    origins=(origin,), publisher_checksum_bindings=(),
)
inventory = DatasetRawInventory.create(
    market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
    instrument_id="BTCUSDT.BINANCE", data_window_identity="a" * 64,
    source_reconciliation_identity="b" * 64, raw_object_count=1, raw_objects=(item,),
)
release = SimpleNamespace(
    raw_inventory=inventory,
    market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
    dataset_release_id="c" * 64,
)
registry = SimpleNamespace(raw={digest: SimpleNamespace(byte_size=len(payload), local_path=path)})
class Result:
    def __init__(self, rows): self.rows = rows
    def fetchall(self): return self.rows
class Connection:
    def __init__(self, members, role, participates=True):
        self.members, self.role, self.participates = members, role, participates
    def execute(self, query, parameters=None):
        compact = " ".join(query.split())
        if "member_identity FROM release_members" in compact: return Result([(digest,)] if self.members else [])
        if "source_sha256s_json FROM spot_execution_bars_1m" in compact:
            return Result([(json.dumps([digest]),)] if self.participates else [])
        if "FROM spot_agg_trades" in compact or "FROM verified_no_trade_intervals" in compact: return Result([])
        if "source_observation_ids_json FROM source_conflicts" in compact: return Result([])
        if "observation_id, raw_object_sha256 FROM source_observations" in compact: return Result([])
        if "FROM instrument_metadata_source_bindings AS binding" in compact: return Result([])
        if "archive_raw_object_sha256, checksum_raw_object_sha256 FROM publisher_checksums" in compact: return Result([])
        if "raw_object_sha256, byte_size, content_verified FROM raw_objects" in compact: return Result([(digest,len(payload),True)])
        if "SELECT raw_object_sha256, observation_id, source_role" in compact:
            return Result([(digest,origin.observation_id,self.role,origin.exact_locator,"{}",200,"RAW_PRESERVED","NOT_APPLICABLE")])
        if "exact_filename, publisher_sha256 FROM publisher_checksums" in compact: return Result([])
        raise AssertionError(compact)
def failure(connection):
    try: verify_full_raw_inventory_gate(connection, release, registry)
    except RuntimeError as exc: return str(exc).split(":",1)[0]
    return "PASS"
print(json.dumps({
    "missing_member": failure(Connection(False, origin.source_role)),
    "nonparticipating_extra": failure(Connection(True, origin.source_role, False)),
    "changed_role": failure(Connection(True, "SPOT_MUTATED_ROLE")),
}))
'''
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "raw.bin"
            source.write_bytes(b"one independently verified Raw object")
            result = subprocess.run(
                [DATA_PYTHON, "-c", probe, source],
                cwd=ROOT,
                env={
                    **os.environ,
                    "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}:{PRODUCT_SITE}",
                },
                check=False,
                capture_output=True,
                text=True,
            )
        if result.returncode:
            self.fail(result.stdout + result.stderr)
        material = json.loads(result.stdout)
        self.assertEqual(material["missing_member"], "DATASET_RAW_INVENTORY_MISMATCH")
        self.assertEqual(
            material["nonparticipating_extra"],
            "DATASET_RAW_INVENTORY_MISMATCH",
        )
        self.assertEqual(material["changed_role"], "DATA_SOURCE_INVALID")


if __name__ == "__main__":
    unittest.main()
