# تقرير الـOwner — DATA_PROVENANCE_DUCKDB_REPAIR_001

## الخلاصة

الإصلاح **محجوب ولا توجد DatasetRelease ناجحة**. السبب ليس نقصًا في آلية DuckDB أو استخدام مصدر غير رسمي، بل أدلة رسمية غير مكتملة لا يجوز تجاوزها بلا اختلاق: دقيقة Spot واحدة ذات kline رسمي غير صالح، و50 عقد Daily mark archive رسمية أعادت `404` بما يحجب 72,000 دقيقة، و24 دقيقة إضافية مفقودة من شبكة mark price الرسمية كلها. لذلك لم تُشغّل الاستراتيجية ولم يبدأ Official Trial ولم يُبنَ ParquetDataCatalog رسمي.

## السبب الحقيقي ومشكلة ديسمبر 2020

التقرير السابق خلط بين "دقيقة بلا صف" و"فقد بيانات". الفحص الحدثي أثبت أن فترتي ديسمبر كانتا بلا تداول، لا أن السعر ظل ثابتًا:

- في 21 ديسمبر كان REST وDaily يفتقدان 252 دقيقة (`13:48Z–18:00Z`). Monthly احتوت 22 صفًا وحدها وفقدت 230 دقيقة أخرى. حُفظت الصفوف الـ22 كتعارضات تاريخية مستبعدة ولم تدخل canonical data.
- في 25 ديسمبر غابت 60 دقيقة (`02:00Z–03:00Z`) من REST وDaily وMonthly.
- عدد الصفوف المتوقع لكل مصدر في ديسمبر هو 44,640. REST وDaily يحتوي كل منهما 44,328 صفًا؛ Monthly يحتوي 44,350 صفًا. ومن ثم إجمالي Monthly المفقود هو 290 دقيقة، وإجمالي فجوات REST/Daily في اليومين 312 دقيقة.
- الصفوف الـ29 المشار إليها سابقًا هي 28 تعارض Monthly حُسمت بتطابق REST وDaily وBar مشتقة من official aggTrades، إضافة إلى صف `2020-12-21T13:47:00Z` ذي close timestamp غير صالح. أُعيدت الدقيقة الأخيرة حتميًا من official trades.

## Bars المستعادة من official trades

- `2020-12-21T13:47:00Z`: `O=22699.58 H=22721.59 L=22675.80 C=22681.32`، volume `4.039609`، quote volume `91646.48825864`، trade count `124`.
- `2021-04-25T04:00:00Z`: `O=49626.76 H=49705.04 L=49600.24 C=49683.94`، volume `5.887034`، quote volume `292226.01345715`، trade count `224`.

كل الحسابات Decimal exact، وكل Bar مرتبطة بbytes رسمية لـaggTrades. لا interpolation ولا previous-close fill ولا تقريب مادي.

على كامل النافذة حُفظ 137 سجل تعارض: 112 Monthly observations استُبعدت بعد consensus رسمي ثلاثي، وصفّان ذوا close-time غير صالح استُعيدا من official trades، و22 Monthly-only rows استُبعدت بإثبات no-trade، وتعارض واحد بقي مانعًا. لم تُحذف أي نسخة رسمية متعارضة.

## فترات VERIFIED_NO_TRADE_INTERVAL

- `2020-12-21T13:48:00Z` إلى `2020-12-21T18:00:00Z` (النهاية حصرية): 252 دقيقة؛ aggregate IDs `467290561→467290562` وtrade IDs `519076631→519076632`.
- `2020-12-25T02:00:00Z` إلى `2020-12-25T03:00:00Z` (النهاية حصرية): 60 دقيقة؛ aggregate IDs `472060507→472060508` وtrade IDs `524618176→524618177`.
- `2021-02-11T03:41:00Z` إلى `2021-02-11T05:00:00Z` (النهاية حصرية): 79 دقيقة؛ aggregate IDs `568349719→568349720` وtrade IDs `633819970→633819971`.
- `2021-03-06T02:00:00Z` إلى `2021-03-06T03:30:00Z` (النهاية حصرية): 90 دقيقة؛ aggregate IDs `614865302→614865303` وtrade IDs `686872224→686872225`.
- `2021-04-20T02:00:00Z` إلى `2021-04-20T04:30:00Z` (النهاية حصرية): 150 دقيقة؛ aggregate IDs `694619830→694619831` وtrade IDs `779500982→779500983`.
- `2021-04-25T04:01:00Z` إلى `2021-04-25T08:45:00Z` (النهاية حصرية): 284 دقيقة؛ aggregate IDs `704256666→704256667` وtrade IDs `790459364→790459365`.

التصنيف هو `PROBABLE_VENUE_OUTAGE` فقط؛ لم نستخدم وصف صيانة معلنة لعدم وجود إعلان Binance رسمي زمني مطابق. لا تحمل هذه الفترات OHLC أوvolume، ولا تُصدّر كـBar ولا تسمح بسعر أوFill.

## البنود غير المحسومة

- Spot `2021-02-11T03:40:00Z`: REST وDaily وMonthly تنشر صفًا صفري الحجم/التداول بسعر `44582.07` لكن close time هو `2021-02-11T03:40:54.773Z` بدل نهاية الدقيقة. لا توجد aggTrades داخل الدقيقة. لا يجوز تصحيح الوقت أوإنشاء OHLC، ولا تنطبق قاعدة no-trade لأن REST وDaily أعادا صفًا؛ الحكم `SOURCE_CONFLICT`.
- Perpetual mark price: 50 عقد Daily archive رسمية أعادت `404`، ولذلك حُجبت دقائقها الـ72,000 حتى مع تطابق REST وMonthly؛ التواريخ هي `2021-01-18`, `2021-01-19`, `2021-01-20`, `2021-01-21`, `2021-01-22`, `2021-01-23`, `2021-01-24`, `2021-01-25`, `2021-01-26`, `2021-01-27`, `2021-01-28`, `2021-01-29`, `2021-01-30`, `2021-01-31`, `2021-02-01`, `2021-02-02`, `2021-02-03`, `2021-02-04`, `2021-02-05`, `2021-02-06`, `2021-02-07`, `2021-02-08`, `2021-02-09`, `2021-02-10`, `2021-02-11`, `2021-02-12`, `2021-02-13`, `2021-02-14`, `2021-02-15`, `2021-02-16`, `2021-02-17`, `2021-02-18`, `2021-02-19`, `2021-02-20`, `2021-03-22`, `2021-03-23`, `2021-03-24`, `2021-03-25`, `2021-03-26`, `2021-05-24`, `2021-05-25`, `2021-05-26`, `2021-05-27`, `2021-05-28`, `2021-06-07`, `2021-06-08`, `2021-06-10`, `2021-06-11`, `2021-06-27`, `2021-06-28`. توجد أيضًا 24 دقيقة متتالية مفقودة من جميع REST/Daily/Monthly من `2020-12-17T07:32:00Z` إلى `2020-12-17T07:56:00Z` (النهاية حصرية). الدقائق الدقيقة: `2020-12-17T07:32:00Z`, `2020-12-17T07:33:00Z`, `2020-12-17T07:34:00Z`, `2020-12-17T07:35:00Z`, `2020-12-17T07:36:00Z`, `2020-12-17T07:37:00Z`, `2020-12-17T07:38:00Z`, `2020-12-17T07:39:00Z`, `2020-12-17T07:40:00Z`, `2020-12-17T07:41:00Z`, `2020-12-17T07:42:00Z`, `2020-12-17T07:43:00Z`, `2020-12-17T07:44:00Z`, `2020-12-17T07:45:00Z`, `2020-12-17T07:46:00Z`, `2020-12-17T07:47:00Z`, `2020-12-17T07:48:00Z`, `2020-12-17T07:49:00Z`, `2020-12-17T07:50:00Z`, `2020-12-17T07:51:00Z`, `2020-12-17T07:52:00Z`, `2020-12-17T07:53:00Z`, `2020-12-17T07:54:00Z`, `2020-12-17T07:55:00Z`. لا يُسمح باستخدام execution/index/premium/last أوالاشتقاق من trades.

## التغطية الرسمية

- Spot: 305,280 دقيقة؛ 304,362 `REAL_OFFICIAL_BAR`، و2 `DERIVED_FROM_OFFICIAL_TRADES`، و915 `VERIFIED_NO_TRADE_INTERVAL`، ودقيقة `SOURCE_CONFLICT` واحدة.
- Perpetual execution: 305,280/305,280 دقيقة مقبولة، بلا blocker.
- Perpetual mark: 233,256/305,280 دقيقة canonical؛ 72,024 دقيقة محجوبة (72,000 بسبب daily archive roles الناقصة، و24 مفقودة من المصادر الثلاثة)، بلا fallback.
- Funding: 636 حدثًا canonical، تطابق فيها REST والأرشيف، والجدول مستند إلى `fundingIntervalHours` الرسمي؛ بلا blocker.
- metadata: صفان رسميان Spot وUSDⓈ-M؛ metadata الحالية موثقة كcurrent observation وليست ادعاءً بأنها historical snapshot.

## DuckDB

المسار المحلي: `data/duckdb/binance-btcusdt-owner-smoke-001.duckdb`

الحجم: `1,236,807,680` byte

SHA-256: `932e97c446c713e8525f43b8111aced2e914b9579eba10823df7c6b0b51887b6`

Schema identity: `8869cdd6c60439e0b06bc773bf9068d77a0b5b6d85166e58c914c3816ab33fce`

Semantic database identity: `d7744bb353bbf2254021f22e1c268948b78df51c7998e3a7255a5b7160944c3b`

Independent rebuild semantic identity: `d7744bb353bbf2254021f22e1c268948b78df51c7998e3a7255a5b7160944c3b`

الجداول وأعداد الصفوف:

- `archive_observations`: 682
- `canonical_execution_bars`: 609,644
- `canonical_funding_events`: 636
- `canonical_mark_bars`: 233,256
- `dataset_releases`: 0
- `derived_spot_klines`: 145
- `funding_observations`: 1,272
- `http_observations`: 2,343
- `instrument_metadata`: 2
- `minute_coverage`: 610,560
- `perpetual_execution_observations`: 915,840
- `perpetual_mark_observations`: 843,768
- `publisher_checksums`: 632
- `raw_objects`: 2,342
- `rebuild_manifests`: 1
- `schema_metadata`: 1
- `source_conflicts`: 137
- `spot_agg_trades`: 1,053,682
- `spot_kline_observations`: 913,117
- `validation_results`: 3
- `verified_no_trade_intervals`: 6

قاعدة DuckDB payload والـraw archives باقية محليًا ومهملة من Git. DuckDB مخزن تحقق/query مشتق فقط؛ لم تنفذ matching أوorders أوfills أوpositions أوaccounting أوfees أوfunding settlement أوPnL.

## DatasetRelease وNautilus

`dataset_releases` تحوي صفر صف، وDatasetRelease IDs هي قائمة فارغة. لا يمكن إنشاء release ناجحة مع blockers المذكورة. لذلك لم يُبنَ ParquetDataCatalog. شُغّلت Qualification بيانات صغيرة فقط داخل Nautilus وأثبتت أن sparse real bars مقبولة، وأن الأمر المعلق لا يحصل على سعر أوFill في الدقيقة غير المتاحة، وأن أول Fill لاحق يستخدم أول market state حقيقية وفق latency المقفلة.

## ضمانات المصدر

استخدم الإصلاح Binance الرسمية فقط وofficial PyPI artifact لأداة DuckDB. لم يستخدم Kaggle أوccxt cache أوأي venue/dataset أخرى. لم تُنشأ أسعار أوBars وهمية، وبقيت كل bytes والنسخ المتعارضة محفوظة ببصماتها.

## الجاهزية

البيانات **غير جاهزة لتشغيل الاستراتيجية**. الحكم النهائي: `DATA_REPAIR_BLOCKED_UNRESOLVED_OFFICIAL_DATA`.

OWNER_REPORT_GITHUB_URL: https://github.com/window92/nautilus-crypto-backtest-lab/blob/main/evidence/repair/data-provenance-duckdb-001/owner-report/README.md

RAW_EVIDENCE_GITHUB_URL: https://github.com/window92/nautilus-crypto-backtest-lab/tree/main/evidence/repair/data-provenance-duckdb-001
