# Failed Owner sign-off acceptance attempt

Status: `FAIL` (preserved; not acceptance authority)

The acceptance output directory was created inside the Git worktree. After the
first full discovery phase wrote its log, the fresh-process and later
read-only research validators correctly rejected that new uncommitted path as
an `EVIDENCE_INCOMPLETE` SourceRevision mismatch. The failure therefore
demonstrates fail-closed behavior; it is not a passing result and is retained
without editing the generated logs or `acceptance.json`.

The replacement acceptance must write to a fresh directory outside the
repository, starting from a clean published commit. Completed output may be
copied into this additive evidence area only after the run has ended.
