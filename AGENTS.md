# Codex Repo Instructions

## Python test/runtime environment
- The project Python environment is at repo root: `.venv` (not `backend/.venv`).
- For backend tests, run from `backend/` using `uv` with the parent venv interpreter:
  - `cd backend && uv run --python ../.venv/bin/python -m unittest -q ...`
- Do not assume global `python`/`pytest` has required dependencies.

## Quick checks
- Backend syntax check:
  - `cd backend && uv run --python ../.venv/bin/python -m py_compile app/main.py`
- Web build check:
  - `cd web && npm run build`
- Rust `mc` check:
  - `cd integrations/mc && cargo check -p mc`

## Agent launch defaults (mc)
- Claude now uses the dedicated command family: `mc claude run|doctor|exec`.
- Codex now uses the dedicated command family: `mc codex run|doctor|exec`.
- `mc launch` remains for `gemini|openclaw|custom`.
