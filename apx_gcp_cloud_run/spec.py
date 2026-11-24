"""What one call is about, resolved once from the target's options, the context and `platform.toml`: the scope, the service, the image, the identity to federate. Nothing here talks to Google."""

from __future__ import annotations

import hashlib
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from action_platform.core.context import Context

from apx_gcp_cloud_run.settings import setting

MAX_NAME = 49
PROVIDER = re.compile(
    r"projects/\d+/locations/global/workloadIdentityPools/[a-z0-9-]+/providers/[a-z0-9-]+"
)
ARTIFACT = re.compile(
    r"(?P<location>[a-z0-9-]+)-docker\.pkg\.dev/(?P<project>[^/]+)/(?P<repository>[^/]+)/(?P<package>.+)"
)


@dataclass(frozen=True)
class Options:
    """What `[deploy]` in platform.toml passes to the target."""

    project_id: str | None = None
    region: str | None = None
    workload_identity_provider: str | None = None
    service_account: str | None = None
    runtime_service_account: str | None = None
    repository: str = "action-platform"
    image: str | None = None
    service: str | None = None
    app: str | None = None
    port: int = 8000
    health: str = "/health"
    public: bool = True
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Spec:
    stage: str
    version: str
    project_id: str | None
    region: str | None
    provider: str | None
    service_account: str | None
    runtime_service_account: str | None
    app: str | None
    service: str | None
    image: str | None
    port: int = 8000
    health: str = "/health"
    public: bool = True
    env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, ctx: Context, options: Options) -> Spec:
        manifest = _manifest(ctx.repo_root)
        project_id = setting("project_id", options.project_id, ctx.env)
        region = setting("region", options.region, ctx.env)
        stage = ctx.stage or "dev"
        app = app_of(options.app or ctx.env.get("AP_APP") or os.environ.get("AP_APP"))

        return cls(
            stage=stage,
            version=ctx.next_version,
            project_id=project_id,
            region=region,
            provider=provider_of(
                setting(
                    "workload_identity_provider",
                    options.workload_identity_provider,
                    ctx.env,
                )
            ),
            service_account=setting(
                "service_account", options.service_account, ctx.env
            ),
            runtime_service_account=options.runtime_service_account
            or (
                f"ap-runtime@{project_id}.iam.gserviceaccount.com"
                if project_id
                else None
            ),
            app=app,
            service=options.service or (service_name(app, stage) if app else None),
            image=options.image or _image(manifest, project_id, region, options),
            port=int(options.port),
            health=options.health,
            public=bool(options.public),
            env={str(k): str(v) for k, v in options.env.items()},
        )

    @property
    def organization(self) -> str | None:
        return self.app.split("/")[0] if self.app else None

    @property
    def audience(self) -> str:
        """The `aud` a provider accepts by default: its full resource name on `https://iam.googleapis.com/`."""
        return f"https://iam.googleapis.com/{self.provider}"

    @property
    def parent(self) -> str:
        return f"projects/{self.project_id}/locations/{self.region}"

    @property
    def name(self) -> str:
        return f"{self.parent}/services/{self.service}"

    @property
    def reference(self) -> str:
        return f"{self.image}:{self.version}"

    @property
    def artifact(self) -> dict[str, str] | None:
        """Location, project, repository and package when the image lives in Artifact Registry."""
        found = ARTIFACT.fullmatch(self.image or "")

        return found.groupdict() if found else None

    def missing(self) -> list[str]:
        """The settings the deploy cannot go without, by the name the plugin's options give them."""
        wanted = {
            "project_id": self.project_id,
            "region": self.region,
            "workload_identity_provider": self.provider,
            "service_account": self.service_account,
        }
        missing = [k for k, v in wanted.items() if not v]

        if not self.service:
            missing.append(
                "the app (AP_APP=<org>/<project>/<app>, or [deploy] service)"
            )

        return missing


def app_of(value: str | None) -> str | None:
    """`<org>/<project>/<app>`, or None when `value` is not that."""
    parts = (value or "").strip("/").split("/")

    return "/".join(parts) if len(parts) == 3 and all(parts) else None


def provider_of(value: str | None) -> str | None:
    """`projects/<number>/locations/global/workloadIdentityPools/<pool>/providers/<id>` from what the user pasted, with or without the `//iam.googleapis.com/` prefix; None when it is not one."""
    if not value:
        return None

    found = PROVIDER.search(value)

    return found.group(0) if found else None


def service_name(app: str, scope: str) -> str:
    """`ap-<org>-<project>-<app>-<scope>` as Cloud Run takes it: lower case, digits and hyphens, a letter first, at most 49 characters — a longer one keeps its head and a hash of the whole."""
    raw = "-".join(["ap", *app.strip("/").split("/"), scope])
    name = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")

    if len(name) <= MAX_NAME:
        return name

    digest = hashlib.sha256(name.encode()).hexdigest()[:8]

    return f"{name[: MAX_NAME - 9].rstrip('-')}-{digest}"


def label(value: str) -> str:
    """A Cloud Run label value: lower case, digits, `_` and `-`, at most 63 characters."""
    return re.sub(r"[^a-z0-9_-]+", "-", value.lower())[:63]


def _image(
    manifest: dict[str, Any],
    project_id: str | None,
    region: str | None,
    options: Options,
) -> str | None:
    """`<region>-docker.pkg.dev/<project>/<repository>/<name>`, the name being the repository's (`[source_host] repo`) or the project's — what the overlay's workflow pushes."""
    if not (project_id and region):
        return None

    repo = str(manifest.get("source_host", {}).get("repo") or "")
    name = repo.rsplit("/", 1)[-1] or str(manifest.get("project", {}).get("name") or "")

    if not name:
        return None

    name = re.sub(r"[^a-z0-9._-]+", "-", name.lower())

    return f"{region}-docker.pkg.dev/{project_id}/{options.repository}/{name}"


def _manifest(root: Path) -> dict[str, Any]:
    path = root / "platform.toml"

    return tomllib.loads(path.read_text()) if path.exists() else {}
