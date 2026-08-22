from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from crypto_lab import DatasetRelease
from crypto_lab import LabRunConfig
from crypto_lab import LabRunRequest
from crypto_lab import MarketProfile
from crypto_lab import RunState
from crypto_lab import run_lab
from crypto_lab.hashing import canonical_sha256
from crypto_lab.runner import QualificationControl
from crypto_lab.runner import capture_source_revision
from crypto_lab.strategies import StrategyPlan
from crypto_lab.strategies import StrategySpec
from crypto_lab.strategies import OrderIntent
from tests.helpers import load_spot_config_dict


ROOT = Path(__file__).resolve().parents[2]
REPAIR_EVIDENCE = ROOT / "evidence/m2/m2-repair-001"


def iso(value) -> str:
    return value.isoformat().replace("+00:00", "Z")


def request_for(release: DatasetRelease, evidence_root: Path) -> LabRunRequest:
    spec = StrategySpec(
        strategy_id="m2-strict-datasetrelease-downstream-smoke",
        strategy_version="1",
        market_profile=release.market_profile,
        instrument_id=release.instrument_id,
        signal_bar_types=(f"{release.instrument_id}-1-MINUTE-LAST-EXTERNAL",),
        parameters={"fixture": "M2_STRICT_DATASETRELEASE_DIRECT"},
        indicator_definitions=(),
        warmup_requirement="ONE_MINUTE",
        sizing_rule="NO_ORDERS",
        entry_rule="NO_ORDERS",
        exit_rule="NO_ORDERS",
        conflict_rule="FIRST_ELIGIBLE_INTENT",
        terminal_behavior="MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE",
        market_order_time_in_force="GTC",
    )
    raw = copy.deepcopy(load_spot_config_dict())
    scoring_start = release.normalized_time_range.start_inclusive + timedelta(minutes=1)
    raw.update(
        {
            "run_id": "m2-strict-release-direct-smoke",
            "run_purpose": "QUALIFICATION",
            "market_profile": release.market_profile.value,
            "instrument_id": release.instrument_id,
            "dataset_release_id": release.dataset_release_id,
            "strategy_spec_id": spec.strategy_spec_id,
            "warmup_start": iso(release.normalized_time_range.start_inclusive),
            "scoring_start": iso(scoring_start),
            "scoring_end_exclusive": iso(release.normalized_time_range.end_exclusive),
            "execution_bar_type": f"{release.instrument_id}-1-MINUTE-LAST-EXTERNAL",
            "signal_bar_types": [f"{release.instrument_id}-1-MINUTE-LAST-EXTERNAL"],
            "funding_binding": release.funding_data_identity,
            "mark_binding": release.mark_data_identity,
        },
    )
    raw["fee_assumption"] = {
        "maker_fee": "0",
        "taker_fee": "0",
        "explicit_zero_fee": True,
        "reason": "M2 qualification-only downstream interface smoke; no historical tier claim",
        "claim_class": "ESTIMATED_FEE",
    }
    venue = raw["nautilus_venue_config"]
    venue["account_type"] = "MARGIN"
    venue["instrument_leverages"] = [
        {"instrument_id": release.instrument_id, "leverage": "1"},
    ]
    raw["nautilus_engine_config"]["portfolio"]["use_mark_prices"] = True
    raw["nautilus_data_config"] = [
        {
            "catalog_path": str(ROOT / "data/catalog" / release.catalog_identity),
            "catalog_fs_protocol": "file",
            "catalog_fs_storage_options": {},
            "catalog_fs_rust_storage_options": {},
            "data_type": "Bar",
            "instrument_id": release.instrument_id,
            "start_time": iso(release.normalized_time_range.start_inclusive),
            "end_time": iso(release.normalized_time_range.end_exclusive),
            "filter_expr": "NOT_APPLICABLE",
            "client_id": "NOT_APPLICABLE",
            "metadata": {},
            "bar_spec": "1-MINUTE-LAST",
            "instrument_ids": [],
            "bar_types": [],
            "optimize_file_loading": False,
        },
    ]
    config = LabRunConfig.from_json_bytes(json.dumps(raw, separators=(",", ":")).encode())
    return LabRunRequest(
        lab_run_config=config,
        source_revision=capture_source_revision(ROOT),
        strategy_spec=spec,
        dataset_release=release,
        instrument=None,
        data=(),
        strategy_plan=StrategyPlan(
            intents_by_bar_ns={},
            conflict_rule="FIRST_ELIGIBLE_INTENT",
            qualification_attempt_all_intents=False,
        ),
        evidence_root=evidence_root,
        qualification_control=QualificationControl.STANDARD,
    )


class M2RepairedDownstreamContractTests(unittest.TestCase):
    def release(self) -> DatasetRelease:
        return DatasetRelease.from_json_bytes(
            REPAIR_EVIDENCE.joinpath("perpetual-qualification-release.json").read_bytes(),
        )

    def test_strict_datasetrelease_flows_directly_through_public_run_lab(self) -> None:
        release = self.release()
        with tempfile.TemporaryDirectory() as temporary:
            result = run_lab(request_for(release, Path(temporary)))
            self.assertEqual(result.state, RunState.COMPLETED)
            self.assertEqual(result.failure_codes, ())
            persisted = result.evidence_dir.joinpath("dataset_release.json").read_bytes()
            self.assertEqual(persisted, release.to_json_bytes() + b"\n")
            checker = json.loads(result.evidence_dir.joinpath("checker.json").read_text())
            checks = {item["name"]: item for item in checker["checks"]}
            self.assertTrue(checks["dataset_binding"]["pass"])
            self.assertTrue(checks["dataset_source_roles"]["pass"])
            self.assertTrue(checks["dataset_catalog_binding"]["pass"])

    def test_interface_identity_mutation_blocks_with_downstream_contract_failure(self) -> None:
        release = self.release()
        material = release.material_payload()
        material["catalog_identity"] = "f" * 64
        mutated = replace(
            release,
            catalog_identity="f" * 64,
            dataset_release_id=canonical_sha256(material),
        )
        with tempfile.TemporaryDirectory() as temporary:
            request = request_for(release, Path(temporary))
            result = run_lab(replace(request, dataset_release=mutated))
            self.assertEqual(result.state, RunState.BLOCKED)
            self.assertIn("DOWNSTREAM_CONTRACT_FAILURE", result.failure_codes)

    def test_native_market_limits_are_enforced_before_nautilus_submission(self) -> None:
        release = self.release()
        signal_ns = release.normalized_time_range.start_ns + 120_000_000_000

        def with_quantity(root: Path, quantity: str) -> LabRunRequest:
            request = request_for(release, root)
            return replace(
                request,
                strategy_plan=StrategyPlan(
                    intents_by_bar_ns={
                        signal_ns: (
                            OrderIntent(
                                side="BUY",
                                quantity=quantity,
                                order_type="MARKET",
                                reason="REPAIRED_MARKET_LIMIT_BINDING",
                            ),
                        ),
                    },
                    conflict_rule="FIRST_ELIGIBLE_INTENT",
                    qualification_attempt_all_intents=False,
                ),
            )

        with tempfile.TemporaryDirectory() as accepted_root:
            accepted = run_lab(with_quantity(Path(accepted_root), "120.000"))
            self.assertEqual(accepted.state, RunState.COMPLETED)
            self.assertEqual(len(accepted.strategy_observations["submitted_intents"]), 1)
            self.assertEqual(accepted.strategy_observations["guard_failures"], [])

        for invalid in ("120.001", "1.0000"):
            with self.subTest(quantity=invalid), tempfile.TemporaryDirectory() as rejected_root:
                rejected = run_lab(with_quantity(Path(rejected_root), invalid))
                self.assertEqual(rejected.state, RunState.BLOCKED)
                self.assertIn("INSTRUMENT_METADATA_INVALID", rejected.failure_codes)
                self.assertEqual(rejected.orders, ())
                self.assertEqual(rejected.fills, ())


if __name__ == "__main__":
    unittest.main()
