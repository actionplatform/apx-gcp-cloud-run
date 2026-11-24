"""The Cloud Run Admin API v2: one service per scope, replaced whole on every deploy, its long-running operations followed to the end."""

from __future__ import annotations

import time
from typing import Any, Callable

from action_platform.core.exception import DeployError

from apx_gcp_cloud_run.abc import Api, Record, Services
from apx_gcp_cloud_run.google import GoogleError
from apx_gcp_cloud_run.spec import Spec, label

RUN = "https://run.googleapis.com/v2"
LATEST = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
REVISION = "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION"
SUCCEEDED = "CONDITION_SUCCEEDED"
FAILED = "CONDITION_FAILED"
OUTPUT_ONLY = {
    "uid",
    "generation",
    "createTime",
    "updateTime",
    "deleteTime",
    "expireTime",
    "creator",
    "lastModifier",
    "observedGeneration",
    "terminalCondition",
    "conditions",
    "latestReadyRevision",
    "latestCreatedRevision",
    "trafficStatuses",
    "uri",
    "urls",
    "reconciling",
    "satisfiesPzs",
}


class CloudRun(Services):
    def __init__(
        self,
        api: Api,
        token: Callable[[], str],
        pause: float = 5,
        timeout: float = 900,
    ) -> None:
        self.api = api
        self.token = token
        self.pause = pause
        self.timeout = timeout

    def _call(self, method: str, path: str, body: Record | None = None) -> Record:
        return self.api.call(
            method,
            f"{RUN}/{path}",
            token=self.token(),
            body=body,
        )

    def get(self, spec: Spec) -> Record | None:
        try:
            return self._call("GET", spec.name)
        except GoogleError as e:
            if e.status == 404:
                return None

            raise

    def apply(self, spec: Spec, service: Record) -> Record:
        return self._call("PATCH", f"{spec.name}?allowMissing=true", service)

    def wait(self, operation: Record) -> str | None:
        deadline = time.monotonic() + self.timeout

        while not operation.get("done"):
            if time.monotonic() > deadline:
                return (
                    f"{operation.get('name')} still running after {int(self.timeout)}s"
                )

            time.sleep(self.pause)
            operation = self._call("GET", str(operation["name"]))

        problem = operation.get("error")

        return str(problem.get("message") or problem) if problem else None

    def delete(self, spec: Spec) -> Record | None:
        try:
            return self._call("DELETE", spec.name)
        except GoogleError as e:
            if e.status == 404:
                return None

            raise

    def revisions(self, spec: Spec) -> list[Record]:
        rows: list[Record] = []
        token = ""

        while True:
            page = self._call(
                "GET",
                f"{spec.name}/revisions" + (f"?pageToken={token}" if token else ""),
            )
            rows.extend(page.get("revisions") or [])
            token = page.get("nextPageToken") or ""

            if not token:
                break

        return sorted(rows, key=lambda r: str(r.get("createTime") or ""), reverse=True)

    def revision(self, name: str) -> Record | None:
        try:
            return self._call("GET", name)
        except GoogleError as e:
            if e.status == 404:
                return None

            raise


def desired(spec: Spec) -> Record:
    """The whole service a deploy asks for: the release's image on the scope, every request to the newest revision."""
    container: Record = {
        "image": spec.reference,
        "ports": [{"containerPort": spec.port}],
    }

    if spec.env:
        container["env"] = [{"name": k, "value": v} for k, v in spec.env.items()]

    template: Record = {
        "labels": {"ap-version": label(spec.version)},
        "containers": [container],
    }

    if spec.runtime_service_account:
        template["serviceAccount"] = spec.runtime_service_account

    return {
        "name": spec.name,
        "labels": {"managed-by": "action-platform", "ap-scope": label(spec.stage)},
        "ingress": "INGRESS_TRAFFIC_ALL",
        "invokerIamDisabled": spec.public,
        "template": template,
        "traffic": [{"type": LATEST, "percent": 100}],
    }


def writable(service: Record, **changes: Any) -> Record:
    """`service` as Cloud Run returned it, without the fields only Cloud Run sets, with `changes` applied."""
    return {
        **{k: v for k, v in service.items() if k not in OUTPUT_ONLY},
        **changes,
    }


def short(revision: str) -> str:
    return revision.rsplit("/", 1)[-1]


def serving(service: Record) -> list[tuple[str, int]]:
    """Each revision that takes requests and its share, largest first; a `LATEST` entry resolved to the newest ready revision."""
    shares: dict[str, int] = {}

    for row in service.get("trafficStatuses") or []:
        revision = row.get("revision") or (
            service.get("latestReadyRevision") if row.get("type") == LATEST else None
        )
        percent = int(row.get("percent") or 0)

        if revision and percent:
            name = short(str(revision))
            shares[name] = shares.get(name, 0) + percent

    return sorted(shares.items(), key=lambda kv: kv[1], reverse=True)


def image_of(revision: Record | None) -> str:
    containers = (revision or {}).get("containers") or [{}]

    return str(containers[0].get("image") or "")


def tag_of(image: str) -> str | None:
    tail = image.rsplit("/", 1)[-1]

    return tail.split(":", 1)[1].split("@", 1)[0] if ":" in tail else None


def ready(revision: Record) -> bool:
    for condition in revision.get("conditions") or []:
        if condition.get("type") == "Ready":
            return condition.get("state") == SUCCEEDED

    return not revision.get("reconciling")


def state_of(service: Record) -> str:
    """`ready`, `deploying` or `failed`, from the service's terminal condition."""
    condition = service.get("terminalCondition") or {}

    if service.get("reconciling") or condition.get("state") not in (SUCCEEDED, FAILED):
        return "deploying"

    return "ready" if condition.get("state") == SUCCEEDED else "failed"


def require(service: Record | None, spec: Spec) -> Record:
    if service is None:
        raise DeployError(f"no Cloud Run service {spec.service} in {spec.parent}")

    return service
