pub mod health;
pub mod proxy;

use axum::Router;

pub fn build_router() -> Router {
    Router::new().merge(health::router())
}
