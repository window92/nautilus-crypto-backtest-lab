# تقرير بيانات Spot الرسمي المجاني

## النتيجة

نجح إصدار Spot للنافذة `[2021-01-01T00:00:00Z, 2021-08-01T00:00:00Z)` بلا فجوة غير محسومة وبلا Bar مصطنعة.

- الدقائق المتوقعة: `305280`.
- `REAL_OFFICIAL_BAR`: `304595`.
- `DERIVED_FROM_OFFICIAL_TRADES`: `1` عند `2021-04-25T04:00:00Z`، ومصدرها raw trades وaggTrades رسميان متطابقان مع continuity كاملة.
- `VERIFIED_NO_TRADE_INTERVAL`: `684`؛ بقيت coverage فقط ولم تُصدّر كـBar.
- source conflicts غير المحسومة: `0`.
- trade-ID gaps غير المفسرة: `0`.
- duplicate events المقبولة: `0`.

الدقيقة `2021-02-11T03:40:00Z` ثبت خلوها من trades: آخر trade ID قبلها `633819970` وأول ID بعدها `633819971`. صف الـkline الصفري حُفظ observation مستبعدًا ولم يصبح canonical Bar.

فترات no-trade المثبتة: `2021-02-11 02:20–05:00` (`160` دقيقة)، `2021-03-06 02:00–03:30` (`90`)، `2021-04-20 02:00–04:30` (`150`)، و`2021-04-25 04:01–08:45` (`284`). لا OHLCV ولا previous-close ولا forward fill فيها.

DatasetRelease: `95e04adb076be05eba0a970aa0978f1a4d1f41ad3caf04e9cd5859dd408ac099`. Catalog: `d91ee22e04e823a5fa45dc7cfe7f5d6246e65c5a53a2722665643f2dca269ea0` بعدد `304596` Bar حقيقية/مشتقة من trades رسمية فقط.
