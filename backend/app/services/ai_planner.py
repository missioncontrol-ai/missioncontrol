from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any
from urllib import request as urllib_request


@dataclass
class PlannedToolCall:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass
class PlanResult:
    assistant_text: str
    tool_calls: list[PlannedToolCall] = field(default_factory=list)
    view_spec: dict[str, Any] | None = None

_READ_TOOLS = [
    "list_missions",
    "search_klusters",
    "search_tasks",
    "list_tasks",
    "read_doc",
    "read_artifact",
    "get_entity_history",
    "list_pending_ledger_events",
]


_WRITE_TOOLS = {
    "create_doc",
    "create_artifact",
    "create_task",
    "update_task",
    "delete_task",
    "create_mission",
    "create_kluster",
    "update_kluster",
    "delete_kluster",
    "update_mission",
    "delete_mission",
    "update_doc",
    "update_artifact",
    "commit_kluster_workspace",
    "release_kluster_workspace",
    "promote_local_skill_overlay",
}


def is_write_tool(name: str) -> bool:
    return name in _WRITE_TOOLS


def plan_turn(*, message: str) -> PlanResult:
    text = (message or "").strip()
    if not text:
        return PlanResult(assistant_text="Tell me what you want to inspect or change.")

    provider = (os.getenv("MC_AI_PROVIDER") or "").strip().lower()
    if provider in {"openai", "anthropic"}:
        planned = _provider_plan(provider=provider, message=text)
        if planned is not None:
            if planned.tool_calls or not _is_factual_query(text):
                return planned
            hinted = _tool_hint_plan(text)
            if hinted is not None:
                return hinted
            return planned

    hinted = _tool_hint_plan(text)
    if hinted is not None:
        return hinted
    return _heuristic_plan(text)


def finalize_assistant_text(*, message: str, draft_text: str, tool_results: list[dict[str, Any]]) -> str:
    generic_fallback = "I can run read operations immediately and queue write operations for approval."
    if not tool_results:
        if _is_factual_query(message) and generic_fallback in draft_text:
            return (
                "I could not map that question to an available MissionControl tool yet. "
                "Try asking about missions, klusters, tasks, docs, or artifacts. "
                "I can also run explicit commands like 'list missions' or 'search klusters <term>'."
            )
        return draft_text

    lower = message.lower()
    for item in tool_results:
        tool = str(item.get("tool") or "")
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        payload = result.get("result") if isinstance(result.get("result"), dict) else {}

        if tool == "search_klusters":
            klusters = payload.get("klusters") if isinstance(payload.get("klusters"), list) else []
            if "active kluster" in lower or ("active" in lower and "kluster" in lower):
                active = [k for k in klusters if str((k or {}).get("status") or "").lower() == "active"]
                if not active:
                    return "I checked your klusters and found no active klusters."
                names = ", ".join(str(k.get("name") or k.get("id")) for k in active[:8])
                suffix = f" (showing first 8: {names})" if names else ""
                return f"I found {len(active)} active klusters{suffix}."
            return f"I found {len(klusters)} klusters accessible to you."

        if tool == "list_missions":
            missions = payload.get("missions") if isinstance(payload.get("missions"), list) else []
            if not missions:
                return "You currently have no readable missions."
            names = ", ".join(str(m.get("name") or m.get("id")) for m in missions[:8])
            suffix = f" (showing first 8: {names})" if names else ""
            return f"You can access {len(missions)} missions{suffix}."

        if tool == "search_tasks":
            tasks = payload.get("tasks") if isinstance(payload.get("tasks"), list) else []
            return f"I found {len(tasks)} matching tasks."

    has_failures = any(
        isinstance((item.get("result") if isinstance(item.get("result"), dict) else {}), dict)
        and not bool((item.get("result") if isinstance(item.get("result"), dict) else {}).get("ok", False))
        for item in tool_results
    )
    if has_failures:
        return (
            "I ran into an issue while using one or more tools. "
            "You can expand the details to see the exact error, and I can retry with a narrower query."
        )

    return draft_text


def _is_factual_query(message: str) -> bool:
    lower = message.lower().strip()
    starters = ("are ", "is ", "what ", "which ", "show ", "list ", "how many ", "do we ", "can you ")
    return lower.endswith("?") or lower.startswith(starters)


def _tool_hint_plan(message: str) -> PlanResult | None:
    lower = message.lower()
    if "user" in lower and _is_factual_query(lower):
        return PlanResult(
            assistant_text=(
                "I do not currently have a tool to list platform users directly. "
                "I can still inspect missions, klusters, tasks, docs, and artifacts."
            ),
            tool_calls=[],
            view_spec={
                "type": "kv",
                "title": "Available Read Scope",
                "data": {"supported": "missions, klusters, tasks, docs, artifacts", "unsupported": "global user directory"},
            },
        )
    if "kluster" in lower:
        query = ""
        if "active" in lower:
            query = ""
        return PlanResult(
            assistant_text="I will check your klusters and summarize what I find.",
            tool_calls=[PlannedToolCall(tool="search_klusters", args={"query": query}, reason="kluster status query")],
            view_spec={
                "type": "table",
                "title": "Klusters",
                "data": {"columns": ["id", "name", "status", "mission_id"], "source": "tool:search_klusters"},
            },
        )
    if "mission" in lower and _is_factual_query(lower):
        return PlanResult(
            assistant_text="I will check your accessible missions.",
            tool_calls=[PlannedToolCall(tool="list_missions", args={}, reason="mission query")],
            view_spec={
                "type": "table",
                "title": "Missions",
                "data": {"columns": ["id", "name", "status", "owners"], "source": "tool:list_missions"},
            },
        )
    return None


def _heuristic_plan(message: str) -> PlanResult:
    lower = message.lower()

    if "mission" in lower and any(k in lower for k in ("list", "show", "what", "which")):
        return PlanResult(
            assistant_text="I will list missions you can access.",
            tool_calls=[PlannedToolCall(tool="list_missions", args={}, reason="mission inventory requested")],
            view_spec={
                "type": "table",
                "title": "Missions",
                "data": {"columns": ["id", "name", "status", "owners"], "source": "tool:list_missions"},
            },
        )

    if "task" in lower and any(k in lower for k in ("list", "show", "open")):
        return PlanResult(
            assistant_text=(
                "I can list tasks once you provide a `kluster_id` (for example: "
                "`list tasks for kluster abc123`)."
            ),
            view_spec={"type": "cards", "title": "Tasks", "data": {"hint": "Provide kluster_id to list tasks"}},
        )

    if any(k in lower for k in ("help", "what can you do", "capabilities")):
        return PlanResult(
            assistant_text=(
                "I can inspect missions, tasks, docs, artifacts, approvals, and invoke MissionControl tools. "
                "Ask in natural language and I will map to safe tool calls."
            ),
            view_spec={
                "type": "kv",
                "title": "AI Console Capabilities",
                "data": {
                    "reads": "auto-execute",
                    "writes": "approval-required",
                    "backend": "MCP tools",
                },
            },
        )

    return PlanResult(
        assistant_text=(
            "Understood. I can run read operations immediately and queue write operations for approval. "
            "If you want an action now, include the target (mission/kluster/task)."
        )
    )


def _provider_plan(*, provider: str, message: str) -> PlanResult | None:
    prompt = (
        "You are a MissionControl planner. Output strict JSON with keys: "
        "assistant_text (string), tool_calls (array of {tool,args,reason}), optional view_spec (object). "
        "Use only tool names from MissionControl MCP. For factual questions, call read tools instead of guessing. "
        f"Common read tools: {', '.join(_READ_TOOLS)}. If unsure, return empty tool_calls."
    )

    try:
        if provider == "openai":
            api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
            model = (os.getenv("MC_AI_MODEL") or "gpt-4o-mini").strip()
            base_url = (os.getenv("MC_AI_BASE_URL") or "https://api.openai.com").strip()
            if not api_key:
                return None
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": message},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
            }
            req = urllib_request.Request(
                _resolve_endpoint(base_url, "/v1/chat/completions"),
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib_request.urlopen(req, timeout=8) as resp:
                raw = json.loads(resp.read().decode("utf-8") or "{}")
            content = (
                raw.get("choices", [{}])[0].get("message", {}).get("content", "")
                if isinstance(raw, dict)
                else ""
            )
            return _parse_plan_json(content)

        if provider == "anthropic":
            api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
            model = (os.getenv("MC_AI_MODEL") or "claude-3-5-haiku-latest").strip()
            base_url = (os.getenv("MC_AI_BASE_URL") or "https://api.anthropic.com").strip()
            if not api_key:
                return None
            body = {
                "model": model,
                "max_tokens": 600,
                "temperature": 0.1,
                "system": prompt,
                "messages": [{"role": "user", "content": message}],
            }
            req = urllib_request.Request(
                _resolve_endpoint(base_url, "/v1/messages"),
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib_request.urlopen(req, timeout=8) as resp:
                raw = json.loads(resp.read().decode("utf-8") or "{}")
            blocks = raw.get("content", []) if isinstance(raw, dict) else []
            text = ""
            if isinstance(blocks, list):
                for block in blocks:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text += str(block.get("text") or "")
            return _parse_plan_json(text)
    except Exception:
        return None

    return None


def _resolve_endpoint(base_url: str, default_path: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return default_path
    if base.endswith("/chat/completions") or base.endswith("/messages"):
        return base
    if base.endswith("/v1") and default_path.startswith("/v1/"):
        return f"{base}{default_path[len('/v1'):]}"
    return f"{base}{default_path}"


def _parse_plan_json(content: str) -> PlanResult | None:
    try:
        parsed = json.loads(content or "{}")
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    assistant_text = str(parsed.get("assistant_text") or "")
    tool_calls_raw = parsed.get("tool_calls")
    tool_calls: list[PlannedToolCall] = []
    if isinstance(tool_calls_raw, list):
        for item in tool_calls_raw:
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool") or "").strip()
            if not tool:
                continue
            args = item.get("args") if isinstance(item.get("args"), dict) else {}
            reason = str(item.get("reason") or "")
            tool_calls.append(PlannedToolCall(tool=tool, args=args, reason=reason))
    view_spec = parsed.get("view_spec") if isinstance(parsed.get("view_spec"), dict) else None
    if not assistant_text and not tool_calls:
        return None
    return PlanResult(assistant_text=assistant_text or "I prepared an execution plan.", tool_calls=tool_calls, view_spec=view_spec)
