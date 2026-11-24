"""Artifact Registry: whether the release's tag was pushed, read from the repository's tags."""

from __future__ import annotations

from typing import Callable
from urllib import parse

from action_platform.core.exception import DeployError

from apx_gcp_cloud_run.abc import Api, Registry
from apx_gcp_cloud_run.google import GoogleError
from apx_gcp_cloud_run.spec import Spec

ARTIFACT_REGISTRY = "https://artifactregistry.googleapis.com/v1"


class ArtifactRegistry(Registry):
    def __init__(self, api: Api, token: Callable[[], str]) -> None:
        self.api = api
        self.token = token

    def tag_exists(self, spec: Spec) -> bool:
        where = spec.artifact

        if where is None:
            raise DeployError(f"{spec.image} is not an Artifact Registry image")

        package = parse.quote(where["package"], safe="")
        tag = parse.quote(spec.version, safe="")
        url = (
            f"{ARTIFACT_REGISTRY}/projects/{where['project']}/locations/{where['location']}"
            f"/repositories/{where['repository']}/packages/{package}/tags/{tag}"
        )

        try:
            self.api.call("GET", url, token=self.token())
        except GoogleError as e:
            if e.status == 404:
                return False

            raise

        return True
