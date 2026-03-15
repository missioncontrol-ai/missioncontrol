"""CodexAdapter — future implementation stub.

Codex drives inference via subprocess (`codex --json`).
Diff-based post-execution approval: workspace diff → AiPendingAction rows
for runtimes without pre-execution tool interception.

Not yet functional. All method calls raise AdapterUnavailableError.
"""
from __future__ import annotations

from app.ai_console.adapter import AgentRuntimeAdapter, TurnResult
from app.ai_console.contracts import (
    AdapterUnavailableError,
    CapabilitySet,
    RuntimeKind,
    RuntimePolicy,
)

_CAPABILITIES = CapabilitySet(
    runtime_kind=RuntimeKind.codex,
    display_name="Codex",
    icon_slug="codex",
    supports_streaming=False,
    supports_file_workspace=True,
    supports_tool_interception=False,  # post-execution diff approval
    supports_skill_packs=False,
    supports_session_resume=False,
    max_context_tokens=128_000,
)


class CodexAdapter(AgentRuntimeAdapter):
    """Stub — OpenAI Codex subprocess adapter (Phase 4 implementation)."""

    @property
    def runtime_kind(self) -> RuntimeKind:
        return RuntimeKind.codex

    @property
    def capability_set(self) -> CapabilitySet:
        return _CAPABILITIES

    async def create_runtime_session(self, *, mc_session_id, system_context, policy, workspace_path) -> str:
        raise AdapterUnavailableError("Codex adapter is not yet implemented")

    async def submit_turn(self, *, runtime_session_id, message, emit_event) -> TurnResult:
        raise AdapterUnavailableError("Codex adapter is not yet implemented")

    async def approve_tool_call(self, *, runtime_session_id, tool_call_id) -> None:
        raise AdapterUnavailableError("Codex adapter is not yet implemented")

    async def reject_tool_call(self, *, runtime_session_id, tool_call_id, reason) -> None:
        raise AdapterUnavailableError("Codex adapter is not yet implemented")

    async def end_session(self, *, runtime_session_id) -> None:
        pass  # No-op for stub

    async def health_check(self) -> bool:
        return False
