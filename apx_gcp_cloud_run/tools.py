"""`gcp_cloud_run_*` MCP tools. Deploying stays with the core's `deploy` tool, which drives the target."""

from __future__ import annotations

from typing import Annotated, Any

from action_platform.mcp.annotations import READ_ONLY
from pydantic import BaseModel, Field

from apx_gcp_cloud_run.connect import connect_command as command
from apx_gcp_cloud_run.spec import app_of, service_name


class Connect(BaseModel):
    command: str
    service: str


def register_tools(mcp: Any) -> None:
    @mcp.tool(annotations=READ_ONLY)
    def connect_command(
        issuer: Annotated[str, Field(description="The platform's public url")],
        organization: Annotated[str, Field(description="The organization slug")],
        app: Annotated[
            str,
            Field(description="An app as <org>/<project>/<app>, to name its service"),
        ] = "",
        scope: Annotated[
            str, Field(description="The scope the service is for")
        ] = "dev",
    ) -> Connect:
        """The Cloud Shell command that connects a Google Cloud project to the platform, and the Cloud Run service name an app's scope gets. Runs nothing."""
        found = app_of(app)

        return Connect(
            command=command(issuer.rstrip("/"), organization),
            service=service_name(found, scope) if found else "",
        )
