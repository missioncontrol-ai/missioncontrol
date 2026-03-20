//! `mc serve` — stdio JSON-RPC 2.0 MCP server.
//!
//! Speaks the MCP protocol over stdin/stdout (Content-Length framing), proxying
//! tool calls to the MissionControl backend. Designed to be the single binary
//! remote agents install: `mc serve` in `mcpServers.command`.
//!
//! ## Usage
//!
//! ```
//! mc serve
//! # or with debug logging:
//! mc serve --debug-protocol
//! ```
//!
//! ## Protocol
//!
//! - JSON-RPC 2.0 over stdio with Content-Length framing (same as LSP)
//! - Protocol version: "2024-11-05"
//! - Methods: initialize, initialized, tools/list, tools/call, ping
//!
//! ## Reliability design
//!
//! 1. Cache is warmed *synchronously* inside the `initialized` handler before
//!    `notifications/tools/list_changed` is sent. This eliminates the race
//!    where Claude Code calls `tools/list` before the (formerly background)
//!    warm-up task completes.
//!
//! 2. If the backend is down at init time, a retry task runs with exponential
//!    backoff. When tools become available it sends a fresh `listChanged`
//!    notification through an mpsc channel that the main loop writes out.
//!
//! 3. `fetch_tools` returns an empty list on transient errors rather than
//!    propagating them as JSON-RPC errors to the client.

use crate::{client::MissionControlClient, mcp_stdio, mcp_tools};
use anyhow::{Context, Result};
use clap::Args;
use serde_json::{json, Value};
use std::{
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio::io::BufReader;
use tokio::sync::mpsc;

const PROTOCOL_VERSION: &str = "2024-11-05";

// ── CLI args ──────────────────────────────────────────────────────────────────

#[derive(Args, Debug)]
pub struct ServeMcpArgs {
    /// Tools cache TTL in seconds (default: 60)
    #[arg(long, default_value = "60")]
    pub tools_cache_ttl: u64,

    /// Run a preflight health check before entering the message loop.
    ///
    /// Disabled by default because an stdio MCP server must respond to
    /// `initialize` immediately; blocking on a network call delays startup
    /// and causes agents (e.g. Codex) to time out waiting for the handshake.
    /// Enable only when invoking `mc serve` outside an agent context.
    #[arg(long)]
    pub preflight: bool,

    /// Log MCP messages to stderr for debugging
    #[arg(long)]
    pub debug_protocol: bool,
}

// ── Tool cache ────────────────────────────────────────────────────────────────

struct ToolsCache {
    tools: Vec<Value>,
    fetched_at: Option<Instant>,
    ttl: Duration,
}

impl ToolsCache {
    fn new(ttl_secs: u64) -> Self {
        Self {
            tools: Vec::new(),
            fetched_at: None,
            ttl: Duration::from_secs(ttl_secs),
        }
    }

    fn is_fresh(&self) -> bool {
        self.fetched_at
            .map(|t| t.elapsed() < self.ttl)
            .unwrap_or(false)
    }

    fn set(&mut self, tools: Vec<Value>) {
        self.tools = tools;
        self.fetched_at = Some(Instant::now());
    }
}

// ── Entry point ───────────────────────────────────────────────────────────────

pub async fn run(args: &ServeMcpArgs, client: &MissionControlClient) -> Result<()> {
    // Optional preflight: verify connectivity before entering the message loop.
    // Off by default — stdio servers must respond to `initialize` immediately.
    if args.preflight {
        client
            .get_json("/mcp/health")
            .await
            .context("preflight health check failed — verify MC_TOKEN and MC_BASE_URL")?;
        tracing::debug!("mcp_server: preflight ok");
    }

    let cache = Arc::new(Mutex::new(ToolsCache::new(args.tools_cache_ttl)));
    let debug = args.debug_protocol;

    // Channel for background tasks (retry warm-up) to push outbound notifications.
    let (notif_tx, mut notif_rx) = mpsc::channel::<Value>(8);

    let stdin = tokio::io::stdin();
    let mut reader = BufReader::new(stdin);
    let mut stdout = tokio::io::stdout();

    // Track the framing format negotiated during the session (default CL).
    let mut session_format = mcp_stdio::MessageFormat::ContentLength;

    loop {
        tokio::select! {
            // Outbound notifications sent by background retry task.
            Some(notif) = notif_rx.recv() => {
                let serialized = serde_json::to_string(&notif)?;
                if debug {
                    eprintln!("mc serve --> (bg) {}", serialized);
                }
                mcp_stdio::write_message(&mut stdout, &serialized, session_format).await?;
            }

            // Inbound messages from the agent host.
            result = mcp_stdio::read_next_message(&mut reader) => {
                let (raw, format) = match result {
                    Ok(Some(msg)) => msg,
                    Ok(None) => break, // EOF — host closed the pipe
                    Err(e) => {
                        tracing::warn!("mcp_server: failed to read message: {}", e);
                        break;
                    }
                };

                // Remember framing format for outbound notifications.
                session_format = format;

                if debug {
                    eprintln!("mc serve <-- {}", raw);
                }

                let response = match serde_json::from_str::<Value>(&raw) {
                    Ok(msg) => dispatch(msg, client, &cache, &notif_tx).await,
                    Err(e) => Some(error_response(
                        Value::Null,
                        -32700,
                        &format!("parse error: {}", e),
                    )),
                };

                if let Some(resp) = response {
                    let serialized = serde_json::to_string(&resp)?;
                    if debug {
                        eprintln!("mc serve --> {}", serialized);
                    }
                    mcp_stdio::write_message(&mut stdout, &serialized, format).await?;
                }
            }
        }
    }

    Ok(())
}

// ── Message dispatch ──────────────────────────────────────────────────────────

async fn dispatch(
    msg: Value,
    client: &MissionControlClient,
    cache: &Arc<Mutex<ToolsCache>>,
    notif_tx: &mpsc::Sender<Value>,
) -> Option<Value> {
    let id = msg.get("id").cloned().unwrap_or(Value::Null);
    let method = msg
        .get("method")
        .and_then(|m| m.as_str())
        .unwrap_or("")
        .to_string();
    let params = msg.get("params").cloned().unwrap_or(Value::Null);

    // Notifications (no "id" field) receive no response.
    let is_notification = msg.get("id").is_none();

    match method.as_str() {
        "initialize" => {
            // Client hello — return server capabilities.
            let _client_info = params.get("clientInfo");
            Some(json!({
                "jsonrpc": "2.0",
                "id": id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": { "listChanged": true }
                    },
                    "serverInfo": {
                        "name": "missioncontrol",
                        "version": env!("CARGO_PKG_VERSION")
                    }
                }
            }))
        }

        // After the client acknowledges initialization:
        // 1. Warm the cache synchronously so tools/list hits cache on first call.
        // 2. If warm fails (backend down), spawn a retry task that will send
        //    another listChanged once the backend becomes available.
        // 3. Send listChanged to trigger the client to call tools/list.
        "initialized" | "notifications/initialized" => {
            let warmed = match crate::mcp_tools::fetch_tools_from_backend(client).await {
                Ok(tools) if !tools.is_empty() => {
                    let count = tools.len();
                    let mut c = cache.lock().unwrap();
                    c.set(tools);
                    tracing::debug!("mcp_server: cache warmed ({} tools)", count);
                    true
                }
                Ok(_) => {
                    tracing::warn!("mcp_server: backend returned 0 tools at init; will retry");
                    false
                }
                Err(e) => {
                    tracing::warn!("mcp_server: cache warm failed: {}; will retry", e);
                    false
                }
            };

            // If warm failed, kick off a background retry with exponential backoff.
            // The retry task sends a fresh listChanged through the channel when
            // tools become available, prompting Claude Code to re-fetch the list.
            if !warmed {
                let client_clone = client.clone();
                let cache_clone = Arc::clone(cache);
                let tx = notif_tx.clone();
                tokio::spawn(async move {
                    let mut delay = Duration::from_secs(2);
                    for attempt in 1..=6u32 {
                        tokio::time::sleep(delay).await;
                        tracing::debug!("mcp_server: retry warm attempt {}", attempt);
                        match crate::mcp_tools::fetch_tools_from_backend(&client_clone).await {
                            Ok(tools) if !tools.is_empty() => {
                                let count = tools.len();
                                {
                                    let mut c = cache_clone.lock().unwrap();
                                    c.set(tools);
                                }
                                tracing::info!(
                                    "mcp_server: retry warm succeeded ({} tools); sending listChanged",
                                    count
                                );
                                let _ = tx
                                    .send(json!({
                                        "jsonrpc": "2.0",
                                        "method": "notifications/tools/list_changed",
                                        "params": {}
                                    }))
                                    .await;
                                return;
                            }
                            Ok(_) => {
                                tracing::warn!("mcp_server: retry {}: 0 tools", attempt);
                            }
                            Err(e) => {
                                tracing::warn!("mcp_server: retry {}: {}", attempt, e);
                            }
                        }
                        delay = (delay * 2).min(Duration::from_secs(30));
                    }
                    tracing::error!(
                        "mcp_server: all retry attempts exhausted; tools unavailable"
                    );
                });
            }

            // Always send listChanged immediately. If warm succeeded, tools/list
            // will hit the hot cache. If not, the retry task will send another
            // listChanged later when the backend is ready.
            Some(json!({
                "jsonrpc": "2.0",
                "method": "notifications/tools/list_changed",
                "params": {}
            }))
        }

        "notifications/cancelled" => None,

        "tools/list" => match fetch_tools(client, cache).await {
            Ok(tools) => Some(json!({
                "jsonrpc": "2.0",
                "id": id,
                "result": { "tools": tools }
            })),
            Err(e) => Some(error_response(
                id,
                -32603,
                &format!("tools/list failed: {}", e),
            )),
        },

        "tools/call" => {
            let tool_name = params
                .get("name")
                .and_then(|n| n.as_str())
                .unwrap_or("")
                .to_string();
            let tool_args = params.get("arguments").cloned().unwrap_or(json!({}));

            match mcp_tools::call_tool(client, None, None, &tool_name, tool_args).await {
                Ok(result) => {
                    let text = result_to_text(&result);
                    Some(json!({
                        "jsonrpc": "2.0",
                        "id": id,
                        "result": {
                            "content": [{ "type": "text", "text": text }],
                            "isError": false
                        }
                    }))
                }
                Err(e) => {
                    let text = format!("tool '{}' failed: {}", tool_name, e);
                    Some(json!({
                        "jsonrpc": "2.0",
                        "id": id,
                        "result": {
                            "content": [{ "type": "text", "text": text }],
                            "isError": true
                        }
                    }))
                }
            }
        }

        "ping" => Some(json!({ "jsonrpc": "2.0", "id": id, "result": {} })),

        _ => {
            if is_notification {
                None
            } else {
                Some(error_response(
                    id,
                    -32601,
                    &format!("method not found: {}", method),
                ))
            }
        }
    }
}

// ── Tools fetch with TTL cache ────────────────────────────────────────────────

async fn fetch_tools(
    client: &MissionControlClient,
    cache: &Arc<Mutex<ToolsCache>>,
) -> Result<Vec<Value>> {
    // Check freshness under the lock; clone if still valid.
    {
        let c = cache.lock().unwrap();
        if c.is_fresh() {
            return Ok(c.tools.clone());
        }
    }

    // Cache miss — fetch from backend. Return empty list on transient failures
    // rather than propagating the error, which would cause Claude Code to see
    // a JSON-RPC error instead of an empty tool list. The retry task (spawned
    // during initialized) will send a fresh listChanged when ready.
    match mcp_tools::fetch_tools_from_backend(client).await {
        Ok(tools) => {
            let mut c = cache.lock().unwrap();
            c.set(tools.clone());
            Ok(tools)
        }
        Err(e) => {
            tracing::warn!("mcp_server: fetch_tools error: {}; returning empty list", e);
            Ok(Vec::new())
        }
    }
}

// ── I/O helpers ───────────────────────────────────────────────────────────────

fn error_response(id: Value, code: i64, message: &str) -> Value {
    json!({
        "jsonrpc": "2.0",
        "id": id,
        "error": {
            "code": code,
            "message": message
        }
    })
}

/// Flatten a backend result Value into a human-readable string for MCP content.
fn result_to_text(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        other => serde_json::to_string_pretty(other).unwrap_or_else(|_| other.to_string()),
    }
}
