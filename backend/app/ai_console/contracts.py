"""Domain contracts for the AI Console runtime layer.

These dataclasses and enums define the stable interface between
MissionControl's coordination layer and pluggable agent runtimes.
They are intentionally free of FastAPI/SQLModel dependencies so they
can be imported anywhere without side-effects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class RuntimeKind(str, Enum):
    opencode = "opencode"
    claude_code = "claude_code"
    codex = "codex"
    native = "native"  # legacy fallback only


@dataclass(frozen=True)
class CapabilitySet:
    runtime_kind: RuntimeKind
    display_name: str
    icon_slug: str
    supports_streaming: bool
    supports_file_workspace: bool
    supports_tool_interception: bool  # can MC gate tool calls before execution
    supports_skill_packs: bool
    supports_session_resume: bool
    max_context_tokens: int


@dataclass
class NormalizedEvent:
    """A runtime-agnostic event that MC persists and serves to the UI."""
    schema_version: int  # 1
    family: str           # lifecycle | io | tool | approval | view | runtime
    event_type: str       # maps to existing AiEvent.event_type strings
    session_id: str
    turn_id: int | None
    runtime_kind: str
    payload: dict[str, Any]
    created_at: datetime


@dataclass
class RuntimePolicy:
    allowed_tools: list[str] = field(default_factory=list)   # empty = all MC tools allowed
    denied_tools: list[str] = field(default_factory=list)
    max_turns_per_session: int = 200
    require_approval_for_writes: bool = True
    workspace_ttl_seconds: int = 900


@dataclass
class SkillPackManifest:
    snapshot_id: str
    format: str            # "mc-skill-pack/v1"
    runtime_kind: str
    instruction_files: list[str]    # CLAUDE.md, AGENTS.md, SYSTEM_PROMPT.md
    tool_definition_files: list[str]  # TOOLS.json, tools/*.json
    context_files: list[str]
    sha256: str
    effective_version: str


class AdapterError(Exception):
    def __init__(self, message: str, recoverable: bool = True):
        super().__init__(message)
        self.recoverable = recoverable


class AdapterUnavailableError(AdapterError):
    def __init__(self, message: str = "Adapter service unavailable"):
        super().__init__(message, recoverable=True)


class AdapterTimeoutError(AdapterError):
    def __init__(self, message: str = "Adapter request timed out"):
        super().__init__(message, recoverable=True)
