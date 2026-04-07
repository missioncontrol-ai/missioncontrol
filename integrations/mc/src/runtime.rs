use crate::{client::MissionControlClient, output, output::OutputMode};
use anyhow::{Context, Result};
use clap::{Args, Subcommand};
use serde_json::{json, Value};
use std::{fs, path::PathBuf, process::Stdio, time::Duration};
use tokio::{
    io::{AsyncBufReadExt, BufReader},
    process::Command,
    time::sleep,
};
use tracing::info;

#[derive(Subcommand, Debug)]
pub enum RuntimeCommand {
    /// Runtime node operations.
    #[command(subcommand)]
    Nodes(RuntimeNodesCommand),
    /// Runtime job operations.
    #[command(subcommand)]
    Jobs(RuntimeJobsCommand),
    /// Runtime lease helpers.
    #[command(subcommand)]
    Leases(RuntimeLeasesCommand),
}

#[derive(Subcommand, Debug)]
pub enum NodeAgentCommand {
    /// Register a node with MissionControl and persist its identity locally.
    Register(NodeAgentRegisterArgs),
    /// Run the resident node loop.
    Run(NodeAgentRunArgs),
    /// Inspect local node-agent readiness.
    Doctor(NodeAgentDoctorArgs),
}

#[derive(Subcommand, Debug)]
pub enum RuntimeNodesCommand {
    Register(RuntimeNodeRegisterArgs),
    List(RuntimeListArgs),
    Heartbeat(RuntimeNodeHeartbeatArgs),
}

#[derive(Subcommand, Debug)]
pub enum RuntimeJobsCommand {
    Submit(RuntimeJobSubmitArgs),
    List(RuntimeListArgs),
}

#[derive(Subcommand, Debug)]
pub enum RuntimeLeasesCommand {
    Create(RuntimeLeaseCreateArgs),
    Status(RuntimeLeaseStatusArgs),
    Complete(RuntimeLeaseCompleteArgs),
}

#[derive(Args, Debug)]
pub struct RuntimeNodeRegisterArgs {
    #[arg(long)]
    pub node_name: String,
    #[arg(long, default_value = "")]
    pub hostname: String,
    #[arg(long, default_value = "untrusted")]
    pub trust_tier: String,
}

#[derive(Args, Debug)]
pub struct NodeAgentRegisterArgs {
    #[arg(long)]
    pub node_name: String,
    #[arg(long, default_value = "")]
    pub hostname: String,
    #[arg(long, default_value = "untrusted")]
    pub trust_tier: String,
}

#[derive(Args, Debug)]
pub struct NodeAgentRunArgs {
    #[arg(long, default_value = "30")]
    pub poll_seconds: u64,
    #[arg(long, default_value = "15")]
    pub heartbeat_seconds: u64,
    #[arg(long, default_value = "node")]
    pub node_name: String,
    #[arg(long, default_value = "")]
    pub hostname: String,
    #[arg(long, default_value = "untrusted")]
    pub trust_tier: String,
    #[arg(long, default_value = "container,host_process")]
    pub capabilities: String,
    #[arg(long, default_value = "")]
    pub labels: String,
}

#[derive(Args, Debug)]
pub struct NodeAgentDoctorArgs {
    #[arg(long, default_value = "node")]
    pub node_name: String,
}

#[derive(Args, Debug)]
pub struct RuntimeNodeHeartbeatArgs {
    #[arg(long)]
    pub node_id: String,
    #[arg(long, default_value = "online")]
    pub status: String,
}

#[derive(Args, Debug)]
pub struct RuntimeJobSubmitArgs {
    #[arg(long, default_value = "")]
    pub mission_id: String,
    #[arg(long, default_value = "")]
    pub runtime_session_id: String,
    #[arg(long, default_value = "container")]
    pub runtime_class: String,
    #[arg(long, default_value = "")]
    pub image: String,
    #[arg(long, default_value = "")]
    pub command: String,
}

#[derive(Args, Debug)]
pub struct RuntimeLeaseCreateArgs {
    #[arg(long)]
    pub job_id: String,
    #[arg(long)]
    pub node_id: String,
}

#[derive(Args, Debug)]
pub struct RuntimeLeaseStatusArgs {
    #[arg(long)]
    pub lease_id: String,
    #[arg(long)]
    pub status: String,
}

#[derive(Args, Debug)]
pub struct RuntimeLeaseCompleteArgs {
    #[arg(long)]
    pub lease_id: String,
    #[arg(long, default_value_t = 0)]
    pub exit_code: i32,
    #[arg(long, default_value = "")]
    pub error_message: String,
}

#[derive(Args, Debug, Default)]
pub struct RuntimeListArgs {
    #[arg(long)]
    pub status: Option<String>,
}

pub async fn run(command: RuntimeCommand, client: &MissionControlClient, output_mode: OutputMode) -> Result<()> {
    match command {
        RuntimeCommand::Nodes(cmd) => run_nodes(cmd, client, output_mode).await,
        RuntimeCommand::Jobs(cmd) => run_jobs(cmd, client, output_mode).await,
        RuntimeCommand::Leases(cmd) => run_leases(cmd, client, output_mode).await,
    }
}

pub async fn run_node_agent(command: NodeAgentCommand, client: &MissionControlClient) -> Result<()> {
    match command {
        NodeAgentCommand::Register(args) => run_node_register(args, client).await,
        NodeAgentCommand::Run(args) => run_node_run(args, client).await,
        NodeAgentCommand::Doctor(args) => run_node_doctor(args).await,
    }
}

async fn run_nodes(command: RuntimeNodesCommand, client: &MissionControlClient, output_mode: OutputMode) -> Result<()> {
    match command {
        RuntimeNodesCommand::Register(args) => {
            let response = client
                .post_json(
                    "/runtime/nodes/register",
                    &json!({"node_name": args.node_name,"hostname": args.hostname,"trust_tier": args.trust_tier}),
                )
                .await?;
            output::print_value(output_mode, &response);
        }
        RuntimeNodesCommand::List(_) => {
            let response = client.get_json("/runtime/nodes").await?;
            output::print_value(output_mode, &response);
        }
        RuntimeNodesCommand::Heartbeat(args) => {
            let response = client
                .post_json(
                    &format!("/runtime/nodes/{}/heartbeat", args.node_id),
                    &json!({"status": args.status}),
                )
                .await?;
            output::print_value(output_mode, &response);
        }
    }
    Ok(())
}

async fn run_jobs(command: RuntimeJobsCommand, client: &MissionControlClient, output_mode: OutputMode) -> Result<()> {
    match command {
        RuntimeJobsCommand::Submit(args) => {
            let response = client
                .post_json(
                    "/runtime/jobs",
                    &json!({"mission_id": args.mission_id,"runtime_session_id": args.runtime_session_id,"runtime_class": args.runtime_class,"image": args.image,"command": args.command}),
                )
                .await?;
            output::print_value(output_mode, &response);
        }
        RuntimeJobsCommand::List(args) => {
            let path = match args.status {
                Some(status) if !status.trim().is_empty() => format!("/runtime/jobs?status={status}"),
                _ => "/runtime/jobs".to_string(),
            };
            let response = client.get_json(&path).await?;
            output::print_value(output_mode, &response);
        }
    }
    Ok(())
}

async fn run_leases(command: RuntimeLeasesCommand, client: &MissionControlClient, output_mode: OutputMode) -> Result<()> {
    match command {
        RuntimeLeasesCommand::Create(args) => {
            let response = client
                .post_json(
                    &format!("/runtime/jobs/{}/leases", args.job_id),
                    &json!({"node_id": args.node_id}),
                )
                .await?;
            output::print_value(output_mode, &response);
        }
        RuntimeLeasesCommand::Status(args) => {
            let response = client
                .post_json(
                    &format!("/runtime/leases/{}/status", args.lease_id),
                    &json!({"status": args.status}),
                )
                .await?;
            output::print_value(output_mode, &response);
        }
        RuntimeLeasesCommand::Complete(args) => {
            let response = client
                .post_json(
                    &format!("/runtime/leases/{}/complete", args.lease_id),
                    &json!({"exit_code": args.exit_code,"error_message": args.error_message}),
                )
                .await?;
            output::print_value(output_mode, &response);
        }
    }
    Ok(())
}

#[derive(Clone, Debug)]
struct NodeState {
    node_id: String,
    node_name: String,
}

fn node_state_path() -> PathBuf {
    crate::config::mc_home_dir().join("runtime").join("node.json")
}

fn load_node_state() -> Result<Option<NodeState>> {
    let path = node_state_path();
    let raw = match fs::read_to_string(&path) {
        Ok(value) => value,
        Err(_) => return Ok(None),
    };
    let json: Value = serde_json::from_str(&raw).context("invalid node state json")?;
    let node_id = json
        .get("node_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let node_name = json
        .get("node_name")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    if node_id.is_empty() || node_name.is_empty() {
        return Ok(None);
    }
    Ok(Some(NodeState { node_id, node_name }))
}

fn persist_node_state(state: &NodeState) -> Result<()> {
    let path = node_state_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(
        path,
        serde_json::to_string_pretty(&json!({
            "node_id": state.node_id,
            "node_name": state.node_name,
        }))?,
    )?;
    Ok(())
}

async fn run_node_register(args: NodeAgentRegisterArgs, client: &MissionControlClient) -> Result<()> {
    let response = client
        .post_json(
            "/runtime/nodes/register",
            &json!({
                "node_name": args.node_name,
                "hostname": args.hostname,
                "trust_tier": args.trust_tier,
                "bootstrap_token": "",
            }),
        )
        .await?;
    let state = NodeState {
        node_id: response
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
        node_name: response
            .get("node_name")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
    };
    persist_node_state(&state)?;
    output::print_value(OutputMode::Json, &response);
    Ok(())
}

async fn run_node_doctor(args: NodeAgentDoctorArgs) -> Result<()> {
    let state = load_node_state()?;
    let payload = json!({
        "ok": state.is_some(),
        "node_name": args.node_name,
        "state_path": node_state_path(),
        "registered": state.as_ref().map(|s| s.node_name.clone()),
    });
    output::print_value(OutputMode::Json, &payload);
    Ok(())
}

async fn run_node_run(args: NodeAgentRunArgs, client: &MissionControlClient) -> Result<()> {
    let state = match load_node_state()? {
        Some(state) => state,
        None => {
            let response = client
                .post_json(
                    "/runtime/nodes/register",
                    &json!({
                        "node_name": args.node_name,
                        "hostname": args.hostname,
                        "trust_tier": args.trust_tier,
                        "bootstrap_token": "",
                    }),
                )
                .await?;
            let state = NodeState {
                node_id: response
                    .get("id")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
                node_name: response
                    .get("node_name")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
            };
            persist_node_state(&state)?;
            state
        }
    };

    let heartbeat_interval = Duration::from_secs(args.heartbeat_seconds.max(1));
    let poll_interval = Duration::from_secs(args.poll_seconds.max(1));
    let capabilities: Vec<String> = args
        .capabilities
        .split(',')
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .collect();
    let labels = parse_kv_pairs(&args.labels);

    info!(node_id = %state.node_id, node_name = %state.node_name, "runtime node loop starting");
    let mut last_heartbeat = tokio::time::Instant::now() - heartbeat_interval;
    loop {
        if last_heartbeat.elapsed() >= heartbeat_interval {
            let _ = client
                .post_json(
                    &format!("/runtime/nodes/{}/heartbeat", state.node_id),
                    &json!({"status":"online","capabilities":capabilities,"labels":labels,"runtime_version":"mc-runtime-node"}),
                )
                .await;
            last_heartbeat = tokio::time::Instant::now();
        }

        let claim = client
            .post_json(
                &format!("/runtime/nodes/{}/leases/claim", state.node_id),
                &json!({}),
            )
            .await
            .unwrap_or_else(|_| json!({"claimed":false}));

        if claim.get("claimed").and_then(Value::as_bool).unwrap_or(false) {
            let lease = claim.get("lease").cloned().unwrap_or_else(|| json!({}));
            let job = claim.get("job").cloned().unwrap_or_else(|| json!({}));
            let lease_id = lease.get("id").and_then(Value::as_str).unwrap_or("").to_string();
            if lease_id.is_empty() {
                sleep(poll_interval).await;
                continue;
            }
            let runtime_class = job.get("runtime_class").and_then(Value::as_str).unwrap_or("container");
            let command = job.get("command").and_then(Value::as_str).unwrap_or("");
            let args_vec = job
                .get("args")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default()
                .into_iter()
                .filter_map(|value| value.as_str().map(|s| s.to_string()))
                .collect::<Vec<_>>();
            let env_map = job.get("env").and_then(Value::as_object).cloned().unwrap_or_default();
            let cwd = job.get("cwd").and_then(Value::as_str).unwrap_or("");

            let _ = client
                .post_json(
                    &format!("/runtime/leases/{}/status", lease_id),
                    &json!({"status":"running"}),
                )
                .await;
            let result = execute_job(
                client,
                &lease_id,
                runtime_class,
                command,
                &args_vec,
                env_map,
                cwd,
            )
            .await;
            match result {
                Ok(exit_code) => {
                    let _ = client
                        .post_json(
                            &format!("/runtime/leases/{}/complete", lease_id),
                            &json!({"exit_code": exit_code,"error_message": ""}),
                        )
                        .await;
                }
                Err(err) => {
                    let _ = client
                        .post_json(
                            &format!("/runtime/leases/{}/complete", lease_id),
                            &json!({"exit_code": 1,"error_message": err.to_string()}),
                        )
                        .await;
                }
            }
        }

        sleep(poll_interval).await;
    }
}

async fn execute_job(
    client: &MissionControlClient,
    lease_id: &str,
    runtime_class: &str,
    command: &str,
    args: &[String],
    env_map: serde_json::Map<String, Value>,
    cwd: &str,
) -> Result<i32> {
    let mut cmd = if command.trim().is_empty() {
        let mut shell = Command::new("sh");
        shell.arg("-lc").arg("true");
        shell
    } else {
        let mut process = Command::new(command);
        process.args(args);
        process
    };
    if !cwd.trim().is_empty() {
        cmd.current_dir(cwd);
    }
    for (key, value) in env_map {
        if let Some(value) = value.as_str() {
            cmd.env(key, value);
        }
    }
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = cmd.spawn().context("failed to spawn runtime job")?;
    if let Some(stdout) = child.stdout.take() {
        let client = client.clone();
        let lease_id = lease_id.to_string();
        tokio::spawn(async move {
            let mut lines = BufReader::new(stdout).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                let _ = client
                    .post_json(
                        &format!("/runtime/leases/{}/logs", lease_id),
                        &json!({"stream":"stdout","content":line}),
                    )
                    .await;
            }
        });
    }
    if let Some(stderr) = child.stderr.take() {
        let client = client.clone();
        let lease_id = lease_id.to_string();
        tokio::spawn(async move {
            let mut lines = BufReader::new(stderr).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                let _ = client
                    .post_json(
                        &format!("/runtime/leases/{}/logs", lease_id),
                        &json!({"stream":"stderr","content":line}),
                    )
                    .await;
            }
        });
    }
    let status = child.wait().await.context("runtime job wait failed")?;
    if runtime_class == "host_process" {
        info!(exit_code = status.code().unwrap_or(1), "host_process job complete");
    } else {
        info!(exit_code = status.code().unwrap_or(1), "container job complete");
    }
    Ok(status.code().unwrap_or(1))
}

fn parse_kv_pairs(input: &str) -> Value {
    let mut map = serde_json::Map::new();
    for item in input.split(',') {
        let trimmed = item.trim();
        if trimmed.is_empty() {
            continue;
        }
        if let Some((key, value)) = trimmed.split_once('=') {
            map.insert(key.trim().to_string(), Value::String(value.trim().to_string()));
        }
    }
    Value::Object(map)
}
