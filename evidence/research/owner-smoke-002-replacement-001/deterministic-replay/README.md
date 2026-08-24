# Deterministic Replay — OWNER_SMOKE_002 Replacement 001

> **هذه ليست توصية تداول، وليست Final Holdout، ولا تسمح بأي Profitability Claim.**

الغرض `EXPLORATORY_OPERATIONAL_VALIDATION` فقط، والنافذة `[2021-01-01T00:00:00Z, 2021-08-01T00:00:00Z)` مع scoring `[2021-02-01T00:00:00Z, 2021-08-01T00:00:00Z)` مصنفة `DATA_QUALITY_INSPECTED_NOT_FINAL_HOLDOUT`.


- Spot: primary/replay semantic digest `41b67c450db299e8ad9045eaa5a73a52ce4512f2f0bc6fb58f608442ceceb733`؛ replay identity `60a312df85e5bba027306db63ddb007e51f48996fabb168f06cd6209827a6387`؛ `PASS`.
- Perpetual: primary/replay semantic digest `8b37dd36555b178c5aff04867b229d478435991f0ab9aea7a28b242ff6c8770b`؛ replay identity `c02f6b6f0c304dbb6eed9891f43c92c371f40989d3219f6e53b2411e481f4f3a`؛ `PASS`.
- كل replay شُغلت في process جديد، وأعيد checker read-only، وتطابقت signals/orders/fills/positions/accounts/fees/funding/equity/terminal state دلاليًا.
- اختلاف paths أوcapture timestamps غير داخل الهوية الدلالية.
