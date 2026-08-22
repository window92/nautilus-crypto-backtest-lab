# Superseded pre-prefix-hardening acceptance

This directory preserves the first executed Repair acceptance and its complete
manifest.  It is not the final acceptance: post-pass source review found that
the AUD-003/AUD-004 implementation did not yet compare a newly committed
replacement with every earlier first-parent Git anchor version, nor prove that
every historical anchor remained a prefix of the current Trial Journal and
Holdout history.

The finding was retained as
`REPAIR-HISTORY-COMMITTED-REPLACEMENT-REVIEW-001`.  Four additional adversarial
tests cover committed-descendant genesis replacement and longer self-consistent
replacement for both histories.  The root-level acceptance artifacts supersede
this directory only after the complete gate set is rerun.
