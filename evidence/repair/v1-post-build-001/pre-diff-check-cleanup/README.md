# Superseded pre-diff-check-cleanup acceptance

This directory preserves the third complete executed Repair acceptance and its
manifest.  It is superseded because the subsequent full staged-diff audit found
one extra blank line at EOF in `tests/adversarial/__init__.py`, which caused
`git diff --cached --check` to report a formatting error.  No test or financial
result failed, but the required Git gate was not clean.

The blank line was removed without changing a test or expected result.  The
root-level evidence becomes authoritative only after every gate is rerun from a
new clean full-history candidate.
