"""Plain HTTPS to Google's REST APIs: JSON in and out, a form for STS, Google's own error message on failure."""

from __future__ import annotations

import json
from urllib import error, parse, request

from action_platform.core.exception import DeployError

from apx_gcp_cloud_run.abc import Api, Record

TIMEOUT = 30


class GoogleError(DeployError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class GoogleApi(Api):
    def call(
        self,
        method: str,
        url: str,
        token: str | None = None,
        body: Record | None = None,
        form: dict[str, str] | None = None,
    ) -> Record:
        headers = {"accept": "application/json"}
        data = None

        if form is not None:
            data = parse.urlencode(form).encode()
            headers["content-type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["content-type"] = "application/json"

        if token:
            headers["authorization"] = f"Bearer {token}"

        req = request.Request(url, data=data, method=method, headers=headers)
        where = f"{method} {parse.urlsplit(url).netloc}{parse.urlsplit(url).path}"

        try:
            with request.urlopen(req, timeout=TIMEOUT) as response:
                text = response.read().decode()
        except error.HTTPError as e:
            raise GoogleError(
                e.code, f"{where} answered {e.code}: {reason(e.read().decode())}"
            ) from None
        except (error.URLError, TimeoutError, OSError) as e:
            raise GoogleError(0, f"{where} unreachable: {e}") from None

        return json.loads(text) if text.strip() else {}


def reason(text: str) -> str:
    """Google's message from an error body: `error.message` for the REST APIs, `error_description` for STS."""
    try:
        data = json.loads(text)
    except ValueError:
        return text.strip()[:300] or "no detail"

    problem = data.get("error") if isinstance(data, dict) else None

    if isinstance(problem, dict):
        return str(problem.get("message") or problem.get("status") or problem)

    if isinstance(data, dict) and data.get("error_description"):
        return f"{problem}: {data['error_description']}"

    return str(problem or data)[:300]
