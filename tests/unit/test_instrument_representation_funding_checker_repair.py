from __future__ import annotations

import ast
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from nautilus_trader.model import Price
from nautilus_trader.model import Quantity

from crypto_lab.checker import CheckerOutcome
from crypto_lab.checker import check_evidence_directory
from crypto_lab.checker import validate_owner_smoke_funding_binding
from crypto_lab.data import DataContractError
from crypto_lab.data import FUNDING_NATIVE_BINDING_RC2_EXPLICIT_BOUNDARY_PAIR
from crypto_lab.data import FundingEvent
from crypto_lab.data import InstrumentMetadata
from crypto_lab.data import bind_lossless_instrument_representation
from crypto_lab.data import lossless_runtime_quantity_text
from crypto_lab.data import to_nautilus_execution_bars
from crypto_lab.data import to_nautilus_funding_updates
from crypto_lab.data import to_nautilus_instrument
from crypto_lab.data import to_nautilus_mark_updates
from crypto_lab.data import validate_limit_order_price
from crypto_lab.data import validate_market_order_quantity
from crypto_lab.owner import _official_child_environment
from crypto_lab.status import FailureCode
from tests.m2_helpers import perp_execution_bars
from tests.m2_helpers import perp_mark_bars
from tests.m2_helpers import spot_bars


ROOT = Path(__file__).resolve().parents[2]
SPOT_OLD_METADATA = "44b14e49d0799f12cfa6492b3b56fe38a650e05852f4f8602c0038514c69106e"
PERP_OLD_METADATA = "24ffafc364399f2f44dd893976c18561d6c131f68ba9429636e0d3fd86e6a615"
BOUNDARY = 1_610_000_000_003_000_000


def metadata(identity: str) -> InstrumentMetadata:
    return InstrumentMetadata.from_json_bytes(
        (ROOT / "data/releases" / f"{identity}.metadata.json").read_bytes(),
    )


def repaired_spot() -> InstrumentMetadata:
    return bind_lossless_instrument_representation(
        metadata(SPOT_OLD_METADATA),
        price_precision=2,
        size_precision=6,
        size_increment=Decimal("0.000001"),
        representation_evidence={"precision_audit_identity": "1" * 64},
        economic_order_grid_evidence={
            "official_exchange_info_sha256": "2" * 64,
            "historical_exact_for_window": False,
        },
    )


def repaired_perp() -> InstrumentMetadata:
    return bind_lossless_instrument_representation(
        metadata(PERP_OLD_METADATA),
        price_precision=8,
        size_precision=3,
        price_increment=Decimal("0.01"),
        representation_evidence={"precision_audit_identity": "3" * 64},
        economic_order_grid_evidence={
            "official_rule_change_sha256": "4" * 64,
            "historical_exact_for_window": True,
        },
    )


class LosslessRepresentationTests(unittest.TestCase):
    def test_spot_and_perpetual_runtime_precision_is_instrument_bound(self) -> None:
        spot = repaired_spot()
        perp = repaired_perp()
        spot_native = to_nautilus_instrument(spot)
        perp_native = to_nautilus_instrument(perp)
        self.assertEqual((spot.price_precision, spot.size_precision), (2, 6))
        self.assertEqual((perp.price_precision, perp.size_precision), (8, 3))
        self.assertEqual(str(spot_native.price_increment), "0.01")
        self.assertEqual(str(spot_native.size_increment), "0.000001")
        self.assertEqual(spot.lot_size_step_size, Decimal("0.000001"))
        self.assertEqual(
            spot.official_definition["filters"][1]["stepSize"],
            "0.00001000",
        )
        self.assertEqual(
            spot.official_definition["binance_economic_order_grid"]["size_increment"],
            Decimal("0.000001"),
        )
        self.assertEqual(str(perp_native.price_increment), "0.01000000")
        self.assertEqual(str(perp_native.size_increment), "0.001")

        spot_native_bars = to_nautilus_execution_bars(spot_bars(), spot)
        perp_native_bars = to_nautilus_execution_bars(perp_execution_bars(), perp)
        marks = to_nautilus_mark_updates(perp_mark_bars(), perp)
        self.assertTrue(all(item.open.precision == 2 and item.volume.precision == 6 for item in spot_native_bars))
        self.assertTrue(all(item.open.precision == 8 and item.volume.precision == 3 for item in perp_native_bars))
        self.assertTrue(all(item.value.precision == 8 for item in marks))
        self.assertEqual(
            [item.open.as_decimal() for item in spot_native_bars],
            [item.open for item in spot_bars()],
        )
        self.assertEqual(
            [item.value.as_decimal() for item in marks],
            [item.close for item in perp_mark_bars()],
        )

    def test_zero_padding_is_lossless_and_rounding_is_rejected(self) -> None:
        self.assertEqual(lossless_runtime_quantity_text("0.10000", 6), "0.100000")
        self.assertEqual(Decimal(lossless_runtime_quantity_text("0.10000", 6)), Decimal("0.1"))
        with self.assertRaises(DataContractError):
            lossless_runtime_quantity_text("0.1000001", 6)

    def test_representation_precision_does_not_weaken_order_grid(self) -> None:
        spot = to_nautilus_instrument(repaired_spot())
        perp = to_nautilus_instrument(repaired_perp())
        validate_market_order_quantity(spot, Quantity.from_str("0.100000"))
        validate_market_order_quantity(perp, Quantity.from_str("0.100"))

        for invalid in (
            Quantity.from_str("0.000001"),
            Quantity.from_str("115.630200"),
        ):
            with self.assertRaises(DataContractError):
                validate_market_order_quantity(spot, invalid)
        # Binance's official before/after table proves the historical step was
        # exactly 1e-6. Therefore every positive precision-6 quantity is on the
        # increment grid; an alleged precision-6/non-multiple control is
        # mathematically impossible. A finer value is rejected losslessly.
        with self.assertRaises(DataContractError):
            validate_market_order_quantity(spot, Quantity.from_str("0.1000001"))
        with self.assertRaises(DataContractError):
            validate_market_order_quantity(perp, Quantity.from_str("120.001"))
        with self.assertRaises(DataContractError):
            validate_limit_order_price(perp, Price.from_str("50000.00000001"))
        with self.assertRaises(DataContractError):
            validate_limit_order_price(spot, Price.from_str("50000.001"))

    def test_explicit_boundary_pair_maps_one_source_event_to_two_updates(self) -> None:
        event = FundingEvent(
            instrument_id="BTCUSDT-PERP.BINANCE",
            calc_time_ns=BOUNDARY,
            funding_interval_hours=8,
            funding_rate=Decimal("0.0001"),
            source_row_number=1,
            source_row_sha256="5" * 64,
            event_key="6" * 64,
        )
        updates = to_nautilus_funding_updates(
            (event,),
            repaired_perp(),
            native_binding=FUNDING_NATIVE_BINDING_RC2_EXPLICIT_BOUNDARY_PAIR,
        )
        self.assertEqual(len(updates), 2)
        self.assertEqual({item.next_funding_ns for item in updates}, {BOUNDARY})
        self.assertEqual({int(item.ts_event) for item in updates}, {BOUNDARY})


def funding_case(*, signed_qty: str | None = "1", mark_ts: int = BOUNDARY - 3_000_000):
    rate = Decimal("0.0001")
    mark = Decimal("50000.00000000")
    source = [{
        "event_key": "7" * 64,
        "calc_time_ns": BOUNDARY,
        "funding_interval_hours": 8,
        "funding_rate": str(rate),
    }]
    positions = [] if signed_qty is None else [{"signed_qty": signed_qty}]
    expected = None if signed_qty is None else (
        -Decimal(signed_qty) * mark * rate
    ).quantize(Decimal("0.00000001"))
    native = [] if expected is None else [{
        "adjustment_type": "FUNDING",
        "ts_event": BOUNDARY,
        "pnl_change": f"{expected} USDT",
        "reason": "funding_settlement:event",
    }]
    checkpoint = [{
        "boundary_ns": BOUNDARY,
        "source_event_key": "7" * 64,
        "runtime_updates_at_boundary": [
            {
                "rate": str(rate),
                "interval": 480,
                "next_funding_ns": BOUNDARY,
                "ts_event": BOUNDARY,
                "ts_init": BOUNDARY,
            },
            {
                "rate": str(rate),
                "interval": 480,
                "next_funding_ns": BOUNDARY,
                "ts_event": BOUNDARY,
                "ts_init": BOUNDARY,
            },
        ],
        "native_mark_price": {
            "instrument_id": "BTCUSDT-PERP.BINANCE",
            "value": str(mark),
            "ts_event": mark_ts,
            "ts_init": mark_ts,
        },
        "native_mark_age_ns": BOUNDARY - mark_ts,
        "mark_selection": "LATEST_CAUSAL_AT_OR_BEFORE_FUNDING_TIMESTAMP",
        "open_positions": positions,
        "eligible_position_capture": "IMMEDIATELY_BEFORE_FUNDING_BOUNDARY",
        "positions_after_boundary": positions,
        "native_adjustments": native,
        "account_events_at_boundary": ([{"type": "AccountState"}] if expected is not None else []),
    }]
    rows = [] if expected is None else [{
        "ts_event": str(BOUNDARY),
        "pnl_change": f"{expected} USDT",
    }]
    contract = {
        "funding_native_binding": FUNDING_NATIVE_BINDING_RC2_EXPLICIT_BOUNDARY_PAIR,
        "funding_source_event_count": 1,
        "funding_runtime_update_count": 2,
    }
    return source, checkpoint, rows, contract


class FundingCheckerRepairTests(unittest.TestCase):
    def validate(self, case):
        return validate_owner_smoke_funding_binding(
            source_events=case[0],
            checkpoints=case[1],
            funding_rows=case[2],
            dataset_contract=case[3],
            instrument_id="BTCUSDT-PERP.BINANCE",
        )

    def test_millisecond_offset_exact_boundary_long_short_and_no_position(self) -> None:
        for case in (
            funding_case(signed_qty="1"),
            funding_case(signed_qty="-1"),
            funding_case(signed_qty=None),
            funding_case(signed_qty="1", mark_ts=BOUNDARY),
        ):
            valid, failures, detail = self.validate(case)
            self.assertTrue(valid, (failures, detail))
        self.assertEqual(self.validate(funding_case(signed_qty=None))[2]["native_settlement_count"], 0)

    def test_duplicate_source_pair_and_genuine_second_settlement_fail(self) -> None:
        duplicate_source = funding_case()
        duplicate_source[0].append(copy.deepcopy(duplicate_source[0][0]))
        self.assertIn(FailureCode.FUNDING_AMBIGUOUS.value, self.validate(duplicate_source)[1])

        duplicate_pair = funding_case()
        duplicate_pair[1][0]["runtime_updates_at_boundary"].append(
            copy.deepcopy(duplicate_pair[1][0]["runtime_updates_at_boundary"][0]),
        )
        duplicate_pair[3]["funding_runtime_update_count"] = 3
        self.assertIn(FailureCode.FUNDING_AMBIGUOUS.value, self.validate(duplicate_pair)[1])

        second_settlement = funding_case()
        second_settlement[1][0]["native_adjustments"].append(
            copy.deepcopy(second_settlement[1][0]["native_adjustments"][0]),
        )
        self.assertIn(FailureCode.FUNDING_DOUBLE_COUNT.value, self.validate(second_settlement)[1])

    def test_missing_future_and_stale_mark_fail_closed(self) -> None:
        missing = funding_case()
        missing[1][0]["native_mark_price"] = None
        self.assertIn(FailureCode.MARK_ROLE_INVALID.value, self.validate(missing)[1])

        future = funding_case(mark_ts=BOUNDARY + 1)
        self.assertIn(FailureCode.MARK_ROLE_INVALID.value, self.validate(future)[1])

        stale = funding_case(mark_ts=BOUNDARY - 60_000_000_001)
        self.assertIn(FailureCode.MARK_ROLE_INVALID.value, self.validate(stale)[1])

    def test_position_opened_after_boundary_is_a_no_position_boundary(self) -> None:
        case = funding_case(signed_qty=None)
        case[1][0]["positions_after_boundary"] = [{"signed_qty": "1"}]
        valid, failures, detail = self.validate(case)
        self.assertTrue(valid, failures)
        self.assertEqual(detail["no_position_boundaries"], 1)
        self.assertEqual(detail["native_settlement_count"], 0)


class HistoricalCheckerRegressionTests(unittest.TestCase):
    def test_raw_rehash_accepts_only_the_added_official_binance_cms_host(self) -> None:
        path = ROOT / "scripts/validate_free_official_raw_objects.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assignment = next(
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "ALLOWED_HOSTS" for target in node.targets)
        )
        allowed_hosts = ast.literal_eval(assignment.value)
        self.assertIn("www.binance.com", allowed_hosts)
        self.assertNotIn("binance.com", allowed_hosts)

    def test_old_spot_false_pass_is_now_check_fail_without_evidence_mutation(self) -> None:
        run = ROOT / "runs/owner-smoke-002-spot-run-retry-001-8a09aee98d9f"
        report = check_evidence_directory(
            run,
            repository_root=ROOT,
            source_revision_current_head_required=False,
        )
        self.assertEqual(report.outcome, CheckerOutcome.CHECK_FAIL)
        self.assertIn(FailureCode.CAUSAL_EXECUTION_UNRESOLVED.value, report.failure_codes)
        check = next(item for item in report.checks if item["name"] == "orders_reach_executable_market_state")
        self.assertEqual(check["no_market_rejection_count"], 89)

    def test_sparse_spot_acceptance_does_not_require_a_bar_for_no_trade_minutes(self) -> None:
        run = (
            ROOT
            / "runs/owner-smoke-002-replacement-001-spot-run-retry-001-abbedb975f37"
        )
        report = check_evidence_directory(
            run,
            repository_root=ROOT,
            source_revision_current_head_required=False,
        )
        self.assertNotIn(
            FailureCode.INSTRUMENT_METADATA_INVALID.value,
            report.failure_codes,
        )
        check = next(
            item
            for item in report.checks
            if item["name"] == "nautilus_executable_market_state_acceptance"
        )
        self.assertTrue(check["pass"])
        self.assertEqual(check["validation"]["expected_executable_bars"], 304596)
        self.assertEqual(check["validation"]["accepted_executable_bars"], 304596)


class ReplacementWorkflowInputTests(unittest.TestCase):
    def test_owner_workflow_pins_official_child_runtime_environment(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"TZ": "Europe/Berlin", "LANG": "en_US.UTF-8", "LC_ALL": "C"},
        ):
            environment = _official_child_environment()
        self.assertEqual(environment["TZ"], "UTC")
        self.assertEqual(environment["LANG"], "C.UTF-8")
        self.assertEqual(environment["LC_ALL"], "C.UTF-8")

    def test_replacement_inputs_lock_new_releases_and_supersession(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/generate_owner_smoke_002_replacement_inputs.py"),
                    "--frozen-at-utc",
                    "2026-08-24T00:00:00Z",
                    "--output-dir",
                    str(Path(directory) / "inputs"),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            payloads = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in sorted((Path(directory) / "inputs").glob("*.json"))
            ]
        self.assertEqual(len(payloads), 2)
        self.assertEqual(
            {item["dataset_release_id"] for item in payloads},
            {
                "fd8542c109cfbf7d6b19d5b7bbb7705c6a161efc807695f3671978c381e34eca",
                "b6c8f5d659f3441c924b613d770342796c90b90a970f42a3dc8227c856198917",
            },
        )
        for payload in payloads:
            claim = payload["protocol"]["claim_basis"]
            self.assertIn("SUPERSEDES_FAILED_TRIALS=", claim)
            self.assertIn(
                "SUPERSESSION_REASON=INSTRUMENT_REPRESENTATION_PREVENTED_EXECUTABLE_MARKET_STATE",
                claim,
            )
            self.assertIn("NO_CANONICAL_MARKET_VALUE_CHANGE", claim)
            self.assertEqual(payload["partition_role"], "DEVELOPMENT")
            self.assertEqual(payload["scoring_start"], "2021-02-01T00:00:00Z")
            self.assertEqual(payload["scoring_end_exclusive"], "2021-08-01T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
