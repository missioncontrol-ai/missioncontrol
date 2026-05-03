pub mod agents;
pub mod approvals;
pub mod auth;
pub mod governance;
pub mod health;
pub mod klusters;
pub mod missions;
pub mod profiles;
pub mod proxy;
pub mod raft;
pub mod runs;
pub mod tasks;

use axum::Router;
use std::sync::Arc;

use crate::state::AppState;

pub fn build_router(include_proxy: bool) -> Router<Arc<AppState>> {
    let mut router = Router::new()
        .merge(health::router())
        .merge(raft::router())
        .merge(auth::router())
        .merge(missions::router())
        .merge(agents::router())
        .merge(klusters::router())
        .merge(tasks::router())
        .merge(approvals::router())
        .merge(runs::router())
        .merge(governance::router())
        .merge(profiles::router());
    if include_proxy {
        router = router.merge(proxy::router());
    }
    router
}
