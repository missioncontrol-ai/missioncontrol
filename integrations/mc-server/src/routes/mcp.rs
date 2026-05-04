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

        // ── Artifacts ─────────────────────────────────────────────────────────

        "create_artifact" => {
            let kluster_id = str_arg(args, "kluster_id");
            let name = str_arg(args, "name");
            if kluster_id.is_empty() || name.is_empty() { return err_result("kluster_id and name are required"); }
            let artifact_type = str_arg_or(args, "artifact_type", "file");
            let uri = str_arg(args, "uri");
            let status = str_arg_or(args, "status", "active");
            let mime_type = str_arg(args, "mime_type");
            let size_bytes = int_arg(args, "size_bytes").unwrap_or(0);
            match sqlx::query(
                "INSERT INTO artifact (kluster_id, name, artifact_type, uri, status, mime_type, size_bytes, \
                 external_pointer, created_at, updated_at) VALUES ($1,$2,$3,$4,$5,$6,$7,false,$8,$8) RETURNING id"
            )
            .bind(&kluster_id).bind(&name).bind(&artifact_type).bind(&uri).bind(&status)
            .bind(&mime_type).bind(size_bytes as i64).bind(now)
            .fetch_one(&state.db).await
            {
                Ok(r) => ok_result(json!({"id": r.get::<i32,_>("id"), "kluster_id": kluster_id, "name": name, "status": status})),
                Err(e) => { tracing::error!("mcp create_artifact: {e}"); err_result("database_error") }
            }
        }

        "update_artifact" => {
            let artifact_id = match int_arg(args, "artifact_id") { Some(v) => v as i32, None => return err_result("artifact_id is required") };
            let name = args.get("name").and_then(|v| v.as_str());
            let status = args.get("status").and_then(|v| v.as_str());
            let mut parts = vec!["updated_at = NOW()".to_string()];
            if name.is_some() { parts.push("name = $2".to_string()); }
            if status.is_some() { parts.push(format!("status = ${}", parts.len() + 2)); }
            let sql = format!("UPDATE artifact SET {} WHERE id=$1", parts.join(", "));
            let mut q = sqlx::query(&sql).bind(artifact_id);
            if let Some(v) = name { q = q.bind(v); }
            if let Some(v) = status { q = q.bind(v); }
            match q.execute(&state.db).await {
                Ok(r) if r.rows_affected() == 0 => err_result("artifact_not_found"),
                Ok(_) => ok_result(json!({"id": artifact_id, "updated": true})),
                Err(e) => { tracing::error!("mcp update_artifact: {e}"); err_result("database_error") }
            }
        }

        // ── Overlap / assignments ─────────────────────────────────────────────

        "get_overlap_suggestions" => {
            let task_id = match int_arg(args, "task_id") { Some(v) => v as i32, None => return err_result("task_id is required") };
            let limit = int_arg(args, "limit").unwrap_or(10).min(50) as i64;
            match sqlx::query(
                "SELECT id, task_id, candidate_task_id, similarity_score, evidence, suggested_action \
                 FROM overlapsuggestion WHERE task_id=$1 ORDER BY similarity_score DESC LIMIT $2"
            )
            .bind(task_id).bind(limit).fetch_all(&state.db).await
            {
                Ok(rows) => ok_result(Value::Array(rows.iter().map(|r| json!({
                    "id": r.get::<i32,_>("id"),
                    "task_id": r.get::<i32,_>("task_id"),
                    "candidate_task_id": r.get::<i32,_>("candidate_task_id"),
                    "similarity_score": r.get::<f64,_>("similarity_score"),
                    "evidence": r.get::<String,_>("evidence"),
                    "suggested_action": r.get::<String,_>("suggested_action"),
                })).collect())),
                Err(e) => { tracing::error!("mcp get_overlap_suggestions: {e}"); err_result("database_error") }
            }
        }

        "list_task_assignments" => {
            let limit = int_arg(args, "limit").unwrap_or(50).min(200) as i64;
            let agent_id = args.get("agent_id").and_then(|v| v.as_i64());
            let kluster_id = args.get("kluster_id").and_then(|v| v.as_str());
            match sqlx::query(
                "SELECT ta.id, ta.task_id, ta.agent_id, ta.status, ta.created_at, ta.updated_at \
                 FROM taskassignment ta \
                 JOIN task t ON t.id = ta.task_id \
                 WHERE ($1::bigint IS NULL OR ta.agent_id = $1) \
                   AND ($2::text IS NULL OR t.kluster_id = $2) \
                 ORDER BY ta.updated_at DESC LIMIT $3"
            )
            .bind(agent_id).bind(kluster_id).bind(limit)
            .fetch_all(&state.db).await
            {
                Ok(rows) => ok_result(Value::Array(rows.iter().map(|r| json!({
                    "id": r.get::<i32,_>("id"),
                    "task_id": r.get::<i32,_>("task_id"),
                    "agent_id": r.get::<i32,_>("agent_id"),
                    "status": r.get::<String,_>("status"),
                })).collect())),
                Err(e) => { tracing::error!("mcp list_task_assignments: {e}"); err_result("database_error") }
            }
        }

        "release_task" => {
            let task_id = str_arg(args, "task_id");
            if task_id.is_empty() { return err_result("task_id is required"); }
            match sqlx::query(
                "UPDATE task SET status='proposed', owner='', updated_at=NOW() \
                 WHERE (id::text=$1 OR public_id=$1) AND status='in_progress'"
            )
            .bind(&task_id).execute(&state.db).await
            {
                Ok(r) if r.rows_affected() == 0 => err_result("task_not_found_or_not_in_progress"),
                Ok(_) => ok_result(json!({"released": true})),
                Err(e) => { tracing::error!("mcp release_task: {e}"); err_result("database_error") }
            }
        }

        // ── Agent sessions ────────────────────────────────────────────────────

        "start_agent_session" => {
            let agent_id = str_arg(args, "agent_id");
            if agent_id.is_empty() { return err_result("agent_id is required"); }
            let aid: i32 = match agent_id.parse() { Ok(v) => v, Err(_) => return err_result("invalid agent_id") };
            let context = str_arg(args, "context");
            let claude_session_id = args.get("claude_session_id").and_then(|v| v.as_str());
            match sqlx::query(
                "INSERT INTO agentsession (agent_id, context, claude_session_id, started_at) \
                 VALUES ($1,$2,$3,NOW()) RETURNING id"
            )
            .bind(aid).bind(&context).bind(claude_session_id)
            .fetch_one(&state.db).await
            {
                Ok(r) => {
                    let _ = sqlx::query("UPDATE agent SET status='online', updated_at=NOW() WHERE id=$1")
                        .bind(aid).execute(&state.db).await;
                    ok_result(json!({"session_id": r.get::<i32,_>("id"), "agent_id": aid, "status": "online"}))
                }
                Err(e) => { tracing::error!("mcp start_agent_session: {e}"); err_result("database_error") }
            }
        }

        "end_agent_session" => {
            let agent_id = str_arg(args, "agent_id");
            if agent_id.is_empty() { return err_result("agent_id is required"); }
            let aid: i32 = match agent_id.parse() { Ok(v) => v, Err(_) => return err_result("invalid agent_id") };
            let session_id = args.get("session_id").and_then(|v| v.as_i64());
            let q = if let Some(sid) = session_id {
                sqlx::query("UPDATE agentsession SET ended_at=NOW() WHERE id=$1 AND agent_id=$2")
                    .bind(sid as i32).bind(aid)
            } else {
                sqlx::query("UPDATE agentsession SET ended_at=NOW() WHERE agent_id=$1 AND ended_at IS NULL")
                    .bind(aid)
            };
            let _ = q.execute(&state.db).await;
            let _ = sqlx::query("UPDATE agent SET status='offline', updated_at=NOW() WHERE id=$1")
                .bind(aid).execute(&state.db).await;
            ok_result(json!({"agent_id": aid, "status": "offline"}))
        }

        // ── Repo bindings ─────────────────────────────────────────────────────

        "list_repo_bindings" => {
            match sqlx::query(
                "SELECT rb.id, rb.name, rb.owner_subject, rb.branch_override, rb.base_path, rb.active, \
                 rc.provider, rc.host, rc.repo_path, rc.default_branch \
                 FROM repobinding rb JOIN repoconnection rc ON rc.id = rb.connection_id \
                 WHERE rb.owner_subject=$1 AND rb.active=true ORDER BY rb.created_at DESC"
            )
            .bind(&principal.subject)
            .fetch_all(&state.db).await
            {
                Ok(rows) => ok_result(Value::Array(rows.iter().map(|r| json!({
                    "id": r.get::<i32,_>("id"),
                    "name": r.get::<String,_>("name"),
                    "provider": r.get::<String,_>("provider"),
                    "host": r.get::<String,_>("host"),
                    "repo_path": r.get::<String,_>("repo_path"),
                    "branch_override": r.get::<String,_>("branch_override"),
                    "base_path": r.get::<String,_>("base_path"),
                })).collect())),
                Err(e) => { tracing::error!("mcp list_repo_bindings: {e}"); err_result("database_error") }
            }
        }

        // ── Profiles ──────────────────────────────────────────────────────────

        "list_profiles" => {
            match sqlx::query(
                "SELECT id, name, description, is_default, sha256, size_bytes, created_at, updated_at \
                 FROM userprofile WHERE owner_subject=$1 ORDER BY updated_at DESC"
            )
            .bind(&principal.subject).fetch_all(&state.db).await
            {
                Ok(rows) => ok_result(Value::Array(rows.iter().map(|r| json!({
                    "id": r.get::<i32,_>("id"),
                    "name": r.get::<String,_>("name"),
                    "description": r.get::<String,_>("description"),
                    "is_default": r.get::<bool,_>("is_default"),
                    "sha256": r.get::<Option<String>,_>("sha256"),
                    "size_bytes": r.get::<i64,_>("size_bytes"),
                })).collect())),
                Err(e) => { tracing::error!("mcp list_profiles: {e}"); err_result("database_error") }
            }
        }

        "get_profile" => {
            let name = str_arg(args, "name");
            if name.is_empty() { return err_result("name is required"); }
            match sqlx::query(
                "SELECT id, name, description, is_default, sha256, size_bytes, created_at, updated_at \
                 FROM userprofile WHERE owner_subject=$1 AND name=$2"
            )
            .bind(&principal.subject).bind(&name).fetch_optional(&state.db).await
            {
                Ok(Some(r)) => ok_result(json!({
                    "id": r.get::<i32,_>("id"),
                    "name": r.get::<String,_>("name"),
                    "description": r.get::<String,_>("description"),
                    "is_default": r.get::<bool,_>("is_default"),
                    "sha256": r.get::<Option<String>,_>("sha256"),
                    "size_bytes": r.get::<i64,_>("size_bytes"),
                })),
                Ok(None) => err_result("profile_not_found"),
                Err(e) => { tracing::error!("mcp get_profile: {e}"); err_result("database_error") }
            }
        }

        "activate_profile" => {
            let name = str_arg(args, "name");
            if name.is_empty() { return err_result("name is required"); }
            let _ = sqlx::query("UPDATE userprofile SET is_default=false WHERE owner_subject=$1")
                .bind(&principal.subject).execute(&state.db).await;
            match sqlx::query(
                "UPDATE userprofile SET is_default=true, updated_at=NOW() WHERE owner_subject=$1 AND name=$2"
            )
            .bind(&principal.subject).bind(&name).execute(&state.db).await
            {
                Ok(r) if r.rows_affected() == 0 => err_result("profile_not_found"),
                Ok(_) => ok_result(json!({"name": name, "is_default": true})),
                Err(e) => { tracing::error!("mcp activate_profile: {e}"); err_result("database_error") }
            }
        }

        "delete_profile" => {
            let name = str_arg(args, "name");
            if name.is_empty() { return err_result("name is required"); }
            match sqlx::query("DELETE FROM userprofile WHERE owner_subject=$1 AND name=$2")
                .bind(&principal.subject).bind(&name).execute(&state.db).await
            {
                Ok(r) if r.rows_affected() == 0 => err_result("profile_not_found"),
                Ok(_) => ok_result(json!({"deleted": true})),
                Err(e) => { tracing::error!("mcp delete_profile: {e}"); err_result("database_error") }
            }
        }

        "profile_status" => {
            let name = str_arg(args, "name");
            if name.is_empty() { return err_result("name is required"); }
            let expected_sha = args.get("expected_sha256").and_then(|v| v.as_str());
            match sqlx::query("SELECT name, sha256, is_default FROM userprofile WHERE owner_subject=$1 AND name=$2")
                .bind(&principal.subject).bind(&name).fetch_optional(&state.db).await
            {
                Ok(Some(r)) => {
                    let sha: Option<String> = r.get("sha256");
                    let matches = expected_sha.map(|e| sha.as_deref() == Some(e));
                    ok_result(json!({"name": name, "sha256": sha, "is_default": r.get::<bool,_>("is_default"), "sha_matches": matches}))
                }
                Ok(None) => err_result("profile_not_found"),
                Err(e) => { tracing::error!("mcp profile_status: {e}"); err_result("database_error") }
            }
        }

        // ── Remote targets ────────────────────────────────────────────────────

        "register_remote_target" => {
            let name = str_arg(args, "name");
            let host = str_arg(args, "host");
            if name.is_empty() || host.is_empty() { return err_result("name and host are required"); }
            let transport = str_arg_or(args, "kind", "ssh");
            let id = uuid::Uuid::new_v4().to_string();
            match sqlx::query(
                "INSERT INTO remotetarget (id, owner_subject, name, host, transport, created_at, updated_at) \
                 VALUES ($1,$2,$3,$4,$5,$6,$6)"
            )
            .bind(&id).bind(&principal.subject).bind(&name).bind(&host).bind(&transport).bind(now)
            .execute(&state.db).await
            {
                Ok(_) => ok_result(json!({"id": id, "name": name, "host": host, "transport": transport})),
                Err(e) if e.to_string().contains("unique") || e.to_string().contains("duplicate") =>
                    err_result("target_name_already_exists"),
                Err(e) => { tracing::error!("mcp register_remote_target: {e}"); err_result("database_error") }
            }
        }

        "list_remote_targets" => {
            match sqlx::query(
                "SELECT id, name, host, transport, created_at FROM remotetarget \
                 WHERE owner_subject=$1 ORDER BY created_at DESC LIMIT 100"
            )
            .bind(&principal.subject).fetch_all(&state.db).await
            {
                Ok(rows) => ok_result(Value::Array(rows.iter().map(|r| json!({
                    "id": r.get::<String,_>("id"),
                    "name": r.get::<String,_>("name"),
                    "host": r.get::<String,_>("host"),
                    "transport": r.get::<String,_>("transport"),
                })).collect())),
                Err(e) => { tracing::error!("mcp list_remote_targets: {e}"); err_result("database_error") }
            }
        }

        "delete_remote_target" => {
            let target_id = str_arg(args, "target_id");
            if target_id.is_empty() { return err_result("target_id is required"); }
            match sqlx::query("DELETE FROM remotetarget WHERE id=$1 AND owner_subject=$2")
                .bind(&target_id).bind(&principal.subject).execute(&state.db).await
            {
                Ok(r) if r.rows_affected() == 0 => err_result("target_not_found"),
                Ok(_) => ok_result(json!({"deleted": true})),
                Err(e) => { tracing::error!("mcp delete_remote_target: {e}"); err_result("database_error") }
            }
        }

        "list_remote_launches" => {
            let status_filter = args.get("status").and_then(|v| v.as_str());
            let limit = int_arg(args, "limit").unwrap_or(20).min(100) as i64;
            match sqlx::query(
                "SELECT id, transport, target_host, agent_kind, status, created_at, updated_at \
                 FROM remotelaunchrecord WHERE owner_subject=$1 \
                 AND ($2::text IS NULL OR status=$2) \
                 ORDER BY created_at DESC LIMIT $3"
            )
            .bind(&principal.subject).bind(status_filter).bind(limit)
            .fetch_all(&state.db).await
            {
                Ok(rows) => ok_result(Value::Array(rows.iter().map(|r| json!({
                    "id": r.get::<String,_>("id"),
                    "transport": r.get::<String,_>("transport"),
                    "target_host": r.get::<String,_>("target_host"),
                    "agent_kind": r.get::<String,_>("agent_kind"),
                    "status": r.get::<String,_>("status"),
                })).collect())),
                Err(e) => { tracing::error!("mcp list_remote_launches: {e}"); err_result("database_error") }
            }
        }

        "get_remote_launch" => {
            let launch_id = str_arg(args, "launch_id");
            if launch_id.is_empty() { return err_result("launch_id is required"); }
            match sqlx::query(
                "SELECT id, transport, target_host, agent_kind, status, error_message, log_tail, \
                 created_at, updated_at, last_heartbeat_at \
                 FROM remotelaunchrecord WHERE id=$1 AND owner_subject=$2"
            )
            .bind(&launch_id).bind(&principal.subject).fetch_optional(&state.db).await
            {
                Ok(Some(r)) => ok_result(json!({
                    "id": r.get::<String,_>("id"),
                    "transport": r.get::<String,_>("transport"),
                    "target_host": r.get::<String,_>("target_host"),
                    "agent_kind": r.get::<String,_>("agent_kind"),
                    "status": r.get::<String,_>("status"),
                    "error_message": r.get::<String,_>("error_message"),
                    "log_tail": r.get::<String,_>("log_tail"),
                    "last_heartbeat_at": r.get::<Option<chrono::NaiveDateTime>,_>("last_heartbeat_at"),
                })),
                Ok(None) => err_result("launch_not_found"),
                Err(e) => { tracing::error!("mcp get_remote_launch: {e}"); err_result("database_error") }
            }
        }

        // ── Mesh tasks ────────────────────────────────────────────────────────

        "submit_mesh_task" => {
            let kluster_id = str_arg(args, "kluster_id");
            let mission_id = str_arg(args, "mission_id");
            let title = str_arg(args, "title");
            if kluster_id.is_empty() || mission_id.is_empty() || title.is_empty() {
                return err_result("kluster_id, mission_id, and title are required");
            }
            let description = str_arg(args, "description");
            let input_json = args.get("input_json").cloned().unwrap_or(json!({}));
            let priority = int_arg(args, "priority").unwrap_or(0) as i32;
            let id = uuid::Uuid::new_v4().to_string();
            match sqlx::query(
                "INSERT INTO meshtask (id, kluster_id, mission_id, title, description, input_json, \
                 priority, status, created_by_subject, created_at, updated_at) \
                 VALUES ($1,$2,$3,$4,$5,$6,$7,'ready',$8,$9,$9)"
            )
            .bind(&id).bind(&kluster_id).bind(&mission_id).bind(&title).bind(&description)
            .bind(input_json.to_string()).bind(priority).bind(&principal.subject).bind(now)
            .execute(&state.db).await
            {
                Ok(_) => ok_result(json!({"task_id": id, "kluster_id": kluster_id, "mission_id": mission_id, "title": title, "status": "ready"})),
                Err(e) => { tracing::error!("mcp submit_mesh_task: {e}"); err_result("database_error") }
            }
        }

        "list_mesh_tasks" => {
            let kluster_id = str_arg(args, "kluster_id");
            let status_filter = args.get("status").and_then(|v| v.as_str());
            let limit = int_arg(args, "limit").unwrap_or(50).min(200) as i64;
            match sqlx::query(
                "SELECT id, kluster_id, mission_id, title, description, status, priority, \
                 claimed_by_agent_id, created_at, updated_at \
                 FROM meshtask \
                 WHERE ($1::text = '' OR kluster_id=$1) \
                   AND ($2::text IS NULL OR status=$2) \
                 ORDER BY priority DESC, created_at ASC LIMIT $3"
            )
            .bind(&kluster_id).bind(status_filter).bind(limit)
            .fetch_all(&state.db).await
            {
                Ok(rows) => ok_result(Value::Array(rows.iter().map(|r| json!({
                    "id": r.get::<String,_>("id"),
                    "kluster_id": r.get::<String,_>("kluster_id"),
                    "mission_id": r.get::<String,_>("mission_id"),
                    "title": r.get::<String,_>("title"),
                    "status": r.get::<String,_>("status"),
                    "priority": r.get::<i32,_>("priority"),
                    "claimed_by_agent_id": r.get::<Option<String>,_>("claimed_by_agent_id"),
                })).collect())),
                Err(e) => { tracing::error!("mcp list_mesh_tasks: {e}"); err_result("database_error") }
            }
        }

        "get_mesh_task" => {
            let task_id = str_arg(args, "task_id");
            if task_id.is_empty() { return err_result("task_id is required"); }
            match sqlx::query(
                "SELECT id, kluster_id, mission_id, title, description, status, priority, \
                 input_json, claimed_by_agent_id, claim_lease_id, lease_expires_at, \
                 created_at, updated_at FROM meshtask WHERE id=$1"
            )
            .bind(&task_id).fetch_optional(&state.db).await
            {
                Ok(Some(r)) => ok_result(json!({
                    "id": r.get::<String,_>("id"),
                    "kluster_id": r.get::<String,_>("kluster_id"),
                    "mission_id": r.get::<String,_>("mission_id"),
                    "title": r.get::<String,_>("title"),
                    "description": r.get::<String,_>("description"),
                    "status": r.get::<String,_>("status"),
                    "priority": r.get::<i32,_>("priority"),
                    "claimed_by_agent_id": r.get::<Option<String>,_>("claimed_by_agent_id"),
                    "claim_lease_id": r.get::<Option<String>,_>("claim_lease_id"),
                    "lease_expires_at": r.get::<Option<chrono::NaiveDateTime>,_>("lease_expires_at"),
                })),
                Ok(None) => err_result("mesh_task_not_found"),
                Err(e) => { tracing::error!("mcp get_mesh_task: {e}"); err_result("database_error") }
            }
        }

        "claim_mesh_task" => {
            let task_id = str_arg(args, "task_id");
            let agent_id = str_arg(args, "agent_id");
            if task_id.is_empty() || agent_id.is_empty() { return err_result("task_id and agent_id are required"); }
            let lease_seconds = int_arg(args, "lease_seconds").unwrap_or(300);
            let lease_id = uuid::Uuid::new_v4().to_string();
            let expires_at = now + chrono::Duration::seconds(lease_seconds);
            match sqlx::query(
                "UPDATE meshtask SET status='claimed', claimed_by_agent_id=$2, claim_lease_id=$3, \
                 lease_expires_at=$4, version_counter=version_counter+1, updated_at=NOW() \
                 WHERE id=$1 AND status='ready' RETURNING id"
            )
            .bind(&task_id).bind(&agent_id).bind(&lease_id).bind(expires_at)
            .fetch_optional(&state.db).await
            {
                Ok(Some(_)) => ok_result(json!({"task_id": task_id, "claim_lease_id": lease_id, "lease_expires_at": expires_at})),
                Ok(None) => json!({"ok": false, "error": "conflict", "detail": "task not found or already claimed"}),
                Err(e) => { tracing::error!("mcp claim_mesh_task: {e}"); err_result("database_error") }
            }
        }

        "heartbeat_mesh_task" => {
            let task_id = str_arg(args, "task_id");
            let claim_lease_id = str_arg(args, "claim_lease_id");
            if task_id.is_empty() || claim_lease_id.is_empty() { return err_result("task_id and claim_lease_id are required"); }
            let expires_at = now + chrono::Duration::seconds(300);
            match sqlx::query(
                "UPDATE meshtask SET lease_expires_at=$3, updated_at=NOW() \
                 WHERE id=$1 AND claim_lease_id=$2 RETURNING id"
            )
            .bind(&task_id).bind(&claim_lease_id).bind(expires_at)
            .fetch_optional(&state.db).await
            {
                Ok(Some(_)) => ok_result(json!({"task_id": task_id, "lease_expires_at": expires_at})),
                Ok(None) => err_result("invalid_task_or_lease"),
                Err(e) => { tracing::error!("mcp heartbeat_mesh_task: {e}"); err_result("database_error") }
            }
        }

        "progress_mesh_task" => {
            let task_id = str_arg(args, "task_id");
            let agent_id = str_arg(args, "agent_id");
            let event_type = str_arg(args, "event_type");
            if task_id.is_empty() || event_type.is_empty() { return err_result("task_id and event_type are required"); }
            let payload_json = args.get("payload_json").cloned().unwrap_or(json!({}));
            let phase = args.get("phase").and_then(|v| v.as_str());
            let step = args.get("step").and_then(|v| v.as_str());
            match sqlx::query(
                "INSERT INTO meshprogressevent (task_id, agent_id, event_type, phase, step, payload_json, occurred_at) \
                 VALUES ($1,$2,$3,$4,$5,$6,NOW()) RETURNING id"
            )
            .bind(&task_id).bind(&agent_id).bind(&event_type).bind(phase).bind(step)
            .bind(payload_json.to_string())
            .fetch_one(&state.db).await
            {
                Ok(r) => ok_result(json!({"event_id": r.get::<i32,_>("id"), "task_id": task_id, "event_type": event_type})),
                Err(e) => { tracing::error!("mcp progress_mesh_task: {e}"); err_result("database_error") }
            }
        }

        "complete_mesh_task" | "fail_mesh_task" | "block_mesh_task"
        | "unblock_mesh_task" | "cancel_mesh_task" | "retry_mesh_task" => {
            let task_id = str_arg(args, "task_id");
            if task_id.is_empty() { return err_result("task_id is required"); }
            let new_status = match tool {
                "complete_mesh_task" => "finished",
                "fail_mesh_task"     => "failed",
                "block_mesh_task"    => "blocked",
                "unblock_mesh_task"  => "ready",
                "cancel_mesh_task"   => "cancelled",
                "retry_mesh_task"    => "ready",
                _                    => return err_result("unknown_tool"),
            };
            match sqlx::query(
                "UPDATE meshtask SET status=$2, updated_at=NOW(), \
                 claim_lease_id=CASE WHEN $2 IN ('finished','failed','cancelled') THEN NULL ELSE claim_lease_id END, \
                 claimed_by_agent_id=CASE WHEN $2 IN ('finished','failed','cancelled') THEN NULL ELSE claimed_by_agent_id END \
                 WHERE id=$1 RETURNING id"
            )
            .bind(&task_id).bind(new_status).fetch_optional(&state.db).await
            {
                Ok(Some(_)) => ok_result(json!({"task_id": task_id, "status": new_status})),
                Ok(None) => err_result("mesh_task_not_found"),
                Err(e) => { tracing::error!("mcp {tool}: {e}"); err_result("database_error") }
            }
        }

        // ── Mesh agents ───────────────────────────────────────────────────────

        "enroll_mesh_agent" => {
            let mission_id = str_arg(args, "mission_id");
            let agent_id_str = args.get("agent_id").and_then(|v| v.as_str()).unwrap_or("");
            if mission_id.is_empty() { return err_result("mission_id is required"); }
            // agent_id may be provided (existing agent) or we enroll a new one
            let id = if agent_id_str.is_empty() { uuid::Uuid::new_v4().to_string() } else { agent_id_str.to_string() };
            let capabilities = args.get("capabilities_json").and_then(|v| v.as_str()).unwrap_or("[]");
            let runtime_kind = str_arg_or(args, "runtime_kind", "claude_code");
            match sqlx::query(
                "INSERT INTO meshagent (id, mission_id, runtime_kind, capabilities, status, enrolled_by_subject, enrolled_at) \
                 VALUES ($1,$2,$3,$4,'offline',$5,NOW()) \
                 ON CONFLICT (id) DO UPDATE SET mission_id=$2, status='offline', enrolled_at=NOW() \
                 RETURNING id"
            )
            .bind(&id).bind(&mission_id).bind(&runtime_kind).bind(capabilities).bind(&principal.subject)
            .fetch_one(&state.db).await
            {
                Ok(r) => ok_result(json!({"agent_id": r.get::<String,_>("id"), "mission_id": mission_id, "status": "offline"})),
                Err(e) => { tracing::error!("mcp enroll_mesh_agent: {e}"); err_result("database_error") }
            }
        }

        "list_mesh_agents" => {
            let mission_id = str_arg(args, "mission_id");
            if mission_id.is_empty() { return err_result("mission_id is required"); }
            match sqlx::query(
                "SELECT id, mission_id, runtime_kind, status, capabilities, enrolled_at, last_heartbeat_at \
                 FROM meshagent WHERE mission_id=$1 ORDER BY enrolled_at DESC LIMIT 100"
            )
            .bind(&mission_id).fetch_all(&state.db).await
            {
                Ok(rows) => ok_result(Value::Array(rows.iter().map(|r| json!({
                    "id": r.get::<String,_>("id"),
                    "mission_id": r.get::<String,_>("mission_id"),
                    "runtime_kind": r.get::<String,_>("runtime_kind"),
                    "status": r.get::<String,_>("status"),
                    "capabilities": r.get::<String,_>("capabilities"),
                    "last_heartbeat_at": r.get::<Option<chrono::NaiveDateTime>,_>("last_heartbeat_at"),
                })).collect())),
                Err(e) => { tracing::error!("mcp list_mesh_agents: {e}"); err_result("database_error") }
            }
        }

        // ── Mesh messages ─────────────────────────────────────────────────────

        "send_mesh_message" => {
            let mission_id = str_arg(args, "mission_id");
            let from_agent_id = str_arg(args, "sender_agent_id");
            let body = args.get("content").cloned().unwrap_or(json!({}));
            if mission_id.is_empty() || from_agent_id.is_empty() { return err_result("mission_id and sender_agent_id are required"); }
            let to_agent_id = args.get("recipient_agent_id").and_then(|v| v.as_str());
            let kluster_id = args.get("kluster_id").and_then(|v| v.as_str());
            let channel = str_arg_or(args, "channel", "coordination");
            let body_json = if body.is_string() {
                json!({"text": body.as_str().unwrap_or("")})
            } else { body };
            match sqlx::query(
                "INSERT INTO meshmessage (mission_id, kluster_id, from_agent_id, to_agent_id, channel, body_json, created_at) \
                 VALUES ($1,$2,$3,$4,$5,$6,NOW()) RETURNING id"
            )
            .bind(&mission_id).bind(kluster_id).bind(&from_agent_id).bind(to_agent_id)
            .bind(&channel).bind(body_json.to_string())
            .fetch_one(&state.db).await
            {
                Ok(r) => ok_result(json!({"message_id": r.get::<i32,_>("id"), "mission_id": mission_id})),
                Err(e) => { tracing::error!("mcp send_mesh_message: {e}"); err_result("database_error") }
            }
        }

        "list_mesh_messages" => {
            let agent_id = str_arg(args, "agent_id");
            let limit = int_arg(args, "limit").unwrap_or(20).min(100) as i64;
            if agent_id.is_empty() { return err_result("agent_id is required"); }
            match sqlx::query(
                "SELECT id, mission_id, from_agent_id, to_agent_id, channel, body_json, created_at, read_at \
                 FROM meshmessage WHERE (to_agent_id=$1 OR to_agent_id IS NULL) \
                 ORDER BY created_at DESC LIMIT $2"
            )
            .bind(&agent_id).bind(limit).fetch_all(&state.db).await
            {
                Ok(rows) => ok_result(Value::Array(rows.iter().map(|r| json!({
                    "id": r.get::<i32,_>("id"),
                    "mission_id": r.get::<String,_>("mission_id"),
                    "from_agent_id": r.get::<String,_>("from_agent_id"),
                    "to_agent_id": r.get::<Option<String>,_>("to_agent_id"),
                    "channel": r.get::<String,_>("channel"),
                    "body_json": r.get::<String,_>("body_json"),
                    "read_at": r.get::<Option<chrono::NaiveDateTime>,_>("read_at"),
                })).collect())),
                Err(e) => { tracing::error!("mcp list_mesh_messages: {e}"); err_result("database_error") }
            }
        }

        // ── Ledger events ─────────────────────────────────────────────────────

        "list_pending_ledger_events" => {
            let mission_id = str_arg(args, "mission_id");
            let limit = int_arg(args, "limit").unwrap_or(100).min(500);
            let rows = if mission_id.is_empty() {
                sqlx::query("SELECT * FROM ledgerevent WHERE state='pending' ORDER BY created_at ASC LIMIT $1")
                    .bind(limit).fetch_all(&state.db).await
            } else {
                sqlx::query("SELECT * FROM ledgerevent WHERE state='pending' AND mission_id=$1 ORDER BY created_at ASC LIMIT $2")
                    .bind(&mission_id).bind(limit).fetch_all(&state.db).await
            };
            match rows {
                Ok(rows) => ok_result(json!(rows.iter().map(|r| json!({
                    "id": r.get::<i32,_>("id"),
                    "event_id": r.get::<String,_>("event_id"),
                    "mission_id": r.get::<Option<String>,_>("mission_id"),
                    "entity_type": r.get::<String,_>("entity_type"),
                    "entity_id": r.get::<String,_>("entity_id"),
                    "action": r.get::<String,_>("action"),
                    "payload_json": r.get::<Option<String>,_>("payload_json"),
                    "state": r.get::<String,_>("state"),
                    "created_by_subject": r.get::<Option<String>,_>("created_by_subject"),
                    "created_at": r.get::<chrono::NaiveDateTime,_>("created_at"),
                })).collect::<Vec<_>>())),
                Err(e) => { tracing::error!("mcp list_pending_ledger_events: {e}"); err_result("database_error") }
            }
        }

        "get_entity_history" => {
            let mission_id = str_arg(args, "mission_id");
            let entity_type = str_arg(args, "entity_type");
            let entity_id = str_arg(args, "entity_id");
            let limit = int_arg(args, "limit").unwrap_or(200).min(500);
            if entity_type.is_empty() || entity_id.is_empty() {
                return err_result("entity_type and entity_id are required");
            }
            let rows = if mission_id.is_empty() {
                sqlx::query("SELECT * FROM ledgerevent WHERE entity_type=$1 AND entity_id=$2 ORDER BY created_at DESC LIMIT $3")
                    .bind(&entity_type).bind(&entity_id).bind(limit).fetch_all(&state.db).await
            } else {
                sqlx::query("SELECT * FROM ledgerevent WHERE mission_id=$1 AND entity_type=$2 AND entity_id=$3 ORDER BY created_at DESC LIMIT $4")
                    .bind(&mission_id).bind(&entity_type).bind(&entity_id).bind(limit).fetch_all(&state.db).await
            };
            match rows {
                Ok(rows) => ok_result(json!(rows.iter().map(|r| json!({
                    "id": r.get::<i32,_>("id"),
                    "event_id": r.get::<String,_>("event_id"),
                    "mission_id": r.get::<Option<String>,_>("mission_id"),
                    "entity_type": r.get::<String,_>("entity_type"),
                    "entity_id": r.get::<String,_>("entity_id"),
                    "action": r.get::<String,_>("action"),
                    "payload_json": r.get::<Option<String>,_>("payload_json"),
                    "state": r.get::<String,_>("state"),
                    "created_by_subject": r.get::<Option<String>,_>("created_by_subject"),
                    "created_at": r.get::<chrono::NaiveDateTime,_>("created_at"),
                })).collect::<Vec<_>>())),
                Err(e) => { tracing::error!("mcp get_entity_history: {e}"); err_result("database_error") }
            }
        }

        // ── Publication ───────────────────────────────────────────────────────

        "resolve_publish_plan" => {
            let mission_id = str_arg(args, "mission_id");
            let entity_kind = str_arg(args, "entity_kind");
            let event_kind = str_arg(args, "event_kind");
            if mission_id.is_empty() || entity_kind.is_empty() {
                return err_result("mission_id and entity_kind are required");
            }
            let row = sqlx::query(
                "SELECT r.id AS route_id, r.format, r.branch, r.rel_path_template, \
                 b.id AS binding_id, b.name AS binding_name, \
                 c.id AS conn_id, c.provider, c.host, c.repo_path \
                 FROM missionpersistenceroute r \
                 JOIN repobinding b ON b.id = r.binding_id \
                 JOIN repoconnection c ON c.id = b.connection_id \
                 WHERE r.mission_id=$1 AND r.entity_kind=$2 AND r.active=true \
                 AND (r.event_kind=$3 OR r.event_kind='') \
                 ORDER BY r.event_kind DESC LIMIT 1"
            )
            .bind(&mission_id).bind(&entity_kind).bind(&event_kind)
            .fetch_optional(&state.db).await;
            match row {
                Ok(Some(r)) => ok_result(json!({
                    "binding_id": r.get::<i32,_>("binding_id"),
                    "binding_name": r.get::<String,_>("binding_name"),
                    "connection_id": r.get::<i32,_>("conn_id"),
                    "provider": r.get::<String,_>("provider"),
                    "host": r.get::<Option<String>,_>("host"),
                    "repo_path": r.get::<String,_>("repo_path"),
                    "branch": r.get::<Option<String>,_>("branch"),
                    "rel_path": r.get::<Option<String>,_>("rel_path_template"),
                    "format": r.get::<Option<String>,_>("format"),
                })),
                Ok(None) => err_result("no_publish_plan_found"),
                Err(e) => { tracing::error!("mcp resolve_publish_plan: {e}"); err_result("database_error") }
            }
        }

        "get_publication_status" => {
            let mission_id = str_arg(args, "mission_id");
            let limit = int_arg(args, "limit").unwrap_or(20).min(200);
            let rows = if mission_id.is_empty() {
                sqlx::query("SELECT * FROM publicationrecord WHERE owner_subject=$1 ORDER BY created_at DESC LIMIT $2")
                    .bind(&principal.subject).bind(limit).fetch_all(&state.db).await
            } else {
                sqlx::query("SELECT * FROM publicationrecord WHERE owner_subject=$1 AND mission_id=$2 ORDER BY created_at DESC LIMIT $3")
                    .bind(&principal.subject).bind(&mission_id).bind(limit).fetch_all(&state.db).await
            };
            match rows {
                Ok(rows) => ok_result(json!(rows.iter().map(|r| json!({
                    "id": r.get::<i32,_>("id"),
                    "owner_subject": r.get::<String,_>("owner_subject"),
                    "mission_id": r.get::<Option<String>,_>("mission_id"),
                    "entity_kind": r.get::<String,_>("entity_kind"),
                    "entity_id": r.get::<String,_>("entity_id"),
                    "event_kind": r.get::<Option<String>,_>("event_kind"),
                    "binding_id": r.get::<Option<i32>,_>("binding_id"),
                    "status": r.get::<String,_>("status"),
                    "error": r.get::<Option<String>,_>("error"),
                    "commit_sha": r.get::<Option<String>,_>("commit_sha"),
                    "created_at": r.get::<chrono::NaiveDateTime,_>("created_at"),
                    "updated_at": r.get::<chrono::NaiveDateTime,_>("updated_at"),
                })).collect::<Vec<_>>())),
                Err(e) => { tracing::error!("mcp get_publication_status: {e}"); err_result("database_error") }
            }
        }

        // ── Skill sync state ──────────────────────────────────────────────────

        "get_skill_sync_status" => {
            let mission_id = str_arg(args, "mission_id");
            let kluster_id = str_arg(args, "kluster_id");
            let agent_id = str_arg(args, "agent_id");
            if mission_id.is_empty() { return err_result("mission_id is required"); }
            let row = sqlx::query(
                "SELECT * FROM skilllocalstate WHERE actor_subject=$1 AND mission_id=$2 \
                 AND ($3='' OR kluster_id=$3) AND ($4='' OR agent_id=$4) LIMIT 1"
            )
            .bind(&principal.subject).bind(&mission_id).bind(&kluster_id).bind(&agent_id)
            .fetch_optional(&state.db).await;
            match row {
                Ok(Some(r)) => ok_result(json!({
                    "mission_id": r.get::<String,_>("mission_id"),
                    "kluster_id": r.get::<Option<String>,_>("kluster_id"),
                    "actor_subject": r.get::<String,_>("actor_subject"),
                    "agent_id": r.get::<Option<String>,_>("agent_id"),
                    "last_snapshot_id": r.get::<Option<String>,_>("last_snapshot_id"),
                    "last_snapshot_sha256": r.get::<Option<String>,_>("last_snapshot_sha256"),
                    "local_overlay_sha256": r.get::<Option<String>,_>("local_overlay_sha256"),
                    "degraded_offline": r.get::<Option<bool>,_>("degraded_offline"),
                    "drift_flag": r.get::<Option<bool>,_>("drift_flag"),
                    "drift_details": r.get::<Option<String>,_>("drift_details"),
                    "last_sync_at": r.get::<Option<chrono::NaiveDateTime>,_>("last_sync_at"),
                })),
                Ok(None) => ok_result(json!({"mission_id": mission_id, "status": "no_record"})),
                Err(e) => { tracing::error!("mcp get_skill_sync_status: {e}"); err_result("database_error") }
            }
        }

        "ack_skill_sync" => {
            let mission_id = str_arg(args, "mission_id");
            let kluster_id = str_arg(args, "kluster_id");
            let agent_id = str_arg(args, "agent_id");
            let snapshot_id = str_arg(args, "snapshot_id");
            let snapshot_sha256 = str_arg(args, "snapshot_sha256");
            let local_overlay_sha256 = str_arg(args, "local_overlay_sha256");
            let degraded = args.get("degraded_offline").and_then(|v| v.as_bool()).unwrap_or(false);
            let drift_flag = args.get("drift_flag").and_then(|v| v.as_bool()).unwrap_or(false);
            let drift_details = args.get("drift_details").map(|v| v.to_string());
            if mission_id.is_empty() || kluster_id.is_empty() {
                return err_result("mission_id and kluster_id are required");
            }
            let result = sqlx::query(
                "INSERT INTO skilllocalstate \
                 (actor_subject, mission_id, kluster_id, agent_id, last_snapshot_id, last_snapshot_sha256, \
                  local_overlay_sha256, degraded_offline, drift_flag, drift_details, last_sync_at) \
                 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,NOW()) \
                 ON CONFLICT (actor_subject, mission_id, kluster_id, agent_id) DO UPDATE SET \
                 last_snapshot_id=$5, last_snapshot_sha256=$6, local_overlay_sha256=$7, \
                 degraded_offline=$8, drift_flag=$9, drift_details=$10, last_sync_at=NOW() \
                 RETURNING *"
            )
            .bind(&principal.subject).bind(&mission_id).bind(&kluster_id)
            .bind(if agent_id.is_empty() { None } else { Some(agent_id.clone()) })
            .bind(if snapshot_id.is_empty() { None } else { Some(snapshot_id) })
            .bind(if snapshot_sha256.is_empty() { None } else { Some(snapshot_sha256) })
            .bind(if local_overlay_sha256.is_empty() { None } else { Some(local_overlay_sha256) })
            .bind(degraded).bind(drift_flag).bind(drift_details)
            .fetch_one(&state.db).await;
            match result {
                Ok(r) => ok_result(json!({
                    "mission_id": r.get::<String,_>("mission_id"),
                    "kluster_id": r.get::<Option<String>,_>("kluster_id"),
                    "actor_subject": r.get::<String,_>("actor_subject"),
                    "last_snapshot_id": r.get::<Option<String>,_>("last_snapshot_id"),
                    "drift_flag": r.get::<Option<bool>,_>("drift_flag"),
                    "last_sync_at": r.get::<Option<chrono::NaiveDateTime>,_>("last_sync_at"),
                })),
                Err(e) => { tracing::error!("mcp ack_skill_sync: {e}"); err_result("database_error") }
            }
        }

        "promote_local_skill_overlay" => {
            let mission_id = str_arg(args, "mission_id");
            let kluster_id = str_arg(args, "kluster_id");
            let agent_id = str_arg(args, "agent_id");
            let sha256 = str_arg(args, "local_overlay_sha256");
            let note = str_arg(args, "note");
            if mission_id.is_empty() || kluster_id.is_empty() || sha256.is_empty() {
                return err_result("mission_id, kluster_id, local_overlay_sha256 are required");
            }
            let drift_details = json!({"promoted": true, "note": note}).to_string();
            let result = sqlx::query(
                "INSERT INTO skilllocalstate \
                 (actor_subject, mission_id, kluster_id, agent_id, local_overlay_sha256, drift_details, last_sync_at) \
                 VALUES ($1,$2,$3,$4,$5,$6,NOW()) \
                 ON CONFLICT (actor_subject, mission_id, kluster_id, agent_id) DO UPDATE SET \
                 local_overlay_sha256=$5, drift_details=$6, last_sync_at=NOW() \
                 RETURNING *"
            )
            .bind(&principal.subject).bind(&mission_id).bind(&kluster_id)
            .bind(if agent_id.is_empty() { None } else { Some(agent_id) })
            .bind(&sha256).bind(&drift_details)
            .fetch_one(&state.db).await;
            match result {
                Ok(r) => ok_result(json!({
                    "mission_id": r.get::<String,_>("mission_id"),
                    "kluster_id": r.get::<Option<String>,_>("kluster_id"),
                    "actor_subject": r.get::<String,_>("actor_subject"),
                    "local_overlay_sha256": r.get::<Option<String>,_>("local_overlay_sha256"),
                    "drift_details": drift_details,
                    "last_sync_at": r.get::<Option<chrono::NaiveDateTime>,_>("last_sync_at"),
                })),
                Err(e) => { tracing::error!("mcp promote_local_skill_overlay: {e}"); err_result("database_error") }
            }
        }

        // ── Profile pin ───────────────────────────────────────────────────────

        "pin_profile_version" => {
            let name = str_arg(args, "name");
            let sha256 = str_arg(args, "sha256");
            if name.is_empty() || sha256.is_empty() { return err_result("name and sha256 are required"); }
            let row = sqlx::query(
                "SELECT name, sha256 FROM userprofile WHERE owner_subject=$1 AND name=$2"
            )
            .bind(&principal.subject).bind(&name)
            .fetch_optional(&state.db).await;
            match row {
                Ok(Some(r)) => {
                    let remote_sha: Option<String> = r.get("sha256");
                    let matches = remote_sha.as_deref() == Some(sha256.as_str());
                    ok_result(json!({"name": name, "pinned_sha256": sha256, "remote_sha256": remote_sha, "matches": matches}))
                }
                Ok(None) => err_result("profile_not_found"),
                Err(e) => { tracing::error!("mcp pin_profile_version: {e}"); err_result("database_error") }
            }
        }

        // ── Mission packs (list only; export/install need Python) ─────────────

        "list_mission_packs" => {
            let limit = int_arg(args, "limit").unwrap_or(50).min(200);
            let rows = sqlx::query(
                "SELECT id, name, version, sha256, created_at FROM missionpack \
                 WHERE owner_subject=$1 ORDER BY created_at DESC LIMIT $2"
            )
            .bind(&principal.subject).bind(limit).fetch_all(&state.db).await;
            match rows {
                Ok(rows) => ok_result(json!(rows.iter().map(|r| json!({
                    "id": r.get::<String,_>("id"),
                    "name": r.get::<String,_>("name"),
                    "version": r.get::<Option<String>,_>("version"),
                    "sha256": r.get::<Option<String>,_>("sha256").map(|s| s[..8.min(s.len())].to_string()),
                    "created_at": r.get::<chrono::NaiveDateTime,_>("created_at"),
                })).collect::<Vec<_>>())),
                Err(e) => { tracing::error!("mcp list_mission_packs: {e}"); err_result("database_error") }
            }
        }

        // ── Remote launch ─────────────────────────────────────────────────────

        "create_remote_launch" => {
            let target_id = str_arg(args, "target_id");
            let target_host = str_arg(args, "target_host");
            let transport = str_arg_or(args, "transport", "ssh");
            let agent_kind = str_arg_or(args, "agent_kind", "generic");
            let agent_profile = str_arg(args, "agent_profile");
            let capability_scope = str_arg_or(args, "capability_scope", "");
            let ttl_hours = int_arg(args, "ttl_hours").unwrap_or(8);
            if target_host.is_empty() && target_id.is_empty() {
                return err_result("target_host or target_id is required");
            }
            let (session_id, raw_token) = match issue_mcp_session(
                &state.db, &principal.subject, ttl_hours, &capability_scope
            ).await {
                Ok(v) => v,
                Err(e) => { tracing::error!("mcp create_remote_launch session: {e}"); return err_result("database_error"); }
            };
            let launch_id = format!("rl-{}", hex::encode(&uuid::Uuid::new_v4().as_bytes()[..4]));
            let result = sqlx::query(
                "INSERT INTO remotelaunchrecord \
                 (id, owner_subject, transport, target_id, target_host, agent_kind, agent_profile, \
                  capability_scope, session_token_id, status, created_at, updated_at) \
                 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'launching',$10,$10) RETURNING *"
            )
            .bind(&launch_id).bind(&principal.subject).bind(&transport)
            .bind(if target_id.is_empty() { None } else { Some(target_id) })
            .bind(if target_host.is_empty() { None } else { Some(target_host) })
            .bind(&agent_kind).bind(if agent_profile.is_empty() { None } else { Some(agent_profile) })
            .bind(&capability_scope).bind(session_id).bind(now)
            .fetch_one(&state.db).await;
            match result {
                Ok(r) => ok_result(json!({
                    "launch_id": r.get::<String,_>("id"),
                    "owner_subject": r.get::<String,_>("owner_subject"),
                    "transport": r.get::<String,_>("transport"),
                    "agent_kind": r.get::<String,_>("agent_kind"),
                    "status": r.get::<String,_>("status"),
                    "session_token_id": r.get::<i32,_>("session_token_id"),
                    "session_token": raw_token,
                    "created_at": r.get::<chrono::NaiveDateTime,_>("created_at"),
                })),
                Err(e) => { tracing::error!("mcp create_remote_launch: {e}"); err_result("database_error") }
            }
        }

        "kill_remote_launch" => {
            let launch_id = str_arg(args, "launch_id");
            if launch_id.is_empty() { return err_result("launch_id is required"); }
            let row = sqlx::query(
                "SELECT id, owner_subject, session_token_id FROM remotelaunchrecord WHERE id=$1"
            )
            .bind(&launch_id).fetch_optional(&state.db).await;
            let row = match row {
                Ok(Some(r)) => r,
                Ok(None) => return err_result("launch_not_found"),
                Err(e) => { tracing::error!("mcp kill_remote_launch fetch: {e}"); return err_result("database_error"); }
            };
            let owner: String = row.get("owner_subject");
            if owner != principal.subject && !principal.is_admin {
                return err_result("forbidden");
            }
            let session_token_id: Option<i32> = row.get("session_token_id");
            if let Some(sid) = session_token_id {
                let _ = sqlx::query("UPDATE usersession SET revoked=true WHERE id=$1")
                    .bind(sid).execute(&state.db).await;
            }
            let _ = sqlx::query(
                "UPDATE remotelaunchrecord SET status='failed', error_message='killed by owner', updated_at=$2 WHERE id=$1"
            )
            .bind(&launch_id).bind(now).execute(&state.db).await;
            ok_result(json!({"killed": launch_id}))
        }

        // ── Still Python-only (tarball/S3/git ops) ────────────────────────────
        "get_artifact_download_url"
        | "load_kluster_workspace"
        | "heartbeat_workspace_lease"
        | "fetch_workspace_artifact"
        | "commit_kluster_workspace"
        | "release_kluster_workspace"
        | "publish_pending_ledger_events"
        | "provision_mission_persistence"
        | "resolve_skill_snapshot"
        | "download_skill_snapshot"
        | "publish_profile"
        | "download_profile"
        | "export_mission_pack"
        | "install_mission_pack" => not_impl(),

        _ => err_result("unknown_tool"),
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

async fn issue_mcp_session(
    db: &sqlx::PgPool,
    subject: &str,
    ttl_hours: i64,
    capability_scope: &str,
) -> Result<(i32, String), sqlx::Error> {
    use rand::RngCore;
    use sha2::{Digest, Sha256};
    let mut raw_bytes = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut raw_bytes);
    let raw_token = format!("mcs_{}", hex::encode(&raw_bytes));
    let token_hash = hex::encode(Sha256::new().chain_update(raw_token.as_bytes()).finalize());
    let token_prefix = &raw_token[..8.min(raw_token.len())];
    let expires_at = chrono::Utc::now().naive_utc() + chrono::Duration::hours(ttl_hours);
    let now = chrono::Utc::now().naive_utc();
    let session_id: i32 = sqlx::query_scalar(
        "INSERT INTO usersession \
         (subject, token_hash, token_prefix, expires_at, created_at, last_used_at, user_agent, revoked, capability_scope) \
         VALUES ($1,$2,$3,$4,$5,$5,'mc-mcp',false,$6) RETURNING id",
    )
    .bind(subject).bind(&token_hash).bind(token_prefix)
    .bind(expires_at).bind(now).bind(capability_scope)
    .fetch_one(db).await?;
    Ok((session_id, raw_token))
}

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
