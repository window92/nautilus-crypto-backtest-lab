# تقرير Owner — إصلاح Instrument representation وFunding checker

## النتيجة

نجح إصلاح البيانات والـchecker كمرحلة مستقلة قبل إعادة البحث. لم تتغير `SSOT.md` أوRuntime Lock أوDependency Lock أوالاستراتيجية، ولم تتغير أي قيمة رقمية لـOHLCV أوMark أوFunding. التحويل الوحيد داخل Nautilus هو zero-padding عددي غير فاقد.

هذه ليست نتيجة استراتيجية، وليست Final Holdout، ولا تمنح أي Profitability Claim.

## العيب المثبت

- Spot: كانت `size_precision=5` بينما 274,195 Bar تحمل حجمًا حقيقيًا يحتاج ست خانات؛ رفض Nautilus الـBars ثم رُفضت 89 أوامر بـ`No market`. الـchecker التاريخي أعاد `CHECK_PASS` خطأً.
- Perpetual: كانت `price_precision=1` بينما execution يحتاج حتى خانتين وMark يحتاج حتى ثماني خانات؛ رُفضت market state ثم رُفضت 180 أوامر.
- التمويل: تحديثا `FundingRateUpdate` يمثلان binding واحدة للحدث الرسمي في runtime المثبت، وليس تسويتين ماليتين. التسوية تُعد فقط من `PositionAdjusted(FUNDING)` وأثر AccountState.

## التمثيل وشبكة الأوامر

- Spot runtime: `price_precision=2`, `size_precision=6`; tick العددي `0.01`; step التاريخي المثبت `0.000001`.
- Perpetual runtime: `price_precision=8`, `size_precision=3`; tick التاريخي المثبت `0.01`; step `0.001`.
- لم تُستخدم `pricePrecision` أو`quantityPrecision` بدل tick/step. فحوص LOT_SIZE وMARKET_LOT_SIZE والحدود تعمل قبل Nautilus submission.

## استمرارية القيم

قورنت ثمانية جداول canonical القديمة والجديدة بـ`EXCEPT ALL`: الصفوف متطابقة في الاتجاهين، والفروق صفر. لا rounding، لا truncation، لا interpolation، ولا تعديل raw decimal spelling.

## قبول Nautilus الكامل

- Spot: expected/accepted executable Bars = `304596/304596`، precision skips = 0، missing market state = 0.
- Perpetual: execution `305280/305280` وMark `305280/305280`، rejected precision events = 0، missing market state = 0.
- نجحت أربعة Sentinel Fills موزعة لكل Profile بكمون 60 ثانية، وفشل control ذي الكمون الصفري كما يجب لأنه same-bar.

## الهويات الجديدة

- Spot DatasetRelease: `fd8542c109cfbf7d6b19d5b7bbb7705c6a161efc807695f3671978c381e34eca`
- Perpetual DatasetRelease: `b6c8f5d659f3441c924b613d770342796c90b90a970f42a3dc8227c856198917`
- Spot metadata: `9c7ba442a19cb74f8059983ae56db23b8c341ac47c3ba77e2fb8da05a661e3ea`
- Perpetual metadata: `b4579742d10d7e1e529689ae07c3db2b6a9362430d0b8cd7112a4d9846eef226`
- Spot catalog: `db0971d28caba547378e3acba5ad8df1cbd0d6d5be963d153248928a729e374f`
- Perpetual catalog: `7c96897a8e1ea3c02198238a277fb8c3d995f54dd90dc381e534a5f21b017ae0`
- DuckDB semantic identity: `11329c1497ff6bf3a68c5d3ba994f5ac2bbd0ece51cf489f9fa3f681a01ecbff`
- DuckDB schema identity: `74276cca97b16757602a2d90f140891fa08d1463c901d5b75ad69d7f23ffa4da`

الـReleases القديمة محفوظة ومصنفة `SUPERSEDED_INSTRUMENT_REPRESENTATION_INCOMPATIBLE_WITH_PINNED_NAUTILUS`، ولم تُحذف Trials أوEvidence سابقة.

## DuckDB والصفوف

- Primary: `data/duckdb/instrument-representation-funding-checker-001/primary-v6.duckdb`، الحجم `1,755,852,800` bytes، وSHA-256 الفيزيائية `bf8413f38cf9c7a4a8238e17680404e36c94dd3b757cbb3581e297b49240e5fb`.
- Independent: `data/duckdb/instrument-representation-funding-checker-001/independent-v3.duckdb`، الحجم `1,758,474,240` bytes، وSHA-256 الفيزيائية `7c6bc679a651757235942f186eb113d22c503b41fe82018550a1c494f86a00b9`.
- أهم counts: Spot Bars `304596`، Perpetual execution `305280`، Perpetual Mark `305280`، funding source events `636`، runtime funding updates `1272`، minute dispositions `610560`، verified no-trade `684`، raw objects `2243`.
- كل build أُغلق ثم أُعيد فتحه read-only، والفروق الفيزيائية لا تغيّر schema أوordered row counts أوper-table semantic hashes أوrelease/catalog identities.

## Funding وMark as-of

عند timestamp تمويل ذي millisecond offset يختار الـchecker أحدث Mark أصلية عند أو قبل الحد فقط، بحد staleness أقصى 60 ثانية. لا future Mark ولا nearest-neighbor ولاinterpolation. لا تُعد update pair تسويتين؛ boundary بلا position مؤهلة يتطلب صفر تسويات، والحد ذو position مؤهلة يتطلب `PositionAdjusted(FUNDING)` مالية واحدة وأثر AccountState متوافقًا.

## ملاحظة controls الرياضية

بما أن Spot step التاريخية المثبتة `0.000001` تساوي وحدة precision=6، فمجموعة «قيمة precision=6 وليست multiple من step» فارغة رياضيًا؛ اختُبرت القيمة الأدق مباشرة `0.1000001` ورُفضت. وينطبق المنطق نفسه على Perpetual precision=3 مع step `0.001`، مع control إضافي لسعر precision=8 خارج tick `0.01`.

## إعادة البناء والاختبارات

البناءان المستقلان متطابقان دلاليًا رغم اختلاف file hash الفيزيائي المتوقع. بوابة القبول النهائية: 268 اختبارًا فريدًا، 960 execution occurrence، failures=0، errors=0، skips=0، xfail=0. نجحت runtime preflight وpip checks وcompileall وraw rehash وgit diff check.

## Replacement Owner Smoke

أُنشئت Replacement Trials من SourceRevision نظيفة وبالاستراتيجية والنافذة والـparameters نفسها:

- Spot: `CHECK_PASS` وreplay `PASS`، 27 Order و27 Fill، وNet PnL `-751.78721000 USDT`.
- Perpetual: `CHECK_PASS` وreplay `PASS`، 55 Order و55 Fill، وNet PnL `-3010.78713375 USDT`.
- 636 source funding events ارتبطت بـ1,272 runtime updates، لكن عُدت 539 settlement مالية أصلية فقط للحدود المؤهلة.
- Final Holdout remained `false`، وReal profitability claim remained `false`؛ النتائج السلبية معروضة كما هي.

التقرير البحثي الكامل: `evidence/research/owner-smoke-002-replacement-001/owner-report/README.md`.
