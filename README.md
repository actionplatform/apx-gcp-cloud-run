# apx-gcp-cloud-run

Google Cloud Run for [Action Platform](https://github.com/actionplatform/action-platform): the `gcp/cloud-run` deploy target, its overlay, and a **connect script** for your Google Cloud project, so that no Google key ever lives on the platform, on a machine or in a repository. The platform's own OIDC token is exchanged through Workload Identity Federation for a short-lived access token of one deploy service account. `apx-` is the prefix every Action Platform extension carries.

```bash
action-platform plugin install gcp-cloud-run
action-platform cloud set gcp/cloud-run        # overlay files come from this plugin
action-platform deploy --dry-run               # readiness: settings, token exchange, roles, image, service state
action-platform deploy                         # the scope's service runs <image>:<version>
action-platform diagnose
```

## Contents

- [What the plugin does](#what-the-plugin-does)
- [Connect a Google Cloud project](#connect-a-google-cloud-project)
- [Configure](#configure)
- [Deploy](#deploy)
- [Tear down](#tear-down)
- [How credentials flow](#how-credentials-flow)
- [Tools and commands](#tools-and-commands)
- [Requirements](#requirements)
- [Development](#development)

## What the plugin does

| | |
|---|---|
| Overlay | a two-stage `Dockerfile` on the [images-base](https://github.com/actionplatform/images-base) build and runtime images (`ap-build package`, any language: Python, Node, Go, Java, Kotlin, Ruby), `.dockerignore`, `.github/workflows/docker-publish.yml` that pushes `<image>:<version>` and `:latest` to Artifact Registry on every published release, `DEPLOY.md` |
| Readiness | before a deploy, without building or changing anything: the four settings and the app are known; the token exchange works (STS, then `generateAccessToken`); the deploy service account holds every permission the deploy needs (`testIamPermissions` on the project, and `iam.serviceAccounts.actAs` on the runtime service account) — a missing one comes with the exact `gcloud` command that grants it; the release's tag exists in Artifact Registry; the service is not mid-rollout (a failed latest revision only warns). The platform runs it after every release for each of the app's scopes |
| Deploy | one Cloud Run service per scope, `ap-<org>-<project>-<app>-<scope>` (lower case, at most 49 characters; a longer name keeps its head and a hash of the whole). The service is created or replaced with the release's image, port 8000, the runtime service account, `[deploy.env]`, and every request to the newest revision; the operation is followed to its end and the service's `/health` must answer 2xx. The service url is the deployment's url |
| Switch traffic | `switch_traffic(weight)`: `weight` percent to the newest ready revision, the rest to the one that served before it |
| Rollback | every request to the newest ready revision older than the serving one with another image, or to the one running a given version |
| Verify | the revision taking the largest share of requests runs the release's image tag |
| Diagnose | state (`ready`, `deploying`, `failed`), the serving revision, its image and version, the url, Cloud Run's message |
| Destroy | the scope's service is deleted; nothing to delete is fine |
| Tool | `gcp_cloud_run_connect_command` |
| Commands | `action-platform gcp-cloud-run connect\|command\|service` |

## Connect a Google Cloud project

Once per project, with an account that may enable APIs, create service accounts and set IAM policies. On the platform: Plugins → GCP Cloud Run → Configure → **Connect Google Cloud**. The button copies a command and opens Cloud Shell; paste it and press Enter. It downloads this release's [`connect/connect.sh`](apx_gcp_cloud_run/connect/connect.sh) from GitHub and runs it for the platform's url and the organization's slug, in the project `gcloud config set project` points at (`PROJECT_ID=…` before the command picks another; `REGION=…` changes `us-central1`; `GITHUB_OWNER=…` lets that owner's GitHub Actions push images).

From a machine logged in to `gcloud`, the same script:

```bash
action-platform gcp-cloud-run connect https://platform.example.com acme --project acme-run --region us-central1 --github-owner acme
```

What it creates — every step is skipped or updated when it already exists, so it is safe to run again:

| Resource | Purpose |
|---|---|
| APIs | Cloud Run, Artifact Registry, IAM, IAM Credentials, STS, Resource Manager |
| Workload identity pool `action-platform`, OIDC provider `platform` | trusts the platform's issuer; maps `google.subject = assertion.sub`, `attribute.organization = assertion.organization`; **attribute condition** `assertion.organization == '<org>'` — a token for any other organization is refused at STS |
| Service account `ap-deploy` | what deploys act as: `roles/run.admin` and `roles/artifactregistry.reader` on the project, `roles/iam.serviceAccountUser` on `ap-runtime`; `roles/iam.workloadIdentityUser` granted to `principalSet://…/attribute.organization/<org>` only |
| Service account `ap-runtime` | what the services run as; no roles — grant it what your app reaches |
| Artifact Registry repository `action-platform` (Docker, in the region) | where the CI pushes images |
| With `GITHUB_OWNER`: provider `github` | trusts GitHub Actions of that owner only, which may write to the repository (`roles/artifactregistry.writer` for `attribute.repository_owner/<owner>`) |

It prints the four values the plugin asks for and, with `GITHUB_OWNER`, the variables each GitHub repository needs.

`roles/run.admin` covers every Cloud Run service of the project: give the platform a project of its own when other services live there.

## Configure

On the platform, Plugins → GCP Cloud Run → Configure:

| Option | Value | Reaches a deploy as |
|---|---|---|
| `project_id` | the project | `AP_GCP_CLOUD_RUN_PROJECT_ID` |
| `region` | `us-central1`, … | `AP_GCP_CLOUD_RUN_REGION` |
| `workload_identity_provider` | `projects/<number>/locations/global/workloadIdentityPools/action-platform/providers/platform` | `AP_GCP_CLOUD_RUN_WORKLOAD_IDENTITY_PROVIDER` |
| `service_account` | `ap-deploy@<project>.iam.gserviceaccount.com` | `AP_GCP_CLOUD_RUN_SERVICE_ACCOUNT` |

Every app of the organization whose `platform.toml` says `target = "gcp/cloud-run"` deploys from then on. On a machine the same values come from those environment variables. `[deploy]` in a repository wins over all of them:

```toml
[deploy]
target = "gcp/cloud-run"
project_id = "acme-run"                  # optional, as region, workload_identity_provider, service_account
runtime_service_account = "orders@acme-run.iam.gserviceaccount.com"   # default ap-runtime@<project>
repository = "action-platform"           # the Artifact Registry repository
image = "us-central1-docker.pkg.dev/acme-run/action-platform/orders"  # default: …/<repository>/<[source_host] repo name>
service = "orders-dev"                   # default ap-<org>-<project>-<app>-<scope>
port = 8000                              # what the container listens on
health = "/health"                       # "" skips the check
public = true                            # false: callers need roles/run.invoker; set health = "" with it

[deploy.env]
LOG_LEVEL = "info"
```

On each GitHub repository, Settings → Secrets and variables → Actions → Variables: `GCP_WORKLOAD_IDENTITY_PROVIDER` (the `github` provider), `GCP_PROJECT_ID`, `GCP_REGION`, and `GCP_REPOSITORY` when it is not `action-platform`. The overlay's workflow reads them; on GitLab or Jenkins, push the image from the pipeline to the same path.

## Deploy

1. `action-platform release minor` tags `vX.Y.Z`; publishing the release runs `docker-publish.yml`, which pushes `<image>:X.Y.Z`.
2. Deploy from the platform (the app's Deployments) or `action-platform deploy --scope dev`. Readiness runs first; the deploy points the scope's service at `X.Y.Z` and waits for Cloud Run.
3. The deployment becomes *verified* once the serving revision runs `X.Y.Z`; its url is the service's.

## Tear down

`action-platform destroy --scope <scope>` (or deleting the app with cloud cleanup on the platform) deletes the scope's Cloud Run service. The images stay in Artifact Registry. To disconnect the project, delete the provider (`gcloud iam workload-identity-pools providers delete platform --workload-identity-pool action-platform --location global`): the platform's tokens stop working at once.

## How credentials flow

```
platform token (aud https://iam.googleapis.com/<provider>, sub org:<org>:project:<project>:app:<app>, organization <org>)
  → POST sts.googleapis.com/v1/token        token exchange; the provider's attribute condition checks the organization
  → POST iamcredentials.googleapis.com/…/serviceAccounts/<service_account>:generateAccessToken   one hour
  → run.googleapis.com, artifactregistry.googleapis.com, cloudresourcemanager.googleapis.com, iam.googleapis.com
```

| Where the deploy runs | Token comes from |
|---|---|
| the platform's worker | signed by the platform for that app (`ctx.identity_token`) |
| a logged-in machine (`action-platform deploy`) | `POST /api/v1/identity/token` with the user's login; it carries `organization` too |

No token is logged or put in an error message. Who may deploy which app is the platform's decision: it signs a token about an app only for a job or a caller allowed to release it.

## Tools and commands

| | |
|---|---|
| `gcp_cloud_run_connect_command` | the Cloud Shell command for a platform url and an organization, and the service name of an app's scope; runs nothing |
| `action-platform gcp-cloud-run connect <platform url> <organization>` | runs the connect script with the `gcloud` this machine is logged in to |
| `action-platform gcp-cloud-run command <platform url> <organization>` | prints the Cloud Shell command |
| `action-platform gcp-cloud-run service <org>/<project>/<app> [scope]` | prints the service name |

## Requirements

- `action-platform` 0.33 or newer.
- Network: `sts.googleapis.com`, `iamcredentials.googleapis.com`, `run.googleapis.com`, `artifactregistry.googleapis.com`, `cloudresourcemanager.googleapis.com`, `iam.googleapis.com`, and the service's `*.run.app` url for the health check. No Google SDK: plain HTTPS.
- `gcloud`, only to connect a project.

## Development

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest
```

`tests/fake.py` answers as STS, IAM Credentials, Resource Manager, IAM, Artifact Registry and the Cloud Run Admin API v2 do; nothing reaches Google. `tests/live/` deploys twice to a real project, verifies, rolls back and deletes — skipped unless its environment variables are set.

| Module | Responsibility (ABC in `abc.py`) |
|---|---|
| `target.py` | `CloudRunTarget`: the core's `DeployTarget` contract, composing the parts below through `parts(spec, ctx)` |
| `spec.py`, `settings.py` | `Spec`: settings, service name, image, audience — resolved once per call without calling Google |
| `google.py` | `Api`: HTTPS and Google's error messages |
| `credentials.py` | `Credentials`: the token exchange |
| `run.py` | `Services`: the Cloud Run service, its revisions and operations |
| `registry.py` | `Registry`: the release's tag in Artifact Registry |
| `iam.py` | `Permissions`: `testIamPermissions` and the `gcloud` fixes |
| `health.py` | `Health`: the service answers after the deploy |
| `checks.py` | `Readiness`: one class per check |
| `connect/` | `connect.sh` and the command that runs it in Cloud Shell |
| `tools.py`, `cli.py` | the MCP tool and the terminal commands |
