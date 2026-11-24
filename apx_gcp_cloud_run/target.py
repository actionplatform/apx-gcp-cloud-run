"""The `gcp/cloud-run` deploy target: the CI pushes `<image>:<version>` to Artifact Registry, the platform points the scope's Cloud Run service at that tag and sends every request to the new revision.

    [deploy]
    target = "gcp/cloud-run"
    project_id = "acme-prod"                 # or the plugin option / AP_GCP_CLOUD_RUN_PROJECT_ID
    region = "us-central1"                   # or the plugin option
    repository = "action-platform"           # the Artifact Registry repository the CI pushes to
    port = 8000                              # what the container listens on
    health = "/health"                       # checked after the deploy; "" skips it
    public = true                            # anyone may call the service's url

    [deploy.env]                             # set on every revision
    LOG_LEVEL = "info"

One service per scope, `ap-<org>-<project>-<app>-<scope>`. The identity is the plugin's `workload_identity_provider` and `service_account`: no key anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from action_platform.abc import DeployTarget
from action_platform.core.context import Check, Context, DeployResult, Diagnosis
from action_platform.core.exception import DeployError
from action_platform.logging import emit, logger

from apx_gcp_cloud_run.abc import (
    Api,
    Credentials,
    Health,
    Permissions,
    Readiness,
    Registry,
    Services,
)
from apx_gcp_cloud_run.checks import (
    CredentialsCheck,
    ImageCheck,
    PermissionsCheck,
    ServiceStateCheck,
    SettingsCheck,
)
from apx_gcp_cloud_run.credentials import Federation, identity_of
from apx_gcp_cloud_run.google import GoogleApi
from apx_gcp_cloud_run.health import HttpHealth
from apx_gcp_cloud_run.iam import IamPermissions
from apx_gcp_cloud_run.registry import ArtifactRegistry
from apx_gcp_cloud_run.run import (
    REVISION,
    CloudRun,
    desired,
    image_of,
    ready,
    require,
    serving,
    short,
    state_of,
    tag_of,
    writable,
)
from apx_gcp_cloud_run.spec import Options, Spec


@dataclass(frozen=True)
class Parts:
    """Every responsibility wired to one identity."""

    credentials: Credentials
    services: Services
    registry: Registry
    permissions: Permissions


class CloudRunTarget(DeployTarget):
    name = "gcp/cloud-run"
    health: Health = HttpHealth()
    pause: float = 5

    def __init__(
        self,
        project_id: str | None = None,
        region: str | None = None,
        workload_identity_provider: str | None = None,
        service_account: str | None = None,
        runtime_service_account: str | None = None,
        repository: str = "action-platform",
        image: str | None = None,
        service: str | None = None,
        app: str | None = None,
        port: int = 8000,
        health: str = "/health",
        public: bool = True,
        env: dict[str, str] | None = None,
        **_: object,
    ) -> None:
        self.options = Options(
            project_id=project_id,
            region=region,
            workload_identity_provider=workload_identity_provider,
            service_account=service_account,
            runtime_service_account=runtime_service_account,
            repository=repository,
            image=image,
            service=service,
            app=app,
            port=int(port),
            health=health,
            public=bool(public),
            env=dict(env or {}),
        )
        self._last: Context | None = None

    def spec(self, ctx: Context) -> Spec:
        self._last = ctx

        return Spec.of(ctx, self.options)

    def attach(self, ctx: Context) -> None:
        """The app's context for `verify` and `url`, which the platform asks without one."""
        self._last = ctx

    def transport(self) -> Api:
        return GoogleApi()

    def identity(self, ctx: Context) -> Callable[[str], str | None]:
        return identity_of(ctx.identity_token)

    def parts(self, spec: Spec, ctx: Context) -> Parts:
        """The Google implementation of every responsibility; override to swap one."""
        api = self.transport()
        credentials = Federation(api, self.identity(ctx))

        def token() -> str:
            return credentials.access_token(spec)

        return Parts(
            credentials=credentials,
            services=CloudRun(api, token, pause=self.pause),
            registry=ArtifactRegistry(api, token),
            permissions=IamPermissions(api, token),
        )

    def preflight(self, ctx: Context) -> None:
        blocking = [c for c in self.readiness(ctx) if c.blocking]

        if blocking:
            raise DeployError("; ".join(f"{c.id}: {c.detail}" for c in blocking))

    def readiness(self, ctx: Context) -> list[Check]:
        spec = self.spec(ctx)
        checks = [SettingsCheck().run(spec)]

        if not checks[-1].ok:
            return checks

        parts = self.parts(spec, ctx)
        checks.append(CredentialsCheck(parts.credentials).run(spec))

        if not checks[-1].ok:
            return checks

        following: list[Readiness] = [
            PermissionsCheck(parts.permissions),
            ImageCheck(parts.registry),
            ServiceStateCheck(parts.services),
        ]

        for check in following:
            try:
                checks.append(check.run(spec))
            except DeployError as e:
                checks.append(Check(check.id, False, str(e)))

        return checks

    def deploy(self, ctx: Context) -> DeployResult:
        spec = self.ready_spec(ctx)
        parts = self.parts(spec, ctx)
        image = ImageCheck(parts.registry).run(spec)

        if image.blocking:
            raise DeployError(f"{image.detail}: {image.fix}")

        emit(f"gcp/cloud-run: {spec.reference} → {spec.service} ({spec.parent})")

        try:
            problem = parts.services.wait(parts.services.apply(spec, desired(spec)))
            url = (parts.services.get(spec) or {}).get("uri")
        except DeployError as e:
            problem, url = str(e), None

        if not problem and url and spec.health:
            problem = self.health.answers(url.rstrip("/") + spec.health)

        if problem:
            emit(f"gcp/cloud-run: {problem}")
            logger.info("gcp/cloud-run: %s failed on %s", spec.version, spec.stage)

            return DeployResult(
                ok=False,
                target=self.name,
                version=spec.version,
                url=url,
                error=f"cloud run: {problem.splitlines()[0]}",
            )

        logger.info("gcp/cloud-run: deployed %s to %s", spec.version, spec.stage)

        return DeployResult(ok=True, target=self.name, version=spec.version, url=url)

    def switch_traffic(self, ctx: Context, weight: int) -> None:
        """`weight` percent to the newest ready revision, the rest to the one that served before it."""
        spec = self.ready_spec(ctx)
        parts = self.parts(spec, ctx)
        service = require(parts.services.get(spec), spec)
        latest = short(str(service.get("latestReadyRevision") or ""))

        if not latest:
            raise DeployError(f"{spec.service} has no ready revision")

        weight = max(0, min(100, int(weight)))
        others = [name for name, _ in serving(service) if name != latest]

        if weight < 100 and not others:
            raise DeployError(
                f"only {latest} serves {spec.service}: nothing to split with"
            )

        traffic = [
            {"type": REVISION, "revision": revision, "percent": percent}
            for revision, percent in (
                (latest, weight),
                (others[0] if others else latest, 100 - weight),
            )
            if percent
        ]
        self._shift(parts, spec, service, traffic)
        emit(f"gcp/cloud-run: {weight}% of {spec.service} to {latest}")

    def rollback(self, ctx: Context, to_version: str | None = None) -> None:
        spec = self.ready_spec(ctx)
        parts = self.parts(spec, ctx)
        service = require(parts.services.get(spec), spec)
        revisions = [r for r in parts.services.revisions(spec) if ready(r)]
        shares = serving(service)
        current = shares[0][0] if shares else None

        if to_version:
            candidates = [r for r in revisions if tag_of(image_of(r)) == to_version]
        else:
            names = [short(str(r.get("name"))) for r in revisions]
            at = names.index(current) if current in names else -1
            now = image_of(revisions[at]) if at >= 0 else ""
            candidates = [r for r in revisions[at + 1 :] if image_of(r) != now]

        if not candidates:
            raise DeployError(
                f"no ready revision of {spec.service} runs "
                + (to_version if to_version else "an earlier version")
            )

        revision = short(str(candidates[0]["name"]))
        self._shift(
            parts,
            spec,
            service,
            [{"type": REVISION, "revision": revision, "percent": 100}],
        )
        emit(f"gcp/cloud-run: {spec.service} serves {revision} again")

    def diagnose(self, ctx: Context) -> Diagnosis:
        spec = self.spec(ctx)

        if spec.missing():
            return Diagnosis(
                ok=False,
                target=self.name,
                status="unconfigured",
                details={"missing": ", ".join(spec.missing())},
            )

        parts = self.parts(spec, ctx)

        try:
            service = parts.services.get(spec)
            shares = serving(service or {})
            revision = (
                parts.services.revision(f"{spec.name}/revisions/{shares[0][0]}")
                if shares
                else None
            )
        except DeployError as e:
            return Diagnosis(
                ok=False,
                target=self.name,
                status="unreachable",
                details={"error": str(e)},
            )

        if service is None:
            return Diagnosis(
                ok=False,
                target=self.name,
                status="missing",
                details={"service": str(spec.service)},
            )

        state = state_of(service)
        image = image_of(revision)

        return Diagnosis(
            ok=state == "ready",
            target=self.name,
            status=state,
            version=tag_of(image),
            url=service.get("uri"),
            details={
                "service": str(spec.service),
                "location": spec.parent,
                "revision": shares[0][0] if shares else "",
                "image": image,
                "message": str(
                    (service.get("terminalCondition") or {}).get("message") or ""
                ),
            },
        )

    def delete(self, ctx: Context) -> None:
        spec = self.ready_spec(ctx)
        parts = self.parts(spec, ctx)
        operation = parts.services.delete(spec)

        if operation is None:
            return

        problem = parts.services.wait(operation)

        if problem:
            raise DeployError(f"deleting {spec.service}: {problem}")

        logger.info("gcp/cloud-run: deleted %s", spec.service)

    def verify(self, version: str, stage: str | None = None) -> bool:
        spec, ctx = self._spec_for(stage)
        parts = self.parts(self.configured(spec), ctx)
        service = parts.services.get(spec)
        shares = serving(service or {})

        if not shares:
            return False

        revision = parts.services.revision(f"{spec.name}/revisions/{shares[0][0]}")

        return tag_of(image_of(revision)) == version

    def url(self, version: str, stage: str | None = None) -> str | None:
        spec, ctx = self._spec_for(stage)

        if spec.missing():
            return None

        try:
            service = self.parts(spec, ctx).services.get(spec)
        except DeployError:
            return None

        return service.get("uri") if service else None

    def ready_spec(self, ctx: Context) -> Spec:
        return self.configured(self.spec(ctx))

    def configured(self, spec: Spec) -> Spec:
        missing = spec.missing()

        if missing:
            raise DeployError("gcp/cloud-run: missing " + ", ".join(missing))

        return spec

    def _shift(
        self, parts: Parts, spec: Spec, service: dict, traffic: list[dict]
    ) -> None:
        problem = parts.services.wait(
            parts.services.apply(spec, writable(service, traffic=traffic))
        )

        if problem:
            raise DeployError(f"moving traffic on {spec.service}: {problem}")

    def _spec_for(self, stage: str | None) -> tuple[Spec, Context]:
        """`verify` and `url` come without a context: the last one this target saw or was attached, on the stage asked for."""
        base = self._last or Context(repo_root=Path.cwd())
        ctx = replace(base, stage=stage or base.stage or "dev")

        return Spec.of(ctx, self.options), ctx
