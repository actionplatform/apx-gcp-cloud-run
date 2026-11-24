"""Connecting a Google Cloud project: `connect.sh` creates the pool, the provider trusting the platform for one organization, the deploy and runtime service accounts and the image repository."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

SCRIPT = Path(__file__).parent / "connect.sh"
CLOUD_SHELL = "https://shell.cloud.google.com/?show=terminal"


def connect_command(
    issuer: str = "{issuer}", organization: str = "{organization}"
) -> str:
    """What the user pastes in Cloud Shell: this release's connect script from GitHub, run for the platform and the organization."""
    try:
        ref = f"v{version('apx-gcp-cloud-run')}"
    except PackageNotFoundError:
        ref = "master"

    script = f"https://raw.githubusercontent.com/actionplatform/apx-gcp-cloud-run/{ref}/apx_gcp_cloud_run/connect/connect.sh"

    return f"curl -fsSL {script} -o ap-connect.sh && sh ap-connect.sh {issuer} {organization}"
