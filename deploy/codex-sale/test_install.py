import importlib.util
import argparse
import json
from pathlib import Path, PurePosixPath
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("installer", Path(__file__).with_name("install.py"))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallationBoundaries(unittest.TestCase):
    def test_native_setup_commands_use_the_application_user(self):
        with patch.object(installer, "run", return_value="{}") as run:
            installer.node(PurePosixPath("/opt/paperclip-test"), "synthetic script")
        self.assertEqual(run.call_args.args[:6], ("docker", "exec", "-u", "node", "-i", "paperclip-test"))

    def test_existing_unmanaged_data_is_never_claimed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "database"
            original.write_text("keep this")
            with self.assertRaisesRegex(ValueError, "existing installation"):
                installer.claim_root(root, "commit", "127.0.0.1", 13100)
            self.assertEqual(original.read_text(), "keep this")
            self.assertFalse((root / installer.MARKER).exists())

    def test_repeat_preserves_manifest_and_rejects_source_or_network_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, fresh = installer.claim_root(root, "commit", "127.0.0.1", 13100)
            self.assertTrue(fresh)
            again, fresh = installer.claim_root(root, "commit", "127.0.0.1", 13100)
            self.assertFalse(fresh)
            self.assertEqual(first, again)
            for commit, host, port in [("other", "127.0.0.1", 13100),
                                       ("commit", "192.168.1.2", 13100),
                                       ("commit", "127.0.0.1", 13101)]:
                with self.assertRaises(ValueError):
                    installer.claim_root(root, commit, host, port)

    def test_owner_marker_cannot_adopt_a_different_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installer.write_json(root / installer.MARKER, {"format": 1, "root": "/another/root"})
            with self.assertRaisesRegex(ValueError, "ownership marker"):
                installer.claim_root(root, "commit", "127.0.0.1", 13100)

    def test_compose_has_only_explicit_data_and_broker_mounts(self):
        root = PurePosixPath("/opt/paperclip-test")
        service = installer.compose_config(root, "127.0.0.1", 13100, "image:commit")["services"]["paperclip"]
        self.assertEqual(service["ports"], ["127.0.0.1:13100:3100"])
        text = json.dumps(service)
        self.assertNotIn("docker.sock", text)
        self.assertNotIn("codex-sale.env", text)
        self.assertNotIn("privileged", text)
        self.assertNotIn("runtime-key", text)
        self.assertEqual(service["environment"]["PAPERCLIP_DEPLOYMENT_MODE"], "authenticated")
        self.assertEqual(service["environment"]["PAPERCLIP_AUTH_PUBLIC_BASE_URL"], "http://127.0.0.1:13100")
        self.assertEqual(service["environment"]["BETTER_AUTH_URL"], "http://127.0.0.1:13100")
        self.assertEqual(service["environment"]["BETTER_AUTH_TRUSTED_ORIGINS"], "http://127.0.0.1:13100")
        self.assertEqual(service["environment"]["PAPERCLIP_AGENT_JWT_SECRET"], "${PAPERCLIP_AGENT_JWT_SECRET}")
        self.assertIn("/run/paperclip-test-provisioner:/run/paperclip-provisioner", service["volumes"])

    def test_service_can_write_only_its_managed_homes_and_keeps_socket_directory(self):
        unit = installer.systemd_unit(PurePosixPath("/opt/paperclip-test"))
        self.assertIn("ReadWritePaths=/opt/paperclip-test/agent-data/managed", unit)
        self.assertIn("RuntimeDirectoryPreserve=yes", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("ProtectSystem=strict", unit)

    def test_repeat_does_not_read_or_replace_the_existing_provider_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "integration").mkdir()
            target = root / "integration/codex-sale.env"
            target.write_text("CODEX_SALE_API_KEY=keep-this-key\n")
            with patch.object(installer.getpass, "getpass", side_effect=AssertionError("must not prompt")):
                installer.save_provider_key(root, Path("does-not-exist"))
            self.assertEqual(target.read_text(), "CODEX_SALE_API_KEY=keep-this-key\n")

    def test_initialization_can_resume_without_resetting_native_config_or_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(installer.os, "chown", create=True), patch.object(installer, "run") as run:
                installer.initialize(root, "127.0.0.1", 13100)
                key = root / "integration/runtime-key"
                key.write_text("existing runtime key")
                env = root / ".env"
                auth = env.read_text()
                native = root / "data/instances/default/config.json"
                config = json.loads(native.read_text())
                config["telemetry"]["enabled"] = True
                installer.write_json(native, config)
                run.reset_mock()
                installer.initialize(root, "127.0.0.1", 13100)
                run.assert_not_called()
                self.assertEqual(env.read_text(), auth)
                self.assertTrue(json.loads(native.read_text())["telemetry"]["enabled"])
                self.assertEqual(key.read_text(), "existing runtime key")

    def test_configure_keeps_an_existing_owner_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {"configuredCompanies": []}
            args = argparse.Namespace(company=None, input_rate=1, output_rate=2, budget_usd=50)
            responses = [json.dumps([{"id": "company", "name": "Test", "budget_monthly_cents": 4200}]),
                         json.dumps({"status": "pass"})]
            with patch.object(installer, "node", side_effect=responses) as node, \
                 patch.object(installer, "run"), patch("builtins.print"):
                installer.configure(root, manifest, args)
            self.assertEqual(node.call_count, 2)
            self.assertTrue(all("companyService" not in call.args[1] for call in node.call_args_list))
            self.assertEqual(manifest["configuredCompanies"], ["company"])

    def test_repeat_keeps_an_explicit_unlimited_budget_instead_of_resetting_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {"configuredCompanies": ["company"]}
            args = argparse.Namespace(company=None, input_rate=1, output_rate=2, budget_usd=999)
            responses = [json.dumps([{"id": "company", "name": "Test", "budget_monthly_cents": 0}]),
                         json.dumps({"status": "pass"})]
            with patch.object(installer, "node", side_effect=responses) as node, \
                 patch.object(installer, "run"), patch("builtins.print"):
                installer.configure(root, manifest, args)
            self.assertEqual(node.call_count, 2)
            self.assertFalse((root / installer.MARKER).exists())


if __name__ == "__main__":
    unittest.main()
