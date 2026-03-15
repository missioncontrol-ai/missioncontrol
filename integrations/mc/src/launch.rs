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
};
use anyhow::{anyhow, Context, Result};
use clap::{Args, ValueEnum};
use std::{
    io::Write,
    path::{Path, PathBuf},
    process::Stdio,
    time::Duration,
};

const SHIM_HOST: &str = "127.0.0.1";
const SHIM_PORT: u16 = 8765;

// ── CLI args ────────────────────────────────────────────────────────────────

#[derive(Args, Debug)]
pub struct LaunchArgs {
    /// Agent to launch: codex, claude, gemini, openclaw, nanoclaw
    agent: AgentKind,

    /// Skip daemon lifecycle management (daemon managed externally)
    #[arg(long)]
    no_daemon: bool,

    /// Run preflights only; do not launch agent (useful for CI)
    #[arg(long)]
    preflight_only: bool,

    /// Skip config generation (use existing ~/.missioncontrol/config/)
    #[arg(long)]
    skip_config_gen: bool,

    /// Do not embed MC_TOKEN in the written agent config.
    ///
    /// Use this for OIDC / short-lived tokens: the token is inherited from the
    /// shell environment at agent exec time instead of being written to disk.
    /// Automatically implied when MC_TOKEN is absent.
    #[arg(long)]
    no_embed_token: bool,

    /// Seconds to wait for daemon to become ready (default: 15)
    #[arg(long, default_value_t = 15)]
    daemon_timeout: u64,

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
    Nanoclaw,
}

impl AgentKind {
    fn driver(&self) -> Box<dyn AgentDriver> {
        match self {
            AgentKind::Codex => Box::new(CodexDriver),
            AgentKind::Claude => Box::new(ClaudeDriver),
            AgentKind::Gemini => Box::new(GeminiDriver),
            AgentKind::Openclaw => Box::new(OpenClawDriver),
            AgentKind::Nanoclaw => Box::new(NanoClawDriver),
        }
    }

    fn config_key(&self) -> &str {
        match self {
            AgentKind::Codex => "codex",
            AgentKind::Claude => "claude",
            AgentKind::Gemini => "gemini",
            AgentKind::Openclaw => "openclaw",
            AgentKind::Nanoclaw => "nanoclaw",
        }
    }
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
    ) -> Result<()>;
    /// Build the Command to exec (binary + required flags).
    fn command(&self, extra_args: &[String]) -> std::process::Command;
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
    ) -> Result<()> {
        let config_path = dirs::home_dir()
            .ok_or_else(|| anyhow!("cannot determine home directory"))?
            .join(".codex")
            .join("config.toml");

        if let Some(parent) = config_path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        let existing = if config_path.exists() {
            std::fs::read_to_string(&config_path)?
        } else {
            String::new()
        };

        // Detect existing missioncontrol section: either via our marker comment
        // or the raw TOML key (handles configs written before the marker existed).
        let has_marker = existing.contains(CODEX_MC_MARKER);
        let has_key = existing.contains("[mcp_servers.missioncontrol]");

        if has_marker || has_key {
            // Prompt user to replace.
            eprint!("mc launch: [mcp_servers.missioncontrol] already exists in {}. Replace? [y/N] ", config_path.display());
            std::io::stderr().flush()?;
            let mut answer = String::new();
            std::io::stdin().read_line(&mut answer)?;
            if !answer.trim().eq_ignore_ascii_case("y") {
                eprintln!("mc launch: keeping existing codex MCP config");
                return Ok(());
            }
            // Remove the existing section (and its marker comment if present).
            let cleaned = remove_codex_mc_section(&existing);
            std::fs::write(&config_path, &cleaned)?;
            eprintln!("mc launch: removed existing missioncontrol section");
        }

        let stanza = render_codex_stanza(base_url, token, embed_token);
        let current = std::fs::read_to_string(&config_path).unwrap_or_default();
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&config_path)?;

        if !current.is_empty() && !current.ends_with('\n') {
            writeln!(file)?;
        }
        writeln!(file)?;
        write!(file, "{}", stanza)?;
        eprintln!(
            "mc launch: installed codex MCP config at {}",
            config_path.display()
        );
        Ok(())
    }

    fn command(&self, extra_args: &[String]) -> std::process::Command {
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
    ) -> Result<()> {
        let config_path = dirs::home_dir()
            .ok_or_else(|| anyhow!("cannot determine home directory"))?
            .join(".claude.json");

        let mut root: serde_json::Value = if config_path.exists() {
            let content = std::fs::read_to_string(&config_path)?;
            serde_json::from_str(&content)
                .unwrap_or_else(|_| serde_json::Value::Object(Default::default()))
        } else {
            serde_json::Value::Object(Default::default())
        };

        let mc_entry = render_json_mcp_entry(
            include_str!("../../../distribution/templates/claude.mcp.json.tmpl"),
            "embedded claude template",
            base_url,
            token,
            embed_token,
        );

        root.as_object_mut()
            .ok_or_else(|| anyhow!("~/.claude.json is not a JSON object"))?
            .entry("mcpServers")
            .or_insert_with(|| serde_json::Value::Object(Default::default()))
            .as_object_mut()
            .ok_or_else(|| anyhow!("~/.claude.json mcpServers is not an object"))?
            .insert("missioncontrol".to_string(), mc_entry);

        std::fs::write(&config_path, serde_json::to_string_pretty(&root)?)?;
        eprintln!(
            "mc launch: installed claude MCP config at {}",
            config_path.display()
        );
        Ok(())
    }

    fn command(&self, extra_args: &[String]) -> std::process::Command {
        let mut cmd = std::process::Command::new("claude");
        cmd.args(extra_args);
        cmd
    }
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
    ) -> Result<()> {
        let config_path = dirs::home_dir()
            .ok_or_else(|| anyhow!("cannot determine home directory"))?
            .join(".gemini")
            .join("settings.json");

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

        root.as_object_mut()
            .ok_or_else(|| anyhow!("~/.gemini/settings.json is not a JSON object"))?
            .entry("mcpServers")
            .or_insert_with(|| serde_json::Value::Object(Default::default()))
            .as_object_mut()
            .ok_or_else(|| anyhow!("~/.gemini/settings.json mcpServers is not an object"))?
            .insert("missioncontrol".to_string(), mc_entry);

        std::fs::write(&config_path, serde_json::to_string_pretty(&root)?)?;
        eprintln!(
            "mc launch: installed gemini MCP config at {}",
            config_path.display()
        );
        Ok(())
    }

    fn command(&self, extra_args: &[String]) -> std::process::Command {
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
    let mut full: serde_json::Value =
        serde_json::from_str(&rendered).unwrap_or_else(|_| panic!("{} must be valid JSON", tmpl_name));
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

// ── OpenClawDriver / NanoClawDriver ──────────────────────────────────────────

struct OpenClawDriver;
struct NanoClawDriver;

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
    ) -> Result<()> {
        install_acp_config("openclaw", base_url, token, embed_token)
    }

    fn command(&self, extra_args: &[String]) -> std::process::Command {
        let config = mc_home_dir().join("config").join("openclaw.acp.json");
        let mut cmd = std::process::Command::new("openclaw");
        cmd.arg("--acp-config").arg(config);
        cmd.args(extra_args);
        cmd
    }
}

impl AgentDriver for NanoClawDriver {
    fn binary(&self) -> &str {
        "nanoclaw"
    }

    fn install_hint(&self) -> &str {
        "See MissionControl docs for NanoClaw installation"
    }

    fn install_config(
        &self,
        _staging_dir: &Path,
        base_url: &str,
        token: &str,
        embed_token: bool,
    ) -> Result<()> {
        install_acp_config("nanoclaw", base_url, token, embed_token)
    }

    fn command(&self, extra_args: &[String]) -> std::process::Command {
        let config = mc_home_dir().join("config").join("nanoclaw.acp.json");
        let mut cmd = std::process::Command::new("nanoclaw");
        cmd.arg("--acp-config").arg(config);
        cmd.args(extra_args);
        cmd
    }
}

fn install_acp_config(name: &str, base_url: &str, token: &str, embed_token: bool) -> Result<()> {
    let config_dir = mc_home_dir().join("config");
    std::fs::create_dir_all(&config_dir)?;
    let out = config_dir.join(format!("{}.acp.json", name));
    let mut config = serde_json::json!({
        "mc_base_url": base_url,
        "shim_host": SHIM_HOST,
        "shim_port": SHIM_PORT,
    });
    if embed_token {
        config["mc_token"] = serde_json::json!(token);
    }
    // When not embedding, mc_token is intentionally absent; the ACP client
    // must read MC_TOKEN from the process environment at runtime.
    std::fs::write(&out, serde_json::to_string_pretty(&config)?)?;
    eprintln!("mc launch: wrote ACP config to {}", out.display());
    Ok(())
}

// ── Orchestration ─────────────────────────────────────────────────────────────

pub async fn run(args: LaunchArgs, client: &MissionControlClient, config: &McConfig) -> Result<()> {
    let driver = args.agent.driver();

    // 1. Verify binary is on PATH before doing anything else.
    check_binary(driver.as_ref())?;

    // 2. Daemon lifecycle (skip if externally managed).
    if !args.no_daemon {
        ensure_daemon_running(args.daemon_timeout, config).await?;

        // Non-fatal probe: warn if the daemon tools cache isn't warm yet so the
        // agent doesn't silently see an empty tools list on its first call.
        if let Ok(http) = reqwest::Client::builder()
            .timeout(Duration::from_secs(2))
            .build()
        {
            let tools_url = format!("http://{}:{}/v1/tools", SHIM_HOST, SHIM_PORT);
            match http.get(&tools_url).send().await {
                Ok(r) if r.status().is_success() => {}
                _ => tracing::warn!(
                    "daemon tools cache not yet warm — agent may see empty tools on first call"
                ),
            }
        }
    }

    // 3. Auth: verify we have a valid session or static token; run interactive
    //    login if neither is available.  Falls through immediately when MC_TOKEN
    //    is already set (static token path).
    let login_client_holder: Option<MissionControlClient> = if config.token.is_none() {
        if auth::load_saved_session(config.base_url.as_str()).is_none() {
            eprintln!("mc: no valid session found for {}", config.base_url.as_str());
            eprintln!("mc: running `mc login` to authenticate...");
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
            .context("session not found after login — run `mc login` manually")?;
        Some(
            MissionControlClient::new_with_token(config.base_url.as_str(), &session_token)
                .context("failed to build client with session token")?,
        )
    } else {
        None
    };
    let effective_client: &MissionControlClient =
        login_client_holder.as_ref().unwrap_or(client);

    // 4. Preflight-only mode: verify connectivity then stop.
    if args.preflight_only {
        effective_client
            .get_json("/mcp/health")
            .await
            .context("auth preflight failed — check MC_TOKEN and MC_BASE_URL")?;
        eprintln!("mc launch: preflight passed");
        return Ok(());
    }

    let base_url = config
        .base_url
        .as_str()
        .trim_end_matches('/')
        .to_string();
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

    let staging_dir = mc_home_dir().join("config");
    std::fs::create_dir_all(&staging_dir)?;

    // 6. Fetch agent config from onboarding manifest and write to staging dir.
    if !args.skip_config_gen {
        fetch_and_stage_agent_config(effective_client, &args.agent, &staging_dir, &base_url, &token)
            .await?;
    }

    // 7. Install config from staging dir to the agent's canonical location.
    driver.install_config(&staging_dir, &base_url, &token, embed_token)?;

    // 8. Exec the agent (replaces the current process on Unix).
    //    Always inject MC_TOKEN into the agent environment so the MCP shim can
    //    authenticate even when the token was NOT embedded in the config file.
    exec_agent(driver.as_ref(), &args.agent_args, &token)
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
        eprintln!("mc launch: --no-embed-token — MC_TOKEN will NOT be written to agent config");
        eprintln!("mc launch: token will be injected into the agent process at exec time");
        return false;
    }
    if crate::auth::is_session_token(token) {
        eprintln!("mc launch: session token (mcs_*) detected — not writing token to agent config");
        eprintln!("mc launch: token will be injected into the agent process at exec time");
        return false;
    }
    if token.is_empty() {
        eprintln!("mc launch: MC_TOKEN is not set — implying --no-embed-token");
        eprintln!("mc launch: ensure MC_TOKEN is present in the environment when the agent runs");
        return false;
    }
    true
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

async fn ensure_daemon_running(timeout_secs: u64, config: &McConfig) -> Result<()> {
    let health_url = format!("http://{}:{}/v1/health", SHIM_HOST, SHIM_PORT);
    let http = reqwest::Client::builder()
        .timeout(Duration::from_secs(1))
        .build()?;

    if http.get(&health_url).send().await.is_ok() {
        return Ok(()); // Already running.
    }

    // Spawn a detached daemon; drop the handle so it becomes an orphan after exec().
    let base_url = config.base_url.as_str();
    let token = config.token.as_deref().unwrap_or("");
    std::process::Command::new(std::env::current_exe()?)
        .args([
            "daemon",
            "--shim-host",
            SHIM_HOST,
            "--shim-port",
            &SHIM_PORT.to_string(),
            "--disable-matrix",
        ])
        .env("MC_BASE_URL", base_url)
        .env("MC_TOKEN", token)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .context("failed to spawn mc daemon")?;

    eprintln!("mc launch: waiting for daemon (up to {} s)…", timeout_secs);
    for _ in 0..timeout_secs {
        tokio::time::sleep(Duration::from_secs(1)).await;
        if http.get(&health_url).send().await.is_ok() {
            eprintln!("mc launch: daemon ready");
            return Ok(());
        }
    }
    anyhow::bail!(
        "daemon did not become ready within {} seconds",
        timeout_secs
    )
}

async fn fetch_and_stage_agent_config(
    client: &MissionControlClient,
    agent: &AgentKind,
    staging_dir: &Path,
    base_url: &str,
    _token: &str,
) -> Result<()> {
    let encoded_url: String =
        url::form_urlencoded::byte_serialize(base_url.as_bytes()).collect();
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
        eprintln!(
            "mc launch: staged manifest config to {}",
            out_path.display()
        );
    } else {
        eprintln!(
            "mc launch: no agent_configs.{} in manifest, using embedded template",
            config_key
        );
    }
    Ok(())
}

fn exec_agent(driver: &dyn AgentDriver, extra_args: &[String], token: &str) -> Result<()> {
    let binary_name = driver.binary().to_string();
    let mut cmd = driver.command(extra_args);

    // Always inject MC_TOKEN into the agent's process environment. This ensures
    // the MCP shim can authenticate regardless of whether the token was embedded
    // in the config file — covering session tokens, --no-embed-token, and the
    // standard embedded-token path uniformly.
    if !token.is_empty() {
        cmd.env("MC_TOKEN", token);
    }

    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        let err = cmd.exec();
        // exec() only returns if it fails.
        Err(anyhow::Error::new(err)
            .context(format!("failed to exec '{}'", binary_name)))
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
