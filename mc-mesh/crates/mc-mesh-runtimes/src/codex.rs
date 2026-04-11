/// AgentRuntime implementation for the OpenAI Codex CLI (`codex`).
///
/// Codex CLI does not emit structured JSON; it streams plain text to stdout.
/// We run `codex --approval-mode full-auto --quiet "<prompt>"` and map each
/// stdout line to a typed ProgressEvent. Exit code determines success/failure.
use anyhow::{anyhow, Result};
use async_trait::async_trait;
use futures::stream::BoxStream;
use mc_mesh_core::agent_runtime::AgentRuntime;
use mc_mesh_core::progress::{ProgressEvent, ProgressEventType};
use mc_mesh_core::types::{
    AgentHandle, AgentSignal, Capability, LaunchContext, PtySession, RuntimeKind, TaskResult,
    TaskSpec,
};
use std::io::{Read, Write};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::Command;

pub struct CodexRuntime {
    capabilities: Vec<Capability>,
    version: String,
}

impl CodexRuntime {
    pub fn new() -> Self {
        CodexRuntime {
            capabilities: vec![
                Capability::new("codex"),
                Capability::new("code.read"),
                Capability::new("code.edit"),
                Capability::new("code.refactor"),
                Capability::new("test.write"),
            ],
            version: detect_version(),
        }
    }
}

impl Default for CodexRuntime {
    fn default() -> Self {
        Self::new()
    }
}

fn detect_version() -> String {
    std::process::Command::new("codex")
        .arg("--version")
        .output()
        .ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|| "unknown".into())
}

fn build_prompt(task: &TaskSpec) -> String {
    if task.description.is_empty() {
        task.title.clone()
    } else {
        format!("{}\n\n{}", task.title, task.description)
    }
}

/// Heuristically classify a codex output line into a ProgressEvent.
///
/// Codex emits a mix of reasoning prose, shell command lines, and status
/// messages. We do lightweight pattern matching to surface useful structure
/// without being brittle about it.
fn classify_line(line: &str) -> Option<ProgressEvent> {
    let trimmed = line.trim();
    if trimmed.is_empty() {
        return None;
    }

    // Shell command invocations: lines starting with $ or `cmd`
    if trimmed.starts_with("$ ") || trimmed.starts_with("` ") {
        let cmd = trimmed.trim_start_matches("$ ").trim_start_matches("` ");
        return Some(ProgressEvent {
            event_type: ProgressEventType::StepStarted,
            phase: Some("running".into()),
            step: Some(format!("shell:{}", cmd.split_whitespace().next().unwrap_or("?"))),
            summary: format!("running: {}", truncate(cmd, 120)),
            payload: serde_json::json!({ "command": cmd }),
        });
    }

    // File write indicators
    if trimmed.starts_with("Writing ")
        || trimmed.starts_with("Created ")
        || trimmed.starts_with("Updated ")
        || trimmed.starts_with("Saved ")
    {
        return Some(ProgressEvent {
            event_type: ProgressEventType::StepFinished,
            phase: Some("running".into()),
            step: Some("file_write".into()),
            summary: truncate(trimmed, 160),
            payload: serde_json::json!({ "line": trimmed }),
        });
    }

    // Explicit error markers
    if trimmed.to_lowercase().starts_with("error:") || trimmed.to_lowercase().starts_with("fatal:") {
        return Some(ProgressEvent::error(
            truncate(trimmed, 200),
            serde_json::json!({ "line": trimmed }),
        ));
    }

    // Everything else is an Info event
    Some(ProgressEvent::info(truncate(trimmed, 200)))
}

fn truncate(s: &str, max: usize) -> String {
    if s.len() <= max {
        s.to_string()
    } else {
        format!("{}…", &s[..max])
    }
}

#[async_trait]
impl AgentRuntime for CodexRuntime {
    fn kind(&self) -> RuntimeKind {
        RuntimeKind::Codex
    }

    fn version(&self) -> &str {
        &self.version
    }

    fn capabilities(&self) -> &[Capability] {
        &self.capabilities
    }

    async fn launch(&self, ctx: LaunchContext) -> Result<AgentHandle> {
        std::fs::create_dir_all(&ctx.work_dir)?;

        let output = std::process::Command::new("codex")
            .arg("--version")
            .output();
        if output.is_err() {
            return Err(anyhow!(
                "codex CLI not found in PATH. Run `mc mesh runtime install codex`."
            ));
        }

        tracing::info!(
            "codex agent {} ready in {}",
            ctx.agent_id,
            ctx.work_dir.display()
        );

        Ok(AgentHandle {
            agent_id: ctx.agent_id,
            runtime_kind: RuntimeKind::Codex,
            pid: 0,
        })
    }

    async fn inject_task(
        &self,
        handle: &AgentHandle,
        task: &TaskSpec,
    ) -> Result<BoxStream<'static, ProgressEvent>> {
        let prompt = build_prompt(task);
        let task_id = task.id.clone();
        let agent_id = handle.agent_id.clone();

        let work_dir = dirs::home_dir()
            .unwrap_or_else(|| std::path::PathBuf::from("."))
            .join(".missioncontrol")
            .join("mc-mesh")
            .join("work")
            .join(&agent_id);

        std::fs::create_dir_all(&work_dir)?;

        tracing::info!(
            "codex injecting task {task_id}: {}",
            &prompt[..prompt.len().min(80)]
        );

        // `codex --approval-mode full-auto` runs non-interactively.
        // `--quiet` suppresses the spinner/ANSI chrome so we get clean lines.
        let mut child = Command::new("codex")
            .arg("--approval-mode")
            .arg("full-auto")
            .arg("--quiet")
            .arg(&prompt)
            .current_dir(&work_dir)
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()?;

        let stdout = child.stdout.take().ok_or_else(|| anyhow!("no stdout"))?;
        let stderr = child.stderr.take().ok_or_else(|| anyhow!("no stderr"))?;

        let stream = async_stream::stream! {
            yield ProgressEvent::phase_started("running", "codex session started");

            let mut stdout_lines = BufReader::new(stdout).lines();
            let mut stderr_lines = BufReader::new(stderr).lines();
            let mut done = false;

            while !done {
                tokio::select! {
                    line = stdout_lines.next_line() => {
                        match line {
                            Ok(Some(l)) => {
                                if let Some(ev) = classify_line(&l) {
                                    yield ev;
                                }
                            }
                            Ok(None) => { done = true; }
                            Err(e) => {
                                yield ProgressEvent::error(
                                    "stdout read error",
                                    serde_json::json!({ "error": e.to_string() }),
                                );
                                done = true;
                            }
                        }
                    }
                    line = stderr_lines.next_line() => {
                        if let Ok(Some(l)) = line {
                            let trimmed = l.trim();
                            if !trimmed.is_empty() {
                                yield ProgressEvent {
                                    event_type: ProgressEventType::Warning,
                                    phase: Some("running".into()),
                                    step: None,
                                    summary: truncate(trimmed, 200),
                                    payload: serde_json::json!({ "stderr": trimmed }),
                                };
                            }
                        }
                    }
                }
            }

            match child.wait().await {
                Ok(status) if status.success() => {
                    yield ProgressEvent {
                        event_type: ProgressEventType::PhaseFinished,
                        phase: Some("running".into()),
                        step: None,
                        summary: "codex task finished".into(),
                        payload: serde_json::json!({ "exit_code": 0 }),
                    };
                }
                Ok(status) => {
                    yield ProgressEvent::error(
                        format!("codex exited with {status}"),
                        serde_json::json!({ "exit_code": status.code() }),
                    );
                }
                Err(e) => {
                    yield ProgressEvent::error(
                        format!("failed to wait on codex: {e}"),
                        serde_json::json!({}),
                    );
                }
            }
        };

        Ok(Box::pin(stream))
    }

    async fn signal(&self, handle: &AgentHandle, signal: AgentSignal) -> Result<()> {
        tracing::info!("Signal to codex agent {}: {:?}", handle.agent_id, signal);
        Ok(())
    }

    async fn collect_result(&self, handle: &AgentHandle) -> Result<TaskResult> {
        Ok(TaskResult {
            task_id: handle.agent_id.clone(),
            success: true,
            exit_code: 0,
            artifact_path: None,
            summary: "codex task finished".into(),
        })
    }

    async fn attach_pty(&self, handle: &AgentHandle) -> Result<PtySession> {
        use portable_pty::{CommandBuilder, PtySize, native_pty_system};

        let work_dir = dirs::home_dir()
            .unwrap_or_else(|| std::path::PathBuf::from("."))
            .join(".missioncontrol")
            .join("mc-mesh")
            .join("work")
            .join(&handle.agent_id);
        std::fs::create_dir_all(&work_dir)?;

        let pty_system = native_pty_system();
        let pair = pty_system.openpty(PtySize { rows: 24, cols: 80, pixel_width: 0, pixel_height: 0 })?;

        let mut cmd = CommandBuilder::new("codex");
        cmd.cwd(&work_dir);
        let _child = pair.slave.spawn_command(cmd)?;
        drop(pair.slave);

        let mut master_reader = pair.master.try_clone_reader()?;
        let mut master_writer = pair.master.take_writer()?;

        let (out_tx, out_rx) = tokio::sync::mpsc::channel::<Vec<u8>>(256);
        let (in_tx, mut in_rx) = tokio::sync::mpsc::channel::<Vec<u8>>(256);

        tokio::task::spawn_blocking(move || {
            let mut buf = [0u8; 4096];
            loop {
                match master_reader.read(&mut buf) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => { if out_tx.blocking_send(buf[..n].to_vec()).is_err() { break; } }
                }
            }
        });
        tokio::task::spawn_blocking(move || {
            loop {
                match in_rx.blocking_recv() {
                    None => break,
                    Some(bytes) => { if master_writer.write_all(&bytes).is_err() { break; } }
                }
            }
        });

        tracing::info!("PTY session opened for codex agent {}", handle.agent_id);
        Ok(PtySession { output: out_rx, input: in_tx, rows: 24, cols: 80 })
    }

    async fn shutdown(&self, handle: AgentHandle) -> Result<()> {
        tracing::info!("Shutting down codex agent {}", handle.agent_id);
        Ok(())
    }
}
