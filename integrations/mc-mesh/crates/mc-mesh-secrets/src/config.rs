/// Configuration for the Infisical client.
/// Service token is the only supported auth mechanism.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct InfisicalConfig {
    /// Base URL of the Infisical instance.
    pub site_url: String,
    /// Service token used as the Bearer token for all API calls.
    pub service_token: String,
    /// Optional default project ID to use when not specified per-call.
    pub default_project_id: Option<String>,
    /// Default environment slug (e.g. "prod", "dev", "staging").
    pub default_environment: String,
}

impl Default for InfisicalConfig {
    fn default() -> Self {
        Self {
            site_url: "https://app.infisical.com".to_string(),
            service_token: String::new(),
            default_project_id: None,
            default_environment: "prod".to_string(),
        }
    }
}

impl InfisicalConfig {
    /// Returns true if the service token is non-empty.
    pub fn is_configured(&self) -> bool {
        !self.service_token.trim().is_empty()
    }
}
