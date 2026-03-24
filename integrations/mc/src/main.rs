use clap::Parser;
use mc::{
    auth::resolve_startup_base_url, booster::AgentBooster, client::MissionControlClient,
    commands::McCommand, config::McConfig, output::OutputMode, secrets,
};
use tracing::Level;
use tracing_subscriber::{fmt, EnvFilter};

const DEFAULT_BASE_URL: &str = "http://localhost:8008";

/// Top-level CLI options that control the mc experience.
#[derive(Parser, Debug)]
#[command(author, version, about, long_about = None)]
pub struct CliOpts {
    /// Base URL pointing at an existing Mission Control deployment.
    /// If omitted, falls back to MC_BASE_URL env, then ~/.missioncontrol/config.json,
    /// then http://localhost:8008.
    #[arg(long, env = "MC_BASE_URL")]
    base_url: Option<String>,

    /// Either MC_TOKEN or OIDC creds are used to authenticate against Mission Control.
    #[arg(long, env = "MC_TOKEN", hide_env_values = true)]
    token: Option<String>,

    /// Optional agent identifier that is propagated throughout approvals and sync calls.
    #[arg(long, env = "MC_AGENT_ID")]
    agent_id: Option<String>,

    /// Optional runtime session identifier propagated for per-instance attribution.
    #[arg(long, env = "MC_RUNTIME_SESSION_ID")]
    runtime_session_id: Option<String>,

    /// Optional profile name propagated for per-profile attribution.
    #[arg(long, env = "MC_AGENT_PROFILE")]
    profile_name: Option<String>,

    /// Timeout (in seconds) for all outbound calls.
    #[arg(long, env = "MC_TIMEOUT_SECS", default_value_t = 10)]
    timeout_secs: u64,

    /// Allow invalid TLS certificates when running against local or self-signed endpoints.
    #[arg(long, env = "MC_ALLOW_INSECURE", default_value_t = false)]
    allow_insecure: bool,

    /// Optional WASM booster module path.
    #[arg(long, env = "MC_BOOSTER_WASM")]
    booster_wasm: Option<std::path::PathBuf>,

    /// Disable the booster hook even if a module is configured.
    #[arg(long, env = "MC_DISABLE_BOOSTER", default_value_t = false)]
    disable_booster: bool,

    /// Allow booster modules to short-circuit MCP tool execution.
    /// Disabled by default so authoritative reads/mutations always hit Mission Control.
    #[arg(long, env = "MC_ALLOW_BOOSTER_SHORT_CIRCUIT", default_value_t = false)]
    allow_booster_short_circuit: bool,

    /// Emit JSON output (machine-readable). Compatibility alias for `--output json`.
    #[arg(
        long,
        global = true,
        default_value_t = false,
        conflicts_with = "output"
    )]
    json: bool,

    /// Output format for command responses.
    #[arg(long, global = true, env = "MC_OUTPUT", value_enum, default_value_t = OutputMode::Human)]
    output: OutputMode,

    #[command(subcommand)]
    command: McCommand,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    fmt()
        .with_writer(std::io::stderr)
        .with_env_filter(EnvFilter::from_default_env().add_directive(Level::INFO.into()))
        .init();

    let opts = CliOpts::parse();

    // Resolve base_url: flag/env → ~/.missioncontrol/config.json → hardcoded default.
    let base_url = resolve_startup_base_url(opts.base_url.clone(), DEFAULT_BASE_URL);

    // Resolve the effective token.
    //
    // Prefer session tokens (`mcs_*`) from `~/.missioncontrol/session.json` over
    // non-session `MC_TOKEN` values so `mc auth login` reliably takes effect even
    // when a legacy static token is exported in the shell environment.
    let saved_session_token = mc::config::load_session_token(&base_url);
    if saved_session_token.is_some() {
        tracing::debug!("session token available from ~/.missioncontrol/session.json");
    }
    let token = match opts.token.clone() {
        Some(raw) if raw.starts_with(mc::auth::SESSION_TOKEN_PREFIX) => Some(raw),
        Some(raw) => saved_session_token.or(Some(raw)),
        None => saved_session_token,
    };
    let token = if let Some(raw) = token {
        Some(
            secrets::resolve_maybe_secret_ref(&raw)
                .await
                .map_err(|e| anyhow::anyhow!("failed to resolve MC token secret ref: {e}"))?,
        )
    } else {
        None
    };

    let config = McConfig::from_parts(
        &base_url,
        token,
        opts.agent_id.clone(),
        opts.runtime_session_id.clone(),
        opts.profile_name.clone(),
        opts.timeout_secs,
        opts.allow_insecure,
        !opts.disable_booster,
        opts.allow_booster_short_circuit,
        opts.booster_wasm,
    )?;
    let client = MissionControlClient::new(&config)?;
    let booster = AgentBooster::load(&config)?;

    let output_mode = if opts.json {
        OutputMode::Json
    } else {
        opts.output
    };

    let ctx = config.agent_context.clone();
    mc::commands::run(opts.command, client, ctx, booster, config, output_mode).await
}
