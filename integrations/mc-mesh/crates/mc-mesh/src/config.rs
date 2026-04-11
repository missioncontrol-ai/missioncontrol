/// mc-mesh daemon configuration.
///
/// Loaded from `/etc/mc-mesh/agent.yaml` or `~/.missioncontrol/mc-mesh.yaml`
/// (whichever exists first).  All fields can be overridden by CLI flags.
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DaemonConfig {
    /// MissionControl backend base URL.
    pub backend_url: String,
    /// Bearer token for the backend.  In practice loaded from env or keyring.
    pub token: String,
    /// Local directory used as the working root for agent processes.
    #[serde(default = "default_work_dir")]
    pub work_dir: PathBuf,
    /// Missions (and their klusters to watch) this daemon manages.
    #[serde(default)]
    pub missions: Vec<MissionEntry>,
    /// Seconds without a backend response before the offline watchdog triggers.
    #[serde(default = "default_grace")]
    pub offline_grace_secs: u64,
    /// Offline policy: "strict" | "safe_readonly" | "autonomous"
    #[serde(default = "default_policy")]
    pub offline_policy: String,
    /// Unix socket path for the local control interface.
    #[serde(default = "default_socket")]
    pub control_socket: PathBuf,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MissionEntry {
    pub mission_id: String,
    /// Agents enrolled in this mission, managed by this daemon.
    #[serde(default)]
    pub agents: Vec<AgentEntry>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentEntry {
    /// The MeshAgent id as assigned by the backend.
    pub agent_id: String,
    /// Runtime kind: claude_code | codex | gemini
    pub runtime_kind: String,
}

fn default_work_dir() -> PathBuf {
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("/tmp"))
        .join(".missioncontrol")
        .join("mc-mesh")
        .join("work")
}

fn default_grace() -> u64 {
    30
}

fn default_policy() -> String {
    "strict".into()
}

fn default_socket() -> PathBuf {
    PathBuf::from("/run/mc-mesh/control.sock")
}

impl DaemonConfig {
    /// Load from the first config file found, falling back to an empty default.
    pub fn load_or_default() -> Self {
        if let Some(cfg) = Self::try_load() {
            cfg
        } else {
            DaemonConfig {
                backend_url: std::env::var("MC_BACKEND_URL")
                    .unwrap_or_else(|_| "http://localhost:8000".into()),
                token: std::env::var("MC_TOKEN").unwrap_or_default(),
                work_dir: default_work_dir(),
                missions: vec![],
                offline_grace_secs: default_grace(),
                offline_policy: default_policy(),
                control_socket: default_socket(),
            }
        }
    }

    fn try_load() -> Option<Self> {
        let candidates = [
            PathBuf::from("/etc/mc-mesh/agent.yaml"),
            dirs::home_dir()?.join(".missioncontrol").join("mc-mesh.yaml"),
        ];
        for path in &candidates {
            if path.exists() {
                if let Ok(cfg) = Self::from_path(path) {
                    return Some(cfg);
                }
            }
        }
        None
    }

    pub fn from_path(path: &Path) -> Result<Self> {
        let text = std::fs::read_to_string(path)
            .with_context(|| format!("reading {}", path.display()))?;
        serde_yaml::from_str(&text)
            .with_context(|| format!("parsing {}", path.display()))
    }

    #[allow(dead_code)]
    pub fn save(&self, path: &Path) -> Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let text = serde_yaml::to_string(self)?;
        std::fs::write(path, text)?;
        Ok(())
    }

    /// Default user config path.
    pub fn user_config_path() -> PathBuf {
        dirs::home_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join(".missioncontrol")
            .join("mc-mesh.yaml")
    }
}
