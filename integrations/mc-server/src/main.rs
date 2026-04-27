use mc_server::{build_app, AppConfig};
use clap::Parser;

#[derive(Parser, Debug)]
#[command(name = "mc-server", about = "MissionControl server node")]
struct Args {
    /// Enable Raft server mode (HTTP API + distributed state)
    #[arg(long)]
    serve: bool,

    /// Bootstrap a new single-node cluster (first node only)
    #[arg(long)]
    raft_bootstrap: bool,

    /// Join an existing cluster (comma-separated peer URLs)
    #[arg(long, value_delimiter = ',')]
    raft_join: Vec<String>,

    /// Serve the mc-ui web frontend
    #[arg(long)]
    ui: bool,

    /// Proxy all unknown routes to a legacy backend URL (migration mode)
    #[arg(long)]
    api_proxy: Option<String>,

    /// Bind address for HTTP server
    #[arg(long, default_value = "0.0.0.0:8008", env = "MC_BIND")]
    bind: String,

    /// Unique node ID within the cluster
    #[arg(long, env = "MC_NODE_ID")]
    node_id: Option<u64>,

    /// Advertised URL for this node (used by peers and mc CLI discovery)
    #[arg(long, env = "MC_ADVERTISE_URL")]
    advertise_url: Option<String>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            std::env::var("RUST_LOG").unwrap_or_else(|_| "mc_server=info".into()),
        )
        .init();

    let args = Args::parse();

    if !args.serve && !args.ui && args.api_proxy.is_none() {
        eprintln!("error: specify at least one of --serve, --ui, or --api-proxy <url>");
        std::process::exit(1);
    }

    tracing::info!(
        serve = args.serve,
        ui = args.ui,
        api_proxy = args.api_proxy.as_deref().unwrap_or("none"),
        bind = %args.bind,
        "mc-server starting",
    );

    let config = AppConfig {
        api_proxy: args.api_proxy.clone(),
    };

    let app = build_app(config);
    let listener = tokio::net::TcpListener::bind(&args.bind).await?;
    tracing::info!(bind = %args.bind, "listening");
    axum::serve(listener, app).await?;

    Ok(())
}
