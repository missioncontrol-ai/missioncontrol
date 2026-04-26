// Linux-only keyring integration using the `keyring` crate (libsecret / D-Bus secret-service).
// Falls back silently if no keyring daemon is available.

const SERVICE: &str = "mc-mesh";

fn key_service_token(profile_name: &str) -> String {
    format!("infisical-service-token:{profile_name}")
}

/// Result type for keyring operations that may be unavailable.
pub enum KeyringResult {
    Ok,
    Unavailable(String),
}

/// Store a service token in the OS keyring under the given profile name.
///
/// Sync — call via `spawn_blocking` from async contexts.
pub fn store_service_token(profile_name: &str, token: &str) -> KeyringResult {
    let key = key_service_token(profile_name);
    let entry = match keyring::Entry::new(SERVICE, &key) {
        Ok(e) => e,
        Err(e) => return KeyringResult::Unavailable(e.to_string()),
    };
    match entry.set_password(token) {
        Ok(()) => KeyringResult::Ok,
        Err(e) => KeyringResult::Unavailable(e.to_string()),
    }
}

/// Load a service token from the OS keyring. Returns `None` if not found or keyring unavailable.
///
/// Sync — call via `spawn_blocking` from async contexts.
pub fn load_service_token(profile_name: &str) -> Option<String> {
    let key = key_service_token(profile_name);
    let entry = keyring::Entry::new(SERVICE, &key).ok()?;
    entry.get_password().ok()
}

/// Delete a service token from the OS keyring.
///
/// Sync — call via `spawn_blocking` from async contexts.
pub fn delete_service_token(profile_name: &str) -> KeyringResult {
    let key = key_service_token(profile_name);
    let entry = match keyring::Entry::new(SERVICE, &key) {
        Ok(e) => e,
        Err(e) => return KeyringResult::Unavailable(e.to_string()),
    };
    let _ = entry.delete_credential();
    KeyringResult::Ok
}
