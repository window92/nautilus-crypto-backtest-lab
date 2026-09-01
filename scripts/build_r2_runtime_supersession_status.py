#!/usr/bin/env python3
"""Build the closed additive registry for R2 runtime-superseded Run bytes."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

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
    root = Path(repository_root).resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise RuntimeSupersessionBuildError("repository root must be an exact directory")
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
