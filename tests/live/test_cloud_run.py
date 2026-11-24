"""Against a real Google Cloud project, when its settings and a token its provider trusts are in the environment — skipped anywhere else. Two deploys to one scope, `verify`, `rollback`, `delete`."""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from action_platform.core.context import Context

from apx_gcp_cloud_run.target import CloudRunTarget

NEEDED = [
    "AP_APP",
    "AP_GCP_CLOUD_RUN_PROJECT_ID",
    "AP_GCP_CLOUD_RUN_REGION",
    "AP_GCP_CLOUD_RUN_WORKLOAD_IDENTITY_PROVIDER",
    "AP_GCP_CLOUD_RUN_SERVICE_ACCOUNT",
    "GCP_CLOUD_RUN_LIVE_TOKEN",
    "GCP_CLOUD_RUN_LIVE_IMAGE",
    "GCP_CLOUD_RUN_LIVE_VERSIONS",
]
LIVE = all(os.environ.get(name) for name in NEEDED)


@unittest.skipUnless(LIVE, "needs a Google Cloud project: " + ", ".join(NEEDED))
class LiveTest(unittest.TestCase):
    def test_deploy_twice_verify_rollback_delete(self):
        first, second = os.environ["GCP_CLOUD_RUN_LIVE_VERSIONS"].split(",")[:2]
        token = os.environ["GCP_CLOUD_RUN_LIVE_TOKEN"]
        target = CloudRunTarget(
            image=os.environ["GCP_CLOUD_RUN_LIVE_IMAGE"],
            port=int(os.environ.get("GCP_CLOUD_RUN_LIVE_PORT", "8080")),
            health=os.environ.get("GCP_CLOUD_RUN_LIVE_HEALTH", ""),
        )

        with TemporaryDirectory() as tmp:

            def ctx(version):
                return Context(
                    repo_root=Path(tmp),
                    stage="ci",
                    next_version=version,
                    env=dict(os.environ),
                    identity=lambda audience: token,
                )

            try:
                self.assertTrue(target.deploy(ctx(first)).ok)
                self.assertTrue(target.deploy(ctx(second)).ok)
                self.assertTrue(target.verify(second, "ci"))

                target.rollback(ctx(second))

                self.assertTrue(target.verify(first, "ci"))
                self.assertTrue(target.url(first, "ci"))
            finally:
                target.delete(ctx(first))

            self.assertEqual(target.diagnose(ctx(first)).status, "missing")
