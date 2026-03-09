#!/usr/bin/env bash
set -euo pipefail

NS="${MC_NAMESPACE:-missioncontrol}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing required command: $1" >&2
    exit 1
  }
}

need kubectl
need docker
need base64

secret_val() {
  local secret="$1"
  local key="$2"
  kubectl -n "$NS" get secret "$secret" -o "jsonpath={.data.$key}" | base64 -d
}

resolve_service_host() {
  local host="$1"
  if [[ "$host" == *.svc.cluster.local ]]; then
    local svc="${host%%.*}"
    local rest="${host#*.}"
    local ns="${rest%%.*}"
    kubectl -n "$ns" get svc "$svc" -o jsonpath='{.spec.clusterIP}'
  else
    echo "$host"
  fi
}

DB_HOST_RAW="$(secret_val missioncontrol-db-env POSTGRES_HOST)"
DB_HOST="$(resolve_service_host "$DB_HOST_RAW")"
DB_PORT="$(secret_val missioncontrol-db-env POSTGRES_PORT)"
DB_USER="$(secret_val missioncontrol-db-env POSTGRES_USER)"
DB_PASS="$(secret_val missioncontrol-db-env POSTGRES_PASSWORD)"
DB_NAME="$(secret_val missioncontrol-db-env POSTGRES_DB)"

API_TOKEN="$(secret_val missioncontrol-api-env MC_TOKEN)"

MQTT_HOST_RAW="$(secret_val missioncontrol-api-env MQTT_HOST)"
MQTT_HOST="$(resolve_service_host "$MQTT_HOST_RAW")"
MQTT_PORT="$(secret_val missioncontrol-api-env MQTT_PORT)"
MQTT_USER="$(secret_val mosquitto-auth MQTT_USERNAME)"
MQTT_PASS="$(secret_val mosquitto-auth MQTT_PASSWORD)"

GIT_PUBLISH_TOKEN_VALUE="${GIT_PUBLISH_TOKEN:-}"
if [[ -z "$GIT_PUBLISH_TOKEN_VALUE" ]] && kubectl -n "$NS" get secret missioncontrol-git-publish >/dev/null 2>&1; then
  GIT_PUBLISH_TOKEN_VALUE="$(secret_val missioncontrol-git-publish GIT_PUBLISH_TOKEN || true)"
fi

docker build -f backend/Dockerfile -t missioncontrol-api:dev-local .
docker rm -f missioncontrol-api-dev >/dev/null 2>&1 || true

docker run -d \
  --name missioncontrol-api-dev \
  --network host \
  -e AUTH_MODE=token \
  -e OIDC_REQUIRED=false \
  -e MC_TOKEN="$API_TOKEN" \
  -e POSTGRES_HOST="$DB_HOST" \
  -e POSTGRES_PORT="$DB_PORT" \
  -e POSTGRES_USER="$DB_USER" \
  -e POSTGRES_PASSWORD="$DB_PASS" \
  -e POSTGRES_DB="$DB_NAME" \
  -e MQTT_HOST="$MQTT_HOST" \
  -e MQTT_PORT="$MQTT_PORT" \
  -e MQTT_USERNAME="$MQTT_USER" \
  -e MQTT_PASSWORD="$MQTT_PASS" \
  -e GIT_PUBLISH_ENABLED="${GIT_PUBLISH_ENABLED:-true}" \
  -e GIT_PUBLISH_REPO_URL="${GIT_PUBLISH_REPO_URL:-}" \
  -e GIT_PUBLISH_BRANCH="${GIT_PUBLISH_BRANCH:-main}" \
  -e GIT_PUBLISH_BASE_PATH="${GIT_PUBLISH_BASE_PATH:-missions}" \
  -e GIT_PUBLISH_TOKEN="$GIT_PUBLISH_TOKEN_VALUE" \
  -e GIT_PUBLISH_USERNAME="${GIT_PUBLISH_USERNAME:-x-access-token}" \
  -e GIT_PUBLISH_AUTHOR_NAME="${GIT_PUBLISH_AUTHOR_NAME:-MissionControl Bot}" \
  -e GIT_PUBLISH_AUTHOR_EMAIL="${GIT_PUBLISH_AUTHOR_EMAIL:-missioncontrol@local}" \
  missioncontrol-api:dev-local >/dev/null

echo "missioncontrol-api-dev is running on http://$(hostname -I | awk '{print $1}'):8008"
