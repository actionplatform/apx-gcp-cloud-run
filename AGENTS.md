# AGENTS.md

Rules an AI agent (or a new contributor) follows in this repository.

## What this is

The `gcp/cloud-run` deploy target for Action Platform, built from [apx-example](https://github.com/actionplatform/apx-example); its `SPEC.md` is the contract. `README.md` says what the plugin does and how credentials flow.

## Code

- Python 3.11+, `ruff check . && ruff format --check .`, `pytest` green before a commit.
- No comments inside function bodies; no test docstrings. Module docstrings say what the module is for.
- `register` declares only — no I/O, threads or connections at import or in `register`.
- Never monkey-patch `action_platform.*`; replace a slot or ask for a hook.
- No Google SDK: Google's REST APIs over plain HTTPS through `google.py`. Never log, print or put in an error message a token of any kind.
- No key: every call acts as the deploy service account through the platform's token (`credentials.py`).
- `needs` lists every host and environment variable the plugin touches.
- One ABC per responsibility (`abc.py`), a `Spec` per call, the target composing them through `parts()`; `tests/fake.py` answers only what Google's APIs answer, `tests/live/` against a real project.
- `connect/connect.sh` stays idempotent: describe before create, update a provider that exists.

## Commits and branches

[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/): `type(scope): description`; one commit per concern; stage files explicitly. Git-flow: `<kind>/<issue>-<slug>` from `master`, kinds `feature bugfix hotfix release chore docs refactor test ci perf`; never commit on `master`. One pull request per issue.

## Release

`action-platform release <level>`; publishing the GitHub release publishes to PyPI; then a PR to `plugins-index` with the new `latest`.
