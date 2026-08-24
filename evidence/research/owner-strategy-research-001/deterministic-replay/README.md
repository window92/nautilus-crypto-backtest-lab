# Deterministic Replay — OWNER_STRATEGY_RESEARCH_001

كل replay شُغلت في process جديد وداخل offline boundary. اختلاف Run paths/IDs غير مادي؛ تطابقت signals/orders/Fills/positions/account events/fees/funding/equity/terminal state دلاليًا.

| Trial | Result | Replay identity | Semantic digest |
|---|---|---|---|
| spot_benchmark | PASS | `b491ddef5edde0dadd25a57cabba3b649c1dbbed6526df716561088b49395ac6` | `9e77fd3563e269a16479bd340cb720b590933a7c5a70b836120a2340e4931b34` |
| spot_candidate_a | PASS | `3e641dd958d65bcec16581d4ae23d5dd8de67dcb2cefd1f6d71a0a797179fe26` | `17021d2178e4f783dbdebc54902fb4fc8250f378eb3bb16f296bafba9bbc9259` |
| spot_candidate_b | PASS | `d0dd6a1d06d0da8e5163a4abaa684489d4cc9969138903ceb3cfc5a12972748d` | `60e481a2bffa5587cbc8601f42b6a00e6651b095230fbaa1058d2384dee08b75` |
| perpetual_benchmark | PASS | `a0a53f01ec34a0316d28b41570fd08e4e1704ed9b74a98672c2feffa3ba2a1b3` | `0b3fe471c3a6dd54f93cec7953b726d2a9c1e98b5e85a390269e5f8aa4518882` |
| perpetual_candidate_a | PASS | `e613b0db5f83b44fdbcf3806b28f7e93d04316bdbd79d00d6d53191b10b3cbac` | `e96c436955c217632d8174afc460c46fcaf3e77fe59a35a9f6335e457764e623` |
| perpetual_candidate_b | PASS | `dbd8486bfdabc4a7df596edbc7d05c68d1dccb1bbfedb76f459d31b4403ccc26` | `c4fc1f569c831b7c8786e599c1106645015cf670a7c75b97d86d8716665dc142` |
