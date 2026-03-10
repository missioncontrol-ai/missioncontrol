from dataclasses import dataclass
from typing import Any

from fastapi import Query
from fastapi.params import Query as QueryParam


DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _resolve_limit(value: Any, *, fallback: int) -> int:
    if isinstance(value, QueryParam):
        resolved = value.default
    else:
        resolved = value
    if resolved is None:
        return fallback
    return int(resolved)


def bounded_limit(value: Any, *, default: int = DEFAULT_LIMIT, maximum: int = MAX_LIMIT) -> int:
    limit_value = _resolve_limit(value, fallback=default)
    return max(1, min(limit_value, maximum))


def limit_query(default: int = DEFAULT_LIMIT, maximum: int = MAX_LIMIT):
    return Query(default=default, ge=1, le=maximum)


@dataclass(frozen=True)
class CursorPage:
    items: list
    next_cursor: str | None


def cursor_window(items: list, *, limit: int, cursor_value) -> CursorPage:
    next_cursor = None
    window = items[:limit]
    if len(items) > limit and window:
        last = window[-1]
        next_cursor = str(cursor_value(last))
    return CursorPage(items=window, next_cursor=next_cursor)
