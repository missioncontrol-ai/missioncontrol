#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$ROOT_DIR/integrations/missioncontrol-mcp"
DST_DIR="$ROOT_DIR/distribution/mc-integration/missioncontrol-mcp"

mkdir -p "$DST_DIR"

rsync -a --delete \
  --exclude 'build/' \
  --exclude 'dist/' \
  --exclude '*.egg-info/' \
  --exclude '__pycache__/' \
  "$SRC_DIR/" "$DST_DIR/"

echo "[sync] synced missioncontrol-mcp source into distribution/mc-integration/missioncontrol-mcp"
