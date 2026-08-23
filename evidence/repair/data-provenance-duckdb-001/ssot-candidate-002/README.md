# DATA_PROVENANCE_SSOT_CANDIDATE_002

هذه Candidate قابلة للتحقق فقط. لم تُعتمد، ولم تُعدّل root `SSOT.md`، ولا تسمح بتنفيذ إصلاح البيانات قبل اعتماد Owner مستقل للبايتات الدقيقة.

## الهوية

| العنصر | SHA-256 |
|---|---|
| Base root `SSOT.md` | `7bb2fc68d9b73b168a582d890a6f952fd0c4eb20fc0e31857903909f27dfaa8f` |
| Full Candidate SSOT | `f51971ed7a09b172c82ff5965f2899d2a302dd71a2af60eb7c920133567b4354` |
| Generated unified patch | `513e2c6ca3ce4047af469593911cf46979d83c38c0b7fa2a6f722bce752d73d8` |

`candidate_ssot_sha256` هو hash ملف `SSOT.data-provenance-candidate.md` الكامل، وليس hash الـpatch أوالـmanifest.

## طريقة الإنشاء والتحقق

1. نُسخت root `SSOT.md` byte-for-byte إلى ملف Candidate الكامل.
2. عُدّل ملف Candidate وحده.
3. وُلّدت unified patch آليًا من الملفين الكاملين، مع المسارين `a/SSOT.md` و`b/SSOT.md`. طُبّع تمثيل empty context records فقط بصورة حتمية كي تمر patch artifact نفسها فحص whitespace؛ لم تتغير أي diff record أخرى، ولا تحتاج patch إلى fuzz أوoffset أو`--unidiff-zero`.
4. طُبقت الـpatch داخل checkoutين نظيفين مستقلين عند HEAD `0d7694b26cccdd966ac0d44347dc8b7fc3626ec0`.
5. تطابق ناتج كل forward application byte-for-byte مع ملف Candidate الكامل.
6. أعيدت الـpatch في كل checkout، فعادت `SSOT.md` byte-for-byte إلى base.
7. لم يُستخدم fuzz أوmanual offset أوpartial application، ونجح `git diff --check` في الاتجاه الأمامي.

## النطاق الدلالي

التعديل محصور في عقود provenance والـcoverage والمصالحة بين مصادر Binance الرسمية، والاشتقاق الحتمي Decimal-exact من official trades، وإثبات no-trade، ودور DuckDB المشتق، وتصدير Bars المقبولة فقط إلى Nautilus.

لا تتغير هوية Nautilus أوRuntime Lock أوlatency أوFill/order/position/account/fee/funding/mark/PnL/portfolio semantics أوMarket Profiles أوresearch governance أوHoldout أوclaims أوOfficial Run offline boundary.

## Candidate 001

تبقى Candidate الأولى دون حذف أوتعديل، وتصنيفها هنا:

`SUPERSEDED_INVALID_PATCH`

## حالة الاعتماد

`PENDING_OWNER_ADOPTION`

لا تطبق الـpatch ولا تبدأ Data Repair حتى يصدر Owner adoption مستقل ودقيق.

## نص الاعتماد الجاهز

``` text
I adopt the exact amended SSOT.md bytes whose SHA-256 is:

f51971ed7a09b172c82ff5965f2899d2a302dd71a2af60eb7c920133567b4354

I authorize applying only:

evidence/repair/data-provenance-duckdb-001/ssot-candidate-002/SSOT.data-provenance-candidate.patch

to the exact base SSOT.md whose SHA-256 is:

7bb2fc68d9b73b168a582d890a6f952fd0c4eb20fc0e31857903909f27dfaa8f

No other SSOT changes are authorized.

Do not implement the data repair until this exact candidate is adopted.
```
