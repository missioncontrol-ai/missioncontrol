use crate::{claude, codex, config::McConfig, gemini};
use anyhow::Result;
use clap::{Args, ValueEnum};

#[derive(Args, Debug)]
pub struct RunArgs {
    /// Runtime to launch (e.g. claude, codex, gemini).
    #[arg(value_name = "RUNTIME")]
    pub runtime: String,

    /// Profile name.
    #[arg(short = 'p', long = "profile")]
    pub profile: Option<String>,

    /// Bind to an existing mission (enables mesh participation via SoloSupervisor).
    #[arg(long)]
    pub mission: Option<String>,

    /// Bind to an existing kluster.
    #[arg(long)]
    pub kluster: Option<String>,

    /// Bind to an existing task.
    #[arg(long)]
    pub task: Option<String>,

    /// Execution mode.
    #[arg(long, value_enum, default_value = "interactive")]
    pub mode: RunMode,

    /// Args passed through to the runtime binary (after --).
    #[arg(last = true)]
    pub passthrough: Vec<String>,
}

#[derive(ValueEnum, Debug, Clone)]
pub enum RunMode {
    Interactive,
    Headless,
    Solo,
}

pub async fn run(
    args: RunArgs,
    client: &crate::client::MissionControlClient,
    config: &McConfig,
) -> Result<()> {
    eprintln!("mc: `mc run <runtime>` is the new unified interface for launching agent runtimes.");

    // --mission or --mode solo → use SoloSupervisor (mesh-visible run).
    let use_solo = args.mission.is_some() || matches!(args.mode, RunMode::Solo);

    if use_solo {
        let mission_id = args
            .mission
            .as_deref()
            .unwrap_or("")
            .to_string();
        if mission_id.is_empty() {
            anyhow::bail!("--mission <id> is required when --mode solo is set");
        }
        let profile = args
            .profile
            .clone()
            .unwrap_or_else(|| "default".to_string());
        let passthrough = args.passthrough.clone();
        let config_clone = config.clone();

        match args.runtime.as_str() {
            "claude" => {
                let paths = claude::claude_paths(&profile);
                let profile_clone = profile.clone();
                crate::solo_supervisor::run_solo(
                    client,
                    &mission_id,
                    "claude_code",
                    &profile,
                    move |agent_id, run_id| {
                        claude::launch_claude_blocking(
                            &passthrough,
                            &paths.runtime_home,
                            &config_clone,
                            &profile_clone,
                            agent_id,
                            run_id,
                        )
                    },
                )
                .await?;
            }
            "codex" => {
                let paths = codex::codex_paths(&profile);
                let profile_clone = profile.clone();
                crate::solo_supervisor::run_solo(
                    client,
                    &mission_id,
                    "codex",
                    &profile,
                    move |agent_id, run_id| {
                        codex::launch_codex_blocking(
                            &passthrough,
                            &paths.runtime_home,
                            &config_clone,
                            &profile_clone,
                            agent_id,
                            run_id,
                        )
                    },
                )
                .await?;
            }
            other => {
                anyhow::bail!(
                    "--mode solo is not yet supported for runtime '{}'; try claude or codex",
                    other
                );
            }
        }
        return Ok(());
    }

    // Fallback: direct exec without mesh participation.
    match args.runtime.as_str() {
        "claude" => claude::run_claude_compat(args.profile, args.passthrough, config).await,
        "codex" => codex::run_codex_compat(args.profile, args.passthrough, config).await,
        "gemini" => {
            gemini::run_gemini_compat(args.profile, args.passthrough, client, config).await
        }
        other => {
            eprintln!(
                "mc: unknown runtime '{}'. Known: claude, codex, gemini",
                other
            );
            std::process::exit(1);
        }
    }
}
