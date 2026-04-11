/// SoloSupervisor — run an agent runtime in the foreground while participating
/// in the mesh as a MeshAgent with supervision_mode=solo.
///
/// Does NOT require the mc-mesh daemon.  Talks directly to the backend HTTP API
/// using the same `MissionControlClient` that `mc` already uses.
///
/// Flow:
///   1. Enroll a MeshAgent with supervision_mode=solo
///   2. Create an AgentRun record
///   3. Start a background heartbeat thread
///   4. Block on `launch_fn` (which execs the runtime as a child process)
///   5. Stop heartbeat thread
///   6. Mark agent offline
///   7. Transition run to completed/failed
use anyhow::{Context, Result};
use serde_json::json;
use std::process::ExitStatus;
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};
use std::time::Duration;

use crate::client::MissionControlClient;

/// Enroll a MeshAgent, start a run record, execute `launch_fn`, then clean up.
///
/// `launch_fn` receives `(agent_id, run_id)` so it can inject `MC_MESH_AGENT_ID`
/// and `MC_RUN_ID` into the child process environment.  It must block until the
/// child exits and return the exit status.
pub async fn run_solo<F>(
    client: &MissionControlClient,
    mission_id: &str,
    runtime_kind: &str,
    profile_name: &str,
    launch_fn: F,
) -> Result<ExitStatus>
where
    F: FnOnce(&str, Option<&str>) -> Result<ExitStatus> + Send + 'static,
{
    // 1. Enroll the agent with supervision_mode=solo.
    let enroll_body = json!({
        "runtime_kind": runtime_kind,
        "capabilities": ["tool_use", "file_edit"],
        "labels": {},
        "supervision_mode": "solo",
        "profile": {"name": profile_name},
    });
    let enroll_path = format!("/work/missions/{mission_id}/agents/enroll");
    let enroll_resp = client
        .post_json(&enroll_path, &enroll_body)
        .await
        .context("failed to enroll mesh agent")?;
    let agent_id = enroll_resp
        .get("id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    if agent_id.is_empty() {
        anyhow::bail!("backend returned empty agent id during enroll");
    }
    eprintln!(
        "mc: enrolled as mesh agent {} (mission {})",
        agent_id, mission_id
    );

    // 2. Create a run record (best-effort — doesn't abort if backend doesn't support it yet).
    let run_resp = client
        .post_json(
            "/runs",
            &json!({
                "mesh_agent_id": agent_id,
                "runtime_kind": runtime_kind,
            }),
        )
        .await
        .ok();
    let run_id: Option<String> = run_resp
        .as_ref()
        .and_then(|v| v.get("id"))
        .and_then(|v| v.as_str())
        .map(String::from);

    // 3. Start background heartbeat thread.
    //    MissionControlClient is Clone so we hand a clone to the thread.
    let hb_client = client.clone();
    let hb_agent_id = agent_id.clone();
    let stop_flag = Arc::new(AtomicBool::new(false));
    let hb_stop = Arc::clone(&stop_flag);

    let hb_thread = std::thread::spawn(move || {
        let rt = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("heartbeat runtime");
        rt.block_on(async move {
            loop {
                tokio::time::sleep(Duration::from_secs(25)).await;
                if hb_stop.load(Ordering::Relaxed) {
                    break;
                }
                let _ = hb_client
                    .post_json(
                        &format!("/work/agents/{hb_agent_id}/heartbeat"),
                        &json!({}),
                    )
                    .await;
            }
        });
    });

    // 4. Launch the runtime — blocks until the child exits.
    let status = launch_fn(&agent_id, run_id.as_deref())?;

    // 5. Stop heartbeat thread.
    stop_flag.store(true, Ordering::Relaxed);
    let _ = hb_thread.join();

    // 6. Mark agent offline.
    let _ = client
        .post_json(
            &format!("/work/agents/{agent_id}/status?status=offline"),
            &json!({}),
        )
        .await;

    // 7. Transition run to completed or failed.
    if let Some(ref rid) = run_id {
        let terminal = if status.success() { "completed" } else { "failed" };
        let _ = client
            .post_json(&format!("/runs/{rid}/{terminal}"), &json!({}))
            .await;
    }

    eprintln!("mc: agent {} offline", agent_id);
    Ok(status)
}
