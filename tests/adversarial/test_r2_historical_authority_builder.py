from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any

from crypto_lab.historical_contracts import load_historical_authority_manifest
from scripts.build_historical_validator_authorities import BUILD_SPEC_SCHEMA
from scripts.build_historical_validator_authorities import EXPECTED_RESULTS_SCHEMA
from scripts.build_historical_validator_authorities import HistoricalAuthorityBuildError
from scripts.build_historical_validator_authorities import build_manifest
from scripts.build_historical_validator_authorities import derive_current_product_build_spec
from scripts.build_historical_validator_authorities import external_root_identity


ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts/build_historical_validator_authorities.py"
VALIDATORS = (
    "validate_audit_qualification.py",
    "validate_audit_research_runs.py",
    "validate_data_provenance_evidence.py",
    "validate_free_official_binance_rebuild.py",
    "validate_free_official_raw_objects.py",
    "validate_instrument_repair_evidence.py",
    "validate_instrument_representation_continuity.py",
    "validate_m1_evidence.py",
    "validate_m2_evidence.py",
    "validate_m3_evidence.py",
    "validate_m4_evidence.py",
    "validate_native_research_metrics_readiness_evidence.py",
    "validate_owner_smoke_002_replacement_evidence.py",
    "validate_owner_strategy_research_001_evidence.py",
)


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class HistoricalBuilderFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.repository = root / "repository"
        self.repository.mkdir()
        _git(self.repository, "init", "-b", "main")
        _git(self.repository, "config", "user.name", "R2 Builder Test")
        _git(self.repository, "config", "user.email", "r2-builder@example.invalid")
        _git(
            self.repository,
            "remote",
            "add",
            "origin",
            "https://example.invalid/r2-historical-builder.git",
        )
        _write(self.repository / ".gitignore", "/data/\n")
        _write(self.repository / "SSOT.md", "fixture authority\n")
        _write(self.repository / "data-tool.lock.json", "{}\n")
        _write(self.repository / "pyproject.toml", "[project]\nname='fixture'\n")
        _write(self.repository / "requirements.data.lock.txt", "duckdb==fixture\n")
        _write(self.repository / "requirements.lock.txt", "nautilus==fixture\n")
        _write(self.repository / "runtime.lock.json", '{"historical":true}\n')
        _write(self.repository / "schemas/fixture.json", "{}\n")
        _write(
            self.repository / "contracts/historical-contract-snapshots.json",
            "{}\n",
        )
        free_payload = b"free-official-raw-fixture\n"
        free_digest = hashlib.sha256(free_payload).hexdigest()
        free_path = (
            "data/raw/data-provenance-duckdb-001/objects/sha256/"
            f"{free_digest[:2]}/{free_digest}.bin"
        )
        free_file = self.repository / free_path
        free_file.parent.mkdir(parents=True, exist_ok=True)
        free_file.write_bytes(free_payload)
        _write_json(
            self.repository
            / "evidence/repair/free-official-binance-data-duckdb-001/raw-object-inventory.json",
            {
                "unique_raw_object_count": 1,
                "raw_objects": [
                    {
                        "raw_object_path": free_path,
                        "raw_object_sha256": free_digest,
                        "byte_size": len(free_payload),
                    },
                ],
            },
        )
        for suffix, payload in (("main", b"m2-main\n"), ("addendum", b"m2-addendum\n")):
            digest = hashlib.sha256(payload).hexdigest()
            raw = self.repository / f"data/raw/sha256/{digest[:2]}/{digest}.blob"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_bytes(payload)
            filename = (
                "raw-object-inventory.json"
                if suffix == "main"
                else "raw-object-inventory-addendum-001.json"
            )
            _write_json(
                self.repository / "evidence/m2/m2-acceptance-001" / filename,
                {
                    "object_count": 1,
                    "objects": [
                        {
                            "sha256": digest,
                            "byte_size": len(payload),
                        },
                    ],
                },
            )
        instrument_payload = b"instrument-raw-fixture\n"
        instrument_digest = hashlib.sha256(instrument_payload).hexdigest()
        instrument = self.repository / (
            "data/raw/instrument-representation-funding-checker-001/objects/sha256/"
            f"{instrument_digest[:2]}/{instrument_digest}.bin"
        )
        instrument.parent.mkdir(parents=True, exist_ok=True)
        instrument.write_bytes(instrument_payload)
        _write(self.repository / "src/crypto_lab/__init__.py", '"""fixture"""\n')
        _write(
            self.repository / "src/crypto_lab/historical_contracts.py",
            "def classify(value):\n    return value\n",
        )
        for name in reversed(VALIDATORS):
            _write(
                self.repository / "scripts" / name,
                "from crypto_lab.historical_contracts import classify\n"
                "print(classify('PASS'))\n",
            )
        _write(
            self.repository / "scripts/run_historical_evidence_acceptance.py",
            "raise SystemExit('fixture wrapper is identity-only')\n",
        )
        builder = self.repository / "scripts/build_historical_validator_authorities.py"
        builder.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BUILDER, builder)
        _git(self.repository, "add", ".")
        _git(self.repository, "commit", "-m", "historical validator semantics")
        self.historical_commit = _git(self.repository, "rev-parse", "HEAD")
        _write(self.repository / "SSOT.md", "fixture product authority after historical result\n")
        _git(self.repository, "add", "SSOT.md")
        _git(self.repository, "commit", "-m", "current product authority")
        self.commit = _git(self.repository, "rev-parse", "HEAD")
        self.builder = builder

        # Ignored external bytes are intentionally created after the Product
        # commit.  They cannot silently become executable Git inputs.
        _write(self.repository / "data/source/z-last.bin", "z\n")
        _write(self.repository / "data/source/a-first.bin", "a\n")
        current_inputs = (
            "data/duckdb/instrument-representation-funding-checker-001/primary-v6-result.json",
            "data/duckdb/instrument-representation-funding-checker-001/independent-v3-result.json",
            "data/duckdb/instrument-representation-funding-checker-001/release-artifacts/release.json",
            "data/duckdb/instrument-representation-funding-checker-001/deterministic-validation-v6.json",
            "data/duckdb/instrument-representation-funding-checker-001/primary-v6.duckdb",
            "data/duckdb/instrument-representation-funding-checker-001/independent-v3.duckdb",
            "data/duckdb/binance-btcusdt-owner-smoke-001.duckdb",
            "data/duckdb/instrument-representation-funding-checker-001/value-continuity-v1.json",
            "data/duckdb/free-official-binance-data-duckdb-001/primary-v4.duckdb",
            "data/catalog/instrument-representation-funding-checker-001/primary-v6/catalog.bin",
            "data/catalog/instrument-representation-funding-checker-001/independent-v3/catalog.bin",
        )
        for relative in current_inputs:
            _write(self.repository / relative, f"fixture:{relative}\n")
        identity = external_root_identity(self.repository, "data/source")
        self.legacy = root / "legacy.json"
        _write_json(
            self.legacy,
            {
                "schema": "historical-contract-snapshots-v1",
                "snapshots": {
                    "legacy": {
                        "git_commit": self.historical_commit,
                        "files": {"SSOT.md": hashlib.sha256(b"fixture authority\n").hexdigest()},
                    },
                },
                "validators": {name: "legacy" for name in reversed(VALIDATORS)},
            },
        )
        self.spec_path = root / "build-spec.json"
        self.expected_stdout_sha256 = hashlib.sha256(b"PASS\n").hexdigest()
        self.expected_stderr_sha256 = hashlib.sha256(b"").hexdigest()
        profile = {
            "bootstrap_sha256": "a" * 64,
            "initial_sys_path": [],
            "python": {},
            "site_packages": {},
        }
        self.spec: dict[str, Any] = {
            "schema": BUILD_SPEC_SCHEMA,
            "product_commit": self.commit,
            "execution_plan": list(VALIDATORS),
            "runtime_profiles": {"fixture-runtime": profile},
            "validators": {
                name: {
                    "authority_id": f"fixture-{name[:-3]}",
                    "source_commit": self.commit,
                    "entrypoint": f"scripts/{name}",
                    "wrapper": "src/crypto_lab/historical_contracts.py",
                    "closure_paths": [],
                    "arguments": ["{repository}"],
                    "external_files": [],
                    "external_roots": [
                        {
                            "locator": "data/source",
                            "target": "data/raw-view",
                            **identity,
                        },
                    ],
                    "interpreter_profile": "fixture-runtime",
                    "expected_exit_code": 0,
                    "expected_status": "PASS",
                    "expected_stdout_sha256": self.expected_stdout_sha256,
                    "expected_stderr_sha256": self.expected_stderr_sha256,
                }
                for name in VALIDATORS
            },
        }
        self.write_spec()
        self.project_runtime_authority = root / "project-runtime-authority.json"
        self.data_runtime_authority = root / "data-runtime-authority.json"
        self.expected_results = root / "expected-results.json"
        self._write_runtime_authorities()
        self._write_expected_results()

    def _write_expected_results(self) -> None:
        _write_json(
            self.expected_results,
            {
                "schema": EXPECTED_RESULTS_SCHEMA,
                "product_commit": self.commit,
                "results": {
                    name: {
                        "source_commit": self.historical_commit,
                        "expected_exit_code": 0,
                        "expected_status": "PASS",
                        "expected_stdout_sha256": self.expected_stdout_sha256,
                        "expected_stderr_sha256": self.expected_stderr_sha256,
                    }
                    for name in VALIDATORS
                },
            },
        )

    def _write_runtime_authorities(self) -> None:
        tree = _git(self.repository, "rev-parse", "HEAD^{tree}")

        def authority(*, duckdb: bool) -> dict[str, Any]:
            distributions = [
                {"dist_info_relative_path": "nautilus_trader-2.0.0rc2.dist-info"},
            ]
            if duckdb:
                distributions.append({"dist_info_relative_path": "duckdb-1.4.5.dist-info"})
            return {
                "schema": "isolated-runtime-bootstrap-authority-v1",
                "bootstrap_sha256": "a" * 64,
                "initial_sys_path": [],
                "python": {"venv_executable": "/fixture/python-data" if duckdb else "/fixture/python"},
                "site_packages": {"distributions": distributions},
                "product": {
                    "source_commit": self.commit,
                    "source_tree": tree,
                },
                "allowed_targets": {"entrypoints": [], "scripts": []},
            }

        _write_json(self.project_runtime_authority, authority(duckdb=False))
        _write_json(self.data_runtime_authority, authority(duckdb=True))

    def write_spec(self) -> None:
        _write_json(self.spec_path, self.spec)

    def build(self) -> dict[str, Any]:
        return build_manifest(
            repository=self.repository,
            build_spec_path=self.spec_path,
            legacy_manifest_path=self.legacy,
            builder_path=self.builder,
        )

    def derive(self) -> dict[str, Any]:
        return derive_current_product_build_spec(
            repository=self.repository,
            product_commit=self.commit,
            legacy_manifest_path=self.legacy,
            project_runtime_authority_path=self.project_runtime_authority,
            data_runtime_authority_path=self.data_runtime_authority,
            expected_results_path=self.expected_results,
            builder_path=self.builder,
        )


class HistoricalAuthorityBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = HistoricalBuilderFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_fixture_plan_is_the_current_repository_legacy_plan(self) -> None:
        current = json.loads(
            (ROOT / "contracts/historical-contract-snapshots.json").read_text(encoding="utf-8"),
        )
        self.assertEqual(tuple(sorted(current["validators"])), VALIDATORS)

    def test_exact_fourteen_plan_build_is_byte_deterministic_and_parseable(self) -> None:
        first = self.fixture.build()
        second = self.fixture.build()
        self.assertEqual(_canonical_bytes(first), _canonical_bytes(second))
        self.assertEqual(tuple(first["execution_plan"]), VALIDATORS)
        self.assertEqual(len(first["authorities"]), 14)
        for name in VALIDATORS:
            authority = first["authorities"][name]
            closure = {item["path"] for item in authority["executable_closure"]}
            self.assertIn(f"scripts/{name}", closure)
            self.assertIn("src/crypto_lab/__init__.py", closure)
            self.assertIn("src/crypto_lab/historical_contracts.py", closure)
            self.assertEqual(
                [item["target"] for item in authority["external_bindings"]],
                ["data/raw-view/a-first.bin", "data/raw-view/z-last.bin"],
            )
        output = Path(self.temporary.name) / "authority.json"
        output.write_bytes(_canonical_bytes(first))
        loaded = load_historical_authority_manifest(output)
        self.assertEqual(loaded["execution_plan"], VALIDATORS)

    def test_current_product_spec_is_derived_without_fourteen_manual_authorities(self) -> None:
        first = self.fixture.derive()
        second = self.fixture.derive()
        self.assertEqual(_canonical_bytes(first), _canonical_bytes(second))
        self.assertEqual(tuple(first["execution_plan"]), VALIDATORS)
        self.assertNotEqual(self.fixture.historical_commit, self.fixture.commit)
        self.assertTrue(
            all(
                value["source_commit"] == self.fixture.historical_commit
                and value["wrapper"] == value["entrypoint"]
                for value in first["validators"].values()
            ),
        )
        self.assertEqual(
            first["validators"]["validate_data_provenance_evidence.py"][
                "interpreter_profile"
            ],
            "data-runtime",
        )
        self.assertEqual(
            first["validators"]["validate_m2_evidence.py"]["interpreter_profile"],
            "project-runtime",
        )
        self.assertTrue(first["validators"]["validate_m2_evidence.py"]["external_files"])
        self.assertFalse(first["validators"]["validate_m1_evidence.py"]["external_files"])
        self.assertTrue(
            all(
                value["expected_stdout_sha256"] == self.fixture.expected_stdout_sha256
                and value["expected_stderr_sha256"] == self.fixture.expected_stderr_sha256
                for value in first["validators"].values()
            ),
        )

        derived_path = Path(self.temporary.name) / "derived-spec.json"
        _write_json(derived_path, first)
        manifest = build_manifest(
            repository=self.fixture.repository,
            build_spec_path=derived_path,
            legacy_manifest_path=self.fixture.legacy,
            builder_path=self.fixture.builder,
        )
        self.assertEqual(tuple(manifest["execution_plan"]), VALIDATORS)

    def test_derive_requires_combined_duckdb_and_nautilus_data_runtime(self) -> None:
        value = json.loads(self.fixture.data_runtime_authority.read_text(encoding="utf-8"))
        value["site_packages"]["distributions"] = [
            {"dist_info_relative_path": "nautilus_trader-2.0.0rc2.dist-info"},
        ]
        _write_json(self.fixture.data_runtime_authority, value)
        with self.assertRaisesRegex(HistoricalAuthorityBuildError, "lacks required"):
            self.fixture.derive()

    def test_derive_rejects_missing_or_cross_commit_expected_results(self) -> None:
        value = json.loads(self.fixture.expected_results.read_text(encoding="utf-8"))
        value["results"].pop(VALIDATORS[0])
        _write_json(self.fixture.expected_results, value)
        with self.assertRaisesRegex(HistoricalAuthorityBuildError, "do not equal"):
            self.fixture.derive()

        self.fixture._write_expected_results()
        value = json.loads(self.fixture.expected_results.read_text(encoding="utf-8"))
        value["results"][VALIDATORS[0]]["source_commit"] = self.fixture.commit
        _write_json(self.fixture.expected_results, value)
        with self.assertRaisesRegex(HistoricalAuthorityBuildError, "is invalid"):
            self.fixture.derive()

    def test_expected_output_digest_is_part_of_bundle_identity(self) -> None:
        first = self.fixture.build()
        name = VALIDATORS[0]
        before = first["authorities"][name]["bundle_identity"]
        self.fixture.spec["validators"][name]["expected_stdout_sha256"] = "f" * 64
        self.fixture.write_spec()
        after = self.fixture.build()["authorities"][name]
        self.assertEqual(after["expected_stdout_sha256"], "f" * 64)
        self.assertNotEqual(after["bundle_identity"], before)

    def test_external_root_inventory_addition_fails_closed(self) -> None:
        _write(self.fixture.repository / "data/source/unpinned.bin", "surprise\n")
        with self.assertRaisesRegex(
            HistoricalAuthorityBuildError,
            "external root inventory expectation differs",
        ):
            self.fixture.build()

    def test_external_root_symlink_fails_closed(self) -> None:
        source = self.fixture.repository / "data/source/a-first.bin"
        source.unlink()
        source.symlink_to(self.fixture.repository / "runtime.lock.json")
        with self.assertRaisesRegex(HistoricalAuthorityBuildError, "symlink in external root"):
            self.fixture.build()

    def test_external_file_hash_mismatch_fails_closed(self) -> None:
        validator = self.fixture.spec["validators"][VALIDATORS[0]]
        source = self.fixture.repository / "data/source/a-first.bin"
        validator["external_roots"] = []
        validator["external_files"] = [
            {
                "locator": "data/source/a-first.bin",
                "target": "evidence/exact.bin",
                "sha256": "0" * 64,
                "size_bytes": source.stat().st_size,
            },
        ]
        self.fixture.write_spec()
        with self.assertRaisesRegex(HistoricalAuthorityBuildError, "expectation differs"):
            self.fixture.build()

    def test_external_target_cannot_overlay_different_historical_git_bytes(self) -> None:
        validator = self.fixture.spec["validators"][VALIDATORS[0]]
        source = self.fixture.repository / "data/source/a-first.bin"
        payload = source.read_bytes()
        validator["external_roots"] = []
        validator["external_files"] = [
            {
                "locator": "data/source/a-first.bin",
                "target": "runtime.lock.json",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            },
        ]
        self.fixture.write_spec()
        with self.assertRaisesRegex(
            HistoricalAuthorityBuildError,
            "collides with different historical Git bytes",
        ):
            self.fixture.build()

    def test_symbolic_commit_name_is_rejected_instead_of_inferred(self) -> None:
        self.fixture.spec["validators"][VALIDATORS[0]]["source_commit"] = "HEAD"
        self.fixture.write_spec()
        with self.assertRaisesRegex(HistoricalAuthorityBuildError, "explicit 40-character commit"):
            self.fixture.build()

    def test_noncanonical_or_incomplete_plan_is_rejected(self) -> None:
        self.fixture.spec["execution_plan"] = list(reversed(VALIDATORS))
        self.fixture.write_spec()
        with self.assertRaisesRegex(HistoricalAuthorityBuildError, "canonical exact 14-validator"):
            self.fixture.build()

    def test_executed_builder_must_equal_product_commit(self) -> None:
        with self.fixture.builder.open("a", encoding="utf-8") as stream:
            stream.write("\n# uncommitted mutation\n")
        with self.assertRaisesRegex(HistoricalAuthorityBuildError, "executed builder differs"):
            self.fixture.build()


if __name__ == "__main__":
    unittest.main()
