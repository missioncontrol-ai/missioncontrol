#!/usr/bin/env bash
set -euo pipefail

docker rm -f missioncontrol-api-dev >/dev/null
echo "missioncontrol-api-dev stopped"
