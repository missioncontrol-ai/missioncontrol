use std::time::Duration;
use crate::config::InfisicalConfig;
use crate::error::{Result, SecretsError};

/// Async HTTP client for the Infisical API.
/// Authenticates using a service token (Bearer token).
pub struct InfisicalClient {
    site_url: String,
    token: String,
    http: reqwest::Client,
}

impl InfisicalClient {
    pub fn new(cfg: &InfisicalConfig) -> Result<Self> {
        let token = cfg.service_token.clone().unwrap_or_default();
        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(10))
            .connect_timeout(Duration::from_secs(5))
            .build()
            .map_err(|e| SecretsError::Http(format!("failed to build reqwest client: {e}")))?;
        Ok(Self {
            site_url: cfg.site_url.trim_end_matches('/').to_string(),
            token,
            http,
        })
    }

    /// Fetch the value of a single secret by name.
    pub async fn fetch_secret(
        &self,
        secret_name: &str,
        project_id: &str,
        environment: &str,
        path: &str,
    ) -> Result<String> {
        let url = format!(
            "{}/api/v3/secrets/raw/{}?workspaceId={}&environment={}&secretPath={}",
            self.site_url,
            secret_name,
            project_id,
            environment,
            urlencoding::encode(path),
        );
        let resp = self.http
            .get(&url)
            .bearer_auth(&self.token)
            .send()
            .await
            .map_err(|e| SecretsError::Http(format!("Infisical fetch_secret: {e}")))?;

        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            return Err(SecretsError::Http(format!("Infisical {status}: {body}")));
        }

        let body: serde_json::Value = resp
            .json()
            .await
            .map_err(|e| SecretsError::Json(e.to_string()))?;

        body["secret"]["secretValue"]
            .as_str()
            .map(|s| s.to_string())
            .ok_or_else(|| SecretsError::SecretNotFound(secret_name.to_string()))
    }

    /// List secret names at the given path (values are not fetched).
    pub async fn list_secrets(
        &self,
        project_id: &str,
        environment: &str,
        path: &str,
    ) -> Result<Vec<String>> {
        let url = format!(
            "{}/api/v3/secrets/raw?workspaceId={}&environment={}&secretPath={}&recursive=false",
            self.site_url,
            project_id,
            environment,
            urlencoding::encode(path),
        );
        let resp = self.http
            .get(&url)
            .bearer_auth(&self.token)
            .send()
            .await
            .map_err(|e| SecretsError::Http(format!("Infisical list_secrets: {e}")))?;

        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            return Err(SecretsError::Http(format!("Infisical {status}: {body}")));
        }

        let body: serde_json::Value = resp
            .json()
            .await
            .map_err(|e| SecretsError::Json(e.to_string()))?;

        Ok(body["secrets"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .filter_map(|s| s["secretKey"].as_str().map(|n| n.to_string()))
                    .collect()
            })
            .unwrap_or_default())
    }

    /// List subfolder names at the given path.
    ///
    /// Uses API v1 (`/api/v1/folders`) rather than v3 because the Infisical v3
    /// secrets endpoint does not expose folder listings — folders are a separate
    /// resource that was never promoted to v3.
    pub async fn list_folders(
        &self,
        project_id: &str,
        environment: &str,
        path: &str,
    ) -> Result<Vec<String>> {
        let url = format!(
            "{}/api/v1/folders?workspaceId={}&environment={}&secretPath={}",
            self.site_url,
            project_id,
            environment,
            urlencoding::encode(path),
        );
        let resp = self.http
            .get(&url)
            .bearer_auth(&self.token)
            .send()
            .await
            .map_err(|e| SecretsError::Http(format!("Infisical list_folders: {e}")))?;

        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            return Err(SecretsError::Http(format!("Infisical {status}: {body}")));
        }

        let body: serde_json::Value = resp
            .json()
            .await
            .map_err(|e| SecretsError::Json(e.to_string()))?;

        Ok(body["folders"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .filter_map(|f| f["name"].as_str().map(|n| n.to_string()))
                    .collect()
            })
            .unwrap_or_default())
    }
}
