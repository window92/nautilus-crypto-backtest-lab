# تقرير Spot — OWNER_STRATEGY_RESEARCH_001

> بحث Development/Exploratory على بيانات مكشوفة. ليس Final Holdout، ولا توصية تداول، ولا Profitability Claim.

| Trial | Signals | Orders/Fills | Native units | Net PnL USDT | Fees | Funding | Ending equity | Max DD | Sharpe | Sortino | Calmar | PF | Exposure |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| TSMOM28_FULL_NOTIONAL | 26 | 9/9 | 3 | 6789.96487886 | 96.17826076 | NOT_APPLICABLE | 16789.96487886 | 0.21357635 | 8.409659559324902 | 204.72041951329558 | UNDEFINED | 17.724830129286936 | 0.458559699202 |
| TSMOM28_VOLATILITY_TARGET_20 | 26 | 15/15 | 3 | 1855.22323510 | 22.70144599 | NOT_APPLICABLE | 11855.22323510 | 0.06671258 | 8.259777393758425 | 142.2264600775919 | UNDEFINED | 14.8152297998605 | 0.458559699202 |
| BUY_AND_HOLD_1X_V1 | 1 | 1/1 | 0 | 2536.88078799 | 9.99035701 | NOT_APPLICABLE | 12536.88078799 | 0.53142368 | 1.2077086439323064 | 5.545161363136624 | 6.0067344883889655 | 1.7251603210490092 | 0.999992326581 |

## الوحدات الأصلية

- `TSMOM28_FULL_NOTIONAL`: 3 وحدات Position مكتملة أصلية؛ متوسط realized PnL `2261.29015512` USDT، ومتوسط realized return `0.23179998`. المركز الطرفي `LONG 0.000999` مفتوح ومستبعد من العينة.
- `TSMOM28_VOLATILITY_TARGET_20`: 3 وحدات Position مكتملة أصلية؛ متوسط realized PnL `386.94204247` USDT، ومتوسط realized return `0.14626735`. المركز الطرفي `LONG 0.113826` مفتوح ومستبعد من العينة.

Gross PnL لجميع Runs هو `UNDEFINED_NATIVE_GROSS_PNL_NOT_EXPOSED`؛ لم يُشتق من Net أوfees أوfunding. SampleAdequacy وMonte Carlo هما `NOT_APPLICABLE` وفق البروتوكول الاستكشافي المجمد، دون pooling بين البروفايلين.

كل checker هو `CHECK_PASS`، وكل replay هو `PASS`، ولا توجد `No market` أوprecision rejection أوnetwork contact. كل المراكز الطرفية عُلّمت دون synthetic close.

مرشّحا Spot يستخدمان LONG/FLAT فقط؛ لا short ولاborrowing ولاfunding. Calmar للمرشحين غير معرّفة لأن pinned Nautilus أعاد Position-return fallback، لا portfolio daily returns. Calmar الـbenchmark فقط معرّفة أصلًا.
