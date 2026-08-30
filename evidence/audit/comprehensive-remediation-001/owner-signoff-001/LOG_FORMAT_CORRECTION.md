# Acceptance log-format correction

GitHub's PR-range `git diff --check` exposed a terminal blank line emitted when
an acceptance command produced no stdout or stderr. The generator now omits
that blank line and has a regression test.

Only logs 16 and 19 in the two Owner sign-off acceptance directories were
affected. Their command metadata and empty captured output did not change. The
log hashes and enclosing acceptance identities were recomputed after removing
the formatting-only line:

- failed attempt: original commit `84a9b4ed6f616304d095d7e492d06ef2baa1530d`,
  old identity `fb191717b4723eed329db695e6e52032134f7c94528d4156321611aff7dee171`,
  corrected identity `dab7df1e322802fd0021cb6bd4cfb68bd74a707892d025428dc36479a450812b`;
- passing attempt: original commit `7056c161851f3a51afaaa2e4a7e1af010a40895e`,
  old identity `4e528dbe1fc3f0cdc9df52ab71baceb938eb36dc443cbaab9e380297df8bf90d`,
  corrected identity `b77d2983bd49f92d3dfa59b997b1bf5caeb4b3798be16c18832873a75f92d39f`.

This is a transport-format correction only. It changes no SSOT contract,
financial result, checker decision, or historical report addition commit. The
original bytes remain available through the cited commits; no history was
rewritten.
