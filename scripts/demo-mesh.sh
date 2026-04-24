#!/usr/bin/env bash
# demo-mesh.sh — end-to-end mc mesh work loop demo.
#
# Creates a 3-task A → B → C dependency chain, starts 3 Python workers
# attached to the mission, then tails the mesh event stream until all
# tasks reach "finished".
#
# Requirements:
#   - Backend running (MC_BASE_URL, default http://localhost:8008)
#   - mc CLI on PATH (integrations/mc)
#   - Python 3 on PATH
#   - MC_TOKEN set (or ~/.missioncontrol/config.json populated)
#
# Usage:
#   ./scripts/demo-mesh.sh [--base-url http://localhost:8008] [--timeout 120]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKER="${SCRIPT_DIR}/demo_workers/worker.py"

BASE_URL="${MC_BASE_URL:-http://localhost:8008}"
TIMEOUT="${DEMO_TIMEOUT:-120}"
TOKEN="${MC_TOKEN:-}"

cleanup_pids=()

cleanup() {
    for pid in "${cleanup_pids[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT INT TERM

log() { echo "[demo] $*" >&2; }

# ---- helpers ----

mc_api() {
    local method="$1" path="$2"
    local extra="${3:-}"
    local auth_header=""
    if [[ -n "$TOKEN" ]]; then
        auth_header="-H \"Authorization: Bearer $TOKEN\""
    fi
    if [[ "$method" == "GET" ]]; then
        curl -sf -H "Content-Type: application/json" \
            ${TOKEN:+-H "Authorization: Bearer $TOKEN"} \
            "${BASE_URL}${path}"
    else
        curl -sf -X POST -H "Content-Type: application/json" \
            ${TOKEN:+-H "Authorization: Bearer $TOKEN"} \
            -d "${extra:-{\}}" \
            "${BASE_URL}${path}"
    fi
}

task_status() {
    local task_id="$1"
    mc_api GET "/work/tasks/${task_id}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))"
}

# ---- 1. Create a mission ----
log "Creating mission..."
MISSION=$(mc_api POST "/missions" '{"name":"demo-mesh-'$(date +%s)'","owners":"demo@example.com"}')
MISSION_ID=$(echo "$MISSION" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
log "Mission: $MISSION_ID"

# ---- 2. Create a kluster ----
log "Creating kluster..."
KLUSTER=$(mc_api POST "/k" "{\"mission_id\":\"${MISSION_ID}\",\"name\":\"demo-k\",\"owners\":\"demo@example.com\"}")
KLUSTER_ID=$(echo "$KLUSTER" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
log "Kluster: $KLUSTER_ID"

# ---- 3. Seed A → B → C tasks ----
log "Creating task A (no deps)..."
TASK_A=$(mc_api POST "/work/klusters/${KLUSTER_ID}/tasks" \
    "{\"title\":\"A - foundation\",\"description\":\"First task, no deps\"}")
A_ID=$(echo "$TASK_A" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
log "Task A: $A_ID  status=$(echo $TASK_A | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['status'])")"

log "Creating task B (depends on A)..."
TASK_B=$(mc_api POST "/work/klusters/${KLUSTER_ID}/tasks" \
    "{\"title\":\"B - middle\",\"description\":\"Second task, depends on A\",\"depends_on\":[\"${A_ID}\"]}")
B_ID=$(echo "$TASK_B" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
log "Task B: $B_ID  status=$(echo $TASK_B | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['status'])")"

log "Creating task C (depends on B)..."
TASK_C=$(mc_api POST "/work/klusters/${KLUSTER_ID}/tasks" \
    "{\"title\":\"C - final\",\"description\":\"Third task, depends on B\",\"depends_on\":[\"${B_ID}\"]}")
C_ID=$(echo "$TASK_C" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
log "Task C: $C_ID  status=$(echo $TASK_C | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['status'])")"

log "Expected: A=ready  B=blocked  C=blocked"

# ---- 4. Start 3 Python workers ----
log "Starting 3 workers for mission $MISSION_ID..."

for i in 1 2 3; do
    MC_MESH_TASK_ID="" MC_TASK_MD_PATH="" \
    MC_BASE_URL="$BASE_URL" \
    mc run python --mission "$MISSION_ID" -- "$WORKER" &
    cleanup_pids+=($!)
    log "Worker $i PID: ${cleanup_pids[-1]}"
done

# ---- 5. Watch for all tasks to finish ----
log "Watching for completion (timeout ${TIMEOUT}s)..."

START=$SECONDS
DONE=0

while (( SECONDS - START < TIMEOUT )); do
    A_S=$(task_status "$A_ID" 2>/dev/null || echo "?")
    B_S=$(task_status "$B_ID" 2>/dev/null || echo "?")
    C_S=$(task_status "$C_ID" 2>/dev/null || echo "?")
    log "A=$A_S  B=$B_S  C=$C_S"

    if [[ "$A_S" == "finished" && "$B_S" == "finished" && "$C_S" == "finished" ]]; then
        DONE=1
        break
    fi

    sleep 3
done

if [[ $DONE -eq 1 ]]; then
    log "SUCCESS: A → B → C all finished in $(( SECONDS - START ))s"
    exit 0
else
    log "TIMEOUT: after ${TIMEOUT}s — A=$A_S  B=$B_S  C=$C_S"
    exit 1
fi
