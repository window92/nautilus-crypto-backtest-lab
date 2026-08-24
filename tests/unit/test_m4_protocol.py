from __future__ import annotations

import json
import unittest
from dataclasses import replace
from decimal import Decimal

from crypto_lab.config import ConfigError
from crypto_lab.research import ClaimScope
from crypto_lab.research import InstrumentScope
from crypto_lab.research import PartitionRole
from crypto_lab.research import PartitionStartEvidence
from crypto_lab.research import ResearchError
from crypto_lab.research import ResearchScheduler
from crypto_lab.research import ResearchProtocol
from crypto_lab.research import TrialDefinition
from crypto_lab.research import UniverseMembershipDecision
from crypto_lab.research import UniverseMembershipEvidence
from crypto_lab.research import validate_partition_boundaries
from tests.m4_helpers import DEVELOPMENT
from tests.m4_helpers import HOLDOUT
from tests.m4_helpers import OOS
from tests.m4_helpers import VALIDATION
from tests.m4_helpers import candidate
from tests.m4_helpers import instant
from tests.m4_helpers import interval
from tests.m4_helpers import valid_protocol


class ResearchProtocolTests(unittest.TestCase):
    def test_protocol_is_strict_immutable_and_content_addressed(self) -> None:
        protocol = valid_protocol()
        reparsed = ResearchProtocol.from_json_bytes(protocol.to_json_bytes())
        self.assertEqual(reparsed, protocol)
        self.assertEqual(reparsed.protocol_id, protocol.protocol_id)
        with self.assertRaises(TypeError):
            protocol.parameter_domain["lookback"] = ("99",)

        raw = json.loads(protocol.to_json_bytes())
        raw["unknown"] = True
        with self.assertRaises(ConfigError):
            ResearchProtocol.from_json_bytes(json.dumps(raw).encode())
        del raw["unknown"]
        del raw["primary_metric"]
        with self.assertRaises(ConfigError):
            ResearchProtocol.from_json_bytes(json.dumps(raw).encode())
        duplicate = protocol.to_json_bytes()[:-1] + b',"research_family_id":"duplicate"}'
        with self.assertRaises(ConfigError):
            ResearchProtocol.from_json_bytes(duplicate)

    def test_material_post_result_change_creates_new_identity(self) -> None:
        before = valid_protocol()
        for change in (
            {"primary_metric": "NAUTILUS_NATIVE_MAX_DRAWDOWN"},
            {"dataset_release_ids": ("e" * 64,)},
            {"final_holdout_interval": interval("2020-04-01T00:00:00Z", "2020-05-02T00:00:00Z")},
            {"random_seeds": (9,)},
        ):
            with self.subTest(change=change):
                values = dict(change)
                if "final_holdout_interval" in values:
                    values["required_benchmark"] = replace(
                        before.required_benchmark,
                        scored_interval=values["final_holdout_interval"],
                    )
                changed = ResearchProtocol.create_from(
                    before,
                    frozen_at_utc=instant("2020-06-01T00:00:00Z"),
                    **values,
                )
                self.assertNotEqual(before.protocol_id, changed.protocol_id)
                self.assertEqual(before.research_family_id, changed.research_family_id)

    def test_partition_overlap_and_insufficient_purge_fail_closed(self) -> None:
        validate_partition_boundaries(
            DEVELOPMENT,
            VALIDATION,
            OOS,
            HOLDOUT,
            valid_protocol().purge_embargo_rule,
        )
        with self.assertRaisesRegex(ResearchError, "PARTITION_LEAKAGE"):
            validate_partition_boundaries(
                DEVELOPMENT,
                interval("2020-01-15T00:00:00Z", "2020-03-01T00:00:00Z"),
                OOS,
                HOLDOUT,
                valid_protocol().purge_embargo_rule,
            )
        with self.assertRaisesRegex(ResearchError, "PARTITION_LEAKAGE"):
            replace(
                valid_protocol().purge_embargo_rule,
                mode="APPLICABLE",
                reason="Forward label",
                max_forward_dependency_seconds=3600,
            )

    def test_scope_is_frozen_and_cannot_silently_expand(self) -> None:
        single = valid_protocol()
        self.assertEqual(single.instrument_scope, InstrumentScope.SINGLE_INSTRUMENT)
        self.assertEqual(single.intended_claim_scope, ClaimScope.INSTRUMENT_ONLY)
        with self.assertRaisesRegex(ResearchError, "RESEARCH_PROTOCOL_INVALID"):
            ResearchProtocol.create_from(
                single,
                instrument_scope=InstrumentScope.POINT_IN_TIME_UNIVERSE,
                intended_claim_scope=ClaimScope.POINT_IN_TIME_UNIVERSE,
                instrument_ids=("BTCUSDT.BINANCE", "ETHUSDT.BINANCE"),
                universe_selection_rule="CURRENT_SURVIVORS",
                universe_as_of_rule="NOT_APPLICABLE",
                universe_membership_sha256="NOT_APPLICABLE",
            )

    def test_scheduler_enforces_candidate_order_budget_and_no_relabel_repeat(self) -> None:
        protocol = valid_protocol(candidate_count=2)
        scheduler = ResearchScheduler(protocol)
        first = scheduler.next_candidate(())
        self.assertEqual(first.candidate_id, candidate(0).candidate_id)
        definition = TrialDefinition.synthetic(
            trial_id="trial-1",
            protocol=protocol,
            candidate=first,
            run_id="run-1",
        )
        second = scheduler.next_candidate((definition,))
        self.assertEqual(second.candidate_id, candidate(1).candidate_id)
        repeated_label = replace(candidate(0), candidate_label="renamed-winner")
        repeated_definition = replace(definition, candidate_id=repeated_label.candidate_id)
        with self.assertRaisesRegex(ResearchError, "RESEARCH_PROTOCOL_INVALID"):
            scheduler.next_candidate((definition, repeated_definition))
        with self.assertRaisesRegex(ResearchError, "RESEARCH_PROTOCOL_INVALID"):
            scheduler.next_candidate(
                (
                    definition,
                    TrialDefinition.synthetic(
                        trial_id="trial-2",
                        protocol=protocol,
                        candidate=second,
                        run_id="run-2",
                    ),
                ),
            )

    def test_scheduler_rejects_symbol_window_cost_seed_and_release_expansion(self) -> None:
        protocol = valid_protocol()
        frozen = TrialDefinition.synthetic(
            trial_id="trial-locked",
            protocol=protocol,
            candidate=candidate(0),
            run_id="run-locked",
        )
        for field, value in (
            ("instrument_id", "ETHUSDT.BINANCE"),
            ("dataset_release_id", "e" * 64),
            ("scored_interval", interval("2020-04-02T00:00:00Z", "2020-05-01T00:00:00Z")),
            ("seed", 9),
            ("config_sha256", "e" * 64),
        ):
            with self.subTest(field=field):
                changed = replace(frozen, **{field: value})
                with self.assertRaisesRegex(ResearchError, "RESEARCH_PROTOCOL_INVALID"):
                    ResearchScheduler(protocol).validate_trial(changed)

    def test_partition_state_resets_capital_and_forbids_warmup_or_financial_carry(self) -> None:
        valid = dict(
            partition_role=PartitionRole.VALIDATION,
            configured_initial_capital=Decimal("100000"),
            observed_starting_cash=Decimal("100000"),
            observed_starting_position_quantity=Decimal("0"),
            observed_starting_realized_pnl=Decimal("0"),
            pending_strategy_orders=0,
            warmup_scored_order_count=0,
            scoring_start_utc=instant("2020-02-01T00:00:00Z"),
            warmup_context_end_exclusive=instant("2020-02-01T00:00:00Z"),
            source="NAUTILUS_PERSISTED_RUN_EVIDENCE",
        )
        PartitionStartEvidence(**valid)
        for field, value in (
            ("observed_starting_cash", Decimal("99999")),
            ("observed_starting_position_quantity", Decimal("1")),
            ("observed_starting_realized_pnl", Decimal("2")),
            ("pending_strategy_orders", 1),
            ("warmup_scored_order_count", 1),
            ("warmup_context_end_exclusive", instant("2020-02-01T00:00:01Z")),
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ResearchError, "PARTITION_LEAKAGE"):
                    PartitionStartEvidence(**{**valid, field: value})

    def test_point_in_time_membership_rejects_current_survivor_or_future_information(self) -> None:
        valid_decision = UniverseMembershipDecision(
            instrument_id="BTCUSDT.BINANCE",
            selected=True,
            selection_timestamp_utc=instant("2020-01-01T00:00:00Z"),
            source_observed_at_utc=instant("2019-12-31T23:59:59Z"),
            official_source_reference="frozen-membership-fixture",
            source_content_sha256="a" * 64,
            available_fields=("listing_status", "quote_volume"),
        )
        evidence = UniverseMembershipEvidence.create((valid_decision,))
        self.assertEqual(evidence.decisions, (valid_decision,))
        with self.assertRaisesRegex(ResearchError, "RESEARCH_PROTOCOL_INVALID"):
            replace(
                valid_decision,
                source_observed_at_utc=instant("2020-01-02T00:00:00Z"),
                official_source_reference="current-survivor-list",
            )

    def test_benchmark_must_be_frozen_on_same_interval_with_explicit_cost_basis(self) -> None:
        protocol = valid_protocol()
        outside = interval("2021-01-01T00:00:00Z", "2021-02-01T00:00:00Z")
        for change in (
            {"frozen_before_result_exposure": False},
            {"cost_basis": "UNKNOWN"},
            {"scored_interval": outside},
        ):
            with self.subTest(change=change):
                with self.assertRaisesRegex(ResearchError, "RESEARCH_PROTOCOL_INVALID"):
                    ResearchProtocol.create_from(
                        protocol,
                        required_benchmark=replace(protocol.required_benchmark, **change),
                    )
        development_benchmark = ResearchProtocol.create_from(
            protocol,
            required_benchmark=replace(
                protocol.required_benchmark,
                scored_interval=protocol.development_interval,
            ),
        )
        self.assertEqual(
            development_benchmark.required_benchmark.scored_interval,
            protocol.development_interval,
        )

    def test_decimal_and_timestamp_shape_rejects_noncanonical_material(self) -> None:
        raw = json.loads(valid_protocol().to_json_bytes())
        raw["frozen_at_utc"] = "2019-12-01T01:00:00+01:00"
        with self.assertRaises(ConfigError):
            ResearchProtocol.from_json_bytes(json.dumps(raw).encode())
        raw = json.loads(valid_protocol().to_json_bytes())
        raw["ordered_candidates"][0]["parameter_values"]["threshold"] = float("nan")
        with self.assertRaises((ConfigError, ValueError)):
            ResearchProtocol.from_json_bytes(json.dumps(raw).encode())


if __name__ == "__main__":
    unittest.main()
