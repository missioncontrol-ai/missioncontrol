#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="${ROOT_DIR}/.venv/bin/python"

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "missing interpreter: ${VENV_PYTHON}" >&2
  echo "create the project virtualenv first" >&2
  exit 1
fi

echo "[test] backend unittest suite"
(
  cd "${ROOT_DIR}/backend"
  "${VENV_PYTHON}" -m unittest discover -s tests -p "test_*.py"
)

if [[ "${MC_PY_BRIDGE_LEGACY:-0}" == "1" ]]; then
  echo "[test] missioncontrol-mcp unittest suite (legacy)"
  (
    cd "${ROOT_DIR}/distribution/missioncontrol-mcp"
    PYTHONPATH=src "${VENV_PYTHON}" -m unittest discover -s tests -p "test_*.py"
  )
else
  echo "[test] skipping legacy missioncontrol-mcp suite (set MC_PY_BRIDGE_LEGACY=1 to enable)"
fi

if [[ "${MC_RUN_LAUNCH_REGRESSION:-0}" == "1" ]]; then
  echo "[test] mc launch regression harness"
  (
    cd "${ROOT_DIR}"
    bash scripts/launch-regression.sh
  )
else
  echo "[test] skipping mc launch regression harness (set MC_RUN_LAUNCH_REGRESSION=1 to enable)"
fi

echo "[test] completed"
