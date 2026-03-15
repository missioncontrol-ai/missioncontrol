"""ClaudeCodeAdapter — future implementation stub.

Claude Code drives inference via subprocess (`claude --print --output-format stream-json`).
MCP server injection: --mcp-server points at MC's /mcp endpoint.

Not yet functional. All method calls raise AdapterUnavailableError.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from app.ai_console.adapter import AgentRuntimeAdapter, TurnResult
from app.ai_console.contracts import (
    AdapterUnavailableError,
    CapabilitySet,
    NormalizedEvent,
    RuntimeKind,
    RuntimePolicy,
)

_CAPABILITIES = CapabilitySet(
    runtime_kind=RuntimeKind.claude_code,
    display_name="Claude Code",
    icon_slug="claude",
    supports_streaming=True,
    supports_file_workspace=True,
    supports_tool_interception=False,   # post-execution diff approval
    supports_skill_packs=True,
    supports_session_resume=False,
    max_context_tokens=200_000,
)


class ClaudeCodeAdapter(AgentRuntimeAdapter):
    """Stub — Claude Code subprocess adapter (Phase 4 implementation)."""

    @property
    def runtime_kind(self) -> RuntimeKind:
        return RuntimeKind.claude_code

    @property
    def capability_set(self) -> CapabilitySet:
        return _CAPABILITIES

    async def create_runtime_session(self, *, mc_session_id, system_context, policy, workspace_path) -> str:
        raise AdapterUnavailableError("Claude Code adapter is not yet implemented")

    async def submit_turn(self, *, runtime_session_id, message, emit_event) -> TurnResult:
        raise AdapterUnavailableError("Claude Code adapter is not yet implemented")

    async def approve_tool_call(self, *, runtime_session_id, tool_call_id) -> None:
        raise AdapterUnavailableError("Claude Code adapter is not yet implemented")

    async def reject_tool_call(self, *, runtime_session_id, tool_call_id, reason) -> None:
        raise AdapterUnavailableError("Claude Code adapter is not yet implemented")

    async def end_session(self, *, runtime_session_id) -> None:
        pass  # No-op for stub

    async def health_check(self) -> bool:
        return False
