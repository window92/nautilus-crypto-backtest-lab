# M0 fixtures

`spot-lab-run-config.json` is a non-executable downstream schema fixture. Its
Dataset Release and StrategySpec identities are synthetic fixed hashes; M0 never
resolves them, loads market data, or starts a strategy. M1–M2 must not treat this
fixture as qualified execution or data evidence.

`canonical-json.expected.json` is an independently written expected byte string
for the canonical JSON/hash test.

`source-revision.json` is a downstream contract fixture for separate Git Source
Revision evidence. It records the clean adopted baseline only; it is not Run
evidence and does not execute or authorize M1.
