use axum::{
    body::Body,
    extract::{Request, State},
    http::StatusCode,
    response::Response,
    routing::any,
    Router,
};
use reqwest::Client;

#[derive(Clone)]
pub struct ProxyState {
    client: Client,
    upstream: String,
}

pub fn router(upstream: String) -> Router {
    let state = ProxyState {
        client: Client::builder()
            .timeout(std::time::Duration::from_secs(30))
            .build()
            .expect("proxy client init"),
        upstream,
    };
    Router::new()
        .route("/{*path}", any(proxy_handler))
        .with_state(state)
}

async fn proxy_handler(
    State(state): State<ProxyState>,
    req: Request,
) -> Result<Response, StatusCode> {
    let path_and_query = req
        .uri()
        .path_and_query()
        .map(|pq| pq.as_str())
        .unwrap_or("/");
    let target = format!(
        "{}{}",
        state.upstream.trim_end_matches('/'),
        path_and_query
    );

    let method = reqwest::Method::from_bytes(req.method().as_str().as_bytes())
        .map_err(|_| StatusCode::BAD_REQUEST)?;

    let body_bytes = axum::body::to_bytes(req.into_body(), usize::MAX)
        .await
        .map_err(|_| StatusCode::BAD_REQUEST)?;

    let upstream_res = state
        .client
        .request(method, &target)
        .body(body_bytes.to_vec())
        .send()
        .await
        .map_err(|_| StatusCode::BAD_GATEWAY)?;

    let status =
        axum::http::StatusCode::from_u16(upstream_res.status().as_u16())
            .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;

    let body_bytes = upstream_res
        .bytes()
        .await
        .map_err(|_| StatusCode::BAD_GATEWAY)?;

    Response::builder()
        .status(status)
        .body(Body::from(body_bytes))
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
}
