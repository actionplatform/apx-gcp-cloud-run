"""Drive the plugin through the core's registry without installing it."""

import asyncio
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from action_platform.core.scaffold.catalog import Matrix, with_plugin_clouds
from action_platform.plugins import Loaded, Plugins, PluginState, registry

from apx_gcp_cloud_run import GcpCloudRunPlugin
from apx_gcp_cloud_run.connect import SCRIPT, connect_command


class PluginTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.plugins = Plugins(
            [Loaded(GcpCloudRunPlugin(), "apx-gcp-cloud-run", "0.1.0")],
            PluginState(file=Path(self.tmp.name) / "plugins.json"),
        )
        registry._current = self.plugins
        self.addCleanup(registry.reset)
        self.addCleanup(self.tmp.cleanup)

    def test_the_four_options_and_the_connect_button(self):
        options = {o.key: o for o in GcpCloudRunPlugin.options}

        self.assertEqual(
            list(options),
            ["project_id", "region", "workload_identity_provider", "service_account"],
        )
        self.assertTrue(all(o.required for o in options.values()))
        button = options["workload_identity_provider"].as_dict()
        self.assertEqual(button["action_label"], "Connect Google Cloud")
        self.assertIn("shell.cloud.google.com", button["action_url"])
        self.assertTrue(
            button["action_copy"].endswith("sh ap-connect.sh {issuer} {organization}")
        )

    def test_the_connect_command_fetches_this_release_of_the_script(self):
        command = connect_command("https://platform.example.com", "acme")

        self.assertIn("/apx_gcp_cloud_run/connect/connect.sh", command)
        self.assertTrue(command.endswith("https://platform.example.com acme"))

    def test_the_connect_script_parses_and_refuses_bad_input(self):
        self.assertEqual(subprocess.run(["sh", "-n", str(SCRIPT)]).returncode, 0)

        bad = subprocess.run(
            ["sh", str(SCRIPT), "http://platform.example.com", "acme"],
            capture_output=True,
            text=True,
        )

        self.assertEqual(bad.returncode, 2)
        self.assertIn("https://", bad.stderr)

    def test_the_connect_script_scopes_trust_to_the_organization(self):
        text = SCRIPT.read_text()

        self.assertIn("attribute.organization=assertion.organization", text)
        self.assertIn("assertion.organization == '$ORGANIZATION'", text)
        self.assertIn("attribute.organization/$ORGANIZATION", text)
        self.assertIn("roles/iam.workloadIdentityUser", text)

    def test_overlay_joins_the_matrix(self):
        merged = with_plugin_clouds(Matrix.from_dict({"clouds": []}))

        self.assertEqual([c.name for c in merged.clouds], ["gcp/cloud-run"])

    def test_tool_is_prefixed_and_answers(self):
        from action_platform.mcp import server

        mcp = server.build()
        names = {t.name for t in asyncio.run(mcp.list_tools())}

        self.assertIn("gcp_cloud_run_connect_command", names)

        result = asyncio.run(
            mcp.call_tool(
                "gcp_cloud_run_connect_command",
                {
                    "issuer": "https://platform.example.com/",
                    "organization": "acme",
                    "app": "acme/shop/orders",
                    "scope": "prod",
                },
            )
        )
        data = json.loads(result.content[0].text)

        self.assertEqual(data["service"], "ap-acme-shop-orders-prod")
        self.assertIn("https://platform.example.com acme", data["command"])
