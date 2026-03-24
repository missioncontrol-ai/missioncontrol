use anyhow::{anyhow, Context, Result};
use serde_json::Value;
use std::process::Command;

/// Resolve `secret://...` references.
///
/// Supported providers:
/// - `secret://keychain/<service>/<account>`
/// - `secret://pass/<entry/path>`
/// - `secret://vault/<path>#<field>` (requires `VAULT_ADDR`, `VAULT_TOKEN`)
pub async fn resolve_maybe_secret_ref(value: &str) -> Result<String> {
    if !value.starts_with("secret://") {
        return Ok(value.to_string());
    }
    let parsed = url::Url::parse(value).with_context(|| format!("invalid secret ref: {value}"))?;
    let provider = parsed.host_str().unwrap_or_default();
    match provider {
        "keychain" => resolve_keychain(&parsed),
        "pass" => resolve_pass(&parsed),
        "vault" => resolve_vault(&parsed).await,
        _ => Err(anyhow!("unsupported secret provider '{}'", provider)),
    }
}

fn resolve_keychain(parsed: &url::Url) -> Result<String> {
    let segments: Vec<&str> = parsed
        .path_segments()
        .map(|s| s.filter(|x| !x.is_empty()).collect())
        .unwrap_or_default();
    if segments.len() < 2 {
        anyhow::bail!("keychain ref must be secret://keychain/<service>/<account>");
    }
    let account = segments.last().unwrap_or(&"").to_string();
    let service = segments[..segments.len() - 1].join("/");
    if service.is_empty() || account.is_empty() {
        anyhow::bail!("keychain ref missing service/account");
    }

    #[cfg(target_os = "macos")]
    {
        let output = Command::new("security")
            .args([
                "find-generic-password",
                "-s",
                &service,
                "-a",
                &account,
                "-w",
            ])
            .output()
            .context("failed to execute security command")?;
        if !output.status.success() {
            anyhow::bail!("keychain secret not found for service/account");
        }
        return Ok(String::from_utf8_lossy(&output.stdout).trim().to_string());
    }

    #[cfg(not(target_os = "macos"))]
    {
        let output = Command::new("secret-tool")
            .args(["lookup", "service", &service, "account", &account])
            .output()
            .context("failed to execute secret-tool")?;
        if !output.status.success() {
            anyhow::bail!("keychain secret not found for service/account");
        }
        Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
    }
}

fn resolve_pass(parsed: &url::Url) -> Result<String> {
    let entry = parsed.path().trim_start_matches('/');
    if entry.is_empty() {
        anyhow::bail!("pass ref must be secret://pass/<entry/path>");
    }
    let output = Command::new("pass")
        .args(["show", entry])
        .output()
        .context("failed to execute pass")?;
    if !output.status.success() {
        anyhow::bail!("pass entry not found: {}", entry);
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let first = text.lines().next().unwrap_or("").trim().to_string();
    if first.is_empty() {
        anyhow::bail!("pass entry is empty: {}", entry);
    }
    Ok(first)
}

async fn resolve_vault(parsed: &url::Url) -> Result<String> {
    let vault_addr =
        std::env::var("VAULT_ADDR").context("VAULT_ADDR is required for vault refs")?;
    let vault_token =
        std::env::var("VAULT_TOKEN").context("VAULT_TOKEN is required for vault refs")?;
    let path = parsed.path().trim_start_matches('/');
    if path.is_empty() {
        anyhow::bail!("vault ref must be secret://vault/<path>#<field>");
    }
    let field = parsed.fragment().unwrap_or("value");
    let url = format!("{}/v1/{}", vault_addr.trim_end_matches('/'), path);
    let resp: Value = reqwest::Client::new()
        .get(&url)
        .header("X-Vault-Token", vault_token)
        .send()
        .await
        .context("failed to query Vault")?
        .error_for_status()
        .context("vault request failed")?
        .json()
        .await
        .context("vault response was not JSON")?;

    let value = resp
        .get("data")
        .and_then(|d| d.get("data").or(Some(d)))
        .and_then(|d| d.get(field))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if value.is_empty() {
        anyhow::bail!("vault secret field '{}' not found at '{}'", field, path);
    }
    Ok(value)
}
