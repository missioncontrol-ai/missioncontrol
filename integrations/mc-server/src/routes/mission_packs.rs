use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use sqlx::Row;
use std::sync::Arc;

use crate::{auth::Principal, state::AppState};

pub fn router() -> Router<Arc<AppState>> {
    Router::new()
        .route("/packs", get(list_packs))
        .route("/packs/{pack_id}", get(get_pack).delete(delete_pack))
        .route(
            "/packs/missions/{mission_id}/export",
            post(export_stub),
        )
        .route("/packs/{pack_id}/install", post(install_stub))
}

fn not_found() -> axum::response::Response {
    (
        StatusCode::NOT_FOUND,
        Json(serde_json::json!({"detail": "Not found"})),
    )
        .into_response()
}

async fn list_packs(
    State(state): State<Arc<AppState>>,
    principal: Principal,
) -> impl IntoResponse {
    match sqlx::query(
        "SELECT id, name, version, sha256, created_at \
         FROM missionpack \
         WHERE owner_subject=$1 \
         ORDER BY created_at DESC",
    )
    .bind(&principal.subject)
    .fetch_all(&state.db)
    .await
    {
        Ok(rows) => {
            let items: Vec<serde_json::Value> = rows
                .iter()
                .map(|row| {
                    let sha256_full: String = row.get("sha256");
                    let sha256_short = sha256_full.chars().take(8).collect::<String>();
                    serde_json::json!({
                        "id": row.get::<String, _>("id"),
                        "name": row.get::<String, _>("name"),
                        "version": row.get::<i32, _>("version"),
                        "sha256": sha256_short,
                        "created_at": format!("{}Z", row.get::<chrono::NaiveDateTime, _>("created_at").format("%Y-%m-%dT%H:%M:%S")),
                    })
                })
                .collect();
            Json(items).into_response()
        }
        Err(e) => {
            tracing::error!("list_packs: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

async fn get_pack(
    State(state): State<Arc<AppState>>,
    principal: Principal,
    Path(pack_id): Path<String>,
) -> impl IntoResponse {
    match sqlx::query(
        "SELECT id, name, version, sha256, manifest_json \
         FROM missionpack \
         WHERE id=$1 AND owner_subject=$2",
    )
    .bind(&pack_id)
    .bind(&principal.subject)
    .fetch_optional(&state.db)
    .await
    {
        Ok(Some(row)) => {
            let manifest_str: String = row.get("manifest_json");
            let manifest: serde_json::Value =
                serde_json::from_str(&manifest_str).unwrap_or(serde_json::json!({}));
            Json(serde_json::json!({
                "id": row.get::<String, _>("id"),
                "name": row.get::<String, _>("name"),
                "version": row.get::<i32, _>("version"),
                "sha256": row.get::<String, _>("sha256"),
                "manifest": manifest,
            }))
            .into_response()
        }
        Ok(None) => not_found(),
        Err(e) => {
            tracing::error!("get_pack: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

async fn delete_pack(
    State(state): State<Arc<AppState>>,
    principal: Principal,
    Path(pack_id): Path<String>,
) -> impl IntoResponse {
    match sqlx::query(
        "DELETE FROM missionpack WHERE id=$1 AND owner_subject=$2",
    )
    .bind(&pack_id)
    .bind(&principal.subject)
    .execute(&state.db)
    .await
    {
        Ok(r) if r.rows_affected() == 0 => not_found(),
        Ok(_) => Json(serde_json::json!({"deleted": pack_id})).into_response(),
        Err(e) => {
            tracing::error!("delete_pack: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

async fn export_stub(
    _state: State<Arc<AppState>>,
    _principal: Principal,
    Path(_mission_id): Path<String>,
) -> impl IntoResponse {
    (
        StatusCode::NOT_IMPLEMENTED,
        Json(serde_json::json!({"detail": "Export not yet implemented"})),
    )
}

async fn install_stub(
    _state: State<Arc<AppState>>,
    _principal: Principal,
    Path(_pack_id): Path<String>,
) -> impl IntoResponse {
    (
        StatusCode::NOT_IMPLEMENTED,
        Json(serde_json::json!({"detail": "Install not yet implemented"})),
    )
}
