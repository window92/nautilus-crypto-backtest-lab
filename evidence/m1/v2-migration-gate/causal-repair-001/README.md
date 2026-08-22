# CAUSAL_FILL_REPAIR_001

This additive qualification repairs the causal-bar probe used by the preceding
`V2_MIGRATION_GATE` epoch. It does not modify or erase that historical evidence.

The previous probe encoded integer-looking OHLC strings (`"100"`, `"101"`, and
so on), which created Nautilus `Price` values with precision `0` for an
Instrument whose required price precision is `2`. Nautilus routed each Bar to
the SimulatedExchange, then the matching engine rejected the Bar at its native
price-precision check before generating synthetic OHLC trade ticks. The later
MARKET BUY therefore reached an uninitialized ask side and emitted `No market`.

The repaired fixture preserves the Appendix A.4 numeric values while encoding
them at valid precision (`"100.00"`, etc.). It uses only public v2 APIs, an
actual `BacktestEngine`, actual `Strategy.on_bar`, external one-minute LAST
Bars, the native standard `DefaultFillModel(1.0, 1.0, 0)`, the native
`StaticLatencyModel`, and actual Nautilus order, matching, Fill, position, and
account paths.

Results:

- G02 causal case: PASS; BUY 1 filled at 200.01 at 120 seconds.
- G03 zero-latency control: PASS as a negative control; BUY 1 filled at 100.01
  at 60 seconds and the no-same-bar checker returned the expected `CHECK_FAIL`.
- Conditional `prob_slippage=0.0` diagnostic: not executed because the locked
  `1.0/1.0/0` Fill Model contract passed.

The earlier capability matrix and blocked summary remain immutable historical
evidence. This repair epoch supersedes only their causal-Fill conclusion.

Verdict: `V2_MIGRATION_GATE_PASS_AFTER_CAUSAL_REPAIR`.
