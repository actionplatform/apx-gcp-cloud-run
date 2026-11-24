"""What the deploy service account may do, asked with its own token through `testIamPermissions`: on the project for Cloud Run and Artifact Registry, on the runtime service account for `actAs`."""

from __future__ import annotations

from typing import Callable

from apx_gcp_cloud_run.abc import Api, Permissions
from apx_gcp_cloud_run.google import GoogleError
from apx_gcp_cloud_run.spec import Spec

RESOURCE_MANAGER = "https://cloudresourcemanager.googleapis.com/v1"
IAM = "https://iam.googleapis.com/v1"
ACT_AS = "iam.serviceAccounts.actAs"
ROLES = {
    "roles/run.admin": [
        "run.services.get",
        "run.services.create",
        "run.services.update",
        "run.services.delete",
        "run.services.setIamPolicy",
        "run.operations.get",
        "run.revisions.get",
        "run.revisions.list",
    ],
    "roles/artifactregistry.reader": ["artifactregistry.tags.get"],
}


class IamPermissions(Permissions):
    def __init__(self, api: Api, token: Callable[[], str]) -> None:
        self.api = api
        self.token = token

    def missing(self, spec: Spec) -> list[str]:
        wanted = [p for permissions in ROLES.values() for p in permissions]
        granted = self._test(
            f"{RESOURCE_MANAGER}/projects/{spec.project_id}:testIamPermissions", wanted
        )
        missing = [p for p in wanted if p not in granted]

        if spec.runtime_service_account:
            try:
                runtime = self._test(
                    f"{IAM}/projects/-/serviceAccounts/{spec.runtime_service_account}:testIamPermissions",
                    [ACT_AS],
                )
            except GoogleError as e:
                if e.status != 404:
                    raise

                runtime = []

            if ACT_AS not in runtime:
                missing.append(ACT_AS)

        return missing

    def _test(self, url: str, permissions: list[str]) -> list[str]:
        data = self.api.call(
            "POST", url, token=self.token(), body={"permissions": permissions}
        )

        return list(data.get("permissions") or [])


def fixes(spec: Spec, missing: list[str]) -> list[str]:
    """The `gcloud` commands that grant what is missing, one per role."""
    member = f"serviceAccount:{spec.service_account}"
    commands = [
        f"gcloud projects add-iam-policy-binding {spec.project_id} --member={member} --role={role} --condition=None"
        for role, permissions in ROLES.items()
        if any(p in missing for p in permissions)
    ]

    if ACT_AS in missing:
        commands.append(
            f"gcloud iam service-accounts add-iam-policy-binding {spec.runtime_service_account} "
            f"--project={spec.project_id} --member={member} --role=roles/iam.serviceAccountUser"
        )

    return commands
