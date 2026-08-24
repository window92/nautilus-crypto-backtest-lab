# تقرير Owner — جاهزية المقاييس البحثية الأصلية

## النتيجة

نجحت Qualification لـNautilusTrader `2.0.0rc2`: أصبح الـrunner يحفظ كل دورة NETTING مكتملة من `cache.position_snapshots()` و`cache.positions_closed()` قبل التخلص من المحرك. لا يوجد ربط يدوي للـFills، ولا Trade IDs مصطنعة، ولا PnL أوledger بديل.

هذه المرحلة لا تعيد تشغيل الاستراتيجية، ولا تغيّر نتائج OWNER_SMOKE_002 replacement، ولا تستخدم Final Holdout، ولا تمنح Profitability Claim.

## التسلسل الأصلي لـNETTING

عند الإغلاق إلى FLAT ثم إعادة الفتح، يحفظ Nautilus الـPosition المغلقة كـsnapshot قبل إعادة استخدام الـPosition الحالية. الوحدة المكتملة هي snapshot مغلقة أصلية أوPosition طرفية مغلقة لم تُفتح بعدها. المركز الطرفي المفتوح مستبعد، والـpartial reduction لا ينشئ وحدة مكتملة مستقلة.

كل Run جديد سيحفظ الهوية الأصلية، instrument/side، أزمنة الفتح والإغلاق، order IDs عندما تكون متاحة، average open/close، peak quantity، `Position.realized_pnl`، `Position.realized_return`، commissions، duration، funding-adjustment count، وهوية الـRun.

## المصالحة التاريخية

- Spot: `BacktestResult.total_positions=14`، current Position واحدة LONG مفتوحة، و13 snapshot/دورة مغلقة. العدد الأصلي المكتمل = `13`، ويتطابق مع 13 callback من `PositionClosed`.
- Perpetual: `BacktestResult.total_positions=28`، current Position واحدة LONG مفتوحة، و27 snapshot/دورة مغلقة. العدد الأصلي المكتمل = `27`، ويتطابق مع 27 callback من `PositionClosed`.

تتطابق timestamps لسلسلة Spot الأصلية واحدًا لواحد مع timestamps الإغلاق الـ13، لذلك هي Position-return fallback. أما سلسلة Perpetual فتضم 212 timestamp يومية على UTC ولا تتقاطع مع timestamps الإغلاق الـ27، ولذلك هي portfolio daily returns وليست متوسط صفقات.

الـRuns التاريخية لم تحفظ payload كل snapshot؛ لذلك لم تُخترع تفاصيل الوحدات القديمة من fills. المصالحة أعلاه تثبت cardinality الأصلية فقط، بينما عقد v2 الجديد يحفظ التفاصيل كاملة في الـRuns اللاحقة.

## Realized PnL وAverage trade

`Position.realized_pnl` في runtime المقفل يضم commissions بعملة settlement ويضم `PositionAdjusted.pnl_change`، بما في ذلك funding. `Position.realized_return` هو عائد السعر الأصلي للـPosition. لا تُعد Position مفتوحة صفقة مكتملة.

- Spot historical: سلسلة `returns_series` الأصلية هي Position-return fallback وعددها 13؛ لذا average realized return الأصلي المتاح هو `-0.016989496851835366`. Average realized PnL المفصل يبقى غير قابل للاستخراج من evidence التاريخية لأن snapshots نفسها لم تُحفظ.
- Perpetual historical: `returns_series` هي portfolio daily returns، وليست trade returns؛ لذلك لا تُسمى Average trade. التفاصيل ستتوفر تلقائيًا في Run لاحق عبر snapshots المحفوظة.

## Gross PnL

يبقى `UNDEFINED_NATIVE_GROSS_PNL_NOT_EXPOSED`. القيمة الأصلية `Position.realized_pnl` net بالنسبة إلى commissions بعملة settlement وfunding المنسوب إلى الـPosition؛ لا توجد قيمة Gross عامة منفصلة وغير ملتبسة في API المقفلة. لم نحسب Gross عبر `Net + fees + funding`.

## Calmar

- Spot: `UNDEFINED_NATIVE_CALMAR_PORTFOLIO_RETURNS_BASIS_UNAVAILABLE` لأن native returns هي Position fallback وليست portfolio daily series.
- Perpetual: Nautilus `CalmarRatio(252)` على 181 daily portfolio returns داخل scoring أعاد `-0.837979294581023`. البسط native CAGR(252)، والمقام القيمة المطلقة لـnative MaxDrawdown؛ zero drawdown يعطي undefined/NaN.

## Sample adequacy وMonte Carlo

العقد الجديد يستهلك عدد الوحدات الأصلية لكل Instrument دون pooling. بروتوكول OWNER_SMOKE_002 replacement استكشافي ومقفل على `NOT_APPLICABLE`، لذلك لا تُغيّر هذه المرحلة حالته. Monte Carlo التاريخية تبقى `NOT_APPLICABLE`; وفي أي protocol لاحق لا تعمل إلا من سلسلة `Position.realized_pnl` الأصلية الكاملة بعد costs المنسوبة بصورة غير ملتبسة.

## النزاهة

لم تتغير `SSOT.md` أوRuntime Lock أوDependency Lock أوStrategy أوDataset Releases أوأي ملف تحت `evidence/research/owner-smoke-002-replacement-001/`. لا تعني هذه الجاهزية أن SMA20 مربحة؛ النتائج التاريخية السلبية باقية كما هي.

## القبول النهائي

نجحت `283` حالة اختبار فريدة عبر `878` عملية تنفيذ، بما فيها discovery كامل ومستقل وترتيب عكسي حتمي. كانت failures/errors/skips/xfail جميعها صفرًا، ونجحت Runtime preflight وpip check وcompileall وhistorical evidence validators.

الحكم: `NATIVE_RESEARCH_METRICS_READINESS_PASS`. هذه جاهزية قياس فقط، ولا تعني أن SMA20 مربحة ولا تغيّر نتائجها السابقة.
