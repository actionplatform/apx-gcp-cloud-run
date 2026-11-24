"""Keyless access: the platform's OIDC token for the provider's audience, exchanged at Google STS for a federated token, which impersonates the deploy service account through IAM Credentials. No key is stored anywhere, and no token is ever logged."""

from __future__ import annotations

from typing import Callable

from action_platform.core.exception import ActionPlatformError, DeployError
from action_platform.remote.client import Remote

from apx_gcp_cloud_run.abc import Api, Credentials
from apx_gcp_cloud_run.spec import Spec

STS = "https://sts.googleapis.com/v1/token"
IAM_CREDENTIALS = "https://iamcredentials.googleapis.com/v1"
SCOPE = "https://www.googleapis.com/auth/cloud-platform"


class Federation(Credentials):
    def __init__(self, api: Api, identity: Callable[[str], str | None]) -> None:
        self.api = api
        self.identity = identity
        self._token: str | None = None

    def access_token(self, spec: Spec) -> str:
        if self._token:
            return self._token

        subject = self.identity(spec.audience)

        if not subject:
            raise DeployError(
                "nothing can sign an identity token here: deploy from the platform, "
                "or log in with `action-platform login`"
            )

        federated = self.api.call(
            "POST",
            STS,
            form={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "audience": f"//iam.googleapis.com/{spec.provider}",
                "scope": SCOPE,
                "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                "subject_token": subject,
            },
        ).get("access_token")

        if not federated:
            raise DeployError("Google STS answered without an access token")

        granted = self.api.call(
            "POST",
            f"{IAM_CREDENTIALS}/projects/-/serviceAccounts/{spec.service_account}:generateAccessToken",
            token=federated,
            body={"scope": [SCOPE], "lifetime": "3600s"},
        ).get("accessToken")

        if not granted:
            raise DeployError(
                f"IAM Credentials gave no access token for {spec.service_account}"
            )

        self._token = granted

        return granted


def identity_of(ctx_token: Callable[[str], str | None]) -> Callable[[str], str | None]:
    """The deploy's own token when the platform runs it; on a machine, one the platform the CLI is logged in to signs."""

    def mint(audience: str) -> str | None:
        token = ctx_token(audience)

        if token:
            return token

        try:
            return Remote.from_credentials().identity_token(audience).get("token")
        except ActionPlatformError:
            return None

    return mint
