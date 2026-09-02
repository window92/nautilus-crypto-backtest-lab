from __future__ import annotations

import csv
import json
import subprocess
import unittest
from decimal import Decimal
from pathlib import Path

from crypto_lab.config import LabRunConfig
from crypto_lab.config import SourceRevision
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.m3 import QualifiedProfileRegistry
from crypto_lab.profile_authority import ProfileAuthorityError
from crypto_lab.profile_authority import resolve_profile_authority


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "evidence/m3/m3-acceptance-001"
BOUNDARY_NS = 1_735_718_400_000_000_000


def summary(name: str) -> dict:
    return json.loads((EVIDENCE / "attempt-summaries" / f"{name}.json").read_text())


def run_dir(name: str) -> Path:
    return EVIDENCE / summary(name)["evidence_dir"]


def json_file(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


class M3RealProfileQualifications(unittest.TestCase):
    def test_historical_v1_runs_are_clean_committed_and_offline_diagnostics(self) -> None:
        baseline = json_file(EVIDENCE / "baseline.json")
        self.assertTrue(baseline["clean_worktree"])
        self.assertEqual(baseline["head"], baseline["origin_main"])
        self.assertFalse(baseline["network_used"])
        self.assertFalse(baseline["official_run"])
        for name in ("spot-primary", "spot-replay", "perpetual-primary", "perpetual-replay"):
            directory = run_dir(name)
            source = SourceRevision.from_json_bytes((directory / "source_revision.json").read_bytes())
            config = LabRunConfig.from_json_bytes((directory / "lab_run_config.json").read_bytes())
            native = json_file(directory / "nautilus_result.json")
            self.assertTrue(source.clean_worktree)
            self.assertEqual(source.git_commit, baseline["head"])
            tree = subprocess.run(
                ["git", "rev-parse", f"{source.git_commit}^{{tree}}"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(tree, source.git_tree)
            self.assertEqual(config.run_purpose.value, "QUALIFICATION")
            self.assertEqual(native["network_guard"], {"attempts": [], "enforced": True, "required": True})
            self.assertEqual(native["project_fee_postings"], 0)
            self.assertEqual(native["project_funding_postings"], 0)
            self.assertFalse(native["project_financial_ledger"])

    def test_historical_v1_spot_path_records_causal_cash_and_fee_diagnostics(self) -> None:
        directory = run_dir("spot-primary")
        config = LabRunConfig.from_json_bytes((directory / "lab_run_config.json").read_bytes())
        native = json_file(directory / "nautilus_result.json")
        checker = json_file(directory / "checker.json")
        fills = csv_rows(directory / "fills.csv")
        account = csv_rows(directory / "account.csv")
        self.assertEqual(checker["outcome"], "CHECK_PASS")
        self.assertFalse(checker["outcome"].startswith("COMPONENT_"))
        self.assertEqual(config.nautilus_venue_config.account_type, "CASH")
        self.assertEqual(config.nautilus_venue_config.oms_type, "NETTING")
        self.assertFalse(config.nautilus_venue_config.allow_cash_borrowing)
        self.assertFalse(config.nautilus_engine_config.portfolio.use_mark_prices)
        self.assertEqual(config.mark_binding, "NOT_APPLICABLE")
        self.assertEqual(config.funding_binding, "NOT_APPLICABLE")
        self.assertEqual(len(fills), 1)
        fill = fills[0]
        submitted = native["strategy_observations"]["submitted_intents"][0]
        self.assertGreater(int(fill["ts_event"]), int(submitted["signal_bar_available_at_ns"]))
        self.assertEqual(
            int(fill["ts_event"]),
            int(submitted["signal_timestamp_ns"]) + 60_000_000_000,
        )
        expected_fee = (
            Decimal(fill["last_px"]) * Decimal(fill["last_qty"]) * Decimal("0.001")
        ).quantize(Decimal("0.00000001"))
        self.assertEqual(Decimal(fill["commission"].split()[0]), expected_fee)
        self.assertTrue(all(Decimal(row["free"]) >= 0 for row in account))
        self.assertTrue(native["terminal_position_open"])
        self.assertEqual(native["terminal_policy"], "MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE")
        self.assertTrue(all(int(row["ts_event"]) < 1_735_689_720_000_000_000 for row in fills))

    def test_spot_short_attempt_is_blocked_pre_submit_without_fill(self) -> None:
        control = json_file(EVIDENCE / "negative-controls.json")["SPOT_SHORT"]
        self.assertEqual(control["state"], "BLOCKED")
        self.assertIn("SPOT_SHORT_OR_BORROW_DETECTED", control["guard_failure_codes"])
        self.assertEqual(control["fills_count"], 0)
        self.assertEqual(control["orders_count"], 0)

    def test_historical_v1_perpetual_path_records_netting_and_limit_diagnostics(self) -> None:
        directory = run_dir("perpetual-primary")
        config = LabRunConfig.from_json_bytes((directory / "lab_run_config.json").read_bytes())
        native = json_file(directory / "nautilus_result.json")
        fills = csv_rows(directory / "fills.csv")
        checker = json_file(directory / "checker.json")
        self.assertEqual(checker["outcome"], "CHECK_PASS")
        self.assertFalse(checker["outcome"].startswith("COMPONENT_"))
        self.assertEqual(config.nautilus_venue_config.account_type, "MARGIN")
        self.assertEqual(config.nautilus_venue_config.oms_type, "NETTING")
        self.assertEqual(config.nautilus_venue_config.default_leverage, Decimal("1"))
        self.assertFalse(config.nautilus_venue_config.liquidation_enabled)
        self.assertTrue(config.nautilus_engine_config.portfolio.use_mark_prices)
        instrument = native["dataset_contract"]["instrument"]
        self.assertEqual(instrument["native_class"], "nautilus_trader.model:CryptoPerpetual")
        self.assertEqual(instrument["min_quantity"], "0.001")
        self.assertEqual(instrument["max_quantity"], "120.000")
        self.assertEqual(instrument["size_increment"], "0.001")
        self.assertEqual(len(fills), 4)
        submitted = {
            item["client_order_id"]: item
            for item in native["strategy_observations"]["submitted_intents"]
        }
        for fill in fills:
            intent = submitted[fill["client_order_id"]]
            self.assertGreater(int(fill["ts_event"]), int(intent["signal_bar_available_at_ns"]))
            self.assertEqual(
                int(fill["ts_event"]),
                int(intent["signal_timestamp_ns"]) + 60_000_000_000,
            )
            expected_fee = (
                Decimal(fill["last_px"]) * Decimal(fill["last_qty"]) * Decimal("0.001")
            ).quantize(Decimal("0.00000001"))
            self.assertEqual(Decimal(fill["commission"].split()[0]), expected_fee)
        lifecycle = [
            item["signed_position"]
            for item in native["strategy_observations"]["position_sequence"]
        ]
        self.assertEqual(lifecycle, ["0.004", "0.003", "0", "-0.001"])
        self.assertTrue(all(int(row["ts_event"]) < 1_735_718_640_000_000_000 for row in fills))

    def test_native_funding_sign_boundary_exactly_once_and_mark_binding(self) -> None:
        directory = run_dir("perpetual-primary")
        native = json_file(directory / "nautilus_result.json")
        funding_source = json_file(directory / "funding_source.json")
        funding = csv_rows(directory / "funding.csv")
        account = csv_rows(directory / "account.csv")
        self.assertEqual(len(funding_source["events"]), 1)
        source = funding_source["events"][0]
        self.assertEqual(source["calc_time_ns"], BOUNDARY_NS)
        self.assertEqual(source["funding_interval_hours"], 8)
        self.assertEqual(source["funding_rate"], "0.00010000")
        self.assertEqual(len(funding), 1)
        checkpoint = native["native_funding_checkpoints"][0]
        self.assertEqual(checkpoint["boundary_ns"], BOUNDARY_NS)
        self.assertEqual(checkpoint["open_positions"][0]["signed_qty"], "0.003")
        self.assertEqual(len(checkpoint["native_adjustments"]), 1)
        mark = next(
            Decimal(item["value"])
            for item in native["strategy_observations"]["mark_price_updates"]
            if item["ts_event"] == BOUNDARY_NS
        )
        expected = (-Decimal("0.003") * mark * Decimal("0.00010000")).quantize(
            Decimal("0.00000001"),
        )
        self.assertEqual(expected, Decimal("-0.02809833"))
        self.assertEqual(Decimal(funding[0]["pnl_change"].split()[0]), expected)
        before = max(
            (row for row in account if int(row["ts_event"]) < BOUNDARY_NS),
            key=lambda row: int(row["ts_event"]),
        )
        at_boundary = {Decimal(row["total"]) for row in account if int(row["ts_event"]) == BOUNDARY_NS}
        self.assertEqual(len(at_boundary), 1)
        self.assertEqual(next(iter(at_boundary)) - Decimal(before["total"]), expected)
        self.assertFalse(native["mark_fallback_accepted"])
        self.assertEqual(native["mark_price_count"], 8)
        self.assertEqual(native["dataset_contract"]["funding_source_event_count"], 1)
        self.assertEqual(native["dataset_contract"]["funding_runtime_update_count"], 2)

    def test_perpetual_negative_controls_fail_for_their_intended_reason(self) -> None:
        controls = json_file(EVIDENCE / "negative-controls.json")
        expected = {
            "PERP_DIRECT_CROSS_ZERO": "CROSS_ZERO_ORDER_REJECTED",
            "PERP_CONCURRENT_ORDER": "CONCURRENT_STRATEGY_ORDER_REJECTED",
            "PERP_ABOVE_MARKET_MAX": "INSTRUMENT_METADATA_INVALID",
        }
        for name, code in expected.items():
            self.assertIn(code, controls[name]["guard_failure_codes"])
        self.assertEqual(controls["PERP_ABOVE_MARKET_MAX"]["orders_count"], 0)
        self.assertEqual(controls["PERP_ABOVE_MARKET_MAX"]["fills_count"], 0)
        self.assertIn("MARK_ROLE_INVALID", controls["PROHIBITED_MARK_FALLBACK"]["failure_codes"])
        self.assertEqual(controls["PROHIBITED_MARK_FALLBACK"]["fills_count"], 0)
        self.assertEqual(controls["DUPLICATE_FUNDING_SETTLEMENT"]["checker"]["outcome"], "CHECK_FAIL")
        self.assertFalse(
            controls["DUPLICATE_FUNDING_SETTLEMENT"]["checker"]["outcome"].startswith(
                "COMPONENT_",
            ),
        )
        self.assertIn(
            "FUNDING_DOUBLE_COUNT",
            controls["DUPLICATE_FUNDING_SETTLEMENT"]["checker"]["failure_codes"],
        )
        post = controls["PERP_POST_BOUNDARY_OPEN"]
        self.assertEqual(post["funding_events_count"], 0)
        self.assertEqual(post["fills_count"], 1)
        post_fills = csv_rows(EVIDENCE / post["evidence_dir"] / "fills.csv")
        self.assertEqual(len(post_fills), 1)
        self.assertGreater(int(post_fills[0]["ts_event"]), BOUNDARY_NS)
        network = controls["NETWORK_ATTEMPT"]
        self.assertEqual(network["state"], "BLOCKED")
        self.assertIn("NETWORK_DURING_OFFICIAL_RUN", network["failure_codes"])
        self.assertEqual(network["fills_count"], 0)

    def test_legacy_replays_registry_and_manifests_are_intact_but_not_current_authority(self) -> None:
        replay = json_file(EVIDENCE / "deterministic-replay.json")
        self.assertEqual({item["result"] for item in replay.values()}, {"PASS"})
        self.assertTrue(all(item["fresh_processes"] for item in replay.values()))
        registry = QualifiedProfileRegistry.from_json_bytes(
            (EVIDENCE / "qualified-profile-registry.json").read_bytes(),
        )
        self.assertEqual(registry.schema_version, 1)
        self.assertEqual(len(registry.records), 2)
        for record in registry.records:
            self.assertEqual(record.schema_version, 1)
            self.assertEqual(record.qualification_state.value, "QUALIFIED")
            self.assertEqual(record.checker_result, "CHECK_PASS")
            self.assertEqual(record.replay_result, "PASS")
            self.assertEqual(len(record.accepted_run_ids), 2)
            with self.assertRaises(ProfileAuthorityError):
                resolve_profile_authority(
                    repository_root=ROOT,
                    registry_ref=(EVIDENCE / "qualified-profile-registry.json")
                    .relative_to(ROOT)
                    .as_posix(),
                    registry_sha256=sha256_file(
                        EVIDENCE / "qualified-profile-registry.json",
                    ),
                    qualified_profile_record_id=record.qualified_profile_record_id,
                    expected_profile_id=record.profile_id.value,
                    expected_runtime_lock_sha256=sha256_file(ROOT / "runtime.lock.json"),
                )
        for name in ("spot-primary", "spot-replay", "perpetual-primary", "perpetual-replay"):
            directory = run_dir(name)
            manifest = json_file(directory / "evidence_manifest.json")
            self.assertFalse((directory / "component_validation.json").exists())
            self.assertFalse((directory / "official_seal.json").exists())
            self.assertEqual(canonical_sha256(manifest["entries"]), manifest["inventory_content_sha256"])
            for item in manifest["entries"]:
                path = directory / item["path"]
                self.assertTrue(path.is_file())
                self.assertEqual(sha256_file(path), item["sha256"])
                self.assertEqual(path.stat().st_size, item["byte_size"])


if __name__ == "__main__":
    unittest.main()
