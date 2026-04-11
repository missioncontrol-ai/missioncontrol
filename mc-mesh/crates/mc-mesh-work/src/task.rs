use mc_mesh_core::client::BackendClient;
use mc_mesh_core::types::MeshTaskRecord;
use anyhow::Result;

/// Poll the backend for tasks this agent can work on.
///
/// Returns `ready` tasks (all policies) plus `running` broadcast tasks — an
/// agent can join a broadcast task that's already been claimed by other agents.
pub async fn poll_ready_tasks(
    client: &BackendClient,
    kluster_id: &str,
    _capabilities: &[mc_mesh_core::types::Capability],
) -> Result<Vec<MeshTaskRecord>> {
    let mut ready: Vec<MeshTaskRecord> = client
        .get(&format!("/work/klusters/{kluster_id}/tasks?status=ready"))
        .await
        .unwrap_or_default();

    // Also fetch broadcast tasks that are already running so every agent joins.
    let broadcast_running: Vec<MeshTaskRecord> = client
        .get(&format!("/work/klusters/{kluster_id}/tasks?status=running"))
        .await
        .unwrap_or_default();

    for t in broadcast_running {
        if t.claim_policy == "broadcast" {
            ready.push(t);
        }
    }

    Ok(ready)
}

/// Claim a task.  Returns the lease expiry timestamp string.
pub async fn claim_task(client: &BackendClient, task_id: &str) -> Result<serde_json::Value> {
    client
        .post_empty(&format!("/work/tasks/{task_id}/claim"))
        .await
}

/// Send a heartbeat to renew the lease on a running task.
pub async fn heartbeat_task(client: &BackendClient, task_id: &str) -> Result<serde_json::Value> {
    client
        .post_empty(&format!("/work/tasks/{task_id}/heartbeat"))
        .await
}

/// Post a typed progress event.
pub async fn post_progress(
    client: &BackendClient,
    task_id: &str,
    event: &mc_mesh_core::progress::ProgressEvent,
) -> Result<()> {
    use serde_json::json;
    let body = json!({
        "event_type": event.event_type.to_string(),
        "phase": event.phase,
        "step": event.step,
        "summary": event.summary,
        "payload_json": event.payload.to_string(),
    });
    client
        .raw_post(&format!("/work/tasks/{task_id}/progress"), &body)
        .await?;
    Ok(())
}

/// Mark a task complete.
pub async fn complete_task(
    client: &BackendClient,
    task_id: &str,
    result_artifact_id: Option<&str>,
) -> Result<serde_json::Value> {
    use serde_json::json;
    let body = json!({ "result_artifact_id": result_artifact_id });
    client
        .post(&format!("/work/tasks/{task_id}/complete"), &body)
        .await
}

/// Mark a task failed.
pub async fn fail_task(client: &BackendClient, task_id: &str, error: &str) -> Result<serde_json::Value> {
    use serde_json::json;
    let body = json!({ "error": error });
    client
        .post(&format!("/work/tasks/{task_id}/fail"), &body)
        .await
}
