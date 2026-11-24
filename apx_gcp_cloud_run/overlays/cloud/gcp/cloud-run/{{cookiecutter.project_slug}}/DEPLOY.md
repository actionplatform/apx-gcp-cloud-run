# Deploy — Google Cloud Run

Overlay `cloud/gcp/cloud-run`. The CI builds the image on every release and pushes it to Artifact Registry; the platform points the scope's Cloud Run service at that tag.

| | |
|---|---|
| Image | `<region>-docker.pkg.dev/<project>/action-platform/<repository name>:<version>` — `.github/workflows/docker-publish.yml`, on every published release |
| Service | one per scope: `ap-<org>-<project>-<app>-<scope>`, the container listening on port 8000 |
| Identity | the platform's OIDC token, exchanged through Workload Identity Federation for the deploy service account; no key |

## Set up once

1. Connect the Google Cloud project: on the platform, Plugins → GCP Cloud Run → Configure → **Connect Google Cloud** copies a command and opens Cloud Shell. Prefix it with `GITHUB_OWNER=<your GitHub owner>` so this repository's workflow may push images. Paste the four values it prints into the plugin's options.
2. On this GitHub repository, Settings → Secrets and variables → Actions → Variables: `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_PROJECT_ID`, `GCP_REGION` and, when it is not `action-platform`, `GCP_REPOSITORY` — the script prints them.
3. `platform.toml`:

```toml
[deploy]
target = "gcp/cloud-run"
```

The first deploy of a scope creates its service.

On GitLab or Jenkins the overlay brings no image job: push `<image>:<version>` to the Artifact Registry repository from the pipeline, the same path the GitHub workflow uses.

## Every release

```bash
action-platform release minor        # tags vX.Y.Z; the workflow pushes the image
action-platform deploy --dry-run     # readiness: settings, token exchange, roles, image published, service state
action-platform deploy               # the service runs <image>:<version>, every request to the new revision
```

`rollback` sends every request back to the revision that served before (or to the one running a given version); `diagnose` reports the state, the serving revision, its image and the url. `action-platform destroy --scope <scope>` deletes the scope's service.
