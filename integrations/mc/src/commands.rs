use crate::{
    agent_context::AgentContext,
    auth,
    booster::AgentBooster,
    client::MissionControlClient,
    config::McConfig,
    daemon::{self, DaemonArgs},
    evolve, governance, launch, maintenance, mcp_server, mcp_tools, ops, remote,
    schema_pack::SchemaPack,
    update,
};
use anyhow::{Context, Result};
use base64::Engine;
use clap::{Args, Subcommand, ValueEnum};
use serde_json::{json, Value};
use std::fs;
use std::io::Cursor;
use std::path::PathBuf;
use tar::{Archive, Builder};
use url::form_urlencoded;

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
    /// AI-native mission operations such as mission lifecycle orchestration.
    #[command(subcommand)]
    Ops(ops::OpsCommand),
    /// Governance automation helpers (roles, policies, events).
    #[command(subcommand)]
    Governance(governance::GovernanceCommand),
    /// Maintenance utilities (doctor, backups).
    #[command(subcommand)]
    Maintenance(maintenance::MaintenanceCommand),
    /// Remote agent control verbs.
    #[command(subcommand)]
    Remote(remote::RemoteCommand),
    /// Self-update helper for the mc binary.
    #[command(subcommand)]
    Update(update::UpdateCommand),
    /// Run the async background daemon (matrix + MQTT).
    Daemon(DaemonArgs),
    /// Launch an agent with a fully wired MissionControl harness.
    Launch(launch::LaunchArgs),
    /// Self-improvement loop — run agents against MC's own backlog to evolve the codebase.
    Evolve(evolve::EvolveArgs),
    /// Authenticate and create a session token stored at ~/.missioncontrol/session.json.
    Login(auth::LoginArgs),
    /// Revoke the current session token and clear local credentials.
    Logout(auth::LogoutArgs),
    /// Show the current authenticated identity.
    Whoami(auth::WhoamiArgs),
    /// Start an MCP server (stdio JSON-RPC 2.0) for LLM runtime connections.
    Serve(mcp_server::ServeMcpArgs),
    /// Manage MissionControl user profiles.
    #[command(subcommand)]
    Profile(ProfileCommand),
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

#[derive(Subcommand, Debug)]
pub enum ProfileCommand {
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
    /// Show remote/local pin status for a profile.
    Status {
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
        McCommand::Tools(cmd) => handle_tools(cmd, client, &booster, &config.schema_pack).await,
        McCommand::Sync(cmd) => handle_sync(cmd, client).await,
        McCommand::Explorer(cmd) => handle_explorer(cmd, client).await,
        McCommand::Admin(cmd) => handle_admin(cmd, client).await,
        McCommand::Workspace(cmd) => {
            handle_workspace(cmd, client, &booster, &config.schema_pack).await
        }
        McCommand::Approvals(cmd) => handle_approvals(cmd, client).await,
        McCommand::Ops(cmd) => ops::run(cmd, &client, &booster, &config.schema_pack).await,
        McCommand::Governance(cmd) => governance::run(cmd, &client).await,
        McCommand::Maintenance(cmd) => maintenance::run(cmd, &client, &config).await,
        McCommand::Remote(cmd) => remote::run(cmd, &client).await,
        McCommand::Update(cmd) => update::run(cmd, &config).await,
        McCommand::Daemon(args) => daemon::run(&args, &client, ctx).await,
        McCommand::Launch(args) => launch::run(args, &client, &config).await,
        McCommand::Evolve(args) => evolve::run(args, &client).await,
        McCommand::Login(args) => auth::login(args, &client, config.base_url.as_str()).await,
        McCommand::Logout(args) => auth::logout(args, &client).await,
        McCommand::Whoami(_) => auth::whoami(&client).await,
        McCommand::Serve(args) => mcp_server::run(&args, &client).await,
        McCommand::Profile(cmd) => handle_profile(cmd, client).await,
    }
}

async fn handle_tools(
    command: ToolsCommand,
    client: MissionControlClient,
    booster: &AgentBooster,
    schema_pack: &SchemaPack,
) -> Result<()> {
    match command {
        ToolsCommand::List => {
            let response = client.get_json("/mcp/tools").await?;
            print_json(&response);
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
    println!(
        "{}",
        serde_json::to_string_pretty(value).unwrap_or_else(|_| value.to_string())
    );
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

async fn handle_profile(command: ProfileCommand, client: MissionControlClient) -> Result<()> {
    match command {
        ProfileCommand::List { limit } => {
            let response = mcp_profile_call(&client, "list_profiles", json!({ "limit": limit })).await?;
            print_json(&response.get("profiles").cloned().unwrap_or_else(|| json!([])));
        }
        ProfileCommand::Show { name } => {
            let response = mcp_profile_call(&client, "get_profile", json!({ "name": name })).await?;
            print_json(&response.get("profile").cloned().unwrap_or(Value::Null));
        }
        ProfileCommand::Activate { name } => {
            let response = mcp_profile_call(&client, "activate_profile", json!({ "name": name })).await?;
            print_json(&response.get("profile").cloned().unwrap_or(Value::Null));
        }
        ProfileCommand::Download { name, out } => {
            let response = mcp_profile_call(&client, "download_profile", json!({ "name": name })).await?;
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
            println!(
                "{}",
                serde_json::to_string_pretty(&json!({
                    "ok": true,
                    "profile": name,
                    "out": out_path.display().to_string()
                }))
                .unwrap_or_else(|_| "{\"ok\":true}".to_string())
            );
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
            let manifest = if manifest.is_array() { manifest } else { json!([manifest]) };
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
            print_json(&response.get("profile").cloned().unwrap_or(Value::Null));
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
            let applied_dir = profile_root.join("applied");
            if apply {
                if applied_dir.exists() {
                    fs::remove_dir_all(&applied_dir)?;
                }
                fs::create_dir_all(&applied_dir)?;
                extract_tar_to_dir(&tar_path, &applied_dir)?;
            }
            let state = json!({
                "profile": name,
                "last_pulled_sha256": sha,
                "bundle_path": tar_path.display().to_string(),
                "applied_dir": if apply { applied_dir.display().to_string() } else { String::new() },
                "pulled_at": chrono::Utc::now().to_rfc3339(),
            });
            fs::write(
                profile_root.join("state.json"),
                serde_json::to_string_pretty(&state)?,
            )?;
            print_json(&json!({
                "ok": true,
                "profile": name,
                "last_pulled_sha256": sha,
                "bundle_path": tar_path.display().to_string(),
                "applied": apply,
                "applied_dir": if apply { applied_dir.display().to_string() } else { String::new() }
            }));
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
            print_json(&json!({"ok": true, "profile": name, "pinned_sha256": sha256}));
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
                serde_json::from_str::<Value>(&fs::read_to_string(&local_pin_path)?).unwrap_or(Value::Null)
            } else {
                Value::Null
            };
            let local_state = if local_state_path.exists() {
                serde_json::from_str::<Value>(&fs::read_to_string(&local_state_path)?).unwrap_or(Value::Null)
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
            print_json(&json!({
                "profile": name,
                "remote_sha256": remote_sha,
                "local_pin": local_pin,
                "local_state": local_state,
                "pin_check": pin_check
            }));
        }
    }
    Ok(())
}

async fn mcp_profile_call(client: &MissionControlClient, tool: &str, args: Value) -> Result<Value> {
    let response = client
        .post_json("/mcp/call", &json!({ "tool": tool, "args": args }))
        .await?;
    let ok = response.get("ok").and_then(|v| v.as_bool()).unwrap_or(false);
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

async fn handle_workspace(
    command: WorkspaceCommand,
    client: MissionControlClient,
    booster: &AgentBooster,
    schema_pack: &SchemaPack,
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
            let response = call_mcp_tool(
                &client,
                booster,
                schema_pack,
                "load_kluster_workspace",
                args,
            )
            .await?;
            print_json(&response);
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
            let response = call_mcp_tool(
                &client,
                booster,
                schema_pack,
                "fetch_workspace_artifact",
                args,
            )
            .await?;
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
            let response = call_mcp_tool(
                &client,
                booster,
                schema_pack,
                "commit_kluster_workspace",
                args,
            )
            .await?;
            print_json(&response);
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

async fn call_mcp_tool(
    client: &MissionControlClient,
    booster: &AgentBooster,
    schema_pack: &SchemaPack,
    tool: &str,
    args: Value,
) -> Result<Value> {
    mcp_tools::call_tool(client, Some(booster), Some(schema_pack), tool, args).await
}
