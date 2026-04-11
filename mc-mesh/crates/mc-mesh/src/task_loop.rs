/// Per-agent task claim → inject → forward loop.
///
/// Each supervised agent runs one of these concurrently.  The loop:
///   1. Polls the backend for ready tasks in all klusters of the agent's mission
///   2. Claims the highest-priority eligible task
///   3. Injects the task into the agent runtime
///   4. Forwards progress events to the backend in real time
///   5. Heartbeats the lease every HEARTBEAT_INTERVAL
///   6. Marks the task complete or failed when the progress stream closes
///
/// A parallel message relay loop polls inbound messages and delivers them to
/// the runtime via `AgentRuntime::signal()`.
use std::sync::Arc;
use std::time::Duration;

use anyhow::Result;
use futures::StreamExt;
use mc_mesh_core::client::BackendClient;
use mc_mesh_core::types::{AgentHandle, AgentSignal, TaskSpec};
use mc_mesh_work::watchdog::{ConnectivityState, OfflinePolicy};
use mc_mesh_work::{claim, task};

const POLL_INTERVAL: Duration = Duration::from_secs(5);
const HEARTBEAT_INTERVAL: Duration = Duration::from_secs(30);
const MESSAGE_POLL_INTERVAL: Duration = Duration::from_secs(3);

/// Run the task loop for a single agent.  Never returns under normal operation.
///
/// Spawns a parallel message relay loop so inbound peer messages are delivered
/// to the runtime via `signal()` even while a task is being executed.
pub async fn run_for_agent(
    agent: Arc<tokio::sync::Mutex<AgentHandle>>,
    runtime: Arc<mc_mesh_core::agent_runtime::DynAgentRuntime>,
    client: Arc<BackendClient>,
    mission_id: String,
    agent_id: String,
    watchdog: Arc<mc_mesh_work::watchdog::Watchdog>,
) {
    // Spawn the message relay as a detached background task.
    {
        let relay_agent = Arc::clone(&agent);
        let relay_runtime = Arc::clone(&runtime);
        let relay_client = Arc::clone(&client);
        let relay_agent_id = agent_id.clone();
        tokio::spawn(async move {
            run_message_relay(relay_agent, relay_runtime, relay_client, relay_agent_id).await;
        });
    }
    // Track the last claimed task so we can fail it if we go offline mid-run.
    let mut current_task_id: Option<String> = None;

    loop {
        // Enforce offline policy before doing any work.
        let connectivity = *watchdog.state_rx.borrow();
        match (watchdog.policy(), connectivity) {
            // Strict: if offline, fail any in-flight task and stop claiming.
            (OfflinePolicy::Strict, ConnectivityState::Offline { .. }) => {
                if let Some(tid) = current_task_id.take() {
                    tracing::warn!(
                        "Watchdog strict offline: failing in-flight task {tid} for agent {agent_id}"
                    );
                    // Best-effort: can't reach backend, but record locally.
                    let _ = task::fail_task(&client, &tid, "watchdog: offline (strict)").await;
                }
                tracing::warn!("Watchdog strict offline: pausing task loop for agent {agent_id}");
                tokio::time::sleep(POLL_INTERVAL).await;
                continue;
            }
            // SafeReadonly: pause claiming but don't actively fail tasks.
            (OfflinePolicy::SafeReadonly, ConnectivityState::Offline { .. }) => {
                tracing::info!("Watchdog safe-readonly offline: suspending claims for agent {agent_id}");
                tokio::time::sleep(POLL_INTERVAL).await;
                continue;
            }
            // Autonomous: continue until the TTL is exceeded, then act like Strict.
            (OfflinePolicy::Autonomous { max_ttl_secs }, ConnectivityState::Offline { since }) => {
                let elapsed = (chrono::Utc::now() - since).num_seconds().unsigned_abs();
                if elapsed > max_ttl_secs {
                    tracing::warn!(
                        "Watchdog autonomous TTL {max_ttl_secs}s exceeded for agent {agent_id}: stopping"
                    );
                    if let Some(tid) = current_task_id.take() {
                        let _ = task::fail_task(&client, &tid, "watchdog: autonomous TTL exceeded").await;
                    }
                    tokio::time::sleep(POLL_INTERVAL).await;
                    continue;
                }
                // Within TTL — fall through and keep running.
                tracing::debug!(
                    "Watchdog autonomous offline: {elapsed}s/{max_ttl_secs}s elapsed, continuing for {agent_id}"
                );
            }
            _ => {} // Connected or Degraded — proceed normally.
        }

        // Heartbeat the agent itself.
        if let Err(e) = client
            .raw_post(&format!("/work/agents/{agent_id}/heartbeat"), &serde_json::json!({}))
            .await
        {
            tracing::warn!("Agent heartbeat failed: {e}");
            watchdog.record_heartbeat_failure();
            tokio::time::sleep(POLL_INTERVAL).await;
            continue;
        }
        watchdog.record_heartbeat_success();

        // Get klusters for this mission.
        let klusters = match get_mission_klusters(&client, &mission_id).await {
            Ok(k) => k,
            Err(e) => {
                tracing::warn!("Could not list klusters for mission {mission_id}: {e}");
                tokio::time::sleep(POLL_INTERVAL).await;
                continue;
            }
        };

        // Try to claim a task from any kluster.
        let caps = runtime.capabilities().to_vec();
        let mut claimed: Option<mc_mesh_core::types::MeshTaskRecord> = None;
        for kluster_id in &klusters {
            match claim::try_claim_one(&client, kluster_id, &caps).await {
                Ok(Some(task_record)) => {
                    claimed = Some(task_record);
                    break;
                }
                Ok(None) => {}
                Err(e) => tracing::debug!("Claim attempt error in {kluster_id}: {e}"),
            }
        }

        let Some(task_record) = claimed else {
            tokio::time::sleep(POLL_INTERVAL).await;
            continue;
        };

        tracing::info!(
            "Agent {agent_id} claimed task {} ({})",
            task_record.id,
            task_record.title
        );
        current_task_id = Some(task_record.id.clone());

        // Update agent status to busy.
        let _ = client
            .raw_post(
                &format!("/work/agents/{agent_id}/status?status=busy"),
                &serde_json::json!({}),
            )
            .await;

        // Build the TaskSpec.
        let task_spec = TaskSpec {
            id: task_record.id.clone(),
            kluster_id: task_record.kluster_id.clone(),
            mission_id: mission_id.clone(),
            title: task_record.title.clone(),
            description: task_record.description.clone(),
            input_json: "{}".into(),
            required_capabilities: task_record.required_capabilities.clone(),
            produces: serde_json::Value::Object(Default::default()),
            consumes: serde_json::Value::Object(Default::default()),
        };

        // Inject and stream progress.
        let handle = agent.lock().await;
        let stream = match runtime.inject_task(&handle, &task_spec).await {
            Ok(s) => s,
            Err(e) => {
                tracing::error!("inject_task failed: {e}");
                let _ = task::fail_task(&client, &task_record.id, &e.to_string()).await;
                drop(handle);
                set_agent_idle(&client, &agent_id).await;
                continue;
            }
        };
        drop(handle);

        // Forward progress events with lease heartbeat.
        let result =
            stream_and_heartbeat(stream, &client, &task_record.id, &agent_id).await;

        match result {
            Ok(success) => {
                if success {
                    let _ = task::complete_task(&client, &task_record.id, None).await;
                } else {
                    let _ = task::fail_task(&client, &task_record.id, "agent reported failure").await;
                }
            }
            Err(e) => {
                tracing::error!("stream_and_heartbeat error: {e}");
                let _ = task::fail_task(&client, &task_record.id, &e.to_string()).await;
            }
        }

        current_task_id = None;
        set_agent_idle(&client, &agent_id).await;
    }
}

/// Forward a progress stream to the backend, heartbeating the lease in parallel.
async fn stream_and_heartbeat(
    mut stream: futures::stream::BoxStream<'static, mc_mesh_core::progress::ProgressEvent>,
    client: &BackendClient,
    task_id: &str,
    _agent_id: &str,
) -> Result<bool> {
    let mut last_heartbeat = std::time::Instant::now();
    let mut success = true;

    while let Some(event) = stream.next().await {
        // Check if this is a final error event.
        if event.event_type == mc_mesh_core::progress::ProgressEventType::Error {
            success = false;
        }

        if let Err(e) = task::post_progress(client, task_id, &event).await {
            tracing::warn!("Progress post failed: {e}");
        }

        // Heartbeat if overdue.
        if last_heartbeat.elapsed() >= HEARTBEAT_INTERVAL {
            if let Err(e) = task::heartbeat_task(client, task_id).await {
                tracing::warn!("Lease heartbeat failed: {e}");
            }
            last_heartbeat = std::time::Instant::now();
        }
    }

    Ok(success)
}

/// Get all kluster ids for a mission.
async fn get_mission_klusters(client: &BackendClient, mission_id: &str) -> Result<Vec<String>> {
    let resp: serde_json::Value = client
        .get(&format!("/missions/{mission_id}/k"))
        .await?;

    // Backend returns an array of kluster objects with an "id" field.
    let ids = resp
        .as_array()
        .unwrap_or(&vec![])
        .iter()
        .filter_map(|k| k.get("id").and_then(|v| v.as_str()).map(String::from))
        .collect();

    Ok(ids)
}

async fn set_agent_idle(client: &BackendClient, agent_id: &str) {
    let _ = client
        .raw_post(
            &format!("/work/agents/{agent_id}/status?status=idle"),
            &serde_json::json!({}),
        )
        .await;
}

/// Poll inbound messages for this agent and deliver them to the runtime.
///
/// Runs forever alongside the task claim loop.  Messages directed at this
/// agent (or broadcast messages in any of the agent's active klusters) are
/// fetched every MESSAGE_POLL_INTERVAL and forwarded via `AgentRuntime::signal()`.
async fn run_message_relay(
    agent: Arc<tokio::sync::Mutex<AgentHandle>>,
    runtime: Arc<mc_mesh_core::agent_runtime::DynAgentRuntime>,
    client: Arc<BackendClient>,
    agent_id: String,
) {
    // We poll the agent-scoped message inbox: GET /work/agents/{id}/messages
    // which returns messages where to_agent_id = agent_id or to_agent_id IS NULL.
    let mut last_id: i64 = 0;

    loop {
        tokio::time::sleep(MESSAGE_POLL_INTERVAL).await;

        let path = format!("/work/agents/{agent_id}/messages?since_id={last_id}");
        let msgs: Vec<serde_json::Value> = match client.get(&path).await {
            Ok(v) => v,
            Err(e) => {
                tracing::debug!("Message poll failed for agent {agent_id}: {e}");
                continue;
            }
        };

        for msg in msgs {
            // Track the highest seen id so we don't re-deliver.
            if let Some(id) = msg.get("id").and_then(|v| v.as_i64()) {
                if id > last_id {
                    last_id = id;
                }
            }

            let from_agent_id = msg
                .get("from_agent_id")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown")
                .to_string();
            let channel = msg
                .get("channel")
                .and_then(|v| v.as_str())
                .unwrap_or("coordination")
                .to_string();
            let body: serde_json::Value = msg
                .get("body_json")
                .and_then(|v| v.as_str())
                .and_then(|s| serde_json::from_str(s).ok())
                .unwrap_or_else(|| msg.get("body_json").cloned().unwrap_or(serde_json::json!({})));

            tracing::info!(
                "Agent {agent_id} received message from {from_agent_id} on channel {channel}"
            );

            let signal = AgentSignal::PeerMessage {
                from_agent_id,
                channel,
                body,
            };

            let handle = agent.lock().await;
            if let Err(e) = runtime.signal(&handle, signal).await {
                tracing::warn!("signal() delivery failed for agent {agent_id}: {e}");
            }
        }
    }
}
