#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib import error, request

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services.todo_import import build_task_description, extract_import_ids, parse_todo_markdown  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import markdown TODO items into a MissionControl mission backlog.")
    parser.add_argument("--base-url", required=True, help="MissionControl API base URL, e.g. http://localhost:8008")
    parser.add_argument("--token", required=True, help="Bearer token")
    parser.add_argument("--mission-name", required=True, help="Target mission name")
    parser.add_argument("--kluster-name", required=True, default="Flywheel Backlog", help="Target kluster name")
    parser.add_argument("--source", action="append", required=True, help="Path to markdown TODO source file")
    parser.add_argument("--include-completed", action="store_true", help="Import completed checklist items too")
    parser.add_argument("--dry-run", action="store_true", help="Show actions without creating tasks")
    return parser.parse_args()


def api_call(base_url: str, token: str, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with request.urlopen(req) as resp:
            text = resp.read().decode("utf-8")
            if not text:
                return {}
            return json.loads(text)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8")
        raise RuntimeError(f"{method} {path} failed: HTTP {exc.code} {detail}") from exc


def ensure_mission(base_url: str, token: str, mission_name: str, dry_run: bool) -> dict[str, Any]:
    missions = api_call(base_url, token, "GET", "/missions")
    for mission in missions:
        if mission.get("name") == mission_name:
            return mission
    if dry_run:
        return {"id": "dry-run-mission", "name": mission_name}
    return api_call(
        base_url,
        token,
        "POST",
        "/missions",
        {
            "name": mission_name,
            "description": f"Imported TODO backlog for {mission_name}",
            "visibility": "internal",
            "status": "active",
        },
    )


def ensure_kluster(base_url: str, token: str, mission_id: str, kluster_name: str, dry_run: bool) -> dict[str, Any]:
    klusters = api_call(base_url, token, "GET", f"/missions/{mission_id}/k")
    for kluster in klusters:
        if kluster.get("name") == kluster_name:
            return kluster
    if dry_run:
        return {"id": "dry-run-kluster", "name": kluster_name, "mission_id": mission_id}
    return api_call(
        base_url,
        token,
        "POST",
        f"/missions/{mission_id}/k",
        {
            "mission_id": mission_id,
            "name": kluster_name,
            "description": "Imported TODO backlog",
            "status": "active",
        },
    )


def existing_task_import_ids(base_url: str, token: str, mission_id: str, kluster_id: str) -> set[str]:
    tasks = api_call(base_url, token, "GET", f"/missions/{mission_id}/k/{kluster_id}/t")
    import_ids: set[str] = set()
    for task in tasks:
        import_ids |= extract_import_ids(str(task.get("description") or ""))
    return import_ids


def main() -> int:
    args = parse_args()
    source_paths = [Path(p).resolve() for p in args.source]
    missing = [str(path) for path in source_paths if not path.exists()]
    if missing:
        print(f"Missing source files: {', '.join(missing)}", file=sys.stderr)
        return 1

    mission = ensure_mission(args.base_url, args.token, args.mission_name, args.dry_run)
    mission_id = str(mission["id"])
    kluster = ensure_kluster(args.base_url, args.token, mission_id, args.kluster_name, args.dry_run)
    kluster_id = str(kluster["id"])
    import_ids = set() if args.dry_run else existing_task_import_ids(args.base_url, args.token, mission_id, kluster_id)

    created = 0
    skipped = 0
    for source_path in source_paths:
        content = source_path.read_text(encoding="utf-8")
        items = parse_todo_markdown(content, str(source_path), include_completed=args.include_completed)
        for item in items:
            if item.import_id in import_ids:
                skipped += 1
                continue
            title = item.text
            if len(title) > 220:
                title = f"{title[:217]}..."
            task_payload = {
                "kluster_id": kluster_id,
                "title": title,
                "description": build_task_description(item),
                "status": "proposed",
                "owner": "",
                "contributors": "",
                "dependencies": "",
                "definition_of_done": "",
                "related_artifacts": "",
            }
            if args.dry_run:
                print(f"DRY RUN create task: {title}")
            else:
                api_call(args.base_url, args.token, "POST", f"/missions/{mission_id}/k/{kluster_id}/t", task_payload)
                import_ids.add(item.import_id)
            created += 1

    print(
        f"Import complete: mission={args.mission_name} mission_id={mission_id} "
        f"kluster={args.kluster_name} kluster_id={kluster_id} created={created} skipped={skipped}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
