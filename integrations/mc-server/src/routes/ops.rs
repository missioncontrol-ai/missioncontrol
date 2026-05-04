use axum::{
    extract::State,
    http::StatusCode,
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use chrono::Utc;
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use tokio::io::AsyncWriteExt;
use uuid::Uuid;

use crate::{auth::Principal, state::AppState};

pub fn router() -> Router<Arc<AppState>> {
    Router::new()
        .route("/ops/backups", post(trigger_backup).get(list_backups))
        .route("/ops/secrets/status", get(get_secrets_status))
        .route("/ops/secrets/bootstrap", post(post_secrets_bootstrap))
        .route("/ops/secrets/rotate", post(post_secrets_rotate))
}

// ── Request / response types ──────────────────────────────────────────────────

#[derive(Deserialize)]
struct BackupRequest {
    target: Option<String>,
    reason: Option<String>,
}

#[derive(Deserialize)]
struct SecretsBootstrapRequest {
    #[allow(dead_code)]
    profile: Option<String>,
}

#[derive(Deserialize)]
struct SecretsRotateRequest {
    #[allow(dead_code)]
    profile: Option<String>,
    #[allow(dead_code)]
    key: Option<String>,
}

#[derive(Serialize, Clone)]
struct BackupRecord {
    id: String,
    target: String,
    reason: Option<String>,
    triggered_by: String,
    status: String,
    created_at: String,
}

// ── Helpers ───────────────────────────────────────────────────────────────────

fn backup_dir() -> String {
    std::env::var("MC_BACKUP_DIR").unwrap_or_else(|_| "/data/backups".to_string())
}

fn backup_file() -> String {
    format!("{}/records.json", backup_dir())
}

async fn read_backup_records() -> Vec<serde_json::Value> {
    match tokio::fs::read_to_string(backup_file()).await {
        Ok(contents) => serde_json::from_str::<Vec<serde_json::Value>>(&contents).unwrap_or_default(),
        Err(_) => vec![],
    }
}

async fn write_backup_records(records: &[serde_json::Value]) -> std::io::Result<()> {
    let dir = backup_dir();
    tokio::fs::create_dir_all(&dir).await?;
    let path = backup_file();
    let json = serde_json::to_string_pretty(records).unwrap_or_else(|_| "[]".into());
    let mut file = tokio::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .open(&path)
        .await?;
    file.write_all(json.as_bytes()).await?;
    file.flush().await?;
    Ok(())
}

fn require_admin(principal: &Principal) -> Option<axum::response::Response> {
    if !principal.is_admin {
        Some(
            (
                StatusCode::FORBIDDEN,
                Json(serde_json::json!({"detail": "admin_required"})),
            )
                .into_response(),
        )
    } else {
        None
    }
}

// ── Handlers ──────────────────────────────────────────────────────────────────

async fn trigger_backup(
    State(_state): State<Arc<AppState>>,
    principal: Principal,
    Json(payload): Json<BackupRequest>,
) -> impl IntoResponse {
    if let Some(r) = require_admin(&principal) {
        return r;
    }

    let backup_id = hex::encode(Uuid::new_v4().as_bytes());
    let now = Utc::now();
    let created_at = now.to_rfc3339();

    let record = BackupRecord {
        id: backup_id.clone(),
        target: payload.target.clone().unwrap_or_else(|| "default".to_string()),
        reason: payload.reason.clone(),
        triggered_by: principal.subject.clone(),
        status: "triggered".to_string(),
        created_at: created_at.clone(),
    };

    // Append to records file
    let mut records = read_backup_records().await;
    records.push(serde_json::json!({
        "id": record.id,
        "target": record.target,
        "reason": record.reason,
        "triggered_by": record.triggered_by,
        "status": record.status,
        "created_at": record.created_at,
    }));

    if let Err(e) = write_backup_records(&records).await {
        tracing::error!("trigger_backup: failed to write records.json: {e}");
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"ok": false, "error": "failed to persist backup record"})),
        )
            .into_response();
    }

    // Optionally run the backup script
    if let Ok(script) = std::env::var("MC_BACKUP_SCRIPT") {
        let id_clone = backup_id.clone();
        tokio::spawn(async move {
            let result = tokio::process::Command::new(&script)
                .env("BACKUP_ID", &id_clone)
                .status()
                .await;
            match result {
                Ok(status) => tracing::info!("backup script exited: {status}"),
                Err(e) => tracing::error!("backup script failed to launch: {e}"),
            }
        });
    }

    Json(serde_json::json!({
        "ok": true,
        "backup": {
            "id": record.id,
            "target": record.target,
            "reason": record.reason,
            "triggered_by": record.triggered_by,
            "status": record.status,
            "created_at": record.created_at,
        }
    }))
    .into_response()
}

async fn list_backups(
    State(_state): State<Arc<AppState>>,
    principal: Principal,
) -> impl IntoResponse {
    if let Some(r) = require_admin(&principal) {
        return r;
    }

    let records = read_backup_records().await;
    Json(serde_json::json!({"backups": records})).into_response()
}

async fn get_secrets_status(
    State(_state): State<Arc<AppState>>,
    principal: Principal,
) -> impl IntoResponse {
    if let Some(r) = require_admin(&principal) {
        return r;
    }
    // Secrets management is Python-managed; not implemented in the Rust server.
    (
        StatusCode::NOT_IMPLEMENTED,
        Json(serde_json::json!({"ok": false, "error": "use_python_api"})),
    )
        .into_response()
}

async fn post_secrets_bootstrap(
    State(_state): State<Arc<AppState>>,
    principal: Principal,
    Json(_payload): Json<SecretsBootstrapRequest>,
) -> impl IntoResponse {
    if let Some(r) = require_admin(&principal) {
        return r;
    }
    // Secrets management is Python-managed; not implemented in the Rust server.
    (
        StatusCode::NOT_IMPLEMENTED,
        Json(serde_json::json!({"ok": false, "error": "use_python_api"})),
    )
        .into_response()
}

async fn post_secrets_rotate(
    State(_state): State<Arc<AppState>>,
    principal: Principal,
    Json(_payload): Json<SecretsRotateRequest>,
) -> impl IntoResponse {
    if let Some(r) = require_admin(&principal) {
        return r;
    }
    // Secrets management is Python-managed; not implemented in the Rust server.
    (
        StatusCode::NOT_IMPLEMENTED,
        Json(serde_json::json!({"ok": false, "error": "use_python_api"})),
    )
        .into_response()
}
