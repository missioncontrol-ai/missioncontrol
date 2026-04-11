/// Utilities shared across all AgentRuntime implementations.
use anyhow::Result;
use mc_mesh_core::types::TaskSpec;
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

/// Build a rich prompt for a task, injecting agent profile and mission roster.
///
/// Structure:
///   [AGENT CONTEXT]  — who this agent is, its role, instructions, scope, constraints
///   [MISSION ROSTER] — other agents in the pool (for delegation decisions)
///   [TASK]           — the actual work to do
///
/// Any section is omitted if the data isn't available.
pub fn build_prompt(task: &TaskSpec) -> String {
    let mut parts: Vec<String> = vec![];

    // --- Agent context ---
    if let Some(profile) = &task.agent_profile {
        let mut ctx = vec!["[AGENT CONTEXT]".to_string()];

        if let Some(name) = profile.get("name").and_then(|v| v.as_str()) {
            if let Some(role) = profile.get("role").and_then(|v| v.as_str()) {
                ctx.push(format!("You are {name}, {role}."));
            } else {
                ctx.push(format!("Your name is {name}."));
            }
        }
        if let Some(desc) = profile.get("description").and_then(|v| v.as_str()) {
            ctx.push(desc.to_string());
        }
        if let Some(instructions) = profile.get("instructions").and_then(|v| v.as_str()) {
            ctx.push(instructions.trim().to_string());
        }
        if let Some(scope) = profile.get("scope") {
            let dirs = scope
                .get("directories")
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|v| v.as_str())
                        .collect::<Vec<_>>()
                        .join(", ")
                })
                .filter(|s| !s.is_empty());
            if let Some(d) = dirs {
                ctx.push(format!("Your scope: {d}"));
            }
            if scope.get("read_only").and_then(|v| v.as_bool()) == Some(true) {
                ctx.push("You have read-only access — do not write or edit files.".into());
            }
        }
        if let Some(constraints) = profile.get("constraints").and_then(|v| v.as_array()) {
            if !constraints.is_empty() {
                ctx.push("Constraints:".to_string());
                for c in constraints {
                    let line = if let Some(s) = c.as_str() {
                        format!("  - {s}")
                    } else if let Some(desc) = c.get("description").and_then(|v| v.as_str()) {
                        format!("  - {desc}")
                    } else {
                        continue;
                    };
                    ctx.push(line);
                }
            }
        }

        parts.push(ctx.join("\n"));
    }

    // --- Mission roster ---
    if !task.mission_roster.is_empty() {
        let mut roster = vec!["[MISSION ROSTER]".to_string()];
        roster.push(
            "Other agents in this mission (you can create tasks for them or message them):"
                .to_string(),
        );
        for agent in &task.mission_roster {
            let id = agent.get("id").and_then(|v| v.as_str()).unwrap_or("?");
            let name = agent.get("name").and_then(|v| v.as_str()).unwrap_or(
                agent
                    .get("runtime_kind")
                    .and_then(|v| v.as_str())
                    .unwrap_or("unknown"),
            );
            let role = agent.get("role").and_then(|v| v.as_str());
            let status = agent.get("status").and_then(|v| v.as_str()).unwrap_or("unknown");
            let caps: Vec<&str> = agent
                .get("capabilities")
                .and_then(|v| v.as_array())
                .map(|arr| arr.iter().filter_map(|v| v.as_str()).collect())
                .unwrap_or_default();

            let role_str = role.map(|r| format!(", {r}")).unwrap_or_default();
            let caps_str = if caps.is_empty() {
                String::new()
            } else {
                format!(", caps: {}", caps.join("/"))
            };
            roster.push(format!(
                "  - {name} (id: {id}{role_str}, status: {status}{caps_str})"
            ));

            if let Some(scope) = agent.get("scope") {
                let dirs = scope
                    .get("directories")
                    .and_then(|v| v.as_array())
                    .map(|arr| {
                        arr.iter()
                            .filter_map(|v| v.as_str())
                            .collect::<Vec<_>>()
                            .join(", ")
                    })
                    .filter(|s| !s.is_empty());
                if let Some(d) = dirs {
                    roster.push(format!("    scope: {d}"));
                }
            }
        }
        roster.push(
            "To delegate: POST /work/klusters/{kluster_id}/tasks with claim_policy=assigned and claimed_by_agent_id set, or send a message via POST /work/klusters/{kluster_id}/messages.".to_string()
        );
        parts.push(roster.join("\n"));
    }

    // --- Task ---
    let mut task_section = vec!["[TASK]".to_string()];
    task_section.push(task.title.clone());
    if !task.description.is_empty() {
        task_section.push(String::new());
        task_section.push(task.description.clone());
    }
    parts.push(task_section.join("\n"));

    parts.join("\n\n")
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
