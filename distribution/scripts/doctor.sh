#!/usr/bin/env bash
set -euo pipefail

ENDPOINT=""
TOKEN=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --endpoint)
      ENDPOINT="${2:-}"
      shift 2
      ;;
    --token)
      TOKEN="${2:-}"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

if ! command -v missioncontrol-mcp >/dev/null 2>&1; then
  echo "[FAIL] missioncontrol-mcp not found on PATH"
  exit 1
fi

echo "[OK] missioncontrol-mcp found: $(command -v missioncontrol-mcp)"

if ! missioncontrol-mcp --help >/dev/null 2>&1; then
  echo "[WARN] missioncontrol-mcp exists but --help failed"
else
  echo "[OK] missioncontrol-mcp --help"
fi

if [[ -n "$ENDPOINT" ]]; then
  if doctor_raw="$(MC_BASE_URL="$ENDPOINT" MC_TOKEN="$TOKEN" missioncontrol-mcp doctor 2>/dev/null)"; then
    echo "$doctor_raw" | sed -n '1,80p' >/tmp/missioncontrol_mcp_doctor.out
    if command -v jq >/dev/null 2>&1; then
      if echo "$doctor_raw" | jq -e --arg endpoint "$ENDPOINT" '.checks[$endpoint].health_ok == true' >/dev/null 2>&1; then
        echo "[OK] missioncontrol-mcp doctor health check"
      else
        echo "[WARN] missioncontrol-mcp doctor reports health check failure"
      fi
      if [[ -n "$TOKEN" ]]; then
        if echo "$doctor_raw" | jq -e --arg endpoint "$ENDPOINT" '.checks[$endpoint].tools_ok == true' >/dev/null 2>&1; then
          echo "[OK] missioncontrol-mcp doctor tools check"
        else
          echo "[WARN] missioncontrol-mcp doctor reports tools check failure"
        fi
      fi
    else
      echo "[INFO] jq not found; skipping missioncontrol-mcp doctor JSON assertions"
    fi
  else
    echo "[WARN] missioncontrol-mcp doctor command failed"
  fi
fi

if [[ -z "$ENDPOINT" ]]; then
  echo "[INFO] No endpoint set. Local bootstrap is complete; set MC_BASE_URL to connect."
  exit 0
fi

if [[ ! "$ENDPOINT" =~ ^https?:// ]]; then
  echo "[WARN] Endpoint does not start with http:// or https:// : $ENDPOINT"
  exit 0
fi

if command -v curl >/dev/null 2>&1; then
  if curl -fsS --connect-timeout 4 --max-time 8 "$ENDPOINT/" >/dev/null 2>&1; then
    echo "[OK] endpoint reachable: $ENDPOINT"
  else
    echo "[WARN] endpoint not reachable: $ENDPOINT"
  fi

  if [[ -n "$TOKEN" ]]; then
    if curl -fsS --connect-timeout 4 --max-time 8 \
      -H "Authorization: Bearer $TOKEN" \
      "$ENDPOINT/mcp/health" >/dev/null 2>&1; then
      echo "[OK] authenticated /mcp/health"
    else
      echo "[WARN] /mcp/health check failed (token invalid, auth policy, or connectivity)."
    fi
  else
    echo "[INFO] No token provided; skipping authenticated /mcp/health check."
  fi
else
  echo "[INFO] curl not found; skipping network checks."
fi

if command -v missioncontrol-explorer >/dev/null 2>&1; then
  if [[ -n "$ENDPOINT" ]]; then
    if explorer_raw="$(MC_BASE_URL="$ENDPOINT" MC_TOKEN="$TOKEN" missioncontrol-explorer tree --format json 2>/tmp/missioncontrol_explorer.err)"; then
      if command -v jq >/dev/null 2>&1; then
        if echo "$explorer_raw" | jq -e '.mission_count >= 0' >/dev/null 2>&1; then
          echo "[OK] missioncontrol-explorer tree --format json"
        else
          echo "[WARN] missioncontrol-explorer returned unexpected JSON shape"
        fi
      else
        echo "[OK] missioncontrol-explorer tree executed"
      fi
    else
      echo "[WARN] missioncontrol-explorer failed: $(tail -n 1 /tmp/missioncontrol_explorer.err 2>/dev/null || echo unknown_error)"
    fi
  else
    echo "[INFO] missioncontrol-explorer found; skipping explorer run because endpoint is empty"
  fi
else
  echo "[WARN] missioncontrol-explorer not found on PATH"
fi

echo "[DONE] doctor checks finished"
