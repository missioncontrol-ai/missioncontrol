use std::sync::OnceLock;
use crate::error::Result;

static WARNED: OnceLock<()> = OnceLock::new();

pub fn apply_sandbox(_allowed: &[String]) -> Result<()> {
    WARNED.get_or_init(|| {
        eprintln!("[mc-mesh] WARNING: OS-level sandbox is not available on this platform.");
        eprintln!("[mc-mesh] All capabilities run without kernel-level isolation.");
    });
    Ok(())
}

pub fn sandbox_enforced() -> bool { false }
