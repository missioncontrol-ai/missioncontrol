import argparse
import json
from typing import Any

from missioncontrol_mcp.server import http_json


def _render_tree_markdown(tree: dict[str, Any]) -> str:
    lines = [
        f"MissionControl Explorer: {tree.get('mission_count', 0)} missions, "
        f"{tree.get('cluster_count', 0)} clusters, {tree.get('task_count', 0)} tasks",
        "",
    ]
    for mission in tree.get("missions", []):
        lines.append(f"- Mission: {mission['name']} ({mission['id']})")
        for cluster in mission.get("clusters", []):
            lines.append(f"  - Cluster: {cluster['name']} ({cluster['id']}) tasks={cluster['task_count']}")
            for task in cluster.get("recent_tasks", []):
                lines.append(f"    - [{task['status']}] {task['title']} (task {task['id']})")
    if tree.get("unassigned_clusters"):
        lines.append("- Unassigned Clusters:")
        for cluster in tree["unassigned_clusters"]:
            lines.append(f"  - {cluster['name']} ({cluster['id']}) tasks={cluster['task_count']}")
    return "\n".join(lines)


def _render_tree_ansi(tree: dict[str, Any]) -> str:
    c_title = "\033[1;36m"
    c_node = "\033[1;32m"
    c_task = "\033[0;37m"
    c_reset = "\033[0m"
    lines = [
        (
            f"{c_title}MissionControl Explorer{c_reset} "
            f"missions={tree.get('mission_count', 0)} "
            f"clusters={tree.get('cluster_count', 0)} tasks={tree.get('task_count', 0)}"
        )
    ]
    for mission in tree.get("missions", []):
        lines.append(f"{c_node}M {mission['name']} ({mission['id']}){c_reset}")
        for cluster in mission.get("clusters", []):
            lines.append(f"  {c_node}C {cluster['name']} ({cluster['id']}){c_reset}")
            for task in cluster.get("recent_tasks", []):
                lines.append(f"    {c_task}T [{task['status']}] {task['title']}#{task['id']}{c_reset}")
    return "\n".join(lines)


def _render_output(payload: dict[str, Any], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(payload, indent=2, sort_keys=True)
    if fmt == "ansi":
        return _render_tree_ansi(payload)
    return _render_tree_markdown(payload)


def _tree_command(args: argparse.Namespace) -> int:
    query = []
    if args.q:
        query.append(f"q={args.q}")
    if args.status:
        query.append(f"status={args.status}")
    if args.limit_tasks_per_cluster:
        query.append(f"limit_tasks_per_cluster={args.limit_tasks_per_cluster}")
    suffix = f"?{'&'.join(query)}" if query else ""
    payload = http_json("GET", f"/explorer/tree{suffix}")
    print(_render_output(payload, args.format))
    return 0


def _show_command(args: argparse.Namespace) -> int:
    payload = http_json("GET", f"/explorer/node/{args.node_type}/{args.node_id}")
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, indent=2))
    return 0


def _admin_policy_show(args: argparse.Namespace) -> int:
    payload = http_json("GET", "/governance/policy/active")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _admin_policy_draft_create(args: argparse.Namespace) -> int:
    payload = http_json(
        "POST",
        "/governance/policy/drafts",
        {"policy": None, "change_note": args.note or "created from cli"},
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _admin_policy_validate(args: argparse.Namespace) -> int:
    http_json("POST", f"/governance/policy/drafts/{args.draft_id}/validate")
    print(f"ok: validated draft {args.draft_id}")
    return 0


def _admin_policy_publish(args: argparse.Namespace) -> int:
    payload = http_json(
        "POST",
        f"/governance/policy/drafts/{args.draft_id}/publish",
        {"change_note": args.note or "published from cli"},
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _admin_policy_rollback(args: argparse.Namespace) -> int:
    payload = http_json(
        "POST",
        "/governance/policy/rollback",
        {"version": args.version, "change_note": args.note or ""},
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="MissionControl explorer CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    tree = sub.add_parser("tree", help="render mission/cluster/task explorer tree")
    tree.add_argument("--q", default="", help="text filter")
    tree.add_argument("--status", default="", help="task status filter")
    tree.add_argument("--limit-tasks-per-cluster", type=int, default=5)
    tree.add_argument("--format", choices=["markdown", "ansi", "json"], default="markdown")
    tree.set_defaults(func=_tree_command)

    show = sub.add_parser("show", help="show details for a node")
    show.add_argument("node_type", choices=["mission", "cluster", "task"])
    show.add_argument("node_id")
    show.add_argument("--format", choices=["json", "markdown"], default="json")
    show.set_defaults(func=_show_command)

    admin = sub.add_parser("admin", help="governance admin commands")
    admin_sub = admin.add_subparsers(dest="admin_command", required=True)

    admin_policy = admin_sub.add_parser("policy", help="manage governance policy")
    admin_policy_sub = admin_policy.add_subparsers(dest="admin_policy_command", required=True)

    admin_show = admin_policy_sub.add_parser("show", help="show active governance policy")
    admin_show.set_defaults(func=_admin_policy_show)

    admin_draft_create = admin_policy_sub.add_parser("draft-create", help="create governance draft")
    admin_draft_create.add_argument("--note", default="")
    admin_draft_create.set_defaults(func=_admin_policy_draft_create)

    admin_validate = admin_policy_sub.add_parser("validate", help="validate draft")
    admin_validate.add_argument("--draft-id", type=int, required=True)
    admin_validate.set_defaults(func=_admin_policy_validate)

    admin_publish = admin_policy_sub.add_parser("publish", help="publish draft")
    admin_publish.add_argument("--draft-id", type=int, required=True)
    admin_publish.add_argument("--note", default="")
    admin_publish.set_defaults(func=_admin_policy_publish)

    admin_rollback = admin_policy_sub.add_parser("rollback", help="rollback to version")
    admin_rollback.add_argument("--version", type=int, required=True)
    admin_rollback.add_argument("--note", default="")
    admin_rollback.set_defaults(func=_admin_policy_rollback)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
