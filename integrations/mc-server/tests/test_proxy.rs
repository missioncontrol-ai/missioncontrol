use axum_test::TestServer;
use mc_server::{build_app, AppConfig};
use wiremock::{matchers::{method, path}, Mock, MockServer, ResponseTemplate};

#[tokio::test]
async fn test_proxy_forwards_unknown_route() {
    let mock_backend = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/missions"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!([])))
        .mount(&mock_backend)
        .await;

    let config = AppConfig {
        api_proxy: Some(mock_backend.uri()),
    };
    let app = build_app(config);
    let server = TestServer::new(app);

    let res = server.get("/missions").await;
    res.assert_status_ok();
}

#[tokio::test]
async fn test_proxy_does_not_override_health() {
    // Point proxy at an unreachable port — /health must be served locally
    let config = AppConfig {
        api_proxy: Some("http://127.0.0.1:1".to_string()),
    };
    let app = build_app(config);
    let server = TestServer::new(app);

    let res = server.get("/health").await;
    res.assert_status_ok();
    let body: serde_json::Value = res.json();
    assert_eq!(body["status"], "ok");
}

#[tokio::test]
async fn test_proxy_returns_502_when_upstream_unreachable() {
    let config = AppConfig {
        api_proxy: Some("http://127.0.0.1:1".to_string()),
    };
    let app = build_app(config);
    let server = TestServer::new(app);

    let res = server.get("/some/unknown/path").await;
    res.assert_status(axum::http::StatusCode::BAD_GATEWAY);
}
