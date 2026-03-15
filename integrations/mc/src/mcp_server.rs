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

use crate::{client::MissionControlClient, mcp_tools};
use anyhow::{Context, Result};
use clap::Args;
use serde_json::{json, Value};
use std::{
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};

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

    let stdin = tokio::io::stdin();
    let mut reader = BufReader::new(stdin);
    let mut stdout = tokio::io::stdout();

    loop {
        // 1. Read either JSONL body or Content-Length framed body.
        let raw = match read_next_message(&mut reader).await {
            Ok(Some(body)) => body,
            Ok(None) => break, // EOF — host closed the pipe
            Err(e) => {
                tracing::warn!("mcp_server: failed to read message: {}", e);
                break;
            }
        };
        if debug {
            eprintln!("mc serve <-- {}", raw);
        }

        // 3. Parse and dispatch.
        let response = match serde_json::from_str::<Value>(&raw) {
            Ok(msg) => dispatch(msg, client, &cache).await,
            Err(e) => Some(error_response(
                Value::Null,
                -32700,
                &format!("parse error: {}", e),
            )),
        };

        // 4. Notifications produce no response; everything else is framed and flushed.
        if let Some(resp) = response {
            let serialized = serde_json::to_string(&resp)?;
            if debug {
                eprintln!("mc serve --> {}", serialized);
            }
            let framed = format!("Content-Length: {}\r\n\r\n{}", serialized.len(), serialized);
            stdout.write_all(framed.as_bytes()).await?;
            stdout.flush().await?;
        }
    }

    Ok(())
}

// ── Message dispatch ──────────────────────────────────────────────────────────

async fn dispatch(
    msg: Value,
    client: &MissionControlClient,
    cache: &Arc<Mutex<ToolsCache>>,
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
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "missioncontrol",
                        "version": env!("CARGO_PKG_VERSION")
                    }
                }
            }))
        }

        // No-op notifications.
        "initialized" | "notifications/initialized" | "notifications/cancelled" => None,

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

    // Cache miss — fetch from backend.
    let response = client
        .get_json("/mcp/tools")
        .await
        .context("failed to fetch tools from backend")?;

    let tools: Vec<Value> = match response {
        Value::Array(arr) => arr,
        Value::Object(ref obj) => {
            // Backend may wrap the list: {"tools": [...]}
            obj.get("tools")
                .and_then(|t| t.as_array())
                .cloned()
                .unwrap_or_default()
        }
        _ => Vec::new(),
    };

    {
        let mut c = cache.lock().unwrap();
        c.set(tools.clone());
    }

    Ok(tools)
}

// ── I/O helpers ───────────────────────────────────────────────────────────────

/// Read one MCP message from stdin.
///
/// Supports:
/// - Content-Length framed messages (LSP style)
/// - newline-delimited JSON messages (JSONL style)
async fn read_next_message(
    reader: &mut BufReader<tokio::io::Stdin>,
) -> Result<Option<String>> {
    let first = loop {
        let mut first_line = String::new();
        let n = reader.read_line(&mut first_line).await?;
        if n == 0 {
            return Ok(None);
        }
        let first = first_line.trim().to_string();
        if first.is_empty() {
            continue;
        }
        break first;
    };

    if first.starts_with('{') {
        return Ok(Some(first));
    }

    // Otherwise treat as Content-Length header block.
    let content_length = read_content_length_with_first_line(reader, first).await?;
    let mut body = vec![0u8; content_length];
    reader.read_exact(&mut body).await.context("read body")?;
    Ok(Some(String::from_utf8_lossy(&body).to_string()))
}

async fn read_content_length_with_first_line(
    reader: &mut BufReader<tokio::io::Stdin>,
    first_line: String,
) -> Result<usize> {
    let mut content_length: Option<usize> = None;
    let mut pending_first = if first_line.is_empty() {
        None
    } else {
        Some(first_line)
    };
    loop {
        let line = if let Some(first) = pending_first.take() {
            first
        } else {
            let mut line = String::new();
            let n = reader.read_line(&mut line).await?;
            if n == 0 {
                anyhow::bail!("unexpected EOF while reading Content-Length headers");
            }
            line
        };
        let trimmed = line.trim();
        if trimmed.is_empty() {
            // Blank line separates headers from body.
            break;
        }
        if let Some((name, value)) = trimmed.split_once(':') {
            if name.trim().eq_ignore_ascii_case("Content-Length") {
                let val: usize = value
                    .trim()
                    .parse()
                    .context("invalid Content-Length value")?;
                content_length = Some(val);
            }
        }
        // Other headers (Content-Type etc.) are ignored.
    }
    content_length.ok_or_else(|| anyhow::anyhow!("missing Content-Length header"))
}

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
