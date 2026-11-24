"""Google in memory, answering only what the real APIs answer: STS `v1/token`, IAM Credentials `generateAccessToken`, `testIamPermissions` on Resource Manager and IAM, Artifact Registry tags, and the Cloud Run Admin API v2 — services, revisions, long-running operations."""

from __future__ import annotations

import copy
import itertools
import uuid
from urllib import parse

from apx_gcp_cloud_run.abc import Api, Record
from apx_gcp_cloud_run.google import GoogleError
from apx_gcp_cloud_run.iam import ROLES

FEDERATED = "ya29.d.federated"
ACCESS = "ya29.c.impersonated"
GRANTED = [p for permissions in ROLES.values() for p in permissions]
NOW = "2026-11-24T20:00:00.000000Z"


class FakeGoogle(Api):
    def __init__(
        self,
        trusted: str = "platform-token",
        impersonation: bool = True,
        granted: list[str] | None = None,
        act_as: bool = True,
        runtime_exists: bool = True,
        published: set[str] | None = None,
        failing_tags: set[str] | None = None,
        reconciling: bool = False,
    ) -> None:
        self.trusted = trusted
        self.impersonation = impersonation
        self.granted = GRANTED if granted is None else granted
        self.act_as = act_as
        self.runtime_exists = runtime_exists
        self.published = published if published is not None else {"1.0.0", "1.1.0"}
        self.failing_tags = failing_tags or set()
        self.reconciling = reconciling
        self.services: dict[str, Record] = {}
        self.revisions: dict[str, list[Record]] = {}
        self.operations: dict[str, Record] = {}
        self.templates: dict[str, Record] = {}
        self.calls: list[tuple[str, str, Record | None]] = []
        self.forms: list[dict[str, str]] = []
        self.counter = itertools.count(1)

    def call(
        self,
        method: str,
        url: str,
        token: str | None = None,
        body: Record | None = None,
        form: dict[str, str] | None = None,
    ) -> Record:
        parts = parse.urlsplit(url)
        self.calls.append((method, f"{parts.netloc}{parts.path}", body))

        if parts.netloc == "sts.googleapis.com":
            return self._sts(form or {})

        if parts.netloc == "iamcredentials.googleapis.com":
            return self._impersonate(token)

        if token != ACCESS:
            raise GoogleError(
                401,
                f"{method} {parts.netloc}{parts.path} answered 401: Request had invalid authentication credentials.",
            )

        if parts.netloc == "cloudresourcemanager.googleapis.com":
            asked = (body or {}).get("permissions") or []
            granted = [p for p in asked if p in self.granted]

            return {"permissions": granted} if granted else {}

        if parts.netloc == "iam.googleapis.com":
            if not self.runtime_exists:
                raise GoogleError(404, "Not found; Gaia id not found for email")

            return {"permissions": list(body["permissions"])} if self.act_as else {}

        if parts.netloc == "artifactregistry.googleapis.com":
            return self._tag(parse.unquote(parts.path))

        return self._run(method, parts.path.removeprefix("/v2/"), parts.query, body)

    def _sts(self, form: dict[str, str]) -> Record:
        self.forms.append(form)

        if form.get("subject_token") != self.trusted:
            raise GoogleError(
                400,
                "POST sts.googleapis.com/v1/token answered 400: invalid_grant: The given credential is rejected by the attribute condition.",
            )

        return {
            "access_token": FEDERATED,
            "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "token_type": "Bearer",
            "expires_in": 3599,
        }

    def _impersonate(self, token: str | None) -> Record:
        if token != FEDERATED or not self.impersonation:
            raise GoogleError(
                403,
                "POST iamcredentials.googleapis.com answered 403: Permission 'iam.serviceAccounts.getAccessToken' denied on resource (or it may not exist).",
            )

        return {"accessToken": ACCESS, "expireTime": "2026-11-24T21:00:00Z"}

    def _tag(self, path: str) -> Record:
        tag = path.rsplit("/tags/", 1)[1]

        if tag not in self.published:
            raise GoogleError(404, "Requested entity was not found.")

        return {
            "name": path.removeprefix("/v1/"),
            "version": path.removeprefix("/v1/").rsplit("/tags/", 1)[0]
            + "/versions/sha256:0f0f",
        }

    def _run(self, method: str, path: str, query: str, body: Record | None) -> Record:
        if "/operations/" in path:
            operation = self.operations[path]
            operation["done"] = True

            return copy.deepcopy(operation)

        if path.endswith("/revisions"):
            service = path.removesuffix("/revisions")

            return {"revisions": copy.deepcopy(self.revisions.get(service, []))}

        if "/revisions/" in path:
            service = path.split("/revisions/", 1)[0]

            for revision in self.revisions.get(service, []):
                if revision["name"] == path:
                    return copy.deepcopy(revision)

            raise GoogleError(404, f"Revision {path} not found.")

        if method == "GET":
            if path not in self.services:
                raise GoogleError(
                    404,
                    f"Resource '{path.rsplit('/', 1)[1]}' of kind 'SERVICE' in region does not exist.",
                )

            return copy.deepcopy(self.services[path])

        if method == "DELETE":
            if path not in self.services:
                raise GoogleError(
                    404,
                    f"Resource '{path.rsplit('/', 1)[1]}' of kind 'SERVICE' in region does not exist.",
                )

            gone = self.services.pop(path)
            self.revisions.pop(path, None)
            self.templates.pop(path, None)

            return self._operation(path, gone)

        if method == "PATCH":
            if path not in self.services and "allowMissing=true" not in query:
                raise GoogleError(404, "not found")

            return self._operation(path, self._apply(path, body or {}))

        raise AssertionError(f"unexpected {method} {path}")

    def _apply(self, name: str, body: Record) -> Record:
        service = self.services.get(name) or {
            "name": name,
            "uid": str(uuid.uuid4()),
            "generation": "0",
            "createTime": NOW,
            "uri": f"https://{name.rsplit('/', 1)[1]}-abcd1234-uc.a.run.app",
            "urls": [f"https://{name.rsplit('/', 1)[1]}-123456789.us-central1.run.app"],
        }
        revisions = self.revisions.setdefault(name, [])
        error = None
        created = revisions[0] if revisions else None

        if not created or body.get("template") != self.templates.get(name):
            image = body["template"]["containers"][0]["image"]
            short = f"{name.rsplit('/', 1)[1]}-{next(self.counter):05d}-xyz"
            failed = image.rsplit(":", 1)[1] in self.failing_tags
            created = {
                "name": f"{name}/revisions/{short}",
                "uid": str(uuid.uuid4()),
                "generation": "1",
                "createTime": f"2026-11-24T20:{len(revisions):02d}:00.000000Z",
                "service": name.rsplit("/", 1)[1],
                "containers": copy.deepcopy(body["template"]["containers"]),
                "serviceAccount": body["template"].get("serviceAccount"),
                "conditions": [
                    {
                        "type": "Ready",
                        "state": "CONDITION_FAILED"
                        if failed
                        else "CONDITION_SUCCEEDED",
                    }
                ],
            }
            revisions.insert(0, created)
            self.templates[name] = copy.deepcopy(body["template"])

            if failed:
                error = {
                    "code": 9,
                    "message": f"Revision '{short}' is not ready and cannot serve traffic. The user-provided container failed to start and listen on the port defined provided by the PORT=8000 environment variable within the allocated timeout.",
                }

        service.update(
            {k: copy.deepcopy(v) for k, v in body.items() if k != "name"},
            generation=str(int(service["generation"]) + 1),
            updateTime=NOW,
            latestCreatedRevision=created["name"],
            reconciling=self.reconciling,
            etag=f'"{uuid.uuid4().hex}"',
        )
        service["observedGeneration"] = service["generation"]

        if error:
            service["terminalCondition"] = {
                "type": "Ready",
                "state": "CONDITION_FAILED",
                "message": error["message"],
            }
        else:
            service["latestReadyRevision"] = created["name"]
            service["terminalCondition"] = {
                "type": "Ready",
                "state": "CONDITION_PENDING"
                if self.reconciling
                else "CONDITION_SUCCEEDED",
            }
            service["trafficStatuses"] = [
                {"type": t["type"], "percent": t["percent"]}
                | ({"revision": t["revision"]} if t.get("revision") else {})
                for t in body.get("traffic") or []
            ]

        self.services[name] = service

        return {"error": error} if error else service

    def _operation(self, name: str, result: Record) -> Record:
        operation_name = f"{name.split('/services/')[0]}/operations/{uuid.uuid4()}"
        operation = {
            "name": operation_name,
            "metadata": {"@type": "type.googleapis.com/google.cloud.run.v2.Service"},
            "done": False,
        }
        finished = dict(operation)

        if "error" in result:
            finished["error"] = result["error"]
        else:
            finished["response"] = {
                "@type": "type.googleapis.com/google.cloud.run.v2.Service",
                **copy.deepcopy(result),
            }

        self.operations[operation_name] = finished

        return operation
