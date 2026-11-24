import json
import re
import unittest
from pathlib import Path

from action_platform.core.context import Context

from apx_gcp_cloud_run.credentials import identity_of
from apx_gcp_cloud_run.google import reason
from apx_gcp_cloud_run.spec import Options, Spec, label, provider_of, service_name

PROVIDER = "projects/42/locations/global/workloadIdentityPools/action-platform/providers/platform"


class SpecTest(unittest.TestCase):
    def test_service_names_follow_cloud_runs_rules(self):
        self.assertEqual(
            service_name("acme/shop/orders", "dev"), "ap-acme-shop-orders-dev"
        )
        self.assertEqual(
            service_name("Acme/My_Shop/api.v2", "prod-eu"),
            "ap-acme-my-shop-api-v2-prod-eu",
        )

        long = service_name(
            "a-very-long-organization/a-long-project-name/the-orders-api", "production"
        )

        self.assertLessEqual(len(long), 49)
        self.assertRegex(long, r"^[a-z][a-z0-9-]*[a-z0-9]$")
        self.assertNotEqual(
            long,
            service_name(
                "a-very-long-organization/a-long-project-name/the-orders-apx",
                "production",
            ),
        )

    def test_the_provider_from_what_the_user_pasted(self):
        self.assertEqual(provider_of(PROVIDER), PROVIDER)
        self.assertEqual(provider_of(f"//iam.googleapis.com/{PROVIDER}"), PROVIDER)
        self.assertEqual(
            provider_of(f"https://iam.googleapis.com/{PROVIDER}"), PROVIDER
        )
        self.assertIsNone(
            provider_of("projects/acme/locations/global/workloadIdentityPools/x")
        )

    def test_audience_and_image(self):
        spec = Spec.of(
            Context(
                repo_root=Path("/nonexistent"),
                stage="prod",
                next_version="2.0.0",
                env={"AP_APP": "acme/shop/orders"},
            ),
            Options(
                project_id="acme-run",
                region="us-central1",
                workload_identity_provider=PROVIDER,
                service_account="ap-deploy@acme-run.iam.gserviceaccount.com",
                image="us-central1-docker.pkg.dev/acme-run/images/shop/orders",
            ),
        )

        self.assertEqual(spec.audience, f"https://iam.googleapis.com/{PROVIDER}")
        self.assertEqual(spec.service, "ap-acme-shop-orders-prod")
        self.assertEqual(spec.organization, "acme")
        self.assertEqual(spec.artifact["package"], "shop/orders")
        self.assertEqual(spec.missing(), [])

    def test_an_image_outside_artifact_registry(self):
        spec = Spec.of(
            Context(repo_root=Path("/nonexistent"), env={}),
            Options(image="docker.io/acme/orders"),
        )

        self.assertIsNone(spec.artifact)
        self.assertIn(
            "the app (AP_APP=<org>/<project>/<app>, or [deploy] service)",
            spec.missing(),
        )

    def test_labels(self):
        self.assertEqual(label("1.2.0-rc.1"), "1-2-0-rc-1")
        self.assertTrue(re.fullmatch(r"[a-z0-9_-]{0,63}", label("X" * 80)))

    def test_google_errors_read_as_google_wrote_them(self):
        self.assertEqual(
            reason(
                json.dumps(
                    {
                        "error": {
                            "code": 403,
                            "message": "Permission denied",
                            "status": "PERMISSION_DENIED",
                        }
                    }
                )
            ),
            "Permission denied",
        )
        self.assertEqual(
            reason(
                json.dumps(
                    {
                        "error": "invalid_grant",
                        "error_description": "The audience in ID Token does not match.",
                    }
                )
            ),
            "invalid_grant: The audience in ID Token does not match.",
        )
        self.assertEqual(reason("<html>bad gateway</html>"), "<html>bad gateway</html>")

    def test_the_deploys_own_token_comes_first(self):
        self.assertEqual(
            identity_of(lambda audience: f"token for {audience}")("aud"),
            "token for aud",
        )
