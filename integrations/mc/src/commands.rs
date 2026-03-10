use crate::agent_context::AgentContext;
use crate::booster::AgentBooster;
use crate::client::MissionControlClient;
use crate::config::{ensure_mc_dirs, mc_home_dir, persist_agent_id, skills_home_dir, McConfig};
use crate::daemon::{self, DaemonArgs};
use anyhow::{Context, Result};
use clap::{Args, Subcommand, ValueEnum};
use reqwest::{header, Method};
use serde::Serialize;
use serde_json::{json, Value};
use std::time::{Duration, Instant};
use tokio::time::timeout;
use url::form_urlencoded;
use uuid::Uuid;

/// Top-level CLI entrypoints for the mc binary.
#[derive(Subcommand, Debug)]
pub enum McCommand {
    /// Inspect and invoke Mission Control Python tools.
    #[command(subcommand)]
    Tools(ToolsCommand),
    /// Manage local skill sync state for Klusters and missions.
    #[command(subcommand)]
    Sync(SyncCommand),
    /// Explore missions, klusters, and tasks via the explorer endpoints.
    #[command(subcommand)]
    Explorer(ExplorerCommand),
    /// Governance helpers for policy, events, and audit.
    #[command(subcommand)]
    Admin(AdminCommand),
    /// Approval workflow commands (requests, decisions).
    #[command(subcommand)]
    Approvals(ApprovalCommand),
    /// Workspace lifecycle helpers (load/heartbeat/artifact/commit/release).
    #[command(subcommand)]
    Workspace(WorkspaceCommand),
    /// Simple health & dependency checks inside Mission Control.
    Doctor(DoctorArgs),
    /// Run the async background daemon (matrix + MQTT) described in ARCHITECTURE-COMPARISON-RUFLO.md.
    Daemon(DaemonArgs),
}

#[derive(Args, Debug)]
pub struct DoctorArgs {
    /// Matrix-style SSE endpoint to exercise during diagnostics.
    #[arg(long, default_value = "/events/stream")]
    pub matrix_endpoint: String,

    /// Seconds to wait for the matrix endpoint to respond before timing out.
    #[arg(long, default_value_t = 5)]
    pub matrix_sample_seconds: u64,

    /// Attempt local repairs (directories, agent_id file) where possible.
    #[arg(long)]
    pub repair: bool,
}

#[derive(Subcommand, Debug)]
pub enum ToolsCommand {
    /// List all registered MCP tools.
    List,
    /// Call an MCP tool with JSON payload and show the response.
    Call(ToolsCallArgs),
}

#[derive(Subcommand, Debug)]
pub enum WorkspaceCommand {
    /// Load and lease a Kluster workspace.
    Load {
        #[arg(long)]
        kluster_id: String,
        #[arg(long)]
        workspace_label: Option<String>,
        #[arg(long)]
        agent_id: Option<String>,
        #[arg(long, default_value_t = 900)]
        lease_seconds: u64,
    },
    /// Heartbeat an existing workspace lease.
    Heartbeat {
        #[arg(long)]
        lease_id: String,
    },
    /// Fetch an artifact via the lease (download URL or inline content).
    FetchArtifact {
        #[arg(long)]
        lease_id: String,
        #[arg(long)]
        artifact_id: i32,
        #[arg(long, default_value = "download_url")]
        mode: String,
        #[arg(long, default_value_t = 60)]
        expires_seconds: u64,
    },
    /// Commit workspace changes with a JSON change_set.
    Commit {
        #[arg(long)]
        lease_id: String,
        #[arg(long)]
        change_set: String,
        #[arg(long)]
        validation_mode: Option<String>,
    },
    /// Release a lease with an optional reason.
    Release {
        #[arg(long)]
        lease_id: String,
        #[arg(long)]
        reason: Option<String>,
    },
}

#[derive(Args, Debug)]
pub struct ToolsCallArgs {
    /// Name of the MCP tool to call (e.g. missioncontrol.kluster.load).
    #[arg(long, short)]
    tool: String,
    /// JSON payload to send as MCP tool args. Defaults to empty object.
    #[arg(long, default_value = "{}")]
    payload: String,
}

#[derive(Subcommand, Debug)]
pub enum SyncCommand {
    /// Retrieve the last sync status for a mission/kluster/agent.
    Status(SyncStatusArgs),
    /// Promote a skill sync snapshot to Mission Control’s ledger.
    Promote(SyncPromoteArgs),
}

#[derive(Args, Debug)]
pub struct SyncStatusArgs {
    #[arg(long)]
    mission_id: String,
    #[arg(long)]
    kluster_id: Option<String>,
    #[arg(long)]
    agent_id: Option<String>,
}

#[derive(Args, Debug)]
pub struct SyncPromoteArgs {
    #[arg(long)]
    mission_id: String,
    #[arg(long)]
    snapshot_id: String,
    #[arg(long)]
    snapshot_sha256: String,
    #[arg(long)]
    local_overlay_sha256: Option<String>,
    #[arg(long)]
    kluster_id: Option<String>,
    #[arg(long)]
    agent_id: Option<String>,
    #[arg(long, default_value_t = false)]
    degraded_offline: bool,
    #[arg(long, default_value_t = false)]
    drift_flag: bool,
    #[arg(long, default_value = "{}")]
    drift_details: String,
}

#[derive(Subcommand, Debug)]
pub enum ExplorerCommand {
    /// Dump the tree-view of missions, klusters, and recent tasks.
    Tree(ExplorerTreeArgs),
    /// Inspect a single mission/kluster/task node.
    Node(ExplorerNodeArgs),
}

#[derive(Args, Debug)]
pub struct ExplorerTreeArgs {
    #[arg(long)]
    mission_id: Option<String>,
    #[arg(long)]
    status: Option<String>,
    #[arg(long)]
    q: Option<String>,
    #[arg(long)]
    limit_tasks_per_cluster: Option<u32>,
    #[arg(long)]
    limit_klusters: Option<u32>,
}

#[derive(Args, Debug)]
pub struct ExplorerNodeArgs {
    #[arg(long, value_enum)]
    node_type: ExplorerNodeType,
    #[arg(long)]
    node_id: String,
}

#[derive(ValueEnum, Clone, Debug)]
pub enum ExplorerNodeType {
    Mission,
    Kluster,
    Task,
}

impl ExplorerNodeType {
    fn as_str(&self) -> &'static str {
        match self {
            ExplorerNodeType::Mission => "mission",
            ExplorerNodeType::Kluster => "kluster",
            ExplorerNodeType::Task => "task",
        }
    }
}

#[derive(Subcommand, Debug)]
pub enum AdminCommand {
    /// Show the currently active governance policy.
    Active,
    /// List previous policy versions (limit defaults to 50).
    Versions {
        #[arg(long)]
        limit: Option<u32>,
    },
    /// Show the recent policy events emitted from approvals.
    Events {
        #[arg(long)]
        limit: Option<u32>,
    },
}

#[derive(Subcommand, Debug)]
pub enum ApprovalCommand {
    /// Create an approval request for a mission action.
    Create {
        #[arg(long)]
        mission_id: String,
        #[arg(long)]
        action: String,
        #[arg(long)]
        channel: Option<String>,
        #[arg(long)]
        reason: Option<String>,
        #[arg(long)]
        target_entity_type: Option<String>,
        #[arg(long)]
        target_entity_id: Option<String>,
        #[arg(long)]
        request_context: Option<String>,
        #[arg(long)]
        expires_in_seconds: Option<u64>,
    },
    /// List approval requests for a mission.
    List {
        #[arg(long)]
        mission_id: String,
        #[arg(long)]
        status: Option<String>,
        #[arg(long)]
        limit: Option<u32>,
    },
    /// Approve a pending request.
    Approve {
        #[arg(long)]
        approval_id: i32,
        #[arg(long)]
        expires_in_seconds: Option<u64>,
        #[arg(long)]
        note: Option<String>,
    },
    /// Reject a pending request.
    Reject {
        #[arg(long)]
        approval_id: i32,
        #[arg(long)]
        note: Option<String>,
    },
}

pub async fn run(
    command: McCommand,
    client: MissionControlClient,
    ctx: AgentContext,
    booster: AgentBooster,
    config: McConfig,
) -> Result<()> {
    match command {
        McCommand::Tools(cmd) => handle_tools(cmd, client, &booster).await,
        McCommand::Sync(cmd) => handle_sync(cmd, client).await,
        McCommand::Explorer(cmd) => handle_explorer(cmd, client).await,
        McCommand::Admin(cmd) => handle_admin(cmd, client).await,
        McCommand::Workspace(cmd) => handle_workspace(cmd, client, &booster).await,
        McCommand::Approvals(cmd) => handle_approvals(cmd, client).await,
        McCommand::Doctor(args) => handle_doctor(client, &config, &args).await,
        McCommand::Daemon(args) => daemon::run(&args, &client, ctx).await,
    }
}

async fn handle_tools(
    command: ToolsCommand,
    client: MissionControlClient,
    booster: &AgentBooster,
) -> Result<()> {
    match command {
        ToolsCommand::List => {
            let response = client.get_json("/mcp/tools").await?;
            print_json(&response);
        }
        ToolsCommand::Call(args) => {
            let payload = serde_json::from_str::<Value>(&args.payload)
                .context("failed to parse payload JSON")?;
            let response = call_mcp_tool(&client, booster, &args.tool, payload).await?;
            print_json(&response);
        }
    }
    Ok(())
}

async fn call_mcp_tool(
    client: &MissionControlClient,
    booster: &AgentBooster,
    tool: &str,
    args_value: Value,
) -> Result<Value> {
    if booster.is_enabled() {
        let short_circuit = booster
            .run(&args_value)
            .context("booster validation failed")?;
        if short_circuit {
            println!("[booster] short-circuited {tool}");
            return Ok(json!({ "booster_short_circuit": true, "tool": tool }));
        }
    }
    let request = json!({
        "tool": tool,
        "args": args_value,
    });
    client.post_json("/mcp/call", &request).await
}

async fn handle_sync(command: SyncCommand, client: MissionControlClient) -> Result<()> {
    match command {
        SyncCommand::Status(args) => {
            let mut serializer = form_urlencoded::Serializer::new(String::new());
            serializer.append_pair("mission_id", &args.mission_id);
            if let Some(kluster) = &args.kluster_id {
                serializer.append_pair("kluster_id", kluster);
            }
            if let Some(agent) = &args.agent_id {
                serializer.append_pair("agent_id", agent);
            }
            let path = build_path_with_query("/skills/sync/status", serializer.finish());
            let response = client.get_json(&path).await?;
            print_json(&response);
        }
        SyncCommand::Promote(args) => {
            let mut body = json!({
                "mission_id": args.mission_id,
                "snapshot_id": args.snapshot_id,
                "snapshot_sha256": args.snapshot_sha256,
                "degraded_offline": args.degraded_offline,
                "drift_flag": args.drift_flag,
            });
            if let Some(kluster_id) = args.kluster_id {
                body["kluster_id"] = json!(kluster_id);
            }
            if let Some(agent_id) = args.agent_id {
                body["agent_id"] = json!(agent_id);
            }
            if let Some(local_overlay) = args.local_overlay_sha256 {
                body["local_overlay_sha256"] = json!(local_overlay);
            }
            let drift_details: Value = serde_json::from_str(&args.drift_details)
                .context("failed to decode drift_details JSON")?;
            body["drift_details"] = drift_details;
            let response = client.post_json("/skills/sync/ack", &body).await?;
            print_json(&response);
        }
    }
    Ok(())
}

async fn handle_workspace(
    command: WorkspaceCommand,
    client: MissionControlClient,
    booster: &AgentBooster,
) -> Result<()> {
    match command {
        WorkspaceCommand::Load {
            kluster_id,
            workspace_label,
            agent_id,
            lease_seconds,
        } => {
            let mut args = json!({
                "kluster_id": kluster_id,
                "lease_seconds": lease_seconds,
            });
            if let Some(label) = workspace_label {
                args["workspace_label"] = json!(label);
            }
            if let Some(agent) = agent_id {
                args["agent_id"] = json!(agent);
            }
            let response = call_mcp_tool(&client, booster, "load_kluster_workspace", args).await?;
            print_json(&response);
        }
        WorkspaceCommand::Heartbeat { lease_id } => {
            let args = json!({"lease_id": lease_id});
            let response =
                call_mcp_tool(&client, booster, "heartbeat_workspace_lease", args).await?;
            print_json(&response);
        }
        WorkspaceCommand::FetchArtifact {
            lease_id,
            artifact_id,
            mode,
            expires_seconds,
        } => {
            let args = json!({
                "lease_id": lease_id,
                "artifact_id": artifact_id,
                "mode": mode,
                "expires_seconds": expires_seconds,
            });
            let response =
                call_mcp_tool(&client, booster, "fetch_workspace_artifact", args).await?;
            print_json(&response);
        }
        WorkspaceCommand::Commit {
            lease_id,
            change_set,
            validation_mode,
        } => {
            let change_set_value: Value =
                serde_json::from_str(&change_set).context("change_set must be valid JSON array")?;
            let mut args = json!({
                "lease_id": lease_id,
                "change_set": change_set_value,
            });
            if let Some(mode) = validation_mode {
                args["validation_mode"] = json!(mode);
            }
            let response =
                call_mcp_tool(&client, booster, "commit_kluster_workspace", args).await?;
            print_json(&response);
        }
        WorkspaceCommand::Release { lease_id, reason } => {
            let mut args = json!({"lease_id": lease_id});
            if let Some(reason_value) = reason {
                args["reason"] = json!(reason_value);
            }
            let response =
                call_mcp_tool(&client, booster, "release_kluster_workspace", args).await?;
            print_json(&response);
        }
    }
    Ok(())
}

async fn handle_explorer(command: ExplorerCommand, client: MissionControlClient) -> Result<()> {
    match command {
        ExplorerCommand::Tree(args) => {
            let mut serializer = form_urlencoded::Serializer::new(String::new());
            if let Some(mission_id) = &args.mission_id {
                serializer.append_pair("mission_id", mission_id);
            }
            if let Some(status) = &args.status {
                serializer.append_pair("status", status);
            }
            if let Some(query) = &args.q {
                serializer.append_pair("q", query);
            }
            if let Some(limit) = args.limit_tasks_per_cluster {
                serializer.append_pair("limit_tasks_per_cluster", &limit.to_string());
            }
            if let Some(limit) = args.limit_klusters {
                serializer.append_pair("limit_klusters", &limit.to_string());
            }
            let path = build_path_with_query("/explorer/tree", serializer.finish());
            let response = client.get_json(&path).await?;
            print_json(&response);
        }
        ExplorerCommand::Node(args) => {
            let path = format!(
                "/explorer/node/{}/{}",
                args.node_type.as_str(),
                args.node_id
            );
            let response = client.get_json(&path).await?;
            print_json(&response);
        }
    }
    Ok(())
}

async fn handle_admin(command: AdminCommand, client: MissionControlClient) -> Result<()> {
    match command {
        AdminCommand::Active => {
            let response = client.get_json("/governance/policy/active").await?;
            print_json(&response);
        }
        AdminCommand::Versions { limit } => {
            let mut serializer = form_urlencoded::Serializer::new(String::new());
            if let Some(limit) = limit {
                serializer.append_pair("limit", &limit.to_string());
            }
            let path = build_path_with_query("/governance/policy/versions", serializer.finish());
            let response = client.get_json(&path).await?;
            print_json(&response);
        }
        AdminCommand::Events { limit } => {
            let mut serializer = form_urlencoded::Serializer::new(String::new());
            if let Some(limit) = limit {
                serializer.append_pair("limit", &limit.to_string());
            }
            let path = build_path_with_query("/governance/policy/events", serializer.finish());
            let response = client.get_json(&path).await?;
            print_json(&response);
        }
    }
    Ok(())
}

async fn handle_approvals(command: ApprovalCommand, client: MissionControlClient) -> Result<()> {
    match command {
        ApprovalCommand::Create {
            mission_id,
            action,
            channel,
            reason,
            target_entity_type,
            target_entity_id,
            request_context,
            expires_in_seconds,
        } => {
            let mut body = json!({
                "mission_id": mission_id,
                "action": action,
            });
            if let Some(channel) = channel {
                body["channel"] = json!(channel);
            }
            if let Some(reason) = reason {
                body["reason"] = json!(reason);
            }
            if let Some(target_type) = target_entity_type {
                body["target_entity_type"] = json!(target_type);
            }
            if let Some(target_id) = target_entity_id {
                body["target_entity_id"] = json!(target_id);
            }
            if let Some(req_ctx) = request_context {
                body["request_context"] =
                    serde_json::from_str(&req_ctx).unwrap_or_else(|_| json!({ "raw": req_ctx }));
            }
            if let Some(expires) = expires_in_seconds {
                body["expires_in_seconds"] = json!(expires);
            }
            let response = client.post_json("/approvals/requests", &body).await?;
            print_json(&response);
        }
        ApprovalCommand::List {
            mission_id,
            status,
            limit,
        } => {
            let mut serializer = form_urlencoded::Serializer::new(String::new());
            serializer.append_pair("mission_id", &mission_id);
            if let Some(status) = status {
                serializer.append_pair("status", &status);
            }
            if let Some(limit) = limit {
                serializer.append_pair("limit", &limit.to_string());
            }
            let path = build_path_with_query("/approvals", serializer.finish());
            let response = client.get_json(&path).await?;
            print_json(&response);
        }
        ApprovalCommand::Approve {
            approval_id,
            expires_in_seconds,
            note,
        } => {
            let mut body = json!({});
            if let Some(expires) = expires_in_seconds {
                body["expires_in_seconds"] = json!(expires);
            }
            if let Some(note) = note {
                body["note"] = json!(note);
            }
            let path = format!("/approvals/{}/approve", approval_id);
            let response = client.post_json(&path, &body).await?;
            print_json(&response);
        }
        ApprovalCommand::Reject { approval_id, note } => {
            let mut body = json!({});
            if let Some(note) = note {
                body["note"] = json!(note);
            }
            let path = format!("/approvals/{}/reject", approval_id);
            let response = client.post_json(&path, &body).await?;
            print_json(&response);
        }
    }
    Ok(())
}

async fn handle_doctor(
    client: MissionControlClient,
    config: &McConfig,
    args: &DoctorArgs,
) -> Result<()> {
    let checks = vec![
        run_health_check(&client).await,
        run_tools_check(&client).await,
        run_matrix_check(
            &client,
            &args.matrix_endpoint,
            Duration::from_secs(args.matrix_sample_seconds),
        )
        .await,
    ];
    let repairs = if args.repair {
        perform_repairs(config)
    } else {
        Vec::new()
    };
    let report = DoctorReport {
        base_url: config.base_url.to_string(),
        agent_id: config.agent_context.agent_id.clone(),
        matrix_endpoint: args.matrix_endpoint.clone(),
        checks,
        repairs,
    };
    println!(
        "Doctor report ({} checks, {} repairs)",
        report.checks.len(),
        report.repairs.len()
    );
    println!(
        "{}",
        serde_json::to_string_pretty(&report).context("failed to serialize doctor report")?
    );
    Ok(())
}

async fn run_health_check(client: &MissionControlClient) -> DoctorCheck {
    let start = Instant::now();
    let name = "mcp_health".to_string();
    match client.get_json("/mcp/health").await {
        Ok(payload) => DoctorCheck {
            name,
            ok: true,
            detail: "mcp health OK".into(),
            duration_ms: start.elapsed().as_millis(),
            payload: Some(payload),
            repair_hint: None,
        },
        Err(err) => DoctorCheck {
            name,
            ok: false,
            detail: err.to_string(),
            duration_ms: start.elapsed().as_millis(),
            payload: None,
            repair_hint: Some("Check MC_BASE_URL/MCP_TOKEN or OIDC configuration".into()),
        },
    }
}

async fn run_tools_check(client: &MissionControlClient) -> DoctorCheck {
    let start = Instant::now();
    let name = "mcp_tools".to_string();
    match client.get_json("/mcp/tools").await {
        Ok(payload) => DoctorCheck {
            name,
            ok: true,
            detail: "tools list succeeded".into(),
            duration_ms: start.elapsed().as_millis(),
            payload: Some(payload),
            repair_hint: None,
        },
        Err(err) => DoctorCheck {
            name,
            ok: false,
            detail: err.to_string(),
            duration_ms: start.elapsed().as_millis(),
            payload: None,
            repair_hint: Some("Ensure approvals/tools access and tokens are valid".into()),
        },
    }
}

async fn run_matrix_check(
    client: &MissionControlClient,
    endpoint: &str,
    sample_duration: Duration,
) -> DoctorCheck {
    let start = Instant::now();
    let name = "matrix_stream".to_string();
    let builder = match client.request_builder(Method::GET, endpoint) {
        Ok(builder) => builder,
        Err(err) => {
            return DoctorCheck {
                name,
                ok: false,
                detail: err.to_string(),
                duration_ms: start.elapsed().as_millis(),
                payload: None,
                repair_hint: Some("Invalid matrix endpoint; update --matrix-endpoint".into()),
            }
        }
    };
    let response = match timeout(
        sample_duration,
        builder.header(header::ACCEPT, "text/event-stream").send(),
    )
    .await
    {
        Ok(Ok(response)) => response,
        Ok(Err(err)) => {
            let hint = if err.to_string().to_lowercase().contains("tls") {
                Some(
                    "Run with --allow-insecure/MC_ALLOW_INSECURE=true for self-signed certs".into(),
                )
            } else {
                Some("Ensure /events/stream is reachable and not throttled".into())
            };
            return DoctorCheck {
                name,
                ok: false,
                detail: err.to_string(),
                duration_ms: start.elapsed().as_millis(),
                payload: None,
                repair_hint: hint,
            };
        }
        Err(_) => {
            return DoctorCheck {
                name,
                ok: false,
                detail: "matrix endpoint timed out".into(),
                duration_ms: start.elapsed().as_millis(),
                payload: None,
                repair_hint: Some("Verify the server is reachable and emitting events".into()),
            }
        }
    };
    let status = response.status();
    let content_type = response
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default()
        .to_string();
    let ok = status.is_success() && content_type.contains("event-stream");
    let detail = if ok {
        "matrix endpoint streaming".into()
    } else {
        format!(
            "matrix endpoint returned {} with content-type {}",
            status, content_type
        )
    };
    let payload = Some(json!({
        "status": status.as_u16(),
        "content_type": content_type,
    }));
    drop(response);
    DoctorCheck {
        name,
        ok,
        detail,
        duration_ms: start.elapsed().as_millis(),
        payload,
        repair_hint: if ok {
            None
        } else {
            Some("Confirm the matrix listener is enabled and not blocked by firewalls".into())
        },
    }
}

fn perform_repairs(config: &McConfig) -> Vec<DoctorRepair> {
    let mut repairs = Vec::new();
    match ensure_mc_dirs() {
        Ok(()) => repairs.push(DoctorRepair::ok(
            "directories",
            format!(
                "Ensured MC_HOME={} and skills dir {}",
                mc_home_dir().display(),
                skills_home_dir().display()
            ),
        )),
        Err(err) => repairs.push(DoctorRepair::failed("directories", err.to_string())),
    }
    if config.agent_context.agent_id.is_none() {
        let agent_id = format!("mc-agent-{}", Uuid::new_v4());
        match persist_agent_id(&agent_id) {
            Ok(()) => repairs.push(DoctorRepair::ok(
                "agent_id",
                format!(
                    "Persisted agent_id {} at {}/agent_id",
                    agent_id,
                    mc_home_dir().display()
                ),
            )),
            Err(err) => repairs.push(DoctorRepair::failed("agent_id", err.to_string())),
        }
    } else {
        repairs.push(DoctorRepair::ok(
            "agent_id",
            "Agent ID already configured".into(),
        ));
    }
    repairs
}

#[derive(Serialize)]
struct DoctorReport {
    base_url: String,
    agent_id: Option<String>,
    matrix_endpoint: String,
    checks: Vec<DoctorCheck>,
    repairs: Vec<DoctorRepair>,
}

#[derive(Serialize)]
struct DoctorCheck {
    name: String,
    ok: bool,
    detail: String,
    duration_ms: u128,
    payload: Option<Value>,
    repair_hint: Option<String>,
}

#[derive(Serialize)]
struct DoctorRepair {
    name: String,
    success: bool,
    detail: String,
}

impl DoctorRepair {
    fn ok(name: &str, detail: String) -> Self {
        Self {
            name: name.to_string(),
            success: true,
            detail,
        }
    }

    fn failed(name: &str, detail: String) -> Self {
        Self {
            name: name.to_string(),
            success: false,
            detail,
        }
    }
}

fn build_path_with_query(base: &str, query: String) -> String {
    if query.is_empty() {
        base.to_string()
    } else {
        format!("{}?{}", base, query)
    }
}

fn print_json(value: &Value) {
    println!(
        "{}",
        serde_json::to_string_pretty(value).unwrap_or_else(|_| value.to_string())
    );
}
