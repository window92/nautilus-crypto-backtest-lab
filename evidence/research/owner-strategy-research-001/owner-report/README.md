# تقرير Owner — OWNER_STRATEGY_RESEARCH_001

## الحكم

اكتملت العائلة المجمدة `BTCUSDT_WEEKLY_TSMOM28_V1` ميكانيكيًا على Spot وPerpetual. جميع التجارب الست انتهت `COMPLETED`، وكل checker أعاد `CHECK_PASS`، وكل replay تطابق دلاليًا. هذا **ليس** إثبات ربحية، ولا Final Holdout، ولا توصية تداول.

## العقد المجمد

- Candidate budget: مرشحان بالضبط: `TSMOM28_FULL_NOTIONAL` و`TSMOM28_VOLATILITY_TARGET_20`.
- Benchmark منفصل لكل Profile: `BUY_AND_HOLD_1X_V1`، ولا يدخل candidate budget.
- القرار أسبوعي الاثنين 00:00 UTC من 29 close مكتملة؛ momentum هو `C[-1]/C[-29]-1`؛ latency = 60 ثانية.
- multiple-testing policy = `HOLM_BONFERRONI`، لكن لا winner selection ولا claim ولا p-value promotion في هذا البحث الاستكشافي.
- النافذة `[2021-01-01, 2021-08-01)` والتسجيل `[2021-02-01, 2021-08-01)` مصنفة `EXPOSED_DEVELOPMENT_DATA` و`DATA_QUALITY_INSPECTED_NOT_FINAL_HOLDOUT`.

## Spot

| Trial | Signals | Orders/Fills | Native units | Net PnL USDT | Fees | Funding | Ending equity | Max DD | Sharpe | Sortino | Calmar | PF | Exposure |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| TSMOM28_FULL_NOTIONAL | 26 | 9/9 | 3 | 6789.96487886 | 96.17826076 | NOT_APPLICABLE | 16789.96487886 | 0.21357635 | 8.409659559324902 | 204.72041951329558 | UNDEFINED | 17.724830129286936 | 0.458559699202 |
| TSMOM28_VOLATILITY_TARGET_20 | 26 | 15/15 | 3 | 1855.22323510 | 22.70144599 | NOT_APPLICABLE | 11855.22323510 | 0.06671258 | 8.259777393758425 | 142.2264600775919 | UNDEFINED | 14.8152297998605 | 0.458559699202 |
| BUY_AND_HOLD_1X_V1 | 1 | 1/1 | 0 | 2536.88078799 | 9.99035701 | NOT_APPLICABLE | 12536.88078799 | 0.53142368 | 1.2077086439323064 | 5.545161363136624 | 6.0067344883889655 | 1.7251603210490092 | 0.999992326581 |

## Perpetual

| Trial | Signals | Orders/Fills | Native units | Net PnL USDT | Fees | Funding | Ending equity | Max DD | Sharpe | Sortino | Calmar | PF | Exposure |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| TSMOM28_FULL_NOTIONAL | 26 | 32/32 | 6 | 13269.12210696 | 267.36610393 | -1594.62877912 | 23269.12210696 | 0.21977610 | 1.8687597898197421 | 2.9021499019766126 | 10.195926114079079 | 1.4117921701598692 | 0.999973143033 |
| TSMOM28_VOLATILITY_TARGET_20 | 26 | 30/30 | 6 | 2350.00312053 | 44.86425774 | -292.37262173 | 12350.00312053 | 0.07417934 | 1.6725397657093881 | 2.5103814881128725 | 4.6051313237798945 | 1.366689450622023 | 0.999973143033 |
| BUY_AND_HOLD_1X_V1 | 1 | 1/1 | 0 | -245.97076187 | 9.98969190 | -2762.76976997 | 9754.02923813 | 0.63986193 | 0.37044400134624783 | 0.5506541423385046 | -0.05326088193325637 | 1.0714947029806365 | 0.999992326581 |

## قراءة المقاييس

- الوحدات المكتملة أصلية من Nautilus NETTING snapshots/closed Positions: Spot = 3 لكل مرشح؛ Perpetual = 6 لكل مرشح. الـbenchmark يحتفظ بمركز طرفي مفتوح، ولذلك completed units = 0.
- متوسط realized PnL/return مشتق فقط من سلسلة Position الأصلية المكتملة. لا Fill pairing ولا Trade IDs مصطنعة.
- Gross PnL = `UNDEFINED_NATIVE_GROSS_PNL_NOT_EXPOSED` لكل Run.
- Calmar تُعرض فقط عندما يقبل `CalmarRatio(252)` الأصلي portfolio daily returns؛ تبقى غير معرّفة لمرشحي Spot ذوي Position-return fallback.
- SampleAdequacy = `NOT_APPLICABLE` وMonte Carlo = `NOT_APPLICABLE` لأن البروتوكول الاستكشافي جمدهما قبل النتائج؛ لم يُضع threshold بعد التعرض.

## البيانات والتنفيذ

- Spot release `fd8542c109cfbf7d6b19d5b7bbb7705c6a161efc807695f3671978c381e34eca`؛ catalog `db0971d28caba547378e3acba5ad8df1cbd0d6d5be963d153248928a729e374f`.
- Perpetual release `b6c8f5d659f3441c924b613d770342796c90b90a970f42a3dc8227c856198917`؛ catalog `7c96897a8e1ea3c02198238a277fb8c3d995f54dd90dc381e534a5f21b017ae0`.
- DuckDB semantic identity `11329c1497ff6bf3a68c5d3ba994f5ac2bbd0ece51cf489f9fa3f681a01ecbff`.
- لا acquisition ولاتعديل raw/DuckDB/release/catalog. كل Run عمل داخل process-level offline boundary، external contacts = 0.
- Nautilus وحده امتلك orders/Fills/positions/accounts/PnL/fees/funding/mark valuation. لا synthetic terminal Fill.

## المقارنة التاريخية المقيدة

SMA20 التاريخية تبقى benchmark مكشوفًا غير معاد التشغيل: Spot Net PnL `-751.78721000 USDT`، وPerpetual Net PnL `-3010.78713375 USDT`. لا تُستخدم هذه المقارنة لاختيار winner أوتغيير parameter.

## النزاهة والاختبارات

المحاولة الأولى لـSpot Candidate A بقيت `FAILED/CHECK_FAIL` ومحفوظة؛ أُعيد candidate نفسه بهوية Trial جديدة بعد إصلاح Product لا يغير semantics. نتيجة القبول النهائية: `PASS`؛ unique tests `294`؛ execution occurrences `924`؛ failures/errors/skips/xfail كلها صفر.

## الأهلية

`final_holdout_used=false`، `real_profitability_claim=false`، `optimization_performed=false`، والحالة `INELIGIBLE_FOR_REAL_PROFITABILITY_CLAIM`. لا يُسمى أي مرشح proven أوwinner، وتنتظر أي عائلة أوHoldout جديدة مراجعة Owner مستقلة.
