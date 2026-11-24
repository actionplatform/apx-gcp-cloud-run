"""Readiness, one class per check, in the order the target runs them: the settings, the token exchange, the service account's roles, the published image, the service's state. Each reads; none changes anything."""

from __future__ import annotations

from action_platform.core.context import Check
from action_platform.core.exception import DeployError

from apx_gcp_cloud_run.abc import (
    Credentials,
    Permissions,
    Readiness,
    Registry,
    Services,
)
from apx_gcp_cloud_run.iam import fixes
from apx_gcp_cloud_run.run import state_of
from apx_gcp_cloud_run.spec import Spec

CONFIGURE = (
    "Plugins → GCP Cloud Run → Configure on the platform, or [deploy] in platform.toml"
)


class SettingsCheck(Readiness):
    id = "gcp.settings"

    def run(self, spec: Spec) -> Check:
        missing = spec.missing()

        if missing:
            return Check(
                self.id, False, "missing: " + ", ".join(missing), fix=CONFIGURE
            )

        return Check(self.id, True, f"{spec.service} in {spec.parent}")


class CredentialsCheck(Readiness):
    id = "gcp.credentials"

    def __init__(self, credentials: Credentials) -> None:
        self.credentials = credentials

    def run(self, spec: Spec) -> Check:
        try:
            self.credentials.access_token(spec)
        except DeployError as e:
            return Check(
                self.id,
                False,
                str(e),
                fix=(
                    "connect the project (Plugins → GCP Cloud Run → Connect Google Cloud): the provider must trust "
                    f"this platform for organization {spec.organization or '<org>'}, and {spec.service_account} must grant "
                    "roles/iam.workloadIdentityUser to the pool's principalSet for that organization"
                ),
            )

        return Check(self.id, True, f"acting as {spec.service_account}")


class PermissionsCheck(Readiness):
    id = "gcp.permissions"

    def __init__(self, permissions: Permissions) -> None:
        self.permissions = permissions

    def run(self, spec: Spec) -> Check:
        try:
            missing = self.permissions.missing(spec)
        except DeployError as e:
            return Check(
                self.id, True, f"not tested: {str(e)[:200]}", severity="warning"
            )

        if missing:
            return Check(
                self.id,
                False,
                f"{spec.service_account} lacks " + ", ".join(missing),
                fix=" && ".join(fixes(spec, missing)),
            )

        return Check(self.id, True, f"{spec.service_account} may deploy {spec.service}")


class ImageCheck(Readiness):
    id = "image.published"

    def __init__(self, registry: Registry) -> None:
        self.registry = registry

    def run(self, spec: Spec) -> Check:
        if spec.artifact is None:
            return Check(
                self.id,
                True,
                f"{spec.image or 'the image'} is not in Artifact Registry: not checked",
                severity="warning",
            )

        try:
            found = self.registry.tag_exists(spec)
        except DeployError as e:
            return Check(
                self.id,
                False,
                str(e),
                fix=" && ".join(fixes(spec, ["artifactregistry.tags.get"])),
            )

        if found:
            return Check(self.id, True, spec.reference)

        return Check(
            self.id,
            False,
            f"{spec.reference} is not published",
            fix="publish the release on GitHub: the overlay's docker-publish workflow pushes the tag",
        )


class ServiceStateCheck(Readiness):
    id = "service.state"

    def __init__(self, services: Services) -> None:
        self.services = services

    def run(self, spec: Spec) -> Check:
        service = self.services.get(spec)

        if service is None:
            return Check(self.id, True, "no service yet; the deploy creates it")

        state = state_of(service)
        message = (service.get("terminalCondition") or {}).get("message") or ""

        if state == "deploying":
            return Check(
                self.id,
                False,
                f"{spec.service} is mid-rollout",
                fix="wait for the running rollout to finish",
            )

        if state == "failed":
            return Check(
                self.id,
                True,
                f"the latest revision failed: {message}".rstrip(": "),
                severity="warning",
            )

        return Check(self.id, True, f"{spec.service} ready")
