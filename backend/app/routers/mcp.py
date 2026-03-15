import json
import os
import hashlib
import base64
import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request, Response
from sqlmodel import select
from app.db import get_session
from app.models import (
    AgentMessage,
    Kluster,
    Mission,
    MissionRoleMembership,
    Doc,
    Artifact,
    Epic,
    IngestionJob,
    Task,
    OverlapSuggestion,
    Agent,
    AgentSession,
    TaskAssignment,
    SkillSnapshot,
    UserProfile,
)
from app.schemas import MCPCall, MCPResponse, MCPTool
from app.services.authz import (
    assert_mission_owner_or_admin,
    assert_mission_reader_or_admin,
    assert_mission_writer_or_admin,
    is_platform_admin,
    readable_mission_ids_for_request,
)
from app.services.vectorstore import query_tasks
from app.services.overlap import task_text
from app.services.vectorstore import index_task
from app.services.vectorstore import index_doc
from app.services.git_ledger import (
    enqueue_ledger_event,
    list_pending_ledger_events,
    publish_pending_ledger_events,
    get_entity_history,
    subject_can_publish_mission,
    actor_subject_from_request,
    request_source,
)
from app.services.git_publisher import GitPublishError
from app.services.schema_pack import enforce_schema_pack
from app.services.ids import new_hash_id
from app.services.task_identity import ensure_task_public_id, resolve_task_by_ref
from app.services.governance import (
    approval_trace_from_context,
    extract_approval_context,
    require_policy_action,
)
from app.services.skills import (
    resolve_effective_snapshot,
    get_sync_state,
    upsert_sync_state,
    validate_kluster_scope,
)
from app.services.object_storage import (
    build_scoped_key,
    get_bytes_from_uri,
    object_storage_enabled,
    presign_get_uri,
    put_bytes,
    scoped_prefix,
)
from app.services.workspace import (
    assert_lease_owner_or_admin,
    commit_workspace_changes,
    create_workspace_lease,
    get_lease,
    heartbeat_workspace_lease,
    release_workspace_lease,
)
from app.services.keystone import ensure_mission_northstar, ensure_kluster_workstream

router = APIRouter(prefix="/mcp", tags=["mcp"])


def _owner_list(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]

TOOLS = [
    MCPTool(
        name="search_klusters",
        description="Search klusters by name or tag",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
    ),
    MCPTool(
        name="search_tasks",
        description="Search tasks by semantic similarity",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    ),
    MCPTool(
        name="read_doc",
        description="Read a doc by id",
        input_schema={"type": "object", "properties": {"doc_id": {"type": "integer"}}},
    ),
    MCPTool(
        name="create_doc",
        description="Create a doc in a kluster",
        input_schema={
            "type": "object",
            "properties": {
                "kluster_id": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "doc_type": {"type": "string"},
                "status": {"type": "string"},
                "provenance": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name="create_artifact",
        description="Create an artifact in a kluster",
        input_schema={
            "type": "object",
            "properties": {
                "kluster_id": {"type": "string"},
                "name": {"type": "string"},
                "artifact_type": {"type": "string"},
                "uri": {"type": "string"},
                "storage_backend": {"type": "string"},
                "content_sha256": {"type": "string"},
                "size_bytes": {"type": "integer"},
                "mime_type": {"type": "string"},
                "status": {"type": "string"},
                "provenance": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name="get_artifact_download_url",
        description="Get a short-lived download URL for an S3-backed artifact",
        input_schema={
            "type": "object",
            "properties": {
                "artifact_id": {"type": "integer"},
                "expires_seconds": {"type": "integer"},
            },
        },
    ),
    MCPTool(
        name="load_kluster_workspace",
        description="Load/sync a kluster workspace and acquire a lease",
        input_schema={
            "type": "object",
            "properties": {
                "kluster_id": {"type": "string"},
                "workspace_label": {"type": "string"},
                "agent_id": {"type": "string"},
                "lease_seconds": {"type": "integer"},
            },
            "required": ["kluster_id"],
        },
    ),
    MCPTool(
        name="heartbeat_workspace_lease",
        description="Extend a workspace lease heartbeat",
        input_schema={
            "type": "object",
            "properties": {"lease_id": {"type": "string"}},
            "required": ["lease_id"],
        },
    ),
    MCPTool(
        name="fetch_workspace_artifact",
        description="Fetch artifact bytes or signed download URL while a lease is active",
        input_schema={
            "type": "object",
            "properties": {
                "lease_id": {"type": "string"},
                "artifact_id": {"type": "integer"},
                "mode": {"type": "string"},
                "expires_seconds": {"type": "integer"},
            },
            "required": ["lease_id", "artifact_id"],
        },
    ),
    MCPTool(
        name="commit_kluster_workspace",
        description="Commit workspace changes with optimistic conflict checks",
        input_schema={
            "type": "object",
            "properties": {
                "lease_id": {"type": "string"},
                "change_set": {"type": "array"},
                "validation_mode": {"type": "string"},
            },
            "required": ["lease_id", "change_set"],
        },
    ),
    MCPTool(
        name="release_kluster_workspace",
        description="Release an active workspace lease",
        input_schema={
            "type": "object",
            "properties": {
                "lease_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["lease_id"],
        },
    ),
    MCPTool(
        name="create_task",
        description="Create a task in a kluster",
        input_schema={
            "type": "object",
            "properties": {
                "kluster_id": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "owner": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name="update_task",
        description="Update a task by id",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "status": {"type": "string"},
                "owner": {"type": "string"},
                "contributors": {"type": "string"},
                "dependencies": {"type": "string"},
                "definition_of_done": {"type": "string"},
                "related_artifacts": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name="delete_task",
        description="Delete a task by id",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name="list_tasks",
        description="List tasks for a kluster",
        input_schema={"type": "object", "properties": {"kluster_id": {"type": "string"}}},
    ),
    MCPTool(
        name="list_pending_ledger_events",
        description="List pending ledger events (default mission-scoped)",
        input_schema={
            "type": "object",
            "properties": {
                "mission_id": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    ),
    MCPTool(
        name="publish_pending_ledger_events",
        description="Publish pending mission-scoped ledger events to Git",
        input_schema={
            "type": "object",
            "properties": {
                "mission_id": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name="get_entity_history",
        description="Get ledger event history for an entity",
        input_schema={
            "type": "object",
            "properties": {
                "mission_id": {"type": "string"},
                "entity_type": {"type": "string"},
                "entity_id": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    ),
    MCPTool(
        name="create_mission",
        description="Create a mission",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "owners": {"type": "string"},
                "contributors": {"type": "string"},
                "tags": {"type": "string"},
                "visibility": {"type": "string"},
                "status": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name="list_missions",
        description="List missions readable by current principal",
        input_schema={"type": "object", "properties": {}},
    ),
    MCPTool(
        name="create_kluster",
        description="Create a kluster in a mission",
        input_schema={
            "type": "object",
            "properties": {
                "mission_id": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "owners": {"type": "string"},
                "contributors": {"type": "string"},
                "tags": {"type": "string"},
                "status": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name="update_kluster",
        description="Update a kluster by id",
        input_schema={
            "type": "object",
            "properties": {
                "kluster_id": {"type": "string"},
                "mission_id": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "owners": {"type": "string"},
                "contributors": {"type": "string"},
                "tags": {"type": "string"},
                "status": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name="delete_kluster",
        description="Delete a kluster by id",
        input_schema={
            "type": "object",
            "properties": {
                "kluster_id": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name="update_mission",
        description="Update a mission by id",
        input_schema={
            "type": "object",
            "properties": {
                "mission_id": {"type": "string"},
                "description": {"type": "string"},
                "owners": {"type": "string"},
                "contributors": {"type": "string"},
                "tags": {"type": "string"},
                "visibility": {"type": "string"},
                "status": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name="delete_mission",
        description="Delete a mission by id",
        input_schema={
            "type": "object",
            "properties": {
                "mission_id": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name="update_doc",
        description="Update a doc by id",
        input_schema={
            "type": "object",
            "properties": {
                "doc_id": {"type": "integer"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "doc_type": {"type": "string"},
                "status": {"type": "string"},
                "provenance": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name="update_artifact",
        description="Update an artifact by id",
        input_schema={
            "type": "object",
            "properties": {
                "artifact_id": {"type": "integer"},
                "name": {"type": "string"},
                "artifact_type": {"type": "string"},
                "uri": {"type": "string"},
                "status": {"type": "string"},
                "provenance": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name="get_overlap_suggestions",
        description="Get overlap suggestions for a task",
        input_schema={"type": "object", "properties": {"task_id": {"type": "string"}}},
    ),
    MCPTool(
        name="register_agent",
        description="Register a new agent",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "capabilities": {"type": "string"},
                "metadata": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name="list_agents",
        description="List registered agents",
        input_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name="get_agent",
        description="Get agent by id",
        input_schema={
            "type": "object",
            "properties": {
                "agent_id": {"type": "integer"},
            },
        },
    ),
    MCPTool(
        name="update_agent_status",
        description="Update agent status (online/offline)",
        input_schema={
            "type": "object",
            "properties": {
                "agent_id": {"type": "integer"},
                "status": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name="start_agent_session",
        description="Start an agent session (brings agent online)",
        input_schema={
            "type": "object",
            "properties": {
                "agent_id": {"type": "integer"},
                "context": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name="end_agent_session",
        description="End an agent session (brings agent offline)",
        input_schema={
            "type": "object",
            "properties": {
                "agent_id": {"type": "integer"},
                "session_id": {"type": "integer"},
            },
        },
    ),
    MCPTool(
        name="claim_task",
        description="Claim a task for an agent",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "agent_id": {"type": "integer"},
            },
        },
    ),
    MCPTool(
        name="release_task",
        description="Release a claimed task back to available",
        input_schema={
            "type": "object",
            "properties": {
                "assignment_id": {"type": "integer"},
            },
        },
    ),
    MCPTool(
        name="list_task_assignments",
        description="List task assignments",
        input_schema={
            "type": "object",
            "properties": {
                "agent_id": {"type": "integer"},
                "task_id": {"type": "string"},
                "status": {"type": "string"},
            },
        },
    ),
    MCPTool(
        name="resolve_skill_snapshot",
        description="Resolve effective mission+kluster skill snapshot",
        input_schema={
            "type": "object",
            "properties": {
                "mission_id": {"type": "string"},
                "kluster_id": {"type": "string"},
            },
            "required": ["mission_id"],
        },
    ),
    MCPTool(
        name="download_skill_snapshot",
        description="Download a resolved skill snapshot tarball",
        input_schema={
            "type": "object",
            "properties": {
                "snapshot_id": {"type": "string"},
            },
            "required": ["snapshot_id"],
        },
    ),
    MCPTool(
        name="get_skill_sync_status",
        description="Read last recorded local skill sync state",
        input_schema={
            "type": "object",
            "properties": {
                "mission_id": {"type": "string"},
                "kluster_id": {"type": "string"},
                "agent_id": {"type": "string"},
            },
            "required": ["mission_id"],
        },
    ),
    MCPTool(
        name="ack_skill_sync",
        description="Record local sync/apply status for mission/kluster skill snapshot",
        input_schema={
            "type": "object",
            "properties": {
                "mission_id": {"type": "string"},
                "kluster_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "snapshot_id": {"type": "string"},
                "snapshot_sha256": {"type": "string"},
                "local_overlay_sha256": {"type": "string"},
                "degraded_offline": {"type": "boolean"},
                "drift_flag": {"type": "boolean"},
                "drift_details": {"type": "object"},
            },
            "required": ["mission_id"],
        },
    ),
    MCPTool(
        name="promote_local_skill_overlay",
        description="Mark local overlay as promoted for current mission/kluster state",
        input_schema={
            "type": "object",
            "properties": {
                "mission_id": {"type": "string"},
                "kluster_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "local_overlay_sha256": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["mission_id"],
        },
    ),
    MCPTool(
        name="list_profiles",
        description="List profiles for the current principal",
        input_schema={"type": "object", "properties": {"limit": {"type": "integer"}}},
    ),
    MCPTool(
        name="get_profile",
        description="Get profile metadata by name",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    ),
    MCPTool(
        name="publish_profile",
        description="Create or replace a profile bundle",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "is_default": {"type": "boolean"},
                "manifest": {"type": "array"},
                "tarball_b64": {"type": "string"},
                "expected_sha256": {"type": "string"},
            },
            "required": ["name", "tarball_b64"],
        },
    ),
    MCPTool(
        name="download_profile",
        description="Download profile bundle by name",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}, "if_sha256": {"type": "string"}},
            "required": ["name"],
        },
    ),
    MCPTool(
        name="activate_profile",
        description="Set profile as default",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    ),
    MCPTool(
        name="profile_status",
        description="Read current profile sha and optional expected-sha match",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}, "expected_sha256": {"type": "string"}},
            "required": ["name"],
        },
    ),
    MCPTool(
        name="pin_profile_version",
        description="Assert profile sha matches a pinned version",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}, "sha256": {"type": "string"}},
            "required": ["name", "sha256"],
        },
    ),
]


def model_to_dict(model):
    if isinstance(model, Agent):
        return {
            "id": model.id,
            "name": model.name,
            "capabilities": model.capabilities,
            "status": model.status,
            "metadata": model.agent_metadata,
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        }
    table = getattr(model, "__table__", None)
    if table is not None:
        return {col.name: getattr(model, col.name) for col in table.columns}
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _profile_to_dict(profile: UserProfile) -> dict:
    return {
        "id": profile.id,
        "name": profile.name,
        "owner_subject": profile.owner_subject,
        "description": profile.description,
        "is_default": profile.is_default,
        "manifest": json.loads(profile.manifest_json or "[]"),
        "sha256": profile.sha256,
        "size_bytes": profile.size_bytes,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def task_to_public_dict(task: Task) -> dict:
    data = model_to_dict(task)
    public_id = task.public_id or str(task.id)
    data["internal_id"] = task.id
    data["id"] = public_id
    data["public_id"] = public_id
    return data


def _mutation_result_with_ledger(
    *,
    session,
    mission_id: str | None,
    payload: dict,
    approval_trace: dict | None = None,
) -> dict:
    pending_count = 0
    if mission_id is not None:
        pending_count = len(list_pending_ledger_events(session=session, mission_id=mission_id, limit=500))
    payload["ledger_pending_count"] = pending_count
    payload["ledger_publish_required"] = True
    payload["ledger_publish_hint"] = "Run publish_pending_ledger_events to persist pending changes to Git."
    if isinstance(approval_trace, dict):
        approval_request_id = approval_trace.get("approval_request_id")
        if approval_request_id:
            payload["approval_request_id"] = approval_request_id
    return payload


def _mcp_ok(*, result: dict, request_id: str) -> MCPResponse:
    payload = dict(result or {})
    payload.setdefault("request_id", request_id)
    return MCPResponse(ok=True, result=payload)


def _mcp_error(*, request_id: str, error: str, error_code: str = "invalid_request", result: dict | None = None) -> MCPResponse:
    payload = dict(result or {})
    payload.setdefault("error_code", error_code)
    payload.setdefault("request_id", request_id)
    return MCPResponse(ok=False, error=f"{error} [request_id={request_id}]", result=payload)


@router.get("/tools", response_model=list[MCPTool])
def list_tools():
    return TOOLS


@router.get("/health")
def mcp_health():
    return {
        "status": "ok",
        "tools_count": len(TOOLS),
        "auth_mode": os.getenv("AUTH_MODE", "auto"),
        "token_configured": bool(os.getenv("MC_TOKEN")),
        "oidc_configured": bool(os.getenv("OIDC_ISSUER_URL") and os.getenv("OIDC_AUDIENCE")),
    }


@router.post("/call", response_model=MCPResponse)
def call_tool(payload: MCPCall, request: Request, response: Response):
    tool = payload.tool
    args = payload.args or {}
    actor_subject = actor_subject_from_request(request)
    source = request_source(request)
    request_id = getattr(request.state, "request_id", None) or request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = request_id
    request.state.mcp_tool = tool
    response.headers["x-request-id"] = request_id
    header_agent_id = str(request.headers.get("x-mc-agent-id") or request.headers.get("x-agent-id") or "")
    header_runtime_session_id = str(
        request.headers.get("x-mc-runtime-session-id") or request.headers.get("x-mc-instance-id") or ""
    )

    with get_session() as session:
        approval_context = extract_approval_context(
            request,
            approval_token=args.get("approval_token"),
            approval_context=args.get("approval_context"),
        )
        approval_trace = approval_trace_from_context(approval_context)
        if approval_trace:
            if approval_trace.get("approval_request_id"):
                response.headers["x-approval-request-id"] = str(approval_trace["approval_request_id"])
            if approval_trace.get("approval_nonce"):
                response.headers["x-approval-nonce"] = str(approval_trace["approval_nonce"])

        def ensure_action(action_name: str):
            try:
                require_policy_action(
                    session=session,
                    action=action_name,
                    request=request,
                    approval_context=approval_context,
                    channel="mcp",
                )
            except HTTPException as exc:
                return MCPResponse(
                    ok=False,
                    error=f"{exc.detail} [request_id={request_id}]",
                    result={"error_code": "policy_denied", "request_id": request_id},
                )
            return None

        if tool == "search_klusters":
            query = (args.get("query") or "").lower()
            admin = is_platform_admin(request)
            readable_ids = readable_mission_ids_for_request(session=session, request=request)
            klusters = session.exec(select(Kluster)).all()
            matches = [
                c for c in klusters if query in c.name.lower() or query in (c.tags or "").lower()
            ]
            if not admin:
                matches = [c for c in matches if c.mission_id in readable_ids]
            return MCPResponse(
                ok=True,
                result={"klusters": [model_to_dict(c) for c in matches], "request_id": request_id},
            )

        if tool == "search_tasks":
            query = args.get("query") or ""
            limit = int(args.get("limit") or 5)
            raw_matches = query_tasks(query, limit=limit)
            public_id_by_internal: dict[int, str] = {}
            if raw_matches:
                task_ids_all = [int(item["id"]) for item in raw_matches if item.get("id") is not None]
                task_rows_all = session.exec(select(Task).where(Task.id.in_(task_ids_all))).all() if task_ids_all else []
                for row in task_rows_all:
                    ensure_task_public_id(session, row)
                    if row.id is not None:
                        public_id_by_internal[row.id] = row.public_id or str(row.id)
            if is_platform_admin(request):
                matches = raw_matches
            else:
                readable_ids = readable_mission_ids_for_request(session=session, request=request)
                if not readable_ids:
                    matches = []
                else:
                    task_ids = [int(item["id"]) for item in raw_matches if item.get("id") is not None]
                    task_rows = session.exec(select(Task).where(Task.id.in_(task_ids))).all() if task_ids else []
                    kluster_ids = {row.kluster_id for row in task_rows if row.kluster_id}
                    kluster_rows = (
                        session.exec(select(Kluster).where(Kluster.id.in_(kluster_ids))).all()
                        if kluster_ids
                        else []
                    )
                    mission_by_kluster = {k.id: k.mission_id for k in kluster_rows}
                    allowed_task_ids = {
                        row.id
                        for row in task_rows
                        if row.id is not None and mission_by_kluster.get(row.kluster_id) in readable_ids
                    }
                    matches = [item for item in raw_matches if int(item["id"]) in allowed_task_ids]
            for item in matches:
                internal_id = int(item["id"])
                item["internal_id"] = internal_id
                item["id"] = public_id_by_internal.get(internal_id, str(internal_id))
            return MCPResponse(ok=True, result={"tasks": matches, "request_id": request_id})

        if tool == "read_doc":
            doc_id = int(args.get("doc_id"))
            doc = session.get(Doc, doc_id)
            if not doc:
                return MCPResponse(
                    ok=False,
                    error=f"Doc not found [request_id={request_id}]",
                    result={"error_code": "not_found", "request_id": request_id},
                )
            kluster = session.get(Kluster, doc.kluster_id)
            if not kluster or not kluster.mission_id:
                return MCPResponse(
                    ok=False,
                    error=f"Doc is not linked to a mission [request_id={request_id}]",
                    result={"error_code": "forbidden", "request_id": request_id},
                )
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=kluster.mission_id)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=f"{exc.detail} [request_id={request_id}]")
            return MCPResponse(ok=True, result={"doc": model_to_dict(doc), "request_id": request_id})

        if tool == "create_doc":
            gated = ensure_action("doc.create")
            if gated:
                return gated
            payload_data = {
                "kluster_id": str(args.get("kluster_id") or ""),
                "title": str(args.get("title") or ""),
                "body": str(args.get("body") or ""),
                "doc_type": str(args.get("doc_type") or "narrative"),
                "status": str(args.get("status") or "draft"),
                "provenance": str(args.get("provenance") or ""),
            }
            try:
                payload_data = enforce_schema_pack(
                    request=request,
                    entity_type="doc",
                    payload=payload_data,
                    operation="create",
                )
            except HTTPException as exc:
                return MCPResponse(ok=False, error=str(exc.detail))
            kluster = session.get(Kluster, payload_data["kluster_id"])
            if not kluster:
                return MCPResponse(ok=False, error="Kluster not found")
            if not kluster.mission_id:
                return MCPResponse(ok=False, error="Kluster is not linked to a mission")
            try:
                assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            doc = Doc(**payload_data)
            if object_storage_enabled():
                safe_title = (doc.title or "doc").strip().lower().replace(" ", "-")[:48] or "doc"
                key = build_scoped_key(
                    mission_id=kluster.mission_id,
                    kluster_id=doc.kluster_id,
                    entity="docs",
                    filename=f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}-{safe_title}.md",
                )
                body_bytes = doc.body.encode("utf-8")
                s3_uri, size_bytes = put_bytes(key=key, body=body_bytes, content_type="text/markdown; charset=utf-8")
                meta = {
                    "storage_backend": "s3",
                    "s3_uri": s3_uri,
                    "size_bytes": size_bytes,
                    "sha256": hashlib.sha256(body_bytes).hexdigest(),
                }
                existing = {}
                if doc.provenance:
                    try:
                        existing = json.loads(doc.provenance)
                    except Exception:
                        existing = {"note": doc.provenance}
                existing["object_storage"] = meta
                doc.provenance = json.dumps(existing, separators=(",", ":"))
            session.add(doc)
            session.commit()
            session.refresh(doc)
            try:
                index_doc(
                    doc.id,
                    f"{doc.title}\n{doc.body}",
                    {"kluster_id": doc.kluster_id, "type": doc.doc_type},
                )
            except Exception:
                pass
            enqueue_ledger_event(
                session=session,
                mission_id=kluster.mission_id,
                kluster_id=doc.kluster_id,
                entity_type="doc",
                entity_id=doc.id,
                action="create",
                before=None,
                after=doc.dict(),
                actor_subject=actor_subject,
                source=source,
            )
            return MCPResponse(
                ok=True,
                result=_mutation_result_with_ledger(
                    session=session,
                    mission_id=kluster.mission_id,
                    payload={"doc": model_to_dict(doc)},
                    approval_trace=approval_trace,
                ),
            )

        if tool == "create_artifact":
            gated = ensure_action("artifact.create")
            if gated:
                return gated
            raw_payload = {
                "kluster_id": str(args.get("kluster_id") or ""),
                "name": str(args.get("name") or ""),
                "artifact_type": str(args.get("artifact_type") or "file"),
                "uri": str(args.get("uri") or ""),
                "storage_backend": str(args.get("storage_backend") or "inline"),
                "content_sha256": str(args.get("content_sha256") or ""),
                "size_bytes": int(args.get("size_bytes") or 0),
                "mime_type": str(args.get("mime_type") or ""),
                "status": str(args.get("status") or "draft"),
                "provenance": str(args.get("provenance") or ""),
            }
            schema_payload = {
                "kluster_id": raw_payload["kluster_id"],
                "name": raw_payload["name"],
                "artifact_type": raw_payload["artifact_type"],
                "uri": raw_payload["uri"],
                "status": raw_payload["status"],
                "provenance": raw_payload["provenance"],
            }
            try:
                payload_data = enforce_schema_pack(
                    request=request,
                    entity_type="artifact",
                    payload=schema_payload,
                    operation="create",
                )
            except HTTPException as exc:
                return MCPResponse(ok=False, error=str(exc.detail))
            for key in ("storage_backend", "content_sha256", "size_bytes", "mime_type"):
                payload_data[key] = raw_payload.get(key)
            if payload_data["uri"].startswith("s3://") and not args.get("storage_backend"):
                payload_data["storage_backend"] = "s3"
            kluster = session.get(Kluster, payload_data["kluster_id"])
            if not kluster:
                return MCPResponse(ok=False, error="Kluster not found")
            if not kluster.mission_id:
                return MCPResponse(ok=False, error="Kluster is not linked to a mission")
            try:
                assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            artifact = Artifact(**payload_data)
            if object_storage_enabled() and artifact.uri and not artifact.uri.startswith("s3://"):
                safe_name = (artifact.name or "artifact").strip().lower().replace(" ", "-")[:48] or "artifact"
                key = build_scoped_key(
                    mission_id=kluster.mission_id,
                    kluster_id=artifact.kluster_id,
                    entity="artifacts",
                    filename=f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}-{safe_name}.json",
                )
                blob = json.dumps(
                    {
                        "name": artifact.name,
                        "artifact_type": artifact.artifact_type,
                        "source_uri": artifact.uri,
                        "status": artifact.status,
                        "provenance": artifact.provenance,
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
                persisted_uri, size_bytes = put_bytes(key=key, body=blob, content_type="application/json")
                artifact.uri = persisted_uri
                artifact.storage_backend = "s3"
                artifact.size_bytes = size_bytes
                artifact.mime_type = "application/json"
                artifact.content_sha256 = hashlib.sha256(blob).hexdigest()
            session.add(artifact)
            session.commit()
            session.refresh(artifact)
            enqueue_ledger_event(
                session=session,
                mission_id=kluster.mission_id,
                kluster_id=artifact.kluster_id,
                entity_type="artifact",
                entity_id=artifact.id,
                action="create",
                before=None,
                after=artifact.dict(),
                actor_subject=actor_subject,
                source=source,
            )
            return MCPResponse(
                ok=True,
                result=_mutation_result_with_ledger(
                    session=session,
                    mission_id=kluster.mission_id,
                    payload={"artifact": model_to_dict(artifact)},
                    approval_trace=approval_trace,
                ),
            )

        if tool == "get_artifact_download_url":
            artifact_id = int(args.get("artifact_id") or 0)
            if artifact_id <= 0:
                return MCPResponse(ok=False, error="artifact_id is required")
            expires_seconds = int(args.get("expires_seconds") or 60)
            artifact = session.get(Artifact, artifact_id)
            if not artifact:
                return MCPResponse(ok=False, error="Artifact not found")
            kluster = session.get(Kluster, artifact.kluster_id)
            if not kluster or not kluster.mission_id:
                return MCPResponse(ok=False, error="Artifact is not linked to a mission")
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=kluster.mission_id)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            if artifact.storage_backend != "s3" or not artifact.uri.startswith("s3://"):
                return MCPResponse(ok=False, error="Artifact does not have retrievable S3-backed content")
            try:
                expected = scoped_prefix(mission_id=kluster.mission_id, kluster_id=artifact.kluster_id)
                download_url = presign_get_uri(
                    artifact.uri,
                    expires_seconds=expires_seconds,
                    expected_prefix=expected,
                )
            except Exception as exc:
                return MCPResponse(ok=False, error=f"S3 presign failed: {exc}")
            ttl = max(1, min(expires_seconds, 3600))
            return MCPResponse(
                ok=True,
                result={
                    "artifact_id": artifact.id,
                    "uri": artifact.uri,
                    "expires_seconds": ttl,
                    "download_url": download_url,
                    "request_id": request_id,
                },
            )

        if tool == "load_kluster_workspace":
            gated = ensure_action("workspace.load")
            if gated:
                return gated
            kluster_id = str(args.get("kluster_id") or "")
            if not kluster_id:
                return MCPResponse(ok=False, error="kluster_id is required")
            kluster = session.get(Kluster, kluster_id)
            if not kluster:
                return MCPResponse(ok=False, error="Kluster not found")
            if not kluster.mission_id:
                return MCPResponse(ok=False, error="Kluster is not linked to a mission")
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=kluster.mission_id)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            lease_seconds = int(args.get("lease_seconds") or 900)
            workspace_label = str(args.get("workspace_label") or header_runtime_session_id or "")
            agent_id = str(args.get("agent_id") or header_agent_id or "")
            lease, snapshot = create_workspace_lease(
                session=session,
                mission_id=kluster.mission_id,
                kluster_id=kluster.id,
                actor_subject=actor_subject,
                agent_id=agent_id,
                workspace_label=workspace_label,
                lease_seconds=lease_seconds,
            )
            return MCPResponse(
                ok=True,
                result={
                    "lease": {
                        "id": lease.id,
                        "mission_id": lease.mission_id,
                        "kluster_id": lease.kluster_id,
                        "actor_subject": lease.actor_subject,
                        "agent_id": lease.agent_id,
                        "workspace_label": lease.workspace_label,
                        "status": lease.status,
                        "lease_seconds": lease.lease_seconds,
                        "last_heartbeat_at": lease.last_heartbeat_at,
                        "expires_at": lease.expires_at,
                    },
                    "workspace_snapshot": snapshot,
                    "request_id": request_id,
                },
            )

        if tool == "heartbeat_workspace_lease":
            gated = ensure_action("workspace.heartbeat")
            if gated:
                return gated
            lease_id = str(args.get("lease_id") or "")
            if not lease_id:
                return MCPResponse(ok=False, error="lease_id is required")
            lease = get_lease(session, lease_id)
            if not lease:
                return MCPResponse(ok=False, error="Workspace lease not found")
            try:
                assert_lease_owner_or_admin(lease, actor_subject, is_admin=is_platform_admin(request))
                assert_mission_reader_or_admin(session=session, request=request, mission_id=lease.mission_id)
                lease = heartbeat_workspace_lease(session=session, lease=lease)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            return MCPResponse(
                ok=True,
                result={
                    "lease": {
                        "id": lease.id,
                        "status": lease.status,
                        "last_heartbeat_at": lease.last_heartbeat_at,
                        "expires_at": lease.expires_at,
                    },
                    "request_id": request_id,
                },
            )

        if tool == "fetch_workspace_artifact":
            gated = ensure_action("workspace.fetch_artifact")
            if gated:
                return gated
            lease_id = str(args.get("lease_id") or "")
            artifact_id = int(args.get("artifact_id") or 0)
            mode = str(args.get("mode") or "download_url").strip().lower()
            if not lease_id:
                return MCPResponse(ok=False, error="lease_id is required")
            if artifact_id <= 0:
                return MCPResponse(ok=False, error="artifact_id is required")
            lease = get_lease(session, lease_id)
            if not lease:
                return MCPResponse(ok=False, error="Workspace lease not found")
            try:
                assert_lease_owner_or_admin(lease, actor_subject, is_admin=is_platform_admin(request))
                assert_mission_reader_or_admin(session=session, request=request, mission_id=lease.mission_id)
                if lease.status != "active":
                    raise HTTPException(status_code=409, detail="Workspace lease is not active")
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            artifact = session.get(Artifact, artifact_id)
            if not artifact:
                return MCPResponse(ok=False, error="Artifact not found")
            if artifact.kluster_id != lease.kluster_id:
                return MCPResponse(ok=False, error="Artifact is outside lease kluster scope")
            if artifact.storage_backend != "s3" or not artifact.uri.startswith("s3://"):
                return MCPResponse(ok=False, error="Artifact is not S3-backed and cannot be lazily fetched")
            expected = scoped_prefix(mission_id=lease.mission_id, kluster_id=lease.kluster_id)
            if mode == "content":
                try:
                    body, content_type = get_bytes_from_uri(artifact.uri, expected_prefix=expected)
                except Exception as exc:
                    return MCPResponse(ok=False, error=f"S3 fetch failed: {exc}")
                return MCPResponse(
                    ok=True,
                    result={
                        "artifact_id": artifact.id,
                        "mode": "content",
                        "mime_type": content_type,
                        "size_bytes": len(body),
                        "content_b64": base64.b64encode(body).decode("ascii"),
                        "request_id": request_id,
                    },
                )
            expires_seconds = int(args.get("expires_seconds") or 60)
            try:
                download_url = presign_get_uri(
                    artifact.uri,
                    expires_seconds=expires_seconds,
                    expected_prefix=expected,
                )
            except Exception as exc:
                return MCPResponse(ok=False, error=f"S3 presign failed: {exc}")
            ttl = max(1, min(expires_seconds, 3600))
            return MCPResponse(
                ok=True,
                result={
                    "artifact_id": artifact.id,
                    "mode": "download_url",
                    "uri": artifact.uri,
                    "expires_seconds": ttl,
                    "download_url": download_url,
                    "request_id": request_id,
                },
            )

        if tool == "commit_kluster_workspace":
            gated = ensure_action("workspace.commit")
            if gated:
                return gated
            lease_id = str(args.get("lease_id") or "")
            if not lease_id:
                return MCPResponse(ok=False, error="lease_id is required")
            changes = args.get("change_set")
            if not isinstance(changes, list) or not changes:
                return MCPResponse(ok=False, error="change_set must be a non-empty array")
            lease = get_lease(session, lease_id)
            if not lease:
                return MCPResponse(ok=False, error="Workspace lease not found")
            try:
                assert_lease_owner_or_admin(lease, actor_subject, is_admin=is_platform_admin(request))
                assert_mission_writer_or_admin(session=session, request=request, mission_id=lease.mission_id)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            try:
                result = commit_workspace_changes(
                    session=session,
                    lease=lease,
                    changes=changes,
                    request=request,
                    actor_subject=actor_subject,
                    source=source,
                )
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            if not result.get("ok"):
                return MCPResponse(
                    ok=False,
                    error="workspace_conflicts_detected",
                    result={"conflicts": result.get("conflicts", []), "request_id": request_id},
                )
            return MCPResponse(
                ok=True,
                result=_mutation_result_with_ledger(
                    session=session,
                    mission_id=lease.mission_id,
                    payload={
                        "applied_count": result.get("applied_count", 0),
                        "applied": result.get("applied", []),
                        "workspace_snapshot": result.get("workspace_snapshot", {}),
                        "request_id": request_id,
                    },
                    approval_trace=approval_trace,
                ),
            )

        if tool == "release_kluster_workspace":
            gated = ensure_action("workspace.release")
            if gated:
                return gated
            lease_id = str(args.get("lease_id") or "")
            reason = str(args.get("reason") or "")
            if not lease_id:
                return MCPResponse(ok=False, error="lease_id is required")
            lease = get_lease(session, lease_id)
            if not lease:
                return MCPResponse(ok=False, error="Workspace lease not found")
            try:
                assert_lease_owner_or_admin(lease, actor_subject, is_admin=is_platform_admin(request))
                assert_mission_reader_or_admin(session=session, request=request, mission_id=lease.mission_id)
                lease = release_workspace_lease(session=session, lease=lease, reason=reason)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            return MCPResponse(
                ok=True,
                result={
                    "lease": {
                        "id": lease.id,
                        "status": lease.status,
                        "release_reason": lease.release_reason,
                        "released_at": lease.released_at,
                    },
                    "request_id": request_id,
                },
            )

        if tool == "create_task":
            gated = ensure_action("task.create")
            if gated:
                return gated
            kluster_id = str(args.get("kluster_id"))
            title = args.get("title") or ""
            description = args.get("description") or ""
            owner = args.get("owner") or ""
            try:
                task_payload = enforce_schema_pack(
                    request=request,
                    entity_type="task",
                    payload={
                        "kluster_id": kluster_id,
                        "title": title,
                        "description": description,
                        "owner": owner,
                    },
                    operation="create",
                )
            except HTTPException as exc:
                return MCPResponse(ok=False, error=str(exc.detail))
            kluster = session.get(Kluster, kluster_id)
            if not kluster:
                return MCPResponse(ok=False, error="Kluster not found")
            if not kluster.mission_id:
                return MCPResponse(ok=False, error="Kluster is not linked to a mission")
            try:
                assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            task = Task(
                kluster_id=task_payload["kluster_id"],
                title=task_payload["title"],
                description=task_payload.get("description", ""),
                owner=task_payload.get("owner", ""),
            )
            session.add(task)
            session.commit()
            session.refresh(task)
            ensure_task_public_id(session, task)
            enqueue_ledger_event(
                session=session,
                mission_id=kluster.mission_id,
                kluster_id=task.kluster_id,
                entity_type="task",
                entity_id=task.public_id or str(task.id),
                action="create",
                before=None,
                after=task.dict(),
                actor_subject=actor_subject,
                source=source,
            )
            return MCPResponse(
                ok=True,
                result=_mutation_result_with_ledger(
                    session=session,
                    mission_id=kluster.mission_id,
                    payload={"task": task_to_public_dict(task)},
                    approval_trace=approval_trace,
                ),
            )

        if tool == "update_task":
            gated = ensure_action("task.update")
            if gated:
                return gated
            task_ref = str(args.get("task_id") or "")
            task = resolve_task_by_ref(session=session, task_ref=task_ref)
            if not task:
                return MCPResponse(ok=False, error="Task not found")
            before = task.dict()
            kluster = session.get(Kluster, task.kluster_id)
            if kluster and kluster.mission_id:
                try:
                    assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
                except HTTPException as exc:
                    return MCPResponse(ok=False, error=exc.detail)
            allowed_fields = {
                "title",
                "description",
                "status",
                "owner",
                "contributors",
                "dependencies",
                "definition_of_done",
                "related_artifacts",
            }
            update_payload = {
                key: args.get(key)
                for key in allowed_fields
                if key in args and args.get(key) is not None
            }
            try:
                update_payload = enforce_schema_pack(
                    request=request,
                    entity_type="task",
                    payload=update_payload,
                    operation="update",
                )
            except HTTPException as exc:
                return MCPResponse(ok=False, error=str(exc.detail))
            for key in allowed_fields:
                if key in update_payload:
                    setattr(task, key, update_payload.get(key))
            task.updated_at = datetime.utcnow()
            session.add(task)
            session.commit()
            session.refresh(task)

            index_task(
                task.id,
                task_text(task),
                {"kluster_id": task.kluster_id, "status": task.status},
            )
            enqueue_ledger_event(
                session=session,
                mission_id=kluster.mission_id if kluster else None,
                kluster_id=task.kluster_id,
                entity_type="task",
                entity_id=task.public_id or str(task.id),
                action="update",
                before=before,
                after=task.dict(),
                actor_subject=actor_subject,
                source=source,
            )
            return MCPResponse(
                ok=True,
                result=_mutation_result_with_ledger(
                    session=session,
                    mission_id=kluster.mission_id if kluster else None,
                    payload={"task": task_to_public_dict(task)},
                    approval_trace=approval_trace,
                ),
            )

        if tool == "delete_task":
            gated = ensure_action("task.delete")
            if gated:
                return gated
            task_ref = str(args.get("task_id") or "")
            task = resolve_task_by_ref(session=session, task_ref=task_ref)
            if not task:
                return MCPResponse(ok=False, error="Task not found")
            task_id = task.id
            kluster = session.get(Kluster, task.kluster_id)
            if not kluster or not kluster.mission_id:
                return MCPResponse(ok=False, error="Task kluster is not linked to a mission")
            try:
                assert_mission_owner_or_admin(session=session, request=request, mission_id=kluster.mission_id)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            before = task.dict()
            overlaps = session.exec(
                select(OverlapSuggestion).where(
                    (OverlapSuggestion.task_id == task_id) | (OverlapSuggestion.candidate_task_id == task_id)
                )
            ).all()
            for overlap in overlaps:
                session.delete(overlap)
            assignments = session.exec(select(TaskAssignment).where(TaskAssignment.task_id == task_id)).all()
            for assignment in assignments:
                session.delete(assignment)
            messages = session.exec(select(AgentMessage).where(AgentMessage.task_id == task_id)).all()
            for message in messages:
                message.task_id = None
                session.add(message)
            session.delete(task)
            session.commit()
            enqueue_ledger_event(
                session=session,
                mission_id=kluster.mission_id,
                kluster_id=kluster.id,
                entity_type="task",
                entity_id=task.public_id or str(task_id),
                action="delete",
                before=before,
                after=None,
                actor_subject=actor_subject,
                source=source,
            )
            return MCPResponse(
                ok=True,
                result=_mutation_result_with_ledger(
                    session=session,
                    mission_id=kluster.mission_id,
                    payload={"ok": True, "deleted_id": task.public_id or str(task_id)},
                    approval_trace=approval_trace,
                ),
            )

        if tool == "list_pending_ledger_events":
            mission_id_raw = args.get("mission_id")
            mission_id = str(mission_id_raw) if mission_id_raw is not None else None
            if not mission_id:
                return MCPResponse(ok=False, error="mission_id is required")
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            limit = int(args.get("limit") or 100)
            events = list_pending_ledger_events(session=session, mission_id=mission_id, limit=limit)
            return MCPResponse(ok=True, result={"events": [model_to_dict(e) for e in events]})

        if tool == "publish_pending_ledger_events":
            gated = ensure_action("mission.publish")
            if gated:
                return gated
            mission_id = str(args.get("mission_id"))
            if not subject_can_publish_mission(session=session, mission_id=mission_id, subject=actor_subject):
                return MCPResponse(
                    ok=False,
                    error="Forbidden: only mission owners or contributors can publish mission ledger events",
                )
            try:
                result = publish_pending_ledger_events(
                    session=session,
                    mission_id=mission_id,
                    actor_subject=actor_subject,
                )
                return MCPResponse(ok=True, result=result)
            except GitPublishError as exc:
                return MCPResponse(ok=False, error=str(exc))

        if tool == "get_entity_history":
            mission_id_raw = args.get("mission_id")
            mission_id = str(mission_id_raw) if mission_id_raw is not None else ""
            if not mission_id:
                return MCPResponse(ok=False, error="mission_id is required")
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            entity_type = args.get("entity_type") or ""
            entity_id = str(args.get("entity_id"))
            limit = int(args.get("limit") or 200)
            events = get_entity_history(
                session=session,
                mission_id=mission_id,
                entity_type=entity_type,
                entity_id=entity_id,
                limit=limit,
            )
            return MCPResponse(ok=True, result={"events": [model_to_dict(e) for e in events]})

        if tool == "list_tasks":
            kluster_id = str(args.get("kluster_id"))
            kluster = session.get(Kluster, kluster_id)
            if not kluster:
                return MCPResponse(ok=False, error="Kluster not found")
            if not kluster.mission_id:
                return MCPResponse(ok=False, error="Kluster is not linked to a mission")
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=kluster.mission_id)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            tasks = session.exec(select(Task).where(Task.kluster_id == kluster_id)).all()
            for task in tasks:
                ensure_task_public_id(session, task)
            return MCPResponse(ok=True, result={"tasks": [task_to_public_dict(t) for t in tasks]})

        if tool == "create_mission":
            gated = ensure_action("mission.create")
            if gated:
                return gated
            name = str(args.get("name") or "").strip()
            if not name:
                return _mcp_error(request_id=request_id, error="name is required")
            mission_payload = {
                "name": name,
                "description": str(args.get("description") or ""),
                "owners": str(args.get("owners") or ""),
                "contributors": str(args.get("contributors") or ""),
                "tags": str(args.get("tags") or ""),
                "visibility": str(args.get("visibility") or "internal"),
                "status": str(args.get("status") or "active"),
            }
            try:
                mission_payload = enforce_schema_pack(
                    request=request,
                    entity_type="mission",
                    payload=mission_payload,
                    operation="create",
                )
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="schema_validation_failed")
            if not mission_payload.get("owners") and actor_subject not in {"unknown", "token-client"}:
                mission_payload["owners"] = actor_subject
            if not _owner_list(mission_payload.get("owners")):
                return _mcp_error(request_id=request_id, error="owners must include at least one owner")
            existing = session.exec(select(Mission).where(Mission.name == name)).first()
            if existing:
                return _mcp_error(request_id=request_id, error="Mission name already exists", error_code="already_exists")
            mission = Mission(id=new_hash_id(), **mission_payload)
            while session.get(Mission, mission.id):
                mission.id = new_hash_id()
            ensure_mission_northstar(mission, actor=actor_subject)
            session.add(mission)
            session.commit()
            session.refresh(mission)
            enqueue_ledger_event(
                session=session,
                mission_id=mission.id,
                kluster_id=None,
                entity_type="mission",
                entity_id=mission.id,
                action="create",
                before=None,
                after=mission.dict(),
                actor_subject=actor_subject,
                source=source,
            )
            return _mcp_ok(
                request_id=request_id,
                result=_mutation_result_with_ledger(
                    session=session,
                    mission_id=mission.id,
                    payload={"mission": model_to_dict(mission)},
                    approval_trace=approval_trace,
                ),
            )

        if tool == "list_missions":
            readable_ids = readable_mission_ids_for_request(session=session, request=request)
            missions = session.exec(select(Mission).order_by(Mission.updated_at.desc())).all()
            if not readable_ids:
                missions = []
            else:
                missions = [m for m in missions if m.id in readable_ids]
            return _mcp_ok(request_id=request_id, result={"missions": [model_to_dict(m) for m in missions]})

        if tool == "create_kluster":
            gated = ensure_action("kluster.create")
            if gated:
                return gated
            mission_id = str(args.get("mission_id") or "").strip()
            if not mission_id:
                return _mcp_error(request_id=request_id, error="mission_id is required")
            create_payload = {
                key: args.get(key)
                for key in {"name", "description", "owners", "contributors", "tags", "status"}
                if key in args and args.get(key) is not None
            }
            if not str(create_payload.get("name") or "").strip():
                return _mcp_error(request_id=request_id, error="name is required")
            create_payload["mission_id"] = mission_id
            try:
                create_payload = enforce_schema_pack(
                    request=request,
                    entity_type="kluster",
                    payload=create_payload,
                    operation="create",
                )
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="schema_validation_failed")
            if not _owner_list(create_payload.get("owners")):
                return _mcp_error(request_id=request_id, error="owners must include at least one owner")
            mission = session.get(Mission, mission_id)
            if not mission:
                return _mcp_error(request_id=request_id, error="Mission not found", error_code="not_found")
            try:
                assert_mission_writer_or_admin(session=session, request=request, mission_id=mission_id)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            if create_payload.get("mission_id") not in {None, mission_id}:
                return _mcp_error(
                    request_id=request_id,
                    error="Payload mission_id must match route mission_id",
                )
            create_payload["mission_id"] = mission_id
            kluster = Kluster(id=new_hash_id(), **create_payload)
            while session.get(Kluster, kluster.id):
                kluster.id = new_hash_id()
            ensure_kluster_workstream(kluster, actor=actor_subject)
            session.add(kluster)
            session.commit()
            session.refresh(kluster)
            enqueue_ledger_event(
                session=session,
                mission_id=kluster.mission_id,
                kluster_id=kluster.id,
                entity_type="kluster",
                entity_id=kluster.id,
                action="create",
                before=None,
                after=kluster.dict(),
                actor_subject=actor_subject,
                source=source,
            )
            return _mcp_ok(
                request_id=request_id,
                result=_mutation_result_with_ledger(
                    session=session,
                    mission_id=kluster.mission_id,
                    payload={"kluster": model_to_dict(kluster)},
                    approval_trace=approval_trace,
                ),
            )

        if tool == "update_kluster":
            gated = ensure_action("kluster.update")
            if gated:
                return gated
            kluster_id = str(args.get("kluster_id"))
            kluster = session.get(Kluster, kluster_id)
            if not kluster:
                return MCPResponse(ok=False, error="Kluster not found")
            if kluster.mission_id:
                try:
                    assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
                except HTTPException as exc:
                    return MCPResponse(ok=False, error=exc.detail)
            before = kluster.dict()
            allowed_fields = {
                "name",
                "description",
                "owners",
                "contributors",
                "tags",
                "status",
            }
            if "mission_id" in args and args.get("mission_id") != kluster.mission_id:
                return MCPResponse(ok=False, error="Changing mission_id is not supported")
            update_payload = {
                key: args.get(key)
                for key in allowed_fields
                if key in args and args.get(key) is not None
            }
            try:
                update_payload = enforce_schema_pack(
                    request=request,
                    entity_type="kluster",
                    payload=update_payload,
                    operation="update",
                )
            except HTTPException as exc:
                return MCPResponse(ok=False, error=str(exc.detail))
            if "owners" in update_payload and not _owner_list(update_payload.get("owners")):
                return MCPResponse(ok=False, error="owners must include at least one owner")
            for key in allowed_fields:
                if key in update_payload:
                    setattr(kluster, key, update_payload.get(key))
            kluster.updated_at = datetime.utcnow()
            session.add(kluster)
            session.commit()
            session.refresh(kluster)
            enqueue_ledger_event(
                session=session,
                mission_id=kluster.mission_id,
                kluster_id=kluster.id,
                entity_type="kluster",
                entity_id=kluster.id,
                action="update",
                before=before,
                after=kluster.dict(),
                actor_subject=actor_subject,
                source=source,
            )
            return MCPResponse(
                ok=True,
                result=_mutation_result_with_ledger(
                    session=session,
                    mission_id=kluster.mission_id,
                    payload={"kluster": model_to_dict(kluster)},
                    approval_trace=approval_trace,
                ),
            )

        if tool == "delete_kluster":
            gated = ensure_action("kluster.delete")
            if gated:
                return gated
            kluster_id = str(args.get("kluster_id"))
            kluster = session.get(Kluster, kluster_id)
            if not kluster:
                return MCPResponse(ok=False, error="Kluster not found")
            if not kluster.mission_id:
                return MCPResponse(ok=False, error="Kluster is not linked to a mission")
            try:
                assert_mission_owner_or_admin(session=session, request=request, mission_id=kluster.mission_id)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            blockers = {
                "tasks": len(session.exec(select(Task.id).where(Task.kluster_id == kluster_id)).all()),
                "docs": len(session.exec(select(Doc.id).where(Doc.kluster_id == kluster_id)).all()),
                "artifacts": len(session.exec(select(Artifact.id).where(Artifact.kluster_id == kluster_id)).all()),
                "epics": len(session.exec(select(Epic.id).where(Epic.kluster_id == kluster_id)).all()),
                "ingestion_jobs": len(
                    session.exec(select(IngestionJob.id).where(IngestionJob.kluster_id == kluster_id)).all()
                ),
            }
            blocking = {k: v for k, v in blockers.items() if v}
            if blocking:
                return MCPResponse(ok=False, error=f"Kluster has linked entities: {blocking}")
            before = kluster.dict()
            session.delete(kluster)
            session.commit()
            enqueue_ledger_event(
                session=session,
                mission_id=kluster.mission_id,
                kluster_id=kluster.id,
                entity_type="kluster",
                entity_id=kluster.id,
                action="delete",
                before=before,
                after=None,
                actor_subject=actor_subject,
                source=source,
            )
            return MCPResponse(
                ok=True,
                result=_mutation_result_with_ledger(
                    session=session,
                    mission_id=kluster.mission_id,
                    payload={"ok": True, "deleted_id": kluster_id},
                    approval_trace=approval_trace,
                ),
            )

        if tool == "update_mission":
            gated = ensure_action("mission.update")
            if gated:
                return gated
            mission_id = str(args.get("mission_id"))
            mission = session.get(Mission, mission_id)
            if not mission:
                return MCPResponse(ok=False, error="Mission not found")
            try:
                assert_mission_writer_or_admin(session=session, request=request, mission_id=mission_id)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            before = mission.dict()
            allowed_fields = {
                "description",
                "owners",
                "contributors",
                "tags",
                "visibility",
                "status",
            }
            update_payload = {
                key: args.get(key)
                for key in allowed_fields
                if key in args and args.get(key) is not None
            }
            try:
                update_payload = enforce_schema_pack(
                    request=request,
                    entity_type="mission",
                    payload=update_payload,
                    operation="update",
                )
            except HTTPException as exc:
                return MCPResponse(ok=False, error=str(exc.detail))
            if "owners" in update_payload and not _owner_list(update_payload.get("owners")):
                return MCPResponse(ok=False, error="owners must include at least one owner")
            for key in allowed_fields:
                if key in update_payload:
                    setattr(mission, key, update_payload.get(key))
            mission.updated_at = datetime.utcnow()
            session.add(mission)
            session.commit()
            session.refresh(mission)
            enqueue_ledger_event(
                session=session,
                mission_id=mission.id,
                kluster_id=None,
                entity_type="mission",
                entity_id=str(mission.id),
                action="update",
                before=before,
                after=mission.dict(),
                actor_subject=actor_subject,
                source=source,
            )
            return MCPResponse(
                ok=True,
                result=_mutation_result_with_ledger(
                    session=session,
                    mission_id=mission.id,
                    payload={"mission": model_to_dict(mission)},
                    approval_trace=approval_trace,
                ),
            )

        if tool == "delete_mission":
            gated = ensure_action("mission.delete")
            if gated:
                return gated
            mission_id = str(args.get("mission_id"))
            mission = session.get(Mission, mission_id)
            if not mission:
                return MCPResponse(ok=False, error="Mission not found")
            try:
                assert_mission_owner_or_admin(session=session, request=request, mission_id=mission_id)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            linked_cluster = session.exec(select(Kluster).where(Kluster.mission_id == mission_id)).first()
            if linked_cluster:
                return MCPResponse(ok=False, error="Mission has linked klusters; move or delete klusters first")
            before = mission.dict()
            role_rows = session.exec(
                select(MissionRoleMembership).where(MissionRoleMembership.mission_id == mission_id)
            ).all()
            for row in role_rows:
                session.delete(row)
            session.delete(mission)
            session.commit()
            enqueue_ledger_event(
                session=session,
                mission_id=mission_id,
                kluster_id=None,
                entity_type="mission",
                entity_id=mission_id,
                action="delete",
                before=before,
                after=None,
                actor_subject=actor_subject,
                source=source,
            )
            return MCPResponse(ok=True, result={"ok": True, "deleted_id": mission_id})

        if tool == "update_doc":
            gated = ensure_action("doc.update")
            if gated:
                return gated
            doc_id = int(args.get("doc_id"))
            doc = session.get(Doc, doc_id)
            if not doc:
                return MCPResponse(ok=False, error="Doc not found")
            before = doc.dict()
            kluster = session.get(Kluster, doc.kluster_id)
            if kluster and kluster.mission_id:
                try:
                    assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
                except HTTPException as exc:
                    return MCPResponse(ok=False, error=exc.detail)
            allowed_fields = {"title", "body", "doc_type", "status", "provenance"}
            update_payload = {
                key: args.get(key)
                for key in allowed_fields
                if key in args and args.get(key) is not None
            }
            try:
                update_payload = enforce_schema_pack(
                    request=request,
                    entity_type="doc",
                    payload=update_payload,
                    operation="update",
                )
            except HTTPException as exc:
                return MCPResponse(ok=False, error=str(exc.detail))
            for key in allowed_fields:
                if key in update_payload:
                    setattr(doc, key, update_payload.get(key))
            doc.updated_at = datetime.utcnow()
            doc.version += 1
            session.add(doc)
            session.commit()
            session.refresh(doc)
            try:
                index_doc(
                    doc.id,
                    f"{doc.title}\n{doc.body}",
                    {"kluster_id": doc.kluster_id, "type": doc.doc_type},
                )
            except Exception:
                pass
            enqueue_ledger_event(
                session=session,
                mission_id=kluster.mission_id if kluster else None,
                kluster_id=doc.kluster_id,
                entity_type="doc",
                entity_id=doc.id,
                action="update",
                before=before,
                after=doc.dict(),
                actor_subject=actor_subject,
                source=source,
            )
            return MCPResponse(
                ok=True,
                result=_mutation_result_with_ledger(
                    session=session,
                    mission_id=kluster.mission_id if kluster else None,
                    payload={"doc": model_to_dict(doc)},
                    approval_trace=approval_trace,
                ),
            )

        if tool == "update_artifact":
            gated = ensure_action("artifact.update")
            if gated:
                return gated
            artifact_id = int(args.get("artifact_id"))
            artifact = session.get(Artifact, artifact_id)
            if not artifact:
                return MCPResponse(ok=False, error="Artifact not found")
            before = artifact.dict()
            kluster = session.get(Kluster, artifact.kluster_id)
            if kluster and kluster.mission_id:
                try:
                    assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
                except HTTPException as exc:
                    return MCPResponse(ok=False, error=exc.detail)
            allowed_fields = {
                "name",
                "artifact_type",
                "uri",
                "storage_backend",
                "content_sha256",
                "size_bytes",
                "mime_type",
                "status",
                "provenance",
            }
            update_payload = {
                key: args.get(key)
                for key in allowed_fields
                if key in args and args.get(key) is not None
            }
            schema_update_payload = {
                key: value
                for key, value in update_payload.items()
                if key in {"name", "artifact_type", "uri", "status", "provenance"}
            }
            try:
                schema_checked_payload = enforce_schema_pack(
                    request=request,
                    entity_type="artifact",
                    payload=schema_update_payload,
                    operation="update",
                )
            except HTTPException as exc:
                return MCPResponse(ok=False, error=str(exc.detail))
            for key, value in schema_checked_payload.items():
                update_payload[key] = value
            for key in allowed_fields:
                if key in update_payload:
                    setattr(artifact, key, update_payload.get(key))
            artifact.updated_at = datetime.utcnow()
            artifact.version += 1
            session.add(artifact)
            session.commit()
            session.refresh(artifact)
            enqueue_ledger_event(
                session=session,
                mission_id=kluster.mission_id if kluster else None,
                kluster_id=artifact.kluster_id,
                entity_type="artifact",
                entity_id=artifact.id,
                action="update",
                before=before,
                after=artifact.dict(),
                actor_subject=actor_subject,
                source=source,
            )
            return MCPResponse(
                ok=True,
                result=_mutation_result_with_ledger(
                    session=session,
                    mission_id=kluster.mission_id if kluster else None,
                    payload={"artifact": model_to_dict(artifact)},
                    approval_trace=approval_trace,
                ),
            )

        if tool == "get_overlap_suggestions":
            task_ref = str(args.get("task_id") or "")
            task = resolve_task_by_ref(session=session, task_ref=task_ref)
            if not task:
                return MCPResponse(ok=False, error="Task not found")
            suggestions = session.exec(
                select(OverlapSuggestion).where(OverlapSuggestion.task_id == task.id)
            ).all()
            return MCPResponse(
                ok=True, result={"overlaps": [model_to_dict(s) for s in suggestions]}
            )

        if tool == "register_agent":
            name = args.get("name") or ""
            capabilities = args.get("capabilities") or ""
            metadata = args.get("metadata") or ""
            agent = Agent(name=name, capabilities=capabilities, agent_metadata=metadata)
            session.add(agent)
            session.commit()
            session.refresh(agent)
            return MCPResponse(ok=True, result={"agent": model_to_dict(agent)})

        if tool == "list_agents":
            status = args.get("status")
            stmt = select(Agent)
            if status:
                stmt = stmt.where(Agent.status == status)
            agents = session.exec(stmt.order_by(Agent.updated_at.desc())).all()
            return MCPResponse(ok=True, result={"agents": [model_to_dict(a) for a in agents]})

        if tool == "get_agent":
            agent_id = int(args.get("agent_id"))
            agent = session.get(Agent, agent_id)
            if not agent:
                return MCPResponse(ok=False, error="Agent not found")
            return MCPResponse(ok=True, result={"agent": model_to_dict(agent)})

        if tool == "update_agent_status":
            agent_id = int(args.get("agent_id"))
            status = args.get("status") or "offline"
            agent = session.get(Agent, agent_id)
            if not agent:
                return MCPResponse(ok=False, error="Agent not found")
            agent.status = status
            agent.updated_at = datetime.utcnow()
            session.add(agent)
            session.commit()
            session.refresh(agent)
            return MCPResponse(ok=True, result={"agent": model_to_dict(agent)})

        if tool == "start_agent_session":
            agent_id = int(args.get("agent_id"))
            context = args.get("context") or ""
            agent = session.get(Agent, agent_id)
            if not agent:
                return MCPResponse(ok=False, error="Agent not found")
            session_obj = AgentSession(agent_id=agent_id, context=context)
            agent.status = "online"
            session.add(session_obj)
            session.add(agent)
            session.commit()
            session.refresh(session_obj)
            return MCPResponse(ok=True, result={"session": model_to_dict(session_obj)})

        if tool == "end_agent_session":
            agent_id = int(args.get("agent_id"))
            session_id = int(args.get("session_id"))
            agent = session.get(Agent, agent_id)
            if not agent:
                return MCPResponse(ok=False, error="Agent not found")
            session_obj = session.get(AgentSession, session_id)
            if not session_obj or session_obj.agent_id != agent_id:
                return MCPResponse(ok=False, error="Session not found")
            session_obj.ended_at = datetime.utcnow()
            agent.status = "offline"
            session.add(session_obj)
            session.add(agent)
            session.commit()
            session.refresh(session_obj)
            return MCPResponse(ok=True, result={"session": model_to_dict(session_obj)})

        if tool == "claim_task":
            task_ref = str(args.get("task_id") or "")
            agent_id = int(args.get("agent_id"))
            agent = session.get(Agent, agent_id)
            if not agent:
                return MCPResponse(ok=False, error="Agent not found")
            task = resolve_task_by_ref(session=session, task_ref=task_ref)
            if not task:
                return MCPResponse(ok=False, error="Task not found")
            task_id = task.id
            assignment = TaskAssignment(task_id=task_id, agent_id=agent_id, status="claimed")
            session.add(assignment)
            session.commit()
            session.refresh(assignment)
            return MCPResponse(
                ok=True,
                result={
                    "assignment": model_to_dict(assignment),
                    "task_id": task.public_id or str(task.id),
                },
            )

        if tool == "release_task":
            assignment_id = int(args.get("assignment_id"))
            assignment = session.get(TaskAssignment, assignment_id)
            if not assignment:
                return MCPResponse(ok=False, error="Assignment not found")
            assignment.status = "available"
            assignment.updated_at = datetime.utcnow()
            session.add(assignment)
            session.commit()
            session.refresh(assignment)
            return MCPResponse(ok=True, result={"assignment": model_to_dict(assignment)})

        if tool == "list_task_assignments":
            agent_id = args.get("agent_id")
            task_ref = args.get("task_id")
            status = args.get("status")
            stmt = select(TaskAssignment)
            if agent_id is not None:
                stmt = stmt.where(TaskAssignment.agent_id == int(agent_id))
            if task_ref is not None:
                task = resolve_task_by_ref(session=session, task_ref=str(task_ref))
                if not task:
                    return MCPResponse(ok=True, result={"assignments": []})
                stmt = stmt.where(TaskAssignment.task_id == int(task.id))
            if status:
                stmt = stmt.where(TaskAssignment.status == status)
            assignments = session.exec(stmt.order_by(TaskAssignment.updated_at.desc())).all()
            return MCPResponse(ok=True, result={"assignments": [model_to_dict(a) for a in assignments]})

        if tool == "resolve_skill_snapshot":
            gated = ensure_action("skills.snapshot.resolve")
            if gated:
                return gated
            mission_id = str(args.get("mission_id") or "")
            kluster_id = str(args.get("kluster_id") or "")
            if not mission_id:
                return MCPResponse(ok=False, error="mission_id is required")
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
                if kluster_id:
                    validate_kluster_scope(session=session, mission_id=mission_id, kluster_id=kluster_id)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            snapshot = resolve_effective_snapshot(session=session, mission_id=mission_id, kluster_id=kluster_id)
            return MCPResponse(
                ok=True,
                result={
                    "snapshot": {
                        "id": snapshot.id,
                        "mission_id": snapshot.mission_id,
                        "kluster_id": snapshot.kluster_id,
                        "effective_version": snapshot.effective_version,
                        "mission_bundle_id": snapshot.mission_bundle_id,
                        "kluster_bundle_id": snapshot.kluster_bundle_id,
                        "sha256": snapshot.sha256,
                        "size_bytes": snapshot.size_bytes,
                        "manifest": json.loads(snapshot.manifest_json or "{}"),
                    }
                },
            )

        if tool == "download_skill_snapshot":
            snapshot_id = str(args.get("snapshot_id") or "")
            if not snapshot_id:
                return MCPResponse(ok=False, error="snapshot_id is required")
            snapshot = session.get(SkillSnapshot, snapshot_id)
            if not snapshot:
                return MCPResponse(ok=False, error="Skill snapshot not found")
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=snapshot.mission_id)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            return MCPResponse(
                ok=True,
                result={
                    "snapshot": {
                        "id": snapshot.id,
                        "sha256": snapshot.sha256,
                        "size_bytes": snapshot.size_bytes,
                        "tarball_b64": snapshot.tarball_b64,
                        "manifest": json.loads(snapshot.manifest_json or "{}"),
                    }
                },
            )

        if tool == "get_skill_sync_status":
            mission_id = str(args.get("mission_id") or "")
            kluster_id = str(args.get("kluster_id") or "")
            agent_id = str(args.get("agent_id") or header_agent_id or "")
            if not mission_id:
                return MCPResponse(ok=False, error="mission_id is required")
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
                if kluster_id:
                    validate_kluster_scope(session=session, mission_id=mission_id, kluster_id=kluster_id)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            actor = actor_subject
            state = get_sync_state(
                session=session,
                actor_subject=actor,
                mission_id=mission_id,
                kluster_id=kluster_id,
                agent_id=agent_id,
            )
            if state is None:
                return MCPResponse(
                    ok=True,
                    result={
                        "status": {
                            "mission_id": mission_id,
                            "kluster_id": kluster_id,
                            "actor_subject": actor,
                            "agent_id": agent_id,
                            "last_snapshot_id": "",
                            "last_snapshot_sha256": "",
                            "local_overlay_sha256": "",
                            "degraded_offline": False,
                            "drift_flag": False,
                            "drift_details": {},
                            "last_sync_at": None,
                        }
                    },
                )
            return MCPResponse(
                ok=True,
                result={
                    "status": {
                        "mission_id": state.mission_id,
                        "kluster_id": state.kluster_id,
                        "actor_subject": state.actor_subject,
                        "agent_id": state.agent_id,
                        "last_snapshot_id": state.last_snapshot_id,
                        "last_snapshot_sha256": state.last_snapshot_sha256,
                        "local_overlay_sha256": state.local_overlay_sha256,
                        "degraded_offline": state.degraded_offline,
                        "drift_flag": state.drift_flag,
                        "drift_details": json.loads(state.drift_details_json or "{}"),
                        "last_sync_at": state.last_sync_at,
                    }
                },
            )

        if tool == "ack_skill_sync":
            mission_id = str(args.get("mission_id") or "")
            kluster_id = str(args.get("kluster_id") or "")
            agent_id = str(args.get("agent_id") or header_agent_id or "")
            if not mission_id:
                return MCPResponse(ok=False, error="mission_id is required")
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
                if kluster_id:
                    validate_kluster_scope(session=session, mission_id=mission_id, kluster_id=kluster_id)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            state = upsert_sync_state(
                session=session,
                actor_subject=actor_subject,
                mission_id=mission_id,
                kluster_id=kluster_id,
                agent_id=agent_id,
                snapshot_id=str(args.get("snapshot_id") or ""),
                snapshot_sha256=str(args.get("snapshot_sha256") or ""),
                local_overlay_sha256=str(args.get("local_overlay_sha256") or ""),
                degraded_offline=bool(args.get("degraded_offline") or False),
                drift_flag=bool(args.get("drift_flag") or False),
                drift_details=args.get("drift_details") or {},
            )
            return MCPResponse(
                ok=True,
                result={
                    "status": {
                        "mission_id": state.mission_id,
                        "kluster_id": state.kluster_id,
                        "actor_subject": state.actor_subject,
                        "agent_id": state.agent_id,
                        "last_snapshot_id": state.last_snapshot_id,
                        "last_snapshot_sha256": state.last_snapshot_sha256,
                        "local_overlay_sha256": state.local_overlay_sha256,
                        "degraded_offline": state.degraded_offline,
                        "drift_flag": state.drift_flag,
                        "drift_details": json.loads(state.drift_details_json or "{}"),
                        "last_sync_at": state.last_sync_at,
                    }
                },
            )

        if tool == "promote_local_skill_overlay":
            mission_id = str(args.get("mission_id") or "")
            kluster_id = str(args.get("kluster_id") or "")
            agent_id = str(args.get("agent_id") or "")
            local_overlay_sha256 = str(args.get("local_overlay_sha256") or "")
            if not mission_id:
                return MCPResponse(ok=False, error="mission_id is required")
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
                if kluster_id:
                    validate_kluster_scope(session=session, mission_id=mission_id, kluster_id=kluster_id)
            except HTTPException as exc:
                return MCPResponse(ok=False, error=exc.detail)
            existing_state = get_sync_state(
                session=session,
                actor_subject=actor_subject,
                mission_id=mission_id,
                kluster_id=kluster_id,
                agent_id=agent_id,
            )
            state = upsert_sync_state(
                session=session,
                actor_subject=actor_subject,
                mission_id=mission_id,
                kluster_id=kluster_id,
                agent_id=agent_id,
                snapshot_id=existing_state.last_snapshot_id if existing_state else "",
                snapshot_sha256=existing_state.last_snapshot_sha256 if existing_state else "",
                local_overlay_sha256=local_overlay_sha256,
                degraded_offline=False,
                drift_flag=False,
                drift_details={"promoted": True, "note": str(args.get("note") or "")},
            )
            return MCPResponse(
                ok=True,
                result={
                    "status": {
                        "mission_id": state.mission_id,
                        "kluster_id": state.kluster_id,
                        "actor_subject": state.actor_subject,
                        "agent_id": state.agent_id,
                        "local_overlay_sha256": state.local_overlay_sha256,
                        "drift_details": json.loads(state.drift_details_json or "{}"),
                        "last_sync_at": state.last_sync_at,
                    }
                },
            )

        if tool == "list_profiles":
            limit = max(1, min(int(args.get("limit") or 50), 200))
            profiles = session.exec(
                select(UserProfile)
                .where(UserProfile.owner_subject == actor_subject)
                .order_by(UserProfile.updated_at.desc())
                .limit(limit)
            ).all()
            return MCPResponse(ok=True, result={"profiles": [_profile_to_dict(p) for p in profiles]})

        if tool == "get_profile":
            name = str(args.get("name") or "").strip()
            if not name:
                return MCPResponse(ok=False, error="name is required")
            profile = session.exec(
                select(UserProfile)
                .where(UserProfile.owner_subject == actor_subject)
                .where(UserProfile.name == name)
            ).first()
            if not profile:
                return MCPResponse(ok=False, error="Profile not found")
            return MCPResponse(ok=True, result={"profile": _profile_to_dict(profile)})

        if tool == "publish_profile":
            name = str(args.get("name") or "").strip()
            tarball_b64 = str(args.get("tarball_b64") or "").strip()
            if not name or not tarball_b64:
                return MCPResponse(ok=False, error="name and tarball_b64 are required")
            expected_sha256 = str(args.get("expected_sha256") or "").strip()
            description = str(args.get("description") or "")
            is_default = bool(args.get("is_default") or False)
            manifest = args.get("manifest")
            if manifest is None:
                manifest = []
            if not isinstance(manifest, list):
                return MCPResponse(ok=False, error="manifest must be an array")
            try:
                raw = base64.b64decode(tarball_b64)
            except Exception:
                return MCPResponse(ok=False, error="tarball_b64 is not valid base64")
            computed_sha = hashlib.sha256(raw).hexdigest()
            profile = session.exec(
                select(UserProfile)
                .where(UserProfile.owner_subject == actor_subject)
                .where(UserProfile.name == name)
            ).first()
            if profile:
                if expected_sha256 and (profile.sha256 or "") != expected_sha256:
                    return MCPResponse(
                        ok=False,
                        error="profile_sha_mismatch",
                        result={
                            "expected_sha256": expected_sha256,
                            "current_sha256": profile.sha256 or "",
                            "name": name,
                        },
                    )
                profile.description = description
                profile.is_default = is_default
                profile.manifest_json = json.dumps(manifest)
                profile.tarball_b64 = tarball_b64
                profile.sha256 = computed_sha
                profile.size_bytes = len(raw)
                profile.updated_at = datetime.utcnow()
                session.add(profile)
            else:
                profile = UserProfile(
                    name=name,
                    owner_subject=actor_subject,
                    description=description,
                    is_default=is_default,
                    manifest_json=json.dumps(manifest),
                    tarball_b64=tarball_b64,
                    sha256=computed_sha,
                    size_bytes=len(raw),
                )
                session.add(profile)
            if is_default:
                others = session.exec(
                    select(UserProfile)
                    .where(UserProfile.owner_subject == actor_subject)
                    .where(UserProfile.name != name)
                    .where(UserProfile.is_default == True)  # noqa: E712
                ).all()
                for other in others:
                    other.is_default = False
                    other.updated_at = datetime.utcnow()
                    session.add(other)
            session.commit()
            session.refresh(profile)
            return MCPResponse(ok=True, result={"profile": _profile_to_dict(profile)})

        if tool == "download_profile":
            name = str(args.get("name") or "").strip()
            if not name:
                return MCPResponse(ok=False, error="name is required")
            if_sha256 = str(args.get("if_sha256") or "").strip()
            profile = session.exec(
                select(UserProfile)
                .where(UserProfile.owner_subject == actor_subject)
                .where(UserProfile.name == name)
            ).first()
            if not profile:
                return MCPResponse(ok=False, error="Profile not found")
            current_sha = (profile.sha256 or "").strip()
            if if_sha256 and if_sha256 != current_sha:
                return MCPResponse(
                    ok=False,
                    error="profile_sha_mismatch",
                    result={"expected_sha256": if_sha256, "current_sha256": current_sha, "name": name},
                )
            return MCPResponse(
                ok=True,
                result={
                    "profile": _profile_to_dict(profile),
                    "tarball_b64": profile.tarball_b64 or "",
                },
            )

        if tool == "activate_profile":
            name = str(args.get("name") or "").strip()
            if not name:
                return MCPResponse(ok=False, error="name is required")
            profile = session.exec(
                select(UserProfile)
                .where(UserProfile.owner_subject == actor_subject)
                .where(UserProfile.name == name)
            ).first()
            if not profile:
                return MCPResponse(ok=False, error="Profile not found")
            others = session.exec(
                select(UserProfile)
                .where(UserProfile.owner_subject == actor_subject)
                .where(UserProfile.id != profile.id)
                .where(UserProfile.is_default == True)  # noqa: E712
            ).all()
            for other in others:
                other.is_default = False
                other.updated_at = datetime.utcnow()
                session.add(other)
            profile.is_default = True
            profile.updated_at = datetime.utcnow()
            session.add(profile)
            session.commit()
            session.refresh(profile)
            return MCPResponse(ok=True, result={"profile": _profile_to_dict(profile)})

        if tool == "profile_status":
            name = str(args.get("name") or "").strip()
            if not name:
                return MCPResponse(ok=False, error="name is required")
            expected_sha256 = str(args.get("expected_sha256") or "").strip()
            profile = session.exec(
                select(UserProfile)
                .where(UserProfile.owner_subject == actor_subject)
                .where(UserProfile.name == name)
            ).first()
            if not profile:
                return MCPResponse(ok=False, error="Profile not found")
            remote_sha = (profile.sha256 or "").strip()
            return MCPResponse(
                ok=True,
                result={
                    "name": name,
                    "remote_sha256": remote_sha,
                    "expected_sha256": expected_sha256,
                    "matches_expected": (not expected_sha256) or (expected_sha256 == remote_sha),
                },
            )

        if tool == "pin_profile_version":
            name = str(args.get("name") or "").strip()
            pinned_sha = str(args.get("sha256") or "").strip()
            if not name or not pinned_sha:
                return MCPResponse(ok=False, error="name and sha256 are required")
            profile = session.exec(
                select(UserProfile)
                .where(UserProfile.owner_subject == actor_subject)
                .where(UserProfile.name == name)
            ).first()
            if not profile:
                return MCPResponse(ok=False, error="Profile not found")
            remote_sha = (profile.sha256 or "").strip()
            matches = remote_sha == pinned_sha
            return MCPResponse(
                ok=matches,
                error=None if matches else "profile_sha_mismatch",
                result={
                    "name": name,
                    "pinned_sha256": pinned_sha,
                    "remote_sha256": remote_sha,
                    "matches": matches,
                },
            )

    raise HTTPException(status_code=400, detail="Unknown tool")
