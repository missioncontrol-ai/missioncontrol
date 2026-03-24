use crate::{
    agent_context::AgentContext,
    auth,
    booster::AgentBooster,
    client::MissionControlClient,
    compat,
    config::McConfig,
    daemon::{self, DaemonArgs},
    drift, evolve, governance, launch, maintenance, mcp_server, mcp_tools, ops,
    output::{self, OutputMode},
    remote,
    schema_pack::SchemaPack,
    update,
};
use anyhow::{Context, Result};
use base64::Engine;
use clap::{Args, CommandFactory, Subcommand, ValueEnum};
use clap_complete::Shell;
use serde_json::{json, Value};
use std::fs;
use std::io::Cursor;
use std::path::PathBuf;
use tar::{Archive, Builder};
use url::form_urlencoded;

/// Top-level CLI entrypoints for the mc binary.
#[derive(Subcommand, Debug)]
pub enum McCommand {
    /// Show quick local/runtime/auth context for the current shell.
    Status(StatusArgs),
    /// Shortcut for `mc system doctor`.
    Doctor(maintenance::DoctorArgs),
    /// Lightweight backend readiness check.
    Health(HealthArgs),
    /// Show binary + backend version details.
    Version(VersionArgs),
    /// Show effective runtime config (redacted).
    Config(ConfigArgs),
    /// Convenience context/profile switcher.
    Use(UseArgs),
    /// Release the currently attached workspace lease.
    Release(ReleaseArgs),
    /// Tail local MissionControl logs.
    Logs(LogsArgs),
    /// Generate shell completion scripts.
    Completion(CompletionArgs),
    /// Authentication and identity helpers.
    #[command(subcommand)]
    Auth(AuthCommand),
    /// Governance and admin workflows.
    #[command(subcommand)]
    Admin(AdminCommand),
    /// Data/catalog/read workflows (tools, sync, explorer).
    #[command(subcommand)]
    Data(DataCommand),
    /// Platform diagnostics and release-control workflows.
    #[command(subcommand)]
    System(SystemCommand),
    /// Agent control workflows (remote, evolve, swarm/subagent workflows).
    #[command(subcommand)]
    Agent(AgentCommand),
    /// Approval workflow commands (requests, decisions).
    #[command(subcommand)]
    Approvals(ApprovalCommand),
    /// Workspace lifecycle helpers (load/heartbeat/artifact/commit/release).
    #[command(subcommand)]
    Workspace(WorkspaceCommand),
    /// Mission operations (lifecycle orchestration and execution workflows).
    #[command(subcommand)]
    Ops(ops::OpsCommand),
    /// Run the async background daemon (matrix + MQTT).
    Daemon(DaemonArgs),
    /// Launch an agent with a fully wired MissionControl harness.
    Launch(launch::LaunchArgs),
    /// Initialize MC profile state for first-time usage.
    Init(InitArgs),
    /// Start an MCP server (stdio JSON-RPC 2.0) for LLM runtime connections.
    Serve(mcp_server::ServeMcpArgs),
    /// Manage MissionControl user profiles.
    #[command(subcommand)]
    Profile(ProfileCommand),
}

#[derive(Subcommand, Debug)]
pub enum AuthCommand {
    /// Authenticate and create a session token stored at ~/.missioncontrol/session.json.
    Login(auth::LoginArgs),
    /// Revoke the current session token and clear local credentials.
    Logout(auth::LogoutArgs),
    /// Show the current authenticated identity.
    Whoami(auth::WhoamiArgs),
}

#[derive(Subcommand, Debug)]
pub enum DataCommand {
    /// Inspect and invoke MissionControl MCP tools.
    #[command(subcommand)]
    Tools(ToolsCommand),
    /// Manage local skill sync state for Klusters and missions.
    #[command(subcommand)]
    Sync(SyncCommand),
    /// Explore missions, klusters, and tasks via the explorer endpoints.
    #[command(subcommand)]
    Explorer(ExplorerCommand),
}

#[derive(Subcommand, Debug)]
pub enum SystemCommand {
    /// Diagnostics + auto-fix helpers.
    Doctor(maintenance::DoctorArgs),
    /// Trigger local backups (postgres, rustfs, or both).
    Backup(maintenance::BackupArgs),
    /// Cleanup local profile/session artifacts with retention limits.
    ProfileGc(maintenance::ProfileGcArgs),
    /// Self-update helper for the mc binary.
    #[command(subcommand)]
    Update(update::UpdateCommand),
    /// Compatibility matrix commands and reports for provider/version drift control.
    #[command(subcommand)]
    Compat(compat::CompatCommand),
    /// Drift ingestion + policy decision helpers for staged release gates.
    #[command(subcommand)]
    Drift(drift::DriftCommand),
}

#[derive(Subcommand, Debug)]
pub enum AgentCommand {
    /// Remote agent control verbs.
    #[command(subcommand)]
    Remote(remote::RemoteCommand),
    /// Self-improvement loop for MissionControl itself (agent-driven backlog/code evolution).
    Evolve(evolve::EvolveArgs),
}

#[derive(Args, Debug)]
pub struct InitArgs {
    /// Initial profile name to create when none exists.
    #[arg(long, default_value = "default")]
    profile: String,
}

#[derive(Args, Debug, Default)]
pub struct StatusArgs {
    /// Validate active lease by sending a heartbeat call.
    #[arg(long, default_value_t = false)]
    verify_lease: bool,
}

#[derive(Args, Debug, Default)]
pub struct HealthArgs {}

#[derive(Args, Debug, Default)]
pub struct VersionArgs {}

#[derive(Args, Debug, Default)]
pub struct ConfigArgs {}

#[derive(Args, Debug, Default)]
pub struct UseArgs {
    #[arg(long)]
    profile: Option<String>,
    #[arg(long)]
    kluster_id: Option<String>,
    #[arg(long, default_value_t = 900)]
    lease_seconds: u64,
    #[arg(long)]
    workspace_label: Option<String>,
    /// Auto-release existing lease when switching klusters.
    #[arg(long, default_value_t = false)]
    auto_release: bool,
    /// Non-interactive confirmation for releasing/switching.
    #[arg(short = 'y', long, default_value_t = false)]
    yes: bool,
    /// Release currently attached lease instead of attaching a kluster.
    #[arg(long, default_value_t = false)]
    release: bool,
}

#[derive(Args, Debug, Default)]
pub struct ReleaseArgs {
    /// Optional reason recorded in lease release metadata.
    #[arg(long)]
    reason: Option<String>,
    /// Succeed even when no active lease is tracked.
    #[arg(long, default_value_t = false)]
    ignore_missing: bool,
}

#[derive(Args, Debug, Default)]
pub struct LogsArgs {
    #[arg(long, default_value_t = 120)]
    lines: usize,
}

#[derive(Args, Debug)]
pub struct CompletionArgs {
    #[arg(value_enum)]
    shell: Shell,
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
        /// When mode=content, decode and write bytes to this local path.
        #[arg(long)]
        out: Option<PathBuf>,
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

#[derive(Subcommand, Debug)]
pub enum ProfileCommand {
    /// Create a new profile shell on MissionControl (empty bundle).
    Create {
        #[arg(long)]
        name: String,
        #[arg(long, default_value = "")]
        description: String,
        #[arg(long)]
        activate: bool,
    },
    /// List current user's profiles.
    List {
        #[arg(long, default_value_t = 50)]
        limit: u32,
    },
    /// Show profile metadata by name.
    Show {
        #[arg(long)]
        name: String,
    },
    /// Activate profile as default.
    Activate {
        #[arg(long)]
        name: String,
    },
    /// Download profile bundle to a local file.
    Download {
        #[arg(long)]
        name: String,
        #[arg(long)]
        out: Option<PathBuf>,
    },
    /// Publish/replace a profile bundle in MissionControl.
    Publish {
        #[arg(long)]
        name: String,
        #[arg(long)]
        bundle: Option<PathBuf>,
        #[arg(long)]
        from_profile_dir: Option<PathBuf>,
        #[arg(long)]
        description: Option<String>,
        #[arg(long)]
        manifest_file: Option<PathBuf>,
        #[arg(long)]
        activate: bool,
    },
    /// Pull profile bundle from MissionControl into local profile cache.
    Pull {
        #[arg(long)]
        name: String,
        #[arg(long)]
        apply: bool,
        #[arg(long)]
        allow_pin_mismatch: bool,
    },
    /// Pin a local profile to a specific remote sha256.
    Pin {
        #[arg(long)]
        name: String,
        #[arg(long)]
        sha256: String,
    },
    /// Delete a profile from MissionControl (requires explicit confirmation flag).
    Delete {
        #[arg(long)]
        name: String,
        #[arg(long, default_value_t = false)]
        confirm_delete: bool,
    },
    /// Show remote/local pin status for a profile.
    Status {
        #[arg(long)]
        name: String,
    },
    /// Activate a profile as default and apply its bundle locally in one step.
    Use {
        #[arg(long)]
        name: String,
    },
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
    /// Governance policy summaries and event feeds.
    #[command(subcommand)]
    Policy(AdminPolicyCommand),
    /// Governance automation helpers (roles, policies, events).
    #[command(subcommand)]
    Governance(governance::GovernanceCommand),
}

#[derive(Subcommand, Debug)]
pub enum AdminPolicyCommand {
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
    output_mode: OutputMode,
) -> Result<()> {
    match command {
        McCommand::Status(args) => handle_status(args, client, &config, output_mode).await,
        McCommand::Doctor(args) => maintenance::run_doctor_command(&client, &config, &args).await,
        McCommand::Health(_args) => handle_health(client, output_mode).await,
        McCommand::Version(_args) => handle_version(client, &config, output_mode).await,
        McCommand::Config(_args) => handle_config(&config, output_mode),
        McCommand::Use(args) => handle_use(args, client, output_mode).await,
        McCommand::Release(args) => handle_release(args, client, output_mode).await,
        McCommand::Logs(args) => handle_logs(args, output_mode),
        McCommand::Completion(args) => handle_completion(args),
        McCommand::Auth(cmd) => handle_auth(cmd, client, &config).await,
        McCommand::Data(cmd) => handle_data(cmd, client, &booster, &config.schema_pack, output_mode).await,
        McCommand::Admin(cmd) => handle_admin(cmd, client).await,
        McCommand::System(cmd) => handle_system(cmd, client, &config).await,
        McCommand::Agent(cmd) => handle_agent(cmd, client, &booster, &config.schema_pack).await,
        McCommand::Workspace(cmd) => {
            handle_workspace(cmd, client, &booster, &config.schema_pack, output_mode).await
        }
        McCommand::Approvals(cmd) => handle_approvals(cmd, client, output_mode).await,
        McCommand::Ops(cmd) => ops::run(cmd, &client, &booster, &config.schema_pack).await,
        McCommand::Daemon(args) => daemon::run(&args, &client, ctx).await,
        McCommand::Launch(args) => launch::run(args, &client, &config).await,
        McCommand::Init(args) => handle_init(args, client, &config, output_mode).await,
        McCommand::Serve(args) => mcp_server::run(&args, &client).await,
        McCommand::Profile(cmd) => handle_profile(cmd, client, output_mode).await,
    }
}

#[derive(serde::Serialize, serde::Deserialize, Default)]
struct ActiveWorkspaceState {
    lease_id: Option<String>,
    mission_id: Option<String>,
    kluster_id: Option<String>,
    status: Option<String>,
}

fn active_workspace_path() -> PathBuf {
    crate::config::mc_home_dir().join("active_workspace.json")
}

fn load_active_workspace() -> ActiveWorkspaceState {
    let path = active_workspace_path();
    let raw = match fs::read_to_string(path) {
        Ok(v) => v,
        Err(_) => return ActiveWorkspaceState::default(),
    };
    serde_json::from_str(&raw).unwrap_or_default()
}

fn save_active_workspace(ctx: &ActiveWorkspaceState) -> Result<()> {
    let path = active_workspace_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, serde_json::to_string_pretty(ctx)?)?;
    Ok(())
}

fn clear_active_workspace() -> Result<()> {
    let path = active_workspace_path();
    if path.exists() {
        fs::remove_file(path)?;
    }
    Ok(())
}

fn response_lease_view(response: &Value) -> &Value {
    response.get("lease").unwrap_or(response)
}

fn extract_workspace_state(response: &Value) -> ActiveWorkspaceState {
    let lease = response_lease_view(response);
    ActiveWorkspaceState {
        lease_id: lease
            .get("id")
            .and_then(|v| v.as_str())
            .or_else(|| response.get("lease_id").and_then(|v| v.as_str()))
            .map(|s| s.to_string()),
        mission_id: lease
            .get("mission_id")
            .and_then(|v| v.as_str())
            .or_else(|| response.get("mission_id").and_then(|v| v.as_str()))
            .map(|s| s.to_string()),
        kluster_id: lease
            .get("kluster_id")
            .and_then(|v| v.as_str())
            .or_else(|| response.get("kluster_id").and_then(|v| v.as_str()))
            .map(|s| s.to_string()),
        status: lease
            .get("status")
            .and_then(|v| v.as_str())
            .or_else(|| response.get("status").and_then(|v| v.as_str()))
            .map(|s| s.to_string()),
    }
}

fn prompt_confirm(prompt: &str) -> Result<bool> {
    use std::io::{self, Write};
    eprint!("{prompt}");
    io::stderr().flush()?;
    let mut buf = String::new();
    io::stdin().read_line(&mut buf)?;
    let answer = buf.trim().to_ascii_lowercase();
    Ok(matches!(answer.as_str(), "y" | "yes"))
}

async fn handle_health(client: MissionControlClient, output_mode: OutputMode) -> Result<()> {
    let response = client.get_json("/mcp/health").await?;
    output::print_value(output_mode, &response);
    Ok(())
}

async fn handle_version(
    client: MissionControlClient,
    config: &McConfig,
    output_mode: OutputMode,
) -> Result<()> {
    let backend = client.get_json("/mcp/health").await.ok();
    let payload = json!({
        "mc_version": env!("CARGO_PKG_VERSION"),
        "base_url": config.base_url.as_str(),
        "backend_health": backend,
    });
    output::print_value(output_mode, &payload);
    Ok(())
}

fn handle_config(config: &McConfig, output_mode: OutputMode) -> Result<()> {
    let payload = json!({
        "base_url": config.base_url.as_str(),
        "timeout_secs": config.timeout.as_secs(),
        "allow_insecure": config.allow_insecure,
        "token": if config.token.is_some() { "***redacted***" } else { "" },
        "agent_context": {
            "agent_id": config.agent_context.agent_id,
            "runtime_session_id": config.agent_context.runtime_session_id,
            "profile_name": config.agent_context.profile_name,
        },
        "paths": {
            "mc_home": crate::config::mc_home_dir(),
            "skills_home": crate::config::skills_home_dir(),
            "agent_id_file": crate::config::agent_id_file(),
        }
    });
    output::print_value(output_mode, &payload);
    Ok(())
}

async fn handle_use(args: UseArgs, client: MissionControlClient, output_mode: OutputMode) -> Result<()> {
    if let Some(profile) = args.profile {
        return handle_profile(ProfileCommand::Use { name: profile }, client, output_mode).await;
    }
    if args.release {
        return handle_release(
            ReleaseArgs {
                reason: Some("released via mc use --release".to_string()),
                ignore_missing: false,
            },
            client,
            output_mode,
        )
        .await;
    }
    let kluster_id = args
        .kluster_id
        .ok_or_else(|| anyhow::anyhow!("`mc use` requires --kluster-id (or --profile)"))?;
    let current = load_active_workspace();
    if let (Some(existing_lease), Some(existing_kluster)) =
        (current.lease_id.clone(), current.kluster_id.clone())
    {
        if existing_kluster != kluster_id {
            let should_release = if args.auto_release || args.yes {
                true
            } else {
                prompt_confirm(&format!(
                    "Release existing lease {} for kluster {} and switch to {}? [y/N] ",
                    existing_lease, existing_kluster, kluster_id
                ))?
            };
            if !should_release {
                anyhow::bail!("switch cancelled; existing lease kept active");
            }
            let _ = mcp_tools::call_tool(
                &client,
                None,
                None,
                "release_kluster_workspace",
                json!({"lease_id": existing_lease, "reason": "switch kluster via mc use"}),
            )
            .await?;
            clear_active_workspace()?;
        }
    }
    let mut tool_args = json!({
        "kluster_id": kluster_id,
        "lease_seconds": args.lease_seconds,
    });
    if let Some(label) = args.workspace_label {
        tool_args["workspace_label"] = json!(label);
    }
    let response = mcp_tools::call_tool(&client, None, None, "load_kluster_workspace", tool_args).await?;
    let state = extract_workspace_state(&response);
    save_active_workspace(&state)?;
    output::print_value(output_mode, &json!({"ok": true, "workspace": state, "lease": response}));
    Ok(())
}

async fn handle_release(
    args: ReleaseArgs,
    client: MissionControlClient,
    output_mode: OutputMode,
) -> Result<()> {
    let state = load_active_workspace();
    let Some(lease_id) = state.lease_id.clone() else {
        if args.ignore_missing {
            output::print_value(output_mode, &json!({"ok": true, "released": false, "reason": "no_active_lease"}));
            return Ok(());
        }
        anyhow::bail!("no active lease is tracked; nothing to release");
    };
    let response = mcp_tools::call_tool(
        &client,
        None,
        None,
        "release_kluster_workspace",
        json!({
            "lease_id": lease_id,
            "reason": args.reason.unwrap_or_else(|| "released via mc release".to_string())
        }),
    )
    .await?;
    clear_active_workspace()?;
    output::print_value(output_mode, &json!({"ok": true, "released": true, "lease": response}));
    Ok(())
}

fn handle_logs(args: LogsArgs, output_mode: OutputMode) -> Result<()> {
    let candidates = [
        crate::config::mc_home_dir().join("daemon.log"),
        crate::config::mc_home_dir().join("logs/daemon.log"),
        crate::config::mc_home_dir().join("logs/mc.log"),
    ];
    let mut entries = Vec::new();
    for path in candidates {
        if !path.exists() {
            continue;
        }
        let raw = fs::read_to_string(&path).unwrap_or_default();
        let lines: Vec<String> = raw
            .lines()
            .rev()
            .take(args.lines)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .map(|s| s.to_string())
            .collect();
        entries.push(json!({"path": path, "lines": lines}));
    }
    let payload = json!({ "logs": entries });
    output::print_value(output_mode, &payload);
    Ok(())
}

fn handle_completion(args: CompletionArgs) -> Result<()> {
    #[derive(clap::Parser)]
    struct CompletionRoot {
        #[command(subcommand)]
        command: McCommand,
    }
    let mut cmd = CompletionRoot::command();
    clap_complete::generate(args.shell, &mut cmd, "mc", &mut std::io::stdout());
    Ok(())
}

async fn handle_status(
    args: StatusArgs,
    client: MissionControlClient,
    config: &McConfig,
    output_mode: OutputMode,
) -> Result<()> {
    let local_session = auth::load_saved_session(config.base_url.as_str());
    let effective_agent_id = config
        .agent_context
        .agent_id
        .clone()
        .or_else(|| crate::config::default_agent_id_from_session(config.base_url.as_str()));
    let remote = match client.get_json("/auth/whoami").await {
        Ok(value) => Some(value),
        Err(err) => Some(json!({ "reachable": false, "error": err.to_string() })),
    };
    let workspace = load_active_workspace();
    let lease_verification = if args.verify_lease {
        if let Some(lease_id) = workspace.lease_id.clone() {
            match mcp_tools::call_tool(
                &client,
                None,
                None,
                "heartbeat_workspace_lease",
                json!({"lease_id": lease_id}),
            )
            .await
            {
                Ok(v) => Some(json!({"ok": true, "result": v})),
                Err(err) => Some(json!({"ok": false, "error": err.to_string()})),
            }
        } else {
            Some(json!({"ok": false, "error": "no active lease"}))
        }
    } else {
        None
    };
    let payload = json!({
        "base_url": config.base_url.as_str(),
        "output": output_mode.as_str(),
        "auth": {
            "has_session": local_session.is_some(),
            "session_subject": local_session.as_ref().map(|s| s.subject.clone()),
            "session_email": local_session.as_ref().and_then(|s| s.email.clone()),
            "session_expires_at": local_session.as_ref().map(|s| s.expires_at.clone()),
            "has_token": config.token.is_some(),
            "remote_whoami": remote,
        },
        "runtime": {
            "profile": config.agent_context.profile_name.clone(),
            "runtime_session_id": config.agent_context.runtime_session_id.clone(),
            "agent_id": effective_agent_id,
        },
        "context": {
            "lease_id": workspace.lease_id,
            "mission_id": workspace.mission_id,
            "kluster_id": workspace.kluster_id,
            "status": workspace.status,
        },
        "lease_verification": lease_verification,
    });
    output::print_value(output_mode, &payload);
    Ok(())
}

async fn handle_auth(command: AuthCommand, client: MissionControlClient, config: &McConfig) -> Result<()> {
    match command {
        AuthCommand::Login(args) => auth::login(args, &client, config.base_url.as_str()).await,
        AuthCommand::Logout(args) => auth::logout(args, &client).await,
        AuthCommand::Whoami(_) => auth::whoami(&client).await,
    }
}

async fn handle_data(
    command: DataCommand,
    client: MissionControlClient,
    booster: &AgentBooster,
    schema_pack: &SchemaPack,
    output_mode: OutputMode,
) -> Result<()> {
    match command {
        DataCommand::Tools(cmd) => handle_tools(cmd, client, booster, schema_pack, output_mode).await,
        DataCommand::Sync(cmd) => handle_sync(cmd, client).await,
        DataCommand::Explorer(cmd) => handle_explorer(cmd, client).await,
    }
}

async fn handle_system(
    command: SystemCommand,
    client: MissionControlClient,
    config: &McConfig,
) -> Result<()> {
    match command {
        SystemCommand::Doctor(args) => maintenance::run_doctor_command(&client, config, &args).await,
        SystemCommand::Backup(args) => maintenance::run_backup_command(&client, args).await,
        SystemCommand::ProfileGc(args) => maintenance::run_profile_gc_command(config, args),
        SystemCommand::Update(cmd) => update::run(cmd, config).await,
        SystemCommand::Compat(cmd) => compat::run(cmd).await,
        SystemCommand::Drift(cmd) => drift::run(cmd).await,
    }
}

async fn handle_agent(
    command: AgentCommand,
    client: MissionControlClient,
    booster: &AgentBooster,
    schema_pack: &SchemaPack,
) -> Result<()> {
    let _ = (booster, schema_pack);
    match command {
        AgentCommand::Remote(cmd) => remote::run(cmd, &client).await,
        AgentCommand::Evolve(args) => evolve::run(args, &client).await,
    }
}

async fn handle_init(
    args: InitArgs,
    client: MissionControlClient,
    config: &McConfig,
    output_mode: OutputMode,
) -> Result<()> {
    let json_output = output_mode.is_machine();
    let profile_name = args.profile.trim();
    if profile_name.is_empty() {
        anyhow::bail!("--profile cannot be empty");
    }
    validate_init_base_url(config)?;

    let login_client_holder: Option<MissionControlClient> = if config.token.is_none() {
        if auth::load_saved_session(config.base_url.as_str()).is_none() {
            eprintln!(
                "mc: no valid session found for {}",
                config.base_url.as_str()
            );
            eprintln!("mc: running `mc auth login` to authenticate...");
            if let Err(err) = auth::login(
                auth::LoginArgs {
                    ttl_hours: 8,
                    print_token: false,
                    non_interactive: false,
                },
                &client,
                config.base_url.as_str(),
            )
            .await
            {
                return bootstrap_local_profile(
                    profile_name,
                    Some(format!(
                        "login failed; continuing in local-only mode: {err}"
                    )),
                    output_mode,
                );
            }
        }
        let session_token = auth::load_saved_session(config.base_url.as_str())
            .map(|s| s.token)
            .ok_or_else(|| anyhow::anyhow!("session not found after login"))?;
        Some(
            MissionControlClient::new_with_token(config.base_url.as_str(), &session_token)
                .context("failed to build client with session token")?,
        )
    } else {
        None
    };
    let effective_client: &MissionControlClient = login_client_holder.as_ref().unwrap_or(&client);

    let listed = match mcp_profile_call(effective_client, "list_profiles", json!({ "limit": 1 }))
        .await
    {
        Ok(v) => v,
        Err(err) => {
            return bootstrap_local_profile(
                profile_name,
                Some(format!(
                    "unable to reach Mission Control profile service; continuing in local-only mode: {err}"
                )),
                output_mode,
            );
        }
    };
    let has_profiles = listed
        .get("profiles")
        .and_then(|v| v.as_array())
        .map(|arr| !arr.is_empty())
        .unwrap_or(false);

    if has_profiles {
        let activated = mcp_profile_call(
            effective_client,
            "activate_profile",
            json!({ "name": profile_name }),
        )
        .await;
        if activated.is_err() {
            let payload = json!({
                "ok": true,
                "created": false,
                "note": "profiles already exist; no bootstrap profile created",
            });
            if json_output {
                print_json(&payload);
            } else {
                println!(
                    "{}{}{} profiles already exist; no bootstrap profile created",
                    crate::ui::YELLOW,
                    "⚑ ",
                    crate::ui::RESET
                );
            }
            return Ok(());
        }
        write_synced_profile_state(profile_name)?;
        let payload = json!({
            "ok": true,
            "created": false,
            "activated_profile": profile_name,
        });
        if json_output {
            print_json(&payload);
        } else {
            println!(
                "{}{}{} profile active: {}{}{}",
                crate::ui::GREEN,
                "✓ ",
                crate::ui::RESET,
                crate::ui::CYAN,
                profile_name,
                crate::ui::RESET
            );
        }
        return Ok(());
    }

    let tarball_b64 = empty_profile_tarball_b64()?;
    let published = match mcp_profile_call(
        effective_client,
        "publish_profile",
        json!({
            "name": profile_name,
            "description": "Bootstrap profile created by mc init",
            "is_default": true,
            "manifest": [],
            "tarball_b64": tarball_b64
        }),
    )
    .await
    {
        Ok(v) => v,
        Err(err) => {
            return bootstrap_local_profile(
                profile_name,
                Some(format!(
                    "failed to publish profile to Mission Control; continuing in local-only mode: {err}"
                )),
                output_mode,
            );
        }
    };
    write_synced_profile_state(profile_name)?;
    let payload = json!({
        "ok": true,
        "created": true,
        "synced": true,
        "profile": published.get("profile").cloned().unwrap_or(Value::Null),
    });
    if json_output {
        print_json(&payload);
    } else {
        println!(
            "{}{}{} profile created + synced: {}{}{}",
            crate::ui::GREEN,
            "✓ ",
            crate::ui::RESET,
            crate::ui::CYAN,
            profile_name,
            crate::ui::RESET
        );
    }
    Ok(())
}

async fn handle_tools(
    command: ToolsCommand,
    client: MissionControlClient,
    booster: &AgentBooster,
    schema_pack: &SchemaPack,
    output_mode: OutputMode,
) -> Result<()> {
    match command {
        ToolsCommand::List => {
            let response = client.get_json("/mcp/tools").await?;
            if output_mode.is_machine() {
                output::print_value(output_mode, &response);
            } else {
                print_tools_human(&response);
            }
        }
        ToolsCommand::Call(args) => {
            let payload = serde_json::from_str::<Value>(&args.payload)
                .context("failed to parse payload JSON")?;
            let response = mcp_tools::call_tool(
                &client,
                Some(booster),
                Some(schema_pack),
                &args.tool,
                payload,
            )
            .await?;
            print_json(&response);
        }
    }
    Ok(())
}

fn print_json(value: &Value) {
    output::print_value(OutputMode::Json, value);
}

fn ui_section(title: &str) {
    println!();
    println!(
        "{}{}{}{}",
        crate::ui::BOLD,
        crate::ui::ORANGE,
        title,
        crate::ui::RESET
    );
    println!("{}{}{}", crate::ui::GRAY, "─".repeat(56), crate::ui::RESET);
}

fn ui_row(label: &str, value: &str, value_color: &str) {
    println!(
        "  {}{: <16}{} {}{}{}",
        crate::ui::DIM,
        format!("{}:", label),
        crate::ui::RESET,
        value_color,
        value,
        crate::ui::RESET
    );
}

fn print_tools_human(value: &Value) {
    let items: Vec<Value> = match value {
        Value::Array(arr) => arr.clone(),
        Value::Object(obj) => obj
            .get("tools")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default(),
        _ => Vec::new(),
    };
    ui_section("MCP Tools");
    ui_row("Count", &items.len().to_string(), crate::ui::CYAN);
    if items.is_empty() {
        println!(
            "  {}no tools returned{}",
            crate::ui::YELLOW,
            crate::ui::RESET
        );
        return;
    }
    for tool in items {
        let name = tool
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown");
        let description = tool
            .get("description")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        println!("  {}•{} {}", crate::ui::CYAN, crate::ui::RESET, name);
        if !description.is_empty() {
            println!("    {}{}{}", crate::ui::DIM, description, crate::ui::RESET);
        }
    }
}

fn print_profiles_human(value: &Value) {
    let Some(items) = value.as_array() else {
        print_json(value);
        return;
    };
    if items.is_empty() {
        println!("no profiles");
        return;
    }
    for p in items {
        let name = p.get("name").and_then(|v| v.as_str()).unwrap_or("unknown");
        let sha = p.get("sha256").and_then(|v| v.as_str()).unwrap_or("-");
        let is_default = p
            .get("is_default")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        if is_default {
            println!(
                "{}*{} {}  {}",
                crate::ui::GREEN,
                crate::ui::RESET,
                name,
                sha
            );
        } else {
            println!("  {}  {}", name, sha);
        }
    }
}

fn print_profile_human(profile: &Value) {
    let name = profile
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown");
    let sha = profile
        .get("sha256")
        .and_then(|v| v.as_str())
        .unwrap_or("-");
    let is_default = profile
        .get("is_default")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let default_marker = if is_default { " (default)" } else { "" };
    println!(
        "{}{}{}{}  {}",
        crate::ui::CYAN,
        name,
        crate::ui::RESET,
        default_marker,
        sha
    );
}

fn print_profile_status_human(value: &Value) {
    let name = value
        .get("profile")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown");
    let remote_sha = value
        .get("remote_sha256")
        .and_then(|v| v.as_str())
        .unwrap_or("-");
    let local_pin = value
        .get("local_pin")
        .and_then(|v| v.get("pinned_sha256"))
        .and_then(|v| v.as_str())
        .unwrap_or("(none)");
    let synced = value
        .get("local_state")
        .and_then(|v| v.get("synced"))
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    println!("profile: {}{}{}", crate::ui::CYAN, name, crate::ui::RESET);
    println!("remote_sha256: {}", remote_sha);
    println!("local_pin: {}", local_pin);
    println!("synced: {}", synced);
}

fn build_path_with_query(base: &str, query: String) -> String {
    if query.is_empty() {
        base.to_string()
    } else {
        format!("{}?{}", base, query)
    }
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

async fn handle_profile(
    command: ProfileCommand,
    client: MissionControlClient,
    output_mode: OutputMode,
) -> Result<()> {
    let json_output = output_mode.is_machine();
    match command {
        ProfileCommand::Create {
            name,
            description,
            activate,
        } => {
            let tarball_b64 = empty_profile_tarball_b64()?;
            let response = mcp_profile_call(
                &client,
                "publish_profile",
                json!({
                    "name": name,
                    "description": description,
                    "is_default": activate,
                    "manifest": [],
                    "tarball_b64": tarball_b64
                }),
            )
            .await?;
            let profile = response.get("profile").cloned().unwrap_or(Value::Null);
            if json_output {
                print_json(&profile);
            } else {
                let shown = profile
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("unknown");
                println!(
                    "{}{}{} created profile: {}{}{}",
                    crate::ui::GREEN,
                    "✓ ",
                    crate::ui::RESET,
                    crate::ui::CYAN,
                    shown,
                    crate::ui::RESET
                );
            }
        }
        ProfileCommand::List { limit } => {
            let response =
                mcp_profile_call(&client, "list_profiles", json!({ "limit": limit })).await?;
            let profiles = response
                .get("profiles")
                .cloned()
                .unwrap_or_else(|| json!([]));
            if json_output {
                print_json(&profiles);
            } else {
                print_profiles_human(&profiles);
            }
        }
        ProfileCommand::Show { name } => {
            let response =
                mcp_profile_call(&client, "get_profile", json!({ "name": name })).await?;
            let profile = response.get("profile").cloned().unwrap_or(Value::Null);
            if json_output {
                print_json(&profile);
            } else {
                print_profile_human(&profile);
            }
        }
        ProfileCommand::Activate { name } => {
            let response =
                mcp_profile_call(&client, "activate_profile", json!({ "name": name })).await?;
            let profile = response.get("profile").cloned().unwrap_or(Value::Null);
            if json_output {
                print_json(&profile);
            } else {
                let shown = profile
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or(&name);
                println!(
                    "{}{}{} default profile: {}{}{}",
                    crate::ui::GREEN,
                    "✓ ",
                    crate::ui::RESET,
                    crate::ui::CYAN,
                    shown,
                    crate::ui::RESET
                );
            }
        }
        ProfileCommand::Download { name, out } => {
            let response =
                mcp_profile_call(&client, "download_profile", json!({ "name": name })).await?;
            let tarball = response
                .get("tarball_b64")
                .and_then(|v| v.as_str())
                .context("profile download response missing tarball_b64")?;
            let bytes = base64::engine::general_purpose::STANDARD
                .decode(tarball)
                .context("tarball_b64 is not valid base64")?;
            let out_path = out.unwrap_or_else(|| PathBuf::from(format!("{}.profile.tar", name)));
            std::fs::write(&out_path, bytes)
                .with_context(|| format!("failed to write {}", out_path.display()))?;
            let payload =
                json!({"ok": true, "profile": name, "out": out_path.display().to_string()});
            if json_output {
                print_json(&payload);
            } else {
                println!(
                    "{}{}{} downloaded profile {}{}{} -> {}",
                    crate::ui::GREEN,
                    "✓ ",
                    crate::ui::RESET,
                    crate::ui::CYAN,
                    name,
                    crate::ui::RESET,
                    out_path.display()
                );
            }
        }
        ProfileCommand::Publish {
            name,
            bundle,
            from_profile_dir,
            description,
            manifest_file,
            activate,
        } => {
            let bundle_bytes = if let Some(bundle_path) = bundle {
                fs::read(&bundle_path)
                    .with_context(|| format!("failed to read bundle {}", bundle_path.display()))?
            } else if let Some(profile_dir) = from_profile_dir {
                build_tar_from_dir(&profile_dir)?
            } else {
                anyhow::bail!("provide --bundle or --from-profile-dir");
            };
            let tarball_b64 = base64::engine::general_purpose::STANDARD.encode(bundle_bytes);
            let manifest = if let Some(path) = manifest_file {
                let raw = fs::read_to_string(&path)
                    .with_context(|| format!("failed to read manifest {}", path.display()))?;
                serde_json::from_str::<Value>(&raw).context("manifest file must be valid JSON")?
            } else {
                json!([])
            };
            let manifest = if manifest.is_array() {
                manifest
            } else {
                json!([manifest])
            };
            let response = mcp_profile_call(
                &client,
                "publish_profile",
                json!({
                "name": name,
                "description": description.unwrap_or_default(),
                "is_default": activate,
                "manifest": manifest,
                "tarball_b64": tarball_b64
                }),
            )
            .await?;
            let profile = response.get("profile").cloned().unwrap_or(Value::Null);
            if json_output {
                print_json(&profile);
            } else {
                let shown = profile
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or(&name);
                println!(
                    "{}{}{} published profile: {}{}{}",
                    crate::ui::GREEN,
                    "✓ ",
                    crate::ui::RESET,
                    crate::ui::CYAN,
                    shown,
                    crate::ui::RESET
                );
            }
        }
        ProfileCommand::Pull {
            name,
            apply,
            allow_pin_mismatch,
        } => {
            let profile_root = crate::config::mc_home_dir().join("profiles").join(&name);
            let mut pull_args = json!({ "name": name });
            if let Some(pinned_sha) = read_local_pinned_sha(&profile_root)? {
                if !allow_pin_mismatch {
                    pull_args["if_sha256"] = json!(pinned_sha);
                }
            }
            let response = mcp_profile_call(&client, "download_profile", pull_args).await?;
            let tarball = response
                .get("tarball_b64")
                .and_then(|v| v.as_str())
                .context("profile download response missing tarball_b64")?;
            let bytes = base64::engine::general_purpose::STANDARD
                .decode(tarball)
                .context("tarball_b64 is not valid base64")?;
            let sha = response
                .get("profile")
                .and_then(|v| v.get("sha256"))
                .and_then(|v| v.as_str())
                .unwrap_or("unknown");
            if let Some(pinned_sha) = read_local_pinned_sha(&profile_root)? {
                if pinned_sha != sha && !allow_pin_mismatch {
                    anyhow::bail!(
                        "profile '{}' is pinned to sha256 '{}' but remote is '{}'; rerun with --allow-pin-mismatch to override",
                        name,
                        pinned_sha,
                        sha
                    );
                }
            }
            let bundles = profile_root.join("bundles");
            fs::create_dir_all(&bundles)?;
            let tar_path = bundles.join(format!("{}.tar", sha));
            fs::write(&tar_path, bytes)?;
            if apply {
                extract_tar_to_dir(&tar_path, &profile_root)?;
            }
            let state = json!({
                "profile": name,
                "last_pulled_sha256": sha,
                "bundle_path": tar_path.display().to_string(),
                "applied": apply,
                "pulled_at": chrono::Utc::now().to_rfc3339(),
            });
            fs::write(
                profile_root.join("state.json"),
                serde_json::to_string_pretty(&state)?,
            )?;
            let payload = json!({
                "ok": true,
                "profile": name,
                "last_pulled_sha256": sha,
                "bundle_path": tar_path.display().to_string(),
                "applied": apply,
            });
            if json_output {
                print_json(&payload);
            } else {
                println!(
                    "{}{}{} pulled profile {}{}{} @ {}",
                    crate::ui::GREEN,
                    "✓ ",
                    crate::ui::RESET,
                    crate::ui::CYAN,
                    name,
                    crate::ui::RESET,
                    sha
                );
            }
        }
        ProfileCommand::Pin { name, sha256 } => {
            let profile_root = crate::config::mc_home_dir().join("profiles").join(&name);
            fs::create_dir_all(&profile_root)?;
            let pin = json!({
                "profile": name,
                "pinned_sha256": sha256,
                "pinned_at": chrono::Utc::now().to_rfc3339(),
            });
            fs::write(
                profile_root.join("pin.json"),
                serde_json::to_string_pretty(&pin)?,
            )?;
            let payload = json!({"ok": true, "profile": name, "pinned_sha256": sha256});
            if json_output {
                print_json(&payload);
            } else {
                println!(
                    "{}{}{} pinned {}{}{} -> {}",
                    crate::ui::GREEN,
                    "✓ ",
                    crate::ui::RESET,
                    crate::ui::CYAN,
                    name,
                    crate::ui::RESET,
                    sha256
                );
            }
        }
        ProfileCommand::Delete {
            name,
            confirm_delete,
        } => {
            if !confirm_delete {
                anyhow::bail!(
                    "refusing to delete profile '{}'; rerun with --confirm-delete",
                    name
                );
            }
            let response =
                mcp_profile_call(&client, "delete_profile", json!({ "name": name })).await?;
            if json_output {
                print_json(&response);
            } else {
                println!(
                    "{}{}{} deleted profile: {}{}{}",
                    crate::ui::GREEN,
                    "✓ ",
                    crate::ui::RESET,
                    crate::ui::CYAN,
                    name,
                    crate::ui::RESET
                );
            }
        }
        ProfileCommand::Status { name } => {
            let remote = mcp_profile_call(&client, "get_profile", json!({ "name": name })).await?;
            let remote_sha = remote
                .get("profile")
                .and_then(|v| v.get("sha256"))
                .cloned()
                .unwrap_or(Value::Null);
            let profile_root = crate::config::mc_home_dir().join("profiles").join(&name);
            let local_pin_path = profile_root.join("pin.json");
            let local_state_path = profile_root.join("state.json");
            let local_pin = if local_pin_path.exists() {
                serde_json::from_str::<Value>(&fs::read_to_string(&local_pin_path)?)
                    .unwrap_or(Value::Null)
            } else {
                Value::Null
            };
            let local_state = if local_state_path.exists() {
                serde_json::from_str::<Value>(&fs::read_to_string(&local_state_path)?)
                    .unwrap_or(Value::Null)
            } else {
                Value::Null
            };
            let pin_check = if let Some(pinned) = local_pin
                .get("pinned_sha256")
                .and_then(|v| v.as_str())
                .map(str::to_string)
            {
                mcp_profile_call(
                    &client,
                    "pin_profile_version",
                    json!({ "name": name, "sha256": pinned }),
                )
                .await
                .unwrap_or_else(|_| json!({"matches": false}))
            } else {
                Value::Null
            };
            let payload = json!({
                "profile": name,
                "remote_sha256": remote_sha,
                "local_pin": local_pin,
                "local_state": local_state,
                "pin_check": pin_check
            });
            if json_output {
                print_json(&payload);
            } else {
                print_profile_status_human(&payload);
            }
        }
        ProfileCommand::Use { name } => {
            // 1. Mark as default on the backend.
            mcp_profile_call(&client, "activate_profile", json!({ "name": name })).await?;

            // 2. Download bundle.
            let response =
                mcp_profile_call(&client, "download_profile", json!({ "name": name })).await?;
            let tarball = response
                .get("tarball_b64")
                .and_then(|v| v.as_str())
                .context("profile download response missing tarball_b64")?;
            let bytes = base64::engine::general_purpose::STANDARD
                .decode(tarball)
                .context("tarball_b64 is not valid base64")?;
            let sha = response
                .get("profile")
                .and_then(|v| v.get("sha256"))
                .and_then(|v| v.as_str())
                .unwrap_or("unknown");

            // 3. Store bundle tarball and extract into the live profile directory.
            let profile_root = crate::config::mc_home_dir().join("profiles").join(&name);
            let bundles = profile_root.join("bundles");
            fs::create_dir_all(&bundles)?;
            let tar_path = bundles.join(format!("{}.tar", sha));
            fs::write(&tar_path, &bytes)?;
            extract_tar_to_dir(&tar_path, &profile_root)?;

            // 4. Write state.
            write_synced_profile_state(&name)?;

            // 5. Notify any live sessions using this profile.
            let marker_json = serde_json::to_string(&json!({
                "additionalContext": format!(
                    "⚠ Profile '{}' was updated (sha256: {}). \
                     File-based config (CLAUDE.md, agents) is live now via symlinks. \
                     MCP server changes require a restart — type /exit and relaunch with `mc launch`.",
                    name, sha
                )
            }))?;
            let active_sessions = crate::launch::sessions_for_profile(&name);
            let mut notified = 0usize;
            for session in &active_sessions {
                let mc_dir = PathBuf::from(&session.instance_home).join("mc");
                if mc_dir.exists() {
                    if fs::write(mc_dir.join("profile-updated"), &marker_json).is_ok() {
                        notified += 1;
                    }
                }
            }

            let payload = json!({
                "ok": true,
                "profile": name,
                "sha256": sha,
                "sessions_notified": notified,
            });
            if json_output {
                print_json(&payload);
            } else {
                println!(
                    "{}{}{} switched to profile: {}{}{}",
                    crate::ui::GREEN,
                    "✓ ",
                    crate::ui::RESET,
                    crate::ui::CYAN,
                    name,
                    crate::ui::RESET
                );
                if notified > 0 {
                    println!(
                        "{}  {} session(s) will be prompted to restart on next message{}",
                        crate::ui::YELLOW,
                        notified,
                        crate::ui::RESET
                    );
                }
            }
        }
    }
    Ok(())
}

async fn mcp_profile_call(client: &MissionControlClient, tool: &str, args: Value) -> Result<Value> {
    let response = client
        .post_json("/mcp/call", &json!({ "tool": tool, "args": args }))
        .await?;
    let ok = response
        .get("ok")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    if !ok {
        let err = response
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("mcp profile tool failed");
        anyhow::bail!("{}", err);
    }
    Ok(response.get("result").cloned().unwrap_or_else(|| json!({})))
}

fn build_tar_from_dir(dir: &PathBuf) -> Result<Vec<u8>> {
    if !dir.exists() || !dir.is_dir() {
        anyhow::bail!("profile dir not found: {}", dir.display());
    }
    let mut out = Vec::<u8>::new();
    {
        let mut builder = Builder::new(&mut out);
        builder.append_dir_all(".", dir)?;
        builder.finish()?;
    }
    Ok(out)
}

fn extract_tar_to_dir(tar_path: &PathBuf, out_dir: &PathBuf) -> Result<()> {
    let bytes = fs::read(tar_path)?;
    let cursor = Cursor::new(bytes);
    let mut archive = Archive::new(cursor);
    archive.unpack(out_dir)?;
    Ok(())
}

fn read_local_pinned_sha(profile_root: &std::path::Path) -> Result<Option<String>> {
    let pin_path = profile_root.join("pin.json");
    if !pin_path.exists() {
        return Ok(None);
    }
    let value: Value = serde_json::from_str(&fs::read_to_string(&pin_path)?)
        .context("pin.json is not valid JSON")?;
    Ok(value
        .get("pinned_sha256")
        .and_then(|v| v.as_str())
        .map(|v| v.to_string()))
}

fn empty_profile_tarball_b64() -> Result<String> {
    let mut bytes = Vec::<u8>::new();
    {
        let mut builder = Builder::new(&mut bytes);
        builder.finish()?;
    }
    Ok(base64::engine::general_purpose::STANDARD.encode(bytes))
}

fn validate_init_base_url(config: &McConfig) -> Result<()> {
    let base = config.base_url.as_str();
    let parsed = url::Url::parse(base).with_context(|| format!("invalid MC base URL: {base}"))?;
    let scheme = parsed.scheme();
    if scheme != "http" && scheme != "https" {
        anyhow::bail!("MC base URL must use http/https, got '{}'", scheme);
    }
    if parsed.host_str().unwrap_or("").trim().is_empty() {
        anyhow::bail!("MC base URL missing host: {}", base);
    }
    if std::env::var("MC_BASE_URL").is_err() && base == "http://localhost:8008" {
        eprintln!(
            "mc: warning: MC_BASE_URL is not set; using default {}",
            base
        );
    }
    Ok(())
}

fn write_synced_profile_state(profile_name: &str) -> Result<()> {
    let profile_root = crate::config::mc_home_dir()
        .join("profiles")
        .join(profile_name);
    fs::create_dir_all(&profile_root)?;
    let payload = json!({
        "profile": profile_name,
        "mode": "remote",
        "synced": true,
        "updated_at": chrono::Utc::now().to_rfc3339(),
    });
    fs::write(
        profile_root.join("state.json"),
        serde_json::to_string_pretty(&payload)?,
    )?;
    Ok(())
}

fn bootstrap_local_profile(
    profile_name: &str,
    warning: Option<String>,
    output_mode: OutputMode,
) -> Result<()> {
    let json_output = output_mode.is_machine();
    let profile_root = crate::config::mc_home_dir()
        .join("profiles")
        .join(profile_name);
    fs::create_dir_all(&profile_root)?;
    let payload = json!({
        "profile": profile_name,
        "mode": "local_only",
        "synced": false,
        "warning": warning.clone().unwrap_or_else(|| "local-only bootstrap".to_string()),
        "created_at": chrono::Utc::now().to_rfc3339(),
    });
    fs::write(
        profile_root.join("state.json"),
        serde_json::to_string_pretty(&payload)?,
    )?;
    if let Some(w) = warning {
        eprintln!("mc: warning: {}", w);
        eprintln!(
            "mc: warning: this profile is local-only and will not sync until Mission Control connectivity/auth is fixed"
        );
    }
    let payload = json!({
        "ok": true,
        "created": true,
        "synced": false,
        "profile": profile_name,
        "mode": "local_only"
    });
    if json_output {
        print_json(&payload);
    } else {
        println!(
            "{}{}{} local-only profile initialized: {}{}{}",
            crate::ui::YELLOW,
            "⚑ ",
            crate::ui::RESET,
            crate::ui::CYAN,
            profile_name,
            crate::ui::RESET
        );
    }
    Ok(())
}

async fn handle_workspace(
    command: WorkspaceCommand,
    client: MissionControlClient,
    booster: &AgentBooster,
    schema_pack: &SchemaPack,
    output_mode: OutputMode,
) -> Result<()> {
    let json_output = output_mode.is_machine();
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
            let response = call_mcp_tool(
                &client,
                booster,
                schema_pack,
                "load_kluster_workspace",
                args,
            )
            .await?;
            if json_output {
                print_json(&response);
            } else {
                print_workspace_human("Workspace Lease", &response);
            }
        }
        WorkspaceCommand::Heartbeat { lease_id } => {
            let args = json!({"lease_id": lease_id});
            let response = call_mcp_tool(
                &client,
                booster,
                schema_pack,
                "heartbeat_workspace_lease",
                args,
            )
            .await?;
            if json_output {
                print_json(&response);
            } else {
                print_workspace_human("Lease Heartbeat", &response);
            }
        }
        WorkspaceCommand::FetchArtifact {
            lease_id,
            artifact_id,
            mode,
            expires_seconds,
            out,
        } => {
            let args = json!({
                "lease_id": lease_id,
                "artifact_id": artifact_id,
                "mode": mode,
                "expires_seconds": expires_seconds,
            });
            let mut response = call_mcp_tool(
                &client,
                booster,
                schema_pack,
                "fetch_workspace_artifact",
                args,
            )
            .await?;
            if let Some(out_path) = out {
                if mode.trim().eq_ignore_ascii_case("content") {
                    let content_b64 = response
                        .get("content_b64")
                        .and_then(|v| v.as_str())
                        .context("artifact content response missing content_b64")?;
                    let bytes = base64::engine::general_purpose::STANDARD
                        .decode(content_b64)
                        .context("artifact content_b64 is not valid base64")?;
                    if let Some(parent) = out_path.parent() {
                        fs::create_dir_all(parent).with_context(|| {
                            format!("failed to create parent directory {}", parent.display())
                        })?;
                    }
                    fs::write(&out_path, &bytes)
                        .with_context(|| format!("failed to write {}", out_path.display()))?;
                    response["written_path"] = json!(out_path.display().to_string());
                    response["written_size_bytes"] = json!(bytes.len());
                } else {
                    anyhow::bail!("--out requires --mode content");
                }
            }
            if json_output {
                print_json(&response);
            } else {
                print_workspace_human("Artifact Fetch", &response);
            }
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
            let response = call_mcp_tool(
                &client,
                booster,
                schema_pack,
                "commit_kluster_workspace",
                args,
            )
            .await?;
            if json_output {
                print_json(&response);
            } else {
                print_workspace_human("Workspace Commit", &response);
            }
        }
        WorkspaceCommand::Release { lease_id, reason } => {
            let mut args = json!({"lease_id": lease_id});
            if let Some(reason_value) = reason {
                args["reason"] = json!(reason_value);
            }
            let response = call_mcp_tool(
                &client,
                booster,
                schema_pack,
                "release_kluster_workspace",
                args,
            )
            .await?;
            if json_output {
                print_json(&response);
            } else {
                print_workspace_human("Lease Release", &response);
            }
        }
    }
    Ok(())
}

fn print_workspace_human(title: &str, value: &Value) {
    ui_section(title);
    if let Some(lease) = value.get("lease_id").and_then(|v| v.as_str()) {
        ui_row("Lease", lease, crate::ui::CYAN);
    }
    if let Some(status) = value.get("status").and_then(|v| v.as_str()) {
        let color = match status {
            "active" | "ok" | "success" => crate::ui::GREEN,
            "released" => crate::ui::YELLOW,
            _ => crate::ui::CYAN,
        };
        ui_row("Status", status, color);
    }
    if let Some(url) = value.get("download_url").and_then(|v| v.as_str()) {
        ui_row("Download URL", url, crate::ui::CYAN);
    }
    println!(
        "  {}Result:{} {}",
        crate::ui::DIM,
        crate::ui::RESET,
        serde_json::to_string_pretty(value).unwrap_or_else(|_| value.to_string())
    );
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
        AdminCommand::Policy(AdminPolicyCommand::Active) => {
            let response = client.get_json("/governance/policy/active").await?;
            print_json(&response);
        }
        AdminCommand::Policy(AdminPolicyCommand::Versions { limit }) => {
            let mut serializer = form_urlencoded::Serializer::new(String::new());
            if let Some(limit) = limit {
                serializer.append_pair("limit", &limit.to_string());
            }
            let path = build_path_with_query("/governance/policy/versions", serializer.finish());
            let response = client.get_json(&path).await?;
            print_json(&response);
        }
        AdminCommand::Policy(AdminPolicyCommand::Events { limit }) => {
            let mut serializer = form_urlencoded::Serializer::new(String::new());
            if let Some(limit) = limit {
                serializer.append_pair("limit", &limit.to_string());
            }
            let path = build_path_with_query("/governance/policy/events", serializer.finish());
            let response = client.get_json(&path).await?;
            print_json(&response);
        }
        AdminCommand::Governance(cmd) => governance::run(cmd, &client).await?,
    }
    Ok(())
}

async fn handle_approvals(
    command: ApprovalCommand,
    client: MissionControlClient,
    output_mode: OutputMode,
) -> Result<()> {
    let json_output = output_mode.is_machine();
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
            if json_output {
                print_json(&response);
            } else {
                print_approval_single("Approval Request Created", &response);
            }
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
            if json_output {
                print_json(&response);
            } else {
                print_approvals_list_human(&response);
            }
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
            if json_output {
                print_json(&response);
            } else {
                print_approval_single("Approval Granted", &response);
            }
        }
        ApprovalCommand::Reject { approval_id, note } => {
            let mut body = json!({});
            if let Some(note) = note {
                body["note"] = json!(note);
            }
            let path = format!("/approvals/{}/reject", approval_id);
            let response = client.post_json(&path, &body).await?;
            if json_output {
                print_json(&response);
            } else {
                print_approval_single("Approval Rejected", &response);
            }
        }
    }
    Ok(())
}

fn print_approvals_list_human(value: &Value) {
    let items: Vec<Value> = match value {
        Value::Array(arr) => arr.clone(),
        Value::Object(obj) => obj
            .get("items")
            .and_then(|v| v.as_array())
            .cloned()
            .or_else(|| obj.get("approvals").and_then(|v| v.as_array()).cloned())
            .unwrap_or_default(),
        _ => Vec::new(),
    };
    ui_section("Approvals");
    ui_row("Count", &items.len().to_string(), crate::ui::CYAN);
    for item in items {
        let id = item.get("id").and_then(|v| v.as_i64()).unwrap_or_default();
        let mission_id = item
            .get("mission_id")
            .and_then(|v| v.as_str())
            .unwrap_or("-");
        let action = item.get("action").and_then(|v| v.as_str()).unwrap_or("-");
        let status = item.get("status").and_then(|v| v.as_str()).unwrap_or("-");
        let status_color = match status {
            "approved" => crate::ui::GREEN,
            "rejected" => crate::ui::RED,
            "pending" => crate::ui::YELLOW,
            _ => crate::ui::CYAN,
        };
        println!(
            "  {}#{}{} {}{}{} {}{}{} {}{}{}",
            crate::ui::DIM,
            id,
            crate::ui::RESET,
            crate::ui::CYAN,
            mission_id,
            crate::ui::RESET,
            crate::ui::BOLD,
            action,
            crate::ui::RESET,
            status_color,
            status,
            crate::ui::RESET
        );
    }
}

fn print_approval_single(title: &str, value: &Value) {
    ui_section(title);
    if let Some(id) = value.get("id").and_then(|v| v.as_i64()) {
        ui_row("ID", &id.to_string(), crate::ui::CYAN);
    }
    if let Some(status) = value.get("status").and_then(|v| v.as_str()) {
        let color = match status {
            "approved" => crate::ui::GREEN,
            "rejected" => crate::ui::RED,
            "pending" => crate::ui::YELLOW,
            _ => crate::ui::CYAN,
        };
        ui_row("Status", status, color);
    }
    if let Some(action) = value.get("action").and_then(|v| v.as_str()) {
        ui_row("Action", action, crate::ui::BOLD);
    }
    if let Some(mission) = value.get("mission_id").and_then(|v| v.as_str()) {
        ui_row("Mission", mission, crate::ui::CYAN);
    }
}

async fn call_mcp_tool(
    client: &MissionControlClient,
    booster: &AgentBooster,
    schema_pack: &SchemaPack,
    tool: &str,
    args: Value,
) -> Result<Value> {
    mcp_tools::call_tool(client, Some(booster), Some(schema_pack), tool, args).await
}
