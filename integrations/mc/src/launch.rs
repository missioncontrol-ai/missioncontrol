//! `mc launch` — one-shot agent launcher.
//!
//! Ensures the daemon is running, validates auth, generates and installs agent
//! config, then exec's the agent binary. Adding a new agent type requires only
//! a new `AgentDriver` impl + a `AgentKind` variant — no changes to `run()`.
//!
//! ## Token embedding
//!
//! By default `mc launch` embeds the `MC_TOKEN` value into the agent's config
//! file. This is convenient for static long-lived tokens.
//!
//! For OIDC / short-lived JWTs, use `--no-embed-token`: the token is omitted
//! from the written config and the agent process inherits `MC_TOKEN` from the
//! shell environment at exec time. The MCP shim always prefers the env var over
//! any embedded value, so the agent will pick up refreshed tokens automatically
//! on every launch.
//!
//! Auto-detection: if `MC_TOKEN` / `config.token` is absent, `--no-embed-token`
//! is implied and a notice is printed.

use crate::{
    auth,
    client::MissionControlClient,
    config::{mc_home_dir, McConfig},
    mc_info, mc_ok, mc_warn, ui,
};
use anyhow::{anyhow, Context, Result};
use clap::{Args, ValueEnum};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs;
#[cfg(unix)]
use std::os::unix::fs as unix_fs;
use std::{
    io::{self, Write},
    path::{Path, PathBuf},
};
use uuid::Uuid;

const CODEX_APPROVAL_RULES: &[&str] = &[
    // Primary allowlist: approve the entire Mission Control CLI tree.
    r#"prefix_rule(pattern=["mc"], decision="allow")"#,
    // Common absolute invocation path on developer workstations.
    r#"prefix_rule(pattern=["/home/merlin/.local/bin/mc"], decision="allow")"#,
    // Shell-wrapper invocation forms used by Codex runtimes.
    r#"prefix_rule(pattern=["zsh", "-lc", "mc"], decision="allow")"#,
    r#"prefix_rule(pattern=["/usr/bin/zsh", "-lc", "mc"], decision="allow")"#,
    r#"prefix_rule(pattern=["mc", "whoami"], decision="allow")"#,
    r#"prefix_rule(pattern=["mc", "auth"], decision="allow")"#,
    r#"prefix_rule(pattern=["mc", "data"], decision="allow")"#,
    r#"prefix_rule(pattern=["mc", "data", "tools", "call"], decision="allow")"#,
    r#"prefix_rule(pattern=["mc", "data", "tools"], decision="allow")"#,
    r#"prefix_rule(pattern=["mc", "data", "sync"], decision="allow")"#,
    r#"prefix_rule(pattern=["mc", "data", "explorer"], decision="allow")"#,
    r#"prefix_rule(pattern=["mc", "admin"], decision="allow")"#,
    r#"prefix_rule(pattern=["mc", "system"], decision="allow")"#,
    r#"prefix_rule(pattern=["mc", "agent"], decision="allow")"#,
    // Legacy compatibility: older assistants may still emit pre-cutover paths.
    r#"prefix_rule(pattern=["mc", "explorer"], decision="allow")"#,
    r#"prefix_rule(pattern=["mc", "tools"], decision="allow")"#,
    r#"prefix_rule(pattern=["mc", "profile"], decision="allow")"#,
    r#"prefix_rule(pattern=["mc", "approvals"], decision="allow")"#,
    r#"prefix_rule(pattern=["mc", "workspace"], decision="allow")"#,
    r#"prefix_rule(pattern=["mc", "tools", "call"], decision="allow")"#,
];

// ── CLI args ────────────────────────────────────────────────────────────────

#[derive(Args, Debug)]
pub struct LaunchArgs {
    /// Agent to launch: codex, claude, gemini, openclaw, custom, resume
    agent: Option<AgentKind>,

    /// No-op (daemon is no longer started by mc launch; kept for backwards compat)
    #[arg(long)]
    no_daemon: bool,

    /// Run preflights only; do not launch agent (useful for CI)
    #[arg(long)]
    preflight_only: bool,

    /// Skip config generation (use existing ~/.missioncontrol/config/)
    #[arg(long)]
    skip_config_gen: bool,

    /// Profile name (research, dev, security, etc). Defaults to active/default profile.
    #[arg(long)]
    profile: Option<String>,

    /// Resume the most recent launch session for the selected agent/profile.
    #[arg(long)]
    resume: bool,

    /// Resume a specific runtime session id.
    #[arg(long)]
    session_id: Option<String>,

    /// Force starting a new runtime session (default when not resuming).
    #[arg(long)]
    new_session: bool,

    /// Write agent config to global locations (~/.codex, ~/.claude.json, ~/.gemini)
    /// instead of the instance-local runtime home. Compatibility escape hatch only.
    #[arg(long)]
    legacy_global_config: bool,

    /// Allow launching when local profile pin does not match remote profile sha.
    #[arg(long)]
    allow_pin_mismatch: bool,

    /// Do not embed MC_TOKEN in the written agent config.
    ///
    /// Use this for OIDC / short-lived tokens: the token is inherited from the
    /// shell environment at agent exec time instead of being written to disk.
    /// Automatically implied when MC_TOKEN is absent.
    #[arg(long)]
    no_embed_token: bool,

    /// Extra args forwarded verbatim to the agent binary (after --)
    #[arg(last = true)]
    agent_args: Vec<String>,
}

#[derive(ValueEnum, Debug, Clone)]
enum AgentKind {
    Codex,
    Claude,
    Gemini,
    Openclaw,
    Custom,
    Resume,
}

impl AgentKind {
    fn driver(&self) -> Box<dyn AgentDriver> {
        match self {
            AgentKind::Codex => Box::new(CodexDriver),
            AgentKind::Claude => Box::new(ClaudeDriver),
            AgentKind::Gemini => Box::new(GeminiDriver),
            AgentKind::Openclaw => Box::new(OpenClawDriver),
            AgentKind::Custom => Box::new(CustomDriver),
            AgentKind::Resume => Box::new(CodexDriver),
        }
    }

    fn config_key(&self) -> &str {
        match self {
            AgentKind::Codex => "codex",
            AgentKind::Claude => "claude",
            AgentKind::Gemini => "gemini",
            AgentKind::Openclaw => "openclaw",
            AgentKind::Custom => "custom",
            AgentKind::Resume => "resume",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct LaunchSessionRecord {
    pub(crate) runtime_session_id: String,
    pub(crate) agent: String,
    pub(crate) profile: String,
    pub(crate) instance_home: String,
    pub(crate) created_at: String,
}

// ── AgentDriver trait ────────────────────────────────────────────────────────

trait AgentDriver {
    /// Agent binary name (looked up on PATH).
    fn binary(&self) -> &str;
    /// Human-readable install hint shown when binary is missing.
    fn install_hint(&self) -> &str;
    /// Write rendered config to the agent's canonical location.
    ///
    /// `embed_token`: when false, omit `MC_TOKEN` from the written config
    /// entirely; the agent process must inherit it from the environment.
    fn install_config(
        &self,
        staging_dir: &Path,
        base_url: &str,
        token: &str,
        embed_token: bool,
        target_home: &Path,
        target_mc_home: &Path,
    ) -> Result<()>;
    /// Build the Command to exec (binary + required flags).
    fn command(&self, extra_args: &[String], target_mc_home: &Path) -> std::process::Command;
}

// ── CodexDriver ──────────────────────────────────────────────────────────────

struct CodexDriver;

/// Sentinel comment that guards the MCP stanza for idempotency.
const CODEX_MC_MARKER: &str = "# mc-launch: missioncontrol";

impl AgentDriver for CodexDriver {
    fn binary(&self) -> &str {
        "codex"
    }

    fn install_hint(&self) -> &str {
        "npm install -g @openai/codex"
    }

    fn install_config(
        &self,
        _staging_dir: &Path,
        base_url: &str,
        token: &str,
        embed_token: bool,
        target_home: &Path,
        _target_mc_home: &Path,
    ) -> Result<()> {
        let config_path = target_home.join(".codex").join("config.toml");

        if let Some(parent) = config_path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        let existing = if config_path.exists() {
            std::fs::read_to_string(&config_path)?
        } else {
            String::new()
        };

        let new_stanza = render_codex_stanza(base_url, token, embed_token);

        // Detect existing missioncontrol section: either via our marker comment
        // or the raw TOML key (handles configs written before the marker existed).
        let has_marker = existing.contains(CODEX_MC_MARKER);
        let has_key = existing.contains("[mcp_servers.missioncontrol]");

        if has_marker || has_key {
            // Extract current section and compare to what we'd write.
            let current_section = extract_codex_mc_section(&existing);
            if current_section.trim() == new_stanza.trim() {
                mc_ok!("codex MCP config is up to date");
                return Ok(());
            }
            // Config differs — prompt user to replace.
            eprint!(
                "{}⚑{} [mcp_servers.missioncontrol] differs from current template. Replace? [y/N] ",
                crate::ui::YELLOW,
                crate::ui::RESET
            );
            std::io::stderr().flush()?;
            let mut answer = String::new();
            std::io::stdin().read_line(&mut answer)?;
            if !answer.trim().eq_ignore_ascii_case("y") {
                mc_info!("keeping existing codex MCP config");
                return Ok(());
            }
            // Remove the existing section (and its marker comment if present).
            let cleaned = remove_codex_mc_section(&existing);
            std::fs::write(&config_path, &cleaned)?;
        }

        let current = std::fs::read_to_string(&config_path).unwrap_or_default();
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&config_path)?;

        if !current.is_empty() && !current.ends_with('\n') {
            writeln!(file)?;
        }
        writeln!(file)?;
        write!(file, "{}", new_stanza)?;
        mc_ok!("codex MCP config written → {}", config_path.display());
        Ok(())
    }

    fn command(&self, extra_args: &[String], _target_mc_home: &Path) -> std::process::Command {
        let mut cmd = std::process::Command::new("codex");
        cmd.args(extra_args);
        cmd
    }
}

fn render_codex_stanza(base_url: &str, token: &str, embed_token: bool) -> String {
    let tmpl = include_str!("../../../distribution/templates/codex.mcp.toml.tmpl");
    let rendered = tmpl.replace("__BASE_URL__", base_url);
    // For the TOML inline table the token entry is `, MC_TOKEN = "__TOKEN__"`.
    // When not embedding, strip it entirely so the agent reads from env.
    let rendered = if embed_token {
        rendered.replace("__TOKEN__", token)
    } else {
        rendered.replace(", MC_TOKEN = \"__TOKEN__\"", "")
    };
    format!("{}\n{}\n", CODEX_MC_MARKER, rendered)
}

/// Extract the missioncontrol MCP section lines from a codex config.toml string
/// (including the marker comment if present), for comparison purposes.
fn extract_codex_mc_section(content: &str) -> String {
    let mut out = Vec::new();
    let mut in_mc_section = false;

    for line in content.lines() {
        let trimmed = line.trim();

        if trimmed == CODEX_MC_MARKER || trimmed == "[mcp_servers.missioncontrol]" {
            in_mc_section = true;
        }

        if in_mc_section {
            if trimmed.starts_with('[')
                && trimmed != CODEX_MC_MARKER
                && !trimmed.starts_with("[mcp_servers.missioncontrol")
            {
                break;
            }
            out.push(line);
        }
    }

    out.join("\n")
}

/// Remove all lines belonging to the missioncontrol MCP section from a codex
/// config.toml string. Handles both marker-prefixed stanzas (written by mc
/// launch) and bare `[mcp_servers.missioncontrol]` sections written by hand or
/// older tool versions.
///
/// The section is considered to end at the next `[` header or EOF.
fn remove_codex_mc_section(content: &str) -> String {
    let mut out = Vec::new();
    let mut in_mc_section = false;

    for line in content.lines() {
        let trimmed = line.trim();

        // Start of our section: the marker comment or the TOML key itself.
        if trimmed == CODEX_MC_MARKER || trimmed == "[mcp_servers.missioncontrol]" {
            in_mc_section = true;
            continue;
        }

        if in_mc_section {
            // A new section header ends the missioncontrol section.
            if trimmed.starts_with('[') {
                in_mc_section = false;
            } else {
                continue; // drop lines inside the old section
            }
        }

        out.push(line);
    }

    // Trim trailing blank lines left behind, then ensure single trailing newline.
    let joined = out.join("\n");
    let trimmed_end = joined.trim_end_matches('\n');
    if trimmed_end.is_empty() {
        String::new()
    } else {
        format!("{}\n", trimmed_end)
    }
}

// ── ClaudeDriver ─────────────────────────────────────────────────────────────

struct ClaudeDriver;

impl AgentDriver for ClaudeDriver {
    fn binary(&self) -> &str {
        "claude"
    }

    fn install_hint(&self) -> &str {
        "npm install -g @anthropic-ai/claude-code"
    }

    fn install_config(
        &self,
        _staging_dir: &Path,
        base_url: &str,
        token: &str,
        embed_token: bool,
        target_home: &Path,
        _target_mc_home: &Path,
    ) -> Result<()> {
        let mc_entry = render_json_mcp_entry(
            include_str!("../../../distribution/templates/claude.mcp.json.tmpl"),
            "embedded claude template",
            base_url,
            token,
            embed_token,
        );
        let mc_entry = absolutize_mc_command(mc_entry);
        let config_path = target_home.join(".claude.json");
        write_json_missioncontrol_entry(&config_path, mc_entry.clone())?;
        mc_ok!("claude MCP config written → {}", config_path.display());

        // Inject MC lifecycle hooks (profile-update, session registration, audit) into settings.json.
        let settings_path = target_home.join(".claude").join("settings.json");
        if let Err(e) = inject_mc_lifecycle_hooks(&settings_path, base_url) {
            mc_warn!("could not inject MC lifecycle hooks: {}", e);
        }

        // Write hook shell scripts into the instance home.
        if let Err(e) = write_hook_scripts(target_home) {
            mc_warn!("could not write hook scripts: {}", e);
        }

        if let Some(global_home) = dirs::home_dir() {
            let global_path = global_home.join(".claude.json");
            if global_path != config_path {
                write_json_missioncontrol_entry(&global_path, mc_entry)?;
                mc_info!(
                    "claude global MCP config updated → {}",
                    global_path.display()
                );
            }
        }

        // Claude Code detects its install method by looking for itself at
        // $HOME/.local/bin/claude. When HOME is set to the isolated instance
        // home, this path doesn't exist and Claude errors with
        // "installMethod is native, but claude command not found".
        // Create a symlink so Claude can find itself in the instance home.
        if let Ok(real_claude) = which_binary("claude") {
            let local_bin = target_home.join(".local").join("bin");
            std::fs::create_dir_all(&local_bin)?;
            let claude_link = local_bin.join("claude");
            if !claude_link.exists() {
                #[cfg(unix)]
                unix_fs::symlink(&real_claude, &claude_link).with_context(|| {
                    format!(
                        "failed to symlink claude into instance home: {} -> {}",
                        claude_link.display(),
                        real_claude.display()
                    )
                })?;
                #[cfg(not(unix))]
                std::fs::copy(&real_claude, &claude_link)?;
                mc_info!("claude self-link → {}", claude_link.display());
            }
        }

        Ok(())
    }

    fn command(&self, extra_args: &[String], _target_mc_home: &Path) -> std::process::Command {
        let mut cmd = std::process::Command::new("claude");
        cmd.args(extra_args);
        cmd
    }
}

/// Inject all MC lifecycle hooks into the Claude Code settings.json.
///
/// Injects:
/// - UserPromptSubmit: emit profile-updated marker (existing behaviour)
/// - SessionStart (startup/resume): HTTP POST to /hooks/claude/session-start
/// - SessionStart (compact): re-inject mission context via shell script
/// - SessionEnd: HTTP POST to /hooks/claude/session-end
/// - PostToolUse (mcp__missioncontrol__.*): HTTP POST to /hooks/claude/tool-audit
/// - PreCompact: dump current context summary to stdout
///
/// Idempotent — safe to call on every launch.
fn inject_mc_lifecycle_hooks(settings_path: &Path, backend_url: &str) -> Result<()> {
    let mut root: Value = if settings_path.exists() {
        let content = fs::read_to_string(settings_path)?;
        serde_json::from_str(&content).unwrap_or_else(|_| json!({}))
    } else {
        json!({})
    };

    let hooks_obj = root
        .as_object_mut()
        .ok_or_else(|| anyhow!("settings.json is not an object"))?
        .entry("hooks")
        .or_insert_with(|| json!({}))
        .as_object_mut()
        .ok_or_else(|| anyhow!("hooks is not an object"))?
        .clone();

    // We'll rebuild the hooks object completely from the current state.
    let mut hooks_map = hooks_obj;

    // ── UserPromptSubmit: profile-update marker (existing) ────────────────
    {
        let ups = hooks_map
            .entry("UserPromptSubmit".to_string())
            .or_insert_with(|| json!([]));
        let arr = ups
            .as_array_mut()
            .ok_or_else(|| anyhow!("UserPromptSubmit is not an array"))?;

        let already = arr.iter().any(|h| {
            h.get("hooks")
                .and_then(|v| v.as_array())
                .and_then(|a| a.first())
                .and_then(|h| h.get("command"))
                .and_then(|v| v.as_str())
                .map(|s| s.contains("profile-updated"))
                .unwrap_or(false)
        });
        if !already {
            let cmd = concat!(
                "sh -c '",
                r#"f="${MC_INSTANCE_HOME}/mc/profile-updated"; "#,
                r#"[ -f "$f" ] && cat "$f" && rm -f "$f"; "#,
                "exit 0'"
            );
            arr.push(json!({
                "matcher": "",
                "hooks": [{"type": "command", "command": cmd}]
            }));
        }
    }

    // ── SessionStart: HTTP registration + compact context re-injection ────
    {
        let session_start = hooks_map
            .entry("SessionStart".to_string())
            .or_insert_with(|| json!([]));
        let arr = session_start
            .as_array_mut()
            .ok_or_else(|| anyhow!("SessionStart is not an array"))?;

        let already_http = arr.iter().any(|h| {
            h.get("hooks")
                .and_then(|v| v.as_array())
                .and_then(|a| a.first())
                .and_then(|h| h.get("url"))
                .and_then(|v| v.as_str())
                .map(|s| s.contains("/hooks/claude/session-start"))
                .unwrap_or(false)
        });
        if !already_http {
            let url = format!("{}/hooks/claude/session-start", backend_url);
            arr.push(json!({
                "matcher": "startup|resume",
                "hooks": [{
                    "type": "http",
                    "url": url,
                    "headers": {"Authorization": "Bearer $MC_AGENT_TOKEN"},
                    "allowedEnvVars": ["MC_AGENT_TOKEN"],
                    "timeout": 10
                }]
            }));
        }

        let already_compact = arr.iter().any(|h| {
            h.get("hooks")
                .and_then(|v| v.as_array())
                .and_then(|a| a.first())
                .and_then(|h| h.get("command"))
                .and_then(|v| v.as_str())
                .map(|s| s.contains("mc-recompact-context.sh"))
                .unwrap_or(false)
        });
        if !already_compact {
            arr.push(json!({
                "matcher": "compact",
                "hooks": [{
                    "type": "command",
                    "command": "\"${MC_INSTANCE_HOME}\"/.claude/hooks/mc-recompact-context.sh"
                }]
            }));
        }
    }

    // ── SessionEnd: HTTP close ────────────────────────────────────────────
    {
        let session_end = hooks_map
            .entry("SessionEnd".to_string())
            .or_insert_with(|| json!([]));
        let arr = session_end
            .as_array_mut()
            .ok_or_else(|| anyhow!("SessionEnd is not an array"))?;

        let already = arr.iter().any(|h| {
            h.get("hooks")
                .and_then(|v| v.as_array())
                .and_then(|a| a.first())
                .and_then(|h| h.get("url"))
                .and_then(|v| v.as_str())
                .map(|s| s.contains("/hooks/claude/session-end"))
                .unwrap_or(false)
        });
        if !already {
            let url = format!("{}/hooks/claude/session-end", backend_url);
            arr.push(json!({
                "hooks": [{
                    "type": "http",
                    "url": url,
                    "headers": {"Authorization": "Bearer $MC_AGENT_TOKEN"},
                    "allowedEnvVars": ["MC_AGENT_TOKEN"],
                    "timeout": 10
                }]
            }));
        }
    }

    // ── PostToolUse: audit MCP tool calls ────────────────────────────────
    {
        let post_tool = hooks_map
            .entry("PostToolUse".to_string())
            .or_insert_with(|| json!([]));
        let arr = post_tool
            .as_array_mut()
            .ok_or_else(|| anyhow!("PostToolUse is not an array"))?;

        let already = arr.iter().any(|h| {
            h.get("hooks")
                .and_then(|v| v.as_array())
                .and_then(|a| a.first())
                .and_then(|h| h.get("url"))
                .and_then(|v| v.as_str())
                .map(|s| s.contains("/hooks/claude/tool-audit"))
                .unwrap_or(false)
        });
        if !already {
            let url = format!("{}/hooks/claude/tool-audit", backend_url);
            arr.push(json!({
                "matcher": "mcp__missioncontrol__.*",
                "hooks": [{
                    "type": "http",
                    "url": url,
                    "headers": {"Authorization": "Bearer $MC_AGENT_TOKEN"},
                    "allowedEnvVars": ["MC_AGENT_TOKEN"],
                    "timeout": 5
                }]
            }));
        }
    }

    // ── PreCompact: dump context summary ─────────────────────────────────
    {
        let pre_compact = hooks_map
            .entry("PreCompact".to_string())
            .or_insert_with(|| json!([]));
        let arr = pre_compact
            .as_array_mut()
            .ok_or_else(|| anyhow!("PreCompact is not an array"))?;

        let already = arr.iter().any(|h| {
            h.get("hooks")
                .and_then(|v| v.as_array())
                .and_then(|a| a.first())
                .and_then(|h| h.get("command"))
                .and_then(|v| v.as_str())
                .map(|s| s.contains("mc-precompact.sh"))
                .unwrap_or(false)
        });
        if !already {
            arr.push(json!({
                "hooks": [{
                    "type": "command",
                    "command": "\"${MC_INSTANCE_HOME}\"/.claude/hooks/mc-precompact.sh"
                }]
            }));
        }
    }

    // Write back.
    root.as_object_mut().unwrap().insert(
        "hooks".to_string(),
        Value::Object(hooks_map.into_iter().collect()),
    );

    if let Some(parent) = settings_path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(settings_path, serde_json::to_string_pretty(&root)?)?;
    Ok(())
}

/// Write the MC hook shell scripts into `<target_home>/.claude/hooks/`.
/// Scripts are embedded at compile time from `distribution/hooks/`.
fn write_hook_scripts(target_home: &Path) -> Result<()> {
    const PRECOMPACT: &str = include_str!("../../../distribution/hooks/mc-precompact.sh");
    const RECOMPACT: &str = include_str!("../../../distribution/hooks/mc-recompact-context.sh");

    let hooks_dir = target_home.join(".claude").join("hooks");
    fs::create_dir_all(&hooks_dir)?;

    let scripts: &[(&str, &str)] = &[
        ("mc-precompact.sh", PRECOMPACT),
        ("mc-recompact-context.sh", RECOMPACT),
    ];

    for (name, content) in scripts {
        let path = hooks_dir.join(name);
        fs::write(&path, content)?;
        // Make executable on Unix.
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = fs::metadata(&path)?.permissions();
            perms.set_mode(0o755);
            fs::set_permissions(&path, perms)?;
        }
        mc_info!("hook script written → {}", path.display());
    }

    Ok(())
}

fn write_json_missioncontrol_entry(config_path: &Path, mc_entry: serde_json::Value) -> Result<()> {
    let mut root: serde_json::Value = if config_path.exists() {
        let content = std::fs::read_to_string(config_path)?;
        serde_json::from_str(&content)
            .unwrap_or_else(|_| serde_json::Value::Object(Default::default()))
    } else {
        serde_json::Value::Object(Default::default())
    };
    root.as_object_mut()
        .ok_or_else(|| anyhow!("{} is not a JSON object", config_path.display()))?
        .entry("mcpServers")
        .or_insert_with(|| serde_json::Value::Object(Default::default()))
        .as_object_mut()
        .ok_or_else(|| anyhow!("{} mcpServers is not an object", config_path.display()))?
        .insert("missioncontrol".to_string(), mc_entry);
    std::fs::write(config_path, serde_json::to_string_pretty(&root)?)?;
    Ok(())
}

// ── GeminiDriver ─────────────────────────────────────────────────────────────

struct GeminiDriver;

impl AgentDriver for GeminiDriver {
    fn binary(&self) -> &str {
        "gemini"
    }

    fn install_hint(&self) -> &str {
        "npm install -g @google/gemini-cli"
    }

    fn install_config(
        &self,
        _staging_dir: &Path,
        base_url: &str,
        token: &str,
        embed_token: bool,
        target_home: &Path,
        _target_mc_home: &Path,
    ) -> Result<()> {
        let config_path = target_home.join(".gemini").join("settings.json");

        if let Some(parent) = config_path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        let mut root: serde_json::Value = if config_path.exists() {
            let content = std::fs::read_to_string(&config_path)?;
            serde_json::from_str(&content)
                .unwrap_or_else(|_| serde_json::Value::Object(Default::default()))
        } else {
            serde_json::Value::Object(Default::default())
        };

        let mc_entry = render_json_mcp_entry(
            include_str!("../../../distribution/templates/gemini.mcp.json.tmpl"),
            "embedded gemini template",
            base_url,
            token,
            embed_token,
        );
        let mc_entry = absolutize_mc_command(mc_entry);

        root.as_object_mut()
            .ok_or_else(|| anyhow!("~/.gemini/settings.json is not a JSON object"))?
            .entry("mcpServers")
            .or_insert_with(|| serde_json::Value::Object(Default::default()))
            .as_object_mut()
            .ok_or_else(|| anyhow!("~/.gemini/settings.json mcpServers is not an object"))?
            .insert("missioncontrol".to_string(), mc_entry);

        std::fs::write(&config_path, serde_json::to_string_pretty(&root)?)?;
        mc_ok!("gemini MCP config written → {}", config_path.display());
        Ok(())
    }

    fn command(&self, extra_args: &[String], _target_mc_home: &Path) -> std::process::Command {
        let mut cmd = std::process::Command::new("gemini");
        cmd.args(extra_args);
        cmd
    }
}

// ── Shared JSON render helper ─────────────────────────────────────────────────

/// Render the `mcpServers.missioncontrol` entry from a template.
///
/// Replaces `__BASE_URL__` and (if `embed_token`) `__TOKEN__` in the template,
/// then returns the inner `mcpServers.missioncontrol` object.  When
/// `embed_token` is false the `MC_TOKEN` key is removed from the `env` map.
fn render_json_mcp_entry(
    tmpl: &str,
    tmpl_name: &str,
    base_url: &str,
    token: &str,
    embed_token: bool,
) -> serde_json::Value {
    let rendered = tmpl
        .replace("__BASE_URL__", base_url)
        .replace("__TOKEN__", token);
    let mut full: serde_json::Value = serde_json::from_str(&rendered)
        .unwrap_or_else(|_| panic!("{} must be valid JSON", tmpl_name));
    if !embed_token {
        if let Some(env_obj) = full
            .pointer_mut("/mcpServers/missioncontrol/env")
            .and_then(|v| v.as_object_mut())
        {
            env_obj.remove("MC_TOKEN");
        }
    }
    full["mcpServers"]["missioncontrol"].clone()
}

fn absolutize_mc_command(mut entry: serde_json::Value) -> serde_json::Value {
    let cmd = entry
        .get("command")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if cmd != "mc" {
        return entry;
    }
    let resolved = std::env::current_exe()
        .ok()
        .filter(|p| p.is_file())
        .or_else(|| which_binary("mc").ok());
    if let Some(path) = resolved {
        if let Some(obj) = entry.as_object_mut() {
            obj.insert(
                "command".to_string(),
                serde_json::Value::String(path.display().to_string()),
            );
        }
    }
    entry
}

// ── OpenClawDriver / CustomDriver ────────────────────────────────────────────

struct OpenClawDriver;
struct CustomDriver;

impl AgentDriver for OpenClawDriver {
    fn binary(&self) -> &str {
        "openclaw"
    }

    fn install_hint(&self) -> &str {
        "See MissionControl docs for OpenClaw installation"
    }

    fn install_config(
        &self,
        _staging_dir: &Path,
        base_url: &str,
        token: &str,
        embed_token: bool,
        _target_home: &Path,
        target_mc_home: &Path,
    ) -> Result<()> {
        install_acp_config("openclaw", base_url, token, embed_token, target_mc_home)
    }

    fn command(&self, extra_args: &[String], target_mc_home: &Path) -> std::process::Command {
        let config = target_mc_home.join("config").join("openclaw.acp.json");
        let mut cmd = std::process::Command::new("openclaw");
        cmd.arg("--acp-config").arg(config);
        cmd.args(extra_args);
        cmd
    }
}

impl AgentDriver for CustomDriver {
    fn binary(&self) -> &str {
        "custom"
    }

    fn install_hint(&self) -> &str {
        "See MissionControl docs for custom ACP-compatible agent installation"
    }

    fn install_config(
        &self,
        _staging_dir: &Path,
        base_url: &str,
        token: &str,
        embed_token: bool,
        _target_home: &Path,
        target_mc_home: &Path,
    ) -> Result<()> {
        install_acp_config("custom", base_url, token, embed_token, target_mc_home)
    }

    fn command(&self, extra_args: &[String], target_mc_home: &Path) -> std::process::Command {
        let config = target_mc_home.join("config").join("custom.acp.json");
        let mut cmd = std::process::Command::new("custom");
        cmd.arg("--acp-config").arg(config);
        cmd.args(extra_args);
        cmd
    }
}

fn install_acp_config(
    name: &str,
    base_url: &str,
    token: &str,
    embed_token: bool,
    target_mc_home: &Path,
) -> Result<()> {
    let config_dir = target_mc_home.join("config");
    std::fs::create_dir_all(&config_dir)?;
    let out = config_dir.join(format!("{}.acp.json", name));
    let mut config = serde_json::json!({
        "mc_base_url": base_url,
    });
    if embed_token {
        config["mc_token"] = serde_json::json!(token);
    }
    // When not embedding, mc_token is intentionally absent; the ACP client
    // must read MC_TOKEN from the process environment at runtime.
    std::fs::write(&out, serde_json::to_string_pretty(&config)?)?;
    mc_ok!("ACP config written → {}", out.display());
    Ok(())
}

// ── Orchestration ─────────────────────────────────────────────────────────────

pub async fn run(args: LaunchArgs, client: &MissionControlClient, config: &McConfig) -> Result<()> {
    let mut selected_agent = resolve_agent_choice(args.agent.clone())?;
    let want_resume = !args.new_session
        && (args.resume
            || args.session_id.is_some()
            || matches!(args.agent, Some(AgentKind::Resume)));
    let base_mc_home = mc_home_dir();
    fs::create_dir_all(&base_mc_home)?;

    let profile_name = resolve_profile_name(
        &args.profile,
        if matches!(selected_agent, AgentKind::Resume) {
            None
        } else {
            Some(selected_agent.config_key())
        },
        client,
    )
    .await
    .unwrap_or_else(|_| "default".to_string());

    let resumed = if want_resume {
        find_resume_session(&base_mc_home, args.session_id.as_deref(), &profile_name)?
    } else {
        None
    };
    if let Some(record) = &resumed {
        if let Ok(kind) = parse_agent_kind(&record.agent) {
            selected_agent = kind;
            mc_info!(
                "resuming runtime session {} ({})",
                record.runtime_session_id,
                record.agent
            );
        }
    }

    let runtime_session_id = resumed
        .as_ref()
        .map(|r| r.runtime_session_id.clone())
        .unwrap_or_else(|| format!("rs_{}", Uuid::new_v4().simple()));
    let instance_home = base_mc_home.join("instances").join(&runtime_session_id);
    let profile_home = base_mc_home.join("profiles").join(&profile_name);
    fs::create_dir_all(&instance_home)?;
    fs::create_dir_all(&profile_home)?;

    let agent_home = instance_home.join("home");
    fs::create_dir_all(&agent_home)?;
    let instance_mc_home = instance_home.join("mc");
    fs::create_dir_all(&instance_mc_home)?;
    persist_runtime_context(
        &instance_home,
        &runtime_session_id,
        selected_agent.config_key(),
        &profile_name,
        &profile_home,
    )?;
    if let Err(e) = write_mc_context_json(
        &instance_mc_home,
        config.base_url.as_str(),
        &profile_name,
        &runtime_session_id,
    ) {
        mc_warn!("could not write mc/context.json: {}", e);
    }
    upsert_launch_session(
        &base_mc_home,
        LaunchSessionRecord {
            runtime_session_id: runtime_session_id.clone(),
            agent: selected_agent.config_key().to_string(),
            profile: profile_name.clone(),
            instance_home: instance_home.display().to_string(),
            created_at: chrono::Utc::now().to_rfc3339(),
        },
    )?;

    let driver = selected_agent.driver();

    // 1. Verify binary is on PATH before doing anything else.
    check_binary(driver.as_ref())?;

    // Print brand banner after confirming the binary exists.
    ui::print_banner(
        config.base_url.as_str(),
        selected_agent.config_key(),
        env!("CARGO_PKG_VERSION"),
    );

    // 2. (Daemon lifecycle removed — mc serve connects directly to backend.)

    // 3. Auth: verify we have a valid session or static token; run interactive
    //    login if neither is available.  Falls through immediately when MC_TOKEN
    //    is already set (static token path).
    let login_client_holder: Option<MissionControlClient> = if config.token.is_none() {
        if auth::load_saved_session(config.base_url.as_str()).is_none() {
            mc_warn!("no valid session found for {}", config.base_url.as_str());
            mc_info!("running `mc auth login` to authenticate...");
            auth::login(
                auth::LoginArgs {
                    ttl_hours: 8,
                    print_token: false,
                    non_interactive: false,
                },
                client,
                config.base_url.as_str(),
            )
            .await
            .context("login failed — cannot launch without authentication")?;
        }
        // Rebuild client with the freshly written (or pre-existing) session token.
        let session_token = auth::load_saved_session(config.base_url.as_str())
            .map(|s| s.token)
            .context("session not found after login — run `mc auth login` manually")?;
        Some(
            MissionControlClient::new_with_token(config.base_url.as_str(), &session_token)
                .context("failed to build client with session token")?,
        )
    } else {
        None
    };
    let effective_client: &MissionControlClient = login_client_holder.as_ref().unwrap_or(client);
    enforce_profile_pin(effective_client, &profile_name, args.allow_pin_mismatch).await?;

    // 4. Preflight-only mode: verify connectivity then stop.
    if args.preflight_only {
        effective_client
            .get_json("/mcp/health")
            .await
            .context("auth preflight failed — check MC_TOKEN and MC_BASE_URL")?;
        mc_ok!("preflight passed");
        return Ok(());
    }

    let base_url = config.base_url.as_str().trim_end_matches('/').to_string();
    // Effective token: static config token, or session token from disk (after login).
    let token = config.token.clone().unwrap_or_else(|| {
        auth::load_saved_session(config.base_url.as_str())
            .map(|s| s.token)
            .unwrap_or_default()
    });

    // 5. Resolve token-embedding mode.
    //
    //    Precedence (highest first):
    //      a) --no-embed-token flag  → never embed
    //      b) token is empty         → cannot embed; auto-implies no-embed with notice
    //      c) default                → embed
    let embed_token = resolve_embed_token(args.no_embed_token, &token);

    let staging_dir = instance_mc_home.join("config");
    std::fs::create_dir_all(&staging_dir)?;

    // 6. Fetch agent config from onboarding manifest and write to staging dir.
    if !args.skip_config_gen {
        fetch_and_stage_agent_config(
            effective_client,
            &selected_agent,
            &staging_dir,
            &base_url,
            &token,
        )
        .await?;
    }

    // 7. Install config in instance-local paths by default.
    let config_target_home = if args.legacy_global_config {
        dirs::home_dir().ok_or_else(|| anyhow!("cannot determine home directory"))?
    } else {
        initialize_profile_overlay(
            &selected_agent,
            &agent_home,
            &profile_home,
            &dirs::home_dir().ok_or_else(|| anyhow!("cannot determine home directory"))?,
        )?;
        agent_home.clone()
    };
    std::env::set_var("MC_HOME", &instance_mc_home);
    std::env::set_var("MC_AGENT_PROFILE", &profile_name);
    std::env::set_var("MC_RUNTIME_SESSION_ID", &runtime_session_id);
    std::env::set_var("MC_INSTANCE_HOME", &instance_home);
    let launch_agent_base = config
        .agent_context
        .agent_id
        .clone()
        .or_else(|| crate::config::default_agent_id_from_session(config.base_url.as_str()))
        .unwrap_or_else(|| format!("mc-agent-{}", Uuid::new_v4().simple()));
    let launch_agent_id = format!(
        "{}:{}:{}",
        launch_agent_base,
        selected_agent.config_key(),
        &runtime_session_id[..12.min(runtime_session_id.len())]
    );
    std::env::set_var("MC_AGENT_ID", &launch_agent_id);
    driver.install_config(
        &staging_dir,
        &base_url,
        &token,
        embed_token,
        &config_target_home,
        &instance_mc_home,
    )?;
    if matches!(selected_agent, AgentKind::Codex) {
        codex_preflight_report(&agent_home);
    }
    if matches!(selected_agent, AgentKind::Claude) {
        claude_preflight_report(&agent_home);
    }

    // 7b. MCP connectivity preflight — verify backend is reachable and tools
    //     are available before handing off to the agent. A failure here is
    //     non-fatal (warn only): the MCP server's retry loop will recover if
    //     the backend comes up after the agent starts.
    mcp_connectivity_preflight(effective_client).await;

    // 8. Exec the agent (replaces the current process on Unix).
    //    Always inject MC_TOKEN into the agent environment so the MCP shim can
    //    authenticate even when the token was NOT embedded in the config file.
    exec_agent(
        driver.as_ref(),
        &args.agent_args,
        &token,
        &runtime_session_id,
        &instance_home,
        &agent_home,
        &instance_mc_home,
        &profile_name,
    )
}

fn claude_preflight_report(agent_home: &Path) {
    let checks = [
        (
            agent_home.join(".claude.json"),
            "Claude config (.claude.json)",
        ),
        (
            agent_home.join(".claude").join("settings.json"),
            "Claude settings (.claude/settings.json)",
        ),
        (
            agent_home.join(".claude").join(".credentials.json"),
            "Claude auth (.claude/.credentials.json)",
        ),
    ];
    let mut missing: Vec<String> = Vec::new();
    for (path, label) in checks {
        if !path.exists() {
            missing.push(format!("{} missing at {}", label, path.display()));
        }
    }
    if missing.is_empty() {
        mc_info!("claude preflight: auth/settings artifacts detected");
        return;
    }
    for line in missing {
        mc_warn!("claude preflight: {}", line);
    }
    mc_warn!("claude may prompt for theme/login if these are not initialized for this profile");
}

fn codex_preflight_report(agent_home: &Path) {
    let checks = [
        (
            agent_home.join(".codex").join("config.toml"),
            "Codex config (.codex/config.toml)",
        ),
        (
            agent_home.join(".codex").join("auth.json"),
            "Codex auth (.codex/auth.json)",
        ),
        (
            agent_home.join(".codex").join("credentials.json"),
            "Codex credentials (.codex/credentials.json)",
        ),
    ];
    let mut missing: Vec<String> = Vec::new();
    for (path, label) in checks {
        if !path.exists() {
            missing.push(format!("{} missing at {}", label, path.display()));
        }
    }
    if missing.is_empty() {
        mc_info!("codex preflight: auth/settings artifacts detected");
        return;
    }
    for line in missing {
        mc_warn!("codex preflight: {}", line);
    }
    mc_warn!("codex may prompt for login if auth artifacts are not initialized for this profile");
}

/// Verify MCP backend connectivity and tool availability before exec.
///
/// Non-fatal: prints status either way. The MCP server's retry loop handles
/// recovery if the backend is temporarily unavailable at agent start time.
async fn mcp_connectivity_preflight(client: &MissionControlClient) {
    let mut tools_count: usize = 0;
    // Health check.
    match client.get_json("/mcp/health").await {
        Err(e) => {
            mc_warn!("MCP preflight: backend unreachable ({})", e);
            mc_warn!(
                "MCP preflight: tools will load once backend is available (retry loop active)"
            );
            return;
        }
        Ok(_) => {}
    }

    // Tools count.
    match client.get_json("/mcp/tools").await {
        Ok(resp) => {
            let count = match &resp {
                serde_json::Value::Array(arr) => arr.len(),
                serde_json::Value::Object(obj) => obj
                    .get("tools")
                    .and_then(|t| t.as_array())
                    .map(|a| a.len())
                    .unwrap_or(0),
                _ => 0,
            };
            if count > 0 {
                mc_ok!(
                    "MCP preflight: backend reachable, {} tools available",
                    count
                );
                tools_count = count;
            } else {
                mc_warn!("MCP preflight: backend reachable but returned 0 tools");
            }
        }
        Err(e) => {
            mc_warn!("MCP preflight: tools fetch failed ({})", e);
        }
    }

    eprintln!();
    eprintln!(
        "{}{}Launch Preflight Summary{}",
        ui::BOLD,
        ui::ORANGE,
        ui::RESET
    );
    eprintln!("{}{}{}", ui::GRAY, "─".repeat(56), ui::RESET);
    eprintln!(
        "  {}Backend:{} {}{}{}",
        ui::DIM,
        ui::RESET,
        ui::GREEN,
        "reachable",
        ui::RESET
    );
    eprintln!(
        "  {}Tools:{} {}{}{}",
        ui::DIM,
        ui::RESET,
        if tools_count > 0 {
            ui::CYAN
        } else {
            ui::YELLOW
        },
        tools_count,
        ui::RESET
    );
}

/// Determine whether to embed `MC_TOKEN` into the written agent config.
///
/// Precedence (highest → lowest):
///   1. `--no-embed-token` flag → never embed
///   2. Token is an `mcs_` session token → never embed (managed by mc, injected at exec)
///   3. Token is empty → cannot embed; auto-implies no-embed
///   4. Default → embed
fn resolve_embed_token(no_embed_flag: bool, token: &str) -> bool {
    if no_embed_flag {
        mc_info!("--no-embed-token: MC_TOKEN will NOT be written to agent config");
        mc_info!("token will be injected into the agent process at exec time");
        return false;
    }
    if crate::auth::is_session_token(token) {
        mc_info!("session token (mcs_*) detected — will NOT be embedded in agent config");
        mc_info!("session token will be injected into the agent process at exec time");
        return false;
    }
    if token.is_empty() {
        mc_warn!("MC_TOKEN is not set — implying --no-embed-token");
        mc_warn!("ensure MC_TOKEN is present in the environment when the agent runs");
        return false;
    }
    true
}

fn resolve_agent_choice(agent: Option<AgentKind>) -> Result<AgentKind> {
    if let Some(kind) = agent {
        if !matches!(kind, AgentKind::Resume) {
            return Ok(kind);
        }
    }
    eprint!("mc launch: choose agent [codex/claude/gemini/openclaw/custom] (default codex): ");
    io::stderr().flush()?;
    let mut answer = String::new();
    io::stdin().read_line(&mut answer)?;
    let trimmed = answer.trim().to_lowercase();
    if trimmed.is_empty() {
        return Ok(AgentKind::Codex);
    }
    parse_agent_kind(&trimmed)
}

fn managed_config_relpaths(agent: &AgentKind) -> &'static [&'static str] {
    match agent {
        AgentKind::Codex => &[
            ".codex/config.toml",
            ".codex/auth.json",
            ".codex/credentials.json",
            ".codex/rules",
        ],
        AgentKind::Claude => &[".claude.json", ".claude"],
        AgentKind::Gemini => &[".gemini/settings.json"],
        _ => &[],
    }
}

fn initialize_profile_overlay(
    agent: &AgentKind,
    agent_home: &Path,
    profile_home: &Path,
    global_home: &Path,
) -> Result<()> {
    if matches!(agent, AgentKind::Codex) {
        let inserted = ensure_codex_approval_rules_for_profile(profile_home)?;
        if inserted > 0 {
            mc_info!(
                "seeded {} codex approval rules at {}",
                inserted,
                profile_home.join(".codex/rules/default.rules").display()
            );
        }
    }
    for rel in managed_config_relpaths(agent) {
        let profile_path = profile_home.join(rel);
        let global_path = global_home.join(rel);
        if global_path.exists() {
            if should_force_profile_refresh(rel) {
                seed_profile_path(&global_path, &profile_path)?;
                mc_info!(
                    "refreshed profile auth from global {}",
                    global_path.display()
                );
            } else if !profile_path.exists() {
                seed_profile_path(&global_path, &profile_path)?;
                mc_info!(
                    "seeded profile config from global {}",
                    global_path.display()
                );
            } else if global_path.is_dir() && profile_path.is_dir() {
                let copied = merge_missing_dir_entries(&global_path, &profile_path)?;
                if copied > 0 {
                    mc_info!(
                        "merged {} missing profile entries from global {}",
                        copied,
                        global_path.display()
                    );
                }
            }
        }

        let instance_path = agent_home.join(rel);
        if let Some(parent) = instance_path.parent() {
            fs::create_dir_all(parent)?;
        }
        if instance_path.exists() {
            let meta = fs::symlink_metadata(&instance_path)?;
            if meta.file_type().is_symlink() || meta.is_file() {
                fs::remove_file(&instance_path)?;
            } else if meta.is_dir() {
                fs::remove_dir_all(&instance_path)?;
            }
        }

        if let Some(parent) = profile_path.parent() {
            fs::create_dir_all(parent)?;
        }
        if !profile_path.exists() {
            if rel.ends_with('/') || *rel == ".claude" {
                fs::create_dir_all(&profile_path)?;
            } else {
                fs::write(&profile_path, "")?;
            }
        }

        #[cfg(unix)]
        {
            unix_fs::symlink(&profile_path, &instance_path).with_context(|| {
                format!(
                    "failed to link instance config {} -> {}",
                    instance_path.display(),
                    profile_path.display()
                )
            })?;
        }
        #[cfg(not(unix))]
        {
            fs::copy(&profile_path, &instance_path)?;
        }
    }
    Ok(())
}

pub(crate) fn ensure_codex_approval_rules_for_profile(profile_home: &Path) -> Result<usize> {
    let rules_dir = profile_home.join(".codex").join("rules");
    fs::create_dir_all(&rules_dir)?;
    let rules_path = rules_dir.join("default.rules");
    let existing = if rules_path.exists() {
        fs::read_to_string(&rules_path)?
    } else {
        String::new()
    };
    let mut appended = String::new();
    let mut inserted: usize = 0;
    for rule in CODEX_APPROVAL_RULES {
        if !existing.contains(rule) {
            appended.push_str(rule);
            appended.push('\n');
            inserted += 1;
        }
    }
    if inserted > 0 {
        let mut content = existing;
        if !content.is_empty() && !content.ends_with('\n') {
            content.push('\n');
        }
        content.push_str(&appended);
        fs::write(&rules_path, content)?;
    }
    Ok(inserted)
}

pub(crate) fn codex_approval_rules_for_profile(profile_home: &Path) -> Result<(PathBuf, Vec<String>)> {
    let rules_path = profile_home
        .join(".codex")
        .join("rules")
        .join("default.rules");
    if !rules_path.exists() {
        return Ok((
            rules_path,
            CODEX_APPROVAL_RULES.iter().map(|s| s.to_string()).collect(),
        ));
    }
    let content = fs::read_to_string(&rules_path)?;
    let missing = CODEX_APPROVAL_RULES
        .iter()
        .filter(|rule| !content.contains(**rule))
        .map(|s| s.to_string())
        .collect::<Vec<_>>();
    Ok((rules_path, missing))
}

fn should_force_profile_refresh(rel: &str) -> bool {
    matches!(rel, ".codex/auth.json" | ".codex/credentials.json")
}

fn seed_profile_path(global_path: &Path, profile_path: &Path) -> Result<()> {
    if let Some(parent) = profile_path.parent() {
        fs::create_dir_all(parent)?;
    }
    if global_path.is_dir() {
        copy_dir_recursive(global_path, profile_path).with_context(|| {
            format!(
                "failed to seed profile directory from {} to {}",
                global_path.display(),
                profile_path.display()
            )
        })?;
    } else {
        fs::copy(global_path, profile_path).with_context(|| {
            format!(
                "failed to seed profile config from {} to {}",
                global_path.display(),
                profile_path.display()
            )
        })?;
    }
    Ok(())
}

fn copy_dir_recursive(src: &Path, dst: &Path) -> Result<()> {
    fs::create_dir_all(dst)?;
    for entry in fs::read_dir(src)? {
        let entry = entry?;
        let path = entry.path();
        let target = dst.join(entry.file_name());
        let meta = fs::symlink_metadata(&path)?;
        if meta.file_type().is_symlink() {
            let link_target = fs::read_link(&path)?;
            #[cfg(unix)]
            unix_fs::symlink(link_target, &target)?;
            #[cfg(not(unix))]
            {
                if path.is_dir() {
                    fs::create_dir_all(&target)?;
                } else {
                    fs::copy(&path, &target)?;
                }
            }
        } else if meta.is_dir() {
            copy_dir_recursive(&path, &target)?;
        } else {
            fs::copy(&path, &target)?;
        }
    }
    Ok(())
}

fn merge_missing_dir_entries(src: &Path, dst: &Path) -> Result<usize> {
    let mut copied: usize = 0;
    for entry in fs::read_dir(src)? {
        let entry = entry?;
        let src_path = entry.path();
        let dst_path = dst.join(entry.file_name());
        let meta = fs::symlink_metadata(&src_path)?;
        if meta.is_dir() {
            if !dst_path.exists() {
                copy_dir_recursive(&src_path, &dst_path)?;
                copied += 1;
            } else if dst_path.is_dir() {
                copied += merge_missing_dir_entries(&src_path, &dst_path)?;
            }
            continue;
        }
        if !dst_path.exists() {
            if meta.file_type().is_symlink() {
                let link_target = fs::read_link(&src_path)?;
                #[cfg(unix)]
                unix_fs::symlink(link_target, &dst_path)?;
                #[cfg(not(unix))]
                {
                    fs::copy(&src_path, &dst_path)?;
                }
            } else {
                fs::copy(&src_path, &dst_path)?;
            }
            copied += 1;
        }
    }
    Ok(copied)
}

fn parse_agent_kind(value: &str) -> Result<AgentKind> {
    match value.trim().to_lowercase().as_str() {
        "codex" => Ok(AgentKind::Codex),
        "claude" => Ok(AgentKind::Claude),
        "gemini" => Ok(AgentKind::Gemini),
        "openclaw" => Ok(AgentKind::Openclaw),
        "custom" | "nanoclaw" => Ok(AgentKind::Custom),
        _ => Err(anyhow!("unsupported agent '{}'", value)),
    }
}

async fn resolve_profile_name(
    requested: &Option<String>,
    agent_key: Option<&str>,
    client: &MissionControlClient,
) -> Result<String> {
    if let Some(profile) = requested {
        return Ok(profile.trim().to_string());
    }
    let profiles = mcp_profile_call(client, "list_profiles", json!({ "limit": 200 })).await?;
    if let Some(items) = profiles.get("profiles").and_then(|v| v.as_array()) {
        for item in items {
            if item
                .get("is_default")
                .and_then(|v| v.as_bool())
                .unwrap_or(false)
            {
                if let Some(name) = item.get("name").and_then(|v| v.as_str()) {
                    return Ok(name.to_string());
                }
            }
        }
        if items.is_empty() {
            let bootstrap_name = "default";
            mc_info!(
                "no remote profiles found; bootstrapping '{}' via MCP",
                bootstrap_name
            );
            let tarball_b64 = empty_profile_tarball_b64()?;
            let published = mcp_profile_call(
                client,
                "publish_profile",
                json!({
                    "name": bootstrap_name,
                    "description": "Bootstrap profile created by mc launch",
                    "is_default": true,
                    "manifest": [],
                    "tarball_b64": tarball_b64
                }),
            )
            .await?;
            if let Some(name) = published
                .get("profile")
                .and_then(|v| v.get("name"))
                .and_then(|v| v.as_str())
            {
                return Ok(name.to_string());
            }
            return Ok(bootstrap_name.to_string());
        }
    }
    Ok(agent_key.unwrap_or("default").to_string())
}

async fn mcp_profile_call(client: &MissionControlClient, tool: &str, args: Value) -> Result<Value> {
    let response = client
        .post_json("/mcp/call", &json!({ "tool": tool, "args": args }))
        .await?;
    let ok = response
        .get("ok")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    if !ok {
        let err = response
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("mcp profile tool failed");
        anyhow::bail!("{}", err);
    }
    Ok(response.get("result").cloned().unwrap_or_else(|| json!({})))
}

fn empty_profile_tarball_b64() -> Result<String> {
    use base64::Engine;
    let mut bytes = Vec::<u8>::new();
    {
        let mut builder = tar::Builder::new(&mut bytes);
        builder.finish()?;
    }
    Ok(base64::engine::general_purpose::STANDARD.encode(bytes))
}

fn session_index_path(base_mc_home: &Path) -> PathBuf {
    base_mc_home.join("sessions").join("launch-index.jsonl")
}

pub(crate) fn sessions_for_profile(profile: &str) -> Vec<LaunchSessionRecord> {
    read_launch_sessions(&mc_home_dir())
        .unwrap_or_default()
        .into_iter()
        .filter(|s| s.profile == profile)
        .collect()
}

fn read_launch_sessions(base_mc_home: &Path) -> Result<Vec<LaunchSessionRecord>> {
    let path = session_index_path(base_mc_home);
    if !path.exists() {
        return Ok(Vec::new());
    }
    let content = fs::read_to_string(path)?;
    let mut out = Vec::new();
    for line in content.lines() {
        if line.trim().is_empty() {
            continue;
        }
        if let Ok(record) = serde_json::from_str::<LaunchSessionRecord>(line) {
            out.push(record);
        }
    }
    Ok(out)
}

fn upsert_launch_session(base_mc_home: &Path, record: LaunchSessionRecord) -> Result<()> {
    let mut sessions = read_launch_sessions(base_mc_home)?;
    sessions.retain(|s| s.runtime_session_id != record.runtime_session_id);
    sessions.push(record);
    let sessions_dir = base_mc_home.join("sessions");
    fs::create_dir_all(&sessions_dir)?;
    let body = sessions
        .iter()
        .map(serde_json::to_string)
        .collect::<std::result::Result<Vec<_>, _>>()?
        .join("\n");
    fs::write(session_index_path(base_mc_home), format!("{}\n", body))?;
    Ok(())
}

fn find_resume_session(
    base_mc_home: &Path,
    session_id: Option<&str>,
    profile: &str,
) -> Result<Option<LaunchSessionRecord>> {
    let mut sessions = read_launch_sessions(base_mc_home)?;
    sessions.sort_by(|a, b| a.created_at.cmp(&b.created_at));
    if let Some(id) = session_id {
        return Ok(sessions.into_iter().find(|s| s.runtime_session_id == id));
    }
    let mut candidates: Vec<LaunchSessionRecord> = sessions
        .into_iter()
        .filter(|s| s.profile == profile)
        .collect();
    candidates.sort_by(|a, b| a.created_at.cmp(&b.created_at));
    candidates.reverse();
    if candidates.is_empty() {
        return Ok(None);
    }
    if candidates.len() == 1 {
        return Ok(candidates.into_iter().next());
    }
    eprintln!("mc launch resume: select session to resume");
    for (idx, candidate) in candidates.iter().take(10).enumerate() {
        eprintln!(
            "  {}) {}  agent={}  created_at={}",
            idx + 1,
            candidate.runtime_session_id,
            candidate.agent,
            candidate.created_at
        );
    }
    eprint!("choice [1]: ");
    io::stderr().flush()?;
    let mut answer = String::new();
    io::stdin().read_line(&mut answer)?;
    let picked = answer
        .trim()
        .parse::<usize>()
        .ok()
        .filter(|n| *n > 0)
        .unwrap_or(1);
    let picked_idx = picked
        .saturating_sub(1)
        .min(candidates.len().saturating_sub(1));
    Ok(Some(candidates[picked_idx].clone()))
}

/// Write (or refresh) `$MC_INSTANCE_HOME/mc/context.json` with the current
/// agent context. Called at launch and patched live by the MCP server after
/// tool calls that return mission/kluster IDs.
///
/// The file is read by the PreCompact and SessionStart(compact) hook scripts
/// to re-inject mission context into Claude's window after compaction.
pub fn write_mc_context_json(
    instance_mc_home: &Path,
    base_url: &str,
    active_profile: &str,
    runtime_session_id: &str,
) -> Result<()> {
    // Load existing file so we can preserve active_mission_id / active_kluster_id
    // written by the MCP server between launch invocations.
    let existing: Value = if instance_mc_home.join("context.json").exists() {
        let raw = fs::read_to_string(instance_mc_home.join("context.json"))?;
        serde_json::from_str(&raw).unwrap_or(json!({}))
    } else {
        json!({})
    };

    let payload = json!({
        "runtime_session_id": runtime_session_id,
        "base_url": base_url,
        "active_profile": active_profile,
        "active_mission_id": existing.get("active_mission_id").cloned().unwrap_or(Value::Null),
        "active_kluster_id": existing.get("active_kluster_id").cloned().unwrap_or(Value::Null),
        "last_sync_at": chrono::Utc::now().to_rfc3339(),
    });

    fs::create_dir_all(instance_mc_home)?;
    fs::write(
        instance_mc_home.join("context.json"),
        serde_json::to_string_pretty(&payload)?,
    )?;
    Ok(())
}

fn persist_runtime_context(
    instance_home: &Path,
    runtime_session_id: &str,
    agent: &str,
    profile: &str,
    profile_home: &Path,
) -> Result<()> {
    let payload = serde_json::json!({
        "runtime_session_id": runtime_session_id,
        "agent": agent,
        "profile": profile,
        "profile_home": profile_home.display().to_string(),
        "created_at": chrono::Utc::now().to_rfc3339(),
    });
    fs::write(
        instance_home.join("runtime-context.json"),
        serde_json::to_string_pretty(&payload)?,
    )?;
    Ok(())
}

// ── Helpers ───────────────────────────────────────────────────────────────────

fn check_binary(driver: &dyn AgentDriver) -> Result<()> {
    let binary = driver.binary();
    which_binary(binary)
        .with_context(|| {
            format!(
                "agent binary '{}' not found on PATH\nInstall with: {}",
                binary,
                driver.install_hint()
            )
        })
        .map(|_| ())
}

fn which_binary(name: &str) -> Result<PathBuf> {
    let path_var = std::env::var_os("PATH").unwrap_or_default();
    for dir in std::env::split_paths(&path_var) {
        let candidate = dir.join(name);
        if candidate.is_file() {
            return Ok(candidate);
        }
    }
    Err(anyhow!("not found on PATH"))
}

async fn fetch_and_stage_agent_config(
    client: &MissionControlClient,
    agent: &AgentKind,
    staging_dir: &Path,
    base_url: &str,
    _token: &str,
) -> Result<()> {
    let encoded_url: String = url::form_urlencoded::byte_serialize(base_url.as_bytes()).collect();
    let manifest = client
        .get_json(&format!("/agent-onboarding.json?endpoint={}", encoded_url))
        .await
        .context("failed to fetch agent onboarding manifest")?;

    let config_key = agent.config_key();
    if let Some(agent_cfg) = manifest
        .get("agent_configs")
        .and_then(|c| c.get(config_key))
    {
        let out_path = staging_dir.join(format!("{}.manifest.json", config_key));
        std::fs::write(&out_path, serde_json::to_string_pretty(agent_cfg)?)?;
        mc_info!("manifest staged → {}", out_path.display());
    } else {
        mc_warn!(
            "no agent_configs.{} in manifest — using embedded template",
            config_key
        );
    }
    Ok(())
}

fn exec_agent(
    driver: &dyn AgentDriver,
    extra_args: &[String],
    token: &str,
    runtime_session_id: &str,
    instance_home: &Path,
    agent_home: &Path,
    instance_mc_home: &Path,
    profile_name: &str,
) -> Result<()> {
    let binary_name = driver.binary().to_string();
    let mut cmd = driver.command(extra_args, instance_mc_home);

    // Always inject MC_TOKEN into the agent's process environment. This ensures
    // the MCP shim can authenticate regardless of whether the token was embedded
    // in the config file — covering session tokens, --no-embed-token, and the
    // standard embedded-token path uniformly.
    //
    // MC_AGENT_TOKEN is an alias used by Claude Code native hooks (SessionStart,
    // SessionEnd, PostToolUse). It is listed in `allowedEnvVars` in the hook
    // config so Claude Code will forward it in HTTP hook Authorization headers.
    if !token.is_empty() {
        cmd.env("MC_TOKEN", token);
        cmd.env("MC_AGENT_TOKEN", token);
    }
    cmd.env("HOME", agent_home);

    // Claude Code checks that $HOME/.local/bin is in PATH to confirm its install
    // method. Since we override HOME to the isolated instance home, prepend the
    // instance's .local/bin so that check passes.
    let instance_local_bin = agent_home.join(".local").join("bin");
    if let Some(current_path) = std::env::var_os("PATH") {
        let new_path = std::env::join_paths(
            std::iter::once(instance_local_bin.clone()).chain(std::env::split_paths(&current_path)),
        )
        .unwrap_or(current_path);
        cmd.env("PATH", new_path);
    }

    cmd.env("MC_HOME", instance_mc_home);
    cmd.env("MC_RUNTIME_SESSION_ID", runtime_session_id);
    cmd.env("MC_INSTANCE_HOME", instance_home);
    cmd.env("MC_AGENT_PROFILE", profile_name);

    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        let err = cmd.exec();
        // exec() only returns if it fails.
        Err(anyhow::Error::new(err).context(format!("failed to exec '{}'", binary_name)))
    }

    #[cfg(not(unix))]
    {
        let status = cmd.status()?;
        if !status.success() {
            anyhow::bail!("'{}' exited with status: {}", binary_name, status);
        }
        Ok(())
    }
}

async fn enforce_profile_pin(
    client: &MissionControlClient,
    profile_name: &str,
    allow_pin_mismatch: bool,
) -> Result<()> {
    let profile_root = mc_home_dir().join("profiles").join(profile_name);
    let pin_path = profile_root.join("pin.json");
    if !pin_path.exists() {
        return Ok(());
    }
    let pin_json: serde_json::Value = serde_json::from_str(&fs::read_to_string(&pin_path)?)
        .context("invalid pin.json (expected JSON)")?;
    let pinned_sha = pin_json
        .get("pinned_sha256")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if pinned_sha.is_empty() {
        return Ok(());
    }
    let encoded: String = url::form_urlencoded::byte_serialize(profile_name.as_bytes()).collect();
    let remote = client
        .get_json(&format!("/me/profiles/{}", encoded))
        .await
        .with_context(|| format!("failed to fetch remote profile '{}'", profile_name))?;
    let remote_sha = remote
        .get("sha256")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if remote_sha.is_empty() {
        anyhow::bail!("remote profile '{}' has no sha256", profile_name);
    }
    if remote_sha != pinned_sha && !allow_pin_mismatch {
        anyhow::bail!(
            "profile '{}' is pinned to sha256 '{}' but remote is '{}'; rerun with --allow-pin-mismatch to override",
            profile_name,
            pinned_sha,
            remote_sha
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn resolve_embed_token_never_embeds_session_tokens() {
        assert!(!resolve_embed_token(false, "mcs_example_session_token"));
    }

    #[test]
    fn codex_config_writes_to_target_home() {
        let tmp = tempdir().expect("tempdir");
        let target_home = tmp.path().join("agent-home");
        let target_mc_home = tmp.path().join("mc-home");
        fs::create_dir_all(&target_home).expect("target_home");
        fs::create_dir_all(&target_mc_home).expect("target_mc_home");

        let driver = CodexDriver;
        driver
            .install_config(
                tmp.path(),
                "http://localhost:8008",
                "tok",
                true,
                &target_home,
                &target_mc_home,
            )
            .expect("install codex config");

        assert!(target_home.join(".codex/config.toml").exists());
    }

    #[test]
    fn claude_config_writes_to_target_home() {
        let tmp = tempdir().expect("tempdir");
        let target_home = tmp.path().join("agent-home");
        let target_mc_home = tmp.path().join("mc-home");
        fs::create_dir_all(&target_home).expect("target_home");
        fs::create_dir_all(&target_mc_home).expect("target_mc_home");

        let driver = ClaudeDriver;
        driver
            .install_config(
                tmp.path(),
                "http://localhost:8008",
                "tok",
                true,
                &target_home,
                &target_mc_home,
            )
            .expect("install claude config");

        assert!(target_home.join(".claude.json").exists());
    }

    #[test]
    fn gemini_config_writes_to_target_home() {
        let tmp = tempdir().expect("tempdir");
        let target_home = tmp.path().join("agent-home");
        let target_mc_home = tmp.path().join("mc-home");
        fs::create_dir_all(&target_home).expect("target_home");
        fs::create_dir_all(&target_mc_home).expect("target_mc_home");

        let driver = GeminiDriver;
        driver
            .install_config(
                tmp.path(),
                "http://localhost:8008",
                "tok",
                true,
                &target_home,
                &target_mc_home,
            )
            .expect("install gemini config");

        assert!(target_home.join(".gemini/settings.json").exists());
    }

    #[test]
    #[cfg(unix)]
    fn overlay_seeds_profile_from_global_and_links_instance() {
        let tmp = tempdir().expect("tempdir");
        let global_home = tmp.path().join("global-home");
        let profile_home = tmp.path().join("profile-home");
        let agent_home = tmp.path().join("agent-home");
        fs::create_dir_all(global_home.join(".claude")).expect("global home");
        fs::create_dir_all(&profile_home).expect("profile home");
        fs::create_dir_all(&agent_home).expect("agent home");

        let global_cfg = global_home.join(".claude.json");
        fs::write(&global_cfg, r#"{"theme":"dark"}"#).expect("write global config");

        initialize_profile_overlay(&AgentKind::Claude, &agent_home, &profile_home, &global_home)
            .expect("initialize profile overlay");

        let profile_cfg = profile_home.join(".claude.json");
        assert!(profile_cfg.exists(), "profile config should be seeded");
        assert_eq!(
            fs::read_to_string(&profile_cfg).expect("read profile"),
            r#"{"theme":"dark"}"#
        );

        let instance_cfg = agent_home.join(".claude.json");
        let meta = fs::symlink_metadata(&instance_cfg).expect("instance metadata");
        assert!(
            meta.file_type().is_symlink(),
            "instance path should be symlink"
        );
        let target = fs::read_link(&instance_cfg).expect("read symlink");
        assert_eq!(target, profile_cfg);
    }

    #[test]
    #[cfg(unix)]
    fn overlay_seeds_claude_dir_and_links_instance_dir() {
        let tmp = tempdir().expect("tempdir");
        let global_home = tmp.path().join("global-home");
        let profile_home = tmp.path().join("profile-home");
        let agent_home = tmp.path().join("agent-home");
        fs::create_dir_all(global_home.join(".claude")).expect("global claude dir");
        fs::create_dir_all(&profile_home).expect("profile home");
        fs::create_dir_all(&agent_home).expect("agent home");

        let credentials = global_home.join(".claude").join(".credentials.json");
        fs::write(&credentials, r#"{"kind":"oauth"}"#).expect("write global creds");

        initialize_profile_overlay(&AgentKind::Claude, &agent_home, &profile_home, &global_home)
            .expect("initialize profile overlay");

        let profile_dir = profile_home.join(".claude");
        let profile_creds = profile_dir.join(".credentials.json");
        assert!(profile_creds.exists(), "profile creds should be seeded");
        assert_eq!(
            fs::read_to_string(&profile_creds).expect("read profile creds"),
            r#"{"kind":"oauth"}"#
        );

        let instance_dir = agent_home.join(".claude");
        let meta = fs::symlink_metadata(&instance_dir).expect("instance dir metadata");
        assert!(
            meta.file_type().is_symlink(),
            "instance dir should be symlink"
        );
        let target = fs::read_link(&instance_dir).expect("read dir symlink");
        assert_eq!(target, profile_dir);
    }

    #[test]
    #[cfg(unix)]
    fn overlay_merges_missing_files_into_existing_profile_claude_dir() {
        let tmp = tempdir().expect("tempdir");
        let global_home = tmp.path().join("global-home");
        let profile_home = tmp.path().join("profile-home");
        let agent_home = tmp.path().join("agent-home");
        fs::create_dir_all(global_home.join(".claude")).expect("global claude dir");
        fs::create_dir_all(profile_home.join(".claude")).expect("profile claude dir");
        fs::create_dir_all(&agent_home).expect("agent home");

        fs::write(
            global_home.join(".claude").join(".credentials.json"),
            r#"{"kind":"oauth"}"#,
        )
        .expect("write global creds");
        fs::write(
            profile_home.join(".claude").join("settings.json"),
            r#"{"theme":"dark"}"#,
        )
        .expect("write existing profile settings");

        initialize_profile_overlay(&AgentKind::Claude, &agent_home, &profile_home, &global_home)
            .expect("initialize profile overlay");

        assert!(
            profile_home
                .join(".claude")
                .join(".credentials.json")
                .exists(),
            "credentials should be merged into existing profile dir"
        );
        assert_eq!(
            fs::read_to_string(profile_home.join(".claude").join("settings.json"))
                .expect("read settings"),
            r#"{"theme":"dark"}"#
        );
    }

    #[test]
    #[cfg(unix)]
    fn overlay_seeds_codex_auth_files_and_links_instance() {
        let tmp = tempdir().expect("tempdir");
        let global_home = tmp.path().join("global-home");
        let profile_home = tmp.path().join("profile-home");
        let agent_home = tmp.path().join("agent-home");
        fs::create_dir_all(global_home.join(".codex")).expect("global codex dir");
        fs::create_dir_all(&profile_home).expect("profile home");
        fs::create_dir_all(&agent_home).expect("agent home");

        fs::write(
            global_home.join(".codex").join("auth.json"),
            r#"{"provider":"openai"}"#,
        )
        .expect("write auth");
        fs::write(
            global_home.join(".codex").join("credentials.json"),
            r#"{"key":"secret-ref"}"#,
        )
        .expect("write creds");

        initialize_profile_overlay(&AgentKind::Codex, &agent_home, &profile_home, &global_home)
            .expect("initialize profile overlay");

        assert!(
            profile_home.join(".codex").join("auth.json").exists(),
            "codex auth should be seeded"
        );
        assert!(
            profile_home
                .join(".codex")
                .join("credentials.json")
                .exists(),
            "codex credentials should be seeded"
        );
        let instance_auth = agent_home.join(".codex").join("auth.json");
        let meta = fs::symlink_metadata(&instance_auth).expect("instance auth metadata");
        assert!(
            meta.file_type().is_symlink(),
            "instance auth should be symlink"
        );
    }

    #[test]
    #[cfg(unix)]
    fn overlay_refreshes_stale_profile_codex_auth_from_global() {
        let tmp = tempdir().expect("tempdir");
        let global_home = tmp.path().join("global-home");
        let profile_home = tmp.path().join("profile-home");
        let agent_home = tmp.path().join("agent-home");
        fs::create_dir_all(global_home.join(".codex")).expect("global codex dir");
        fs::create_dir_all(profile_home.join(".codex")).expect("profile codex dir");
        fs::create_dir_all(&agent_home).expect("agent home");

        fs::write(
            global_home.join(".codex").join("auth.json"),
            r#"{"refresh":"fresh"}"#,
        )
        .expect("write global auth");
        fs::write(
            profile_home.join(".codex").join("auth.json"),
            r#"{"refresh":"stale"}"#,
        )
        .expect("write stale profile auth");

        initialize_profile_overlay(&AgentKind::Codex, &agent_home, &profile_home, &global_home)
            .expect("initialize profile overlay");

        assert_eq!(
            fs::read_to_string(profile_home.join(".codex").join("auth.json"))
                .expect("read profile auth"),
            r#"{"refresh":"fresh"}"#
        );
    }
}
