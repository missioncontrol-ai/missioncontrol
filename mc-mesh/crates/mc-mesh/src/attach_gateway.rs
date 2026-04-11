/// Local unix socket gateway for `mc mesh attach`.
///
/// The daemon binds a unix socket at `~/.missioncontrol/mc-mesh.sock`.
/// The `mc` CLI connects, sends a single line with the target agent ID,
/// receives `OK\n` (or `ERR <reason>\n`), then I/O becomes raw PTY proxy:
///
///   client → socket → PTY master input
///   PTY master output → socket → client
///
/// This keeps the attachment path entirely local — no backend round-trip.
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

use anyhow::Result;
use mc_mesh_core::agent_runtime::DynAgentRuntime;
use mc_mesh_core::types::AgentHandle;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixListener;
use tokio::sync::Mutex;

/// Return the path to the local control socket.
pub fn socket_path() -> PathBuf {
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("/tmp"))
        .join(".missioncontrol")
        .join("mc-mesh.sock")
}

/// Shared map from agent_id → runtime, built by the daemon.
pub type RuntimeMap = Arc<Mutex<HashMap<String, Arc<DynAgentRuntime>>>>;

/// Start the attach gateway.  Runs until the process is killed.
pub async fn run(runtimes: RuntimeMap) -> Result<()> {
    let path = socket_path();
    // Create parent dir if needed.
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    // Remove stale socket from a previous run.
    let _ = std::fs::remove_file(&path);

    let listener = UnixListener::bind(&path)
        .map_err(|e| anyhow::anyhow!("attach gateway bind {}: {e}", path.display()))?;

    tracing::info!("attach gateway listening on {}", path.display());

    loop {
        match listener.accept().await {
            Ok((stream, _)) => {
                let rt_map = Arc::clone(&runtimes);
                tokio::spawn(async move {
                    if let Err(e) = handle_connection(stream, rt_map).await {
                        tracing::debug!("attach session ended: {e}");
                    }
                });
            }
            Err(e) => {
                tracing::warn!("attach gateway accept error: {e}");
            }
        }
    }
}

async fn handle_connection(
    stream: tokio::net::UnixStream,
    runtimes: RuntimeMap,
) -> Result<()> {
    let (read_half, mut write_half) = stream.into_split();
    let mut reader = BufReader::new(read_half);

    // First line: <agent-id>\n
    let mut agent_id = String::new();
    reader.read_line(&mut agent_id).await?;
    let agent_id = agent_id.trim().to_string();

    if agent_id.is_empty() {
        write_half.write_all(b"ERR empty agent id\n").await?;
        return Ok(());
    }

    // Look up the runtime.
    let runtime = {
        let map = runtimes.lock().await;
        map.get(&agent_id).cloned()
    };
    let Some(runtime) = runtime else {
        write_half
            .write_all(format!("ERR agent {agent_id} not found\n").as_bytes())
            .await?;
        return Ok(());
    };

    // Open PTY session.
    let handle = AgentHandle {
        agent_id: agent_id.clone(),
        runtime_kind: runtime.kind(),
        pid: 0,
    };
    let session = match runtime.attach_pty(&handle).await {
        Ok(s) => s,
        Err(e) => {
            write_half
                .write_all(format!("ERR {e}\n").as_bytes())
                .await?;
            return Ok(());
        }
    };

    write_half.write_all(b"OK\n").await?;
    tracing::info!("attach session started for agent {agent_id}");

    let mut pty_output = session.output;
    let pty_input = session.input;

    // PTY output → socket  (spawned task)
    tokio::spawn(async move {
        while let Some(bytes) = pty_output.recv().await {
            if write_half.write_all(&bytes).await.is_err() {
                break;
            }
        }
    });

    // Socket → PTY input  (this task)
    let mut read_raw = reader.into_inner();
    let mut buf = vec![0u8; 4096];
    loop {
        match read_raw.read(&mut buf).await {
            Ok(0) | Err(_) => break,
            Ok(n) => {
                if pty_input.send(buf[..n].to_vec()).await.is_err() {
                    break;
                }
            }
        }
    }

    tracing::info!("attach session ended for agent {agent_id}");
    Ok(())
}
