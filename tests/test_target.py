import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from action_platform.core.context import Context
from action_platform.core.exception import DeployError

from apx_gcp_cloud_run.target import CloudRunTarget
from tests.fake import ACCESS, FEDERATED, FakeGoogle

PROVIDER = "projects/123456789/locations/global/workloadIdentityPools/action-platform/providers/platform"
SERVICE = "projects/acme-run/locations/us-central1/services/ap-acme-shop-orders-dev"
ENV = {
    "AP_APP": "acme/shop/orders",
    "AP_GCP_CLOUD_RUN_PROJECT_ID": "acme-run",
    "AP_GCP_CLOUD_RUN_REGION": "us-central1",
    "AP_GCP_CLOUD_RUN_WORKLOAD_IDENTITY_PROVIDER": PROVIDER,
    "AP_GCP_CLOUD_RUN_SERVICE_ACCOUNT": "ap-deploy@acme-run.iam.gserviceaccount.com",
}


class Healthy:
    def __init__(self, answer=None):
        self.answer = answer
        self.asked = []

    def answers(self, url):
        self.asked.append(url)

        return self.answer


class FakeTarget(CloudRunTarget):
    pause = 0

    def __init__(self, google: FakeGoogle, health=None, **options) -> None:
        super().__init__(**options)
        self.google = google
        self.health = health or Healthy()

    def transport(self):
        return self.google

    def identity(self, ctx):
        return ctx.identity_token


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "platform.toml").write_text(
            '[project]\nname = "orders"\n\n[source_host]\nkind = "github"\nrepo = "acme/Orders"\n'
        )
        self.minted = []

    def identity(self, audience):
        self.minted.append(audience)

        return "platform-token"

    def ctx(self, stage="dev", version="1.0.0", env=None, identity=True):
        return Context(
            repo_root=self.root,
            stage=stage,
            next_version=version,
            env=dict(ENV if env is None else env),
            identity=self.identity if identity else None,
        )


class TargetTest(Base):
    def test_deploy_federates_and_points_the_scope_at_the_tag(self):
        google = FakeGoogle()
        health = Healthy()
        target = FakeTarget(google, health)

        result = target.deploy(self.ctx())

        self.assertTrue(result.ok, result.error)
        self.assertEqual(
            result.url, "https://ap-acme-shop-orders-dev-abcd1234-uc.a.run.app"
        )
        self.assertEqual(health.asked, [result.url + "/health"])
        self.assertEqual(self.minted, [f"https://iam.googleapis.com/{PROVIDER}"])
        self.assertEqual(
            google.forms[0]["audience"], f"//iam.googleapis.com/{PROVIDER}"
        )
        self.assertEqual(
            google.forms[0]["subject_token_type"],
            "urn:ietf:params:oauth:token-type:jwt",
        )
        container = google.services[SERVICE]["template"]["containers"][0]
        self.assertEqual(
            container["image"],
            "us-central1-docker.pkg.dev/acme-run/action-platform/orders:1.0.0",
        )
        self.assertEqual(container["ports"], [{"containerPort": 8000}])
        self.assertEqual(
            google.services[SERVICE]["template"]["serviceAccount"],
            "ap-runtime@acme-run.iam.gserviceaccount.com",
        )
        self.assertTrue(google.services[SERVICE]["invokerIamDisabled"])
        self.assertIn(
            (
                "GET",
                "artifactregistry.googleapis.com/v1/projects/acme-run/locations/us-central1/repositories/action-platform/packages/orders/tags/1.0.0",
            ),
            [(m, u) for m, u, _ in google.calls],
        )

    def test_verify_reads_the_serving_revision_and_url_is_the_service(self):
        google = FakeGoogle()
        target = FakeTarget(google)
        target.deploy(self.ctx(version="1.0.0"))
        target.deploy(self.ctx(version="1.1.0"))

        self.assertTrue(target.verify("1.1.0", "dev"))
        self.assertFalse(target.verify("1.0.0", "dev"))
        self.assertFalse(target.verify("1.1.0", "prod"))
        self.assertEqual(
            target.url("1.1.0", "dev"),
            "https://ap-acme-shop-orders-dev-abcd1234-uc.a.run.app",
        )

    def test_verify_works_from_an_attached_context(self):
        google = FakeGoogle()
        FakeTarget(google).deploy(self.ctx(version="1.0.0"))
        fresh = FakeTarget(google)
        fresh.attach(self.ctx(stage="dev"))

        self.assertTrue(fresh.verify("1.0.0", "dev"))

    def test_verify_without_any_context_cannot_ask(self):
        with self.assertRaises(DeployError):
            FakeTarget(FakeGoogle()).verify("1.0.0", "dev")

    def test_an_unpublished_image_stops_the_deploy(self):
        google = FakeGoogle(published=set())

        with self.assertRaises(DeployError) as raised:
            FakeTarget(google).deploy(self.ctx())

        self.assertIn("orders:1.0.0 is not published", str(raised.exception))
        self.assertEqual(google.services, {})

    def test_a_revision_that_fails_to_start_fails_the_deploy_and_keeps_the_old_one(
        self,
    ):
        google = FakeGoogle(failing_tags={"1.1.0"})
        target = FakeTarget(google)
        target.deploy(self.ctx(version="1.0.0"))

        result = target.deploy(self.ctx(version="1.1.0"))

        self.assertFalse(result.ok)
        self.assertIn("failed to start", result.error)
        self.assertTrue(target.verify("1.0.0", "dev"))

    def test_an_unhealthy_service_fails_the_deploy(self):
        result = FakeTarget(FakeGoogle(), Healthy("answered 503")).deploy(self.ctx())

        self.assertFalse(result.ok)
        self.assertIn("503", result.error)

    def test_rollback_sends_every_request_to_the_previous_revision(self):
        google = FakeGoogle()
        target = FakeTarget(google)
        target.deploy(self.ctx(version="1.0.0"))
        target.deploy(self.ctx(version="1.1.0"))

        target.rollback(self.ctx())

        self.assertTrue(target.verify("1.0.0", "dev"))
        self.assertEqual(
            google.services[SERVICE]["traffic"],
            [
                {
                    "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                    "revision": "ap-acme-shop-orders-dev-00001-xyz",
                    "percent": 100,
                }
            ],
        )
        self.assertEqual(len(google.revisions[SERVICE]), 2)

        target.rollback(self.ctx(), to_version="1.1.0")

        self.assertTrue(target.verify("1.1.0", "dev"))

    def test_rollback_without_an_earlier_version_says_so(self):
        target = FakeTarget(FakeGoogle())
        target.deploy(self.ctx())

        with self.assertRaises(DeployError):
            target.rollback(self.ctx())

    def test_switch_traffic_splits_between_the_newest_and_the_previous(self):
        google = FakeGoogle()
        target = FakeTarget(google)
        target.deploy(self.ctx(version="1.0.0"))
        target.deploy(self.ctx(version="1.1.0"))
        target.rollback(self.ctx())

        target.switch_traffic(self.ctx(), 10)

        self.assertEqual(
            google.services[SERVICE]["trafficStatuses"],
            [
                {
                    "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                    "percent": 10,
                    "revision": "ap-acme-shop-orders-dev-00002-xyz",
                },
                {
                    "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                    "percent": 90,
                    "revision": "ap-acme-shop-orders-dev-00001-xyz",
                },
            ],
        )
        self.assertTrue(target.verify("1.0.0", "dev"))

        target.switch_traffic(self.ctx(), 100)

        self.assertTrue(target.verify("1.1.0", "dev"))

    def test_switch_traffic_needs_a_second_revision_below_100(self):
        target = FakeTarget(FakeGoogle())
        target.deploy(self.ctx())

        with self.assertRaises(DeployError):
            target.switch_traffic(self.ctx(), 50)

    def test_diagnose_and_delete(self):
        google = FakeGoogle()
        target = FakeTarget(google)
        target.deploy(self.ctx())

        diagnosis = target.diagnose(self.ctx())

        self.assertTrue(diagnosis.ok)
        self.assertEqual(diagnosis.status, "ready")
        self.assertEqual(diagnosis.version, "1.0.0")
        self.assertEqual(
            diagnosis.details["revision"], "ap-acme-shop-orders-dev-00001-xyz"
        )

        target.delete(self.ctx())
        target.delete(self.ctx())

        self.assertEqual(target.diagnose(self.ctx()).status, "missing")

    def test_diagnose_without_settings(self):
        diagnosis = FakeTarget(FakeGoogle()).diagnose(self.ctx(env={}))

        self.assertEqual(diagnosis.status, "unconfigured")
        self.assertIn("project_id", diagnosis.details["missing"])

    def test_platform_toml_wins_over_the_plugin_options(self):
        google = FakeGoogle()
        target = FakeTarget(
            google,
            region="europe-west1",
            port=8080,
            public=False,
            env={"LOG_LEVEL": "info"},
        )

        self.assertTrue(target.deploy(self.ctx()).ok)

        name = (
            "projects/acme-run/locations/europe-west1/services/ap-acme-shop-orders-dev"
        )
        container = google.services[name]["template"]["containers"][0]
        self.assertEqual(container["ports"], [{"containerPort": 8080}])
        self.assertEqual(container["env"], [{"name": "LOG_LEVEL", "value": "info"}])
        self.assertFalse(google.services[name]["invokerIamDisabled"])

    def test_no_token_ever_reaches_an_error(self):
        google = FakeGoogle(impersonation=False)
        checks = FakeTarget(google).readiness(self.ctx())

        for check in checks:
            for secret in ("platform-token", FEDERATED, ACCESS):
                self.assertNotIn(secret, check.detail)
                self.assertNotIn(secret, check.fix or "")


class ReadinessTest(Base):
    def ids(self, checks):
        return {c.id: c for c in checks}

    def test_all_green(self):
        checks = self.ids(FakeTarget(FakeGoogle()).readiness(self.ctx()))

        self.assertEqual(
            list(checks),
            [
                "gcp.settings",
                "gcp.credentials",
                "gcp.permissions",
                "image.published",
                "service.state",
            ],
        )
        self.assertTrue(all(c.ok for c in checks.values()))
        self.assertEqual(
            checks["service.state"].detail, "no service yet; the deploy creates it"
        )

    def test_missing_settings_stop_early(self):
        checks = FakeTarget(FakeGoogle()).readiness(self.ctx(env={"AP_APP": "a/b/c"}))

        self.assertEqual([c.id for c in checks], ["gcp.settings"])
        self.assertIn("workload_identity_provider", checks[0].detail)
        self.assertTrue(checks[0].blocking)

    def test_a_token_the_provider_rejects(self):
        google = FakeGoogle(trusted="another-organization")
        checks = FakeTarget(google).readiness(self.ctx())

        self.assertEqual(checks[-1].id, "gcp.credentials")
        self.assertTrue(checks[-1].blocking)
        self.assertIn("attribute condition", checks[-1].detail)
        self.assertIn("organization acme", checks[-1].fix)

    def test_no_identity_on_this_machine(self):
        checks = FakeTarget(FakeGoogle()).readiness(self.ctx(identity=False))

        self.assertFalse(checks[-1].ok)
        self.assertIn("identity token", checks[-1].detail)

    def test_a_missing_role_comes_with_the_gcloud_command(self):
        google = FakeGoogle(granted=["artifactregistry.tags.get"], act_as=False)
        check = self.ids(FakeTarget(google).readiness(self.ctx()))["gcp.permissions"]

        self.assertTrue(check.blocking)
        self.assertIn("run.services.create", check.detail)
        self.assertIn(
            "gcloud projects add-iam-policy-binding acme-run "
            "--member=serviceAccount:ap-deploy@acme-run.iam.gserviceaccount.com "
            "--role=roles/run.admin --condition=None",
            check.fix,
        )
        self.assertIn(
            "gcloud iam service-accounts add-iam-policy-binding ap-runtime@acme-run.iam.gserviceaccount.com "
            "--project=acme-run --member=serviceAccount:ap-deploy@acme-run.iam.gserviceaccount.com "
            "--role=roles/iam.serviceAccountUser",
            check.fix,
        )
        self.assertNotIn("artifactregistry.reader", check.fix)

    def test_a_runtime_service_account_that_does_not_exist(self):
        google = FakeGoogle(runtime_exists=False)
        check = self.ids(FakeTarget(google).readiness(self.ctx()))["gcp.permissions"]

        self.assertFalse(check.ok)
        self.assertIn("iam.serviceAccounts.actAs", check.detail)

    def test_an_image_not_pushed(self):
        google = FakeGoogle(published={"0.9.0"})
        check = self.ids(FakeTarget(google).readiness(self.ctx()))["image.published"]

        self.assertTrue(check.blocking)
        self.assertIn("docker-publish", check.fix)

    def test_a_service_mid_rollout_blocks(self):
        google = FakeGoogle(reconciling=True)
        target = FakeTarget(google)
        target.deploy(self.ctx())

        check = self.ids(target.readiness(self.ctx()))["service.state"]

        self.assertTrue(check.blocking)
        self.assertIn("mid-rollout", check.detail)

    def test_a_failed_latest_revision_only_warns(self):
        google = FakeGoogle(failing_tags={"1.0.0"})
        target = FakeTarget(google)
        target.deploy(self.ctx())

        check = self.ids(target.readiness(self.ctx(version="1.1.0")))["service.state"]

        self.assertTrue(check.ok)
        self.assertEqual(check.severity, "warning")

    def test_preflight_raises_on_what_blocks(self):
        with self.assertRaises(DeployError):
            FakeTarget(FakeGoogle(published=set())).preflight(self.ctx())
