# تقرير بيانات Perpetual الرسمي المجاني

## النتيجة

نجح إصدار BTCUSDT USDⓈ-M للنافذة `[2021-01-01T00:00:00Z, 2021-08-01T00:00:00Z)` من بيانات Binance الرسمية المجانية فقط.

- execution 1m: `305280/305280`.
- Mark 1m: `305280/305280`، missing/duplicate = `0`.
- Funding events: `636` archive event تطابقت exact مع `636` REST event؛ schedule identity `5226e413ae2f3be21a3519506bda3a7c095a6eaf3c76cad6436a27672d217b2b`.
- source substitutions: `0`؛ لم تُشتق Mark من execution أوindex أوpremium أوlast أوSpot.

خمسون Daily Mark delivery object بقيت 404 تاريخية. لكل منها أثبت REST وMonthly الرسميان coverage كاملة واتفاقًا exact؛ لذلك صُنفت route packaging غير المتاحة `REDUNDANT_OFFICIAL_DELIVERY_ROLE_UNAVAILABLE` ولم تُخفَ الـ404. وفي يوليو غابت `7200` دقيقة من Monthly، فحُسمت فقط باتفاق Daily وREST الرسميين.

النافذة الأصلية بقيت محجوبة بسبب `IRRECOVERABLE_OFFICIAL_MARK_DELIVERY_GAP` في `[2020-12-17T07:32:00Z, 2020-12-17T07:56:00Z)`؛ لم تُملأ. أول تحريك شهري ميكانيكي، N=1، أعطى النافذة الحالية المكتملة دون فحص Strategy أوPnL.

DatasetRelease: `9c8a5f679f38852119d1d2054b0711965f0a6d89d5dd0e0ebedaa8d8df66b503`. Catalog: `0b5a7e8abaf1af06dc375ef638037df7ee007d2382d9df7c8fe2e1a29cf64f4c`.
