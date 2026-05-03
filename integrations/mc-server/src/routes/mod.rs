pub mod agents;
pub mod approvals;
pub mod artifacts;
pub mod auth;
pub mod budgets;
pub mod chat_integrations;
pub mod docs;
pub mod explorer;
pub mod google_chat_integrations;
pub mod event_triggers;
pub mod feedback;
pub mod governance;
pub mod health;
pub mod hooks;
pub mod ingestion;
pub mod klusters;
pub mod mission_packs;
pub mod missions;
pub mod onboarding;
pub mod persistence;
pub mod profiles;
pub mod proxy;
pub mod raft;
pub mod remotectl;
pub mod runs;
pub mod runtime;
pub mod scheduled_jobs;
pub mod schema_pack;
pub mod search;
pub mod skills;
pub mod tasks;
pub mod teams_integrations;
pub mod work;

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
        .merge(profiles::router())
        .merge(hooks::router())
        .merge(scheduled_jobs::router())
        .merge(work::router())
        .merge(runtime::router())
        .merge(budgets::router())
        .merge(event_triggers::router())
        .merge(feedback::router())
        .merge(mission_packs::router())
        .merge(onboarding::router())
        .merge(remotectl::router())
        .merge(artifacts::router())
        .merge(docs::router())
        .merge(persistence::router())
        .merge(schema_pack::router())
        .merge(chat_integrations::router())
        .merge(ingestion::router())
        .merge(search::router())
        .merge(skills::router())
        .merge(google_chat_integrations::router())
        .merge(teams_integrations::router())
        .merge(explorer::router());
    if include_proxy {
        router = router.merge(proxy::router());
    }
    router
}
