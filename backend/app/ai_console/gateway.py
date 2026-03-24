"""ConsoleGateway — session binding, approval gating, event normalisation.

Routes AI Console turns through the selected AgentRuntimeAdapter and wraps
the runtime's raw events with MC's coordination/governance layer.
"""
from __future__ import annotations

import json
from dataclasses import fields as dataclass_fields
from datetime import datetime
from typing import Any

from app.ai_console.adapter import TurnResult
from app.ai_console.contracts import (
    AdapterUnavailableError,
    NormalizedEvent,
    RuntimeKind,
    RuntimePolicy,
)
from app.ai_console.event_store import emit_event, emit_normalized
from app.ai_console.registry import default_runtime, get_adapter
from app.models import AiPendingAction, AiSession, AiTurn
from app.services.ids import new_hash_id


class ConsoleGateway:
    """Orchestrates adapter calls and MC-side persistence for every session turn."""

    # ── Session lifecycle ─────────────────────────────────────────────────────

    async def create_session(
        self,
        *,
        db,
        subject: str,
        session_id: str,
        title: str,
        runtime_kind: str,
        policy_dict: dict[str, Any],
    ) -> AiSession:
        adapter = get_adapter(runtime_kind)

        # Only pass keys that RuntimePolicy accepts.
        valid_keys = {f.name for f in dataclass_fields(RuntimePolicy)}
        policy = RuntimePolicy(**{k: v for k, v in policy_dict.items() if k in valid_keys})

        cap = adapter.capability_set
        system_context = _build_system_context()

        runtime_session_id: str | None = None
        try:
            runtime_session_id = await adapter.create_runtime_session(
                mc_session_id=session_id,
                system_context=system_context,
                policy=policy,
                workspace_path=None,
            )
        except AdapterUnavailableError:
            # Non-fatal — MC session is created without a live runtime.
            # Turns will surface a friendly error until opencode is available.
            runtime_session_id = None
        except Exception as exc:
            runtime_session_id = None

        now = datetime.utcnow()
        row = AiSession(
            id=session_id,
            owner_subject=subject,
            title=title,
            status="active",
            runtime_kind=runtime_kind,
            runtime_session_id=runtime_session_id,
            policy_json=json.dumps(policy_dict, separators=(",", ":")),
            capability_snapshot_json=json.dumps(
                {
                    "runtime_kind": cap.runtime_kind.value,
                    "display_name": cap.display_name,
                    "icon_slug": cap.icon_slug,
                    "supports_streaming": cap.supports_streaming,
                    "supports_tool_interception": cap.supports_tool_interception,
                    "max_context_tokens": cap.max_context_tokens,
                },
                separators=(",", ":"),
            ),
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)

        emit_event(
            db=db,
            session_id=row.id,
            turn_id=None,
            event_type="session_started",
            payload={
                "session_id": row.id,
                "title": row.title,
                "runtime_kind": runtime_kind,
                "runtime_available": runtime_session_id is not None,
            },
        )
        return row

    # ── Turn processing ───────────────────────────────────────────────────────

    async def process_turn(
        self,
        *,
        db,
        session_row: AiSession,
        user_turn: AiTurn,
        message: str,
        subject: str,
    ) -> TurnResult:
        if not session_row.runtime_session_id:
            return self._turn_no_runtime(db=db, session_row=session_row, user_turn=user_turn)

        adapter = get_adapter(session_row.runtime_kind)

        async def _emit(event: NormalizedEvent) -> None:
            emit_normalized(db=db, event=event)

        try:
            result = await adapter.submit_turn(
                runtime_session_id=session_row.runtime_session_id,
                message=message,
                emit_event=_emit,
            )
        except AdapterUnavailableError as exc:
            result = self._error_result(db=db, session_row=session_row, user_turn=user_turn, exc=exc)
        except Exception as exc:
            result = self._error_result(db=db, session_row=session_row, user_turn=user_turn, exc=exc)

        if result.assistant_text:
            _save_assistant_turn(db=db, session_id=session_row.id, text=result.assistant_text)

        return result

    def _turn_no_runtime(self, *, db, session_row: AiSession, user_turn: AiTurn) -> TurnResult:
        msg = (
            "The AI runtime service is currently unavailable. "
            f"Runtime '{session_row.runtime_kind}' could not be reached at startup. "
            "Check runtime credentials/configuration and restart your session."
        )
        emit_event(
            db=db,
            session_id=session_row.id,
            turn_id=user_turn.id,
            event_type="runtime_error",
            payload={"error": "runtime_unavailable", "runtime_kind": session_row.runtime_kind},
        )
        _save_assistant_turn(db=db, session_id=session_row.id, text=msg)
        return TurnResult(assistant_text=msg, error="runtime_unavailable")

    def _error_result(self, *, db, session_row: AiSession, user_turn: AiTurn, exc: Exception) -> TurnResult:
        msg = f"An error occurred while contacting the AI runtime: {exc}"
        emit_event(
            db=db,
            session_id=session_row.id,
            turn_id=user_turn.id,
            event_type="runtime_error",
            payload={"error": str(exc), "runtime_kind": session_row.runtime_kind},
        )
        _save_assistant_turn(db=db, session_id=session_row.id, text=msg)
        return TurnResult(assistant_text=msg, error=str(exc))

    # ── Approval flow ─────────────────────────────────────────────────────────

    async def approve_action(
        self,
        *,
        db,
        session_row: AiSession,
        action_row: AiPendingAction,
        subject: str,
    ) -> None:
        """Notify the runtime adapter that an operator approved a tool call."""
        if not session_row.runtime_session_id:
            return
        try:
            adapter = get_adapter(session_row.runtime_kind)
            await adapter.approve_tool_call(
                runtime_session_id=session_row.runtime_session_id,
                tool_call_id=action_row.id,
            )
        except Exception:
            pass  # Non-fatal — MC has already recorded the approval decision.

    async def reject_action(
        self,
        *,
        db,
        session_row: AiSession,
        action_row: AiPendingAction,
        subject: str,
        reason: str,
    ) -> None:
        """Notify the runtime adapter that an operator rejected a tool call."""
        if not session_row.runtime_session_id:
            return
        try:
            adapter = get_adapter(session_row.runtime_kind)
            await adapter.reject_tool_call(
                runtime_session_id=session_row.runtime_session_id,
                tool_call_id=action_row.id,
                reason=reason,
            )
        except Exception:
            pass

    async def end_session(self, *, db, session_row: AiSession) -> None:
        if session_row.runtime_session_id:
            try:
                adapter = get_adapter(session_row.runtime_kind)
                await adapter.end_session(runtime_session_id=session_row.runtime_session_id)
            except Exception:
                pass
        session_row.status = "closed"
        session_row.updated_at = datetime.utcnow()
        db.add(session_row)
        db.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_system_context() -> str:
    return (
        "You are MissionControl AI, an intelligent assistant for the MissionControl platform. "
        "You help operators understand missions, klusters, tasks, and platform state. "
        "Available tools allow you to read and write platform data. "
        "Read operations are executed automatically. "
        "Write operations require explicit operator approval before execution."
    )


def _save_assistant_turn(*, db, session_id: str, text: str) -> AiTurn:
    turn = AiTurn(
        session_id=session_id,
        role="assistant",
        content_json=json.dumps({"text": text}, separators=(",", ":")),
        created_at=datetime.utcnow(),
    )
    db.add(turn)
    db.commit()
    db.refresh(turn)
    return turn


# Module-level singleton — import and use directly.
_gateway = ConsoleGateway()


def get_gateway() -> ConsoleGateway:
    return _gateway
