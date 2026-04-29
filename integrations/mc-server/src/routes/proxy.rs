use axum::{
    body::Body,
    extract::{Request, State},
    http::{header, StatusCode},
    response::Response,
    routing::any,
    Router,
};
use std::sync::Arc;

use crate::state::AppState;

pub fn router() -> Router<Arc<AppState>> {
    Router::new().route("/{*path}", any(proxy_handler))
}

async fn proxy_handler(
    State(state): State<Arc<AppState>>,
    req: Request,
) -> Result<Response, StatusCode> {
    let (client, upstream) = match (&state.proxy_client, &state.proxy_upstream) {
        (Some(c), Some(u)) => (c, u),
        _ => return Err(StatusCode::NOT_FOUND),
    };

    let path_and_query = req
        .uri()
        .path_and_query()
        .map(|pq| pq.as_str())
        .unwrap_or("/");
    let target = format!("{}{}", upstream.trim_end_matches('/'), path_and_query);

    let method = reqwest::Method::from_bytes(req.method().as_str().as_bytes())
        .map_err(|_| StatusCode::BAD_REQUEST)?;

    // Forward request headers; drop hop-by-hop headers that must not cross proxies.
    let mut upstream_req = client.request(method, &target);
    for (name, value) in req.headers() {
        match name {
            &header::HOST
            | &header::CONNECTION
            | &header::TRANSFER_ENCODING
            | &header::UPGRADE => continue,
            _ => {}
        }
        upstream_req = upstream_req.header(name.as_str(), value.as_bytes());
    }

    // Buffer the request body. SSE requests are GETs with no body; for other
    // verbs (POST/PUT) we buffer since reqwest 0.11 doesn't accept an axum Body directly.
    let body_bytes = axum::body::to_bytes(req.into_body(), usize::MAX)
        .await
        .map_err(|_| StatusCode::BAD_REQUEST)?;
    if !body_bytes.is_empty() {
        upstream_req = upstream_req.body(body_bytes.to_vec());
    }

    let upstream_res = upstream_req
        .send()
        .await
        .map_err(|_| StatusCode::BAD_GATEWAY)?;

    let status = axum::http::StatusCode::from_u16(upstream_res.status().as_u16())
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;

    // Forward response headers; drop hop-by-hop headers.
    // Note: upstream_res uses reqwest (http 0.2) HeaderName; compare by string
    // to avoid the http 0.2 vs http 1.x type mismatch.
    let mut builder = Response::builder().status(status);
    for (name, value) in upstream_res.headers() {
        match name.as_str() {
            "connection" | "transfer-encoding" | "keep-alive" | "proxy-authenticate"
            | "proxy-authorization" | "te" | "trailers" | "upgrade" => continue,
            _ => {}
        }
        builder = builder.header(name.as_str(), value.as_bytes());
    }

    // Stream the response body. This is essential for SSE — buffering with
    // .bytes() would hold the connection until the upstream closes it.
    let stream = upstream_res.bytes_stream();
    builder
        .body(Body::from_stream(stream))
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
}
