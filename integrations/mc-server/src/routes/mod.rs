pub mod health;
pub mod missions;
pub mod proxy;

use axum::Router;
use std::sync::Arc;

use crate::state::AppState;

pub fn build_router(include_proxy: bool) -> Router<Arc<AppState>> {
    let mut router = Router::new()
        .merge(health::router())
        .merge(missions::router());
    if include_proxy {
        router = router.merge(proxy::router());
    }
    router
}
