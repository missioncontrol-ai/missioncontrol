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

echo "[test] missioncontrol-mcp unittest suite"
(
  cd "${ROOT_DIR}/integrations/missioncontrol-mcp"
  PYTHONPATH=src "${VENV_PYTHON}" -m unittest discover -s tests -p "test_*.py"
)

echo "[test] completed"
