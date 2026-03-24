from __future__ import annotations

import os

from fastapi import HTTPException

from app.ai_console.contracts import RuntimeKind


_ALIASES = {
    "claude": RuntimeKind.claude_code,
    "claude_code": RuntimeKind.claude_code,
    "codex": RuntimeKind.codex,
    "opencode": RuntimeKind.opencode,
}


def configured_default_runtime() -> RuntimeKind:
    raw = (os.getenv("MC_CENTRAL_RUNTIME_DEFAULT") or "claude_code").strip().lower()
    return _ALIASES.get(raw, RuntimeKind.claude_code)


def normalize_runtime_kind(value: str | None) -> RuntimeKind:
    raw = (value or "").strip().lower()
    if not raw:
        return configured_default_runtime()
    resolved = _ALIASES.get(raw)
    if resolved is None:
        raise HTTPException(status_code=422, detail=f"Unsupported runtime_kind '{value}'")
    return resolved
