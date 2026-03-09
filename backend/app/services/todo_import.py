import hashlib
import re
from dataclasses import dataclass


_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
_CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.+?)\s*$")
_MARKER_RE = re.compile(r"\[todo-import-id:([0-9a-f]{16})\]")


@dataclass(frozen=True)
class TodoItem:
    source_path: str
    line_number: int
    heading_path: str
    text: str
    completed: bool
    import_id: str


def parse_todo_markdown(content: str, source_path: str, *, include_completed: bool = False) -> list[TodoItem]:
    items: list[TodoItem] = []
    headings: list[str] = []
    for index, raw_line in enumerate(content.splitlines(), start=1):
        heading_match = _HEADING_RE.match(raw_line)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            while len(headings) >= level:
                headings.pop()
            headings.append(heading_text)
            continue

        checkbox_match = _CHECKBOX_RE.match(raw_line)
        if not checkbox_match:
            continue

        completed = checkbox_match.group(1).lower() == "x"
        if completed and not include_completed:
            continue
        text = checkbox_match.group(2).strip()
        if not text:
            continue
        heading_path = " / ".join(headings)
        import_id = stable_import_id(source_path=source_path, heading_path=heading_path, text=text)
        items.append(
            TodoItem(
                source_path=source_path,
                line_number=index,
                heading_path=heading_path,
                text=text,
                completed=completed,
                import_id=import_id,
            )
        )
    return items


def stable_import_id(*, source_path: str, heading_path: str, text: str) -> str:
    normalized = f"{source_path.strip()}|{heading_path.strip()}|{text.strip()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def extract_import_ids(description: str) -> set[str]:
    if not description:
        return set()
    return {match.group(1) for match in _MARKER_RE.finditer(description)}


def build_task_description(item: TodoItem) -> str:
    parts = [
        f"Imported from `{item.source_path}` line {item.line_number}.",
    ]
    if item.heading_path:
        parts.append(f"Section: {item.heading_path}")
    parts.append(f"[todo-import-id:{item.import_id}]")
    return "\n".join(parts)
