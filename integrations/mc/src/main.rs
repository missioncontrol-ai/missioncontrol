use clap::Parser;
use mc::{
    booster::AgentBooster, client::MissionControlClient, commands::McCommand, config::McConfig,
};
use tracing::Level;
use tracing_subscriber::{fmt, EnvFilter};

/// Top-level CLI options that control the mc experience.
#[derive(Parser, Debug)]
#[command(author, version, about, long_about = None)]
pub struct CliOpts {
    /// Base URL pointing at an existing Mission Control deployment.
    #[arg(long, env = "MC_BASE_URL", default_value = "http://localhost:8008")]
    base_url: String,

    /// Either MC_TOKEN or OIDC creds are used to authenticate against Mission Control.
    #[arg(long, env = "MC_TOKEN")]
    token: Option<String>,

    /// Optional agent identifier that is propagated throughout approvals and sync calls.
    #[arg(long, env = "MC_AGENT_ID")]
    agent_id: Option<String>,

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

    #[command(subcommand)]
    command: McCommand,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    fmt()
        .with_env_filter(EnvFilter::from_default_env().add_directive(Level::INFO.into()))
        .init();

    let opts = CliOpts::parse();
    let config = McConfig::from_parts(
        &opts.base_url,
        opts.token.clone(),
        opts.agent_id.clone(),
        opts.timeout_secs,
        opts.allow_insecure,
        !opts.disable_booster,
        opts.booster_wasm,
    )?;
    let client = MissionControlClient::new(&config)?;
    let booster = AgentBooster::load(&config)?;

    let ctx = config.agent_context.clone();
    mc::commands::run(opts.command, client, ctx, booster, config).await
}
