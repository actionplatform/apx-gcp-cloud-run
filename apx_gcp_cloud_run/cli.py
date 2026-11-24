"""`action-platform gcp-cloud-run …`: connect a Google Cloud project from a machine logged in to it, or print what to paste in Cloud Shell."""

from __future__ import annotations

import os
import shutil

import typer
from action_platform.core.process import stream

from apx_gcp_cloud_run.connect import SCRIPT, connect_command
from apx_gcp_cloud_run.spec import app_of, service_name

app = typer.Typer(
    help="Google Cloud Run: connect a project, name a scope's service.",
    no_args_is_help=True,
)


@app.command("connect")
def connect(
    issuer: str = typer.Argument(..., help="The platform's public url"),
    organization: str = typer.Argument(..., help="The organization slug"),
    project: str = typer.Option("", help="Project ID; default: gcloud's project"),
    region: str = typer.Option("us-central1", help="Region of the image repository"),
    github_owner: str = typer.Option(
        "", help="Let this GitHub owner's Actions push images to the repository"
    ),
) -> None:
    """Connect the project gcloud is logged in to: pool, provider, service accounts, repository. Safe to run again."""
    if shutil.which("gcloud") is None:
        typer.echo(
            "gcloud not found: https://cloud.google.com/sdk/docs/install", err=True
        )
        raise typer.Exit(1)

    env = {**os.environ, "REGION": region, "GITHUB_OWNER": github_owner}

    if project:
        env["PROJECT_ID"] = project

    result = stream(["sh", str(SCRIPT), issuer, organization], env=env)

    if not result.ok:
        raise typer.Exit(result.returncode)


@app.command("command")
def command(
    issuer: str = typer.Argument(..., help="The platform's public url"),
    organization: str = typer.Argument(..., help="The organization slug"),
) -> None:
    """Print the command to paste in Cloud Shell."""
    typer.echo(connect_command(issuer.rstrip("/"), organization))


@app.command("service")
def service(
    app_path: str = typer.Argument(..., help="<org>/<project>/<app>"),
    scope: str = typer.Argument("dev"),
) -> None:
    """The Cloud Run service name a scope of an app gets."""
    found = app_of(app_path)

    if found is None:
        typer.echo("the app is <org>/<project>/<app>", err=True)
        raise typer.Exit(2)

    typer.echo(service_name(found, scope))
