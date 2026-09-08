from __future__ import annotations

import ast
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import crypto_lab
from crypto_lab.config import ConfigError
from crypto_lab.hashing import sha256_file
from crypto_lab.m3 import QualificationDownstreamBundle
from crypto_lab.m3 import QualifiedProfileRegistry
from crypto_lab.research import M3ResearchBoundary
from crypto_lab.research import ResearchEligibility
from tests.integration.test_m3_m4_downstream_contract import write_v2_downstream_fixture


ROOT = Path(__file__).resolve().parents[2]
M3_EVIDENCE = ROOT / "evidence/m3/m3-acceptance-001"


class M4PublicContractTests(unittest.TestCase):
    def test_legacy_v1_public_input_is_diagnostic_not_current_boundary(self) -> None:
        registry_path = M3_EVIDENCE / "qualified-profile-registry.json"
        before = {
            path: sha256_file(path)
            for path in (registry_path, *sorted((M3_EVIDENCE / "downstream").glob("*.json")))
        }
        legacy = QualifiedProfileRegistry.from_json_bytes(registry_path.read_bytes())
        self.assertEqual(legacy.schema_version, 1)
        self.assertEqual({record.checker_result for record in legacy.records}, {"CHECK_PASS"})
        with self.assertRaisesRegex(
            ConfigError,
            "mechanical_integrity.component_validation",
        ):
            M3ResearchBoundary.load(
                registry_path=registry_path,
                downstream_directory=M3_EVIDENCE / "downstream",
                expected_registry_identity=legacy.registry_content_sha256,
            )
        after = {path: sha256_file(path) for path in before}
        self.assertEqual(before, after)

    def test_m4_consumes_v2_component_registry_and_bundles_without_mutation(self) -> None:
        with TemporaryDirectory() as temporary:
            registry_path, downstream, registry = write_v2_downstream_fixture(Path(temporary))
            before = {
                path: sha256_file(path)
                for path in (registry_path, *sorted(downstream.glob("*.json")))
            }
            boundary = M3ResearchBoundary.load(
                registry_path=registry_path,
                downstream_directory=downstream,
                expected_registry_identity=registry.registry_content_sha256,
            )
            self.assertEqual(len(boundary.bundles), 2)
            self.assertEqual(boundary.registry.schema_version, 2)
            self.assertEqual(
                {bundle.profile_record.checker_result for bundle in boundary.bundles},
                {"COMPONENT_CHECK_PASS"},
            )
            self.assertTrue(all(
                bundle.claim_evaluation.research_eligibility is ResearchEligibility.INELIGIBLE
                for bundle in boundary.bundles
            ))
            after = {path: sha256_file(path) for path in before}
            self.assertEqual(before, after)

    def test_public_shapes_parse_without_defaults_or_internal_m3_imports(self) -> None:
        for public_name in (
            "ResearchProtocol",
            "TrialRecord",
            "HoldoutLockStore",
            "PerformanceDiagnostics",
            "ClaimEvaluation",
            "ReportInput",
            "ReportOutput",
        ):
            self.assertTrue(hasattr(crypto_lab, public_name), public_name)
        with TemporaryDirectory() as temporary:
            registry_path, downstream, registry = write_v2_downstream_fixture(Path(temporary))
            self.assertEqual(
                QualifiedProfileRegistry.from_json_bytes(registry_path.read_bytes()),
                registry,
            )
            for record in registry.records:
                bundle = QualificationDownstreamBundle.from_json_bytes(
                    (downstream / f"{record.profile_id.value}.json").read_bytes(),
                )
                self.assertEqual(bundle.profile_record, record)
                self.assertEqual(
                    bundle.run_result["component_validation_outcome"],
                    "COMPONENT_CHECK_PASS",
                )
        source = (ROOT / "src/crypto_lab/research.py").read_text(encoding="utf-8")
        imports = [
            node for node in ast.walk(ast.parse(source))
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        forbidden = ("scripts.", "crypto_lab.runner", "crypto_lab.data")
        rendered = "\n".join(ast.unparse(node) for node in imports)
        self.assertFalse(any(item in rendered for item in forbidden), rendered)

    def test_dependency_direction_has_no_reporting_to_execution_path(self) -> None:
        reporting = (ROOT / "src/crypto_lab/reporting.py").read_text(encoding="utf-8")
        tree = ast.parse(reporting)
        rendered = "\n".join(
            ast.unparse(node)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        )
        for forbidden in (
            "crypto_lab.runner",
            "crypto_lab.data",
            "nautilus_trader.backtest",
            "nautilus_trader.execution",
        ):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
