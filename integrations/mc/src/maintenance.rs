use crate::{client::MissionControlClient, config::McConfig};
use anyhow::Result;
use clap::{Args, Subcommand, ValueEnum};
use reqwest::Method;
use serde_json::{json, Value};
use std::fmt;
use std::time::Duration;
use tokio::time::timeout;
use uuid::Uuid;

#[derive(Subcommand, Debug)]
pub enum MaintenanceCommand {
    /// Diagnostics + repair helpers.
    Doctor(DoctorArgs),
    /// Trigger local backups (postgres, rustfs, or both).
    Backup(BackupArgs),
    /// Cleanup local profile/session artifacts with retention limits.
    ProfileGc(ProfileGcArgs),
}

#[derive(Args, Debug)]
pub struct DoctorArgs {
    #[arg(long, default_value = "/events/stream")]
    pub matrix_endpoint: String,
    #[arg(long, default_value_t = 5)]
    pub matrix_sample_seconds: u64,
    #[arg(long)]
    pub repair: bool,
}

#[derive(Args, Debug)]
pub struct BackupArgs {
    #[arg(long, value_enum, default_value = "all")]
    pub target: BackupTarget,
    #[arg(long)]
    pub reason: Option<String>,
}

#[derive(Args, Debug)]
pub struct ProfileGcArgs {
    /// Keep at most this many runtime instance dirs (newest first).
    #[arg(long, default_value_t = 20)]
    pub keep_instances: usize,
    /// Keep at most this many bundle tar files per profile (newest first).
    #[arg(long, default_value_t = 10)]
    pub keep_bundles: usize,
    /// Remove instance dirs older than this many days regardless of count.
    #[arg(long, default_value_t = 14)]
    pub max_age_days: u64,
}

#[derive(ValueEnum, Clone, Debug)]
pub enum BackupTarget {
    Postgres,
    Rustfs,
    All,
}

impl fmt::Display for BackupTarget {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let label = match self {
            BackupTarget::Postgres => "postgres",
            BackupTarget::Rustfs => "rustfs",
            BackupTarget::All => "all",
        };
        f.write_str(label)
    }
}

pub async fn run(
    command: MaintenanceCommand,
    client: &MissionControlClient,
    config: &McConfig,
) -> Result<()> {
    match command {
        MaintenanceCommand::Doctor(args) => run_doctor(client, config, &args).await,
        MaintenanceCommand::Backup(args) => run_backup(client, args).await,
        MaintenanceCommand::ProfileGc(args) => run_profile_gc(config, args),
    }
}

fn run_profile_gc(config: &McConfig, args: ProfileGcArgs) -> Result<()> {
    let root = crate::config::mc_home_dir();
    let mut removed_instances = Vec::<String>::new();
    let mut removed_bundles = Vec::<String>::new();

    let instances_dir = root.join("instances");
    if instances_dir.exists() {
        let mut entries: Vec<_> = std::fs::read_dir(&instances_dir)?
            .filter_map(|entry| entry.ok())
            .filter(|entry| entry.path().is_dir())
            .collect();
        entries.sort_by_key(|entry| {
            entry
                .metadata()
                .and_then(|m| m.modified())
                .unwrap_or(std::time::SystemTime::UNIX_EPOCH)
        });
        entries.reverse();

        let age_limit = std::time::Duration::from_secs(args.max_age_days.saturating_mul(24 * 60 * 60));
        let now = std::time::SystemTime::now();
        for (idx, entry) in entries.iter().enumerate() {
            let path = entry.path();
            let modified = entry
                .metadata()
                .and_then(|m| m.modified())
                .unwrap_or(std::time::SystemTime::UNIX_EPOCH);
            let old = now.duration_since(modified).unwrap_or_default() > age_limit;
            if idx >= args.keep_instances || old {
                std::fs::remove_dir_all(&path)?;
                removed_instances.push(path.display().to_string());
            }
        }
    }

    let profiles_dir = root.join("profiles");
    if profiles_dir.exists() {
        for profile in std::fs::read_dir(&profiles_dir)?.filter_map(|entry| entry.ok()) {
            let bundles_dir = profile.path().join("bundles");
            if !bundles_dir.exists() {
                continue;
            }
            let mut bundles: Vec<_> = std::fs::read_dir(&bundles_dir)?
                .filter_map(|entry| entry.ok())
                .filter(|entry| entry.path().is_file())
                .collect();
            bundles.sort_by_key(|entry| {
                entry
                    .metadata()
                    .and_then(|m| m.modified())
                    .unwrap_or(std::time::SystemTime::UNIX_EPOCH)
            });
            bundles.reverse();
            for (idx, bundle) in bundles.iter().enumerate() {
                if idx >= args.keep_bundles {
                    let path = bundle.path();
                    std::fs::remove_file(&path)?;
                    removed_bundles.push(path.display().to_string());
                }
            }
        }
    }

    print_json(&json!({
        "ok": true,
        "root": root.display().to_string(),
        "removed_instances": removed_instances,
        "removed_bundles": removed_bundles,
        "keep_instances": args.keep_instances,
        "keep_bundles": args.keep_bundles,
        "max_age_days": args.max_age_days
    }));
    crate::mc_ok!(
        "profile-gc complete: removed {} instance dirs and {} bundle files",
        removed_instances.len(),
        removed_bundles.len()
    );
    let _ = config;
    Ok(())
}

async fn run_doctor(
    client: &MissionControlClient,
    config: &McConfig,
    args: &DoctorArgs,
) -> Result<()> {
    let checks = vec![
        run_health_check(client).await,
        run_tools_check(client).await,
        run_matrix_check(
            client,
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
    print_json(&serde_json::to_value(&report)?);
    Ok(())
}

async fn run_backup(client: &MissionControlClient, args: BackupArgs) -> Result<()> {
    let payload = json!({
        "target": args.target.to_string(),
        "reason": args.reason,
    });
    let response = client.post_json("/ops/backups", &payload).await?;
    print_json(&response);
    Ok(())
}

async fn run_health_check(client: &MissionControlClient) -> DoctorCheck {
    let start = std::time::Instant::now();
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
    let start = std::time::Instant::now();
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
    let start = std::time::Instant::now();
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
        builder
            .header(reqwest::header::ACCEPT, "text/event-stream")
            .send(),
    )
    .await
    {
        Ok(Ok(response)) => response,
        Ok(Err(err)) => {
            let hint = if err.to_string().to_lowercase().contains("tls") {
                Some("Run with MC_ALLOW_INSECURE=true for self-signed certs".into())
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
        .get(reqwest::header::CONTENT_TYPE)
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
    // re-use helpers from config module
    let mut repairs = Vec::new();
    match crate::config::ensure_mc_dirs() {
        Ok(()) => repairs.push(DoctorRepair::ok(
            "directories",
            format!(
                "Ensured MC_HOME={} and skills dir {}",
                crate::config::mc_home_dir().display(),
                crate::config::skills_home_dir().display()
            ),
        )),
        Err(err) => repairs.push(DoctorRepair::failed("directories", err.to_string())),
    }
    if config.agent_context.agent_id.is_none() {
        let agent_id = format!("mc-agent-{}", Uuid::new_v4());
        match crate::config::persist_agent_id(&agent_id) {
            Ok(()) => repairs.push(DoctorRepair::ok(
                "agent_id",
                format!(
                    "Persisted agent_id {} at {}/agent_id",
                    agent_id,
                    crate::config::mc_home_dir().display()
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

#[derive(serde::Serialize)]
struct DoctorReport {
    base_url: String,
    agent_id: Option<String>,
    matrix_endpoint: String,
    checks: Vec<DoctorCheck>,
    repairs: Vec<DoctorRepair>,
}

#[derive(serde::Serialize)]
struct DoctorCheck {
    name: String,
    ok: bool,
    detail: String,
    duration_ms: u128,
    payload: Option<Value>,
    repair_hint: Option<String>,
}

#[derive(serde::Serialize)]
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

fn print_json(value: &Value) {
    println!(
        "{}",
        serde_json::to_string_pretty(value).unwrap_or_else(|_| value.to_string())
    );
}
