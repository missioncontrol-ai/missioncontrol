use crate::{
    client::MissionControlClient,
    config::McConfig,
    launch::{self, LaunchArgs},
};
use anyhow::{Result, bail};
use clap::{Args, Subcommand};

#[derive(Subcommand, Debug)]
pub enum GeminiCommand {
    /// Run Gemini in a prepared MissionControl runtime.
    Run(GeminiRunArgs),
}

#[derive(Args, Debug)]
pub struct GeminiRunArgs {
    /// Profile name (preferred positional form).
    #[arg(value_name = "PROFILE")]
    profile_positional: Option<String>,
    /// Profile name.
    #[arg(short = 'p', long = "profile")]
    profile_name: Option<String>,
    /// Never prompt; fail on ambiguity.
    #[arg(long, default_value_t = false)]
    headless: bool,
}

pub async fn run_gemini_compat(
    profile: Option<String>,
    _extra_args: Vec<String>,
    client: &MissionControlClient,
    config: &McConfig,
) -> Result<()> {
    let profile_name = resolve_profile(None, profile)?;
    let launch_args = LaunchArgs {
        agent: Some("gemini".to_string()),
        no_daemon: false,
        preflight_only: false,
        skip_config_gen: false,
        profile: Some(profile_name),
        legacy_global_config: false,
        allow_pin_mismatch: false,
        no_embed_token: false,
        agent_args: _extra_args,
    };
    launch::run(launch_args, client, config).await
}

pub async fn run(
    command: GeminiCommand,
    client: &MissionControlClient,
    config: &McConfig,
) -> Result<()> {
    match command {
        GeminiCommand::Run(args) => run_gemini(args, client, config).await,
    }
}

async fn run_gemini(
    args: GeminiRunArgs,
    client: &MissionControlClient,
    config: &McConfig,
) -> Result<()> {
    eprintln!("mc: deprecation notice: `mc gemini run` is being unified — prefer `mc run gemini` (identical behavior)");
    let profile = resolve_profile(args.profile_positional, args.profile_name)?;
    let launch_args = LaunchArgs {
        agent: Some("gemini".to_string()),
        no_daemon: false,
        preflight_only: false,
        skip_config_gen: false,
        profile: Some(profile),
        legacy_global_config: false,
        allow_pin_mismatch: false,
        no_embed_token: false,
        agent_args: Vec::new(),
    };
    let _ = args.headless;
    launch::run(launch_args, client, config).await
}

fn resolve_profile(positional: Option<String>, flag: Option<String>) -> Result<String> {
    if positional.is_some() && flag.is_some() {
        bail!("profile provided both positionally and via --profile; choose one");
    }
    Ok(positional
        .or(flag)
        .unwrap_or_else(|| "default".to_string())
        .trim()
        .to_string())
}
