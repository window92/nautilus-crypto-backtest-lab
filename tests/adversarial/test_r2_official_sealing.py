from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.sealing import COMMON_OFFICIAL_LEAVES
from crypto_lab.sealing import OfficialSealOutcome
from crypto_lab.sealing import build_evidence_manifest
from crypto_lab.sealing import build_official_seal
from crypto_lab.sealing import build_official_status
from crypto_lab.sealing import verify_official_seal
from crypto_lab.sealing import write_canonical_json


class OfficialSealingAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="r2-official-seal-")
        self.run_dir = Path(self.temporary.name)
        self.run_id = "r2-seal-fixture"
        leaves = set(COMMON_OFFICIAL_LEAVES) | {"funding.csv", "funding_source.json"}
        for name in sorted(leaves):
            (self.run_dir / name).write_bytes(f"fixture:{name}\n".encode())
        config = {
            "run_id": self.run_id,
            "market_profile": "BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING",
        }
        source = {
            "git_commit": "1" * 40,
            "git_tree": "2" * 40,
        }
        dataset = {"dataset_release_id": "3" * 64}
        runtime = {"installed_payload_sha256": "4" * 64}
        component = {
            "outcome": "COMPONENT_CHECK_PASS",
            "failure_codes": [],
            "checks": [{"name": "fixture", "pass": True}],
            "mutated_run_evidence": False,
        }
        self._write("lab_run_config.json", config)
        (self.run_dir / "lab_run_config.sha256").write_text("5" * 64 + "\n")
        self._write("source_revision.json", source)
        self._write("dataset_release.json", dataset)
        self._write("runtime_identity.json", runtime)
        self._write("component_validation.json", component)
        self.component = component
        self._seal()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, name: str, value: object) -> None:
        (self.run_dir / name).write_bytes(canonical_json_bytes(value) + b"\n")

    def _seal(self) -> None:
        manifest = build_evidence_manifest(self.run_dir, run_id=self.run_id)
        write_canonical_json(self.run_dir / "evidence_manifest.json", manifest)
        status = build_official_status(
            run_id=self.run_id,
            state="COMPLETED",
            failure_codes=(),
            component_outcome="COMPONENT_CHECK_PASS",
            component_validation_sha256=sha256_file(
                self.run_dir / "component_validation.json",
            ),
            manifest_sha256=sha256_file(self.run_dir / "evidence_manifest.json"),
        )
        write_canonical_json(self.run_dir / "status.json", status)
        seal = build_official_seal(
            self.run_dir,
            run_id=self.run_id,
        )
        write_canonical_json(self.run_dir / "official_seal.json", seal)

    def _verify(self):
        # The fixture intentionally contains tiny placeholder leaves.  Patch
        # the fixed checker symbol at the unit-test boundary; production
        # callers cannot inject an alternative validator into the seal API.
        with mock.patch("crypto_lab.checker.check_evidence_directory") as checker:
            checker.return_value.to_builtins.return_value = self.component
            return verify_official_seal(self.run_dir)

    def test_public_api_rejects_injected_component_pass_oracle(self) -> None:
        with self.assertRaises(TypeError):
            verify_official_seal(
                self.run_dir,
                component_revalidator=lambda _path: self.component,  # type: ignore[call-arg]
            )

    def test_complete_exact_package_has_official_seal_pass(self) -> None:
        report = self._verify()
        self.assertEqual(report.outcome, OfficialSealOutcome.OFFICIAL_SEAL_PASS)
        self.assertEqual(report.failure_codes, ())
        self.assertTrue(all(item["pass"] for item in report.checks))

    def test_canonical_no_fill_stream_may_be_empty_but_funding_csv_may_not(self) -> None:
        original = (self.run_dir / "native_fills.jsonl").read_bytes()
        (self.run_dir / "native_fills.jsonl").write_bytes(b"")
        for name in ("evidence_manifest.json", "status.json", "official_seal.json"):
            (self.run_dir / name).unlink()
        self._seal()
        self.assertEqual(self._verify().outcome, OfficialSealOutcome.OFFICIAL_SEAL_PASS)
        (self.run_dir / "native_fills.jsonl").write_bytes(original)

    def test_missing_mandatory_funding_is_structured_failure(self) -> None:
        (self.run_dir / "funding.csv").unlink()
        report = self._verify()
        self.assertEqual(report.outcome, OfficialSealOutcome.OFFICIAL_SEAL_FAIL)
        profile = next(item for item in report.checks if item["name"] == "profile_file_contract")
        self.assertIn("funding.csv", profile["missing"])

    def test_extra_modified_and_empty_leaf_are_rejected(self) -> None:
        (self.run_dir / "undeclared.txt").write_text("extra\n")
        self.assertEqual(self._verify().outcome, OfficialSealOutcome.OFFICIAL_SEAL_FAIL)
        (self.run_dir / "undeclared.txt").unlink()
        original_fills = (self.run_dir / "fills.csv").read_bytes()
        (self.run_dir / "fills.csv").write_text("mutated\n")
        self.assertEqual(self._verify().outcome, OfficialSealOutcome.OFFICIAL_SEAL_FAIL)
        (self.run_dir / "fills.csv").write_bytes(original_fills)
        (self.run_dir / "funding.csv").write_bytes(b"")
        self.assertEqual(self._verify().outcome, OfficialSealOutcome.OFFICIAL_SEAL_FAIL)

    def test_manifest_builder_cannot_authorize_a_preexisting_extra_leaf(self) -> None:
        for name in ("evidence_manifest.json", "status.json", "official_seal.json"):
            (self.run_dir / name).unlink()
        (self.run_dir / "undeclared.txt").write_text("extra before manifest\n")
        with self.assertRaisesRegex(ValueError, "closed profile contract"):
            build_evidence_manifest(self.run_dir, run_id=self.run_id)

    def test_research_release_requires_rebuild_proof_as_a_sealed_leaf(self) -> None:
        for name in ("evidence_manifest.json", "status.json", "official_seal.json"):
            (self.run_dir / name).unlink()
        dataset = json.loads((self.run_dir / "dataset_release.json").read_bytes())
        dataset["normalizer_version"] = "binance-public-data-v1-m2.5"
        self._write("dataset_release.json", dataset)
        with self.assertRaisesRegex(ValueError, "dataset_rebuild_validation.json"):
            build_evidence_manifest(self.run_dir, run_id=self.run_id)
        self._write(
            "dataset_rebuild_validation.json",
            {"schema": "fixture-rebuild-proof", "status": "PASS"},
        )
        self._seal()
        (self.run_dir / "dataset_rebuild_validation.json").unlink()
        report = self._verify()
        self.assertEqual(report.outcome, OfficialSealOutcome.OFFICIAL_SEAL_FAIL)
        self.assertIn("EVIDENCE_INCOMPLETE", report.failure_codes)

    def test_symlink_leaf_is_rejected_before_hash_trust(self) -> None:
        target = self.run_dir / "outside"
        target.write_text("not allowed\n")
        (self.run_dir / "orders.csv").unlink()
        (self.run_dir / "orders.csv").symlink_to(target)
        report = self._verify()
        self.assertNotEqual(report.outcome, OfficialSealOutcome.OFFICIAL_SEAL_PASS)

    def test_manifest_status_and_seal_tamper_each_fail(self) -> None:
        for name in ("evidence_manifest.json", "status.json", "official_seal.json"):
            with self.subTest(name=name):
                original = (self.run_dir / name).read_bytes()
                payload = json.loads(original)
                payload["run_id"] = "different-run"
                self._write(name, payload)
                self.assertNotEqual(
                    self._verify().outcome,
                    OfficialSealOutcome.OFFICIAL_SEAL_PASS,
                )
                (self.run_dir / name).write_bytes(original)

    def test_unknown_root_fields_fail_after_all_downstream_hashes_are_rebuilt(self) -> None:
        originals = {
            name: (self.run_dir / name).read_bytes()
            for name in ("evidence_manifest.json", "status.json", "official_seal.json")
        }
        for name in originals:
            with self.subTest(name=name):
                for restore_name, payload in originals.items():
                    (self.run_dir / restore_name).write_bytes(payload)
                value = json.loads((self.run_dir / name).read_bytes())
                value["undeclared_schema_field"] = "must-fail-closed"
                if name == "official_seal.json":
                    value.pop("seal_identity")
                    value["seal_identity"] = canonical_sha256(value)
                    self._write(name, value)
                else:
                    self._write(name, value)
                    if name == "evidence_manifest.json":
                        status = json.loads((self.run_dir / "status.json").read_bytes())
                        status["evidence_manifest_sha256"] = sha256_file(
                            self.run_dir / "evidence_manifest.json",
                        )
                        self._write("status.json", status)
                    seal = build_official_seal(self.run_dir, run_id=self.run_id)
                    self._write("official_seal.json", seal)
                report = self._verify()
                self.assertEqual(report.outcome, OfficialSealOutcome.OFFICIAL_SEAL_FAIL)
                self.assertIn("EVIDENCE_INCOMPLETE", report.failure_codes)


if __name__ == "__main__":
    unittest.main()
