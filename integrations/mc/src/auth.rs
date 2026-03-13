//! `mc login` / `mc logout` / `mc whoami` — session token management.
//!
//! Session tokens (`mcs_*`) are issued by the MissionControl server and stored
//! at `~/.missioncontrol/session.json`. They are:
//!
//! - Revocable server-side at any time
//! - Never embedded in agent config files (mc launch uses env injection)
//! - Auto-loaded by McConfig when MC_TOKEN is absent
//! - Validated for expiry before use, with a clear renewal hint

use crate::{client::MissionControlClient, config::mc_home_dir};
use anyhow::{anyhow, Context, Result};
use clap::Args;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

/// Prefix all MissionControl session tokens use.
pub const SESSION_TOKEN_PREFIX: &str = "mcs_";

// ── CLI arg types ─────────────────────────────────────────────────────────────

#[derive(Args, Debug)]
pub struct LoginArgs {
    /// Session TTL in hours (default: 8, max: 720)
    #[arg(long, default_value_t = 8)]
    pub ttl_hours: u64,

    /// Print the session token to stdout (useful in scripts: export MC_TOKEN=$(mc login --print-token))
    #[arg(long)]
    pub print_token: bool,
}

#[derive(Args, Debug)]
pub struct LogoutArgs {
    /// Only clear the local session file; do not call the revoke endpoint
    #[arg(long)]
    pub local_only: bool,
}

#[derive(Args, Debug)]
pub struct WhoamiArgs {}

// ── Saved session file ────────────────────────────────────────────────────────

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct SavedSession {
    pub token: String,
    pub subject: String,
    /// RFC3339 timestamp
    pub expires_at: String,
    /// The base URL this session was created against
    pub base_url: String,
    pub session_id: Option<i64>,
}

pub fn session_file_path() -> PathBuf {
    mc_home_dir().join("session.json")
}

/// Read the saved session from disk and validate it is not expired and matches
/// the given base URL. Returns `None` if absent, expired, or URL-mismatched.
pub fn load_saved_session(base_url: &str) -> Option<SavedSession> {
    let path = session_file_path();
    let content = std::fs::read_to_string(&path).ok()?;
    let session: SavedSession = serde_json::from_str(&content).ok()?;

    // URL match: strip trailing slashes before comparing
    let stored = session.base_url.trim_end_matches('/');
    let wanted = base_url.trim_end_matches('/');
    if !stored.eq_ignore_ascii_case(wanted) {
        return None;
    }

    // Expiry: parse and check
    if let Ok(expires) = chrono::DateTime::parse_from_rfc3339(&session.expires_at) {
        if expires <= chrono::Utc::now() {
            return None;
        }
    }

    Some(session)
}

pub fn save_session(session: &SavedSession) -> Result<()> {
    let path = session_file_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let json = serde_json::to_string_pretty(session)?;
    std::fs::write(&path, &json)?;
    // Restrict permissions to owner-read-only
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))?;
    }
    Ok(())
}

pub fn clear_session() -> Result<()> {
    let path = session_file_path();
    if path.exists() {
        std::fs::remove_file(&path)?;
    }
    Ok(())
}

/// Returns true if the token looks like an mc session token.
pub fn is_session_token(token: &str) -> bool {
    token.starts_with(SESSION_TOKEN_PREFIX)
}

// ── Command handlers ──────────────────────────────────────────────────────────

pub async fn login(
    args: LoginArgs,
    client: &MissionControlClient,
    base_url: &str,
) -> Result<()> {
    let ttl = args.ttl_hours.clamp(1, 720);
    let payload = serde_json::json!({ "ttl_hours": ttl });
    let resp = client
        .post_json("/auth/sessions", &payload)
        .await
        .context("failed to create session — check MC_TOKEN / OIDC credentials and MC_BASE_URL")?;

    let token = resp["token"]
        .as_str()
        .ok_or_else(|| anyhow!("server response missing 'token' field"))?
        .to_string();
    let subject = resp["subject"].as_str().unwrap_or("unknown").to_string();
    let expires_at = resp["expires_at"].as_str().unwrap_or("").to_string();
    let session_id = resp["session_id"].as_i64();

    let session = SavedSession {
        token: token.clone(),
        subject: subject.clone(),
        expires_at: expires_at.clone(),
        base_url: base_url.trim_end_matches('/').to_string(),
        session_id,
    };
    save_session(&session).context("failed to write session file")?;

    if args.print_token {
        println!("{}", token);
    } else {
        eprintln!("mc login: session created for {}", subject);
        eprintln!("mc login: token prefix: {}...", &token[..token.len().min(12)]);
        eprintln!("mc login: expires: {}", expires_at);
        eprintln!("mc login: saved to {}", session_file_path().display());
        eprintln!();
        eprintln!("To use this session:");
        eprintln!("  mc launch claude   # token injected automatically");
        eprintln!("  mc whoami          # verify identity");
    }

    Ok(())
}

pub async fn logout(args: LogoutArgs, client: &MissionControlClient) -> Result<()> {
    if !args.local_only {
        // Best-effort server-side revoke; don't fail if the session is already expired
        match client.delete("/auth/sessions/current").await {
            Ok(_) => eprintln!("mc logout: session revoked on server"),
            Err(e) => eprintln!("mc logout: server revoke failed ({}); clearing local file anyway", e),
        }
    }
    clear_session()?;
    eprintln!("mc logout: cleared {}", session_file_path().display());
    Ok(())
}

pub async fn whoami(client: &MissionControlClient) -> Result<()> {
    // Show local session file info first
    let session_path = session_file_path();
    if session_path.exists() {
        if let Ok(content) = std::fs::read_to_string(&session_path) {
            if let Ok(session) = serde_json::from_str::<SavedSession>(&content) {
                eprintln!("local session: {} (expires {})", session.subject, session.expires_at);
            }
        }
    }

    // Fetch live identity from server
    let resp = client
        .get_json("/auth/me")
        .await
        .context("failed to fetch identity — check auth credentials")?;

    println!("{}", serde_json::to_string_pretty(&resp)?);
    Ok(())
}
