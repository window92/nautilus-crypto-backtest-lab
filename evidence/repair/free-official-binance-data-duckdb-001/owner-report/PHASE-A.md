# تقرير Owner — Free Official Binance Phase A

## النتيجة

اكتملت مرحلة تأهيل المصادر الرسمية المجانية واختيار النافذة، ولم تُطبّق أي Candidate على `SSOT.md` ولم تُنشأ DatasetRelease ولم تُعدّل DuckDB الحالية ولم تُشغّل استراتيجية.

- هوية التحليل الدلالية: `bf7c4d476702a6438e2940d85548943ca1b2b926f74ba64380e20bd0490c654d`، وتطابقت في عمليتي تحليل مستقلتين.
- النافذة القديمة `[2020-12-01T00:00:00Z, 2021-07-01T00:00:00Z)` بقيت `EXPOSED_DATA_BLOCKED_NOT_FINAL_HOLDOUT` بسبب 24 دقيقة Mark أصلية مفقودة رسميًا.
- أول نافذة ناجحة وفق التحريك الشهري الميكانيكي هي `[2021-01-01T00:00:00Z, 2021-08-01T00:00:00Z)`؛ warmup حتى `2021-02-01T00:00:00Z` ثم scoring حتى نهاية النافذة.
- لم تُفحص أسعار لاختيار النافذة، ولم تُفحص Signals أوPnL أوأي نتيجة استراتيجية، ولم يُستهلك Final Holdout.

## Spot

- الدقائق المتوقعة: `305280`.
- `REAL_OFFICIAL_BAR`: `304595`.
- `DERIVED_FROM_OFFICIAL_TRADES`: `1`، وهي دقيقة `2021-04-25T04:00:00Z` فقط؛ تطابقت raw trades مع aggTrades exact ولم يضف الاشتقاق أي event بعد آخر trade فعلي.
- `VERIFIED_NO_TRADE_INTERVAL`: `684`، بلا OHLCV وبلا Bar.
- الدقيقة `2021-02-11T03:40:00Z` ثبت خلوها من trades عبر raw trades وaggTrades مستقلين: آخر trade ID `633819970` وأول ID تالٍ `633819971`، ولا event داخل الدقيقة. صفوف kline ذات volume/count صفر حُفظت كobservations مستبعدة ولم تصبح Bar.
- كل مجموعات no-trade الأربع (`2021-02-11` و`2021-03-06` و`2021-04-20` و`2021-04-25`) اجتازت independently raw-trade وaggregate-trade boundary matching؛ archive trade-ID gaps = `0` وevents داخل مجموعات الانقطاع = `0`.
- unresolved gaps/conflicts: `0`.

## Perpetual

- execution accepted: `305280/305280`.
- mark accepted: `305280/305280`، بلا substitution وبلا reconstruction.
- خمسون Daily Mark delivery object غير متاحة بقيت محفوظة كـ404، لكن REST وMonthly كاملتان ومتطابقتان exact لكل `72000` دقيقة متأثرة.
- Monthly Mark لشهر يوليو نفسها ناقصة دلاليًا رغم تطابق checksum: غاب `7200` صف، وحُسمت الدقائق فقط لأن Daily وREST كاملتان ومتطابقتان exact.
- funding: `636` event أرشيف و`636` REST event متطابقة؛ interval الرسمي `8h`، وأقصى انحراف timestamp محفوظ عن slot النظري `46 ms` دون إعادة كتابة event time.

## فجوة Mark القديمة

أكدت Monthly archive وDaily archive وREST الرسمية المجانية غياب كل دقائق `[2020-12-17T07:32:00Z, 2020-12-17T07:56:00Z)`. صُنّفت `IRRECOVERABLE_OFFICIAL_MARK_DELIVERY_GAP`، ولم تُشتق Mark من execution أوindex أوpremium أوlast أوSpot.

## نزاهة المرحلة

- مصادر مدفوعة أوطرف ثالث: صفر.
- credentials: صفر.
- أسعار أوBars اصطناعية: صفر.
- material float arithmetic: صفر.
- SSOT الجذرية بقيت `f51971ed7a09b172c82ff5965f2899d2a302dd71a2af60eb7c920133567b4354`.
- DuckDB الحالية بقيت `932e97c446c713e8525f43b8111aced2e914b9579eba10823df7c6b0b51887b6` بحجم `1236807680` byte.
- Candidate 003 محفوظة تاريخيًا ومرفوضة بالسبب `REJECTED_BY_OWNER_PAID_PROVIDER_PATH_NOT_AUTHORIZED`.

## Candidate 004

- root SSOT الحالية: `f51971ed7a09b172c82ff5965f2899d2a302dd71a2af60eb7c920133567b4354`.
- Candidate 004 الكاملة: `b4deb7048242239234de7eaa353b623b3e45247eb42f1021dbc26ffd910edb99`.
- الـdiff المولدة آليًا: `19ff0232d938c320ee9d9cf549a754f244ae00f357be1d3d738b4f303ff74577`.
- forward/reverse: عمليتان مستقلتان، exact byte equality، بلا fuzz أوoffset.
- adoption status: `PENDING_OWNER_BYTE_ADOPTION`.

هذه المرحلة تتوقف هنا. تنفيذ pipeline وDataset Releases ينتظر اعتماد Owner الحرفي:

`OWNER_ADOPTS_SSOT_CANDIDATE_004_SHA256=b4deb7048242239234de7eaa353b623b3e45247eb42f1021dbc26ffd910edb99`
