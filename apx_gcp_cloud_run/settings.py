"""Where a value comes from, in order: the target's own argument (`[deploy]` in platform.toml), what the deploy job carries (`AP_GCP_CLOUD_RUN_<KEY>` in `ctx.env`), the process environment, the plugin's options file on this machine."""

from __future__ import annotations

import os
from typing import Any

from action_platform.plugins.options import FileOptions

SLUG = "gcp-cloud-run"


def env_name(key: str) -> str:
    return f"AP_{SLUG}_{key}".upper().replace("-", "_")


def setting(
    key: str,
    explicit: str | None = None,
    env: dict[str, str] | None = None,
    options: Any | None = None,
) -> str | None:
    name = env_name(key)
    found = (
        explicit
        or (env or {}).get(name)
        or os.environ.get(name)
        or (options or FileOptions(SLUG)).get(key)
    )

    return str(found).strip() if found else None
