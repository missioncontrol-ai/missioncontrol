#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${MC_BASE_URL:-http://localhost:8008}"
TOKEN="${MC_TOKEN:-}"
ACTOR="${MC_PLAYBOOK_ACTOR:-token-client}"
RUN_ID="${MC_PLAYBOOK_RUN_ID:-$(date +%Y%m%d%H%M%S)}"

if [[ -z "$TOKEN" ]]; then
  echo "MC_TOKEN is required" >&2
  exit 2
fi

API_AUTH=(-H "Authorization: Bearer ${TOKEN}")
JSON_HDR=(-H "Content-Type: application/json")
HTTP_RETRIES="${MC_PLAYBOOK_HTTP_RETRIES:-4}"
HTTP_RETRY_SLEEP_SEC="${MC_PLAYBOOK_HTTP_RETRY_SLEEP_SEC:-0.5}"
HTTP_RETRY_MAX_SLEEP_SEC="${MC_PLAYBOOK_HTTP_RETRY_MAX_SLEEP_SEC:-5}"

http_request() {
  local method="$1"
  local url="$2"
  local data="${3:-}"
  local attempt=1
  local max_attempts="$HTTP_RETRIES"
  if [[ "$max_attempts" -lt 1 ]]; then
    max_attempts=1
  fi
  while true; do
    local response http_code body hdr_file retry_after next_sleep
    hdr_file="$(mktemp)"
    if [[ -n "$data" ]]; then
      response="$(curl -sS -D "$hdr_file" "${API_AUTH[@]}" "${JSON_HDR[@]}" -X "$method" "$url" -d "$data" -w $'\n%{http_code}')"
    else
      response="$(curl -sS -D "$hdr_file" "${API_AUTH[@]}" -X "$method" "$url" -w $'\n%{http_code}')"
    fi
    http_code="${response##*$'\n'}"
    body="${response%$'\n'*}"
    if [[ "$http_code" =~ ^2[0-9][0-9]$ ]]; then
      printf '%s' "$body"
      rm -f "$hdr_file"
      return 0
    fi
    if [[ "$http_code" == "429" || "$http_code" =~ ^5[0-9][0-9]$ ]]; then
      if [[ "$attempt" -lt "$max_attempts" ]]; then
        retry_after="$(awk 'BEGIN{IGNORECASE=1} /^retry-after:/ {gsub("\r","",$2); print $2; exit}' "$hdr_file" 2>/dev/null || true)"
        if [[ -n "$retry_after" && "$retry_after" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
          sleep "$retry_after"
        else
          next_sleep="$(awk "BEGIN{v=${HTTP_RETRY_SLEEP_SEC} * (2^(${attempt}-1)); if (v>${HTTP_RETRY_MAX_SLEEP_SEC}) v=${HTTP_RETRY_MAX_SLEEP_SEC}; printf \"%.3f\", v}")"
          sleep "$next_sleep"
        fi
        attempt=$((attempt + 1))
        rm -f "$hdr_file"
        continue
      fi
    fi
    echo "HTTP ${http_code}: ${url}" >&2
    if [[ -n "$body" ]]; then
      echo "$body" >&2
    fi
    rm -f "$hdr_file"
    return 22
  done
}

mcp_call() {
  local tool="$1"
  local args_json="$2"
  http_request "POST" "${BASE_URL}/mcp/call" "{\"tool\":\"${tool}\",\"args\":${args_json}}"
}

api_call() {
  local method="$1"
  local path="$2"
  local payload="${3:-}"
  http_request "$method" "${BASE_URL}${path}" "$payload"
}

assert_ok() {
  local label="$1"
  local resp="$2"
  local ok
  ok="$(jq -r '.ok // false' <<<"$resp")"
  if [[ "$ok" != "true" ]]; then
    echo "[FAIL] ${label}: $(jq -c '.' <<<"$resp")" >&2
    exit 1
  fi
  echo "[OK] ${label}"
}

mission_name="mcp-playbook-${RUN_ID}"
kluster_name="mcp-playbook-kluster-${RUN_ID}"

echo "== MCP validation playbook run_id=${RUN_ID} base_url=${BASE_URL}"

create_mission_resp="$(mcp_call create_mission "$(jq -cn --arg name "$mission_name" --arg owners "$ACTOR" '{name:$name,owners:$owners,description:"MCP validation mission"}')")"
assert_ok "create_mission" "$create_mission_resp"
mission_id="$(jq -r '.result.mission.id' <<<"$create_mission_resp")"
echo "mission_id=${mission_id}"

create_kluster_resp="$(mcp_call create_kluster "$(jq -cn --arg mission_id "$mission_id" --arg name "$kluster_name" --arg owners "$ACTOR" '{mission_id:$mission_id,name:$name,owners:$owners,description:"MCP validation kluster"}')")"
assert_ok "create_kluster" "$create_kluster_resp"
kluster_id="$(jq -r '.result.kluster.id' <<<"$create_kluster_resp")"
echo "kluster_id=${kluster_id}"

create_task_resp="$(mcp_call create_task "$(jq -cn --arg kluster_id "$kluster_id" --arg title "playbook-task" --arg owner "$ACTOR" '{kluster_id:$kluster_id,title:$title,description:"playbook task",owner:$owner}')")"
assert_ok "create_task" "$create_task_resp"
task_id="$(jq -r '.result.task.id' <<<"$create_task_resp")"
echo "task_id=${task_id}"

list_tasks_resp="$(mcp_call list_tasks "$(jq -cn --arg kluster_id "$kluster_id" '{kluster_id:$kluster_id}')")"
assert_ok "list_tasks" "$list_tasks_resp"

update_task_resp="$(mcp_call update_task "$(jq -cn --arg task_id "$task_id" '{task_id:$task_id,status:"in_progress",description:"updated by playbook"}')")"
assert_ok "update_task" "$update_task_resp"

create_doc_resp="$(mcp_call create_doc "$(jq -cn --arg kluster_id "$kluster_id" '{kluster_id:$kluster_id,title:"playbook-doc",body:"# playbook\ndoc body",doc_type:"narrative"}')")"
assert_ok "create_doc" "$create_doc_resp"
doc_id="$(jq -r '.result.doc.id' <<<"$create_doc_resp")"
echo "doc_id=${doc_id}"

doc_patch_resp="$(api_call PATCH "/docs/${doc_id}" '{"status":"review","body":"# playbook\nupdated body"}')"
echo "[OK] update_doc_api status=$(jq -r '.status' <<<"$doc_patch_resp")"

create_artifact_resp="$(mcp_call create_artifact "$(jq -cn --arg kluster_id "$kluster_id" '{kluster_id:$kluster_id,name:"playbook-artifact",artifact_type:"file",uri:"https://example.com/playbook",status:"draft"}')")"
assert_ok "create_artifact" "$create_artifact_resp"
artifact_id="$(jq -r '.result.artifact.id' <<<"$create_artifact_resp")"
echo "artifact_id=${artifact_id}"

artifact_patch_resp="$(api_call PATCH "/artifacts/${artifact_id}" '{"status":"review"}')"
echo "[OK] update_artifact_api status=$(jq -r '.status' <<<"$artifact_patch_resp")"

load_ws_resp="$(mcp_call load_kluster_workspace "$(jq -cn --arg kluster_id "$kluster_id" '{kluster_id:$kluster_id}')")"
assert_ok "load_kluster_workspace" "$load_ws_resp"
lease_id="$(jq -r '.result.lease.id' <<<"$load_ws_resp")"
doc_path="$(jq -r '.result.workspace_snapshot.docs[0].path // empty' <<<"$load_ws_resp")"
if [[ -n "$doc_path" ]]; then
  commit_ws_resp="$(mcp_call commit_kluster_workspace "$(jq -cn --arg lease_id "$lease_id" --arg doc_path "$doc_path" '{lease_id:$lease_id,change_set:[{path:$doc_path,content:"# playbook\nworkspace commit"}]}')")"
  assert_ok "commit_kluster_workspace" "$commit_ws_resp"
fi
release_ws_resp="$(mcp_call release_kluster_workspace "$(jq -cn --arg lease_id "$lease_id" '{lease_id:$lease_id,reason:"playbook done"}')")"
assert_ok "release_kluster_workspace" "$release_ws_resp"

delete_task_resp="$(mcp_call delete_task "$(jq -cn --arg task_id "$task_id" '{task_id:$task_id}')")"
assert_ok "delete_task" "$delete_task_resp"

echo "== Cleanup attempt"
delete_doc_resp="$(api_call DELETE "/docs/${doc_id}")"
echo "[OK] delete_doc_api id=$(jq -r '.deleted_id // empty' <<<"$delete_doc_resp")"

delete_artifact_resp="$(api_call DELETE "/artifacts/${artifact_id}")"
echo "[OK] delete_artifact_api id=$(jq -r '.deleted_id // empty' <<<"$delete_artifact_resp")"

set +e
delete_kluster_resp="$(api_call DELETE "/missions/${mission_id}/k/${kluster_id}" 2>/tmp/mcp_playbook_cleanup.err)"
cleanup_rc=$?
set -e
if [[ $cleanup_rc -eq 0 ]]; then
  echo "[OK] delete_kluster"
  api_call DELETE "/missions/${mission_id}" >/dev/null
  echo "[OK] delete_mission"
else
  echo "[WARN] cleanup blocked"
  if [[ -f /tmp/mcp_playbook_cleanup.err ]]; then
    echo "[WARN] delete_kluster stderr: $(tr '\n' ' ' </tmp/mcp_playbook_cleanup.err)"
  fi
fi

cat <<EOF
== RESULT
run_id=${RUN_ID}
mission_id=${mission_id}
kluster_id=${kluster_id}
doc_id=${doc_id}
artifact_id=${artifact_id}
task_id=${task_id}
notes=playbook now performs explicit doc/artifact delete before kluster delete
EOF
