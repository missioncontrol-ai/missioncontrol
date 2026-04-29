use std::{env, path::PathBuf};

/// Returns `~/.mc` by default, or `$MC_HOME` if set — matching the mc CLI.
pub fn mc_home_dir() -> PathBuf {
    if let Ok(val) = env::var("MC_HOME") {
        if !val.is_empty() {
            return expand_home(&val);
        }
    }
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".mc")
}

pub fn mc_mesh_work_dir() -> PathBuf {
    mc_home_dir().join("mc-mesh").join("work")
}

pub fn mc_mesh_config_path() -> PathBuf {
    mc_home_dir().join("mc-mesh.yaml")
}

pub fn session_file_path() -> PathBuf {
    mc_home_dir().join("session.json")
}

pub fn receipts_db_path() -> PathBuf {
    mc_home_dir().join("receipts.db")
}

pub fn attach_socket_path() -> PathBuf {
    mc_home_dir().join("mc-mesh.sock")
}

pub fn mgmt_socket_path() -> PathBuf {
    mc_home_dir().join("mc-mesh-mgmt.sock")
}

pub fn secrets_socket_path() -> PathBuf {
    mc_home_dir().join("mc-mesh-secrets.sock")
}

pub fn sync_cache_dir() -> PathBuf {
    mc_home_dir().join("sync")
}

fn expand_home(val: &str) -> PathBuf {
    if let Some(stripped) = val.strip_prefix("~/") {
        if let Some(home) = dirs::home_dir() {
            return home.join(stripped);
        }
    }
    PathBuf::from(val)
}
