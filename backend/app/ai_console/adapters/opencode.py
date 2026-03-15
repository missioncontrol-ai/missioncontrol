"""OpencodeAdapter — connects MissionControl to opencode's HTTP service.

opencode (https://github.com/sst/opencode) runs as a headless HTTP server via:
    opencode serve --hostname 0.0.0.0 --port 4096

Real opencode API (from packages/opencode/src/server/routes/):
  GET    /global/health                     → {healthy: true, version: "x.y.z"}
  GET    /global/event                      → SSE stream (global bus events)
  GET    /event                             → SSE stream (instance-scoped events)
  POST   /session                           → Session.Info  (body optional)
  DELETE /session/{sessionID}              → true
  POST   /session/{sessionID}/message      → streams JSON response (blocking)
  POST   /session/{sessionID}/prompt_async → 204 (non-blocking, events via SSE)
  GET    /permission                        → pending permission requests
  POST   /permission/{requestID}/reply     → {reply: "once"|"always"|"reject", message?: str}

Message body (POST /session/{id}/message):
  {parts: [{type: "text", text: "..."}], system?: "...", model?: {providerID, modelID}}

Permission events come through the SSE stream with type "permission.asked".
Reply via POST /permission/{requestID}/reply.

Environment variables:
  MC_OPENCODE_URL              — default: http://opencode:4096
  MC_OPENCODE_TIMEOUT_SECONDS  — default: 120
  OPENCODE_SERVER_PASSWORD     — if set on the opencode side, set here too
  OPENCODE_SERVER_USERNAME     — default: "opencode" (if password is set)
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from typing import Awaitable, Callable

from app.ai_console.adapter import AgentRuntimeAdapter, TurnResult
from app.ai_console.contracts import (
    AdapterTimeoutError,
    AdapterUnavailableError,
    CapabilitySet,
    NormalizedEvent,
    RuntimeKind,
    RuntimePolicy,
)

_OPENCODE_URL = os.getenv("MC_OPENCODE_URL", "http://opencode:4096").rstrip("/")
_OPENCODE_TIMEOUT = float(os.getenv("MC_OPENCODE_TIMEOUT_SECONDS", "120"))
_OPENCODE_PASSWORD = os.getenv("OPENCODE_SERVER_PASSWORD", "")
_OPENCODE_USERNAME = os.getenv("OPENCODE_SERVER_USERNAME", "opencode")

_CAPABILITIES = CapabilitySet(
    runtime_kind=RuntimeKind.opencode,
    display_name="opencode",
    icon_slug="opencode",
    supports_streaming=True,
    supports_file_workspace=True,
    supports_tool_interception=True,
    supports_skill_packs=True,
    supports_session_resume=True,
    max_context_tokens=200_000,
)


class OpencodeAdapter(AgentRuntimeAdapter):
    """Connects to opencode running as an HTTP service (MC_OPENCODE_URL)."""

    @property
    def runtime_kind(self) -> RuntimeKind:
        return RuntimeKind.opencode

    @property
    def capability_set(self) -> CapabilitySet:
        return _CAPABILITIES

    async def create_runtime_session(
        self,
        *,
        mc_session_id: str,
        system_context: str,
        policy: RuntimePolicy,
        workspace_path: str | None,
    ) -> str:
        # POST /session — body is optional; opencode generates its own session ID
        body: dict = {}
        data = await self._post("/session", body, timeout=10)
        session_id = data.get("id")
        if not session_id:
            raise AdapterUnavailableError(f"opencode returned no session id: {data!r}")
        return str(session_id)

    async def submit_turn(
        self,
        *,
        runtime_session_id: str,
        message: str,
        emit_event: Callable[[NormalizedEvent], Awaitable[None]],
    ) -> TurnResult:
        # Use the blocking POST /session/{id}/message endpoint — it streams
        # the complete response and returns when the turn is done.
        # This avoids the prompt_async + status-polling race condition where
        # a fast model completes before the first poll fires.
        body = {"parts": [{"type": "text", "text": message}]}
        response = await self._post(
            f"/session/{runtime_session_id}/message",
            body,
            timeout=_OPENCODE_TIMEOUT,
        )

        # Response is {info: {role, modelID, providerID, tokens, ...}, parts: [...]}
        assistant_text_parts: list[str] = []
        events_emitted = 0

        if isinstance(response, dict):
            parts = response.get("parts", [])
            for part in parts if isinstance(parts, list) else []:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text", "")
                    if text:
                        assistant_text_parts.append(text)
                        norm = NormalizedEvent(
                            schema_version=1,
                            family="io",
                            event_type="text_delta",
                            session_id=runtime_session_id,
                            turn_id=None,
                            runtime_kind=RuntimeKind.opencode.value,
                            payload={"text": text},
                            created_at=datetime.utcnow(),
                        )
                        await emit_event(norm)
                        events_emitted += 1

        norm = NormalizedEvent(
            schema_version=1,
            family="lifecycle",
            event_type="done",
            session_id=runtime_session_id,
            turn_id=None,
            runtime_kind=RuntimeKind.opencode.value,
            payload={"model": (response or {}).get("info", {}).get("modelID", "") if isinstance(response, dict) else ""},
            created_at=datetime.utcnow(),
        )
        await emit_event(norm)
        events_emitted += 1

        return TurnResult(
            assistant_text="".join(assistant_text_parts),
            events_emitted=events_emitted,
        )

    async def approve_tool_call(self, *, runtime_session_id: str, tool_call_id: str) -> None:
        # tool_call_id here is the permission requestID from opencode
        # POST /permission/{requestID}/reply with reply="once"
        await self._post(
            f"/permission/{tool_call_id}/reply",
            {"reply": "once"},
            timeout=10,
        )

    async def reject_tool_call(self, *, runtime_session_id: str, tool_call_id: str, reason: str) -> None:
        await self._post(
            f"/permission/{tool_call_id}/reply",
            {"reply": "reject", "message": reason},
            timeout=10,
        )

    async def end_session(self, *, runtime_session_id: str) -> None:
        try:
            await self._delete(f"/session/{runtime_session_id}", timeout=5)
        except Exception:
            pass  # Non-fatal

    async def health_check(self) -> bool:
        try:
            data = await self._get("/global/health", timeout=3)
            return isinstance(data, dict) and data.get("healthy") is True
        except Exception:
            return False

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if _OPENCODE_PASSWORD:
            creds = base64.b64encode(f"{_OPENCODE_USERNAME}:{_OPENCODE_PASSWORD}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"
        return headers

    async def _request(self, method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict | list:
        url = f"{_OPENCODE_URL}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self._auth_headers())
        try:
            loop = asyncio.get_event_loop()
            response_bytes = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=timeout).read()),
                timeout=timeout + 1,
            )
            return json.loads(response_bytes or b"{}")
        except asyncio.TimeoutError:
            raise AdapterTimeoutError(f"opencode request to {path} timed out")
        except urllib.error.HTTPError as exc:
            if exc.code == 204:
                return {}
            raise AdapterUnavailableError(f"opencode HTTP {exc.code} for {path}: {exc.reason}")
        except urllib.error.URLError as exc:
            raise AdapterUnavailableError(f"opencode unreachable at {_OPENCODE_URL}: {exc}")
        except Exception as exc:
            raise AdapterUnavailableError(f"opencode request failed: {exc}")

    async def _post_raw(self, path: str, body: dict, timeout: float = 30, expect_204: bool = False) -> dict | list:
        url = f"{_OPENCODE_URL}{path}"
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method="POST", headers=self._auth_headers())
        try:
            loop = asyncio.get_event_loop()

            def _do_request():
                try:
                    resp = urllib.request.urlopen(req, timeout=timeout)
                    return resp.read()
                except urllib.error.HTTPError as exc:
                    if exc.code == 204:
                        return b""
                    raise

            response_bytes = await asyncio.wait_for(
                loop.run_in_executor(None, _do_request),
                timeout=timeout + 1,
            )
            if not response_bytes:
                return {}
            return json.loads(response_bytes)
        except asyncio.TimeoutError:
            raise AdapterTimeoutError(f"opencode POST to {path} timed out")
        except urllib.error.URLError as exc:
            raise AdapterUnavailableError(f"opencode unreachable: {exc}")
        except Exception as exc:
            raise AdapterUnavailableError(f"opencode request failed: {exc}")

    async def _post(self, path: str, body: dict, timeout: float = 30) -> dict | list:
        return await self._request("POST", path, body=body, timeout=timeout)

    async def _get(self, path: str, timeout: float = 10) -> dict | list:
        return await self._request("GET", path, body=None, timeout=timeout)

    async def _delete(self, path: str, timeout: float = 5) -> dict | list:
        return await self._request("DELETE", path, body=None, timeout=timeout)
