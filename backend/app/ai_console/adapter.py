"""Abstract base class for all agent runtime adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

from app.ai_console.contracts import CapabilitySet, NormalizedEvent, RuntimeKind, RuntimePolicy

if TYPE_CHECKING:
    pass


class AgentRuntimeAdapter(ABC):
    """Protocol that all runtime drivers must implement.

    MissionControl calls these methods to manage agent sessions and turns.
    Each adapter translates MC's coordination language into the specific
    API of its underlying runtime (opencode, Claude Code, Codex, etc.).
    """

    @property
    @abstractmethod
    def runtime_kind(self) -> RuntimeKind: ...

    @property
    @abstractmethod
    def capability_set(self) -> CapabilitySet: ...

    @abstractmethod
    async def create_runtime_session(
        self,
        *,
        mc_session_id: str,
        system_context: str,        # injected mission/kluster/skill context
        policy: RuntimePolicy,
        workspace_path: str | None,
    ) -> str: ...                   # returns runtime_session_id

    @abstractmethod
    async def submit_turn(
        self,
        *,
        runtime_session_id: str,
        message: str,
        emit_event: Callable[[NormalizedEvent], Awaitable[None]],
    ) -> "TurnResult": ...

    @abstractmethod
    async def approve_tool_call(
        self,
        *,
        runtime_session_id: str,
        tool_call_id: str,
    ) -> None: ...

    @abstractmethod
    async def reject_tool_call(
        self,
        *,
        runtime_session_id: str,
        tool_call_id: str,
        reason: str,
    ) -> None: ...

    @abstractmethod
    async def end_session(self, *, runtime_session_id: str) -> None: ...

    @abstractmethod
    async def health_check(self) -> bool: ...


@dataclass
class TurnResult:
    assistant_text: str
    tool_calls_executed: int = 0
    pending_action_ids: list[str] = field(default_factory=list)
    events_emitted: int = 0
    error: str | None = None
