"""The plugin: the gcp/cloud-run overlay, `action-platform gcp-cloud-run` commands, the `gcp_cloud_run_*` tool, and the four options an organization fills in."""

from __future__ import annotations

from pathlib import Path

from action_platform.abc import Option, Plugin, Surface

from apx_gcp_cloud_run import cli
from apx_gcp_cloud_run.connect import CLOUD_SHELL, connect_command
from apx_gcp_cloud_run.tools import register_tools


class GcpCloudRunPlugin(Plugin):
    slug = "gcp-cloud-run"
    name = "GCP Cloud Run"
    description = "Deploy a Docker image the CI pushes to Artifact Registry to Google Cloud Run, keyless through Workload Identity Federation"
    min_core = "0.33"
    needs = [
        "env: AP_GCP_CLOUD_RUN_PROJECT_ID, AP_GCP_CLOUD_RUN_REGION, AP_GCP_CLOUD_RUN_WORKLOAD_IDENTITY_PROVIDER, AP_GCP_CLOUD_RUN_SERVICE_ACCOUNT (the plugin's options on the platform), AP_APP",
        "net: sts.googleapis.com, iamcredentials.googleapis.com, run.googleapis.com, artifactregistry.googleapis.com, cloudresourcemanager.googleapis.com, iam.googleapis.com; the service's *.run.app url for the health check",
        "tool: gcloud, only for `action-platform gcp-cloud-run connect`",
    ]
    options = [
        Option(
            "project_id",
            "Project ID",
            "text",
            help="The Google Cloud project the services run in.",
            required=True,
        ),
        Option(
            "region",
            "Region",
            "text",
            help="Where the services and the Artifact Registry repository live, us-central1 for one.",
            required=True,
        ),
        Option(
            "workload_identity_provider",
            "Workload identity provider",
            "text",
            help="projects/<number>/locations/global/workloadIdentityPools/<pool>/providers/<id>, as the connect script prints it.",
            required=True,
            action_label="Connect Google Cloud",
            action_url=CLOUD_SHELL,
            action_copy=connect_command(),
        ),
        Option(
            "service_account",
            "Deploy service account",
            "text",
            help="The service account deploys act as, ap-deploy@<project>.iam.gserviceaccount.com from the connect script.",
            required=True,
        ),
    ]

    @property
    def overlays(self) -> Path:
        return Path(__file__).parent / "overlays"

    def register(self, surface: Surface) -> None:
        if surface.mcp is not None:
            register_tools(surface.mcp)

        if surface.cli is not None:
            surface.cli.add_typer(cli.app, name="gcp-cloud-run")
