#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_ROOT="${MC_PRESSURE_OUT_ROOT:-$ROOT_DIR/artifacts/pressure}"
RUN_ID="${MC_PRESSURE_RUN_ID:-$(date +%Y%m%d%H%M%S)}"
MODE="${MC_PRESSURE_MODE:-agent}" # agent | playbook
WORKERS="${MC_PRESSURE_WORKERS:-5}"
DURATION_SEC="${MC_PRESSURE_DURATION_SEC:-600}"
MODEL="${MC_PRESSURE_MODEL:-gpt-5.1-codex-mini}"
BASE_URL="${MC_BASE_URL:-http://localhost:8008}"
MCP_CMD="${MC_PRESSURE_MCP_COMMAND:-missioncontrol-mcp}"
SHIM_HOST="${MC_DAEMON_HOST:-127.0.0.1}"
SHIM_PORT="${MC_DAEMON_PORT:-8765}"
UNSANDBOXED="${MC_PRESSURE_UNSANDBOXED:-0}"
AUTOSTART_DAEMON="${MC_PRESSURE_AUTOSTART_DAEMON:-1}"
DAEMON_PID=""
INTERVAL_MS="${MC_PRESSURE_INTERVAL_MS:-200}"
ON_429_SLEEP_MS="${MC_PRESSURE_ON_429_SLEEP_MS:-1000}"
TOKENS_CSV="${MC_PRESSURE_TOKENS:-}"
REPORT_VERSION="1.0.0"
SCENARIO_FILE="${MC_PRESSURE_SCENARIO_FILE:-$ROOT_DIR/scripts/pressure-scenarios/reliability-trio.json}"
AGENT_STARTUP_TIMEOUT_SEC="${MC_PRESSURE_AGENT_STARTUP_TIMEOUT_SEC:-120}"
AGENT_TOOL_TIMEOUT_SEC="${MC_PRESSURE_AGENT_TOOL_TIMEOUT_SEC:-120}"
AGENT_ITER_SLEEP_MS="${MC_PRESSURE_AGENT_ITER_SLEEP_MS:-500}"
AGENT_EXEC_TIMEOUT_SEC="${MC_PRESSURE_AGENT_EXEC_TIMEOUT_SEC:-300}"

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required" >&2
  exit 1
fi
if [[ -z "${MC_TOKEN:-}" ]]; then
  echo "MC_TOKEN is required" >&2
  exit 2
fi

if [[ "$MODE" == "agent" ]] && ! command -v codex >/dev/null 2>&1; then
  echo "codex is required for MC_PRESSURE_MODE=agent" >&2
  exit 1
fi
if [[ "$MODE" == "agent" ]] && ! command -v timeout >/dev/null 2>&1; then
  echo "timeout is required for MC_PRESSURE_MODE=agent" >&2
  exit 1
fi
if [[ ! -f "$SCENARIO_FILE" ]]; then
  echo "MC_PRESSURE_SCENARIO_FILE not found: $SCENARIO_FILE" >&2
  exit 2
fi

mkdir -p "$OUT_ROOT"
RUN_DIR="$OUT_ROOT/$RUN_ID"
mkdir -p "$RUN_DIR/workers"

cleanup() {
  if [[ -n "$DAEMON_PID" ]]; then
    kill "$DAEMON_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "== MC pressure test =="
echo "run_id=$RUN_ID"
echo "mode=$MODE workers=$WORKERS duration_sec=$DURATION_SEC model=$MODEL"
echo "base_url=$BASE_URL shim=${SHIM_HOST}:${SHIM_PORT}"
echo "out_dir=$RUN_DIR"

echo "== preflight checks =="
if ! curl -fsS "http://${SHIM_HOST}:${SHIM_PORT}/v1/health" >/dev/null 2>&1; then
  if [[ "$AUTOSTART_DAEMON" == "1" ]]; then
    if ! command -v mc >/dev/null 2>&1; then
      echo "mc is required when MC_PRESSURE_AUTOSTART_DAEMON=1" >&2
      exit 1
    fi
    echo "shim not reachable; auto-starting mc daemon"
    MC_BASE_URL="$BASE_URL" MC_TOKEN="${MC_TOKEN}" \
      mc daemon --disable-matrix --shim-host "$SHIM_HOST" --shim-port "$SHIM_PORT" \
      >"$RUN_DIR/mc-daemon.log" 2>&1 &
    DAEMON_PID="$!"
    for _ in $(seq 1 40); do
      if curl -fsS "http://${SHIM_HOST}:${SHIM_PORT}/v1/health" >/dev/null 2>&1; then
        break
      fi
      sleep 0.25
    done
  fi
fi
curl -fsS -H "Authorization: Bearer ${MC_TOKEN}" "$BASE_URL/mcp/health" >/dev/null
curl -fsS "http://${SHIM_HOST}:${SHIM_PORT}/v1/health" >/dev/null
echo "preflight=ok"

run_agent_worker() {
  local worker_id="$1"
  local worker_token="$2"
  local worker_dir="$RUN_DIR/workers/agent-${worker_id}"
  mkdir -p "$worker_dir"
  local status_file="$worker_dir/status.json"
  local start_ts
  start_ts="$(date +%s)"
  local deadline=$((start_ts + DURATION_SEC))
  local attempts=0
  local successes=0
  local failures=0

  while [[ "$(date +%s)" -lt "$deadline" ]]; do
    attempts=$((attempts + 1))
    local iter_log="$worker_dir/iter-${attempts}.jsonl"
    local iter_msg="$worker_dir/iter-${attempts}.txt"
    local prompt
    prompt=$(
      cat <<EOF
You are pressure-test worker ${worker_id} run ${RUN_ID} attempt ${attempts}.
Use the missioncontrol MCP server.
1) Validate MCP connectivity by querying missioncontrol MCP resources/templates.
2) If missioncontrol MCP connects successfully, return RESULT: ok even when no resources are exposed.
3) If write/read resources are available, execute one write action and one read action.
4) End with exactly one line: RESULT: ok | RESULT: fail
EOF
    )

    codex_args=(exec --json --skip-git-repo-check -C "$ROOT_DIR" -m "$MODEL")
    if [[ "$UNSANDBOXED" == "1" ]]; then
      codex_args+=(--dangerously-bypass-approvals-and-sandbox)
    else
      codex_args+=(--sandbox workspace-write)
    fi
    if timeout "${AGENT_EXEC_TIMEOUT_SEC}" codex "${codex_args[@]}" \
      -c "mcp_servers.missioncontrol.command=\"$MCP_CMD\"" \
      -c "mcp_servers.missioncontrol.startup_timeout_sec=${AGENT_STARTUP_TIMEOUT_SEC}" \
      -c "mcp_servers.missioncontrol.tool_timeout_sec=${AGENT_TOOL_TIMEOUT_SEC}" \
      -c "mcp_servers.missioncontrol.env={MC_MCP_MODE=\"shim\",MC_DAEMON_HOST=\"$SHIM_HOST\",MC_DAEMON_PORT=\"$SHIM_PORT\",MC_FAIL_OPEN_ON_LIST=\"1\",MC_STARTUP_PREFLIGHT=\"none\",MC_BASE_URL=\"$BASE_URL\",MC_TOKEN=\"${worker_token}\"}" \
      -o "$iter_msg" "$prompt" >"$iter_log" 2>"$worker_dir/iter-${attempts}.stderr"; then
      if rg -q "RESULT: ok" "$iter_msg"; then
        successes=$((successes + 1))
      else
        failures=$((failures + 1))
      fi
    else
      failures=$((failures + 1))
    fi
    sleep "$(awk "BEGIN{printf \"%.3f\", ${AGENT_ITER_SLEEP_MS}/1000}")"
  done

  local end_ts
  end_ts="$(date +%s)"
  jq -n \
    --arg worker_id "$worker_id" \
    --arg mode "$MODE" \
    --argjson attempts "$attempts" \
    --argjson successes "$successes" \
    --argjson failures "$failures" \
    --argjson start_ts "$start_ts" \
    --argjson end_ts "$end_ts" \
    '{worker_id:$worker_id,mode:$mode,attempts:$attempts,successes:$successes,failures:$failures,start_ts:$start_ts,end_ts:$end_ts}' \
    > "$status_file"
}

run_playbook_worker() {
  local worker_id="$1"
  local worker_token="$2"
  local worker_dir="$RUN_DIR/workers/playbook-${worker_id}"
  mkdir -p "$worker_dir"
  local status_file="$worker_dir/status.json"
  local start_ts
  start_ts="$(date +%s)"
  local deadline=$((start_ts + DURATION_SEC))
  local attempts=0
  local successes=0
  local failures=0

  while [[ "$(date +%s)" -lt "$deadline" ]]; do
    attempts=$((attempts + 1))
    local err_file="$worker_dir/iter-${attempts}.stderr"
    if MC_PLAYBOOK_RUN_ID="${RUN_ID}-w${worker_id}-i${attempts}" \
      MC_PLAYBOOK_SCENARIO_FILE="${SCENARIO_FILE}" \
      MC_BASE_URL="$BASE_URL" \
      MC_TOKEN="${worker_token}" \
      "$ROOT_DIR/scripts/mcp-validation-playbook.sh" \
      >"$worker_dir/iter-${attempts}.log" 2>"$err_file"; then
      successes=$((successes + 1))
    else
      failures=$((failures + 1))
      if rg -q " 429|error: 429|URL returned error: 429" "$err_file" 2>/dev/null; then
        sleep "$(awk "BEGIN{printf \"%.3f\", ${ON_429_SLEEP_MS}/1000}")"
      fi
    fi
    sleep "$(awk "BEGIN{printf \"%.3f\", ${INTERVAL_MS}/1000}")"
  done

  local end_ts
  end_ts="$(date +%s)"
  jq -n \
    --arg worker_id "$worker_id" \
    --arg mode "$MODE" \
    --argjson attempts "$attempts" \
    --argjson successes "$successes" \
    --argjson failures "$failures" \
    --argjson start_ts "$start_ts" \
    --argjson end_ts "$end_ts" \
    '{worker_id:$worker_id,mode:$mode,attempts:$attempts,successes:$successes,failures:$failures,start_ts:$start_ts,end_ts:$end_ts}' \
    > "$status_file"
}

IFS=',' read -r -a token_list <<< "$TOKENS_CSV"
token_count="${#token_list[@]}"
for i in $(seq 1 "$WORKERS"); do
  worker_token="${MC_TOKEN:-}"
  if [[ "$token_count" -gt 0 && -n "${token_list[0]}" ]]; then
    idx=$(( (i - 1) % token_count ))
    worker_token="$(echo "${token_list[$idx]}" | xargs)"
  fi
  if [[ -z "$worker_token" ]]; then
    echo "worker $i has empty token; set MC_TOKEN or MC_PRESSURE_TOKENS" >&2
    exit 2
  fi
  if [[ "$MODE" == "agent" ]]; then
    run_agent_worker "$i" "$worker_token" &
  else
    run_playbook_worker "$i" "$worker_token" &
  fi
done
wait

startup_timeout_hits="$({ rg -n -e "MCP startup incomplete" -e "timed out after" "$RUN_DIR" -g '*.stderr' -g '*.txt' 2>/dev/null || true; } | wc -l | tr -d ' ')"
auth_config_hits="$({ rg -n -e "MC_TOKEN is required" -e "Forbidden" -e "Unauthorized" "$RUN_DIR/workers" -g '*.stderr' -g '*.log' 2>/dev/null || true; } | wc -l | tr -d ' ')"
rate_limit_hits="$({ rg -n -e " 429" -e "error: 429" -e "URL returned error: 429" "$RUN_DIR/workers" -g '*.stderr' -g '*.log' 2>/dev/null || true; } | wc -l | tr -d ' ')"
ownership_acl_hits="$({ rg -n -e "owner required" -e "contributor or owner required" "$RUN_DIR/workers" -g '*.stderr' -g '*.log' 2>/dev/null || true; } | wc -l | tr -d ' ')"
shim_transport_hits="$({ rg -n -e "MCP startup incomplete" -e "timed out handshaking" -e "timed out after" -e "unexpected status code" -e "Connection refused" "$RUN_DIR/workers" -g '*.stderr' -g '*.log' -g '*.txt' 2>/dev/null || true; } | wc -l | tr -d ' ')"
api_5xx_hits="$({ rg -n -e "HTTP 5[0-9][0-9]" "$RUN_DIR/workers" -g '*.stderr' -g '*.log' 2>/dev/null || true; } | wc -l | tr -d ' ')"
scenario_assertion_hits="$({ rg -n -e "scenario_assertion" "$RUN_DIR/workers" -g '*.stderr' -g '*.log' 2>/dev/null || true; } | wc -l | tr -d ' ')"
playbook_results_file="$RUN_DIR/playbook-results.jsonl"
{ rg --no-heading -n "PLAYBOOK_RESULT_JSON=" "$RUN_DIR/workers" -g '*.log' 2>/dev/null || true; } \
  | sed 's/^.*PLAYBOOK_RESULT_JSON=//' > "$playbook_results_file"

jq -s \
  --arg report_version "$REPORT_VERSION" \
  --arg run_id "$RUN_ID" \
  --arg mode "$MODE" \
  --arg model "$MODEL" \
  --arg base_url "$BASE_URL" \
  --arg shim_host "$SHIM_HOST" \
  --argjson shim_port "$SHIM_PORT" \
  --arg scenario_file "$SCENARIO_FILE" \
  --argjson startup_timeout_hits "$startup_timeout_hits" \
  --argjson auth_config_hits "$auth_config_hits" \
  --argjson rate_limit_hits "$rate_limit_hits" \
  --argjson ownership_acl_hits "$ownership_acl_hits" \
  --argjson shim_transport_hits "$shim_transport_hits" \
  --argjson api_5xx_hits "$api_5xx_hits" \
  --argjson scenario_assertion_hits "$scenario_assertion_hits" \
  --slurpfile playbook_results "$playbook_results_file" \
  '{
    report_version: $report_version,
    run_id: $run_id,
    mode: $mode,
    model: $model,
    workers: length,
    duration_sec: (map(.end_ts - .start_ts) | max // 0),
    attempts: (map(.attempts)|add),
    successes: (map(.successes)|add),
    failures: (map(.failures)|add),
    fatal_worker_failures: (map(select(.failures > 0)) | length),
    success_rate: (if (map(.attempts)|add) == 0 then 0 else ((map(.successes)|add) / (map(.attempts)|add)) end),
    startup_timeout_hits: $startup_timeout_hits,
    metrics: {
      startup_timeout_hits: $startup_timeout_hits,
      rate_limit_hits: $rate_limit_hits
    },
    failures_by_category: {
      auth_config: $auth_config_hits,
      rate_limit: $rate_limit_hits,
      ownership_acl: $ownership_acl_hits,
      shim_transport: $shim_transport_hits,
      api_5xx: $api_5xx_hits,
      scenario_assertion: $scenario_assertion_hits
    },
    endpoint: {
      base_url: $base_url,
      shim_host: $shim_host,
      shim_port: $shim_port
    },
    scenario: {
      file: $scenario_file,
      playbook_results: ($playbook_results // [])
    },
    end_state: {
      playbook_results_count: (($playbook_results // []) | length),
      all_cleanup_succeeded: (($playbook_results // []) | all(.cleanup.kluster_deleted == true and .cleanup.mission_deleted == true)),
      task_counts_match: (($playbook_results // []) | all(.actual_task_count >= .expected_task_count))
    }
  }' "$RUN_DIR"/workers/*/status.json > "$RUN_DIR/summary.json"

pass=true
if [[ "$(jq -r '.fatal_worker_failures' "$RUN_DIR/summary.json")" -ne 0 ]]; then
  pass=false
fi
if [[ "$(jq -r '.end_state.task_counts_match' "$RUN_DIR/summary.json")" != "true" ]]; then
  pass=false
fi
if [[ "$MODE" == "playbook" ]] && [[ "$(jq -r '.end_state.all_cleanup_succeeded' "$RUN_DIR/summary.json")" != "true" ]]; then
  pass=false
fi
if [[ "$MODE" == "playbook" ]] && [[ "$(jq -r '.end_state.playbook_results_count' "$RUN_DIR/summary.json")" -eq 0 ]]; then
  pass=false
fi
jq --argjson pass "$pass" '. + {pass:$pass}' "$RUN_DIR/summary.json" > "$RUN_DIR/summary.tmp.json"
mv "$RUN_DIR/summary.tmp.json" "$RUN_DIR/summary.json"

echo "== summary =="
cat "$RUN_DIR/summary.json"

if [[ "$(jq -r '.pass' "$RUN_DIR/summary.json")" == "true" ]]; then
  echo "result=pass strict_gate=ok"
  exit 0
fi
echo "result=fail strict_gate=failed"
exit 1
