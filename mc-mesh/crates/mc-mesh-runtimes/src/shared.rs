/// Utilities shared across all AgentRuntime implementations.
use anyhow::Result;
use std::path::Path;
use tokio::process::Child;

/// Spawn a CLI binary as a supervised child.  Returns the child handle.
pub async fn spawn_cli(
    binary: &str,
    args: &[&str],
    work_dir: &Path,
    env: &[(String, String)],
) -> Result<Child> {
    let mut cmd = tokio::process::Command::new(binary);
    cmd.args(args)
        .current_dir(work_dir)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());
    for (k, v) in env {
        cmd.env(k, v);
    }
    Ok(cmd.spawn()?)
}

/// Gracefully kill a child, then force-kill if it doesn't exit within timeout.
pub async fn shutdown_child(mut child: Child, timeout_secs: u64) -> Result<()> {
    use tokio::time::{sleep, Duration};
    // SIGTERM
    let _ = child.kill().await;
    tokio::select! {
        _ = child.wait() => {}
        _ = sleep(Duration::from_secs(timeout_secs)) => {
            let _ = child.kill().await;
        }
    }
    Ok(())
}
