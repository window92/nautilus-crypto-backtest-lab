# تقرير المالك — BINANCE_ORIGIN_ARCHIVE_RECOVERY_001

## النتيجة

هذه مرحلة تأهيل مزوّد وإنشاء SSOT Candidate فقط. لم تُعتمد Candidate 003، ولم تتغير `SSOT.md` الجذرية، ولم تتغير DuckDB، ولم تُشغّل Strategy أوOfficial Trial، ولم يُنشأ DatasetRelease.

الحكم النهائي:

`SSOT_CANDIDATE_READY_PROVIDER_ACCESS_REQUIRED`

توجد قناة مؤهلة دلاليًا لدى Tardis لأحداث Binance الأصلية، ونجح Validator على عينات كبيرة متعددة، لكن بايتات الفاصلين المستهدفين لم تُنزّل لأن الوصول التاريخي الدقيق مدفوع. لذلك لا أدّعي أن صفوف الهدف موجودة، ولا أن البيانات مستحيلة.

## أساس Binance الرسمي

- توثيق Binance الحالي يعرّف Mark Price WebSocket كـ`markPriceUpdate` مع event time وسعر Mark نصي، ويعرض سرعتي 3 ثوانٍ وثانية واحدة.
- مستودع Binance الرسمي يحتوي Issue #483 الذي يصف بالضبط فجوة 24 دقيقة في 2020-12-17 من 07:32 حتى 07:55 UTC. هذه مساهمة مستخدم وليست إقرارًا من Binance بوجود أرشيف قابل للاسترداد.
- REST الرسمي أعاد 07:30 و07:31 ثم 07:56 و07:57 داخل نافذة الفحص نصف المفتوحة؛ لم يُعد أي صف للفجوة ذات 24 دقيقة. الاستعلام الشامل أعاد أيضًا 07:58 لأنها حد `endTime` شامل، لكنها خارج نافذة المزوّد `[07:30,07:58)`.

## تأهيل المزوّدين

### Crypto Lake

توثيق المزوّد يذكر `funding` لـ`BINANCE_FUTURES` منذ 2020-01-01 ويحتوي `mark_price` مع `origin_time` و`received_time`. لكن المخطط يعرّف `mark_price` كـ`float64`، أي `PROVIDER_NORMALIZED_RECORD` وليس payload Binance أصليًا. العينة العامة لا تحتوي Prefix التاريخ 2020-12-17؛ الوصول الكامل يحتاج خطة مدفوعة وبيانات اعتماد AWS صادرة عن المزوّد. لم تُثبت بايتات الهدف، ولم تُقبل هذه القناة كحقيقة Mark canonical.

### Tardis.dev

الميتاداتا الأولية تثبت `binance-futures`، الرمز `btcusdt` من 2019-11-17، وقناة `markPrice` الخام؛ وتوثيق المزوّد يقول إن الصيغة هي payload WebSocket الأصلي مع local receive timestamp، وأن الاشتراك `@markPrice@1s` متاح منذ 2020-02-13. لا يوجد incident report في الميتاداتا يتقاطع مع الهدف، لكن ذلك ليس إثبات صفوف الهدف.

اختبار التحكم استخدم عشرة فواصل، كل منها 10 دقائق، موزعة على الأشهر السبعة للنافذة المقفلة: **100/100 دقيقة مطابقة دلاليًا بالكامل** من **5999 حدثًا أصليًا**. الحساب `Decimal` فقط، والترتيب `(exchange event timestamp, capture order)`، والحدود نصف مفتوحة، ولم يحدث interpolation أوfill أوaverage أوsubstitution.

طلب الهدف الخام أعاد HTTP 401 لأن الوصول غير المصرح به متاح لأول يوم من الشهر فقط. الوصول المطلوب بدقة لفاصل 2020 هو:

- Mark: Tardis `Perpetuals`، اشتراك `Business` بفوترة سنوية، وRaw Data Replay API لـ`binance-futures/markPrice`.
- Spot: Tardis `Spot`، اشتراك `Business` بفوترة سنوية، وRaw Data Replay API لـ`binance/aggTrade`.
- أوخطة `All Exchanges` بنفس `Business/yearly` لتغطي الاثنين.

لا تُرسل أي بيانات اعتماد في المحادثة؛ يكفي تجهيز الوصول في بيئة التنفيذ لاحقًا.

### Amberdata

المسار الموثق `/markets/futures/tickers/{instrument}` يعرض `markPrice` و`exchangeTimestamp` كسجل normalized ويتطلب وصول المزوّد؛ طلبا metadata والهدف أعادا 403. والأهم أن قسم Binance Historical Ticker نفسه يذكر بداية Futures tickers في 2021-04-12، بعد الهدف. لا تُستخدم عبارات التغطية الأوسع لتجاوز هذا القيد الخاص بالمنتج. النتيجة: غير مؤهل للهدف.

## فجوة Mark المستهدفة

الحالة الحالية هي `ACCESS_REQUIRED_TARGET_BYTES_NOT_CONFIRMED`. لم تُشتق أي Bar للـ24 دقيقة، ولم تُقبل أي قيمة، ولا توجد events مستهدفة محفوظة يمكن منها إثبات continuity لكل دقيقة أوالمقارنة مع أرشيف مستقل ثانٍ.

## دقيقة Spot: 2021-02-11T03:40:00Z

تم الحفاظ على الـkline الرسمي المتعارض في الأدلة السابقة. طلب Tardis الخام لـ`aggTrade` حول الدقيقة أعاد 401، ولذلك لم يُثبت غياب event داخل الدقيقة ولا استمرارية aggregate/trade IDs قبلها وبعدها. تبقى الدقيقة:

`SOURCE_CONFLICT`

ولا تصبح `VERIFIED_NO_TRADE_INTERVAL`. لم تُنشأ OHLCV ولم يحدث forward fill. اختبرنا Validator على دقيقة سليمة عامة تحتوي 1,348 event؛ رفض no-trade كما يجب مع ID/capture continuity سليمة.

## خمسون Daily Mark 404

فُحصت التواريخ الخمسون مستقلًا من DuckDB read-only. لكل تاريخ: Monthly الرسمي موجود، publisher checksum مطابق، REST يحتوي 1,440 دقيقة فريدة وصحيحة، Monthly يحتوي 1,440 دقيقة، والتطابق دقيق في OHLC وclose time والنصوص العشرية الأصلية. النتيجة **50/50 PASS** واقتراح التصنيف لكل منها:

`REDUNDANT_OFFICIAL_DELIVERY_ROLE_UNAVAILABLE`

تبقى استجابات 404 محفوظة؛ لا يعني هذا غياب market state. التواريخ:

2021-01-18, 2021-01-19, 2021-01-20, 2021-01-21, 2021-01-22, 2021-01-23, 2021-01-24, 2021-01-25, 2021-01-26, 2021-01-27, 2021-01-28, 2021-01-29, 2021-01-30, 2021-01-31, 2021-02-01, 2021-02-02, 2021-02-03, 2021-02-04, 2021-02-05, 2021-02-06, 2021-02-07, 2021-02-08, 2021-02-09, 2021-02-10, 2021-02-11, 2021-02-12, 2021-02-13, 2021-02-14, 2021-02-15, 2021-02-16, 2021-02-17, 2021-02-18, 2021-02-19, 2021-02-20, 2021-03-22, 2021-03-23, 2021-03-24, 2021-03-25, 2021-03-26, 2021-05-24, 2021-05-25, 2021-05-26, 2021-05-27, 2021-05-28, 2021-06-07, 2021-06-08, 2021-06-10, 2021-06-11, 2021-06-27, 2021-06-28

التفصيل minute/date-level موجود في `daily-404-reconciliation.json`.

## SSOT Candidate 003

- Base root SSOT SHA-256: `f51971ed7a09b172c82ff5965f2899d2a302dd71a2af60eb7c920133567b4354`
- Complete Candidate SHA-256: `9e6e9328b40104a65ed7d4f785731032d7b1b4cd37df5d845cad2197d6db067a`
- Unified diff SHA-256: `b6940d7f1a7adf592a46984710c23579ecab6b2c8b67002ed38f2dd3e4a665c1`
- الحجم: 131868 bytes، 2582 سطرًا.
- Forward/reverse: PASS في عمليتين مستقلتين داخل checkout نظيف، بلا fuzz أوoffset؛ ناتج forward يطابق Candidate byte-for-byte وناتج reverse يطابق base.
- semantic audit: PASS؛ لا تغيير في Runtime، latency، Fill، orders، fees، funding settlement، PnL، research، Holdout أوclaims.
- adoption status: `PENDING_OWNER_BYTE_ADOPTION`.

Candidate تسمح فقط بمزوّد خارجي كناقل immutable لـBinance-origin events؛ تمنع provider averaging وsilent precedence وsynthetic OHLC، وتبقي الخلاف fail-closed. وهي تسمح باعتبار Daily 404 مسار تسليم redundant فقط عند اتفاق Monthly+REST الكامل والدقيق.

## سلامة الحالة

- root `SSOT.md`: لم تتغير وبصمتها `f51971ed7a09b172c82ff5965f2899d2a302dd71a2af60eb7c920133567b4354`.
- Runtime Lock: `4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd`؛ لم يتغير.
- Dependency Lock: `b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47`؛ لم يتغير.
- DuckDB الحالية: `data/duckdb/binance-btcusdt-owner-smoke-001.duckdb`، حجم 1236807680 bytes، SHA-256 `932e97c446c713e8525f43b8111aced2e914b9579eba10823df7c6b0b51887b6`؛ فُتحت read-only فقط وبقيت byte-for-byte.
- الأدلة التاريخية المرتبطة بالـmanifest: 43/43 سليمة.
- لا Product Code، لا DatasetRelease، لا commit، ولاpush.

## مسارات المراجعة المحلية

- Candidate الكاملة: `evidence/repair/binance-origin-archive-recovery-001/ssot-candidate-003/SSOT.candidate-003.md`
- diff: `evidence/repair/binance-origin-archive-recovery-001/ssot-candidate-003/SSOT.candidate-003.diff`
- manifest: `evidence/repair/binance-origin-archive-recovery-001/ssot-candidate-003/candidate-manifest.json`
- تأهيل المزوّد: `evidence/repair/binance-origin-archive-recovery-001/provider-coverage-qualification.json`
- Mark target: `evidence/repair/binance-origin-archive-recovery-001/target-mark-gap-status.json`
- Spot: `evidence/repair/binance-origin-archive-recovery-001/spot-no-trade-status.json`
- Daily 404: `evidence/repair/binance-origin-archive-recovery-001/daily-404-reconciliation.json`

الخطوة التالية خارج هذه المرحلة: مراجعة Owner لبايتات Candidate 003 واعتمادها صراحة إن وافق، ثم توفير الوصول الموثق للمزوّد داخل بيئة التنفيذ لتنزيل target bytes والتحقق منها. لا يبدأ Data Repair أوStrategy قبل ذلك.
