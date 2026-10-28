#!/usr/bin/env bash
# Deploy the Lull API to Google Cloud Run: the steps of docker/README.md ("On Cloud Run"), from
# engine/. Each step that creates something skips it when it exists, so the script reruns.
#
#   PROJECT=my-project REGION=us-central1 docker/deploy-cloudrun.sh
#
# The first run also needs NEBIUS_API_KEY and TAVILY_API_KEY in the environment: they go to
# Secret Manager from stdin and are never printed or written to disk. The active gcloud account
# is used as it is. WEB_ORIGINS (comma-separated) is the site's origin(s) for CORS and /mcp.
set -euo pipefail
# Git Bash on Windows would turn mount-path=/data and the cron line into Windows paths
export MSYS2_ARG_CONV_EXCL="${MSYS2_ARG_CONV_EXCL:-volume=;*/10}"

PROJECT=${PROJECT:?set PROJECT}
REGION=${REGION:-us-central1}
SERVICE=${SERVICE:-lull-api}
REPO=${REPO:-lull}
BUCKET=${BUCKET:-$PROJECT-lull-data}
TAG=${TAG:-$(git rev-parse --short HEAD)}
WEB_ORIGINS=${WEB_ORIGINS:-http://localhost:3000}
SA="$SERVICE@$PROJECT.iam.gserviceaccount.com"
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/$SERVICE:$TAG"
SECRETS=("NEBIUS_API_KEY:lull-nebius-api-key" "TAVILY_API_KEY:lull-tavily-api-key")
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
g() { gcloud --project "$PROJECT" --quiet "$@"; }
step() { printf '== %s\n' "$*"; }

step "image registry $REPO, keeping the 2 newest images"
g artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 ||
  g artifacts repositories create "$REPO" --repository-format docker --location "$REGION"
cat > "$WORK/cleanup.json" <<'JSON'
[{"name": "keep-2-newest", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 2}},
 {"name": "delete-the-rest", "action": {"type": "Delete"}, "condition": {"tagState": "ANY"}}]
JSON
g artifacts repositories set-cleanup-policies "$REPO" --location "$REGION" \
  --policy "$WORK/cleanup.json" --no-dry-run >/dev/null

step "secrets"
for pair in "${SECRETS[@]}"; do
  var=${pair%%:*} name=${pair##*:}
  if ! g secrets describe "$name" >/dev/null 2>&1; then
    : "${!var:?$var is needed once, to create the secret $name}"
    printf '%s' "${!var}" | g secrets create "$name" --replication-policy automatic --data-file=-
  fi
done

step "data bucket gs://$BUCKET: hierarchical namespace, uniform access, no soft delete"
g storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 ||
  g storage buckets create "gs://$BUCKET" --location "$REGION" --uniform-bucket-level-access \
    --enable-hierarchical-namespace --soft-delete-duration=0
cat > "$WORK/lifecycle.json" <<'JSON'
{"rule": [{"action": {"type": "Delete"}, "condition": {"age": 8, "matchesPrefix": ["runs/"]}}]}
JSON
g storage buckets update "gs://$BUCKET" --lifecycle-file "$WORK/lifecycle.json" >/dev/null

step "service account $SA: the bucket's objects and the two secrets, nothing project-wide"
g iam service-accounts describe "$SA" >/dev/null 2>&1 ||
  g iam service-accounts create "$SERVICE" --display-name "Lull API (Cloud Run)"
g storage buckets add-iam-policy-binding "gs://$BUCKET" --member "serviceAccount:$SA" \
  --role roles/storage.objectUser >/dev/null
for pair in "${SECRETS[@]}"; do
  g secrets add-iam-policy-binding "${pair##*:}" --member "serviceAccount:$SA" \
    --role roles/secretmanager.secretAccessor >/dev/null
done

step "image $IMAGE (Cloud Build; the upload is what .gcloudignore lets through)"
g builds submit . --config docker/cloudbuild.yaml --substitutions "_IMAGE=$IMAGE"

# the environment: in a file, since the origins and URLs hold commas
env_file() {  # $1: LULL_PUBLIC_URL
  cat > "$WORK/env.yaml" <<YAML
LULL_DATA: "/data"
LULL_HOME: "/tmp/lull-home"
LULL_BUCKET: "$BUCKET"
LULL_PROXY_HOPS: "1"
LULL_SPEECH_THREADS: "8"
LULL_SPEECH_WAIT: "200"
LULL_RUN_WALL_CLOCK_S: "900"
LULL_WEB_ORIGINS: "$WEB_ORIGINS"
LULL_PUBLIC_URL: "$1"
YAML
}
public_urls() {  # the service's URLs, then its smoke tag's
  local urls
  urls=$(g run services describe "$SERVICE" --region "$REGION" \
    --format 'value(metadata.annotations."run.googleapis.com/urls")' | tr -d '[]" ')
  local host=${urls%%,*}
  printf '%s,https://smoke---%s' "$urls" "${host#https://}"
}
DEPLOY=(--image "$IMAGE" --region "$REGION" --service-account "$SA"
  --cpu 8 --memory 8Gi --concurrency 20 --timeout 3600 --min-instances 0 --max-instances 1
  --execution-environment gen2 --cpu-throttling --cpu-boost --port 8080
  --add-volume "name=data,type=cloud-storage,bucket=$BUCKET"
  --add-volume-mount "volume=data,mount-path=/data"
  --set-secrets "NEBIUS_API_KEY=lull-nebius-api-key:latest,TAVILY_API_KEY=lull-tavily-api-key:latest")

if ! g run services describe "$SERVICE" --region "$REGION" >/dev/null 2>&1; then
  step "first deploy of $SERVICE (it takes the traffic: there is no revision to keep it)"
  env_file ""
  g run deploy "$SERVICE" "${DEPLOY[@]}" --env-vars-file "$WORK/env.yaml" --allow-unauthenticated
  step "LULL_PUBLIC_URL from the service's URLs"
  env_file "$(public_urls)"
  g run services update "$SERVICE" --region "$REGION" --env-vars-file "$WORK/env.yaml"
  SMOKE_URL=$(g run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')
else
  step "deploy of $SERVICE without traffic, tagged smoke"
  env_file "$(public_urls)"
  g run deploy "$SERVICE" "${DEPLOY[@]}" --env-vars-file "$WORK/env.yaml" --no-traffic \
    --tag smoke
  SMOKE_URL=$(g run services describe "$SERVICE" --region "$REGION" \
    --format 'value(status.traffic.filter(tag=smoke).url)' | head -n 1)
fi

step "keep-warm: GET /v1/health every 10 minutes, 07:00-23:00 UTC"
URL=$(g run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')
if g scheduler jobs describe lull-keep-warm --location "$REGION" >/dev/null 2>&1; then
  g scheduler jobs update http lull-keep-warm --location "$REGION" --uri "$URL/v1/health"
else
  g scheduler jobs create http lull-keep-warm --location "$REGION" --schedule '*/10 7-22 * * *' \
    --time-zone UTC --uri "$URL/v1/health" --http-method GET --attempt-deadline 300s
fi

step "done: smoke it, then move the traffic (later deploys) once the old revision is idle"
echo "uv run python docker/smoke.py $SMOKE_URL"
echo "uv run python docker/smoke.py $SMOKE_URL --mcp --mcp-list-only"
echo "gcloud run services update-traffic $SERVICE --region $REGION --to-tags smoke=100"
