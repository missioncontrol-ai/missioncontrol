use axum::{
    extract::State,
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use chrono::Utc;
use serde::Deserialize;
use serde_json::{json, Value};
use sqlx::Row;
use std::sync::Arc;

use crate::{auth::Principal, state::AppState};

pub fn router() -> Router<Arc<AppState>> {
    Router::new()
        .route("/mcp/tools", get(list_tools))
        .route("/mcp/health", get(mcp_health))
        .route("/mcp/call", post(call_tool))
}

async fn mcp_health() -> impl IntoResponse {
    Json(json!({"ok": true, "version": "mc-server"}))
}

fn tool_def(name: &str, description: &str, schema: Value) -> Value {
    json!({"name": name, "description": description, "inputSchema": schema})
}

async fn list_tools() -> impl IntoResponse {
    let tools = vec![
        tool_def("search_klusters", "Search klusters by name or tag", json!({"type":"object","properties":{"query":{"type":"string"}}})),
        tool_def("search_tasks", "Search tasks by semantic similarity", json!({"type":"object","properties":{"query":{"type":"string"},"limit":{"type":"integer"}}})),
        tool_def("read_doc", "Read a doc by id", json!({"type":"object","properties":{"doc_id":{"type":"integer"}}})),
        tool_def("create_doc", "Create a doc in a kluster", json!({"type":"object","properties":{"kluster_id":{"type":"string"},"title":{"type":"string"},"body":{"type":"string"},"doc_type":{"type":"string"},"status":{"type":"string"},"provenance":{"type":"string"}}})),
        tool_def("create_artifact", "Create an artifact in a kluster", json!({"type":"object","properties":{"kluster_id":{"type":"string"},"name":{"type":"string"},"artifact_type":{"type":"string"},"uri":{"type":"string"},"content_b64":{"type":"string"},"storage_backend":{"type":"string"},"storage_class":{"type":"string"},"content_sha256":{"type":"string"},"size_bytes":{"type":"integer"},"mime_type":{"type":"string"},"external_pointer":{"type":"boolean"},"external_uri":{"type":"string"},"status":{"type":"string"},"provenance":{"type":"string"}}})),
        tool_def("get_artifact_download_url", "Get a short-lived download URL for an S3-backed artifact", json!({"type":"object","properties":{"artifact_id":{"type":"integer"},"expires_seconds":{"type":"integer"}}})),
        tool_def("load_kluster_workspace", "Load/sync a kluster workspace and acquire a lease", json!({"type":"object","properties":{"kluster_id":{"type":"string"},"workspace_label":{"type":"string"},"agent_id":{"type":"string"},"lease_seconds":{"type":"integer"}},"required":["kluster_id"]})),
        tool_def("heartbeat_workspace_lease", "Extend a workspace lease heartbeat", json!({"type":"object","properties":{"lease_id":{"type":"string"}},"required":["lease_id"]})),
        tool_def("fetch_workspace_artifact", "Fetch artifact bytes or signed download URL while a lease is active", json!({"type":"object","properties":{"lease_id":{"type":"string"},"artifact_id":{"type":"integer"},"mode":{"type":"string"},"expires_seconds":{"type":"integer"}},"required":["lease_id","artifact_id"]})),
        tool_def("commit_kluster_workspace", "Commit workspace changes with optimistic conflict checks", json!({"type":"object","properties":{"lease_id":{"type":"string"},"change_set":{"type":"array"},"validation_mode":{"type":"string"}},"required":["lease_id","change_set"]})),
        tool_def("release_kluster_workspace", "Release an active workspace lease", json!({"type":"object","properties":{"lease_id":{"type":"string"},"reason":{"type":"string"}},"required":["lease_id"]})),
        tool_def("create_task", "Create a task in a kluster", json!({"type":"object","properties":{"kluster_id":{"type":"string"},"title":{"type":"string"},"description":{"type":"string"},"owner":{"type":"string"}}})),
        tool_def("claim_task", "Atomically claim a proposed task. Sets status to in_progress and assigns ownership to the caller.", json!({"type":"object","properties":{"task_id":{"type":"string"}},"required":["task_id"]})),
        tool_def("update_task", "Update a task by id. Pass expected_status to guard against concurrent overwrites.", json!({"type":"object","properties":{"task_id":{"type":"string"},"title":{"type":"string"},"description":{"type":"string"},"status":{"type":"string"},"owner":{"type":"string"},"contributors":{"type":"string"},"dependencies":{"type":"string"},"definition_of_done":{"type":"string"},"related_artifacts":{"type":"string"},"expected_status":{"type":"string"}}})),
        tool_def("delete_task", "Delete a task by id", json!({"type":"object","properties":{"task_id":{"type":"string"}}})),
        tool_def("list_tasks", "List tasks for a kluster", json!({"type":"object","properties":{"kluster_id":{"type":"string"}}})),
        tool_def("list_pending_ledger_events", "List pending ledger events (default mission-scoped)", json!({"type":"object","properties":{"mission_id":{"type":"string"},"limit":{"type":"integer"}}})),
        tool_def("publish_pending_ledger_events", "Publish pending mission-scoped ledger events to Git", json!({"type":"object","properties":{"mission_id":{"type":"string"}}})),
        tool_def("list_repo_bindings", "List configured repository bindings for current principal", json!({"type":"object","properties":{}})),
        tool_def("provision_mission_persistence", "Create/update connection, binding, and mission policy routes in one call", json!({"type":"object","properties":{"mission_id":{"type":"string"}}})),
        tool_def("resolve_publish_plan", "Resolve publish route (binding/repo/branch/path) for an entity", json!({"type":"object","properties":{"entity_type":{"type":"string"},"entity_id":{"type":"string"},"mission_id":{"type":"string"}}})),
        tool_def("get_publication_status", "List recent publication records", json!({"type":"object","properties":{"mission_id":{"type":"string"},"limit":{"type":"integer"}}})),
        tool_def("get_entity_history", "Get ledger event history for an entity", json!({"type":"object","properties":{"entity_type":{"type":"string"},"entity_id":{"type":"string"},"mission_id":{"type":"string"}}})),
        tool_def("create_mission", "Create a mission", json!({"type":"object","properties":{"name":{"type":"string"},"description":{"type":"string"}}})),
        tool_def("list_missions", "List missions readable by current principal", json!({"type":"object","properties":{"limit":{"type":"integer"}}})),
        tool_def("create_kluster", "Create a kluster in a mission", json!({"type":"object","properties":{"mission_id":{"type":"string"},"name":{"type":"string"},"description":{"type":"string"}}})),
        tool_def("update_kluster", "Update a kluster by id", json!({"type":"object","properties":{"kluster_id":{"type":"string"},"name":{"type":"string"},"description":{"type":"string"},"status":{"type":"string"}}})),
        tool_def("delete_kluster", "Delete a kluster by id", json!({"type":"object","properties":{"kluster_id":{"type":"string"}}})),
        tool_def("update_mission", "Update a mission by id", json!({"type":"object","properties":{"mission_id":{"type":"string"},"name":{"type":"string"},"description":{"type":"string"},"status":{"type":"string"}}})),
        tool_def("delete_mission", "Delete a mission by id", json!({"type":"object","properties":{"mission_id":{"type":"string"}}})),
        tool_def("update_doc", "Update a doc by id", json!({"type":"object","properties":{"doc_id":{"type":"integer"},"title":{"type":"string"},"body":{"type":"string"},"status":{"type":"string"}}})),
        tool_def("update_artifact", "Update an artifact by id", json!({"type":"object","properties":{"artifact_id":{"type":"integer"},"name":{"type":"string"},"status":{"type":"string"}}})),
        tool_def("get_overlap_suggestions", "Get overlap suggestions for a task", json!({"type":"object","properties":{"task_id":{"type":"string"},"limit":{"type":"integer"}}})),
        tool_def("register_agent", "Register a new agent", json!({"type":"object","properties":{"name":{"type":"string"},"capabilities":{"type":"string"}}})),
        tool_def("list_agents", "List registered agents", json!({"type":"object","properties":{"mission_id":{"type":"string"}}})),
        tool_def("get_agent", "Get agent by id", json!({"type":"object","properties":{"agent_id":{"type":"string"}}})),
        tool_def("update_agent_status", "Update agent status (online/offline)", json!({"type":"object","properties":{"agent_id":{"type":"string"},"status":{"type":"string"}}})),
        tool_def("start_agent_session", "Start an agent session (brings agent online)", json!({"type":"object","properties":{"agent_id":{"type":"string"},"context":{"type":"string"},"claude_session_id":{"type":"string"}}})),
        tool_def("end_agent_session", "End an agent session (brings agent offline)", json!({"type":"object","properties":{"agent_id":{"type":"string"},"session_id":{"type":"string"}}})),
        tool_def("release_task", "Release a claimed task back to available", json!({"type":"object","properties":{"task_id":{"type":"string"}}})),
        tool_def("list_task_assignments", "List task assignments", json!({"type":"object","properties":{"agent_id":{"type":"string"},"kluster_id":{"type":"string"}}})),
        tool_def("resolve_skill_snapshot", "Resolve effective mission+kluster skill snapshot", json!({"type":"object","properties":{"mission_id":{"type":"string"},"kluster_id":{"type":"string"}}})),
        tool_def("download_skill_snapshot", "Download a resolved skill snapshot tarball", json!({"type":"object","properties":{"mission_id":{"type":"string"},"kluster_id":{"type":"string"}}})),
        tool_def("get_skill_sync_status", "Read last recorded local skill sync state", json!({"type":"object","properties":{"mission_id":{"type":"string"},"kluster_id":{"type":"string"}}})),
        tool_def("ack_skill_sync", "Record local sync/apply status for mission/kluster skill snapshot", json!({"type":"object","properties":{"mission_id":{"type":"string"},"kluster_id":{"type":"string"},"snapshot_id":{"type":"string"},"sha256":{"type":"string"}}})),
        tool_def("promote_local_skill_overlay", "Mark local overlay as promoted for current mission/kluster state", json!({"type":"object","properties":{"mission_id":{"type":"string"},"kluster_id":{"type":"string"}}})),
        tool_def("list_profiles", "List profiles for the current principal", json!({"type":"object","properties":{}})),
        tool_def("get_profile", "Get profile metadata by name", json!({"type":"object","properties":{"name":{"type":"string"}}})),
        tool_def("publish_profile", "Create or replace a profile bundle", json!({"type":"object","properties":{"name":{"type":"string"},"description":{"type":"string"},"tarball_b64":{"type":"string"}}})),
        tool_def("download_profile", "Download profile bundle by name", json!({"type":"object","properties":{"name":{"type":"string"}}})),
        tool_def("activate_profile", "Set profile as default", json!({"type":"object","properties":{"name":{"type":"string"}}})),
        tool_def("delete_profile", "Delete a profile by name", json!({"type":"object","properties":{"name":{"type":"string"}}})),
        tool_def("profile_status", "Read current profile sha and optional expected-sha match", json!({"type":"object","properties":{"name":{"type":"string"},"expected_sha256":{"type":"string"}}})),
        tool_def("pin_profile_version", "Assert profile sha matches a pinned version", json!({"type":"object","properties":{"name":{"type":"string"},"sha256":{"type":"string"}}})),
        tool_def("register_remote_target", "Register a remote SSH or K8s host as a named target for agent launches", json!({"type":"object","properties":{"name":{"type":"string"},"host":{"type":"string"},"kind":{"type":"string"}}})),
        tool_def("list_remote_targets", "List registered remote targets for the current user", json!({"type":"object","properties":{}})),
        tool_def("delete_remote_target", "Delete a registered remote target by ID", json!({"type":"object","properties":{"target_id":{"type":"string"}}})),
        tool_def("create_remote_launch", "Launch an agent process on a registered remote target", json!({"type":"object","properties":{"target_id":{"type":"string"},"mission_id":{"type":"string"},"kluster_id":{"type":"string"},"runtime_kind":{"type":"string"},"prompt":{"type":"string"}}})),
        tool_def("list_remote_launches", "List remote agent launches, optionally filtered by status", json!({"type":"object","properties":{"status":{"type":"string"},"limit":{"type":"integer"}}})),
        tool_def("get_remote_launch", "Get status, heartbeat age, and log tail for a specific remote launch", json!({"type":"object","properties":{"launch_id":{"type":"string"}}})),
        tool_def("kill_remote_launch", "Kill a running remote launch: revokes its session token and marks it failed", json!({"type":"object","properties":{"launch_id":{"type":"string"}}})),
        tool_def("submit_mesh_task", "Create a task in a kluster (mesh work model)", json!({"type":"object","properties":{"kluster_id":{"type":"string"},"title":{"type":"string"},"description":{"type":"string"},"kind":{"type":"string"},"input_json":{"type":"string"},"priority":{"type":"integer"},"mission_id":{"type":"string"}}})),
        tool_def("list_mesh_tasks", "List tasks in a kluster (mesh work model)", json!({"type":"object","properties":{"kluster_id":{"type":"string"},"status":{"type":"string"},"limit":{"type":"integer"}}})),
        tool_def("get_mesh_task", "Get a single mesh task by ID", json!({"type":"object","properties":{"task_id":{"type":"string"}}})),
        tool_def("claim_mesh_task", "Claim a mesh task for an agent; returns claim_lease_id", json!({"type":"object","properties":{"task_id":{"type":"string"},"agent_id":{"type":"string"},"lease_seconds":{"type":"integer"}}})),
        tool_def("heartbeat_mesh_task", "Renew a mesh task lease to prevent expiry", json!({"type":"object","properties":{"task_id":{"type":"string"},"claim_lease_id":{"type":"string"}}})),
        tool_def("progress_mesh_task", "Post a typed progress event for a mesh task", json!({"type":"object","properties":{"task_id":{"type":"string"},"claim_lease_id":{"type":"string"},"event_type":{"type":"string"},"payload_json":{"type":"string"}}})),
        tool_def("complete_mesh_task", "Mark a mesh task as complete", json!({"type":"object","properties":{"task_id":{"type":"string"},"claim_lease_id":{"type":"string"},"output_json":{"type":"string"}}})),
        tool_def("fail_mesh_task", "Mark a mesh task as failed", json!({"type":"object","properties":{"task_id":{"type":"string"},"claim_lease_id":{"type":"string"},"error":{"type":"string"}}})),
        tool_def("block_mesh_task", "Mark a mesh task as blocked", json!({"type":"object","properties":{"task_id":{"type":"string"},"claim_lease_id":{"type":"string"},"reason":{"type":"string"}}})),
        tool_def("unblock_mesh_task", "Unblock a mesh task (set back to ready)", json!({"type":"object","properties":{"task_id":{"type":"string"}}})),
        tool_def("cancel_mesh_task", "Cancel a mesh task", json!({"type":"object","properties":{"task_id":{"type":"string"}}})),
        tool_def("retry_mesh_task", "Retry a failed or cancelled mesh task", json!({"type":"object","properties":{"task_id":{"type":"string"}}})),
        tool_def("enroll_mesh_agent", "Enroll an agent in a mission (mesh work model)", json!({"type":"object","properties":{"mission_id":{"type":"string"},"agent_id":{"type":"string"},"capabilities_json":{"type":"string"}}})),
        tool_def("list_mesh_agents", "List agents enrolled in a mission", json!({"type":"object","properties":{"mission_id":{"type":"string"}}})),
        tool_def("send_mesh_message", "Send a message in a kluster or mission channel", json!({"type":"object","properties":{"kluster_id":{"type":"string"},"mission_id":{"type":"string"},"content":{"type":"string"},"sender_agent_id":{"type":"string"},"recipient_agent_id":{"type":"string"}}})),
        tool_def("list_mesh_messages", "List messages for an agent inbox", json!({"type":"object","properties":{"agent_id":{"type":"string"},"kluster_id":{"type":"string"},"limit":{"type":"integer"}}})),
        tool_def("list_mission_packs", "List mission packs owned by the current principal", json!({"type":"object","properties":{"limit":{"type":"integer"}}})),
        tool_def("export_mission_pack", "Export a mission into a portable mission pack tarball", json!({"type":"object","properties":{"mission_id":{"type":"string"},"name":{"type":"string"}}})),
        tool_def("install_mission_pack", "Install a mission pack, creating mission + klusters + skills + budgets", json!({"type":"object","properties":{"pack_id":{"type":"string"},"name_override":{"type":"string"}}})),
    ];
    Json(tools)
}

#[derive(Deserialize)]
struct McpCallRequest {
    tool: String,
    args: Option<Value>,
}

fn ok_result(result: Value) -> Value {
    json!({"ok": true, "result": result})
}

fn err_result(error: &str) -> Value {
    json!({"ok": false, "error": error, "result": {}})
}

fn not_impl() -> Value {
    err_result("not_implemented_in_rust_server")
}

async fn call_tool(
    State(state): State<Arc<AppState>>,
    principal: Principal,
    Json(payload): Json<McpCallRequest>,
) -> impl IntoResponse {
    let args = payload.args.unwrap_or(json!({}));
    let result = dispatch(&state, &principal, &payload.tool, &args).await;
    Json(result)
}

async fn dispatch(
    state: &AppState,
    principal: &Principal,
    tool: &str,
    args: &Value,
) -> Value {
    let now = Utc::now().naive_utc();

    match tool {
        // ── Tasks ─────────────────────────────────────────────────────────────

        "create_task" => {
            let kluster_id = str_arg(args, "kluster_id");
            let title = str_arg(args, "title");
            if kluster_id.is_empty() || title.is_empty() {
                return err_result("kluster_id and title are required");
            }
            let description = str_arg(args, "description");
            let owner = str_arg_or(args, "owner", &principal.subject);
            match sqlx::query(
                "INSERT INTO task (kluster_id, title, description, owner, status, created_at, updated_at) \
                 VALUES ($1,$2,$3,$4,'proposed',$5,$5) RETURNING id",
            )
            .bind(&kluster_id)
            .bind(&title)
            .bind(&description)
            .bind(&owner)
            .bind(now)
            .fetch_one(&state.db)
            .await
            {
                Ok(row) => {
                    let id: i32 = row.get("id");
                    ok_result(json!({"id": id, "kluster_id": kluster_id, "title": title, "description": description, "owner": owner, "status": "proposed"}))
                }
                Err(e) => {
                    tracing::error!("mcp create_task: {e}");
                    err_result("database_error")
                }
            }
        }

        "list_tasks" => {
            let kluster_id = str_arg(args, "kluster_id");
            if kluster_id.is_empty() {
                return err_result("kluster_id is required");
            }
            match sqlx::query(
                "SELECT id, kluster_id, title, description, status, owner, created_at, updated_at \
                 FROM task WHERE kluster_id=$1 ORDER BY created_at DESC LIMIT 200",
            )
            .bind(&kluster_id)
            .fetch_all(&state.db)
            .await
            {
                Ok(rows) => ok_result(Value::Array(rows.iter().map(|r| task_row_to_json(r)).collect())),
                Err(e) => { tracing::error!("mcp list_tasks: {e}"); err_result("database_error") }
            }
        }

        "update_task" => {
            let task_id = str_arg(args, "task_id");
            if task_id.is_empty() { return err_result("task_id is required"); }

            let row = sqlx::query("SELECT id, status FROM task WHERE id=$1 OR public_id=$1")
                .bind(&task_id)
                .fetch_optional(&state.db)
                .await;
            let row = match row {
                Ok(Some(r)) => r,
                Ok(None) => return err_result("task_not_found"),
                Err(e) => { tracing::error!("mcp update_task lookup: {e}"); return err_result("database_error"); }
            };
            let db_id: i32 = row.get("id");
            let current_status: String = row.get("status");

            if let Some(exp) = args.get("expected_status").and_then(|v| v.as_str()) {
                if exp != current_status {
                    return json!({"ok": false, "error": "conflict", "current_status": current_status, "expected_status": exp});
                }
            }

            let title = args.get("title").and_then(|v| v.as_str());
            let description = args.get("description").and_then(|v| v.as_str());
            let status = args.get("status").and_then(|v| v.as_str());
            let owner = args.get("owner").and_then(|v| v.as_str());

            let mut parts: Vec<String> = vec!["updated_at = NOW()".to_string()];
            if title.is_some() { parts.push("title = $2".to_string()); }
            if description.is_some() { parts.push(format!("description = ${}", parts.len() + 2)); }
            if status.is_some() { parts.push(format!("status = ${}", parts.len() + 2)); }
            if owner.is_some() { parts.push(format!("owner = ${}", parts.len() + 2)); }


            let sql = format!("UPDATE task SET {} WHERE id=$1", parts.join(", "));
            let mut q = sqlx::query(&sql).bind(db_id);
            if let Some(v) = title { q = q.bind(v); }
            if let Some(v) = description { q = q.bind(v); }
            if let Some(v) = status { q = q.bind(v); }
            if let Some(v) = owner { q = q.bind(v); }

            match q.execute(&state.db).await {
                Ok(_) => ok_result(json!({"id": db_id, "updated": true})),
                Err(e) => { tracing::error!("mcp update_task: {e}"); err_result("database_error") }
            }
        }

        "delete_task" => {
            let task_id = str_arg(args, "task_id");
            if task_id.is_empty() { return err_result("task_id is required"); }
            match sqlx::query("DELETE FROM task WHERE id=$1 OR public_id=$1")
                .bind(&task_id)
                .execute(&state.db)
                .await
            {
                Ok(r) if r.rows_affected() == 0 => err_result("task_not_found"),
                Ok(_) => ok_result(json!({"deleted": true})),
                Err(e) => { tracing::error!("mcp delete_task: {e}"); err_result("database_error") }
            }
        }

        "claim_task" => {
            let task_id = str_arg(args, "task_id");
            if task_id.is_empty() { return err_result("task_id is required"); }
            match sqlx::query(
                "UPDATE task SET status='in_progress', owner=$2, updated_at=NOW() \
                 WHERE (id=$1::integer OR public_id=$1) AND status='proposed' RETURNING id",
            )
            .bind(&task_id)
            .bind(&principal.subject)
            .fetch_optional(&state.db)
            .await
            {
                Ok(Some(row)) => ok_result(json!({"id": row.get::<i32,_>("id"), "status": "in_progress", "owner": principal.subject})),
                Ok(None) => json!({"ok": false, "error": "conflict", "detail": "task not found or already claimed"}),
                Err(e) => { tracing::error!("mcp claim_task: {e}"); err_result("database_error") }
            }
        }

        // ── Missions ──────────────────────────────────────────────────────────

        "create_mission" => {
            let name = str_arg(args, "name");
            if name.is_empty() { return err_result("name is required"); }
            let description = str_arg(args, "description");
            let id = format!("m_{}", &uuid::Uuid::new_v4().to_string().replace("-", "")[..12]);
            match sqlx::query(
                "INSERT INTO mission (id, name, description, status, owner_subject, created_at, updated_at) \
                 VALUES ($1,$2,$3,'active',$4,$5,$5)"
            )
            .bind(&id).bind(&name).bind(&description).bind(&principal.subject).bind(now)
            .execute(&state.db).await
            {
                Ok(_) => ok_result(json!({"id": id, "name": name, "description": description, "status": "active"})),
                Err(e) => { tracing::error!("mcp create_mission: {e}"); err_result("database_error") }
            }
        }

        "list_missions" => {
            let limit = int_arg(args, "limit").unwrap_or(50).min(200);
            match sqlx::query(
                "SELECT m.id, m.name, m.description, m.status, m.created_at, m.updated_at \
                 FROM mission m \
                 LEFT JOIN missionrolemembership mrm ON mrm.mission_id = m.id AND mrm.subject = $1 \
                 WHERE m.owner_subject = $1 OR mrm.subject IS NOT NULL \
                 ORDER BY m.updated_at DESC LIMIT $2",
            )
            .bind(&principal.subject)
            .bind(limit as i64)
            .fetch_all(&state.db)
            .await
            {
                Ok(rows) => ok_result(Value::Array(rows.iter().map(|r| json!({
                    "id": r.get::<String,_>("id"),
                    "name": r.get::<String,_>("name"),
                    "description": r.get::<String,_>("description"),
                    "status": r.get::<String,_>("status"),
                    "created_at": r.get::<chrono::NaiveDateTime,_>("created_at"),
                    "updated_at": r.get::<chrono::NaiveDateTime,_>("updated_at"),
                })).collect())),
                Err(e) => { tracing::error!("mcp list_missions: {e}"); err_result("database_error") }
            }
        }

        "update_mission" => {
            let mission_id = str_arg(args, "mission_id");
            if mission_id.is_empty() { return err_result("mission_id is required"); }
            let name = args.get("name").and_then(|v| v.as_str());
            let description = args.get("description").and_then(|v| v.as_str());
            let status = args.get("status").and_then(|v| v.as_str());
            let mut parts = vec!["updated_at = NOW()".to_string()];
            if name.is_some() { parts.push("name = $2".to_string()); }
            if description.is_some() { parts.push(format!("description = ${}", parts.len() + 2)); }
            if status.is_some() { parts.push(format!("status = ${}", parts.len() + 2)); }
            let sql = format!("UPDATE mission SET {} WHERE id=$1", parts.join(", "));
            let mut q = sqlx::query(&sql).bind(&mission_id);
            if let Some(v) = name { q = q.bind(v); }
            if let Some(v) = description { q = q.bind(v); }
            if let Some(v) = status { q = q.bind(v); }
            match q.execute(&state.db).await {
                Ok(r) if r.rows_affected() == 0 => err_result("mission_not_found"),
                Ok(_) => ok_result(json!({"id": mission_id, "updated": true})),
                Err(e) => { tracing::error!("mcp update_mission: {e}"); err_result("database_error") }
            }
        }

        "delete_mission" => {
            let mission_id = str_arg(args, "mission_id");
            if mission_id.is_empty() { return err_result("mission_id is required"); }
            match sqlx::query("DELETE FROM mission WHERE id=$1")
                .bind(&mission_id).execute(&state.db).await
            {
                Ok(r) if r.rows_affected() == 0 => err_result("mission_not_found"),
                Ok(_) => ok_result(json!({"deleted": true})),
                Err(e) => { tracing::error!("mcp delete_mission: {e}"); err_result("database_error") }
            }
        }

        // ── Klusters ──────────────────────────────────────────────────────────

        "create_kluster" => {
            let mission_id = str_arg(args, "mission_id");
            let name = str_arg(args, "name");
            if mission_id.is_empty() || name.is_empty() { return err_result("mission_id and name are required"); }
            let description = str_arg(args, "description");
            let id = format!("k_{}", &uuid::Uuid::new_v4().to_string().replace("-", "")[..12]);
            match sqlx::query(
                "INSERT INTO kluster (id, mission_id, name, description, status, created_at, updated_at) \
                 VALUES ($1,$2,$3,$4,'active',$5,$5)"
            )
            .bind(&id).bind(&mission_id).bind(&name).bind(&description).bind(now)
            .execute(&state.db).await
            {
                Ok(_) => ok_result(json!({"id": id, "mission_id": mission_id, "name": name, "description": description, "status": "active"})),
                Err(e) => { tracing::error!("mcp create_kluster: {e}"); err_result("database_error") }
            }
        }

        "update_kluster" => {
            let kluster_id = str_arg(args, "kluster_id");
            if kluster_id.is_empty() { return err_result("kluster_id is required"); }
            let name = args.get("name").and_then(|v| v.as_str());
            let description = args.get("description").and_then(|v| v.as_str());
            let status = args.get("status").and_then(|v| v.as_str());
            let mut parts = vec!["updated_at = NOW()".to_string()];
            if name.is_some() { parts.push("name = $2".to_string()); }
            if description.is_some() { parts.push(format!("description = ${}", parts.len() + 2)); }
            if status.is_some() { parts.push(format!("status = ${}", parts.len() + 2)); }
            let sql = format!("UPDATE kluster SET {} WHERE id=$1", parts.join(", "));
            let mut q = sqlx::query(&sql).bind(&kluster_id);
            if let Some(v) = name { q = q.bind(v); }
            if let Some(v) = description { q = q.bind(v); }
            if let Some(v) = status { q = q.bind(v); }
            match q.execute(&state.db).await {
                Ok(r) if r.rows_affected() == 0 => err_result("kluster_not_found"),
                Ok(_) => ok_result(json!({"id": kluster_id, "updated": true})),
                Err(e) => { tracing::error!("mcp update_kluster: {e}"); err_result("database_error") }
            }
        }

        "delete_kluster" => {
            let kluster_id = str_arg(args, "kluster_id");
            if kluster_id.is_empty() { return err_result("kluster_id is required"); }
            match sqlx::query("DELETE FROM kluster WHERE id=$1")
                .bind(&kluster_id).execute(&state.db).await
            {
                Ok(r) if r.rows_affected() == 0 => err_result("kluster_not_found"),
                Ok(_) => ok_result(json!({"deleted": true})),
                Err(e) => { tracing::error!("mcp delete_kluster: {e}"); err_result("database_error") }
            }
        }

        // ── Docs ──────────────────────────────────────────────────────────────

        "read_doc" => {
            let doc_id = int_arg(args, "doc_id");
            let doc_id = match doc_id { Some(v) => v, None => return err_result("doc_id is required") };
            match sqlx::query(
                "SELECT id, kluster_id, title, body, doc_type, status, provenance, created_at, updated_at FROM doc WHERE id=$1"
            )
            .bind(doc_id as i32)
            .fetch_optional(&state.db)
            .await
            {
                Ok(Some(r)) => ok_result(json!({
                    "id": r.get::<i32,_>("id"),
                    "kluster_id": r.get::<String,_>("kluster_id"),
                    "title": r.get::<String,_>("title"),
                    "body": r.get::<String,_>("body"),
                    "doc_type": r.get::<String,_>("doc_type"),
                    "status": r.get::<String,_>("status"),
                    "provenance": r.get::<String,_>("provenance"),
                    "created_at": r.get::<chrono::NaiveDateTime,_>("created_at"),
                    "updated_at": r.get::<chrono::NaiveDateTime,_>("updated_at"),
                })),
                Ok(None) => err_result("doc_not_found"),
                Err(e) => { tracing::error!("mcp read_doc: {e}"); err_result("database_error") }
            }
        }

        "create_doc" => {
            let kluster_id = str_arg(args, "kluster_id");
            let title = str_arg(args, "title");
            if kluster_id.is_empty() || title.is_empty() { return err_result("kluster_id and title are required"); }
            let body = str_arg(args, "body");
            let doc_type = str_arg_or(args, "doc_type", "note");
            let status = str_arg_or(args, "status", "active");
            let provenance = str_arg(args, "provenance");
            match sqlx::query(
                "INSERT INTO doc (kluster_id, title, body, doc_type, status, provenance, created_at, updated_at) \
                 VALUES ($1,$2,$3,$4,$5,$6,$7,$7) RETURNING id"
            )
            .bind(&kluster_id).bind(&title).bind(&body).bind(&doc_type).bind(&status).bind(&provenance).bind(now)
            .fetch_one(&state.db).await
            {
                Ok(r) => ok_result(json!({"id": r.get::<i32,_>("id"), "kluster_id": kluster_id, "title": title, "status": status})),
                Err(e) => { tracing::error!("mcp create_doc: {e}"); err_result("database_error") }
            }
        }

        "update_doc" => {
            let doc_id = match int_arg(args, "doc_id") { Some(v) => v, None => return err_result("doc_id is required") };
            let title = args.get("title").and_then(|v| v.as_str());
            let body = args.get("body").and_then(|v| v.as_str());
            let status = args.get("status").and_then(|v| v.as_str());
            let mut parts = vec!["updated_at = NOW()".to_string()];
            if title.is_some() { parts.push("title = $2".to_string()); }
            if body.is_some() { parts.push(format!("body = ${}", parts.len() + 2)); }
            if status.is_some() { parts.push(format!("status = ${}", parts.len() + 2)); }
            let sql = format!("UPDATE doc SET {} WHERE id=$1", parts.join(", "));
            let mut q = sqlx::query(&sql).bind(doc_id as i32);
            if let Some(v) = title { q = q.bind(v); }
            if let Some(v) = body { q = q.bind(v); }
            if let Some(v) = status { q = q.bind(v); }
            match q.execute(&state.db).await {
                Ok(r) if r.rows_affected() == 0 => err_result("doc_not_found"),
                Ok(_) => ok_result(json!({"id": doc_id, "updated": true})),
                Err(e) => { tracing::error!("mcp update_doc: {e}"); err_result("database_error") }
            }
        }

        // ── Agents ────────────────────────────────────────────────────────────

        "register_agent" => {
            let name = str_arg(args, "name");
            if name.is_empty() { return err_result("name is required"); }
            let capabilities = args.get("capabilities").and_then(|v| v.as_str()).unwrap_or("[]");
            match sqlx::query(
                "INSERT INTO agent (name, capabilities, status, metadata, created_at, updated_at) \
                 VALUES ($1,$2,'offline','{}',NOW(),NOW()) RETURNING id"
            )
            .bind(&name).bind(capabilities)
            .fetch_one(&state.db).await
            {
                Ok(r) => ok_result(json!({"id": r.get::<i32,_>("id"), "name": name, "status": "offline"})),
                Err(e) => { tracing::error!("mcp register_agent: {e}"); err_result("database_error") }
            }
        }

        "list_agents" => {
            match sqlx::query("SELECT id, name, status, capabilities, created_at FROM agent ORDER BY created_at DESC LIMIT 100")
                .fetch_all(&state.db).await
            {
                Ok(rows) => ok_result(Value::Array(rows.iter().map(|r| json!({
                    "id": r.get::<i32,_>("id"),
                    "name": r.get::<String,_>("name"),
                    "status": r.get::<String,_>("status"),
                    "capabilities": r.get::<String,_>("capabilities"),
                })).collect())),
                Err(e) => { tracing::error!("mcp list_agents: {e}"); err_result("database_error") }
            }
        }

        "get_agent" => {
            let agent_id = str_arg(args, "agent_id");
            if agent_id.is_empty() { return err_result("agent_id is required"); }
            let id: i32 = match agent_id.parse() { Ok(v) => v, Err(_) => return err_result("invalid agent_id") };
            match sqlx::query("SELECT id, name, status, capabilities, metadata, created_at, updated_at FROM agent WHERE id=$1")
                .bind(id).fetch_optional(&state.db).await
            {
                Ok(Some(r)) => ok_result(json!({
                    "id": r.get::<i32,_>("id"),
                    "name": r.get::<String,_>("name"),
                    "status": r.get::<String,_>("status"),
                    "capabilities": r.get::<String,_>("capabilities"),
                })),
                Ok(None) => err_result("agent_not_found"),
                Err(e) => { tracing::error!("mcp get_agent: {e}"); err_result("database_error") }
            }
        }

        "update_agent_status" => {
            let agent_id = str_arg(args, "agent_id");
            let status = str_arg(args, "status");
            if agent_id.is_empty() || status.is_empty() { return err_result("agent_id and status are required"); }
            let id: i32 = match agent_id.parse() { Ok(v) => v, Err(_) => return err_result("invalid agent_id") };
            match sqlx::query("UPDATE agent SET status=$2, updated_at=NOW() WHERE id=$1")
                .bind(id).bind(&status).execute(&state.db).await
            {
                Ok(r) if r.rows_affected() == 0 => err_result("agent_not_found"),
                Ok(_) => ok_result(json!({"id": id, "status": status})),
                Err(e) => { tracing::error!("mcp update_agent_status: {e}"); err_result("database_error") }
            }
        }

        // ── Klusters search (name-based fallback, no vectorstore) ─────────────

        "search_klusters" => {
            let query = str_arg(args, "query");
            match sqlx::query(
                "SELECT id, mission_id, name, description, status FROM kluster \
                 WHERE name ILIKE $1 OR description ILIKE $1 ORDER BY updated_at DESC LIMIT 20"
            )
            .bind(format!("%{}%", query))
            .fetch_all(&state.db).await
            {
                Ok(rows) => ok_result(Value::Array(rows.iter().map(|r| json!({
                    "id": r.get::<String,_>("id"),
                    "mission_id": r.get::<String,_>("mission_id"),
                    "name": r.get::<String,_>("name"),
                    "status": r.get::<String,_>("status"),
                })).collect())),
                Err(e) => { tracing::error!("mcp search_klusters: {e}"); err_result("database_error") }
            }
        }

        // search_tasks needs vectorstore — return SQL fallback
        "search_tasks" => {
            let query = str_arg(args, "query");
            let limit = int_arg(args, "limit").unwrap_or(10).min(50) as i64;
            match sqlx::query(
                "SELECT id, kluster_id, title, description, status, owner FROM task \
                 WHERE title ILIKE $1 OR description ILIKE $1 ORDER BY updated_at DESC LIMIT $2"
            )
            .bind(format!("%{}%", query))
            .bind(limit)
            .fetch_all(&state.db).await
            {
                Ok(rows) => ok_result(Value::Array(rows.iter().map(|r| task_row_to_json(r)).collect())),
                Err(e) => { tracing::error!("mcp search_tasks: {e}"); err_result("database_error") }
            }
        }

        // ── Tools requiring Python services ───────────────────────────────────
        "get_artifact_download_url"
        | "load_kluster_workspace"
        | "heartbeat_workspace_lease"
        | "fetch_workspace_artifact"
        | "commit_kluster_workspace"
        | "release_kluster_workspace"
        | "list_pending_ledger_events"
        | "publish_pending_ledger_events"
        | "list_repo_bindings"
        | "provision_mission_persistence"
        | "resolve_publish_plan"
        | "get_publication_status"
        | "get_entity_history"
        | "get_overlap_suggestions"
        | "start_agent_session"
        | "end_agent_session"
        | "release_task"
        | "list_task_assignments"
        | "resolve_skill_snapshot"
        | "download_skill_snapshot"
        | "get_skill_sync_status"
        | "ack_skill_sync"
        | "promote_local_skill_overlay"
        | "list_profiles"
        | "get_profile"
        | "publish_profile"
        | "download_profile"
        | "activate_profile"
        | "delete_profile"
        | "profile_status"
        | "pin_profile_version"
        | "create_artifact"
        | "update_artifact"
        | "register_remote_target"
        | "list_remote_targets"
        | "delete_remote_target"
        | "create_remote_launch"
        | "list_remote_launches"
        | "get_remote_launch"
        | "kill_remote_launch"
        | "submit_mesh_task"
        | "list_mesh_tasks"
        | "get_mesh_task"
        | "claim_mesh_task"
        | "heartbeat_mesh_task"
        | "progress_mesh_task"
        | "complete_mesh_task"
        | "fail_mesh_task"
        | "block_mesh_task"
        | "unblock_mesh_task"
        | "cancel_mesh_task"
        | "retry_mesh_task"
        | "enroll_mesh_agent"
        | "list_mesh_agents"
        | "send_mesh_message"
        | "list_mesh_messages"
        | "list_mission_packs"
        | "export_mission_pack"
        | "install_mission_pack" => not_impl(),

        _ => err_result("unknown_tool"),
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

fn str_arg<'a>(args: &'a Value, key: &str) -> String {
    args.get(key).and_then(|v| v.as_str()).unwrap_or("").to_string()
}

fn str_arg_or<'a>(args: &'a Value, key: &str, default: &str) -> String {
    let v = args.get(key).and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
    if v.is_empty() { default.to_string() } else { v }
}

fn int_arg(args: &Value, key: &str) -> Option<i64> {
    args.get(key).and_then(|v| v.as_i64())
}

fn task_row_to_json(r: &sqlx::postgres::PgRow) -> Value {
    json!({
        "id": r.get::<i32,_>("id"),
        "kluster_id": r.get::<String,_>("kluster_id"),
        "title": r.get::<String,_>("title"),
        "description": r.get::<String,_>("description"),
        "status": r.get::<String,_>("status"),
        "owner": r.get::<String,_>("owner"),
    })
}
