from __future__ import annotations

import json
import os
from collections import deque
from datetime import datetime
from threading import Lock
from typing import Any

_MAX_ENTRIES = 5000
_EVENTS: deque[dict[str, Any]] = deque(maxlen=_MAX_ENTRIES)
_LOCK = Lock()


def emit_structured_log(event: dict[str, Any]) -> None:
    if not isinstance(event, dict):
        return
    payload = dict(event)
    payload.setdefault("timestamp", datetime.utcnow().isoformat() + "Z")

    with _LOCK:
        _EVENTS.append(payload)

    export_path = (os.getenv("MC_LOG_EXPORT_PATH") or "").strip()
    if not export_path:
        return
    try:
        line = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        with open(export_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        return


def recent_logs(limit: int = 200) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 1000))
    with _LOCK:
        items = list(_EVENTS)
    if safe_limit >= len(items):
        return items
    return items[-safe_limit:]
