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

    /// Bind to an existing mission.
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

pub async fn run(args: RunArgs, client: &crate::client::MissionControlClient, config: &McConfig) -> Result<()> {
    eprintln!("mc: `mc run <runtime>` is the new unified interface for launching agent runtimes.");
    match args.runtime.as_str() {
        "claude" => {
            claude::run_claude_compat(args.profile, args.passthrough, config).await
        }
        "codex" => {
            codex::run_codex_compat(args.profile, args.passthrough, config).await
        }
        "gemini" => {
            gemini::run_gemini_compat(args.profile, args.passthrough, client, config).await
        }
        other => {
            eprintln!("mc: unknown runtime '{}'. Known: claude, codex, gemini", other);
            std::process::exit(1);
        }
    }
}
