"""The responsibilities a Cloud Run deploy is made of, one contract each; `CloudRunTarget` composes an implementation of every one."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from action_platform.core.context import Check

    from apx_gcp_cloud_run.spec import Spec

Record = dict[str, Any]


class Api(ABC):
    """HTTPS to Google's APIs. Raises `GoogleError` with the status and Google's message; never puts a token in it."""

    @abstractmethod
    def call(
        self,
        method: str,
        url: str,
        token: str | None = None,
        body: Record | None = None,
        form: dict[str, str] | None = None,
    ) -> Record: ...


class Credentials(ABC):
    """Who the deploy acts as: an access token for the deploy service account."""

    @abstractmethod
    def access_token(self, spec: Spec) -> str: ...


class Services(ABC):
    """The scope's Cloud Run service and its revisions."""

    @abstractmethod
    def get(self, spec: Spec) -> Record | None:
        """The service as Cloud Run describes it, or None when there is none."""

    @abstractmethod
    def apply(self, spec: Spec, service: Record) -> Record:
        """Create or replace the service; the long-running operation."""

    @abstractmethod
    def wait(self, operation: Record) -> str | None:
        """Follow an operation to its end: None when it succeeded, else Google's reason."""

    @abstractmethod
    def delete(self, spec: Spec) -> Record | None:
        """Delete the service; the operation, or None when there was nothing to delete."""

    @abstractmethod
    def revisions(self, spec: Spec) -> list[Record]:
        """The service's revisions, newest first."""

    @abstractmethod
    def revision(self, name: str) -> Record | None:
        """One revision by its full resource name."""


class Registry(ABC):
    """Artifact Registry, where the CI publishes the image."""

    @abstractmethod
    def tag_exists(self, spec: Spec) -> bool: ...


class Permissions(ABC):
    """What the deploy service account may do."""

    @abstractmethod
    def missing(self, spec: Spec) -> list[str]:
        """The permissions the deploy needs that the service account lacks."""


class Health(ABC):
    """Whether the deployed service answers."""

    @abstractmethod
    def answers(self, url: str) -> str | None:
        """None when `url` answers 2xx in time; otherwise what it did instead."""


class Readiness(ABC):
    """One check a deploy has to pass, run without building or changing anything."""

    id: str

    @abstractmethod
    def run(self, spec: Spec) -> Check: ...
