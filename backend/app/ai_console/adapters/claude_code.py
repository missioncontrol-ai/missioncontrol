"""ClaudeCodeAdapter — Anthropic SDK-backed runtime adapter."""
from __future__ import annotations

from datetime import datetime
import os
from typing import Any, Awaitable, Callable

from app.ai_console.adapter import AgentRuntimeAdapter, TurnResult
from app.ai_console.contracts import (
    AdapterTimeoutError,
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
    """Claude runtime adapter backed by Anthropic Messages API."""

    def __init__(self):
        self._api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
        self._model = (os.getenv("MC_CLAUDE_MODEL") or "claude-sonnet-4-5").strip()
        self._max_tokens = max(256, int((os.getenv("MC_CLAUDE_MAX_TOKENS") or "4096").strip()))
        self._timeout = max(10.0, float((os.getenv("MC_CLAUDE_TIMEOUT_SECONDS") or "120").strip()))
        self._client: Any | None = None

    def _client_or_raise(self):
        if not self._api_key:
            raise AdapterUnavailableError("ANTHROPIC_API_KEY is not configured for claude_code runtime")
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
            except Exception as exc:
                raise AdapterUnavailableError(f"anthropic SDK is not installed: {exc}")
            self._client = AsyncAnthropic(api_key=self._api_key, timeout=self._timeout)
        return self._client

    @property
    def runtime_kind(self) -> RuntimeKind:
        return RuntimeKind.claude_code

    @property
    def capability_set(self) -> CapabilitySet:
        return _CAPABILITIES

    async def create_runtime_session(self, *, mc_session_id, system_context, policy, workspace_path) -> str:
        self._client_or_raise()
        return f"claude:{mc_session_id}"

    async def submit_turn(self, *, runtime_session_id, message, emit_event) -> TurnResult:
        client = self._client_or_raise()
        try:
            response = await client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                messages=[{"role": "user", "content": message}],
            )
        except TimeoutError as exc:
            raise AdapterTimeoutError(f"claude request timed out: {exc}")
        except Exception as exc:
            raise AdapterUnavailableError(f"claude runtime request failed: {exc}")

        text_parts: list[str] = []
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", "") == "text":
                text = getattr(block, "text", "") or ""
                if text:
                    text_parts.append(text)
                    await emit_event(
                        NormalizedEvent(
                            schema_version=1,
                            family="io",
                            event_type="text_delta",
                            session_id=runtime_session_id,
                            turn_id=None,
                            runtime_kind=RuntimeKind.claude_code.value,
                            payload={"text": text},
                            created_at=datetime.utcnow(),
                        )
                    )

        await emit_event(
            NormalizedEvent(
                schema_version=1,
                family="lifecycle",
                event_type="done",
                session_id=runtime_session_id,
                turn_id=None,
                runtime_kind=RuntimeKind.claude_code.value,
                payload={"model": self._model},
                created_at=datetime.utcnow(),
            )
        )
        return TurnResult(assistant_text="".join(text_parts), events_emitted=len(text_parts) + 1)

    async def approve_tool_call(self, *, runtime_session_id, tool_call_id) -> None:
        return None

    async def reject_tool_call(self, *, runtime_session_id, tool_call_id, reason) -> None:
        return None

    async def end_session(self, *, runtime_session_id) -> None:
        return None

    async def health_check(self) -> bool:
        return bool(self._api_key)
