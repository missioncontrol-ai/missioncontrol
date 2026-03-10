use crate::agent_context::AgentContext;
use dirs::home_dir;
use std::{env, fs, path::PathBuf, time::Duration};
use thiserror::Error;
use url::Url;

/// Runtime configuration derived from CLI flags and env vars.
#[derive(Clone, Debug)]
pub struct McConfig {
    pub base_url: Url,
    pub token: Option<String>,
    pub timeout: Duration,
    pub allow_insecure: bool,
    pub agent_context: AgentContext,
    pub booster_wasm: Option<PathBuf>,
    pub booster_enabled: bool,
}

#[derive(Error, Debug)]
pub enum ConfigError {
    #[error("invalid base URL: {0}")]
    BaseUrl(#[from] url::ParseError),
    #[error("timeout must be greater than zero")]
    Timeout,
}

impl McConfig {
    pub fn from_parts(
        base_url: &str,
        token: Option<String>,
        agent_id: Option<String>,
        timeout_secs: u64,
        allow_insecure: bool,
        booster_enabled: bool,
        booster_wasm: Option<PathBuf>,
    ) -> Result<Self, ConfigError> {
        if timeout_secs == 0 {
            return Err(ConfigError::Timeout);
        }

        let base_url = Url::parse(base_url)?;
        Ok(Self {
            base_url,
            token,
            timeout: Duration::from_secs(timeout_secs),
            allow_insecure,
            agent_context: AgentContext::new(resolve_agent_id(agent_id)),
            booster_wasm,
            booster_enabled,
        })
    }

    pub fn with_allow_insecure(&self, allow: bool) -> Self {
        let mut clone = self.clone();
        clone.allow_insecure = allow;
        clone
    }
}

fn resolve_agent_id(arg: Option<String>) -> Option<String> {
    arg.filter(|value| !value.trim().is_empty())
        .or_else(read_agent_id_from_disk)
}

pub fn mc_home_dir() -> PathBuf {
    expand_home_path(&env::var("MC_HOME").unwrap_or_else(|_| "~/.missioncontrol".into()))
}

pub fn skills_home_dir() -> PathBuf {
    mc_home_dir().join("skills")
}

pub fn ensure_mc_dirs() -> std::io::Result<()> {
    fs::create_dir_all(mc_home_dir())?;
    fs::create_dir_all(skills_home_dir())?;
    Ok(())
}

pub fn agent_id_file() -> PathBuf {
    mc_home_dir().join("agent_id")
}

fn read_agent_id_from_disk() -> Option<String> {
    let path = agent_id_file();
    let contents = fs::read_to_string(path).ok()?;
    let trimmed = contents.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}

pub fn persist_agent_id(agent_id: &str) -> std::io::Result<()> {
    let path = agent_id_file();
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir)?;
    }
    fs::write(path, format!("{}\n", agent_id))
}

fn expand_home_path(value: &str) -> PathBuf {
    if let Some(stripped) = value.strip_prefix('~') {
        if let Some(home) = home_dir() {
            if stripped.is_empty() {
                return home;
            }
            if let Some(stripped_without_slash) = stripped.strip_prefix('/') {
                return home.join(stripped_without_slash);
            }
            return home.join(stripped);
        }
    }
    PathBuf::from(value)
}
