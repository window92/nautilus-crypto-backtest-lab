# تقرير DuckDB وNautilus Catalog

## التخزين والتحقق

- DuckDB: `1.4.5` في `.data-venv` مستقلة؛ wheel SHA-256 `aa294d028c149ca21110e366eaffcb4fc9ab11d7d203d50f7bc49a07ab34b960`؛ offline reinstall و`pip check` نجحا.
- Primary: `data/duckdb/free-official-binance-data-duckdb-001/primary-v4.duckdb`، `1757949952` byte، SHA-256 `965dea1cfb4dadf189448e1cb34a58dedbba111727d075fd633a6d41c4400fc6`.
- Independent: `data/duckdb/free-official-binance-data-duckdb-001/independent-v4.duckdb`، `1758736384` byte، SHA-256 `3403b25612115a4f431847eaa1be738451a7a1eebc60ec954650d006b0154cf0`.
- semantic database identity المشتركة: `c363294d7c00373904c970beddb25c87f5e68d53178510e03312f4423da3914d`.
- schema identity: `3ca976671f80d429da1ec30db348968999b04deb4dca87e60ed9650a9fc05cee`؛ عدد الجداول `18`؛ financial FLOAT/DOUBLE columns = `0`.
- physical hashes مختلفة كما هو مسموح، لكن schema والصفوف المرتبة وper-table hashes وconflicts/dispositions وrelease IDs وcatalog inventories متطابقة exact.
- القاعدتان أُغلقتا، جعلتا read-only، وأُعيد التحقق منهما read-only.

DuckDB طبقة derived validation/storage فقط. raw bytes في content-addressed store هي authority، ولم تُستخدم DuckDB للتنفيذ أوالأوامر أوالحسابات أوPnL.

Nautilus-compatible `ParquetDataCatalog` أعيد بناؤه في مسارين مستقلين ثم قرئ في process منفصل. Spot inventory identity `dde9350bbbb26f53780672a7cd8f3581eed80b991d0f7ff0c6545d84cfbf34a6` وPerpetual inventory identity `e1daf747a1ba51422001da0f346877dd59820aec02af7676b6bae55fa18556f3`، مع semantic equality كاملة وverified-no-trade exported count = `0`.
