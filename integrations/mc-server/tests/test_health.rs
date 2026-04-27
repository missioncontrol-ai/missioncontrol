use axum_test::TestServer;
use mc_server::{build_app, AppConfig};

#[tokio::test]
async fn test_health_returns_ok() {
    let app = build_app(AppConfig::default());
    let server = TestServer::new(app);
    let res = server.get("/health").await;
    res.assert_status_ok();
    let body: serde_json::Value = res.json();
    assert_eq!(body["status"], "ok");
}

#[tokio::test]
async fn test_health_includes_version() {
    let app = build_app(AppConfig::default());
    let server = TestServer::new(app);
    let res = server.get("/health").await;
    let body: serde_json::Value = res.json();
    assert!(body["version"].is_string());
    assert!(!body["version"].as_str().unwrap().is_empty());
}
