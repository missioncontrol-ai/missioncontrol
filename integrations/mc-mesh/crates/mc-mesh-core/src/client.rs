use anyhow::Result;
use reqwest::{Client, Response};
use serde::{de::DeserializeOwned, Serialize};

/// Thin HTTP client with bearer auth for the MissionControl backend.
#[derive(Clone)]
pub struct BackendClient {
    pub base_url: String,
    pub token: String,
    inner: Client,
}

impl BackendClient {
    pub fn new(base_url: impl Into<String>, token: impl Into<String>) -> Self {
        BackendClient {
            base_url: base_url.into(),
            token: token.into(),
            inner: Client::new(),
        }
    }

    fn url(&self, path: &str) -> String {
        format!("{}{}", self.base_url.trim_end_matches('/'), path)
    }

    fn auth_header(&self) -> String {
        format!("Bearer {}", self.token)
    }

    pub async fn get<T: DeserializeOwned>(&self, path: &str) -> Result<T> {
        let resp = self
            .inner
            .get(self.url(path))
            .header("Authorization", self.auth_header())
            .send()
            .await?
            .error_for_status()?;
        Ok(resp.json().await?)
    }

    pub async fn post<B: Serialize, T: DeserializeOwned>(&self, path: &str, body: &B) -> Result<T> {
        let resp = self
            .inner
            .post(self.url(path))
            .header("Authorization", self.auth_header())
            .json(body)
            .send()
            .await?
            .error_for_status()?;
        Ok(resp.json().await?)
    }

    pub async fn post_empty<T: DeserializeOwned>(&self, path: &str) -> Result<T> {
        let resp = self
            .inner
            .post(self.url(path))
            .header("Authorization", self.auth_header())
            .send()
            .await?
            .error_for_status()?;
        Ok(resp.json().await?)
    }

    pub async fn raw_post<B: Serialize>(&self, path: &str, body: &B) -> Result<Response> {
        Ok(self
            .inner
            .post(self.url(path))
            .header("Authorization", self.auth_header())
            .json(body)
            .send()
            .await?
            .error_for_status()?)
    }

    pub async fn patch<B: Serialize, T: DeserializeOwned>(&self, path: &str, body: &B) -> Result<T> {
        let resp = self
            .inner
            .patch(self.url(path))
            .header("Authorization", self.auth_header())
            .json(body)
            .send()
            .await?
            .error_for_status()?;
        Ok(resp.json().await?)
    }

    /// Fetch the mission roster — concise agent list for prompt injection.
    pub async fn get_mission_roster(&self, mission_id: &str) -> Result<Vec<serde_json::Value>> {
        self.get(&format!("/work/missions/{mission_id}/roster")).await
    }

    /// Fetch a single agent's full detail (includes profile/machine/runtime).
    pub async fn get_agent(&self, agent_id: &str) -> Result<serde_json::Value> {
        self.get(&format!("/work/agents/{agent_id}")).await
    }

    /// Update an agent's profile.
    pub async fn update_agent_profile(
        &self,
        agent_id: &str,
        profile: &serde_json::Value,
    ) -> Result<serde_json::Value> {
        self.patch(&format!("/work/agents/{agent_id}/profile"), profile).await
    }
}
