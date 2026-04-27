use reqwest::Client;
use sqlx::PgPool;

pub struct AppState {
    pub db: PgPool,
    pub proxy_upstream: Option<String>,
    pub proxy_client: Option<Client>,
}
