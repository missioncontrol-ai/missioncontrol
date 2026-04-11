/// mc-mesh daemon binary.
///
/// Headless — users interact via `mc mesh …` in the mc CLI.
mod attach_gateway;
mod config;
mod daemon;
mod supervisor;
mod task_loop;

use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(
    name = "mc-mesh",
    version,
    about = "mc-mesh daemon — agent coordination for MissionControl"
)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Run the daemon (supervisor + task loops for all configured agents).
    Run {
        #[arg(long, env = "MC_BACKEND_URL", default_value = "")]
        backend_url: String,
        #[arg(long, env = "MC_TOKEN", default_value = "")]
        token: String,
        #[arg(long, env = "MC_MESH_WORK_DIR", default_value = "")]
        work_dir: String,
        #[arg(long, default_value = "30")]
        offline_grace_secs: u64,
    },
    /// Print version.
    Version,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| {
                "mc_mesh=info,mc_mesh_core=info,mc_mesh_work=info,mc_mesh_runtimes=info".into()
            }),
        )
        .init();

    let cli = Cli::parse();
    match cli.command {
        Commands::Run {
            backend_url,
            token,
            work_dir,
            offline_grace_secs,
        } => {
            let work_dir = if work_dir.is_empty() {
                config::DaemonConfig::load_or_default().work_dir
            } else {
                std::path::PathBuf::from(work_dir)
            };
            daemon::run(daemon::CliOverrides {
                backend_url,
                token,
                work_dir,
                offline_grace_secs,
            })
            .await
        }
        Commands::Version => {
            println!("mc-mesh {}", env!("CARGO_PKG_VERSION"));
            Ok(())
        }
    }
}
