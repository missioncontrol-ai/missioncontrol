use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::IntoResponse,
    routing::get,
    Json, Router,
};
use chrono::Utc;
use serde::Deserialize;
use serde_json::json;
use sqlx::Row;
use std::sync::Arc;

use crate::{auth::Principal, state::AppState};

pub fn router() -> Router<Arc<AppState>> {
    Router::new()
        .route(
            "/persistence/connections",
            get(list_connections).post(create_connection),
        )
        .route(
            "/persistence/bindings",
            get(list_bindings).post(create_binding),
        )
        .route(
            "/persistence/missions/{mission_id}/policy",
            get(get_policy).put(put_policy),
        )
        .route(
            "/persistence/publish/plan",
            axum::routing::post(publish_plan_stub),
        )
        .route(
            "/persistence/publish/execute",
            axum::routing::post(publish_execute_stub),
        )
        .route(
            "/persistence/publish/records",
            get(list_publication_records),
        )
        .route(
            "/persistence/publish/records/{record_id}",
            get(get_publication_record),
        )
}

fn row_to_connection(row: &sqlx::postgres::PgRow) -> serde_json::Value {
    json!({
        "id": row.get::<i32, _>("id"),
        "owner_subject": row.get::<String, _>("owner_subject"),
        "name": row.get::<String, _>("name"),
        "provider": row.get::<String, _>("provider"),
        "host": row.get::<String, _>("host"),
        "repo_path": row.get::<String, _>("repo_path"),
        "default_branch": row.get::<String, _>("default_branch"),
        "credential_ref": row.get::<String, _>("credential_ref"),
        "options_json": row.get::<String, _>("options_json"),
        "created_at": row.get::<chrono::NaiveDateTime, _>("created_at"),
        "updated_at": row.get::<chrono::NaiveDateTime, _>("updated_at"),
    })
}

fn row_to_binding(row: &sqlx::postgres::PgRow) -> serde_json::Value {
    json!({
        "id": row.get::<i32, _>("id"),
        "owner_subject": row.get::<String, _>("owner_subject"),
        "name": row.get::<String, _>("name"),
        "connection_id": row.get::<i32, _>("connection_id"),
        "branch_override": row.get::<String, _>("branch_override"),
        "base_path": row.get::<String, _>("base_path"),
        "active": row.get::<bool, _>("active"),
        "created_at": row.get::<chrono::NaiveDateTime, _>("created_at"),
        "updated_at": row.get::<chrono::NaiveDateTime, _>("updated_at"),
    })
}

fn row_to_policy(row: &sqlx::postgres::PgRow) -> serde_json::Value {
    json!({
        "id": row.get::<i32, _>("id"),
        "mission_id": row.get::<String, _>("mission_id"),
        "default_binding_id": row.get::<Option<i32>, _>("default_binding_id"),
        "fallback_mode": row.get::<String, _>("fallback_mode"),
        "require_approval": row.get::<bool, _>("require_approval"),
        "created_at": row.get::<chrono::NaiveDateTime, _>("created_at"),
        "updated_at": row.get::<chrono::NaiveDateTime, _>("updated_at"),
    })
}

fn row_to_route(row: &sqlx::postgres::PgRow) -> serde_json::Value {
    json!({
        "id": row.get::<i32, _>("id"),
        "mission_id": row.get::<String, _>("mission_id"),
        "entity_kind": row.get::<String, _>("entity_kind"),
        "event_kind": row.get::<String, _>("event_kind"),
        "binding_id": row.get::<i32, _>("binding_id"),
        "branch_override": row.get::<String, _>("branch_override"),
        "path_template": row.get::<String, _>("path_template"),
        "format": row.get::<String, _>("format"),
        "active": row.get::<bool, _>("active"),
        "created_at": row.get::<chrono::NaiveDateTime, _>("created_at"),
        "updated_at": row.get::<chrono::NaiveDateTime, _>("updated_at"),
    })
}

fn row_to_publication_record(row: &sqlx::postgres::PgRow) -> serde_json::Value {
    json!({
        "id": row.get::<i32, _>("id"),
        "owner_subject": row.get::<String, _>("owner_subject"),
        "mission_id": row.get::<Option<String>, _>("mission_id"),
        "ledger_event_id": row.get::<Option<i32>, _>("ledger_event_id"),
        "entity_kind": row.get::<String, _>("entity_kind"),
        "entity_id": row.get::<String, _>("entity_id"),
        "event_kind": row.get::<String, _>("event_kind"),
        "binding_id": row.get::<i32, _>("binding_id"),
        "repo_url": row.get::<String, _>("repo_url"),
        "branch": row.get::<String, _>("branch"),
        "file_path": row.get::<String, _>("file_path"),
        "commit_sha": row.get::<String, _>("commit_sha"),
        "status": row.get::<String, _>("status"),
        "error": row.get::<String, _>("error"),
        "detail_json": row.get::<String, _>("detail_json"),
        "created_at": row.get::<chrono::NaiveDateTime, _>("created_at"),
        "updated_at": row.get::<chrono::NaiveDateTime, _>("updated_at"),
    })
}

/// Check if the principal is an owner of a mission (for policy endpoints).
async fn is_mission_owner(
    db: &sqlx::PgPool,
    principal: &Principal,
    mission_id: &str,
) -> bool {
    if principal.is_admin {
        return true;
    }
    if let Ok(Some(row)) =
        sqlx::query("SELECT owners FROM mission WHERE id=$1")
            .bind(mission_id)
            .fetch_optional(db)
            .await
    {
        let owners: String = row.get("owners");
        let sub = principal.subject.to_lowercase();
        return owners
            .split(',')
            .map(|x| x.trim().to_lowercase())
            .any(|x| x == sub);
    }
    false
}

async fn list_connections(
    State(state): State<Arc<AppState>>,
    principal: Principal,
) -> impl IntoResponse {
    match sqlx::query(
        "SELECT * FROM repoconnection WHERE owner_subject=$1 ORDER BY updated_at DESC",
    )
    .bind(&principal.subject)
    .fetch_all(&state.db)
    .await
    {
        Ok(rows) => {
            let connections: Vec<serde_json::Value> = rows.iter().map(row_to_connection).collect();
            Json(connections).into_response()
        }
        Err(e) => {
            tracing::error!("list_connections: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

async fn create_connection(
    State(state): State<Arc<AppState>>,
    principal: Principal,
    Json(payload): Json<serde_json::Value>,
) -> impl IntoResponse {
    let name = payload
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    let repo_path = payload
        .get("repo_path")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();

    if name.is_empty() || repo_path.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "name and repo_path are required"})),
        )
            .into_response();
    }

    let provider = payload
        .get("provider")
        .and_then(|v| v.as_str())
        .unwrap_or("github_app")
        .trim()
        .to_string();
    let host = payload
        .get("host")
        .and_then(|v| v.as_str())
        .unwrap_or("github.com")
        .trim()
        .to_string();
    let default_branch = payload
        .get("default_branch")
        .and_then(|v| v.as_str())
        .unwrap_or("main")
        .trim()
        .to_string();
    let credential_ref = payload
        .get("credential_ref")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    let options_json = payload
        .get("options_json")
        .and_then(|v| v.as_str())
        .unwrap_or("{}")
        .to_string();

    let now = Utc::now().naive_utc();
    match sqlx::query(
        r#"INSERT INTO repoconnection
            (owner_subject, name, provider, host, repo_path, default_branch,
             credential_ref, options_json, created_at, updated_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$9) RETURNING *"#,
    )
    .bind(&principal.subject)
    .bind(&name)
    .bind(&provider)
    .bind(&host)
    .bind(&repo_path)
    .bind(&default_branch)
    .bind(&credential_ref)
    .bind(&options_json)
    .bind(now)
    .fetch_one(&state.db)
    .await
    {
        Ok(row) => (StatusCode::OK, Json(row_to_connection(&row))).into_response(),
        Err(e) => {
            tracing::error!("create_connection: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

async fn list_bindings(
    State(state): State<Arc<AppState>>,
    principal: Principal,
) -> impl IntoResponse {
    match sqlx::query(
        "SELECT * FROM repobinding WHERE owner_subject=$1 ORDER BY updated_at DESC",
    )
    .bind(&principal.subject)
    .fetch_all(&state.db)
    .await
    {
        Ok(rows) => {
            let bindings: Vec<serde_json::Value> = rows.iter().map(row_to_binding).collect();
            Json(bindings).into_response()
        }
        Err(e) => {
            tracing::error!("list_bindings: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

async fn create_binding(
    State(state): State<Arc<AppState>>,
    principal: Principal,
    Json(payload): Json<serde_json::Value>,
) -> impl IntoResponse {
    let name = payload
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    let connection_id = payload
        .get("connection_id")
        .and_then(|v| v.as_i64())
        .unwrap_or(0) as i32;

    if name.is_empty() || connection_id <= 0 {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "name and connection_id are required"})),
        )
            .into_response();
    }

    // Verify connection exists and belongs to principal
    let conn_row = match sqlx::query(
        "SELECT id, owner_subject FROM repoconnection WHERE id=$1",
    )
    .bind(connection_id)
    .fetch_optional(&state.db)
    .await
    {
        Ok(Some(r)) => r,
        Ok(None) => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "connection not found"})),
            )
                .into_response()
        }
        Err(e) => {
            tracing::error!("create_binding fetch connection: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    let conn_owner: String = conn_row.get("owner_subject");
    if conn_owner != principal.subject && !principal.is_admin {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "connection not found"})),
        )
            .into_response();
    }

    let branch_override = payload
        .get("branch_override")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    let base_path = payload
        .get("base_path")
        .and_then(|v| v.as_str())
        .unwrap_or("missions")
        .trim()
        .trim_matches('/')
        .to_string();
    let active = payload
        .get("active")
        .and_then(|v| v.as_bool())
        .unwrap_or(true);

    let now = Utc::now().naive_utc();
    match sqlx::query(
        r#"INSERT INTO repobinding
            (owner_subject, name, connection_id, branch_override, base_path, active, created_at, updated_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$7) RETURNING *"#,
    )
    .bind(&principal.subject)
    .bind(&name)
    .bind(connection_id)
    .bind(&branch_override)
    .bind(&base_path)
    .bind(active)
    .bind(now)
    .fetch_one(&state.db)
    .await
    {
        Ok(row) => (StatusCode::OK, Json(row_to_binding(&row))).into_response(),
        Err(e) => {
            tracing::error!("create_binding: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

async fn get_policy(
    State(state): State<Arc<AppState>>,
    principal: Principal,
    Path(mission_id): Path<String>,
) -> impl IntoResponse {
    if !is_mission_owner(&state.db, &principal, &mission_id).await {
        return StatusCode::FORBIDDEN.into_response();
    }

    let policy_row = match sqlx::query(
        "SELECT * FROM missionpersistencepolicy WHERE mission_id=$1",
    )
    .bind(&mission_id)
    .fetch_optional(&state.db)
    .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("get_policy fetch policy: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    let route_rows = match sqlx::query(
        "SELECT * FROM missionpersistenceroute WHERE mission_id=$1 AND active=true ORDER BY id ASC",
    )
    .bind(&mission_id)
    .fetch_all(&state.db)
    .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("get_policy fetch routes: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    let policy_val = policy_row.as_ref().map(row_to_policy);
    let routes_val: Vec<serde_json::Value> = route_rows.iter().map(row_to_route).collect();
    Json(json!({"policy": policy_val, "routes": routes_val})).into_response()
}

async fn put_policy(
    State(state): State<Arc<AppState>>,
    principal: Principal,
    Path(mission_id): Path<String>,
    Json(payload): Json<serde_json::Value>,
) -> impl IntoResponse {
    if !is_mission_owner(&state.db, &principal, &mission_id).await {
        return StatusCode::FORBIDDEN.into_response();
    }

    let now = Utc::now().naive_utc();
    let default_binding_id: Option<i32> = payload
        .get("default_binding_id")
        .and_then(|v| v.as_i64())
        .map(|v| v as i32);
    let fallback_mode = payload
        .get("fallback_mode")
        .and_then(|v| v.as_str())
        .unwrap_or("fail_closed")
        .to_string();
    let require_approval = payload
        .get("require_approval")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    // UPSERT policy
    let upsert_result = sqlx::query(
        r#"INSERT INTO missionpersistencepolicy
            (mission_id, default_binding_id, fallback_mode, require_approval, created_at, updated_at)
           VALUES ($1,$2,$3,$4,$5,$5)
           ON CONFLICT (mission_id) DO UPDATE SET
             default_binding_id=EXCLUDED.default_binding_id,
             fallback_mode=EXCLUDED.fallback_mode,
             require_approval=EXCLUDED.require_approval,
             updated_at=EXCLUDED.updated_at"#,
    )
    .bind(&mission_id)
    .bind(default_binding_id)
    .bind(&fallback_mode)
    .bind(require_approval)
    .bind(now)
    .execute(&state.db)
    .await;

    if let Err(e) = upsert_result {
        tracing::error!("put_policy upsert: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    // Delete existing routes
    if let Err(e) = sqlx::query("DELETE FROM missionpersistenceroute WHERE mission_id=$1")
        .bind(&mission_id)
        .execute(&state.db)
        .await
    {
        tracing::error!("put_policy delete routes: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    // Insert new routes
    let routes = payload
        .get("routes")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    for item in &routes {
        let entity_kind = item
            .get("entity_kind")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        if entity_kind.is_empty() {
            continue;
        }
        let event_kind = item
            .get("event_kind")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        let binding_id = item
            .get("binding_id")
            .and_then(|v| v.as_i64())
            .unwrap_or(0) as i32;
        let branch_override = item
            .get("branch_override")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        let path_template = item
            .get("path_template")
            .and_then(|v| v.as_str())
            .unwrap_or("missions/{mission_id}/{entity_kind}/{entity_id}.json")
            .to_string();
        let format = item
            .get("format")
            .and_then(|v| v.as_str())
            .unwrap_or("json_v1")
            .to_string();
        let active = item
            .get("active")
            .and_then(|v| v.as_bool())
            .unwrap_or(true);

        if let Err(e) = sqlx::query(
            r#"INSERT INTO missionpersistenceroute
                (mission_id, entity_kind, event_kind, binding_id, branch_override,
                 path_template, format, active, created_at, updated_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$9)"#,
        )
        .bind(&mission_id)
        .bind(&entity_kind)
        .bind(&event_kind)
        .bind(binding_id)
        .bind(&branch_override)
        .bind(&path_template)
        .bind(&format)
        .bind(active)
        .bind(now)
        .execute(&state.db)
        .await
        {
            tracing::error!("put_policy insert route: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    }

    Json(json!({"ok": true, "mission_id": mission_id, "updated_by": principal.subject}))
        .into_response()
}

async fn publish_plan_stub(
    State(_state): State<Arc<AppState>>,
    _principal: Principal,
) -> impl IntoResponse {
    StatusCode::NOT_IMPLEMENTED.into_response()
}

async fn publish_execute_stub(
    State(_state): State<Arc<AppState>>,
    _principal: Principal,
) -> impl IntoResponse {
    StatusCode::NOT_IMPLEMENTED.into_response()
}

#[derive(Deserialize)]
struct ListRecordsQuery {
    mission_id: Option<String>,
    limit: Option<i64>,
}

async fn list_publication_records(
    State(state): State<Arc<AppState>>,
    principal: Principal,
    Query(q): Query<ListRecordsQuery>,
) -> impl IntoResponse {
    let limit = q.limit.unwrap_or(20).min(500);

    let rows = if let Some(ref mid) = q.mission_id {
        sqlx::query(
            "SELECT * FROM publicationrecord WHERE owner_subject=$1 AND mission_id=$2 \
             ORDER BY created_at DESC LIMIT $3",
        )
        .bind(&principal.subject)
        .bind(mid)
        .bind(limit)
        .fetch_all(&state.db)
        .await
    } else {
        sqlx::query(
            "SELECT * FROM publicationrecord WHERE owner_subject=$1 \
             ORDER BY created_at DESC LIMIT $2",
        )
        .bind(&principal.subject)
        .bind(limit)
        .fetch_all(&state.db)
        .await
    };

    match rows {
        Ok(rows) => {
            let records: Vec<serde_json::Value> =
                rows.iter().map(row_to_publication_record).collect();
            Json(json!({"records": records})).into_response()
        }
        Err(e) => {
            tracing::error!("list_publication_records: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

async fn get_publication_record(
    State(state): State<Arc<AppState>>,
    principal: Principal,
    Path(record_id): Path<i32>,
) -> impl IntoResponse {
    let row = match sqlx::query("SELECT * FROM publicationrecord WHERE id=$1")
        .bind(record_id)
        .fetch_optional(&state.db)
        .await
    {
        Ok(Some(r)) => r,
        Ok(None) => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "publication record not found"})),
            )
                .into_response()
        }
        Err(e) => {
            tracing::error!("get_publication_record: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    let owner: String = row.get("owner_subject");
    if owner != principal.subject && !principal.is_admin {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "publication record not found"})),
        )
            .into_response();
    }

    // If record has a mission_id, verify ownership
    let mission_id: Option<String> = row.try_get("mission_id").ok().and_then(|v: Option<String>| v);
    if let Some(ref mid) = mission_id {
        if !is_mission_owner(&state.db, &principal, mid).await {
            return StatusCode::FORBIDDEN.into_response();
        }
    }

    Json(json!({"record": row_to_publication_record(&row)})).into_response()
}
