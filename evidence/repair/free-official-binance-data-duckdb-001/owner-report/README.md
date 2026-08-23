# تقرير Owner — Free Official Binance Data and DuckDB Repair 001

## الحكم

اكتمل الإصلاح ونجحت بوابات البيانات: النافذة المختارة `[2021-01-01T00:00:00Z, 2021-08-01T00:00:00Z)` جاهزة لاستئناف Owner Smoke Research بعد مراجعة المشرف، دون تشغيل Strategy أوOfficial Trial في هذه المهمة.

## القرارات والسبب الجذري

اعتمدت Candidate 004 exact وأصبحت root `SSOT.md` بهوية `b4deb7048242239234de7eaa353b623b3e45247eb42f1021dbc26ffd910edb99`. Candidate 003 بقيت byte-for-byte وموسومة `REJECTED_BY_OWNER_PAID_PROVIDER_PATH_NOT_AUTHORIZED`. لم يُستخدم مزود مدفوع أوطرف ثالث أوcredential.

سبب Spot كان اختلاف packaging تاريخي وصفوف kline صفرية خلال توقفات تداول، إضافة إلى دقيقة جزئية. حُسمت بأحداث Binance raw trades/aggTrades الرسمية وtrade-ID continuity، لا بأولوية صامتة. النتيجة: `304595` Bar رسمية، Bar واحدة مشتقة حتميًا من trades الرسمية، و`684` دقيقة no-trade بلا Bar؛ unresolved = `0`.

سبب Perpetual القديم هو فجوة Mark رسمية غير قابلة للاستعادة مدتها `24` دقيقة، فبقيت النافذة القديمة محجوبة ولم تُصنع Mark. اختيرت آليًا أول نافذة N=1 المكتملة: execution وMark كل منهما `305280/305280`، وfunding `636` event. لم تُفحص Signals أوPnL ولم يُستهلك Final Holdout.

## DuckDB وDataset Releases

- semantic database identity: `c363294d7c00373904c970beddb25c87f5e68d53178510e03312f4423da3914d`.
- Primary DB: `data/duckdb/free-official-binance-data-duckdb-001/primary-v4.duckdb`، `1757949952` byte، `965dea1cfb4dadf189448e1cb34a58dedbba111727d075fd633a6d41c4400fc6`.
- Independent DB: `data/duckdb/free-official-binance-data-duckdb-001/independent-v4.duckdb`، `1758736384` byte، `3403b25612115a4f431847eaa1be738451a7a1eebc60ec954650d006b0154cf0`.
- Spot DatasetRelease: `95e04adb076be05eba0a970aa0978f1a4d1f41ad3caf04e9cd5859dd408ac099`.
- Perpetual DatasetRelease: `9c8a5f679f38852119d1d2054b0711965f0a6d89d5dd0e0ebedaa8d8df66b503`.
- semantic rebuild وNautilus catalog rebuild: exact PASS.
- raw objects: `2241`، source observations: `2670036`، checksums: `630`.

كل canonical row قابلة للتتبع إلى raw object رسمي. القاعدتان read-only بعد CHECKPOINT والإغلاق، والـraw bytes خارج DuckDB بقيت authority. لا synthetic OHLC، لا interpolation/fill، لا Mark substitution، لا مصدر غير رسمي، ولا material float arithmetic.

## الاختبارات

اكتُشفت `249` حالة فريدة؛ full + independent + reverse = `747` occurrence. data repair targeted = `71`، adversarial = `39`، Owner Smoke contract tests = `13`. failures/errors/skips/xfail = `0`. نجحت runtime preflight وpip checks وcompileall وraw rehash وhistorical integrity وdeterministic rebuild وcatalog comparison.

## القيود

لم تُشغّل Strategy أوBacktest أوOfficial Trial أوOptimization، ولم تُفحص profitability. الجاهزية هنا جاهزية بيانات فقط، وNext Action هو `READY_TO_RESUME_OWNER_SMOKE_RESEARCH_WITHOUT_FINAL_HOLDOUT`.
