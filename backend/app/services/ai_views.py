from __future__ import annotations

from typing import Any

_ALLOWED_TYPES = {"cards", "kv", "table", "timeline", "log_stream", "action_bar"}


def validate_view_spec(spec: dict[str, Any]) -> tuple[bool, str | None]:
    if not isinstance(spec, dict):
        return False, "view spec must be an object"
    view_type = spec.get("type")
    if view_type not in _ALLOWED_TYPES:
        return False, f"unsupported view type: {view_type}"
    title = spec.get("title")
    if title is not None and not isinstance(title, str):
        return False, "view title must be a string"
    data = spec.get("data")
    if data is not None and not isinstance(data, (dict, list, str, int, float, bool)):
        return False, "view data must be JSON-compatible"
    actions = spec.get("actions")
    if actions is not None:
        if not isinstance(actions, list):
            return False, "view actions must be a list"
        for action in actions:
            if not isinstance(action, dict):
                return False, "view action entries must be objects"
            if not isinstance(action.get("label", ""), str):
                return False, "view action label must be a string"
            intent = action.get("intent")
            if intent is not None and not isinstance(intent, str):
                return False, "view action intent must be a string"
    return True, None
