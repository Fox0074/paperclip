import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("provisioner", Path(__file__).with_name("provisioner.py"))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
AGENT = "11111111-1111-4111-8111-111111111111"
COMPANY = "22222222-2222-4222-8222-222222222222"


class ProvisionerBoundaries(unittest.TestCase):
    def test_invalid_ids_unknown_company_and_arbitrary_mount_inputs_are_rejected(self):
        profile = {"companyId": COMPANY, "id": "codex-sale"}
        value = dict(companyId=COMPANY, agentId=AGENT, presetId="codex-sale", name="Engineer", role="engineer")
        self.assertEqual(p.validate_request(value, [profile]), profile)
        for change in [{"agentId": "../../etc"}, {"companyId": AGENT}, {"mount": "/"}]:
            with self.assertRaises(ValueError):
                p.validate_request({**value, **change}, [profile])

    def test_home_symlinks_cannot_escape_configured_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            home_root = root / "homes"
            home_root.mkdir()
            try:
                (home_root / AGENT).symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("Symlink creation unavailable")
            with self.assertRaises(ValueError):
                p.managed_home({"homeRoot": str(home_root)}, AGENT)

    def test_cleanup_does_not_remove_unlabelled_containers_or_unmanaged_data(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = {"homeRoot": directory}
            value = {"agentId": AGENT}
            with patch.object(p, "inspect", return_value={"Config": {"Labels": {}}}), patch.object(p, "docker") as docker:
                with self.assertRaises(ValueError):
                    p.remove(profile, value)
                docker.assert_not_called()
            home = Path(directory) / AGENT
            home.mkdir()
            (home / "valuable.txt").write_text("keep")
            with patch.object(p, "inspect", return_value=None):
                with self.assertRaises(ValueError):
                    p.remove(profile, value)
            self.assertTrue((home / "valuable.txt").exists())


if __name__ == "__main__":
    unittest.main()
