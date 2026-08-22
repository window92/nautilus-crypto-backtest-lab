# M0–M2 repair epoch 001

This additive epoch records the Owner-authorized repair of audit findings F-01
through F-05. Historical M0, M1, and M2 acceptance evidence and every frozen
raw Binance object remain immutable. No M3 or M4 execution belongs to this
epoch.

The regression tests were added and executed before production behavior was
changed. `golden-first-failures.json` and `golden-first-output.txt` preserve
that expected failing state.

The authoritative successful pre-commit suite is under
`acceptance-final-004/`, with final integrity output under
`verification-final-004/`: 106 unique executable test cases, 107 execution
occurrences, and one separately reported non-test acceptance check. The only
repeat is the native G09 test, executed once in the M0 regression set and once
in the M1 set, with canonical ownership assigned to M1.

Earlier `acceptance-final/`, `acceptance-final-002/`, `acceptance-final-003/`,
and root-level
finalization artifacts are retained as superseded repair history. The final
003 added explicit subcases for every prohibited mark-role substitution
and both Spot/Perpetual source-direction crossings; it does not change the
unique test ID count. Final 004 normalizes the runner's text framing to one
terminal newline so the complete staged diff passes Git's whitespace check.

Repaired release identities:

- Spot qualification: `2e0bdefe2b664821c559e95d35a3462c8354606076e1ec81d0ce6272f89b9a44`
- Perpetual qualification: `4b1f8c9032605d44728df29a0341df0a0a5d4a6b73ed0833e5c54a420c966b86`
- M3-ready Perpetual contract fixture: `749e654402021fafafe4a3269005c5ef1253c3743f04c35622726bca957a356b`

The M3-ready object is only a frozen M2 DatasetRelease for the authorized
eight-minute window. It is not a profile qualification, strategy run,
research run, or Official Run.
