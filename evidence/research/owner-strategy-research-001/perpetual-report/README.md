# تقرير Perpetual — OWNER_STRATEGY_RESEARCH_001

> بحث Development/Exploratory على بيانات مكشوفة. ليس Final Holdout، ولا توصية تداول، ولا Profitability Claim.

| Trial | Signals | Orders/Fills | Native units | Net PnL USDT | Fees | Funding | Ending equity | Max DD | Sharpe | Sortino | Calmar | PF | Exposure |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| TSMOM28_FULL_NOTIONAL | 26 | 32/32 | 6 | 13269.12210696 | 267.36610393 | -1594.62877912 | 23269.12210696 | 0.21977610 | 1.8687597898197421 | 2.9021499019766126 | 10.195926114079079 | 1.4117921701598692 | 0.999973143033 |
| TSMOM28_VOLATILITY_TARGET_20 | 26 | 30/30 | 6 | 2350.00312053 | 44.86425774 | -292.37262173 | 12350.00312053 | 0.07417934 | 1.6725397657093881 | 2.5103814881128725 | 4.6051313237798945 | 1.366689450622023 | 0.999973143033 |
| BUY_AND_HOLD_1X_V1 | 1 | 1/1 | 0 | -245.97076187 | 9.98969190 | -2762.76976997 | 9754.02923813 | 0.63986193 | 0.37044400134624783 | 0.5506541423385046 | -0.05326088193325637 | 1.0714947029806365 | 0.999992326581 |

## الوحدات الأصلية

- `TSMOM28_FULL_NOTIONAL`: 6 وحدات Position مكتملة أصلية؛ متوسط realized PnL `1644.16919129` USDT، ومتوسط realized return `0.13688431`. المركز الطرفي `LONG 0.562` مفتوح ومستبعد من العينة.
- `TSMOM28_VOLATILITY_TARGET_20`: 6 وحدات Position مكتملة أصلية؛ متوسط realized PnL `272.54363361` USDT، ومتوسط realized return `0.09447805`. المركز الطرفي `LONG 0.118` مفتوح ومستبعد من العينة.

Gross PnL لجميع Runs هو `UNDEFINED_NATIVE_GROSS_PNL_NOT_EXPOSED`؛ لم يُشتق من Net أوfees أوfunding. SampleAdequacy وMonte Carlo هما `NOT_APPLICABLE` وفق البروتوكول الاستكشافي المجمد، دون pooling بين البروفايلين.

كل checker هو `CHECK_PASS`، وكل replay هو `PASS`، ولا توجد `No market` أوprecision rejection أوnetwork contact. كل المراكز الطرفية عُلّمت دون synthetic close.

العقود الدائمة استخدمت MARGIN/NETTING/leverage 1، وMark الرسمية وfunding الأصلية. كل reversal اجتاز close-to-flat ثم separate reopen؛ لا direct cross-zero ولا project funding posting.
