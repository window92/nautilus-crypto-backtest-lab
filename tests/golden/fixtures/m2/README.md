# M2 official-row qualification fixtures

These byte fixtures are deliberately tiny extracts from the exact official
Binance Public Data archives recorded by
`evidence/m2/m2-acceptance-001/raw-object-inventory.json`.

- `spot-pre-transition.csv`: final two BTCUSDT Spot one-minute rows of
  2024-12-31 (official millisecond timestamp contract).
- `spot-post-transition.csv`: first two BTCUSDT Spot one-minute rows of
  2025-01-01 (official microsecond timestamp contract).
- `usdm-execution.csv`: header and first four BTCUSDT USD-M contract-price
  one-minute rows of 2025-01-01.
- `usdm-mark.csv`: header and first four BTCUSDT USD-M mark-price one-minute
  rows of 2025-01-01.
- `usdm-funding.csv`: header and first four BTCUSDT January 2025 official
  funding records. The interval is evidence read from each row; it is not a
  project default.

The extracts are test inputs, not provenance authority. The frozen raw archive
hashes and publisher checksums remain the authority.
