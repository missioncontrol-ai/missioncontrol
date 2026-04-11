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
    RepoConnection,
    RepoBinding,
    MissionPersistencePolicy,
    MissionPersistenceRoute,
    MeshTask,
    MeshAgent,
    MeshMessage,
    MeshProgressEvent,
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
from app.services.persistence_publish import (
    PublishRoutingError,
    get_publication_status,
    resolve_publish_plan,
)
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
                "content_b64": {"type": "string"},
                "storage_backend": {"type": "string"},
                "storage_class": {"type": "string"},
                "content_sha256": {"type": "string"},
                "size_bytes": {"type": "integer"},
                "mime_type": {"type": "string"},
                "external_pointer": {"type": "boolean"},
                "external_uri": {"type": "string"},
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
        name="claim_task",
        description="Atomically claim a proposed task. Sets status to in_progress and assigns ownership to the caller. Returns conflict if the task is already claimed by another agent.",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
    ),
    MCPTool(
        name="update_task",
        description="Update a task by id. Pass expected_status to guard against concurrent overwrites (returns conflict if current status differs).",
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
                "expected_status": {"type": "string", "description": "If provided, the update fails with conflict if the task's current status does not match."},
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
        name="list_repo_bindings",
        description="List configured repository bindings for current principal",
        input_schema={"type": "object", "properties": {}},
    ),
    MCPTool(
        name="provision_mission_persistence",
        description="Create/update connection, binding, and mission policy routes in one call",
        input_schema={
            "type": "object",
            "properties": {
                "mission_id": {"type": "string"},
                "connection": {"type": "object"},
                "binding": {"type": "object"},
                "default_binding_name": {"type": "string"},
                "routes": {"type": "array"},
            },
            "required": ["mission_id", "connection", "binding"],
        },
    ),
    MCPTool(
        name="resolve_publish_plan",
        description="Resolve publish route (binding/repo/branch/path) for an entity",
        input_schema={
            "type": "object",
            "properties": {
                "mission_id": {"type": "string"},
                "entity_kind": {"type": "string"},
                "event_kind": {"type": "string"},
                "entity_id": {"type": "string"},
            },
            "required": ["mission_id", "entity_kind", "event_kind", "entity_id"],
        },
    ),
    MCPTool(
        name="get_publication_status",
        description="List recent publication records",
        input_schema={
            "type": "object",
            "properties": {
                "mission_id": {"type": "string"},
                "limit": {"type": "integer"},
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
        name="delete_profile",
        description="Delete a profile by name",
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
    MCPTool(
        name="register_remote_target",
        description="Register a remote SSH or K8s host as a named target for agent launches.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short identifier (e.g. 'dev-box')"},
                "host": {"type": "string", "description": "Hostname or IP"},
                "user": {"type": "string", "description": "SSH username"},
                "port": {"type": "integer", "description": "SSH port (default: 22)"},
                "transport": {"type": "string", "enum": ["ssh", "k8s"]},
                "ssh_pubkey": {"type": "string", "description": "SSH public key of target host"},
                "key_fingerprint": {"type": "string"},
            },
            "required": ["name", "host", "transport"],
        },
    ),
    MCPTool(
        name="list_remote_targets",
        description="List registered remote targets for the current user.",
        input_schema={"type": "object", "properties": {}, "required": []},
    ),
    MCPTool(
        name="delete_remote_target",
        description="Delete a registered remote target by ID.",
        input_schema={
            "type": "object",
            "properties": {"target_id": {"type": "string"}},
            "required": ["target_id"],
        },
    ),
    MCPTool(
        name="create_remote_launch",
        description=(
            "Create a remote agent launch record and receive a scoped session token. "
            "Returns launch_id, session_token, target_host, target_user, and mc_base_url. "
            "After calling this: SSH to target_host and run: "
            "MC_TOKEN=<session_token> MC_BASE_URL=<mc_base_url> MC_LAUNCH_ID=<launch_id> mc launch <agent_kind>"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target_id": {"type": "string", "description": "Registered target ID"},
                "target_host": {"type": "string", "description": "Ad-hoc hostname (if no target_id)"},
                "target_user": {"type": "string"},
                "transport": {"type": "string", "enum": ["ssh", "k8s"]},
                "agent_kind": {"type": "string", "description": "Agent type: claude, codex, etc."},
                "agent_profile": {"type": "string"},
                "capability_scope": {"type": "string", "description": "Comma-separated capability scopes"},
                "ttl_hours": {"type": "integer", "description": "Token TTL in hours (default: 8)"},
            },
            "required": ["transport", "agent_kind"],
        },
    ),
    MCPTool(
        name="list_remote_launches",
        description="List remote agent launches, optionally filtered by status.",
        input_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string",
                           "description": "Filter: launching|running|heartbeat_lost|completed|failed"},
            },
            "required": [],
        },
    ),
    MCPTool(
        name="get_remote_launch",
        description="Get status, heartbeat age, and log tail for a specific remote launch.",
        input_schema={
            "type": "object",
            "properties": {"launch_id": {"type": "string"}},
            "required": ["launch_id"],
        },
    ),
    MCPTool(
        name="kill_remote_launch",
        description="Kill a running remote launch: revokes its session token and marks it failed.",
        input_schema={
            "type": "object",
            "properties": {"launch_id": {"type": "string"}},
            "required": ["launch_id"],
        },
    ),
    # --- Mesh work model tools ---
    MCPTool(
        name="submit_mesh_task",
        description="Create a task in a kluster (mesh work model)",
        input_schema={
            "type": "object",
            "properties": {
                "kluster_id": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "claim_policy": {"type": "string", "description": "assigned | first_claim | broadcast (default: first_claim)"},
                "priority": {"type": "integer"},
                "required_capabilities": {"type": "array", "items": {"type": "string"}},
                "depends_on": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["kluster_id", "title"],
        },
    ),
    MCPTool(
        name="list_mesh_tasks",
        description="List tasks in a kluster (mesh work model)",
        input_schema={
            "type": "object",
            "properties": {
                "kluster_id": {"type": "string"},
                "status": {"type": "string", "description": "Optional status filter"},
            },
            "required": ["kluster_id"],
        },
    ),
    MCPTool(
        name="get_mesh_task",
        description="Get a single mesh task by ID",
        input_schema={
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    ),
    MCPTool(
        name="claim_mesh_task",
        description="Claim a mesh task for an agent; returns claim_lease_id",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "agent_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
    ),
    MCPTool(
        name="heartbeat_mesh_task",
        description="Renew a mesh task lease to prevent expiry",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "claim_lease_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
    ),
    MCPTool(
        name="progress_mesh_task",
        description="Post a typed progress event for a mesh task",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "event_type": {"type": "string"},
                "phase": {"type": "string"},
                "step": {"type": "string"},
                "summary": {"type": "string"},
                "payload": {"type": "object"},
            },
            "required": ["task_id", "event_type"],
        },
    ),
    MCPTool(
        name="complete_mesh_task",
        description="Mark a mesh task as complete",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "claim_lease_id": {"type": "string"},
                "result_artifact_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
    ),
    MCPTool(
        name="fail_mesh_task",
        description="Mark a mesh task as failed",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "claim_lease_id": {"type": "string"},
                "error": {"type": "string"},
            },
            "required": ["task_id", "error"],
        },
    ),
    MCPTool(
        name="block_mesh_task",
        description="Mark a mesh task as blocked",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["task_id"],
        },
    ),
    MCPTool(
        name="unblock_mesh_task",
        description="Unblock a mesh task (set back to ready)",
        input_schema={
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    ),
    MCPTool(
        name="cancel_mesh_task",
        description="Cancel a mesh task",
        input_schema={
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    ),
    MCPTool(
        name="retry_mesh_task",
        description="Retry a failed or cancelled mesh task",
        input_schema={
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    ),
    MCPTool(
        name="enroll_mesh_agent",
        description="Enroll an agent in a mission (mesh work model)",
        input_schema={
            "type": "object",
            "properties": {
                "mission_id": {"type": "string"},
                "runtime_kind": {"type": "string"},
                "capabilities": {"type": "array", "items": {"type": "string"}},
                "labels": {"type": "object"},
            },
            "required": ["mission_id", "runtime_kind"],
        },
    ),
    MCPTool(
        name="list_mesh_agents",
        description="List agents enrolled in a mission",
        input_schema={
            "type": "object",
            "properties": {"mission_id": {"type": "string"}},
            "required": ["mission_id"],
        },
    ),
    MCPTool(
        name="send_mesh_message",
        description="Send a message in a kluster or mission channel",
        input_schema={
            "type": "object",
            "properties": {
                "kluster_id": {"type": "string"},
                "mission_id": {"type": "string"},
                "content": {"type": "string"},
                "message_type": {"type": "string", "description": "Default: info"},
            },
            "required": ["content"],
        },
    ),
    MCPTool(
        name="list_mesh_messages",
        description="List messages for an agent inbox",
        input_schema={
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "since_id": {"type": "integer"},
            },
            "required": ["agent_id"],
        },
    ),
    MCPTool(
        name="list_mission_packs",
        description="List mission packs owned by the current principal",
        input_schema={"type": "object", "properties": {}},
    ),
    MCPTool(
        name="export_mission_pack",
        description="Export a mission into a portable mission pack tarball",
        input_schema={
            "type": "object",
            "properties": {"mission_id": {"type": "string"}},
            "required": ["mission_id"],
        },
    ),
    MCPTool(
        name="install_mission_pack",
        description="Install a mission pack, creating mission + klusters + skills + budgets",
        input_schema={
            "type": "object",
            "properties": {
                "pack_id": {"type": "string"},
                "target_mission_id": {"type": "string"},
            },
            "required": ["pack_id"],
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
        "mirror_uri": profile.mirror_uri or "",
        "mirror_sha256": profile.mirror_sha256 or "",
        "mirror_size_bytes": profile.mirror_size_bytes or 0,
        "mirrored_at": profile.mirrored_at,
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
            return _mcp_ok(request_id=request_id, result={"tasks": matches})

        if tool == "read_doc":
            doc_id = int(args.get("doc_id"))
            doc = session.get(Doc, doc_id)
            if not doc:
                return _mcp_error(request_id=request_id, error="Doc not found", error_code="not_found")
            kluster = session.get(Kluster, doc.kluster_id)
            if not kluster or not kluster.mission_id:
                return _mcp_error(
                    request_id=request_id,
                    error="Doc is not linked to a mission",
                    error_code="forbidden",
                )
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=kluster.mission_id)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
            return _mcp_ok(request_id=request_id, result={"doc": model_to_dict(doc)})

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
                return _mcp_error(
                    request_id=request_id,
                    error=str(exc.detail),
                    error_code="schema_validation_failed",
                )
            kluster = session.get(Kluster, payload_data["kluster_id"])
            if not kluster:
                return _mcp_error(request_id=request_id, error="Kluster not found", error_code="not_found")
            if not kluster.mission_id:
                return _mcp_error(
                    request_id=request_id,
                    error="Kluster is not linked to a mission",
                    error_code="forbidden",
                )
            try:
                assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
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
            return _mcp_ok(
                request_id=request_id,
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
                "content_b64": str(args.get("content_b64") or ""),
                "storage_backend": str(args.get("storage_backend") or "inline"),
                "storage_class": str(args.get("storage_class") or ""),
                "content_sha256": str(args.get("content_sha256") or ""),
                "size_bytes": int(args.get("size_bytes") or 0),
                "mime_type": str(args.get("mime_type") or ""),
                "external_pointer": bool(args.get("external_pointer") or False),
                "external_uri": str(args.get("external_uri") or ""),
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
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="schema_validation_failed")
            for key in (
                "storage_backend",
                "storage_class",
                "content_sha256",
                "size_bytes",
                "mime_type",
                "content_b64",
                "external_pointer",
                "external_uri",
            ):
                payload_data[key] = raw_payload.get(key)
            if payload_data["uri"].startswith("s3://") and not args.get("storage_backend"):
                payload_data["storage_backend"] = "s3"
            kluster = session.get(Kluster, payload_data["kluster_id"])
            if not kluster:
                return _mcp_error(request_id=request_id, error="Kluster not found", error_code="not_found")
            if not kluster.mission_id:
                return _mcp_error(request_id=request_id, error="Kluster is not linked to a mission", error_code="forbidden")
            try:
                assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
            artifact = Artifact(**payload_data)
            content_b64 = str(artifact.content_b64 or "").strip()
            if content_b64 and artifact.external_pointer:
                return _mcp_error(
                    request_id=request_id,
                    error="content_b64 and external_pointer cannot both be set",
                    error_code="invalid_request",
                )
            if content_b64:
                try:
                    body = base64.b64decode(content_b64, validate=True)
                except Exception as exc:
                    return _mcp_error(request_id=request_id, error=f"Invalid content_b64: {exc}", error_code="invalid_request")
                mime_type = str(artifact.mime_type or "application/octet-stream")
                artifact.size_bytes = len(body)
                artifact.content_sha256 = hashlib.sha256(body).hexdigest()
                inline_threshold = 512 * 1024
                use_s3 = object_storage_enabled() and (len(body) > inline_threshold or mime_type.startswith("image/"))
                if use_s3:
                    safe_name = (artifact.name or "artifact").strip().lower().replace(" ", "-")[:48] or "artifact"
                    key = build_scoped_key(
                        mission_id=kluster.mission_id,
                        kluster_id=artifact.kluster_id,
                        entity="artifacts",
                        filename=f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}-{safe_name}",
                    )
                    persisted_uri, size_bytes = put_bytes(key=key, body=body, content_type=mime_type)
                    artifact.uri = persisted_uri
                    artifact.storage_backend = "s3"
                    artifact.storage_class = "s3_primary"
                    artifact.size_bytes = size_bytes
                    artifact.content_b64 = None
                    artifact.external_pointer = False
                    artifact.external_uri = ""
                else:
                    artifact.uri = artifact.uri or f"db-inline://artifacts/{artifact.content_sha256}"
                    artifact.storage_backend = "inline"
                    artifact.storage_class = "db_inline"
                    artifact.external_pointer = False
                    artifact.external_uri = ""
            elif artifact.external_pointer or artifact.external_uri:
                resolved_uri = (artifact.external_uri or artifact.uri or "").strip()
                if not resolved_uri:
                    return _mcp_error(
                        request_id=request_id,
                        error="external pointer mode requires external_uri or uri",
                        error_code="invalid_request",
                    )
                artifact.uri = resolved_uri
                artifact.external_pointer = True
                artifact.external_uri = resolved_uri
                artifact.storage_class = "external_pointer"
                artifact.storage_backend = artifact.storage_backend or "external"
                artifact.content_b64 = None
            elif object_storage_enabled() and artifact.uri and artifact.uri.startswith("s3://"):
                artifact.storage_backend = "s3"
                artifact.storage_class = artifact.storage_class or "s3_primary"
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
            return _mcp_ok(
                request_id=request_id,
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
                return _mcp_error(request_id=request_id, error="artifact_id is required")
            expires_seconds = int(args.get("expires_seconds") or 60)
            artifact = session.get(Artifact, artifact_id)
            if not artifact:
                return _mcp_error(request_id=request_id, error="Artifact not found", error_code="not_found")
            kluster = session.get(Kluster, artifact.kluster_id)
            if not kluster or not kluster.mission_id:
                return _mcp_error(request_id=request_id, error="Artifact is not linked to a mission", error_code="forbidden")
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=kluster.mission_id)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
            if artifact.storage_backend != "s3" or not artifact.uri.startswith("s3://"):
                return _mcp_error(
                    request_id=request_id,
                    error="Artifact does not have retrievable S3-backed content",
                    error_code="invalid_request",
                )
            try:
                expected = scoped_prefix(mission_id=kluster.mission_id, kluster_id=artifact.kluster_id)
                download_url = presign_get_uri(
                    artifact.uri,
                    expires_seconds=expires_seconds,
                    expected_prefix=expected,
                )
            except Exception as exc:
                return _mcp_error(request_id=request_id, error=f"S3 presign failed: {exc}", error_code="storage_error")
            ttl = max(1, min(expires_seconds, 3600))
            return _mcp_ok(
                request_id=request_id,
                result={
                    "artifact_id": artifact.id,
                    "uri": artifact.uri,
                    "expires_seconds": ttl,
                    "download_url": download_url,
                },
            )

        if tool == "load_kluster_workspace":
            gated = ensure_action("workspace.load")
            if gated:
                return gated
            kluster_id = str(args.get("kluster_id") or "")
            if not kluster_id:
                return _mcp_error(request_id=request_id, error="kluster_id is required")
            kluster = session.get(Kluster, kluster_id)
            if not kluster:
                return _mcp_error(request_id=request_id, error="Kluster not found", error_code="not_found")
            if not kluster.mission_id:
                return _mcp_error(request_id=request_id, error="Kluster is not linked to a mission", error_code="forbidden")
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=kluster.mission_id)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
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
            return _mcp_ok(
                request_id=request_id,
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
                },
            )

        if tool == "heartbeat_workspace_lease":
            gated = ensure_action("workspace.heartbeat")
            if gated:
                return gated
            lease_id = str(args.get("lease_id") or "")
            if not lease_id:
                return _mcp_error(request_id=request_id, error="lease_id is required")
            lease = get_lease(session, lease_id)
            if not lease:
                return _mcp_error(request_id=request_id, error="Workspace lease not found", error_code="not_found")
            try:
                assert_lease_owner_or_admin(lease, actor_subject, is_admin=is_platform_admin(request))
                assert_mission_reader_or_admin(session=session, request=request, mission_id=lease.mission_id)
                lease = heartbeat_workspace_lease(session=session, lease=lease)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
            return _mcp_ok(
                request_id=request_id,
                result={
                    "lease": {
                        "id": lease.id,
                        "status": lease.status,
                        "last_heartbeat_at": lease.last_heartbeat_at,
                        "expires_at": lease.expires_at,
                    },
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
                return _mcp_error(request_id=request_id, error="lease_id is required")
            if artifact_id <= 0:
                return _mcp_error(request_id=request_id, error="artifact_id is required")
            lease = get_lease(session, lease_id)
            if not lease:
                return _mcp_error(request_id=request_id, error="Workspace lease not found", error_code="not_found")
            try:
                assert_lease_owner_or_admin(lease, actor_subject, is_admin=is_platform_admin(request))
                assert_mission_reader_or_admin(session=session, request=request, mission_id=lease.mission_id)
                if lease.status != "active":
                    raise HTTPException(status_code=409, detail="Workspace lease is not active")
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
            artifact = session.get(Artifact, artifact_id)
            if not artifact:
                return _mcp_error(request_id=request_id, error="Artifact not found", error_code="not_found")
            if artifact.kluster_id != lease.kluster_id:
                return _mcp_error(request_id=request_id, error="Artifact is outside lease kluster scope", error_code="forbidden")
            if mode == "content":
                if artifact.storage_backend == "s3" and artifact.uri.startswith("s3://"):
                    expected = scoped_prefix(mission_id=lease.mission_id, kluster_id=lease.kluster_id)
                    try:
                        body, content_type = get_bytes_from_uri(artifact.uri, expected_prefix=expected)
                    except Exception as exc:
                        return _mcp_error(request_id=request_id, error=f"S3 fetch failed: {exc}", error_code="storage_error")
                elif artifact.content_b64:
                    try:
                        body = base64.b64decode(str(artifact.content_b64), validate=True)
                    except Exception as exc:
                        return _mcp_error(
                            request_id=request_id,
                            error=f"Inline content decode failed: {exc}",
                            error_code="storage_error",
                        )
                    content_type = artifact.mime_type or "application/octet-stream"
                else:
                    return _mcp_error(
                        request_id=request_id,
                        error="Artifact does not have managed retrievable content",
                        error_code="invalid_request",
                    )
                return _mcp_ok(
                    request_id=request_id,
                    result={
                        "artifact_id": artifact.id,
                        "mode": "content",
                        "mime_type": content_type,
                        "size_bytes": len(body),
                        "content_b64": base64.b64encode(body).decode("ascii"),
                    },
                )
            expires_seconds = int(args.get("expires_seconds") or 60)
            if artifact.storage_backend != "s3" or not artifact.uri.startswith("s3://"):
                return _mcp_error(
                    request_id=request_id,
                    error="Artifact is not S3-backed and cannot generate download_url",
                    error_code="invalid_request",
                )
            expected = scoped_prefix(mission_id=lease.mission_id, kluster_id=lease.kluster_id)
            try:
                download_url = presign_get_uri(
                    artifact.uri,
                    expires_seconds=expires_seconds,
                    expected_prefix=expected,
                )
            except Exception as exc:
                return _mcp_error(request_id=request_id, error=f"S3 presign failed: {exc}", error_code="storage_error")
            ttl = max(1, min(expires_seconds, 3600))
            return _mcp_ok(
                request_id=request_id,
                result={
                    "artifact_id": artifact.id,
                    "mode": "download_url",
                    "uri": artifact.uri,
                    "expires_seconds": ttl,
                    "download_url": download_url,
                },
            )

        if tool == "commit_kluster_workspace":
            gated = ensure_action("workspace.commit")
            if gated:
                return gated
            lease_id = str(args.get("lease_id") or "")
            if not lease_id:
                return _mcp_error(request_id=request_id, error="lease_id is required")
            changes = args.get("change_set")
            if not isinstance(changes, list) or not changes:
                return _mcp_error(request_id=request_id, error="change_set must be a non-empty array")
            lease = get_lease(session, lease_id)
            if not lease:
                return _mcp_error(request_id=request_id, error="Workspace lease not found", error_code="not_found")
            try:
                assert_lease_owner_or_admin(lease, actor_subject, is_admin=is_platform_admin(request))
                assert_mission_writer_or_admin(session=session, request=request, mission_id=lease.mission_id)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
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
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="conflict")
            if not result.get("ok"):
                return _mcp_error(
                    request_id=request_id,
                    error="workspace_conflicts_detected",
                    error_code="workspace_conflicts_detected",
                    result={"conflicts": result.get("conflicts", [])},
                )
            return _mcp_ok(
                request_id=request_id,
                result=_mutation_result_with_ledger(
                    session=session,
                    mission_id=lease.mission_id,
                    payload={
                        "applied_count": result.get("applied_count", 0),
                        "applied": result.get("applied", []),
                        "workspace_snapshot": result.get("workspace_snapshot", {}),
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
                return _mcp_error(request_id=request_id, error="lease_id is required")
            lease = get_lease(session, lease_id)
            if not lease:
                return _mcp_error(request_id=request_id, error="Workspace lease not found", error_code="not_found")
            try:
                assert_lease_owner_or_admin(lease, actor_subject, is_admin=is_platform_admin(request))
                assert_mission_reader_or_admin(session=session, request=request, mission_id=lease.mission_id)
                lease = release_workspace_lease(session=session, lease=lease, reason=reason)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
            return _mcp_ok(
                request_id=request_id,
                result={
                    "lease": {
                        "id": lease.id,
                        "status": lease.status,
                        "release_reason": lease.release_reason,
                        "released_at": lease.released_at,
                    },
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
                return _mcp_error(
                    request_id=request_id,
                    error=str(exc.detail),
                    error_code="schema_validation_failed",
                )
            kluster = session.get(Kluster, kluster_id)
            if not kluster:
                return _mcp_error(request_id=request_id, error="Kluster not found", error_code="not_found")
            if not kluster.mission_id:
                return _mcp_error(
                    request_id=request_id,
                    error="Kluster is not linked to a mission",
                    error_code="forbidden",
                )
            try:
                assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
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
            return _mcp_ok(
                request_id=request_id,
                result=_mutation_result_with_ledger(
                    session=session,
                    mission_id=kluster.mission_id,
                    payload={"task": task_to_public_dict(task)},
                    approval_trace=approval_trace,
                ),
            )

        if tool == "claim_task":
            gated = ensure_action("task.update")
            if gated:
                return gated
            task_ref = str(args.get("task_id") or "")
            if not task_ref:
                return _mcp_error(request_id=request_id, error="task_id is required", error_code="invalid_request")
            task = resolve_task_by_ref(session=session, task_ref=task_ref)
            if not task:
                return _mcp_error(request_id=request_id, error="Task not found", error_code="not_found")
            kluster = session.get(Kluster, task.kluster_id)
            if kluster and kluster.mission_id:
                try:
                    assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
                except HTTPException as exc:
                    return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
            # Atomic claim: only succeed if task is proposed, or already owned by this actor
            if task.status != "proposed":
                if task.owner and task.owner != actor_subject:
                    return _mcp_error(
                        request_id=request_id,
                        error=f"Task is already claimed (status={task.status}, owner={task.owner})",
                        error_code="conflict",
                        result={"current_status": task.status, "current_owner": task.owner},
                    )
            before = task.dict()
            task.status = "in_progress"
            task.owner = actor_subject
            task.updated_at = datetime.utcnow()
            session.add(task)
            session.commit()
            session.refresh(task)
            enqueue_ledger_event(
                session=session,
                mission_id=kluster.mission_id if kluster else None,
                kluster_id=task.kluster_id,
                entity_type="task",
                entity_id=task.public_id or str(task.id),
                action="claim",
                before=before,
                after=task.dict(),
                actor_subject=actor_subject,
                source=source,
            )
            return _mcp_ok(
                request_id=request_id,
                result=_mutation_result_with_ledger(
                    session=session,
                    mission_id=kluster.mission_id if kluster else None,
                    payload={"task": task_to_public_dict(task)},
                    approval_trace=None,
                ),
            )

        if tool == "update_task":
            gated = ensure_action("task.update")
            if gated:
                return gated
            task_ref = str(args.get("task_id") or "")
            task = resolve_task_by_ref(session=session, task_ref=task_ref)
            if not task:
                return _mcp_error(request_id=request_id, error="Task not found", error_code="not_found")
            # Optimistic locking: if expected_status is provided, reject if current status differs
            expected_status = args.get("expected_status")
            if expected_status is not None and task.status != expected_status:
                return _mcp_error(
                    request_id=request_id,
                    error=f"Task status conflict: expected '{expected_status}' but current status is '{task.status}'",
                    error_code="conflict",
                    result={"current_status": task.status, "expected_status": expected_status},
                )
            before = task.dict()
            kluster = session.get(Kluster, task.kluster_id)
            if kluster and kluster.mission_id:
                try:
                    assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
                except HTTPException as exc:
                    return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
            # Owner guard: status transitions require ownership (unless admin or task is unowned)
            new_status = args.get("status")
            if new_status and new_status != task.status and task.owner and task.owner != actor_subject:
                if not is_platform_admin(request):
                    return _mcp_error(
                        request_id=request_id,
                        error=f"Task is owned by '{task.owner}'; only the owner can change its status",
                        error_code="forbidden",
                        result={"current_owner": task.owner},
                    )
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
                return _mcp_error(
                    request_id=request_id,
                    error=str(exc.detail),
                    error_code="schema_validation_failed",
                )
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
            return _mcp_ok(
                request_id=request_id,
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
                return _mcp_error(request_id=request_id, error="Task not found", error_code="not_found")
            task_id = task.id
            kluster = session.get(Kluster, task.kluster_id)
            if not kluster or not kluster.mission_id:
                return _mcp_error(
                    request_id=request_id,
                    error="Task kluster is not linked to a mission",
                    error_code="forbidden",
                )
            try:
                assert_mission_owner_or_admin(session=session, request=request, mission_id=kluster.mission_id)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
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
            return _mcp_ok(
                request_id=request_id,
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
                return _mcp_error(request_id=request_id, error="mission_id is required")
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
            limit = int(args.get("limit") or 100)
            events = list_pending_ledger_events(session=session, mission_id=mission_id, limit=limit)
            return _mcp_ok(request_id=request_id, result={"events": [model_to_dict(e) for e in events]})

        if tool == "publish_pending_ledger_events":
            gated = ensure_action("mission.publish")
            if gated:
                return gated
            mission_id = str(args.get("mission_id"))
            if not subject_can_publish_mission(session=session, mission_id=mission_id, subject=actor_subject):
                return _mcp_error(
                    request_id=request_id,
                    error="Forbidden: only mission owners or contributors can publish mission ledger events",
                    error_code="forbidden",
                )
            try:
                result = publish_pending_ledger_events(
                    session=session,
                    mission_id=mission_id,
                    actor_subject=actor_subject,
                )
                return _mcp_ok(request_id=request_id, result=result)
            except PublishRoutingError as exc:
                return _mcp_error(request_id=request_id, error=str(exc), error_code="ledger_publish_failed")

        if tool == "list_repo_bindings":
            bindings = session.exec(
                select(RepoBinding)
                .where(RepoBinding.owner_subject == actor_subject)
                .where(RepoBinding.active == True)  # noqa: E712
                .order_by(RepoBinding.updated_at.desc())
            ).all()
            return _mcp_ok(request_id=request_id, result={"bindings": [model_to_dict(b) for b in bindings]})

        if tool == "provision_mission_persistence":
            mission_id = str(args.get("mission_id") or "").strip()
            if not mission_id:
                return _mcp_error(request_id=request_id, error="mission_id is required", error_code="invalid_input")
            try:
                assert_mission_owner_or_admin(session=session, request=request, mission_id=mission_id)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")

            now = datetime.utcnow()
            conn_input = args.get("connection") if isinstance(args.get("connection"), dict) else {}
            bind_input = args.get("binding") if isinstance(args.get("binding"), dict) else {}
            routes_input = args.get("routes") if isinstance(args.get("routes"), list) else []

            conn_name = str(conn_input.get("name") or "").strip()
            repo_path = str(conn_input.get("repo_path") or "").strip()
            if not conn_name or not repo_path:
                return _mcp_error(
                    request_id=request_id,
                    error="connection.name and connection.repo_path are required",
                    error_code="invalid_input",
                )
            connection = session.exec(
                select(RepoConnection)
                .where(RepoConnection.owner_subject == actor_subject)
                .where(RepoConnection.name == conn_name)
            ).first()
            if not connection:
                connection = RepoConnection(
                    owner_subject=actor_subject,
                    name=conn_name,
                    provider=str(conn_input.get("provider") or "github_app").strip(),
                    host=str(conn_input.get("host") or "github.com").strip(),
                    repo_path=repo_path,
                    default_branch=str(conn_input.get("default_branch") or "main").strip(),
                    credential_ref=str(conn_input.get("credential_ref") or "").strip(),
                    options_json=json.dumps(conn_input.get("options") or {}, separators=(",", ":")),
                    created_at=now,
                    updated_at=now,
                )
            else:
                connection.provider = str(conn_input.get("provider") or connection.provider).strip()
                connection.host = str(conn_input.get("host") or connection.host).strip()
                connection.repo_path = repo_path
                connection.default_branch = str(conn_input.get("default_branch") or connection.default_branch).strip()
                connection.credential_ref = str(conn_input.get("credential_ref") or connection.credential_ref).strip()
                connection.options_json = json.dumps(conn_input.get("options") or {}, separators=(",", ":"))
                connection.updated_at = now
            session.add(connection)
            session.commit()
            session.refresh(connection)

            binding_name = str(bind_input.get("name") or "").strip()
            if not binding_name:
                return _mcp_error(request_id=request_id, error="binding.name is required", error_code="invalid_input")
            binding = session.exec(
                select(RepoBinding)
                .where(RepoBinding.owner_subject == actor_subject)
                .where(RepoBinding.name == binding_name)
            ).first()
            if not binding:
                binding = RepoBinding(
                    owner_subject=actor_subject,
                    name=binding_name,
                    connection_id=connection.id,
                    branch_override=str(bind_input.get("branch_override") or "").strip(),
                    base_path=str(bind_input.get("base_path") or "missions").strip().strip("/"),
                    active=bool(bind_input.get("active", True)),
                    created_at=now,
                    updated_at=now,
                )
            else:
                binding.connection_id = connection.id
                binding.branch_override = str(bind_input.get("branch_override") or binding.branch_override).strip()
                binding.base_path = str(bind_input.get("base_path") or binding.base_path).strip().strip("/")
                binding.active = bool(bind_input.get("active", True))
                binding.updated_at = now
            session.add(binding)
            session.commit()
            session.refresh(binding)

            policy = session.exec(
                select(MissionPersistencePolicy).where(MissionPersistencePolicy.mission_id == mission_id)
            ).first()
            if not policy:
                policy = MissionPersistencePolicy(mission_id=mission_id, created_at=now, updated_at=now)
            policy.default_binding_id = binding.id
            policy.fallback_mode = str(args.get("fallback_mode") or "fail_closed")
            policy.require_approval = bool(args.get("require_approval", False))
            policy.updated_at = now
            session.add(policy)
            session.commit()

            session.exec(
                MissionPersistenceRoute.__table__.delete().where(MissionPersistenceRoute.mission_id == mission_id)
            )
            for item in routes_input:
                if not isinstance(item, dict):
                    continue
                target_name = str(item.get("binding_name") or binding.name).strip()
                target = session.exec(
                    select(RepoBinding)
                    .where(RepoBinding.owner_subject == actor_subject)
                    .where(RepoBinding.name == target_name)
                ).first()
                if not target:
                    continue
                row = MissionPersistenceRoute(
                    mission_id=mission_id,
                    entity_kind=str(item.get("entity_kind") or "").strip(),
                    event_kind=str(item.get("event_kind") or "").strip(),
                    binding_id=target.id,
                    branch_override=str(item.get("branch_override") or "").strip(),
                    path_template=str(
                        item.get("path_template") or "missions/{mission_id}/{entity_kind}/{entity_id}.json"
                    ),
                    format=str(item.get("format") or "json_v1"),
                    active=bool(item.get("active", True)),
                    created_at=now,
                    updated_at=now,
                )
                if row.entity_kind:
                    session.add(row)
            session.commit()
            routes = session.exec(
                select(MissionPersistenceRoute)
                .where(MissionPersistenceRoute.mission_id == mission_id)
                .where(MissionPersistenceRoute.active == True)  # noqa: E712
                .order_by(MissionPersistenceRoute.id.asc())
            ).all()
            return _mcp_ok(
                request_id=request_id,
                result={
                    "ok": True,
                    "mission_id": mission_id,
                    "connection": model_to_dict(connection),
                    "binding": model_to_dict(binding),
                    "routes": [model_to_dict(r) for r in routes],
                },
            )

        if tool == "resolve_publish_plan":
            mission_id = str(args.get("mission_id") or "").strip()
            entity_kind = str(args.get("entity_kind") or "").strip()
            event_kind = str(args.get("event_kind") or "").strip()
            entity_id = str(args.get("entity_id") or "").strip()
            if not mission_id or not entity_kind or not event_kind or not entity_id:
                return _mcp_error(
                    request_id=request_id,
                    error="mission_id, entity_kind, event_kind, entity_id are required",
                    error_code="invalid_input",
                )
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
            try:
                plan = resolve_publish_plan(
                    session=session,
                    owner_subject=actor_subject,
                    mission_id=mission_id,
                    entity_kind=entity_kind,
                    event_kind=event_kind,
                    entity_id=entity_id,
                )
            except PublishRoutingError as exc:
                return _mcp_error(request_id=request_id, error=str(exc), error_code="publish_route_unavailable")
            return _mcp_ok(
                request_id=request_id,
                result={
                    "mission_id": mission_id,
                    "entity_kind": entity_kind,
                    "event_kind": event_kind,
                    "entity_id": entity_id,
                    "binding_id": plan.binding.id,
                    "binding_name": plan.binding.name,
                    "connection_id": plan.connection.id,
                    "provider": plan.connection.provider,
                    "repo": f"{plan.connection.host}/{plan.connection.repo_path}",
                    "branch": plan.branch,
                    "path": plan.rel_path,
                    "format": plan.format,
                },
            )

        if tool == "get_publication_status":
            mission_id_raw = args.get("mission_id")
            mission_id = str(mission_id_raw) if mission_id_raw is not None else None
            if mission_id:
                try:
                    assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
                except HTTPException as exc:
                    return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
            limit = int(args.get("limit") or 20)
            records = get_publication_status(
                session=session,
                owner_subject=actor_subject,
                mission_id=mission_id,
                limit=limit,
            )
            return _mcp_ok(request_id=request_id, result={"records": [model_to_dict(r) for r in records]})

        if tool == "get_entity_history":
            mission_id_raw = args.get("mission_id")
            mission_id = str(mission_id_raw) if mission_id_raw is not None else ""
            if not mission_id:
                return _mcp_error(request_id=request_id, error="mission_id is required")
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
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
            return _mcp_ok(request_id=request_id, result={"events": [model_to_dict(e) for e in events]})

        if tool == "list_tasks":
            kluster_id = str(args.get("kluster_id"))
            kluster = session.get(Kluster, kluster_id)
            if not kluster:
                return _mcp_error(request_id=request_id, error="Kluster not found", error_code="not_found")
            if not kluster.mission_id:
                return _mcp_error(
                    request_id=request_id,
                    error="Kluster is not linked to a mission",
                    error_code="forbidden",
                )
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=kluster.mission_id)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
            tasks = session.exec(select(Task).where(Task.kluster_id == kluster_id)).all()
            for task in tasks:
                ensure_task_public_id(session, task)
            return _mcp_ok(request_id=request_id, result={"tasks": [task_to_public_dict(t) for t in tasks]})

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
                "visibility": str(args.get("visibility") or "public"),
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
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
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
                return _mcp_error(request_id=request_id, error="Kluster not found", error_code="not_found")
            if kluster.mission_id:
                try:
                    assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
                except HTTPException as exc:
                    return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
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
                return _mcp_error(request_id=request_id, error="Changing mission_id is not supported")
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
                return _mcp_error(
                    request_id=request_id,
                    error=str(exc.detail),
                    error_code="schema_validation_failed",
                )
            if "owners" in update_payload and not _owner_list(update_payload.get("owners")):
                return _mcp_error(request_id=request_id, error="owners must include at least one owner")
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
            return _mcp_ok(
                request_id=request_id,
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
                return _mcp_error(request_id=request_id, error="Kluster not found", error_code="not_found")
            if not kluster.mission_id:
                return _mcp_error(
                    request_id=request_id,
                    error="Kluster is not linked to a mission",
                    error_code="forbidden",
                )
            try:
                assert_mission_owner_or_admin(session=session, request=request, mission_id=kluster.mission_id)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
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
                return _mcp_error(
                    request_id=request_id,
                    error=f"Kluster has linked entities: {blocking}",
                    error_code="conflict",
                )
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
            return _mcp_ok(
                request_id=request_id,
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
                return _mcp_error(request_id=request_id, error="Mission not found", error_code="not_found")
            try:
                assert_mission_writer_or_admin(session=session, request=request, mission_id=mission_id)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
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
                return _mcp_error(
                    request_id=request_id,
                    error=str(exc.detail),
                    error_code="schema_validation_failed",
                )
            if "owners" in update_payload and not _owner_list(update_payload.get("owners")):
                return _mcp_error(request_id=request_id, error="owners must include at least one owner")
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
            return _mcp_ok(
                request_id=request_id,
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
                return _mcp_error(request_id=request_id, error="Mission not found", error_code="not_found")
            try:
                assert_mission_owner_or_admin(session=session, request=request, mission_id=mission_id)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
            linked_cluster = session.exec(select(Kluster).where(Kluster.mission_id == mission_id)).first()
            if linked_cluster:
                return _mcp_error(
                    request_id=request_id,
                    error="Mission has linked klusters; move or delete klusters first",
                    error_code="conflict",
                )
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
            return _mcp_ok(request_id=request_id, result={"ok": True, "deleted_id": mission_id})

        if tool == "update_doc":
            gated = ensure_action("doc.update")
            if gated:
                return gated
            doc_id = int(args.get("doc_id"))
            doc = session.get(Doc, doc_id)
            if not doc:
                return _mcp_error(request_id=request_id, error="Doc not found", error_code="not_found")
            before = doc.dict()
            kluster = session.get(Kluster, doc.kluster_id)
            if kluster and kluster.mission_id:
                try:
                    assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
                except HTTPException as exc:
                    return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
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
                return _mcp_error(
                    request_id=request_id,
                    error=str(exc.detail),
                    error_code="schema_validation_failed",
                )
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
            return _mcp_ok(
                request_id=request_id,
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
                return _mcp_error(request_id=request_id, error="Artifact not found", error_code="not_found")
            before = artifact.dict()
            kluster = session.get(Kluster, artifact.kluster_id)
            if kluster and kluster.mission_id:
                try:
                    assert_mission_writer_or_admin(session=session, request=request, mission_id=kluster.mission_id)
                except HTTPException as exc:
                    return _mcp_error(request_id=request_id, error=str(exc.detail), error_code="forbidden")
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
                return _mcp_error(
                    request_id=request_id,
                    error=str(exc.detail),
                    error_code="schema_validation_failed",
                )
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
            return _mcp_ok(
                request_id=request_id,
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
                return _mcp_error(request_id=request_id, error="Task not found", error_code="not_found")
            suggestions = session.exec(
                select(OverlapSuggestion).where(OverlapSuggestion.task_id == task.id)
            ).all()
            return _mcp_ok(request_id=request_id, result={"overlaps": [model_to_dict(s) for s in suggestions]})

        if tool == "register_agent":
            name = args.get("name") or ""
            capabilities = args.get("capabilities") or ""
            metadata = args.get("metadata") or ""
            agent = Agent(name=name, capabilities=capabilities, agent_metadata=metadata)
            session.add(agent)
            session.commit()
            session.refresh(agent)
            return _mcp_ok(request_id=request_id, result={"agent": model_to_dict(agent)})

        if tool == "list_agents":
            status = args.get("status")
            stmt = select(Agent)
            if status:
                stmt = stmt.where(Agent.status == status)
            agents = session.exec(stmt.order_by(Agent.updated_at.desc())).all()
            return _mcp_ok(request_id=request_id, result={"agents": [model_to_dict(a) for a in agents]})

        if tool == "get_agent":
            agent_id = int(args.get("agent_id"))
            agent = session.get(Agent, agent_id)
            if not agent:
                return _mcp_error(request_id=request_id, error="Agent not found", error_code="not_found")
            return _mcp_ok(request_id=request_id, result={"agent": model_to_dict(agent)})

        if tool == "update_agent_status":
            agent_id = int(args.get("agent_id"))
            status = args.get("status") or "offline"
            agent = session.get(Agent, agent_id)
            if not agent:
                return _mcp_error(request_id=request_id, error="Agent not found", error_code="not_found")
            agent.status = status
            agent.updated_at = datetime.utcnow()
            session.add(agent)
            session.commit()
            session.refresh(agent)
            return _mcp_ok(request_id=request_id, result={"agent": model_to_dict(agent)})

        if tool == "start_agent_session":
            agent_id = int(args.get("agent_id"))
            context = args.get("context") or ""
            agent = session.get(Agent, agent_id)
            if not agent:
                return _mcp_error(request_id=request_id, error="Agent not found", error_code="not_found")
            session_obj = AgentSession(agent_id=agent_id, context=context)
            agent.status = "online"
            session.add(session_obj)
            session.add(agent)
            session.commit()
            session.refresh(session_obj)
            return _mcp_ok(request_id=request_id, result={"session": model_to_dict(session_obj)})

        if tool == "end_agent_session":
            agent_id = int(args.get("agent_id"))
            session_id = int(args.get("session_id"))
            agent = session.get(Agent, agent_id)
            if not agent:
                return _mcp_error(request_id=request_id, error="Agent not found", error_code="not_found")
            session_obj = session.get(AgentSession, session_id)
            if not session_obj or session_obj.agent_id != agent_id:
                return _mcp_error(request_id=request_id, error="Session not found", error_code="not_found")
            session_obj.ended_at = datetime.utcnow()
            agent.status = "offline"
            session.add(session_obj)
            session.add(agent)
            session.commit()
            session.refresh(session_obj)
            return _mcp_ok(request_id=request_id, result={"session": model_to_dict(session_obj)})

        if tool == "claim_task":
            task_ref = str(args.get("task_id") or "")
            agent_id = int(args.get("agent_id"))
            agent = session.get(Agent, agent_id)
            if not agent:
                return _mcp_error(request_id=request_id, error="Agent not found", error_code="not_found")
            task = resolve_task_by_ref(session=session, task_ref=task_ref)
            if not task:
                return _mcp_error(request_id=request_id, error="Task not found", error_code="not_found")
            task_id = task.id
            assignment = TaskAssignment(task_id=task_id, agent_id=agent_id, status="claimed")
            session.add(assignment)
            session.commit()
            session.refresh(assignment)
            return _mcp_ok(
                request_id=request_id,
                result={
                    "assignment": model_to_dict(assignment),
                    "task_id": task.public_id or str(task.id),
                },
            )

        if tool == "release_task":
            assignment_id = int(args.get("assignment_id"))
            assignment = session.get(TaskAssignment, assignment_id)
            if not assignment:
                return _mcp_error(request_id=request_id, error="Assignment not found", error_code="not_found")
            assignment.status = "available"
            assignment.updated_at = datetime.utcnow()
            session.add(assignment)
            session.commit()
            session.refresh(assignment)
            return _mcp_ok(request_id=request_id, result={"assignment": model_to_dict(assignment)})

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
                    return _mcp_ok(request_id=request_id, result={"assignments": []})
                stmt = stmt.where(TaskAssignment.task_id == int(task.id))
            if status:
                stmt = stmt.where(TaskAssignment.status == status)
            assignments = session.exec(stmt.order_by(TaskAssignment.updated_at.desc())).all()
            return _mcp_ok(request_id=request_id, result={"assignments": [model_to_dict(a) for a in assignments]})

        if tool == "resolve_skill_snapshot":
            gated = ensure_action("skills.snapshot.resolve")
            if gated:
                return gated
            mission_id = str(args.get("mission_id") or "")
            kluster_id = str(args.get("kluster_id") or "")
            if not mission_id:
                return _mcp_error(request_id=request_id, error="mission_id is required")
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
                if kluster_id:
                    validate_kluster_scope(session=session, mission_id=mission_id, kluster_id=kluster_id)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=exc.detail, error_code="forbidden")
            snapshot = resolve_effective_snapshot(session=session, mission_id=mission_id, kluster_id=kluster_id)
            return _mcp_ok(
                request_id=request_id,
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
                return _mcp_error(request_id=request_id, error="snapshot_id is required")
            snapshot = session.get(SkillSnapshot, snapshot_id)
            if not snapshot:
                return _mcp_error(request_id=request_id, error="Skill snapshot not found", error_code="not_found")
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=snapshot.mission_id)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=exc.detail, error_code="forbidden")
            return _mcp_ok(
                request_id=request_id,
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
                return _mcp_error(request_id=request_id, error="mission_id is required")
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
                if kluster_id:
                    validate_kluster_scope(session=session, mission_id=mission_id, kluster_id=kluster_id)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=exc.detail, error_code="forbidden")
            actor = actor_subject
            state = get_sync_state(
                session=session,
                actor_subject=actor,
                mission_id=mission_id,
                kluster_id=kluster_id,
                agent_id=agent_id,
            )
            if state is None:
                return _mcp_ok(
                    request_id=request_id,
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
            return _mcp_ok(
                request_id=request_id,
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
                return _mcp_error(request_id=request_id, error="mission_id is required")
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
                if kluster_id:
                    validate_kluster_scope(session=session, mission_id=mission_id, kluster_id=kluster_id)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=exc.detail, error_code="forbidden")
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
            return _mcp_ok(
                request_id=request_id,
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
                return _mcp_error(request_id=request_id, error="mission_id is required")
            try:
                assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
                if kluster_id:
                    validate_kluster_scope(session=session, mission_id=mission_id, kluster_id=kluster_id)
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=exc.detail, error_code="forbidden")
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
            return _mcp_ok(
                request_id=request_id,
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
            return _mcp_ok(request_id=request_id, result={"profiles": [_profile_to_dict(p) for p in profiles]})

        if tool == "get_profile":
            name = str(args.get("name") or "").strip()
            if not name:
                return _mcp_error(request_id=request_id, error="name is required")
            profile = session.exec(
                select(UserProfile)
                .where(UserProfile.owner_subject == actor_subject)
                .where(UserProfile.name == name)
            ).first()
            if not profile:
                return _mcp_error(request_id=request_id, error="Profile not found", error_code="not_found")
            return _mcp_ok(request_id=request_id, result={"profile": _profile_to_dict(profile)})

        if tool == "publish_profile":
            name = str(args.get("name") or "").strip()
            tarball_b64 = str(args.get("tarball_b64") or "").strip()
            if not name or not tarball_b64:
                return _mcp_error(request_id=request_id, error="name and tarball_b64 are required")
            expected_sha256 = str(args.get("expected_sha256") or "").strip()
            description = str(args.get("description") or "")
            is_default = bool(args.get("is_default") or False)
            manifest = args.get("manifest")
            if manifest is None:
                manifest = []
            if not isinstance(manifest, list):
                return _mcp_error(request_id=request_id, error="manifest must be an array")
            try:
                raw = base64.b64decode(tarball_b64)
            except Exception:
                return _mcp_error(request_id=request_id, error="tarball_b64 is not valid base64")
            computed_sha = hashlib.sha256(raw).hexdigest()
            mirror_uri = ""
            mirror_sha = ""
            mirror_size = 0
            mirrored_at = None
            if object_storage_enabled():
                owner_slug = "".join(ch if ch.isalnum() else "-" for ch in actor_subject.lower()).strip("-") or "owner"
                name_slug = "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-") or "profile"
                key = f"profiles/{owner_slug}/{name_slug}/{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.tar"
                persisted_uri, persisted_size = put_bytes(key=key, body=raw, content_type="application/x-tar")
                mirror_uri = persisted_uri
                mirror_size = persisted_size
                mirror_sha = computed_sha
                mirrored_at = datetime.utcnow()
            profile = session.exec(
                select(UserProfile)
                .where(UserProfile.owner_subject == actor_subject)
                .where(UserProfile.name == name)
            ).first()
            if profile:
                if expected_sha256 and (profile.sha256 or "") != expected_sha256:
                    return _mcp_error(
                        request_id=request_id,
                        error="profile_sha_mismatch",
                        error_code="profile_sha_mismatch",
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
                if mirror_uri:
                    profile.mirror_uri = mirror_uri
                    profile.mirror_size_bytes = mirror_size
                    profile.mirror_sha256 = mirror_sha
                    profile.mirrored_at = mirrored_at
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
                    mirror_uri=mirror_uri,
                    mirror_sha256=mirror_sha,
                    mirror_size_bytes=mirror_size,
                    mirrored_at=mirrored_at,
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
            return _mcp_ok(request_id=request_id, result={"profile": _profile_to_dict(profile)})

        if tool == "download_profile":
            name = str(args.get("name") or "").strip()
            if not name:
                return _mcp_error(request_id=request_id, error="name is required")
            if_sha256 = str(args.get("if_sha256") or "").strip()
            profile = session.exec(
                select(UserProfile)
                .where(UserProfile.owner_subject == actor_subject)
                .where(UserProfile.name == name)
            ).first()
            if not profile:
                return _mcp_error(request_id=request_id, error="Profile not found", error_code="not_found")
            current_sha = (profile.sha256 or "").strip()
            if if_sha256 and if_sha256 != current_sha:
                return _mcp_error(
                    request_id=request_id,
                    error="profile_sha_mismatch",
                    error_code="profile_sha_mismatch",
                    result={
                        "expected_sha256": if_sha256,
                        "current_sha256": current_sha,
                        "name": name,
                    },
                )
            tarball_b64 = profile.tarball_b64 or ""
            if not tarball_b64 and profile.mirror_uri:
                try:
                    body, _ = get_bytes_from_uri(profile.mirror_uri)
                    tarball_b64 = base64.b64encode(body).decode("ascii")
                except Exception as exc:
                    return _mcp_error(
                        request_id=request_id,
                        error=f"profile mirror fetch failed: {exc}",
                        error_code="storage_error",
                    )
            return _mcp_ok(
                request_id=request_id,
                result={
                    "profile": _profile_to_dict(profile),
                    "tarball_b64": tarball_b64,
                },
            )

        if tool == "activate_profile":
            name = str(args.get("name") or "").strip()
            if not name:
                return _mcp_error(request_id=request_id, error="name is required")
            profile = session.exec(
                select(UserProfile)
                .where(UserProfile.owner_subject == actor_subject)
                .where(UserProfile.name == name)
            ).first()
            if not profile:
                return _mcp_error(request_id=request_id, error="Profile not found", error_code="not_found")
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
            return _mcp_ok(request_id=request_id, result={"profile": _profile_to_dict(profile)})

        if tool == "delete_profile":
            name = str(args.get("name") or "").strip()
            if not name:
                return _mcp_error(request_id=request_id, error="name is required")
            profile = session.exec(
                select(UserProfile)
                .where(UserProfile.owner_subject == actor_subject)
                .where(UserProfile.name == name)
            ).first()
            if not profile:
                return _mcp_error(request_id=request_id, error="Profile not found", error_code="not_found")
            session.delete(profile)
            session.commit()
            return _mcp_ok(request_id=request_id, result={"ok": True, "deleted_profile": name})

        if tool == "profile_status":
            name = str(args.get("name") or "").strip()
            if not name:
                return _mcp_error(request_id=request_id, error="name is required")
            expected_sha256 = str(args.get("expected_sha256") or "").strip()
            profile = session.exec(
                select(UserProfile)
                .where(UserProfile.owner_subject == actor_subject)
                .where(UserProfile.name == name)
            ).first()
            if not profile:
                return _mcp_error(request_id=request_id, error="Profile not found", error_code="not_found")
            remote_sha = (profile.sha256 or "").strip()
            return _mcp_ok(
                request_id=request_id,
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
                return _mcp_error(request_id=request_id, error="name and sha256 are required")
            profile = session.exec(
                select(UserProfile)
                .where(UserProfile.owner_subject == actor_subject)
                .where(UserProfile.name == name)
            ).first()
            if not profile:
                return _mcp_error(request_id=request_id, error="Profile not found", error_code="not_found")
            remote_sha = (profile.sha256 or "").strip()
            matches = remote_sha == pinned_sha
            if not matches:
                return _mcp_error(
                    request_id=request_id,
                    error="profile_sha_mismatch",
                    error_code="profile_sha_mismatch",
                    result={
                        "name": name,
                        "pinned_sha256": pinned_sha,
                        "remote_sha256": remote_sha,
                        "matches": matches,
                    },
                )
            return _mcp_ok(
                request_id=request_id,
                result={
                    "name": name,
                    "pinned_sha256": pinned_sha,
                    "remote_sha256": remote_sha,
                    "matches": matches,
                },
            )

        elif tool == "register_remote_target":
            from app.routers.remotectl import TargetCreate, create_target
            body = TargetCreate(
                name=args["name"],
                host=args["host"],
                user=args.get("user", ""),
                port=args.get("port", 22),
                transport=args.get("transport", "ssh"),
                ssh_pubkey=args.get("ssh_pubkey", ""),
                key_fingerprint=args.get("key_fingerprint", ""),
            )
            result = create_target(body=body, request=request)
            return _mcp_ok(request_id=request_id, result=dict(result))

        elif tool == "list_remote_targets":
            from app.routers.remotectl import list_targets
            data = list_targets(request=request)
            return _mcp_ok(request_id=request_id, result={
                "targets": [dict(t) for t in data["targets"]]
            })

        elif tool == "delete_remote_target":
            from app.routers.remotectl import delete_target
            delete_target(target_id=args["target_id"], request=request)
            return _mcp_ok(request_id=request_id, result={"deleted": args["target_id"]})

        elif tool == "create_remote_launch":
            from app.routers.remotectl import LaunchCreate, create_launch
            # capability_scope: MCP sends a comma-separated string; LaunchCreate expects list[str]
            scope_raw = args.get("capability_scope", "")
            scope_list = [s.strip() for s in scope_raw.split(",") if s.strip()] if scope_raw else []
            body = LaunchCreate(
                transport=args["transport"],
                target_id=args.get("target_id"),
                target_host=args.get("target_host", ""),
                agent_kind=args["agent_kind"],
                agent_profile=args.get("agent_profile", ""),
                capability_scope=scope_list,
                ttl_hours=args.get("ttl_hours", 8),
            )
            result = create_launch(body=body, request=request)
            result["launch_id"] = result["id"]
            result["mc_base_url"] = os.environ.get("MC_BASE_URL", "")
            return _mcp_ok(request_id=request_id, result=result)

        elif tool == "list_remote_launches":
            from app.routers.remotectl import list_launches
            data = list_launches(request=request)
            launches = [dict(l) for l in data.get("launches", [])]
            if args.get("status"):
                launches = [l for l in launches if l.get("status") == args["status"]]
            return _mcp_ok(request_id=request_id, result={"launches": launches})

        elif tool == "get_remote_launch":
            from app.routers.remotectl import get_launch
            result = get_launch(launch_id=args["launch_id"], request=request)
            return _mcp_ok(request_id=request_id, result=result)

        elif tool == "kill_remote_launch":
            from app.routers.remotectl import delete_launch
            delete_launch(launch_id=args["launch_id"], request=request)
            return _mcp_ok(request_id=request_id, result={"killed": args["launch_id"]})

        # --- Mesh work model tools ---

        elif tool == "submit_mesh_task":
            import uuid as _uuid
            from datetime import datetime as _dt
            from app.routers.work import (
                MeshTaskCreate,
                _task_to_read,
                _detect_cycle,
                LEASE_TTL_SECONDS,
            )
            kluster_id = str(args.get("kluster_id") or "")
            with get_session() as _s:
                kluster = _s.get(Kluster, kluster_id)
                if kluster is None:
                    return _mcp_error(request_id=request_id, error="kluster not found", error_code="not_found")
                mission_id = kluster.mission_id or ""
                body = MeshTaskCreate(
                    title=str(args.get("title") or ""),
                    description=str(args.get("description") or ""),
                    claim_policy=str(args.get("claim_policy") or "first_claim"),
                    priority=int(args.get("priority") or 0),
                    required_capabilities=list(args.get("required_capabilities") or []),
                    depends_on=list(args.get("depends_on") or []),
                )
                valid_policies = {"assigned", "first_claim", "broadcast"}
                if body.claim_policy not in valid_policies:
                    return _mcp_error(request_id=request_id, error=f"claim_policy must be one of {valid_policies}")
                for dep_id in body.depends_on:
                    dep = _s.get(MeshTask, dep_id)
                    if dep is None or dep.kluster_id != kluster_id:
                        return _mcp_error(request_id=request_id, error=f"depends_on task {dep_id!r} not found in this kluster")
                new_id = str(_uuid.uuid4())
                if body.depends_on and _detect_cycle(kluster_id, new_id, body.depends_on, _s):
                    return _mcp_error(request_id=request_id, error="depends_on would create a cycle")
                import json as _json
                initial_status = "ready" if not body.depends_on else "pending"
                task = MeshTask(
                    id=new_id,
                    kluster_id=kluster_id,
                    mission_id=mission_id,
                    title=body.title,
                    description=body.description,
                    claim_policy=body.claim_policy,
                    depends_on=_json.dumps(body.depends_on),
                    produces=_json.dumps(body.produces),
                    consumes=_json.dumps(body.consumes),
                    required_capabilities=_json.dumps(body.required_capabilities),
                    status=initial_status,
                    priority=body.priority,
                    created_by_subject=actor_subject,
                    created_at=_dt.utcnow(),
                    updated_at=_dt.utcnow(),
                )
                _s.add(task)
                _s.commit()
                _s.refresh(task)
                return _mcp_ok(request_id=request_id, result={"task": _task_to_read(task)})

        elif tool == "list_mesh_tasks":
            from app.routers.work import _task_to_read, _expire_stale_leases
            kluster_id = str(args.get("kluster_id") or "")
            status_filter = args.get("status")
            with get_session() as _s:
                _expire_stale_leases(_s, kluster_id)
                _s.commit()
                q = select(MeshTask).where(MeshTask.kluster_id == kluster_id)
                if status_filter:
                    q = q.where(MeshTask.status == status_filter)
                q = q.order_by(MeshTask.priority.desc(), MeshTask.created_at)
                tasks = _s.exec(q).all()
                return _mcp_ok(request_id=request_id, result={"tasks": [_task_to_read(t) for t in tasks]})

        elif tool == "get_mesh_task":
            from app.routers.work import _task_to_read
            task_id = str(args.get("task_id") or "")
            with get_session() as _s:
                task = _s.get(MeshTask, task_id)
                if task is None:
                    return _mcp_error(request_id=request_id, error="task not found", error_code="not_found")
                return _mcp_ok(request_id=request_id, result={"task": _task_to_read(task)})

        elif tool == "claim_mesh_task":
            from app.routers.work import claim_task as _claim_task
            task_id = str(args.get("task_id") or "")
            # call the work router function directly
            try:
                result = _claim_task(task_id=task_id, request=request)
                return _mcp_ok(request_id=request_id, result=result if isinstance(result, dict) else dict(result))
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code=str(exc.status_code))

        elif tool == "heartbeat_mesh_task":
            from app.routers.work import HeartbeatBody, heartbeat_task as _heartbeat_task
            task_id = str(args.get("task_id") or "")
            body = HeartbeatBody(claim_lease_id=args.get("claim_lease_id"))
            try:
                result = _heartbeat_task(task_id=task_id, request=request, body=body)
                return _mcp_ok(request_id=request_id, result=result if isinstance(result, dict) else dict(result))
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code=str(exc.status_code))

        elif tool == "progress_mesh_task":
            from app.routers.work import MeshProgressEventCreate, append_progress as _append_progress
            import json as _json
            task_id = str(args.get("task_id") or "")
            payload_raw = args.get("payload") or {}
            body = MeshProgressEventCreate(
                event_type=str(args.get("event_type") or ""),
                phase=args.get("phase"),
                step=args.get("step"),
                summary=str(args.get("summary") or ""),
                payload_json=_json.dumps(payload_raw) if payload_raw else "{}",
            )
            try:
                result = _append_progress(task_id=task_id, body=body, request=request)
                return _mcp_ok(request_id=request_id, result=result if isinstance(result, dict) else dict(result))
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code=str(exc.status_code))

        elif tool == "complete_mesh_task":
            from app.routers.work import CompleteBody, complete_task as _complete_task
            task_id = str(args.get("task_id") or "")
            body = CompleteBody(
                claim_lease_id=args.get("claim_lease_id"),
                result_artifact_id=args.get("result_artifact_id"),
            )
            try:
                result = _complete_task(task_id=task_id, body=body)
                return _mcp_ok(request_id=request_id, result=result if isinstance(result, dict) else dict(result))
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code=str(exc.status_code))

        elif tool == "fail_mesh_task":
            from app.routers.work import FailBody, fail_task as _fail_task
            task_id = str(args.get("task_id") or "")
            body = FailBody(
                claim_lease_id=args.get("claim_lease_id"),
                error=str(args.get("error") or ""),
            )
            try:
                result = _fail_task(task_id=task_id, body=body)
                return _mcp_ok(request_id=request_id, result=result if isinstance(result, dict) else dict(result))
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code=str(exc.status_code))

        elif tool == "block_mesh_task":
            from app.routers.work import block_task as _block_task
            task_id = str(args.get("task_id") or "")
            reason = args.get("reason")
            try:
                result = _block_task(task_id=task_id, waiting_on=reason)
                return _mcp_ok(request_id=request_id, result=result if isinstance(result, dict) else dict(result))
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code=str(exc.status_code))

        elif tool == "unblock_mesh_task":
            from app.routers.work import unblock_task as _unblock_task
            task_id = str(args.get("task_id") or "")
            try:
                result = _unblock_task(task_id=task_id)
                return _mcp_ok(request_id=request_id, result=result if isinstance(result, dict) else dict(result))
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code=str(exc.status_code))

        elif tool == "cancel_mesh_task":
            from app.routers.work import cancel_task as _cancel_task
            task_id = str(args.get("task_id") or "")
            try:
                result = _cancel_task(task_id=task_id, request=request)
                return _mcp_ok(request_id=request_id, result=result if isinstance(result, dict) else dict(result))
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code=str(exc.status_code))

        elif tool == "retry_mesh_task":
            from app.routers.work import retry_task as _retry_task
            task_id = str(args.get("task_id") or "")
            try:
                result = _retry_task(task_id=task_id, request=request)
                return _mcp_ok(request_id=request_id, result=result if isinstance(result, dict) else dict(result))
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code=str(exc.status_code))

        elif tool == "enroll_mesh_agent":
            from app.routers.work import MeshAgentEnroll, enroll_agent as _enroll_agent, _agent_to_read
            mission_id = str(args.get("mission_id") or "")
            body = MeshAgentEnroll(
                runtime_kind=str(args.get("runtime_kind") or "custom"),
                capabilities=list(args.get("capabilities") or []),
                labels=dict(args.get("labels") or {}),
            )
            try:
                result = _enroll_agent(mission_id=mission_id, body=body, request=request)
                return _mcp_ok(request_id=request_id, result={"agent": result if isinstance(result, dict) else dict(result)})
            except HTTPException as exc:
                return _mcp_error(request_id=request_id, error=str(exc.detail), error_code=str(exc.status_code))

        elif tool == "list_mesh_agents":
            from app.routers.work import _agent_to_read
            mission_id = str(args.get("mission_id") or "")
            with get_session() as _s:
                # Intentionally no owner_subject filter: agents are shared within a mission
                # and visible to any authenticated participant. This matches the REST endpoint
                # GET /work/missions/{mission_id}/agents in work.py which also omits subject scoping.
                agents = _s.exec(select(MeshAgent).where(MeshAgent.mission_id == mission_id)).all()
                return _mcp_ok(request_id=request_id, result={"agents": [_agent_to_read(a) for a in agents]})

        elif tool == "send_mesh_message":
            import json as _json
            from datetime import datetime as _dt
            kluster_id = args.get("kluster_id")
            mission_id_msg = args.get("mission_id")
            content = str(args.get("content") or "")
            message_type = str(args.get("message_type") or "info")
            with get_session() as _s:
                if kluster_id:
                    kluster = _s.get(Kluster, kluster_id)
                    if kluster is None:
                        return _mcp_error(request_id=request_id, error="kluster not found", error_code="not_found")
                    msg = MeshMessage(
                        mission_id=kluster.mission_id or "",
                        kluster_id=kluster_id,
                        from_agent_id=actor_subject,
                        channel=message_type,
                        body_json=_json.dumps({"content": content}),
                        created_at=_dt.utcnow(),
                    )
                elif mission_id_msg:
                    msg = MeshMessage(
                        mission_id=mission_id_msg,
                        kluster_id=None,
                        from_agent_id=actor_subject,
                        channel=message_type,
                        body_json=_json.dumps({"content": content}),
                        created_at=_dt.utcnow(),
                    )
                else:
                    return _mcp_error(request_id=request_id, error="kluster_id or mission_id required")
                _s.add(msg)
                _s.commit()
                _s.refresh(msg)
                return _mcp_ok(request_id=request_id, result={"id": msg.id, "created_at": msg.created_at})

        elif tool == "list_mesh_messages":
            import json as _json
            agent_id = str(args.get("agent_id") or "")
            since_id = int(args.get("since_id") or 0)
            with get_session() as _s:
                q = (
                    select(MeshMessage)
                    .where(
                        (MeshMessage.to_agent_id == agent_id) | (MeshMessage.to_agent_id == None)
                    )
                    .where(MeshMessage.id > since_id)
                    .order_by(MeshMessage.id)
                    .limit(100)
                )
                msgs = _s.exec(q).all()
                return _mcp_ok(request_id=request_id, result={"messages": [
                    {
                        "id": m.id,
                        "mission_id": m.mission_id,
                        "kluster_id": m.kluster_id,
                        "from_agent_id": m.from_agent_id,
                        "to_agent_id": m.to_agent_id,
                        "task_id": m.task_id,
                        "channel": m.channel,
                        "body_json": _json.loads(m.body_json or "{}"),
                        "in_reply_to": m.in_reply_to,
                        "created_at": m.created_at,
                        "read_at": m.read_at,
                    }
                    for m in msgs
                ]})

        if tool == "list_mission_packs":
            from app.models import MissionPack as _MissionPack
            packs = session.exec(
                select(_MissionPack).where(_MissionPack.owner_subject == actor_subject)
            ).all()
            return _mcp_ok(request_id=request_id, result={"packs": [
                {"id": p.id, "name": p.name, "version": p.version, "sha256": p.sha256[:8], "created_at": p.created_at}
                for p in packs
            ]})

        if tool == "export_mission_pack":
            mission_id = str(args.get("mission_id") or "").strip()
            if not mission_id:
                return _mcp_error(request_id=request_id, error="mission_id is required")
            from app.services.mission_pack import pack_mission as _pack_mission
            try:
                pack = _pack_mission(mission_id, actor_subject)
                return _mcp_ok(request_id=request_id, result={"pack_id": pack.id, "name": pack.name, "sha256": pack.sha256})
            except ValueError as e:
                return _mcp_error(request_id=request_id, error=str(e), error_code="not_found")

        if tool == "install_mission_pack":
            pack_id = str(args.get("pack_id") or "").strip()
            if not pack_id:
                return _mcp_error(request_id=request_id, error="pack_id is required")
            target_mission_id = args.get("target_mission_id") or None
            from app.services.mission_pack import install_mission_pack as _install_pack
            try:
                result = _install_pack(pack_id, actor_subject, target_mission_id)
                return _mcp_ok(request_id=request_id, result=result)
            except ValueError as e:
                return _mcp_error(request_id=request_id, error=str(e), error_code="not_found")

    return _mcp_error(request_id=request_id, error=f"Unknown tool: {tool}", error_code="unknown_tool")
