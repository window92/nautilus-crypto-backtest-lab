from __future__ import annotations

import copy
import unittest

from crypto_lab.config import LabRunConfig
from tests.helpers import encode_config
from tests.helpers import load_spot_config_dict


class G18ConfigHashTests(unittest.TestCase):
    def test_material_change_changes_config_sha256(self) -> None:
        original_data = load_spot_config_dict()
        changed_data = copy.deepcopy(original_data)
        changed_data["initial_capital"]["amount"] = "1000.01"
        changed_data["nautilus_venue_config"]["starting_balances"][0]["amount"] = "1000.01"

        original = LabRunConfig.from_json_bytes(encode_config(original_data))
        changed = LabRunConfig.from_json_bytes(encode_config(changed_data))
        self.assertNotEqual(original.config_sha256, changed.config_sha256)

    def test_occurrence_id_and_physical_catalog_path_are_not_material(self) -> None:
        original_data = load_spot_config_dict()
        changed_data = copy.deepcopy(original_data)
        changed_data["run_id"] = "m0-downstream-contract-999"
        changed_data["nautilus_data_config"][0]["catalog_path"] = "/another/physical/path"

        original = LabRunConfig.from_json_bytes(encode_config(original_data))
        changed = LabRunConfig.from_json_bytes(encode_config(changed_data))
        self.assertEqual(original.config_sha256, changed.config_sha256)


if __name__ == "__main__":
    unittest.main()
