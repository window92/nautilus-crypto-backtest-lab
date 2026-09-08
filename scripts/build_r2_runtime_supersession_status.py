#!/usr/bin/env python3
"""Build the closed additive registry for R2 runtime-superseded Run bytes."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from crypto_lab.git_identity import require_repository_root
from crypto_lab.result_status import (
    HistoricalCopyRole,
    HistoricalResultClass,
    R2_AUDITED_BASELINE_COMMIT,
    R2_RUNTIME_SUPERSEDED_RESULTS,
    R2_RUNTIME_SUPERSESSION_AUTHORITY,
    build_runtime_supersession_record_v3,
    build_runtime_supersession_registry_v3,
)


OUTPUT_RELATIVE_PATH = Path(
    "evidence/audit/adversarial-remediation-002/"
    "runtime-authority-supersession-status.json",
)
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")

# These identities were frozen from the immutable retry-002 through retry-005
# terminal bytes, the completed Spot retry-006 bytes, and the completed
# retry-007 Spot plus Perpetual Benchmark bytes.  Failed Perpetual trials
# remain failed evidence, not superseded results.
# Re-running the builder cannot silently bless modified evidence merely by
# computing new hashes.
EXPECTED_RUNTIME_SUPERSESSION_EVIDENCE_HASHES: dict[str, dict[str, str]] = {
    "runs/adversarial-remediation-002-retry-002-spot-benchmark-run-7da743fdaa06": {
        "component_validation.json": "7cee36205bb968d5b89c7a209cb7eacbeb17a933cf09b8b4417340ae6545c1cf",
        "evidence_manifest.json": "671c5c78aea67f6dc8a074082959b3a4def3cac792ff3dcd55a9d43c767c235e",
        "official_seal.json": "edad446a49f4c27fbf1bbe7ad56e4cd156720534f3825dda7a7177dadb7f7ddd",
        "runtime_identity.json": "8b89e215d9173b6cfd73f3e3bbe04e47437464550129a477745f46fb526b40e2",
        "source_revision.json": "6c10c07370e1e77581848f8fdb4fb8da12ce7cc9eb09314a7379bdddfbd72c02",
        "status.json": "d93c9c49cb2221ecff3b1c19b219f12f4379481013688af9736c1389206a6a79",
    },
    "runs/adversarial-remediation-002-retry-002-spot-candidate-a-run-e1cacf032f78": {
        "component_validation.json": "ce6d09830088f4a147df596866efb6936b9aaec5fb2397b23cdcf9fd57488b62",
        "evidence_manifest.json": "b370070092b12d4b33ba0ce4f9b80c1260eba137f95bd3341ba033ce8bcc6544",
        "official_seal.json": "0f447079d7ce210e3b7d57a81224b9c31b3d8887d7afe8138422bbfb610b87c7",
        "runtime_identity.json": "8b89e215d9173b6cfd73f3e3bbe04e47437464550129a477745f46fb526b40e2",
        "source_revision.json": "f0d06413b57f49b6119f42c3a72ae56415634ff2d1e77d5d166d9985187c580e",
        "status.json": "3c0e63ad047c439b1598bea93b23fd4b5c5fda2c7720774d498d565df541a732",
    },
    "runs/adversarial-remediation-002-retry-002-spot-candidate-b-run-9bbdbc35e204": {
        "component_validation.json": "6522c88ea6f39916bf77258ed7eb1d59b65b5ea3d17570f9cb0926d8d246bcc8",
        "evidence_manifest.json": "5b2b68e6893f367ae2c0e34289e7ae27c6b611e5621726b47106027ffb0606b3",
        "official_seal.json": "1cf74016945a6ff3490e300ea2a0edc40633af6b3914bf7dc0a4b963a797879e",
        "runtime_identity.json": "8b89e215d9173b6cfd73f3e3bbe04e47437464550129a477745f46fb526b40e2",
        "source_revision.json": "1a81aa4650ace2a527cf7578504c54669ebe14ad468f1bbb91cf7e0f91c07da4",
        "status.json": "0436874012cf946365d37ec5cb4dc0080127fe4f6fcd4a37673c0f0938de0ea3",
    },
    "runs/adversarial-remediation-002-retry-003-spot-benchmark-run-f28ac747c930": {
        "component_validation.json": "368792899e5e8c2d4795404f4814288fce118c7d7c326b6932fee262587759be",
        "evidence_manifest.json": "0d194d28e5533783ceb95ab5e9cb0ba75f345a2df8aa3d3a22d2c384e9e3dafe",
        "official_seal.json": "46fd98d099d3c8d0dc6be35a05d84c7c64a6c5f1ac1050762b89c43d38640c43",
        "runtime_identity.json": "a56e465c7506ff4456c3d4e7aed9499564bf49fd4f4b0a6bedb93cbd7c3826a2",
        "source_revision.json": "55d05c39aea5a2324280a95d35799ada7039936d15765b3371fe98b4fe49d3fb",
        "status.json": "80d7918b4992173857057ded913d96f13c01597914d4866fc1fc3f5e73bd5bb3",
    },
    "runs/adversarial-remediation-002-retry-004-spot-benchmark-run-524a71ec1f23": {
        "component_validation.json": "222e1adcdf9443cbd711ac1c39aaea8e55f65bd3f0014065cacaa5f47e7e286a",
        "evidence_manifest.json": "8ea36b3b68dbe783306c6cbfd25c3ba09dfca4de6412bfbaad5758d6f47e39b6",
        "official_seal.json": "eb8bac5f3269feef8d034c8046fab1a6045e6c5ceb2a3925913e53e914f667c1",
        "runtime_identity.json": "954a6ad4c20de481cb20f8c851388da76c72bac48cb26755dab3fc26e1895146",
        "source_revision.json": "e7a80fa0e0b0c3cb8b609df987b7bef5ef2f79f2dc5e398e0b5e706c21227889",
        "status.json": "621e64de2138d6901379f40c7d155122a9d9c2e6533db2d3929d3ccb3cb771ab",
    },
    (
        "runs/replays/adversarial-remediation-002-retry-002-spot-benchmark-"
        "buy-and-hold-1x-development/adversarial-remediation-002-retry-002-"
        "spot-benchmark-run-7da743fdaa06"
    ): {
        "component_validation.json": "7cee36205bb968d5b89c7a209cb7eacbeb17a933cf09b8b4417340ae6545c1cf",
        "evidence_manifest.json": "d681ca4c70edd0c9cdbfea5455ab7b04d4485a80b9b68333adaaf9efd363b81b",
        "official_seal.json": "7001d1d5e97ecd48333965872f0226731ded329f39dc5649d09764b5c45ec459",
        "runtime_identity.json": "8b89e215d9173b6cfd73f3e3bbe04e47437464550129a477745f46fb526b40e2",
        "source_revision.json": "b1775fa18adc7539c1cdf3a70a4b873307fda7af92aa6ba223db528a91e2fdb9",
        "status.json": "5a2790cb29595a5d565611d9e540b9cf999685bba71f85d3776647414cccdbe2",
    },
    (
        "runs/replays/adversarial-remediation-002-retry-002-spot-candidate-a-"
        "development/adversarial-remediation-002-retry-002-spot-candidate-a-"
        "run-e1cacf032f78"
    ): {
        "component_validation.json": "ce6d09830088f4a147df596866efb6936b9aaec5fb2397b23cdcf9fd57488b62",
        "evidence_manifest.json": "e991c9d4fcf6e9de148f0e87b6fd1ab7d503a5b6484ff326019eb41c2f0d21f5",
        "official_seal.json": "fc736c77f273a1277170a7c2bc011973773f915912a5ada34f7456f9c449b2b0",
        "runtime_identity.json": "8b89e215d9173b6cfd73f3e3bbe04e47437464550129a477745f46fb526b40e2",
        "source_revision.json": "49c807e52b919291072b9296d907fa624f44bbe633ad08b189db8206a9763f22",
        "status.json": "0907d15c9550bf2f91d2900c19a436f74cededb06790bffa0c38605bd496ceb2",
    },
    (
        "runs/replays/adversarial-remediation-002-retry-002-spot-candidate-b-"
        "development/adversarial-remediation-002-retry-002-spot-candidate-b-"
        "run-9bbdbc35e204"
    ): {
        "component_validation.json": "6522c88ea6f39916bf77258ed7eb1d59b65b5ea3d17570f9cb0926d8d246bcc8",
        "evidence_manifest.json": "978bf5e5a592d3a634e8f6b62308201804d32652d0cb3704bde60ab7d06b0421",
        "official_seal.json": "d19c7aa11f0ca2c9ecc2eeabf9900dd9dc89ab4924fd1c9c4b79e6152e4d23a6",
        "runtime_identity.json": "8b89e215d9173b6cfd73f3e3bbe04e47437464550129a477745f46fb526b40e2",
        "source_revision.json": "156382f423876b0bb43029bef6a818a2bdc45173cb8abd5a97acd79243f6fd63",
        "status.json": "78c470b1a0d60034438c48b25fcf025b8fc302cc54cc1ca810bdfe22fcf485a8",
    },
    (
        "runs/replays/adversarial-remediation-002-retry-003-spot-benchmark-"
        "buy-and-hold-1x-development/adversarial-remediation-002-retry-003-"
        "spot-benchmark-run-f28ac747c930"
    ): {
        "component_validation.json": "368792899e5e8c2d4795404f4814288fce118c7d7c326b6932fee262587759be",
        "evidence_manifest.json": "17c135424a3100a6f0893c53b2604473661ac0fef4ca1af29022f6b272001b86",
        "official_seal.json": "02ad615863589268767fa3211e6b0392c0a32d62d2810e76f5ba2cdbbf31f786",
        "runtime_identity.json": "a56e465c7506ff4456c3d4e7aed9499564bf49fd4f4b0a6bedb93cbd7c3826a2",
        "source_revision.json": "2afbacf05e04b44d49932882df563f6c2d07553ee722febb3d4f2eb0e1c88b7a",
        "status.json": "318d3be55781bc1b9e1b58d9c28447c5fdc5eae96fac611fcb3772442b612184",
    },
    (
        "runs/replays/adversarial-remediation-002-retry-004-spot-benchmark-"
        "buy-and-hold-1x-development/adversarial-remediation-002-retry-004-"
        "spot-benchmark-run-524a71ec1f23"
    ): {
        "component_validation.json": "222e1adcdf9443cbd711ac1c39aaea8e55f65bd3f0014065cacaa5f47e7e286a",
        "evidence_manifest.json": "aa5c4a59086f7f04d69b946bdb90a42d2da6e3b6266d2ab018097db02031942a",
        "official_seal.json": "0a2dd05e70d825ab0766a3f327fa57961330e7788071f3983708ccc61215b6d4",
        "runtime_identity.json": "954a6ad4c20de481cb20f8c851388da76c72bac48cb26755dab3fc26e1895146",
        "source_revision.json": "b98acfc41aa7491439c41a78aadc8c3178e3a8cf405ef14a6a8cdf8323927f7b",
        "status.json": "e5fc2295289f33e1d7baaa4a6bddcc366197a7a552e966115728f1dc33738257",
    },
    "runs/adversarial-remediation-002-retry-005-spot-benchmark-run-2c31e21fea1f": {
        "component_validation.json": "0177379ff13bf6dd5c47ace2eeb412870ad7236a08d420ff7becf6b032b4b3c6",
        "evidence_manifest.json": "20620a299f2946c3342d44a7fb2dc19ffafdef0224dd84c01b252abde51137f5",
        "official_seal.json": "cb2117fdbae0abe144a4b9f8bc8f5edbfc58e22a491237b0cbbeabf823038979",
        "runtime_identity.json": "7c7f7796c96c9e18ed9496bd72b9e1b0657e5f74f3b74539db4f4c0e0c1945e3",
        "source_revision.json": "2e931139cbc427dc779e3a66e70806935f78f6ebe36f05d5ac2032d230e15e37",
        "status.json": "81152525afe81aca7bc0279cd1c488cfedcfdf4976bd87d798ced75c17717d34",
    },
    (
        "runs/replays/adversarial-remediation-002-retry-005-spot-benchmark-"
        "buy-and-hold-1x-development/adversarial-remediation-002-retry-005-"
        "spot-benchmark-run-2c31e21fea1f"
    ): {
        "component_validation.json": "0177379ff13bf6dd5c47ace2eeb412870ad7236a08d420ff7becf6b032b4b3c6",
        "evidence_manifest.json": "ecd17b879786ec7d444062d72cc96d46198f31dd98ebc69a7b14ffa8d17dd5e2",
        "official_seal.json": "8cfef22686e6db66299ab9a9bd82145cedfad413bcee5aba28c3f3468db4a6e9",
        "runtime_identity.json": "7c7f7796c96c9e18ed9496bd72b9e1b0657e5f74f3b74539db4f4c0e0c1945e3",
        "source_revision.json": "61b7cd15a84b59154e2f4ae6bd9bcffc8009e4172f1576ba5c961e14c59b279b",
        "status.json": "b5f53372046a794d743139f8de1e914995896c6e30f8092cd837bf754ef42228",
    },
    "runs/adversarial-remediation-002-retry-005-spot-candidate-a-run-c14c350c3c6c": {
        "component_validation.json": "49260169c85e1dd8b59245cd9c4161c6a259d2fbbac85b85ceeb5a46e93330c0",
        "evidence_manifest.json": "124903be138a752dbe882e2044daf6bb94c1dd8aa5a3a9b086f88ba93a0471a9",
        "official_seal.json": "dbd92fe8b4fe00e3a1ebec712459b89e9fa2b4e44eeac3a39435401dc900bf59",
        "runtime_identity.json": "7c7f7796c96c9e18ed9496bd72b9e1b0657e5f74f3b74539db4f4c0e0c1945e3",
        "source_revision.json": "faeeb14939f4608e37ccb21e67a7ee4b3304730482d47eac6c1e413b8495bbba",
        "status.json": "6ea829bd8c891bd29fe96d2a4a6d98ecf8746145d055106f4afc3c40a228b0ce",
    },
    (
        "runs/replays/adversarial-remediation-002-retry-005-spot-candidate-a-"
        "development/adversarial-remediation-002-retry-005-spot-candidate-a-"
        "run-c14c350c3c6c"
    ): {
        "component_validation.json": "49260169c85e1dd8b59245cd9c4161c6a259d2fbbac85b85ceeb5a46e93330c0",
        "evidence_manifest.json": "e09c4d24d3846ea61c0160c7ffc46c6e3b350984d73101a6b5925fad40f8ae24",
        "official_seal.json": "42f7c581c3d3f94bd1c2b2f8d13218b3234f5f5fe48e9d3b6f4b480695dbb6ca",
        "runtime_identity.json": "7c7f7796c96c9e18ed9496bd72b9e1b0657e5f74f3b74539db4f4c0e0c1945e3",
        "source_revision.json": "b0cabb526cd361fdc776cf33d6de3cb8314b9b2a31d3c42cdc26f4add4c2f85c",
        "status.json": "a9bbefeabd507047e5f9fb3f60a3ff90cff7ebd8a364997b38fd69a47e2ceac6",
    },
    "runs/adversarial-remediation-002-retry-005-spot-candidate-b-run-cdd40a577711": {
        "component_validation.json": "c84602d18b367fb4eb0d31dfa048e10d015ec63782f6d6fab3a1b596256ceb59",
        "evidence_manifest.json": "143c80569bf28405e6a3b66e898c567f890de02a26c1da077d53f2bd1a6d51f9",
        "official_seal.json": "ca9eece381c56186af7f317fce051ebbdf35ffe504481b0cac8b5d8f1abfcf23",
        "runtime_identity.json": "7c7f7796c96c9e18ed9496bd72b9e1b0657e5f74f3b74539db4f4c0e0c1945e3",
        "source_revision.json": "99a8737252ccf203dfcf9b448d49ef0ae0be88e5d73510c92169876447ceeeda",
        "status.json": "a84722f6aa96e7f60b6695a9e097ea5e39f7217abe35de95a5fdfca7babbbf0e",
    },
    (
        "runs/replays/adversarial-remediation-002-retry-005-spot-candidate-b-"
        "development/adversarial-remediation-002-retry-005-spot-candidate-b-"
        "run-cdd40a577711"
    ): {
        "component_validation.json": "c84602d18b367fb4eb0d31dfa048e10d015ec63782f6d6fab3a1b596256ceb59",
        "evidence_manifest.json": "98ea0da1b41bbb10662148b7086071b9bf9f43e9c82a8004f79100cef510062c",
        "official_seal.json": "f6bfea2402b15b23fd4536d33c0517e104d5def8ac0009938fc26b8ac49eb2cc",
        "runtime_identity.json": "7c7f7796c96c9e18ed9496bd72b9e1b0657e5f74f3b74539db4f4c0e0c1945e3",
        "source_revision.json": "6fd7ae16d006be605bfa7d18cddbf43382d8e7f65644d47291e649dde54da49f",
        "status.json": "07fbcac50504bde1e434b568df2297ad9d928c8befb6dd31c51ca246c76f7174",
    },
    (
        "runs/adversarial-remediation-002-retry-005-perpetual-benchmark-run-"
        "2a0ab6ee5579"
    ): {
        "component_validation.json": "fd3003c56605f2b4e59b0132e20f0bf1e5ee58ef9390d8bdfc7fb665ae6ed973",
        "evidence_manifest.json": "ef7af1a4e66321a63f831c46f8740a5f3491eabf7d817d8ebede759c19d31df6",
        "official_seal.json": "0095c9324c672444940c5d323a115cf366c28094ed51ebbf53efbd33d4cfaeb1",
        "runtime_identity.json": "7c7f7796c96c9e18ed9496bd72b9e1b0657e5f74f3b74539db4f4c0e0c1945e3",
        "source_revision.json": "7e5c092e6829f5935b8e999167cbf3de5a96204dfc80222921887ea117474ec8",
        "status.json": "9f09e00a2220b3484ec9dd29ae517988eb90eb065902ad326b2a886c599c6644",
    },
    (
        "runs/replays/adversarial-remediation-002-retry-005-perpetual-benchmark-"
        "buy-and-hold-1x-development/adversarial-remediation-002-retry-005-"
        "perpetual-benchmark-run-2a0ab6ee5579"
    ): {
        "component_validation.json": "fd3003c56605f2b4e59b0132e20f0bf1e5ee58ef9390d8bdfc7fb665ae6ed973",
        "evidence_manifest.json": "a4d487004596454e4d166df6b89e58ca4963e11f43678fe45b609c3335010bf2",
        "official_seal.json": "06bb304e7ed135a0b7596981e7fe8b8c1740dd9d5af175ba43d3b9683d3fbd92",
        "runtime_identity.json": "7c7f7796c96c9e18ed9496bd72b9e1b0657e5f74f3b74539db4f4c0e0c1945e3",
        "source_revision.json": "173103806ffe4e75966a42a94028ef1c8d448ac222de7fabb8e0f1eecf2c2572",
        "status.json": "3b612181e10ce80dc971e41ceb28f6e835164f8dc71a75a6850510a942a4b76a",
    },
    "runs/adversarial-remediation-002-retry-006-spot-benchmark-run-9602e7984645": {
        "component_validation.json": "621535b4b096665e0736f14e0d60fe01e09f90deca3d6b45e67c056fae628ec5",
        "evidence_manifest.json": "e950032b5ed1b6bf4cc5ec33bacc53677d8cc2461bccf40a06ee9ffe858e737d",
        "official_seal.json": "3c78626bbd115b99352fe6a3f5109f1b2b2d41cc0f5163c96c25d458cccda23b",
        "runtime_identity.json": "c180a96d274e4f63454ee3233e38cb0115942160cdab0ef1e4dafd5d561226de",
        "source_revision.json": "00b7c447141eef6d729500f2cf875f5f68efce039c30d754fa53459fbc1d2b17",
        "status.json": "6082b726f416d814c6afafd79780c22626d672b59e9cf4f70efa98ed9951173d",
    },
    (
        "runs/replays/adversarial-remediation-002-retry-006-spot-benchmark-"
        "buy-and-hold-1x-development/adversarial-remediation-002-retry-006-"
        "spot-benchmark-run-9602e7984645"
    ): {
        "component_validation.json": "621535b4b096665e0736f14e0d60fe01e09f90deca3d6b45e67c056fae628ec5",
        "evidence_manifest.json": "544073b12493a5cef620c20eb4b095d445b5fdce21e84b9ca5d41ddf5e630cb2",
        "official_seal.json": "c188f885263b6786ed8322400fe5ac5711ee5a3b6da7a93e51aebcff82eff4a8",
        "runtime_identity.json": "c180a96d274e4f63454ee3233e38cb0115942160cdab0ef1e4dafd5d561226de",
        "source_revision.json": "5d2a8b149726b556538bb74a58434fb14ee532c513b170861e6278397c9d255d",
        "status.json": "184d4b7009941e96e29c02114042e3cba66045f5d469a0249090de775e8e027e",
    },
    "runs/adversarial-remediation-002-retry-006-spot-candidate-a-run-1a928b3db2d3": {
        "component_validation.json": "e5c9572e4aa9b288bdff291a2f680774b528663158f15bae6c203b5a486ed7ea",
        "evidence_manifest.json": "e1f1ba7c3dd591e32cff5fcbad034fb60ffd9cb42d52fff64d73c43153fa85cd",
        "official_seal.json": "24a18c3b51b8f3fd10d1fe5f9a7c7213d97f8448450cc0c99c68f8c369032389",
        "runtime_identity.json": "c180a96d274e4f63454ee3233e38cb0115942160cdab0ef1e4dafd5d561226de",
        "source_revision.json": "8605eb2817c566d340d92afc79fc6468fb64015aac23b1b9f3504fba175ee220",
        "status.json": "77bf09cc1fd109e1c9810e0cc6a9c6f97623d588b7baade99905872c0633abc2",
    },
    (
        "runs/replays/adversarial-remediation-002-retry-006-spot-candidate-a-"
        "development/adversarial-remediation-002-retry-006-spot-candidate-a-"
        "run-1a928b3db2d3"
    ): {
        "component_validation.json": "e5c9572e4aa9b288bdff291a2f680774b528663158f15bae6c203b5a486ed7ea",
        "evidence_manifest.json": "ccd2b6a7bb889bc218100a4ee1466dd6145eb3b95d002d92e98ba0171b86fd10",
        "official_seal.json": "40128c9e3523894647596a1916751b22ec7c917948fc2473ed6819bd19dd5eff",
        "runtime_identity.json": "c180a96d274e4f63454ee3233e38cb0115942160cdab0ef1e4dafd5d561226de",
        "source_revision.json": "bf96d489ff10d0675eb9040679c8fff8f5e5f6cfeab9db33971b8c124dcdfb17",
        "status.json": "0dd2dee5aec65b15e5e374c44b64ab6b097ba3bb5a82c73b93f0a51167644c31",
    },
    "runs/adversarial-remediation-002-retry-006-spot-candidate-b-run-c5ea2b43962f": {
        "component_validation.json": "e97baa8c3969cf9dd467ceea2b1b3f6392739d17343423e8421953a4eaa8dafa",
        "evidence_manifest.json": "f86c05bd518e5cfcf3c30d26ea5708e4f8ee653064b460a78c4f0a29da5e338e",
        "official_seal.json": "ccce5d8086e897f835b4fb6941706cefc7321b2bdf382d730325ab5ee80c2cd6",
        "runtime_identity.json": "c180a96d274e4f63454ee3233e38cb0115942160cdab0ef1e4dafd5d561226de",
        "source_revision.json": "a5cab71fbed0c858555c234f4988fd285de499c954925b9973ec5a9a24a81474",
        "status.json": "28bb7cc4715027f3a9bba54aaba16e0118b29926b27da85a4f0d406b2fec1366",
    },
    (
        "runs/replays/adversarial-remediation-002-retry-006-spot-candidate-b-"
        "development/adversarial-remediation-002-retry-006-spot-candidate-b-"
        "run-c5ea2b43962f"
    ): {
        "component_validation.json": "e97baa8c3969cf9dd467ceea2b1b3f6392739d17343423e8421953a4eaa8dafa",
        "evidence_manifest.json": "6fc9ba6d43fda297bf54465011ce70f217a212f8fe8bac2e118ff66c9eccdb10",
        "official_seal.json": "0b9ed5cf50dc7eae2ebc48b650bc37f5f901cc6ee43a7ebe2a15899efd43109f",
        "runtime_identity.json": "c180a96d274e4f63454ee3233e38cb0115942160cdab0ef1e4dafd5d561226de",
        "source_revision.json": "16ed32e6b68ab59c3d8b6ed29c265ec9824267ee641504eefe0bd6e0e9941c40",
        "status.json": "bfc62467af4b97e6f696fba5ac9057e65a7f4ef6c8f66c4b74e1bce64dea73c4",
    },
    (
        "runs/adversarial-remediation-002-retry-007-perpetual-benchmark-run-"
        "df8caf74b27d"
    ): {
        "component_validation.json": "bb00b3928ecf858353d93aed77056ffe801b3ebd58be84758883b5cfba44110c",
        "evidence_manifest.json": "f2742be90a6c9c2308e33ed534fca0aa1570ebfdba7c8e2a8c1dbf5f6c12ffa9",
        "official_seal.json": "7b82b9fadcae041582aeca6602d644f04eb31b94acae7219e918415ac116ec22",
        "runtime_identity.json": "2cb723a7f2d4cd87f096f241799dabd99584503fa08cf1614cc3e3562d151fb8",
        "source_revision.json": "380d2585925c1ea443cd6f3af5dd257ed6c3c59b3a6474a749e15181b6c7cbd9",
        "status.json": "da540d574d035acd446ce9ab3451b91c4b1965d0a1243f67e91e757a053bec24",
    },
    (
        "runs/replays/adversarial-remediation-002-retry-007-perpetual-benchmark-"
        "buy-and-hold-1x-development/adversarial-remediation-002-retry-007-"
        "perpetual-benchmark-run-df8caf74b27d"
    ): {
        "component_validation.json": "bb00b3928ecf858353d93aed77056ffe801b3ebd58be84758883b5cfba44110c",
        "evidence_manifest.json": "6dc0b97e833bc0bd7522f4ab017b5f73758cbf16e6b7f63ff636a01eb4e7b62d",
        "official_seal.json": "5ed645250230efc87df30ea69e9c0f1a11aaaca62886866f58407e4fc4745b2c",
        "runtime_identity.json": "2cb723a7f2d4cd87f096f241799dabd99584503fa08cf1614cc3e3562d151fb8",
        "source_revision.json": "48ae6de37c08e99e1a667c9ca50a510493e92ee9debc39a67f9c7729d8f570a6",
        "status.json": "10b2a6ed4c236b225304b4d3b207d0baad58677d1b0248b58b0d9860dc50df10",
    },
    "runs/adversarial-remediation-002-retry-007-spot-benchmark-run-46d5ef9362e7": {
        "component_validation.json": "38250a7621d24a30cfce13bb6a594646b920b182fa5f530aae2d5f184ac8fd46",
        "evidence_manifest.json": "541d481a45732ecf6382dac6e0e8eb5e519a0af88a8f3ee92c189d1bd2ee10cc",
        "official_seal.json": "dc584404ac679f5c80f0dd1806625e1fa948cc199a6e10648563ba92083bb0d9",
        "runtime_identity.json": "2cb723a7f2d4cd87f096f241799dabd99584503fa08cf1614cc3e3562d151fb8",
        "source_revision.json": "e23643f14a6bdf4bdd92afbdd571458d505d9d9646a6faa703f70f252a61f112",
        "status.json": "5663f1deb45ff57fe9b13e142a17b07b7ac5cb7813ac7644ffae38c1713fc3f8",
    },
    (
        "runs/replays/adversarial-remediation-002-retry-007-spot-benchmark-"
        "buy-and-hold-1x-development/adversarial-remediation-002-retry-007-"
        "spot-benchmark-run-46d5ef9362e7"
    ): {
        "component_validation.json": "38250a7621d24a30cfce13bb6a594646b920b182fa5f530aae2d5f184ac8fd46",
        "evidence_manifest.json": "e6a6985ead6a0b6619f328bf71b417211817b0bf5c9f8bad5cb7c828cd4cad1a",
        "official_seal.json": "03fa2e36832f10a2e327a66dfb32b477a60ee5c41f9214dfc09f91f1b521f7c6",
        "runtime_identity.json": "2cb723a7f2d4cd87f096f241799dabd99584503fa08cf1614cc3e3562d151fb8",
        "source_revision.json": "5cfb8f1ea16a319cc0145e1b7229bc7d71b08e503bf05b5c79c526a26e456b16",
        "status.json": "72b93364b15ca623c6f6301023bd038ec1c8309b02f8fd8002cb04c63b819419",
    },
    "runs/adversarial-remediation-002-retry-007-spot-candidate-a-run-80168115684f": {
        "component_validation.json": "b541836b2b4eafa481115ca0e922c331104cd335a25212c5f4a98501423de952",
        "evidence_manifest.json": "c03bdb0ba7c2bfb8889d537d29e850ff5f4c14d40ab92f6bda402df887307b2e",
        "official_seal.json": "0c8af53e7c7285c8d5af90dfd52489c3e09f3e882f8202f9b728da82f78c4b2e",
        "runtime_identity.json": "2cb723a7f2d4cd87f096f241799dabd99584503fa08cf1614cc3e3562d151fb8",
        "source_revision.json": "2ce5bddab1f4619b0b239b446aa5b0c83c6cd498d3a3cf2ecb4f852c2328b382",
        "status.json": "2c4e2477a0cd0c280515d95dbd02f34166beb8a1094e89bd355892749b1318f7",
    },
    (
        "runs/replays/adversarial-remediation-002-retry-007-spot-candidate-a-"
        "development/adversarial-remediation-002-retry-007-spot-candidate-a-"
        "run-80168115684f"
    ): {
        "component_validation.json": "b541836b2b4eafa481115ca0e922c331104cd335a25212c5f4a98501423de952",
        "evidence_manifest.json": "1276c8b77851d74e895d9b6a35a2ad7372f0a7837e0f03b4273f5d9a41dbf12d",
        "official_seal.json": "80d8ac3e9597dbdad950d1f71a620e0b5c4667a9a86dceac32848e61ab324b48",
        "runtime_identity.json": "2cb723a7f2d4cd87f096f241799dabd99584503fa08cf1614cc3e3562d151fb8",
        "source_revision.json": "a2bf09682d5905d6bb69bc5e00e13c3a27d32a85e4a3b9520ae6d44071e1f54d",
        "status.json": "27cb1d64f1d8621546d89bf581b813909e2e37359f8f2ee8b4ad82114940dcfc",
    },
    "runs/adversarial-remediation-002-retry-007-spot-candidate-b-run-4a9e4ae97945": {
        "component_validation.json": "bbbdf13b20d981833617d01ad61027bd7d4796f2d34aab2540cecb5caefae0c1",
        "evidence_manifest.json": "35a122ef31756f11b218dbee7c2e6ecb6ec3f349138fac240331c4777fc0a02b",
        "official_seal.json": "599194d0f2b4dddb751f2e00870ddf95825da8c51a5afc5663ae30ab16542899",
        "runtime_identity.json": "2cb723a7f2d4cd87f096f241799dabd99584503fa08cf1614cc3e3562d151fb8",
        "source_revision.json": "cef9c82969e4f88650ebdb6d5cf4970cdff26ee8d0b10560c6c611a2dc8f7ca8",
        "status.json": "1d305de454aa5cad4db2047c581ad64dcb164a3618553d4b8a5ff2fce3d1e4fe",
    },
    (
        "runs/replays/adversarial-remediation-002-retry-007-spot-candidate-b-"
        "development/adversarial-remediation-002-retry-007-spot-candidate-b-"
        "run-4a9e4ae97945"
    ): {
        "component_validation.json": "bbbdf13b20d981833617d01ad61027bd7d4796f2d34aab2540cecb5caefae0c1",
        "evidence_manifest.json": "6ca577f84429731d2310d8cb541830a3cde408a47b4685041a81be7fe5a6cde2",
        "official_seal.json": "125329143b1013d5d4378a3772e0a9ea326984ba7e9141a9bb6fefbdf920ca51",
        "runtime_identity.json": "2cb723a7f2d4cd87f096f241799dabd99584503fa08cf1614cc3e3562d151fb8",
        "source_revision.json": "ade6e24d623c8145602921f321dd4fb7753529b91ed4589a0d32129e6bf79dd2",
        "status.json": "76722161561d7eae16e670edc542bd5c41ceeea5de0e20ea898ab8517b702156",
    },
}


# Frozen from the verified complete retry-015 Primary/Replay bytes.
EXPECTED_RUNTIME_SUPERSESSION_EVIDENCE_HASHES.update({
    "runs/adversarial-remediation-002-retry-015-spot-benchmark-run-81b0c9a95088": {
        "component_validation.json": "9a3ead33b8c28869f13f999f7fed0b7cb932eef834e1e71948aeb9b80a373b9c",
        "evidence_manifest.json": "57d5805ca0de7fce927b57aabbed253b63dac5904a0262b36abcc7ddc321bf13",
        "official_seal.json": "5cba1d95ea4994c444a4e3a58dd19feb06e7933d0b7babc9e34cb0509543a9b7",
        "runtime_identity.json": "211a12bfcd71fdb2c1cee6283c16bedd0e93490b182a98e10df51b24c4062c13",
        "source_revision.json": "2e08ae5b0d2e0ee83d7f117b7376e8d59ab4e262de02873669a9f5279020eba3",
        "status.json": "21c2ed2f20bd4b7f0cb702158f5f4906efcf1a2fdf8b013a0bb4e3fa145c7053"
    },
    "runs/replays/adversarial-remediation-002-retry-015-spot-benchmark-buy-and-hold-1x-development/adversarial-remediation-002-retry-015-spot-benchmark-run-81b0c9a95088": {
        "component_validation.json": "9a3ead33b8c28869f13f999f7fed0b7cb932eef834e1e71948aeb9b80a373b9c",
        "evidence_manifest.json": "ab2e78c9f1a8c530975691e2c1e803f61c27fdf1cc5c63c645eac08626e79ea7",
        "official_seal.json": "966114fe95ffd5a4034b53ec2192158b87c4a75a9e3c980139127de8ce899678",
        "runtime_identity.json": "211a12bfcd71fdb2c1cee6283c16bedd0e93490b182a98e10df51b24c4062c13",
        "source_revision.json": "c3f56873a735cdbf6aa6b814387709923392f08790b765b3af61b61581d38194",
        "status.json": "4f10d94c76a7df485d0cadef43185674b84427891282bb1c7628f0f4a5cbda7f"
    },
    "runs/adversarial-remediation-002-retry-015-spot-candidate-a-run-a55e69cccd67": {
        "component_validation.json": "77579c11e1bf7f04825079c0daa554bc8ee4238e33305c265a2d713e63185334",
        "evidence_manifest.json": "52b0064bba278da13be374063ca9b914011d81859f7d5c2158f557bd1ffc533f",
        "official_seal.json": "7f381c677a189428e208a29c33289ba9af5141929d037fba38673ec00167273b",
        "runtime_identity.json": "211a12bfcd71fdb2c1cee6283c16bedd0e93490b182a98e10df51b24c4062c13",
        "source_revision.json": "bd461e630868d6efe79de35548a0beb00e0a213d41ef40405dfc843557d2a49c",
        "status.json": "ce9b0d5a1b7ae0a9233a517a34d8b19a857a525d52a3cf7c24be05a7e4522b6a"
    },
    "runs/replays/adversarial-remediation-002-retry-015-spot-candidate-a-development/adversarial-remediation-002-retry-015-spot-candidate-a-run-a55e69cccd67": {
        "component_validation.json": "77579c11e1bf7f04825079c0daa554bc8ee4238e33305c265a2d713e63185334",
        "evidence_manifest.json": "17d9dad5ba21fdf8d8529eec7d1beb50b36f971d6972550764c2a358d2c29619",
        "official_seal.json": "471b431bf418bb8bdf49bf3ffb7394a74ea44fbf46f7d022ca5c617b3f3ac548",
        "runtime_identity.json": "211a12bfcd71fdb2c1cee6283c16bedd0e93490b182a98e10df51b24c4062c13",
        "source_revision.json": "271300ca8617fd32d7c768fb721947564546699892c67351c83c3570e836fc63",
        "status.json": "8f091e015839d80d9c22fa496e47e379a9e4e4a9b90cf2c5ca7fe4a4988ebc22"
    },
    "runs/adversarial-remediation-002-retry-015-spot-candidate-b-run-885f230b33dc": {
        "component_validation.json": "c9ac0fe629f4b5d1533f29fc59ab1af0798875675958203a93c550a688898fae",
        "evidence_manifest.json": "cbab26e5af53f3b92e2a2ffc4a6baa7044cff97e22f90ecfe6834ce0ac37df8d",
        "official_seal.json": "07b79e2f599c8e1bff6aacb0e6356496b54e3e32d2a797dda49ca4535889066d",
        "runtime_identity.json": "211a12bfcd71fdb2c1cee6283c16bedd0e93490b182a98e10df51b24c4062c13",
        "source_revision.json": "964b2b541f57c21ac73268d0805ca11042d51130f9b3155f7db080373aab025f",
        "status.json": "54cafad5b5f79d309f7274a9abe23c4447f732c14ff277fdc25cec5e9bd5b374"
    },
    "runs/replays/adversarial-remediation-002-retry-015-spot-candidate-b-development/adversarial-remediation-002-retry-015-spot-candidate-b-run-885f230b33dc": {
        "component_validation.json": "c9ac0fe629f4b5d1533f29fc59ab1af0798875675958203a93c550a688898fae",
        "evidence_manifest.json": "1713f03739cbcd7979b741511585a898bebf08fc196b86e68c42f1e3c632db9b",
        "official_seal.json": "eefb742ce2b2d43ba4d74192de390ffff71023e72da24c37af4201e0447596bd",
        "runtime_identity.json": "211a12bfcd71fdb2c1cee6283c16bedd0e93490b182a98e10df51b24c4062c13",
        "source_revision.json": "3e550979e73def1c1b5137999bf386f2c19676953d1704a0ce827ec77b6d7d5a",
        "status.json": "377d52ea278edaf2319996d7dc13975df63ed05f542552eee548dfdb437f7ef9"
    },
    "runs/adversarial-remediation-002-retry-015-perpetual-benchmark-run-37ac036e9f58": {
        "component_validation.json": "0e127189cf6aa0f5e5c6065a36da52499df5f06638a2f57b744f3ec76bd5c19b",
        "evidence_manifest.json": "77132bb441cfe9dedddac759199d47900735b9587daefd01a8cbc1239cfbc1d7",
        "official_seal.json": "5c2ed8e22d9cf308f85d3ef0f0e3792b0f109a7081f7814bf531a065e0236c38",
        "runtime_identity.json": "211a12bfcd71fdb2c1cee6283c16bedd0e93490b182a98e10df51b24c4062c13",
        "source_revision.json": "6a7771a6bb0a2f6dbad010fe11c5ae70460232a3e3656a790af55d22d27a3f20",
        "status.json": "98c745a23131b9180fa06913f6e3af0515f8f82c1d09f252c7e6a6f7f7c7714b"
    },
    "runs/replays/adversarial-remediation-002-retry-015-perpetual-benchmark-buy-and-hold-1x-development/adversarial-remediation-002-retry-015-perpetual-benchmark-run-37ac036e9f58": {
        "component_validation.json": "0e127189cf6aa0f5e5c6065a36da52499df5f06638a2f57b744f3ec76bd5c19b",
        "evidence_manifest.json": "ba670d818eb988bb6b2fb9f6dfa9a777d26923df00fd10bce21514c2ca00a26c",
        "official_seal.json": "7b17f3663925492c82c86549766671c8c0961bf40cc0e9f1936c29a9edd47961",
        "runtime_identity.json": "211a12bfcd71fdb2c1cee6283c16bedd0e93490b182a98e10df51b24c4062c13",
        "source_revision.json": "381ebba9b0185607e13c982f4690b27950b7c7be31f6ad78be06562d4b70d737",
        "status.json": "97cfdec5677fb9986fb79fdd4853789f7036d7dcfb6c7b863956f4091fde7ca6"
    },
    "runs/adversarial-remediation-002-retry-015-perpetual-candidate-a-run-89d6480c4282": {
        "component_validation.json": "34da65d1c2ccaeb8c31cdf205d0d1e4c528289132ea8ef9da115e9ec3879c908",
        "evidence_manifest.json": "5c168308950f78ab643dbb28bad046827c4e428ea91a64216bbeb5cd41ac977b",
        "official_seal.json": "b714da5502752670d9a2ee7c20afe949d1dbc087aa097a909d87817eb60c55a3",
        "runtime_identity.json": "211a12bfcd71fdb2c1cee6283c16bedd0e93490b182a98e10df51b24c4062c13",
        "source_revision.json": "a812cc319e307bb3a995c8eba08fa16cd3d8cd577ac6246040c87b2e34bbd339",
        "status.json": "be1793ebc6bfdd284522e23cb8fc54f72b826001710566dc5452220cd972461c"
    },
    "runs/replays/adversarial-remediation-002-retry-015-perpetual-candidate-a-development/adversarial-remediation-002-retry-015-perpetual-candidate-a-run-89d6480c4282": {
        "component_validation.json": "34da65d1c2ccaeb8c31cdf205d0d1e4c528289132ea8ef9da115e9ec3879c908",
        "evidence_manifest.json": "78c1652627a7be730adb5a8d183401474b10b235f61021b04623c93c8cb7ab60",
        "official_seal.json": "1ae044fc42ee183e5573da7b61f0b066bed64708d5da2e0253dbcf821450b175",
        "runtime_identity.json": "211a12bfcd71fdb2c1cee6283c16bedd0e93490b182a98e10df51b24c4062c13",
        "source_revision.json": "b700cfc377fb9e0f11d8d44c4fabbcf1fe7dd65527225a30ab54155fc3d5b241",
        "status.json": "7c1fab7eb58cad72b27a295d3d848f81833d7896dd93342f33a518affdccfdd7"
    },
    "runs/adversarial-remediation-002-retry-015-perpetual-candidate-b-run-1b9ebe2c7557": {
        "component_validation.json": "7b8b5ec8764955c37204e5eecd6ebfc0250df000d7937572e378f1a49de79fda",
        "evidence_manifest.json": "0d49136e53c8ced832c46c9ac9a603627d00dda4c8b40c53440eec31eb76ece0",
        "official_seal.json": "4fc798f7b50a8b7fd270cc6a68c5550edb1a64671667a8d028d1c236d49aa891",
        "runtime_identity.json": "211a12bfcd71fdb2c1cee6283c16bedd0e93490b182a98e10df51b24c4062c13",
        "source_revision.json": "6212707bd7481681c03c15d86b8dac8609946dfa7018611d1560ffa21fdad3a2",
        "status.json": "a9176d1f0f7e5b7b137cb6e823d49024b872abd01d22d13ba5d163eb0e296a5d"
    },
    "runs/replays/adversarial-remediation-002-retry-015-perpetual-candidate-b-development/adversarial-remediation-002-retry-015-perpetual-candidate-b-run-1b9ebe2c7557": {
        "component_validation.json": "7b8b5ec8764955c37204e5eecd6ebfc0250df000d7937572e378f1a49de79fda",
        "evidence_manifest.json": "249a66d37d661cc9a14dca6d4683cda23d28d2a832fed523fac18ad9b5512ccb",
        "official_seal.json": "7d61c48cf722e4c10fc59ba68b51c443b0a40c5908a7471efe6261a405f7552f",
        "runtime_identity.json": "211a12bfcd71fdb2c1cee6283c16bedd0e93490b182a98e10df51b24c4062c13",
        "source_revision.json": "3e0a1b0e38547fd997d6ae08c1304afbe533cd85d9f65ce8cee37f6ab43cbbb6",
        "status.json": "d41ea93cdf305b93b22b5dcfd7ef7fa2fcf362f733541eae7e3ebc8b7882e6a5"
    }
})


EXPECTED_RUNTIME_SUPERSESSION_EVIDENCE_HASHES.update({
    "runs/adversarial-remediation-002-retry-016-spot-benchmark-run-0897cdbb1086": {
        "component_validation.json": "73f729ed809ec02b9437b2606dd4a2ca88769729e79c4b0b6d35d8f2a8fa7fc2",
        "evidence_manifest.json": "f20b7ba5005900726204db43b24fa2f6612657e7078f0e65d8ddac874f48010b",
        "official_seal.json": "7300720ab1ce6146514ab4616668b6fe6debe5cbbab74715b1efbce3dda1222a",
        "runtime_identity.json": "f8322646dfde3294127258b21253cc4608212678afe429943ecce97308e6e5fa",
        "source_revision.json": "00826f9e5d112b1b389651699108271772481529f38b19c14a67616d4d0c7ce4",
        "status.json": "95b827d488bbfb6a900b674bfd1d8dcf5ed2c7709a49d6695e99fd420a1381be"
    },
    "runs/replays/adversarial-remediation-002-retry-016-spot-benchmark-buy-and-hold-1x-development/adversarial-remediation-002-retry-016-spot-benchmark-run-0897cdbb1086": {
        "component_validation.json": "73f729ed809ec02b9437b2606dd4a2ca88769729e79c4b0b6d35d8f2a8fa7fc2",
        "evidence_manifest.json": "19b4ae1fc22118413dc5d051a0116cfe748f0f48f8d7630b0a193cc1532afb7d",
        "official_seal.json": "369b2f8dbd9c846047b3a4d5cdafd687015c19005045f6f57bc5a9f5a4214eac",
        "runtime_identity.json": "f8322646dfde3294127258b21253cc4608212678afe429943ecce97308e6e5fa",
        "source_revision.json": "7dabda1481febf56c729f8d7ebf35d31c0ed0200b950ab2efe0094f440aa34d9",
        "status.json": "08463f73ad5cf9afcaf49f4d21ae122d29ba6054f0f8e4ccf0180af255a50cf7"
    }
})


EXPECTED_RUNTIME_SUPERSESSION_EVIDENCE_HASHES.update({
    "runs/adversarial-remediation-002-retry-017-spot-benchmark-run-16147e079965": {
        "component_validation.json": "e4bc4330cbe3a7b1a657562a43bf7e4fb4bf270d13468883b5b5f275af42590a",
        "evidence_manifest.json": "fa12ada16d91704289c7d412236dd58e60e8198d7dc5db45360efbc5126e3a2b",
        "official_seal.json": "7fe0b418387c232140e5370df5e5d4b3663c60398d8e0291dac6ed12484e7b24",
        "runtime_identity.json": "7e3b3205e87b408ec91e6266f12cb0f4bb0c3e1a9b927a8a1b1d770f9d06558d",
        "source_revision.json": "81844a0bf4d2c2b4b892547d6dae050a35cc7082b7d81ca1cd720d79ed61a753",
        "status.json": "67b2266f00b35260d3a0214437c43403fc37061b5475abee1904ea93c5b84936"
    },
    "runs/replays/adversarial-remediation-002-retry-017-spot-benchmark-buy-and-hold-1x-development/adversarial-remediation-002-retry-017-spot-benchmark-run-16147e079965": {
        "component_validation.json": "e4bc4330cbe3a7b1a657562a43bf7e4fb4bf270d13468883b5b5f275af42590a",
        "evidence_manifest.json": "88273338dd31950e199cdcf5b5d15f088fa4c1468989ccac44b398e5d3fa26ce",
        "official_seal.json": "62d74ed8c7b54e69ff15c348fc68ff34103820a7f40d7d7f2047a08f1036473b",
        "runtime_identity.json": "7e3b3205e87b408ec91e6266f12cb0f4bb0c3e1a9b927a8a1b1d770f9d06558d",
        "source_revision.json": "337059730d79ff8c0b57c38023d0ea9218f13cae060c9097a6409c5cb045d705",
        "status.json": "1fd4cb2124a33d3474df6202375df7a0ce03d659f54ce15ba5c5424a4dcfcb54"
    },
    "runs/adversarial-remediation-002-retry-017-spot-candidate-a-run-9c834f598670": {
        "component_validation.json": "cd777c6b715e30d347a5988e86c835fdc7b7c98d4893b0996d83c05e27cf183c",
        "evidence_manifest.json": "8cd80c4f07a22b229dc99bcf484da0b7534ae7c95745e8272ddc388203e4ca2d",
        "official_seal.json": "c7e35c6fe15ba9582bfcc58f4c4d35ea5545168df067fb35389003f573919f4c",
        "runtime_identity.json": "7e3b3205e87b408ec91e6266f12cb0f4bb0c3e1a9b927a8a1b1d770f9d06558d",
        "source_revision.json": "e1be1baafe158a0c22aaeac9814a54439b9b6659559807cbe8d2a45b70e41862",
        "status.json": "2c06d39f541f8a3ba2b8993b127929237236830d3851523689def607dd0b5d1f"
    },
    "runs/replays/adversarial-remediation-002-retry-017-spot-candidate-a-development/adversarial-remediation-002-retry-017-spot-candidate-a-run-9c834f598670": {
        "component_validation.json": "cd777c6b715e30d347a5988e86c835fdc7b7c98d4893b0996d83c05e27cf183c",
        "evidence_manifest.json": "39e5552a750e1aed3d8fd4d790b6c9ec0485ab4973f0d4b18a2bc09912708bf4",
        "official_seal.json": "addf77a5fe808472f8e495922c25e2af07405961f591c733b079a2d7c937b77e",
        "runtime_identity.json": "7e3b3205e87b408ec91e6266f12cb0f4bb0c3e1a9b927a8a1b1d770f9d06558d",
        "source_revision.json": "42fb016fd6f03886b57b5a020bc5765bb07c9667e797f93d504e09f3fa61e46e",
        "status.json": "dc76dbd38fe144fe76de0b74389be7546fd18a4fa1ed17c2f37732471e554904"
    },
    "runs/adversarial-remediation-002-retry-017-spot-candidate-b-run-78a761838c6e": {
        "component_validation.json": "efda25295f3410660208ccfcbd3eabcc80254a8fcca0ab5825fe5459e3226ff9",
        "evidence_manifest.json": "b514462219115d9a08302ae4ae93e844ff4991c1a2e3716320c7a4cf790a7757",
        "official_seal.json": "24734bfe67a7c42c909b4d398bced59d882ea75be6d336a3a419b66809a8639a",
        "runtime_identity.json": "7e3b3205e87b408ec91e6266f12cb0f4bb0c3e1a9b927a8a1b1d770f9d06558d",
        "source_revision.json": "2dce321b3c23ed49c2bc08387429be88e12dd14a68adc95de4814e1266f0d5e2",
        "status.json": "c986a4d0af2a4deb2029f1306ed2c8b5dcb9a4a6dafcb9426db75c2948dfa3e3"
    },
    "runs/replays/adversarial-remediation-002-retry-017-spot-candidate-b-development/adversarial-remediation-002-retry-017-spot-candidate-b-run-78a761838c6e": {
        "component_validation.json": "efda25295f3410660208ccfcbd3eabcc80254a8fcca0ab5825fe5459e3226ff9",
        "evidence_manifest.json": "0e86e01ccc1192136c8e7bad530be861b7a76537846abe103fa2f2ef83f60890",
        "official_seal.json": "ab189e3f276a3cd992d4e088fff02affa890b85991475af82d3c7f96829dd81b",
        "runtime_identity.json": "7e3b3205e87b408ec91e6266f12cb0f4bb0c3e1a9b927a8a1b1d770f9d06558d",
        "source_revision.json": "79c97c923878604f1bcfcc9a0ea3717ed96e3d2702ea50351afe7ec900aad267",
        "status.json": "38e08ddc8df895e37ff1a1e23c9852be1d6aa2859f88120ba679c4b1f2ec6293"
    },
    "runs/adversarial-remediation-002-retry-017-perpetual-benchmark-run-0ecef1839e9e": {
        "component_validation.json": "3dee3f5eb4bbee05881eeaf982512af4b11b8b2161385d802563c997f2212193",
        "evidence_manifest.json": "4981ac4f90ac64d1f498963e667a1ecbb498261a61cfb9bf5ab4a6ce801030e8",
        "official_seal.json": "24e5f5b5a96be7bcf5402e6e2b214022f659d842ba2bbdbc0cdf93c26997b138",
        "runtime_identity.json": "7e3b3205e87b408ec91e6266f12cb0f4bb0c3e1a9b927a8a1b1d770f9d06558d",
        "source_revision.json": "ddb053f55f02362e3de29352e2e01d0cbbd4d9cbc21e97405e83ac2bde011a79",
        "status.json": "50a512c56bf477395d03163be883b137a08004ca40677ede92e7830a10c5092d"
    },
    "runs/replays/adversarial-remediation-002-retry-017-perpetual-benchmark-buy-and-hold-1x-development/adversarial-remediation-002-retry-017-perpetual-benchmark-run-0ecef1839e9e": {
        "component_validation.json": "3dee3f5eb4bbee05881eeaf982512af4b11b8b2161385d802563c997f2212193",
        "evidence_manifest.json": "a1d9312da0124d5e982e551623db65cae7c8ef3d4e13f356c24597375ae98de1",
        "official_seal.json": "59ed6e8ef78dbb295f06c88c24c40f211a92c9f80c6331a83d1755f28bd9b77f",
        "runtime_identity.json": "7e3b3205e87b408ec91e6266f12cb0f4bb0c3e1a9b927a8a1b1d770f9d06558d",
        "source_revision.json": "abdd3ec131ec0d47c35e44bdc558f92914d6fce3ff7210e70fe8c1cb952d198e",
        "status.json": "f40c891e0f85675f87d468143d5dd6603cc96559b75c1ff8046164c2dea99470"
    },
    "runs/adversarial-remediation-002-retry-017-perpetual-candidate-a-run-55681bcb7efc": {
        "component_validation.json": "6b8f41b69b39b70db6017932fbbe72e2880ee901977da1b278d1565cf291cf47",
        "evidence_manifest.json": "6b7d6c5f81cb89c4374b0861cbdbb4a77de7e66f61394ce21452094a24120baf",
        "official_seal.json": "496cf5579eb8e19c6297f4016030e2d407d5e7324027e1670965f601d7e3755b",
        "runtime_identity.json": "7e3b3205e87b408ec91e6266f12cb0f4bb0c3e1a9b927a8a1b1d770f9d06558d",
        "source_revision.json": "5cebd97b829aaedcb88dcfe3e19b30f29f588d0c81d730c3dd528c421224ace1",
        "status.json": "dafe5a2ac0cc5e34893920c9affedab3fadf2fc7c8c7855b9c2aa6ea020ed11f"
    },
    "runs/replays/adversarial-remediation-002-retry-017-perpetual-candidate-a-development/adversarial-remediation-002-retry-017-perpetual-candidate-a-run-55681bcb7efc": {
        "component_validation.json": "6b8f41b69b39b70db6017932fbbe72e2880ee901977da1b278d1565cf291cf47",
        "evidence_manifest.json": "cd9397b80d5a1f80e5f8c8f7fceb10505102287cf8a54c5c7a92c24906bd6ab6",
        "official_seal.json": "b287dbc74af3f4cb525891e935f94c8a9cf961642196a469aebfc436b81b0665",
        "runtime_identity.json": "7e3b3205e87b408ec91e6266f12cb0f4bb0c3e1a9b927a8a1b1d770f9d06558d",
        "source_revision.json": "55b4ae91439397e9f7e2abb5639582d93b26a1c637141b76af2da5274d015d05",
        "status.json": "73cadf28445ffe46186cfc97eb9872556b7d4928b60708a7e2e4c1e7a5484a5c"
    },
    "runs/adversarial-remediation-002-retry-017-perpetual-candidate-b-run-1d6744832d2c": {
        "component_validation.json": "70b246e3f2cd088a2fe25bc68399c8582cfb1c749e57beb5abf78baa35817e36",
        "evidence_manifest.json": "11b64258d57dbaf6ab180278f59da3034ea76d65b0ecf98e9bf0b5fdcde9dbbc",
        "official_seal.json": "773fd3dbd2e01773aa0995bd50f5a495966ad7a66952475d09100aeea7173958",
        "runtime_identity.json": "7e3b3205e87b408ec91e6266f12cb0f4bb0c3e1a9b927a8a1b1d770f9d06558d",
        "source_revision.json": "ac31f86b84b1ce0537d8080e157e2843ac4a9191f38fb23bd7f95adab28a7c6c",
        "status.json": "1387bf5e14167fceae5b575954d7b1a31fe4acd8bff08c3ee815556df37a883d"
    },
    "runs/replays/adversarial-remediation-002-retry-017-perpetual-candidate-b-development/adversarial-remediation-002-retry-017-perpetual-candidate-b-run-1d6744832d2c": {
        "component_validation.json": "70b246e3f2cd088a2fe25bc68399c8582cfb1c749e57beb5abf78baa35817e36",
        "evidence_manifest.json": "984894ea18fc78e8b9a7d808cf0fbd30ce2ca36a703fa1d00564552bf0fff2b1",
        "official_seal.json": "4e3e84a8f8105c41d6ca4f18ce934be430938f251b7336416e8ed044bddc0ba4",
        "runtime_identity.json": "7e3b3205e87b408ec91e6266f12cb0f4bb0c3e1a9b927a8a1b1d770f9d06558d",
        "source_revision.json": "bca187c4be283b855387d162d89d6f2fb19c0465e13cd36cc07a3c49808ff5b1",
        "status.json": "98086e60b116ac419b42b2b73ae970f3c3909be5ccd8edac0c500b5eb36d3c3b"
    }
})



class RuntimeSupersessionBuildError(ValueError):
    """The frozen runtime-supersession registry could not be proven."""


def _recorded_at_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise RuntimeSupersessionBuildError("recorded_at_utc must be explicit UTC ending in Z")
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise RuntimeSupersessionBuildError("recorded_at_utc is invalid") from exc
    if result.tzinfo is None or result.utcoffset() != UTC.utcoffset(result):
        raise RuntimeSupersessionBuildError("recorded_at_utc must use UTC")
    return result


def build_registry(
    *,
    repository_root: Path,
    source_commit: str,
    recorded_at_utc: str,
) -> bytes:
    try:
        root = require_repository_root(repository_root)
    except (TypeError, ValueError) as exc:
        raise RuntimeSupersessionBuildError(
            f"repository authority is invalid: {exc}",
        ) from exc
    if _GIT_SHA.fullmatch(source_commit) is None:
        raise RuntimeSupersessionBuildError("source_commit must be explicit lowercase 40-hex")
    expected_paths = {
        item[key]
        for item in R2_RUNTIME_SUPERSEDED_RESULTS.values()
        for key in ("primary_path", "replay_path")
    }
    if expected_paths != set(EXPECTED_RUNTIME_SUPERSESSION_EVIDENCE_HASHES):
        raise RuntimeSupersessionBuildError("frozen runtime-supersession scope is inconsistent")
    records: list[dict[str, object]] = []
    for logical_id, expected in sorted(R2_RUNTIME_SUPERSEDED_RESULTS.items()):
        for copy_role, key in (
            (HistoricalCopyRole.PRIMARY, "primary_path"),
            (HistoricalCopyRole.REPLAY, "replay_path"),
        ):
            relative = expected[key]
            try:
                record = build_runtime_supersession_record_v3(
                    root / relative,
                    repository_root=root,
                    logical_result_id=logical_id,
                    market_profile=expected["market_profile"],
                    result_class=HistoricalResultClass(expected["result_class"]),
                    copy_role=copy_role,
                )
            except (OSError, ValueError) as exc:
                raise RuntimeSupersessionBuildError(
                    f"cannot bind immutable superseded result {relative}: {exc}",
                ) from exc
            if record["evidence_hashes"] != EXPECTED_RUNTIME_SUPERSESSION_EVIDENCE_HASHES[relative]:
                raise RuntimeSupersessionBuildError(
                    f"runtime-supersession evidence identity mismatch: {relative}",
                )
            records.append(record)
    return build_runtime_supersession_registry_v3(
        records,
        authority_id=R2_RUNTIME_SUPERSESSION_AUTHORITY,
        audited_baseline_commit=R2_AUDITED_BASELINE_COMMIT,
        source_commit=source_commit,
        recorded_at_utc=_recorded_at_utc(recorded_at_utc),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--recorded-at-utc", required=True)
    arguments = parser.parse_args(argv)
    sys.stdout.buffer.write(
        build_registry(
            repository_root=arguments.repository,
            source_commit=arguments.source_commit,
            recorded_at_utc=arguments.recorded_at_utc,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
