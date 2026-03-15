use crate::agent_context::AgentContext;
use crate::client::MissionControlClient;
use anyhow::Context;
use axum::{
    extract::State,
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use chrono::{DateTime, Utc};
use clap::Args;
use futures_util::StreamExt;
use reqwest::Method;
use rumqttc::{AsyncClient, Event as MqttEvent, Packet, QoS, TlsConfiguration, Transport};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{net::SocketAddr, sync::Arc, time::Duration};
use tokio::io::AsyncWriteExt;
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::{broadcast, RwLock};
use tracing::{info, warn};
use url::Url;

/// CLI arguments that control the daemon behavior.
#[derive(Args, Debug)]
pub struct DaemonArgs {
    /// Matrix-style SSE endpoint relative to the configured base URL.
    #[arg(long, default_value = "/events/stream")]
    pub matrix_endpoint: String,

    /// Disable matrix SSE stream and run shim API only.
    #[arg(long, env = "MC_DISABLE_MATRIX", default_value_t = false)]
    pub disable_matrix: bool,

    /// Optional MQTT broker URL the daemon will keep alive while syncing.
    #[arg(long)]
    pub mqtt_url: Option<String>,

    /// MQTT topic to keep subscribed to (default `missioncontrol/inbox`).
    #[arg(long, default_value = "missioncontrol/inbox")]
    pub mqtt_topic: String,

    /// Optional port to host a local SSE fan-out server for dashboards or local controllers.
    #[arg(long)]
    pub fanout_port: Option<u16>,

    /// Local host for the shim-compatible control API.
    #[arg(long, env = "MC_DAEMON_HOST", default_value = "127.0.0.1")]
    pub shim_host: String,

    /// Local port for the shim-compatible control API.
    #[arg(long, env = "MC_DAEMON_PORT", default_value_t = 8765)]
    pub shim_port: u16,

    /// Cache TTL (seconds) for `/v1/tools` responses.
    #[arg(long, env = "MC_TOOLS_CACHE_TTL_SEC", default_value_t = 60)]
    pub tools_cache_ttl_sec: u64,

    /// Maximum stale cache age (seconds) for `/v1/tools` when backend refresh fails.
    #[arg(long, env = "MC_TOOLS_STALE_SEC", default_value_t = 600)]
    pub tools_stale_sec: u64,

    /// Optional local bearer token required for shim API access.
    #[arg(long, env = "MC_DAEMON_SHIM_TOKEN")]
    pub shim_token: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct MatrixEvent {
    pub id: Option<String>,
    pub event: Option<String>,
    pub payload: Value,
    pub received_at: DateTime<Utc>,
}

#[derive(Debug)]
struct RawSseChunk {
    id: Option<String>,
    event: Option<String>,
    data: String,
}

#[derive(Clone)]
struct ShimApiState {
    client: MissionControlClient,
    cache: Arc<RwLock<Option<ToolsCache>>>,
    ttl_fresh: Duration,
    ttl_stale: Duration,
    shim_token: Option<String>,
}

#[derive(Clone)]
struct ToolsCache {
    tools: Value,
    cached_at: DateTime<Utc>,
}

#[derive(Debug, Deserialize)]
struct ShimToolCallRequest {
    name: String,
    #[serde(default)]
    arguments: Value,
}

#[derive(Debug, Serialize)]
struct ShimErrorPayload {
    ok: bool,
    error: String,
    category: String,
}

impl ShimApiState {
    fn new(
        client: MissionControlClient,
        ttl_fresh: Duration,
        ttl_stale: Duration,
        shim_token: Option<String>,
    ) -> Self {
        Self {
            client,
            cache: Arc::new(RwLock::new(None)),
            ttl_fresh,
            ttl_stale,
            shim_token,
        }
    }

    async fn refresh_tools(&self) -> anyhow::Result<Value> {
        let tools = self.client.get_json("/mcp/tools").await?;
        let mut cache = self.cache.write().await;
        *cache = Some(ToolsCache {
            tools: tools.clone(),
            cached_at: Utc::now(),
        });
        Ok(tools)
    }

    async fn tools_with_cache(&self) -> anyhow::Result<(Value, String, f64, Option<String>)> {
        let now = Utc::now();
        {
            let cache = self.cache.read().await;
            if let Some(cached) = cache.as_ref() {
                let age = now
                    .signed_duration_since(cached.cached_at)
                    .to_std()
                    .unwrap_or_else(|_| Duration::from_secs(0));
                if age <= self.ttl_fresh {
                    return Ok((
                        cached.tools.clone(),
                        "fresh".to_string(),
                        age.as_secs_f64(),
                        None,
                    ));
                }
            }
        }
        match self.refresh_tools().await {
            Ok(tools) => Ok((tools, "fresh".to_string(), 0.0, None)),
            Err(err) => {
                let category = classify_error_category(&err).to_string();
                let cache = self.cache.read().await;
                if let Some(cached) = cache.as_ref() {
                    let age = now
                        .signed_duration_since(cached.cached_at)
                        .to_std()
                        .unwrap_or_else(|_| Duration::from_secs(0));
                    if age <= self.ttl_stale {
                        return Ok((
                            cached.tools.clone(),
                            "stale".to_string(),
                            age.as_secs_f64(),
                            Some(format!("{} ({})", err, category)),
                        ));
                    }
                }
                Err(err)
            }
        }
    }

    async fn ready_state(&self) -> (bool, Option<f64>) {
        let cache = self.cache.read().await;
        if let Some(cached) = cache.as_ref() {
            let age = Utc::now()
                .signed_duration_since(cached.cached_at)
                .to_std()
                .unwrap_or_else(|_| Duration::from_secs(0))
                .as_secs_f64();
            if age <= self.ttl_stale.as_secs_f64() {
                return (true, Some(age));
            }
            return (false, Some(age));
        }
        (false, None)
    }
}

impl MatrixEvent {
    fn from_raw(raw: RawSseChunk) -> Self {
        let payload =
            serde_json::from_str(&raw.data).unwrap_or_else(|_| Value::String(raw.data.clone()));
        Self {
            id: raw.id,
            event: raw.event,
            payload,
            received_at: Utc::now(),
        }
    }
}

/// Run the daemon loop until it is cancelled.
pub async fn run(
    args: &DaemonArgs,
    client: &MissionControlClient,
    ctx: AgentContext,
) -> anyhow::Result<()> {
    info!(
        matrix = %args.matrix_endpoint,
        matrix_enabled = !args.disable_matrix,
        agent_id = ?ctx.agent_id,
        shim_host = %args.shim_host,
        shim_port = args.shim_port,
        shim_auth = args.shim_token.is_some(),
        "starting mc daemon"
    );
    if let Some(ref mqtt_url) = args.mqtt_url {
        info!(mqtt_broker = %mqtt_url, "MQTT sync support is experimental and disabled until configured");
    }

    let (tx, _) = broadcast::channel::<MatrixEvent>(256);
    if let Some(port) = args.fanout_port {
        tokio::spawn(start_fanout_server(port, tx.clone()));
        info!(fanout_port = port, "fan-out SSE server started");
    }
    if let Some(mqtt_url) = &args.mqtt_url {
        let mqtt_topic = args.mqtt_topic.clone();
        let mqtt_url = mqtt_url.clone();
        let mqtt_tx = tx.clone();
        tokio::spawn(async move {
            if let Err(err) = start_mqtt_sync(mqtt_url, mqtt_topic, mqtt_tx).await {
                warn!(error = %err, "mqtt sync stopped");
            }
        });
    }

    let shim_ip = args
        .shim_host
        .parse::<std::net::IpAddr>()
        .context("invalid --shim-host value; expected a literal IPv4/IPv6 address")?;
    let shim_addr = SocketAddr::from((shim_ip, args.shim_port));
    let fresh_ttl = Duration::from_secs(args.tools_cache_ttl_sec.max(1));
    let stale_ttl = Duration::from_secs(args.tools_stale_sec.max(args.tools_cache_ttl_sec).max(1));
    let shim_state = ShimApiState::new(
        client.clone(),
        fresh_ttl,
        stale_ttl,
        args.shim_token.clone(),
    );
    let shim_task = tokio::spawn(start_shim_api(shim_addr, shim_state));

    if args.disable_matrix {
        match shim_task.await {
            Ok(server_res) => server_res,
            Err(join_err) => Err(anyhow::anyhow!("shim api task failed: {join_err}")),
        }
    } else {
        tokio::select! {
            stream_res = stream_matrix_events(client.clone(), &args.matrix_endpoint, tx) => stream_res,
            shim_res = shim_task => {
                match shim_res {
                    Ok(server_res) => server_res,
                    Err(join_err) => Err(anyhow::anyhow!("shim api task failed: {join_err}")),
                }
            }
        }
    }
}

async fn stream_matrix_events(
    client: MissionControlClient,
    endpoint: &str,
    broadcaster: broadcast::Sender<MatrixEvent>,
) -> anyhow::Result<()> {
    let mut last_event_id: Option<String> = None;
    let mut backoff = Duration::from_secs(1);
    loop {
        match connect_and_stream(
            &client,
            endpoint,
            last_event_id.clone(),
            broadcaster.clone(),
        )
        .await
        {
            Ok(received_id) => {
                last_event_id = received_id;
                backoff = Duration::from_secs(1);
            }
            Err(err) => {
                warn!(error = %err, "matrix stream failed, backing off");
                tokio::time::sleep(backoff).await;
                backoff = (backoff * 2).min(Duration::from_secs(30));
            }
        }
    }
}

async fn connect_and_stream(
    client: &MissionControlClient,
    endpoint: &str,
    last_event_id: Option<String>,
    broadcaster: broadcast::Sender<MatrixEvent>,
) -> anyhow::Result<Option<String>> {
    let auth_builder = client.request_builder(Method::GET, endpoint)?;
    let mut builder = auth_builder;
    if let Some(ref event_id) = last_event_id {
        builder = builder.header("Last-Event-ID", event_id.clone());
    }
    let response = builder
        .send()
        .await
        .context("failed to open matrix event stream")?
        .error_for_status()
        .context("matrix stream rejected")?;

    let mut stream = response.bytes_stream();
    let mut buffer = String::new();
    let mut last_id = None;
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.context("reading matrix chunk failed")?;
        buffer.push_str(&String::from_utf8_lossy(&chunk));
        while let Some(pos) = buffer.find("\n\n") {
            let block_string = buffer[..pos].to_string();
            buffer = buffer[pos + 2..].to_string();
            if let Some(raw) = parse_sse_block(block_string.trim()) {
                last_id = raw.id.clone();
                let event = MatrixEvent::from_raw(raw);
                println!(
                    "[matrix] {}",
                    serde_json::to_string(&event.payload)
                        .unwrap_or_else(|_| event.payload.to_string())
                );
                let _ = broadcaster.send(event);
            }
        }
    }

    Ok(last_id)
}

fn parse_sse_block(block: &str) -> Option<RawSseChunk> {
    let mut chunk = RawSseChunk {
        id: None,
        event: None,
        data: String::new(),
    };
    for line in block.lines() {
        let line = line.trim_end();
        if line.is_empty() {
            continue;
        }
        let mut parts = line.splitn(2, ':');
        let key = parts.next()?.trim();
        let value = parts.next().unwrap_or("").trim_start();
        match key {
            "data" => {
                if !chunk.data.is_empty() {
                    chunk.data.push('\n');
                }
                chunk.data.push_str(value);
            }
            "id" => chunk.id = Some(value.to_string()),
            "event" => chunk.event = Some(value.to_string()),
            _ => {}
        }
    }
    if chunk.data.is_empty() {
        None
    } else {
        Some(chunk)
    }
}

async fn start_fanout_server(port: u16, broadcaster: broadcast::Sender<MatrixEvent>) {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let listener = match TcpListener::bind(addr).await {
        Ok(listener) => listener,
        Err(err) => {
            warn!(error = %err, port, "failed to bind fan-out port");
            return;
        }
    };

    loop {
        match listener.accept().await {
            Ok((stream, client)) => {
                info!(client = ?client, "fan-out client connected");
                let receiver = broadcaster.subscribe();
                tokio::spawn(async move {
                    if let Err(err) = serve_sse(stream, receiver).await {
                        warn!(client = ?client, error = %err, "fan-out connection error");
                    }
                });
            }
            Err(err) => {
                warn!(error = %err, "fan-out accept failure");
                tokio::time::sleep(Duration::from_secs(1)).await;
            }
        }
    }
}

async fn serve_sse(
    mut stream: TcpStream,
    mut receiver: broadcast::Receiver<MatrixEvent>,
) -> anyhow::Result<()> {
    const HEADER: &[u8] = b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ncache-control: no-cache\r\nconnection: keep-alive\r\n\r\n";
    stream.write_all(HEADER).await?;
    loop {
        match receiver.recv().await {
            Ok(event) => {
                let payload = serde_json::to_string(&event.payload)
                    .unwrap_or_else(|_| event.payload.to_string());
                let mut message = String::from("event: matrix\n");
                if let Some(id) = &event.id {
                    message.push_str("id: ");
                    message.push_str(id);
                    message.push('\n');
                }
                message.push_str("data: ");
                message.push_str(&payload);
                message.push_str("\n\n");
                stream.write_all(message.as_bytes()).await?;
                stream.flush().await?;
            }
            Err(broadcast::error::RecvError::Lagged(count)) => {
                warn!(lagged = count, "fan-out stream lag detected");
            }
            Err(broadcast::error::RecvError::Closed) => break,
        }
    }
    Ok(())
}

async fn start_shim_api(addr: SocketAddr, state: ShimApiState) -> anyhow::Result<()> {
    let app = Router::new()
        .route("/v1/initialize", post(shim_initialize))
        .route("/v1/tools", get(shim_tools))
        .route("/v1/call", post(shim_call))
        .route("/v1/health", get(shim_health))
        .route("/healthz", get(shim_livez))
        .route("/readyz", get(shim_readyz))
        .route("/livez", get(shim_livez))
        .with_state(state);

    let listener = TcpListener::bind(addr)
        .await
        .with_context(|| format!("failed to bind shim API listener at {addr}"))?;
    info!(address = %addr, "shim API server started");
    axum::serve(listener, app)
        .await
        .context("shim API server exited unexpectedly")
}

async fn shim_initialize(
    State(state): State<ShimApiState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if let Err(resp) = check_shim_auth(&headers, &state) {
        return resp;
    }

    let (ready, cache_age_sec) = state.ready_state().await;
    let mut preflight_started = false;
    if !ready {
        let prefetch = state.clone();
        preflight_started = true;
        tokio::spawn(async move {
            if let Err(err) = prefetch.refresh_tools().await {
                warn!(error = %err, category = classify_error_category(&err), "shim initialize prefetch failed");
            }
        });
    }

    Json(serde_json::json!({
        "ok": true,
        "preflight_started": preflight_started,
        "cache_age_sec": cache_age_sec
    }))
    .into_response()
}

fn classify_error_category(err: &anyhow::Error) -> &'static str {
    for cause in err.chain() {
        if let Some(req_err) = cause.downcast_ref::<reqwest::Error>() {
            if req_err.is_timeout() {
                return "timeout";
            }
            if req_err.is_connect() {
                return "network_error";
            }
            if let Some(status) = req_err.status() {
                if status.as_u16() == 401 || status.as_u16() == 403 {
                    return "auth_error";
                }
                if status.is_server_error() {
                    return "http_5xx";
                }
                if status.is_client_error() {
                    return "http_4xx";
                }
            }
        }
    }
    "unknown_error"
}

fn shim_error(
    status: StatusCode,
    category: &str,
    message: impl Into<String>,
) -> axum::response::Response {
    (
        status,
        Json(ShimErrorPayload {
            ok: false,
            error: message.into(),
            category: category.to_string(),
        }),
    )
        .into_response()
}

fn check_shim_auth(
    headers: &HeaderMap,
    state: &ShimApiState,
) -> Result<(), axum::response::Response> {
    let Some(expected) = state.shim_token.as_ref() else {
        return Ok(());
    };
    let auth = headers
        .get("authorization")
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default();
    let token_header = headers
        .get("x-mc-shim-token")
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default();

    let bearer_ok = auth
        .strip_prefix("Bearer ")
        .map(|v| v.trim() == expected)
        .unwrap_or(false);
    let token_ok = token_header == expected;
    if bearer_ok || token_ok {
        Ok(())
    } else {
        Err(shim_error(
            StatusCode::UNAUTHORIZED,
            "auth_error",
            "missing or invalid shim token",
        ))
    }
}

async fn shim_tools(State(state): State<ShimApiState>, headers: HeaderMap) -> impl IntoResponse {
    if let Err(resp) = check_shim_auth(&headers, &state) {
        return resp;
    }

    match state.tools_with_cache().await {
        Ok((tools, cache_state, age_sec, warning)) => Json(serde_json::json!({
            "ok": true,
            "tools": tools,
            "cache": {
                "state": cache_state,
                "age_sec": age_sec,
                "warning": warning
            }
        }))
        .into_response(),
        Err(err) => shim_error(
            StatusCode::BAD_GATEWAY,
            classify_error_category(&err),
            err.to_string(),
        ),
    }
}

async fn shim_call(
    State(state): State<ShimApiState>,
    headers: HeaderMap,
    Json(body): Json<ShimToolCallRequest>,
) -> impl IntoResponse {
    if let Err(resp) = check_shim_auth(&headers, &state) {
        return resp;
    }
    if body.name.trim().is_empty() {
        return shim_error(
            StatusCode::BAD_REQUEST,
            "validation_error",
            "missing tool name",
        );
    }

    let args = if body.arguments.is_object() {
        body.arguments
    } else {
        Value::Object(Default::default())
    };
    let payload = serde_json::json!({
        "tool": body.name,
        "args": args
    });
    match state.client.post_json("/mcp/call", &payload).await {
        Ok(result) => Json(result).into_response(),
        Err(err) => shim_error(
            StatusCode::BAD_GATEWAY,
            classify_error_category(&err),
            err.to_string(),
        ),
    }
}

async fn shim_health(State(state): State<ShimApiState>, headers: HeaderMap) -> impl IntoResponse {
    if let Err(resp) = check_shim_auth(&headers, &state) {
        return resp;
    }
    let (ready, cache_age) = state.ready_state().await;
    Json(serde_json::json!({
        "ok": true,
        "mode": "mc-daemon",
        "ready": ready,
        "cache_tools_age_sec": cache_age,
        "tools_cache_ttl_sec": state.ttl_fresh.as_secs_f64(),
        "tools_stale_sec": state.ttl_stale.as_secs_f64()
    }))
    .into_response()
}

async fn shim_readyz(State(state): State<ShimApiState>, headers: HeaderMap) -> impl IntoResponse {
    if let Err(resp) = check_shim_auth(&headers, &state) {
        return resp;
    }
    let (ready, cache_age) = state.ready_state().await;
    if ready {
        Json(serde_json::json!({
            "ok": true,
            "ready": true,
            "cache_tools_age_sec": cache_age
        }))
        .into_response()
    } else {
        shim_error(
            StatusCode::SERVICE_UNAVAILABLE,
            "not_ready",
            "shim daemon is alive but has no usable tool cache yet",
        )
    }
}

async fn shim_livez() -> impl IntoResponse {
    Json(serde_json::json!({
        "ok": true,
        "alive": true
    }))
}

async fn start_mqtt_sync(
    mqtt_url: String,
    mqtt_topic: String,
    broadcaster: broadcast::Sender<MatrixEvent>,
) -> anyhow::Result<()> {
    let mut options = build_mqtt_options(&mqtt_url)?;
    options.set_keep_alive(Duration::from_secs(30));
    let (client, mut eventloop) = AsyncClient::new(options, 10);
    client
        .subscribe(&mqtt_topic, QoS::AtLeastOnce)
        .await
        .context("mqtt subscribe failed")?;
    loop {
        match eventloop.poll().await {
            Ok(MqttEvent::Incoming(Packet::Publish(publish))) => {
                let payload =
                    serde_json::from_slice::<Value>(&publish.payload).unwrap_or_else(|_| {
                        Value::String(String::from_utf8_lossy(&publish.payload).into())
                    });
                let event = MatrixEvent {
                    id: Some(publish.pkid.to_string()),
                    event: Some("mqtt".to_string()),
                    payload,
                    received_at: Utc::now(),
                };
                let _ = broadcaster.send(event);
            }
            Ok(_) => {}
            Err(err) => {
                warn!(error = %err, "mqtt event loop error");
                tokio::time::sleep(Duration::from_secs(5)).await;
            }
        }
    }
}

fn build_mqtt_options(mqtt_url: &str) -> anyhow::Result<rumqttc::MqttOptions> {
    let parsed = Url::parse(mqtt_url).context("failed to parse mqtt url")?;
    let host = parsed.host_str().context("mqtt url missing host")?;
    let port = parsed.port().unwrap_or(1883);
    let mut options = rumqttc::MqttOptions::new("mc-daemon", host, port);
    if parsed.scheme() == "mqtts" {
        options.set_transport(Transport::tls_with_config(TlsConfiguration::default()));
    }
    Ok(options)
}
