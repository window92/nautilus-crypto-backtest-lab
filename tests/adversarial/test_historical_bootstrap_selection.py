from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from crypto_lab.hashing import sha256_file
from crypto_lab.historical_contracts import HistoricalAuthorityError
from scripts.run_historical_evidence_acceptance import bound_historical_bootstrap

ROOT = Path(__file__).resolve().parents[2]


class HistoricalBootstrapSelectionTests(unittest.TestCase):
    def test_old_authority_resolves_only_its_exact_preserved_bootstrap(self):
        manifest = json.loads((ROOT / 'contracts/historical-validator-authorities-v2.json').read_bytes())
        for profile in manifest['runtime_profiles'].values():
            path = bound_historical_bootstrap(ROOT, profile)
            self.assertEqual(sha256_file(path), profile['bootstrap_sha256'])
            self.assertNotEqual(path, ROOT / 'scripts/isolated_runtime_bootstrap.py')
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                target = root / path.relative_to(ROOT)
                target.parent.mkdir(parents=True)
                target.write_bytes(path.read_bytes())
                self.assertEqual(bound_historical_bootstrap(root, profile), target)
                target.write_bytes(path.read_bytes() + b'\n# altered\n')
                with self.assertRaises(HistoricalAuthorityError):
                    bound_historical_bootstrap(root, profile)
                target.unlink()
                with self.assertRaises(HistoricalAuthorityError):
                    bound_historical_bootstrap(root, profile)
                target.symlink_to(path)
                with self.assertRaises(HistoricalAuthorityError):
                    bound_historical_bootstrap(root, profile)


if __name__ == '__main__':
    unittest.main()
