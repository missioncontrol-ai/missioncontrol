use axum::Router;
use reqwest::Client;
use sqlx::PgPool;
use std::sync::Arc;

use crate::{routes, state::AppState};

#[derive(Default, Clone)]
pub struct AppConfig {
    pub api_proxy: Option<String>,
}

pub fn build_app(db: PgPool, config: AppConfig) -> Router {
    let proxy_client = config.api_proxy.as_ref().map(|_| {
        Client::builder()
            .timeout(std::time::Duration::from_secs(30))
            .build()
            .expect("proxy client init")
    });

    let state = Arc::new(AppState {
        db,
        proxy_upstream: config.api_proxy.clone(),
        proxy_client,
    });

    routes::build_router(config.api_proxy.is_some()).with_state(state)
}
