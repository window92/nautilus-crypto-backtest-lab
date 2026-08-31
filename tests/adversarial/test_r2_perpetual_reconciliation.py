from __future__ import annotations

import copy
import csv
import json
import unittest
from decimal import Decimal
from pathlib import Path

from crypto_lab.hashing import canonical_sha256
from crypto_lab.native_positions import NATIVE_COMPLETED_DIRECT_SEQUENCE_SOURCE
from crypto_lab.native_positions import NativeCommission
from crypto_lab.native_positions import NativeCompletedPositionUnit
from crypto_lab.native_positions import NativeCompletedPositionSequence
from crypto_lab.perpetual_reconciliation import replay_perpetual_valuation_states
from crypto_lab.perpetual_reconciliation import validate_perpetual_native_account_projection
from crypto_lab.perpetual_reconciliation import validate_perpetual_reconciliation


class PerpetualReconciliationAdversarialTests(unittest.TestCase):
    """Known-result controls which do not use product accounting helpers."""

    def setUp(self) -> None:
        self.fills = [
            self._fill(0, 1, "BUY", "2.000", "100.00000000", "0.20000000"),
            self._fill(1, 2, "BUY", "1.000", "110.00000000", "0.11000000"),
            self._fill(2, 4, "SELL", "1.000", "120.00000000", "0.12000000"),
        ]
        self.funding = [
            {
                "adjustment_type": "FUNDING",
                "instrument_id": "BTCUSDT-PERP.BINANCE",
                "pnl_change": "-0.50000000 USDT",
                "quantity_change": "",
                "reason": "funding_settlement:fixture-1",
                "ts_event": "3",
            },
        ]
        self.positions = [
            self._position(
                0,
                1,
                "2.000",
                "100.00000000",
                "-0.20000000 USDT",
                row_type="PositionOpened",
            ),
            self._position(
                1,
                2,
                "3.000",
                "103.3333333333333333333333333",
                "-0.31000000 USDT",
            ),
            self._position(
                2,
                4,
                "2.000",
                "103.3333333333333333333333333",
                "15.73666667 USDT",
            ),
            {
                "row_type": "FINAL_NATIVE_POSITION",
                "event_index": "0",
                "ts_event": "4",
                "instrument_id": "BTCUSDT-PERP.BINANCE",
                "position_id": "position-1",
                "side": "LONG",
                "signed_qty": "2.000",
                "quantity": "2.000",
                "avg_px_open": "103.3333333333333333333333333",
                "realized_pnl": "15.73666667 USDT",
            },
        ]
        self.accounts = [
            self._account(0, 0, "1000.00000000", "0.00000000"),
            self._account(1, 1, "999.80000000", "5.00000000"),
            self._account(2, 2, "999.69000000", "7.75000000"),
            self._account(3, 3, "999.19000000", "7.75000000"),
            self._account(4, 4, "1015.73666667", "5.16666667"),
        ]
        self.native_completed = self._native_empty(terminal_open=1)
        self.terminal = {
            "realized_pnl": "15.73666667 USDT",
            "unrealized_pnl": "53.33333333 USDT",
            "total_pnl": "69.07000000 USDT",
            "equity": {"USDT": {"amount": "1069.07000000", "currency": "USDT"}},
            "source": "nautilus_trader.portfolio.Portfolio public API",
        }
        self.mark = {
            "instrument_id": "BTCUSDT-PERP.BINANCE",
            "value": "130.00000000",
            "ts_event": 10,
            "ts_init": 10,
        }

    @staticmethod
    def _fill(
        index: int,
        timestamp: int,
        side: str,
        quantity: str,
        price: str,
        commission: str,
    ) -> dict[str, str]:
        return {
            "fill_index": str(index),
            "event_id": f"fill-{index}",
            "client_order_id": f"order-{index}",
            "venue_order_id": f"venue-{index}",
            "trade_id": f"trade-{index}",
            "position_id": "position-1",
            "account_id": "BINANCE-001",
            "instrument_id": "BTCUSDT-PERP.BINANCE",
            "order_side": side,
            "order_type": "MARKET",
            "last_qty": quantity,
            "last_px": price,
            "commission": f"{commission} USDT",
            "currency": "USDT",
            "liquidity_side": "TAKER",
            "ts_event": str(timestamp),
            "ts_init": str(timestamp),
        }

    @staticmethod
    def _position(
        index: int,
        timestamp: int,
        signed: str,
        average: str,
        realized: str,
        *,
        row_type: str = "PositionChanged",
    ) -> dict[str, str]:
        return {
            "row_type": row_type,
            "event_index": str(index),
            "ts_event": str(timestamp),
            "instrument_id": "BTCUSDT-PERP.BINANCE",
            "position_id": "position-1",
            "side": "LONG" if Decimal(signed) > 0 else "SHORT" if Decimal(signed) < 0 else "FLAT",
            "signed_qty": signed,
            "quantity": str(abs(Decimal(signed))),
            "avg_px_open": average,
            "realized_pnl": realized,
        }

    @staticmethod
    def _account(
        index: int,
        timestamp: int,
        total: str,
        locked: str,
    ) -> dict[str, str]:
        free = Decimal(total) - Decimal(locked)
        return {
            "event_index": str(index),
            "ts_event": str(timestamp),
            "account_id": "BINANCE-001",
            "account_type": "MARGIN",
            "currency": "USDT",
            "total": total,
            "locked": locked,
            "free": f"{free:.8f}",
            "reported": "True" if index == 0 else "False",
        }

    @staticmethod
    def _native_empty(*, terminal_open: int = 0) -> dict[str, object]:
        return NativeCompletedPositionSequence.create(
            semantic_sequence_sha256=canonical_sha256(()),
            source=NATIVE_COMPLETED_DIRECT_SEQUENCE_SOURCE,
            source_run_id="perp-reconciliation-fixture",
            instrument_id="BTCUSDT-PERP.BINANCE",
            settlement_currency="USDT",
            status="AVAILABLE",
            completed_trade_count=0,
            terminal_open_position_count=terminal_open,
            terminal_closed_position_count=0,
            units=(),
            net_outcomes=(),
            realized_returns=(),
            unambiguous_net_after_cost=True,
            project_trade_pairing_used=False,
        ).to_builtins()

    @staticmethod
    def _closed_payload() -> dict[str, object]:
        events = [
            {
                **{
                    key: value
                    for key, value in PerpetualReconciliationAdversarialTests._fill(
                        index,
                        timestamp,
                        side,
                        "2.000",
                        price,
                        commission,
                    ).items()
                    if key != "fill_index"
                },
                "type": "OrderFilled",
            }
            for index, timestamp, side, price, commission in (
                (0, 1, "BUY", "100.00000000", "0.20000000"),
                (1, 2, "SELL", "120.00000000", "0.24000000"),
            )
        ]
        return {
            "type": "Position",
            "events": events,
            "adjustments": [],
            "side": "FLAT",
            "entry": "BUY",
            "instrument_id": "BTCUSDT-PERP.BINANCE",
            "position_id": "position-1",
            "strategy_id": "strategy-1",
            "trader_id": "trader-1",
            "account_id": "BINANCE-001",
            "ts_opened": 1,
            "ts_closed": 2,
            "ts_init": 1,
            "ts_last": 2,
            "duration_ns": 1,
            "opening_order_id": "order-0",
            "closing_order_id": "order-1",
            "base_currency": "BTC",
            "quote_currency": "USDT",
            "settlement_currency": "USDT",
            "is_inverse": False,
            "multiplier": "1",
            "price_precision": 8,
            "size_precision": 3,
            "quantity": "0.000",
            "signed_qty": "0.000",
            "peak_qty": "2.000",
            "buy_qty": "2.000",
            "sell_qty": "2.000",
            "avg_px_open": "100.00000000",
            "avg_px_close": "120.00000000",
            "realized_return": "0.1978",
            "realized_pnl": "39.56000000 USDT",
            "commissions": ["0.44000000 USDT"],
            "trade_ids": ["trade-0", "trade-1"],
            "venue_order_ids": ["venue-0", "venue-1"],
        }

    @staticmethod
    def _closed_native(
        *,
        average_open: Decimal = Decimal("100"),
        native_payload: dict[str, object] | None = None,
        commissions: tuple[NativeCommission, ...] = (
            NativeCommission(amount=Decimal("0.44000000"), currency="USDT"),
        ),
    ) -> dict[str, object]:
        unit = NativeCompletedPositionUnit(
            sequence_index=0,
            source_kind="DIRECT_POSITION_CLOSED_SNAPSHOT",
            source_run_id="perp-reconciliation-fixture",
            native_position_id="position-1",
            parent_position_id="position-1",
            native_payload_sha256=canonical_sha256(
                PerpetualReconciliationAdversarialTests._closed_payload()
                if native_payload is None
                else native_payload,
            ),
            instrument_id="BTCUSDT-PERP.BINANCE",
            entry_side="BUY",
            opened_ns=1,
            closed_ns=2,
            opening_order_id="order-0",
            closing_order_id="order-1",
            average_open_price=average_open,
            average_close_price=Decimal("120"),
            peak_quantity=Decimal("2.000"),
            realized_pnl=Decimal("39.56000000"),
            realized_pnl_currency="USDT",
            realized_return=Decimal("0.1978"),
            commissions=commissions,
            duration_ns=1,
            funding_adjustment_count=0,
            native_net_after_cost_unambiguous=True,
        )
        return NativeCompletedPositionSequence.create(
            semantic_sequence_sha256=canonical_sha256((unit.semantic_payload(),)),
            source=NATIVE_COMPLETED_DIRECT_SEQUENCE_SOURCE,
            source_run_id="perp-reconciliation-fixture",
            instrument_id="BTCUSDT-PERP.BINANCE",
            settlement_currency="USDT",
            status="AVAILABLE",
            completed_trade_count=1,
            terminal_open_position_count=0,
            terminal_closed_position_count=1,
            units=(unit,),
            net_outcomes=(Decimal("39.56000000"),),
            realized_returns=(Decimal("0.1978"),),
            unambiguous_net_after_cost=True,
            project_trade_pairing_used=False,
        ).to_builtins()

    def _run(self, **changes: object):
        values = {
            "fills": copy.deepcopy(self.fills),
            "account_rows": copy.deepcopy(self.accounts),
            "position_rows": copy.deepcopy(self.positions),
            "funding_rows": copy.deepcopy(self.funding),
            "native_completed_trades": copy.deepcopy(self.native_completed),
            "native_closed_position_snapshots": [],
            "terminal_portfolio": copy.deepcopy(self.terminal),
            "terminal_mark": copy.deepcopy(self.mark),
            "run_id": "perp-reconciliation-fixture",
            "instrument_id": "BTCUSDT-PERP.BINANCE",
            "settlement_currency": "USDT",
            "initial_balance": Decimal("1000.00000000"),
            "taker_fee": Decimal("0.001"),
            "quantity_increment": Decimal("0.001"),
            "margin_maint": Decimal("0.025"),
            "multiplier": Decimal("1"),
            "money_quantum": Decimal("0.00000001"),
            "scoring_end_exclusive_ns": 10,
        }
        values.update(changes)
        return validate_perpetual_reconciliation(**values)

    def assertRejected(self, expected: str, **changes: object) -> None:  # noqa: N802
        report = self._run(**changes)
        self.assertFalse(report.passed)
        self.assertIn(expected, report.errors)

    def test_known_result_reconciles_every_financial_layer(self) -> None:
        report = self._run()
        self.assertTrue(report.passed, report.errors)
        self.assertEqual(report.detail["realized_pnl"], "15.73666667")
        self.assertEqual(report.detail["unrealized_pnl"], "53.33333333")
        self.assertEqual(report.detail["ending_equity"], "1069.07000000")

    def test_native_reported_account_pair_is_bound_without_double_counting(self) -> None:
        rows = copy.deepcopy(self.accounts)
        rows[3]["reported"] = "True"
        mirror = copy.deepcopy(rows[3])
        mirror["event_index"] = "4"
        mirror["reported"] = "False"
        rows.insert(4, mirror)
        rows[5]["event_index"] = "5"
        report = self._run(account_rows=rows)
        self.assertTrue(report.passed, report.errors)

        rows[4]["free"] = "998.19000000"
        self.assertRejected("PERP_ACCOUNT_COMPONENT_MISMATCH", account_rows=rows)

    def test_native_account_projection_binds_margin_and_csv_rows(self) -> None:
        native_events = []
        for row in self.accounts:
            locked = Decimal(row["locked"])
            native_events.append(
                {
                    "account_id": row["account_id"],
                    "account_type": "MARGIN",
                    "balances": [
                        {
                            "currency": "USDT",
                            "free": row["free"],
                            "locked": row["locked"],
                            "total": row["total"],
                            "type": "AccountBalance",
                        },
                    ],
                    "base_currency": "None",
                    "info": {},
                    "margins": []
                    if locked == 0
                    else [
                        {
                            "currency": "USDT",
                            "initial": "0.00000000",
                            "instrument_id": "BTCUSDT-PERP.BINANCE",
                            "maintenance": row["locked"],
                            "type": "MarginBalance",
                        },
                    ],
                    "reported": row["reported"] == "True",
                    "ts_event": int(row["ts_event"]),
                    "ts_init": int(row["ts_event"]),
                    "type": "AccountState",
                },
            )
        passed, detail = validate_perpetual_native_account_projection(
            native_account_events=native_events,
            account_rows=self.accounts,
            instrument_id="BTCUSDT-PERP.BINANCE",
            settlement_currency="USDT",
        )
        self.assertTrue(passed, detail)

        native_events[1]["margins"][0]["maintenance"] = "6.00000000"
        passed, detail = validate_perpetual_native_account_projection(
            native_account_events=native_events,
            account_rows=self.accounts,
            instrument_id="BTCUSDT-PERP.BINANCE",
            settlement_currency="USDT",
        )
        self.assertFalse(passed)
        self.assertIn("PERP_NATIVE_ACCOUNT_EVENTS_INVALID", detail["errors"])

        native_events[1]["margins"][0]["maintenance"] = self.accounts[1]["locked"]
        native_events[1]["base_currency"] = "BTC"
        passed, detail = validate_perpetual_native_account_projection(
            native_account_events=native_events,
            account_rows=self.accounts,
            instrument_id="BTCUSDT-PERP.BINANCE",
            settlement_currency="USDT",
        )
        self.assertFalse(passed)
        self.assertIn("PERP_NATIVE_ACCOUNT_EVENTS_INVALID", detail["errors"])

    def test_retained_native_benchmark_account_shape_reconciles_exactly(self) -> None:
        """Exercise the real rc2 reported/unreported funding AccountState pairs."""

        root = Path(__file__).resolve().parents[2]
        run = root / (
            "runs/adversarial-remediation-002-retry-002-perpetual-benchmark-"
            "run-d97648d2fd36"
        )
        result = json.loads((run / "nautilus_result.json").read_text(encoding="utf-8"))
        config = json.loads((run / "lab_run_config.json").read_text(encoding="utf-8"))

        def rows(name: str) -> list[dict[str, str]]:
            with (run / name).open(encoding="utf-8", newline="") as stream:
                return list(csv.DictReader(stream))

        instrument = result["dataset_contract"]["instrument"]
        self.assertEqual(instrument["margin_init"], "0.0500")
        self.assertEqual(instrument["margin_maint"], "0.0250")
        accounts = rows("account.csv")
        self.assertEqual(len(accounts), 1086)
        self.assertEqual(
            sum(row["reported"] == "True" for row in accounts),
            543,
        )
        projection_ok, projection_detail = validate_perpetual_native_account_projection(
            native_account_events=result["semantic_sequence"]["account_events"],
            account_rows=accounts,
            instrument_id="BTCUSDT-PERP.BINANCE",
            settlement_currency="USDT",
        )
        self.assertTrue(projection_ok, projection_detail)

        arguments = {
            "fills": rows("fills.csv"),
            "account_rows": accounts,
            "position_rows": rows("positions.csv"),
            "funding_rows": rows("funding.csv"),
            "native_completed_trades": json.loads(
                (run / "native_completed_trades.json").read_text(encoding="utf-8"),
            ),
            "native_closed_position_snapshots": result["native_closed_position_snapshots"],
            "terminal_portfolio": result["terminal_portfolio"],
            "terminal_mark": {
                "instrument_id": "BTCUSDT-PERP.BINANCE",
                "value": "41445.30000000",
                "ts_event": 1627776000000000000,
                "ts_init": 1627776000000000000,
            },
            "run_id": config["run_id"],
            "instrument_id": config["instrument_id"],
            "settlement_currency": "USDT",
            "initial_balance": Decimal("10000.00000000"),
            "taker_fee": Decimal("0.001"),
            "quantity_increment": Decimal("0.001"),
            "margin_maint": Decimal(instrument["margin_maint"]),
            "multiplier": Decimal(instrument["multiplier"]),
            "money_quantum": Decimal("0.00000001"),
            "scoring_end_exclusive_ns": 1627776000000000000,
        }
        report = validate_perpetual_reconciliation(**arguments)
        self.assertTrue(report.passed, report.errors)
        self.assertEqual(report.detail["reconciled_account_delta_count"], 543)
        self.assertEqual(report.detail["funding_settlement_count"], 542)
        self.assertEqual(report.detail["realized_pnl"], "-2772.75946187")
        self.assertEqual(report.detail["unrealized_pnl"], "2526.78870000")
        self.assertEqual(report.detail["ending_equity"], "9754.02923813")

        wrong_margin_semantics = validate_perpetual_reconciliation(
            **{
                **arguments,
                "margin_maint": Decimal(instrument["margin_init"]),
            },
        )
        self.assertFalse(wrong_margin_semantics.passed)
        self.assertIn(
            "PERP_ACCOUNT_MARGIN_STATE_MISMATCH",
            wrong_margin_semantics.errors,
        )

    def test_no_fill_no_position_run_reconciles_without_inventing_state(self) -> None:
        terminal = {
            "realized_pnl": "0.00000000 USDT",
            "unrealized_pnl": "0.00000000 USDT",
            "total_pnl": "0.00000000 USDT",
            "equity": {"USDT": {"amount": "1000.00000000", "currency": "USDT"}},
            "source": "nautilus_trader.portfolio.Portfolio public API",
        }
        report = self._run(
            fills=[],
            account_rows=[self._account(0, 0, "1000.00000000", "0.00000000")],
            position_rows=[],
            funding_rows=[],
            native_completed_trades=self._native_empty(),
            terminal_portfolio=terminal,
        )
        self.assertTrue(report.passed, report.errors)
        self.assertEqual(report.detail["fill_count"], 0)
        self.assertEqual(report.detail["terminal_signed_position"], "0")

    def test_account_plus_10000_is_rejected(self) -> None:
        rows = copy.deepcopy(self.accounts)
        rows[-1]["total"] = rows[-1]["free"] = "11015.73666667"
        self.assertRejected("PERP_ACCOUNT_DELTA_MISMATCH", account_rows=rows)

    def test_final_position_tamper_is_rejected(self) -> None:
        rows = copy.deepcopy(self.positions)
        rows[-1]["signed_qty"] = rows[-1]["quantity"] = "9.000"
        self.assertRejected("PERP_FINAL_POSITION_MISMATCH", position_rows=rows)

    def test_locked_free_margin_tamper_is_rejected(self) -> None:
        rows = copy.deepcopy(self.accounts)
        for row in rows[1:]:
            row["locked"] = "100.00000000"
            row["free"] = f"{Decimal(row['total']) - Decimal('100'):.8f}"
        self.assertRejected("PERP_ACCOUNT_MARGIN_STATE_MISMATCH", account_rows=rows)

    def test_position_snapshot_fields_type_and_side_are_mandatory(self) -> None:
        rows = copy.deepcopy(self.positions)
        rows[0]["avg_px_open"] = ""
        self.assertRejected("PERP_POSITION_AVERAGE_ENTRY_MISMATCH", position_rows=rows)
        rows = copy.deepcopy(self.positions)
        rows[1]["realized_pnl"] = ""
        self.assertRejected("PERP_POSITION_REALIZED_PNL_MISMATCH", position_rows=rows)
        rows = copy.deepcopy(self.positions)
        rows[0]["row_type"] = "PositionChanged"
        self.assertRejected("PERP_FILL_POSITION_DELTA_MISMATCH", position_rows=rows)
        rows = copy.deepcopy(self.positions)
        rows[0]["side"] = "SHORT"
        self.assertRejected("PERP_FILL_POSITION_DELTA_MISMATCH", position_rows=rows)

    def test_native_completed_schema_and_run_identity_are_strict(self) -> None:
        native = copy.deepcopy(self.native_completed)
        native["source_run_id"] = "different-run"
        self.assertRejected("PERP_NATIVE_COMPLETED_SEQUENCE_INVALID", native_completed_trades=native)
        native = copy.deepcopy(self.native_completed)
        native["unexpected"] = "field"
        self.assertRejected("PERP_NATIVE_COMPLETED_SEQUENCE_INVALID", native_completed_trades=native)

    def test_completed_lifecycle_native_values_and_extra_currency_are_bound(self) -> None:
        fills = [
            self._fill(0, 1, "BUY", "2.000", "100.00000000", "0.20000000"),
            self._fill(1, 2, "SELL", "2.000", "120.00000000", "0.24000000"),
        ]
        positions = [
            self._position(
                0,
                1,
                "2.000",
                "100.00000000",
                "-0.20000000 USDT",
                row_type="PositionOpened",
            ),
            self._position(
                1,
                2,
                "0.000",
                "100.00000000",
                "39.56000000 USDT",
                row_type="PositionClosed",
            ),
            {
                "row_type": "FINAL_NATIVE_POSITION",
                "event_index": "0",
                "ts_event": "2",
                "instrument_id": "BTCUSDT-PERP.BINANCE",
                "position_id": "position-1",
                "side": "FLAT",
                "signed_qty": "0.000",
                "quantity": "0.000",
                "avg_px_open": "100.00000000",
                "realized_pnl": "39.56000000 USDT",
            },
        ]
        accounts = [
            self._account(0, 0, "1000.00000000", "0.00000000"),
            self._account(1, 1, "999.80000000", "5.00000000"),
            self._account(2, 2, "1039.56000000", "0.00000000"),
        ]
        terminal = {
            "realized_pnl": "39.56000000 USDT",
            "unrealized_pnl": "0.00000000 USDT",
            "total_pnl": "39.56000000 USDT",
            "equity": {"USDT": {"amount": "1039.56000000", "currency": "USDT"}},
            "source": "nautilus_trader.portfolio.Portfolio public API",
        }
        common = {
            "fills": fills,
            "position_rows": positions,
            "account_rows": accounts,
            "funding_rows": [],
            "native_completed_trades": self._closed_native(),
            "native_closed_position_snapshots": [self._closed_payload()],
            "terminal_portfolio": terminal,
        }
        report = self._run(**common)
        self.assertTrue(report.passed, report.errors)

        wrong_average = {**common, "native_completed_trades": self._closed_native(average_open=Decimal("101"))}
        self.assertFalse(self._run(**wrong_average).passed)
        self.assertIn(
            "PERP_NATIVE_COMPLETED_SEQUENCE_MISMATCH",
            self._run(**wrong_average).errors,
        )

        extra_currency = self._closed_native(
            commissions=(
                NativeCommission(amount=Decimal("0.44000000"), currency="USDT"),
                NativeCommission(amount=Decimal("0.01000000"), currency="BTC"),
            ),
        )
        report = self._run(**{**common, "native_completed_trades": extra_currency})
        self.assertFalse(report.passed)
        self.assertIn("PERP_NATIVE_COMPLETED_UNIT_INVALID", report.errors)

        for name, mutate in (
            ("extra-field", lambda payload: payload.update({"unexpected": "forged"})),
            (
                "fill-quantity",
                lambda payload: payload["events"][0].update({"last_qty": "9.000"}),
            ),
            (
                "raw-commission-currency",
                lambda payload: payload.update({"commissions": ["0.44000000 BTC"]}),
            ),
        ):
            with self.subTest(raw_snapshot_tamper=name):
                tampered_payload = copy.deepcopy(self._closed_payload())
                mutate(tampered_payload)
                report = self._run(
                    **{
                        **common,
                        "native_closed_position_snapshots": [tampered_payload],
                        # Rebuild the typed sequence and all of its identities
                        # around the forged raw hash.  Schema/hash consistency
                        # must not substitute for native financial semantics.
                        "native_completed_trades": self._closed_native(
                            native_payload=tampered_payload,
                        ),
                    },
                )
                self.assertFalse(report.passed)
                self.assertIn("PERP_NATIVE_COMPLETED_UNIT_INVALID", report.errors)

        states = replay_perpetual_valuation_states(
            fills=fills,
            funding_rows=[],
            valuation_marks=[
                {
                    "instrument_id": "BTCUSDT-PERP.BINANCE",
                    "value": "110.00000000",
                    "ts_event": 1,
                    "ts_init": 1,
                },
                {
                    "instrument_id": "BTCUSDT-PERP.BINANCE",
                    "value": "130.00000000",
                    "ts_event": 2,
                    "ts_init": 2,
                },
            ],
            instrument_id="BTCUSDT-PERP.BINANCE",
            settlement_currency="USDT",
            initial_balance=Decimal("1000"),
            taker_fee=Decimal("0.001"),
            quantity_increment=Decimal("0.001"),
            money_quantum=Decimal("0.00000001"),
        )
        self.assertEqual(states[0].equity, Decimal("1019.80000000"))
        self.assertEqual(states[1].equity, Decimal("1039.56000000"))

    def test_commission_currency_and_amount_are_rejected(self) -> None:
        rows = copy.deepcopy(self.fills)
        rows[0]["commission"] = "0.20000000 BTC"
        self.assertRejected("PERP_FILL_ROW_INVALID", fills=rows)
        rows = copy.deepcopy(self.fills)
        rows[0]["commission"] = "0.10000000 USDT"
        self.assertRejected("PERP_COMMISSION_AMOUNT_MISMATCH", fills=rows)

    def test_realized_unrealized_and_terminal_mark_tamper_are_rejected(self) -> None:
        terminal = copy.deepcopy(self.terminal)
        terminal["realized_pnl"] = "16.73666667 USDT"
        self.assertRejected("PERP_REPORTED_REALIZED_PNL_MISMATCH", terminal_portfolio=terminal)
        terminal = copy.deepcopy(self.terminal)
        terminal["unrealized_pnl"] = "54.33333333 USDT"
        self.assertRejected("PERP_REPORTED_UNREALIZED_PNL_MISMATCH", terminal_portfolio=terminal)
        mark = copy.deepcopy(self.mark)
        mark["ts_event"] = mark["ts_init"] = 11
        self.assertRejected("PERP_TERMINAL_MARK_INVALID", terminal_mark=mark)

    def test_deleted_duplicate_fill_and_bad_reversal_are_rejected(self) -> None:
        self.assertRejected("PERP_FILL_POSITION_CARDINALITY_MISMATCH", fills=self.fills[:-1])
        duplicate = copy.deepcopy(self.fills)
        duplicate.append(copy.deepcopy(duplicate[-1]))
        duplicate[-1]["fill_index"] = "3"
        self.assertRejected("PERP_FILL_ROW_INVALID", fills=duplicate)
        reversal = copy.deepcopy(self.fills)
        reversal[-1]["last_qty"] = "4.000"
        reversal[-1]["commission"] = "0.48000000 USDT"
        self.assertRejected("PERP_FILL_POSITION_DELTA_MISMATCH", fills=reversal)

    def test_deleted_duplicate_and_wrong_sign_funding_are_rejected(self) -> None:
        self.assertRejected("PERP_ACCOUNT_DELTA_MISMATCH", funding_rows=[])
        duplicate = copy.deepcopy(self.funding) * 2
        self.assertRejected("PERP_ACCOUNT_DELTA_MISMATCH", funding_rows=duplicate)
        wrong = copy.deepcopy(self.funding)
        wrong[0]["pnl_change"] = "0.50000000 USDT"
        self.assertRejected("PERP_ACCOUNT_DELTA_MISMATCH", funding_rows=wrong)

    def test_unbound_account_delta_is_rejected(self) -> None:
        rows = copy.deepcopy(self.accounts)
        rows.insert(4, self._account(4, 3, "1000.19000000", "7.75000000"))
        rows[-1]["event_index"] = "5"
        self.assertRejected("PERP_ACCOUNT_DELTA_MISMATCH", account_rows=rows)


if __name__ == "__main__":
    unittest.main()
