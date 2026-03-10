use crate::agent_context::AgentContext;
use crate::client::MissionControlClient;
use anyhow::Context;
use chrono::{DateTime, Utc};
use clap::Args;
use futures_util::StreamExt;
use reqwest::Method;
use rumqttc::{AsyncClient, Event as MqttEvent, Packet, QoS, TlsConfiguration, Transport};
use serde::Serialize;
use serde_json::Value;
use std::{net::SocketAddr, time::Duration};
use tokio::io::AsyncWriteExt;
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::broadcast;
use tracing::{info, warn};
use url::Url;

/// CLI arguments that control the daemon behavior.
#[derive(Args, Debug)]
pub struct DaemonArgs {
    /// Matrix-style SSE endpoint relative to the configured base URL.
    #[arg(long, default_value = "/events/stream")]
    pub matrix_endpoint: String,

    /// Optional MQTT broker URL the daemon will keep alive while syncing.
    #[arg(long)]
    pub mqtt_url: Option<String>,

    /// MQTT topic to keep subscribed to (default `missioncontrol/inbox`).
    #[arg(long, default_value = "missioncontrol/inbox")]
    pub mqtt_topic: String,

    /// Optional port to host a local SSE fan-out server for dashboards or Ruflo queens.
    #[arg(long)]
    pub fanout_port: Option<u16>,
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
    info!(matrix = %args.matrix_endpoint, agent_id = ?ctx.agent_id, "starting mc daemon");
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

    stream_matrix_events(client.clone(), &args.matrix_endpoint, tx).await
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
