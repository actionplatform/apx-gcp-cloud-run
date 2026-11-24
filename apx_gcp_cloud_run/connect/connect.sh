#!/bin/sh
set -eu

if [ "$#" -lt 2 ]; then
  echo "usage: sh connect.sh <platform url> <organization>" >&2
  echo "env: PROJECT_ID (default: gcloud's project), REGION (us-central1), GITHUB_OWNER (lets that owner's GitHub Actions push images)" >&2
  exit 2
fi

ISSUER="${1%/}"
ORGANIZATION="$2"
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"
GITHUB_OWNER="${GITHUB_OWNER:-}"
POOL="${POOL:-action-platform}"
PROVIDER="${PROVIDER:-platform}"
REPOSITORY="${REPOSITORY:-action-platform}"
DEPLOY="ap-deploy"
RUNTIME="ap-runtime"

case "$ISSUER" in https://*) ;; *) echo "the platform url must start with https://" >&2; exit 2 ;; esac
printf '%s' "$ORGANIZATION" | grep -Eq '^[a-z0-9][a-z0-9-]{0,62}$' || { echo "organization must be a slug: $ORGANIZATION" >&2; exit 2; }
[ -n "$PROJECT_ID" ] || { echo "set PROJECT_ID or run: gcloud config set project <id>" >&2; exit 2; }

DEPLOY_SA="$DEPLOY@$PROJECT_ID.iam.gserviceaccount.com"
RUNTIME_SA="$RUNTIME@$PROJECT_ID.iam.gserviceaccount.com"

gcloud services enable --project "$PROJECT_ID" \
  run.googleapis.com artifactregistry.googleapis.com iam.googleapis.com \
  iamcredentials.googleapis.com sts.googleapis.com cloudresourcemanager.googleapis.com

NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
POOL_NAME="projects/$NUMBER/locations/global/workloadIdentityPools/$POOL"

gcloud iam workload-identity-pools describe "$POOL" --project "$PROJECT_ID" --location global >/dev/null 2>&1 \
  || gcloud iam workload-identity-pools create "$POOL" --project "$PROJECT_ID" --location global \
    --display-name "Action Platform"

provider() {
  id="$1" issuer="$2" mapping="$3" condition="$4"
  if gcloud iam workload-identity-pools providers describe "$id" --project "$PROJECT_ID" --location global \
    --workload-identity-pool "$POOL" >/dev/null 2>&1; then
    verb=update-oidc
  else
    verb=create-oidc
  fi
  gcloud iam workload-identity-pools providers "$verb" "$id" --project "$PROJECT_ID" --location global \
    --workload-identity-pool "$POOL" --issuer-uri "$issuer" \
    --attribute-mapping "$mapping" --attribute-condition "$condition"
}

provider "$PROVIDER" "$ISSUER" \
  "google.subject=assertion.sub,attribute.organization=assertion.organization" \
  "assertion.organization == '$ORGANIZATION'"

account() {
  gcloud iam service-accounts describe "$1@$PROJECT_ID.iam.gserviceaccount.com" --project "$PROJECT_ID" >/dev/null 2>&1 \
    || gcloud iam service-accounts create "$1" --project "$PROJECT_ID" --display-name "$2"
}

account "$DEPLOY" "Action Platform deploy"
account "$RUNTIME" "Action Platform runtime"

for role in roles/run.admin roles/artifactregistry.reader; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$DEPLOY_SA" \
    --role "$role" --condition None >/dev/null
done

gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" --project "$PROJECT_ID" \
  --member "serviceAccount:$DEPLOY_SA" --role roles/iam.serviceAccountUser >/dev/null

gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" --project "$PROJECT_ID" \
  --member "principalSet://iam.googleapis.com/$POOL_NAME/attribute.organization/$ORGANIZATION" \
  --role roles/iam.workloadIdentityUser >/dev/null

gcloud artifacts repositories describe "$REPOSITORY" --project "$PROJECT_ID" --location "$REGION" >/dev/null 2>&1 \
  || gcloud artifacts repositories create "$REPOSITORY" --project "$PROJECT_ID" --location "$REGION" \
    --repository-format docker --description "Images Action Platform deploys"

if [ -n "$GITHUB_OWNER" ]; then
  provider github https://token.actions.githubusercontent.com \
    "google.subject=assertion.sub,attribute.repository_owner=assertion.repository_owner,attribute.repository=assertion.repository" \
    "assertion.repository_owner == '$GITHUB_OWNER'"
  gcloud artifacts repositories add-iam-policy-binding "$REPOSITORY" --project "$PROJECT_ID" --location "$REGION" \
    --member "principalSet://iam.googleapis.com/$POOL_NAME/attribute.repository_owner/$GITHUB_OWNER" \
    --role roles/artifactregistry.writer >/dev/null
fi

echo
echo "On the platform, Plugins → GCP Cloud Run → Configure:"
echo "  project_id                  $PROJECT_ID"
echo "  region                      $REGION"
echo "  workload_identity_provider  $POOL_NAME/providers/$PROVIDER"
echo "  service_account             $DEPLOY_SA"

if [ -n "$GITHUB_OWNER" ]; then
  echo
  echo "On each GitHub repository (Settings → Secrets and variables → Actions → Variables):"
  echo "  GCP_WORKLOAD_IDENTITY_PROVIDER  $POOL_NAME/providers/github"
  echo "  GCP_PROJECT_ID                  $PROJECT_ID"
  echo "  GCP_REGION                      $REGION"
  echo "  GCP_REPOSITORY                  $REPOSITORY"
fi
