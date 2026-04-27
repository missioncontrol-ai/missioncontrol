use axum::Router;
use crate::routes;

#[derive(Default, Clone)]
pub struct AppConfig {
    pub api_proxy: Option<String>,
}

pub fn build_app(config: AppConfig) -> Router {
    let mut router = routes::build_router();
    if let Some(proxy_url) = config.api_proxy {
        // Local routes take precedence; proxy catches everything else
        router = router.merge(routes::proxy::router(proxy_url));
    }
    router
}
