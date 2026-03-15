"""Adapter registry — maps RuntimeKind values to adapter instances.

Populated at import time by the bottom of this module. Additional adapters
can be registered at any point before they are first used.
"""
from __future__ import annotations

from typing import Callable, TYPE_CHECKING

from app.ai_console.contracts import CapabilitySet, RuntimeKind

if TYPE_CHECKING:
    from app.ai_console.adapter import AgentRuntimeAdapter

# kind → factory callable (called once, result cached)
_registry: dict[RuntimeKind, Callable[[], "AgentRuntimeAdapter"]] = {}
# kind → singleton instance (lazy-initialised)
_instances: dict[RuntimeKind, "AgentRuntimeAdapter"] = {}


def register_adapter(kind: RuntimeKind, factory: "type[AgentRuntimeAdapter] | Callable[[], AgentRuntimeAdapter]") -> None:
    """Register a factory for a runtime kind. Replaces any existing registration."""
    _registry[kind] = factory
    # Invalidate any cached instance so the next call gets a fresh one.
    _instances.pop(kind, None)


def get_adapter(kind: "RuntimeKind | str") -> "AgentRuntimeAdapter":
    """Return the singleton adapter for *kind*, creating it if necessary."""
    if isinstance(kind, str):
        try:
            kind = RuntimeKind(kind)
        except ValueError:
            kind = RuntimeKind.opencode

    if kind not in _instances:
        if kind not in _registry:
            raise KeyError(f"No adapter registered for runtime kind {kind!r}")
        _instances[kind] = _registry[kind]()
    return _instances[kind]


def available_runtimes() -> list[CapabilitySet]:
    """Return capability sets for all registered runtimes, opencode first."""
    priority = [RuntimeKind.opencode, RuntimeKind.claude_code, RuntimeKind.codex]
    out: list[CapabilitySet] = []
    for kind in priority:
        if kind in _registry:
            try:
                out.append(get_adapter(kind).capability_set)
            except Exception:
                pass
    return out


def default_runtime() -> RuntimeKind:
    return RuntimeKind.opencode


# ── Bootstrap registrations ──────────────────────────────────────────────────
# Imported lazily to avoid circular imports during module loading.

def _bootstrap() -> None:
    from app.ai_console.adapters.opencode import OpencodeAdapter
    from app.ai_console.adapters.claude_code import ClaudeCodeAdapter
    from app.ai_console.adapters.codex import CodexAdapter

    register_adapter(RuntimeKind.opencode, OpencodeAdapter)
    register_adapter(RuntimeKind.claude_code, ClaudeCodeAdapter)
    register_adapter(RuntimeKind.codex, CodexAdapter)


_bootstrap()
